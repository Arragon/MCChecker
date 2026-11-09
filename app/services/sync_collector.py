"""Host-side wiring to the independent collector; only stdlib and private IPC here."""
import hashlib
import json
import os
import threading

from app.core import storage
from app.core.errors import CONFIG_DISABLED, DOWNLOAD_FAILED, REVISION_CONFLICT, AppError
from . import collector_manager, sync_connections, sync_credentials, sync_heartbeat, workspace_catalog
from .collector_client import CollectorClientError


class CollectorBridge:
    def __init__(self, manager=None):
        self.manager = manager or collector_manager.get_manager()
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None

    def _call(self, operation, wid, cid, params, connection=None):
        if self.stopping.is_set():
            raise CollectorClientError("collector_offline")
        client = self.manager.client()
        kwargs = {}
        if connection:
            kwargs = {"expected_policy_revision": connection["policy_revision"],
                      "expected_auth_epoch": connection["auth_epoch"]}
        raw, meta = client.request_with_meta(operation, wid, cid, params, timeout=30, **kwargs)
        if operation == "read_resource" and params.get("kind") in ("file", "table"):
            result = dict(meta.get("result") or {})
            result["content"] = raw
        else:
            result = json.loads(raw)
        if not isinstance(result, dict):
            raise CollectorClientError("protocol_mismatch")
        return result

    def _configure(self, wid, cid, generation=None):
        connection = sync_connections.get_connection(wid, cid)
        if not connection["enabled"]:
            raise CollectorClientError("disabled")
        credential = sync_credentials.get_credential_state(wid, cid)
        if generation is None:
            if credential["state"] != "valid":
                raise CollectorClientError("locked" if credential["state"] == "locked" else "auth_failed")
            generation = credential["active_generation"]
        params = {"origin": connection["origin"], "policy_revision": connection["policy_revision"],
                  "auth_epoch": credential["auth_epoch"], "generation": generation,
                  "auto_relogin": sync_heartbeat.get_state(wid, cid)["auto_relogin"]}
        self._call("configure_connection", wid, cid, params)
        return params

    def _assert_current(self, wid, cid, frozen):
        try:
            current = sync_connections.get_connection(wid, cid)
            sync_credentials.assert_epoch(wid, cid, frozen["auth_epoch"])
            if not current["enabled"] or current["policy_revision"] != frozen["policy_revision"]:
                raise CollectorClientError("source_changed")
        except AppError:
            raise CollectorClientError("source_changed")

    def put(self, wid, cid, generation, payload):
        self._call("store_credentials", wid, cid, {"generation": generation, "credentials": json.loads(payload)})

    def delete(self, wid, cid, generation):
        # Revocation holds the metadata lock. Do not take the bridge lock here:
        # readers acquire it before reading metadata. Client serializes pipe requests.
        return self._call("delete_credentials", wid, cid, {"generation": generation})["deleted"]

    def verify_activation(self, wid, cid, candidate):
        with self.lock:
            connection = self._configure(wid, cid, candidate["generation"])
            try:
                self._call("activate_credentials", wid, cid, {"generation": candidate["generation"]}, connection)
            except CollectorClientError:
                raise AppError(REVISION_CONFLICT, "凭据尚未在当前采集器验证，请重新测试连接", field="candidate_id")

    def probe(self, wid, cid, candidate_id):
        with self.lock:
            state = sync_credentials.get_credential_state(wid, cid)
            candidate = state.get("candidate")
            if not candidate or candidate["candidate_id"] != candidate_id:
                return {"ok": False, "reason_code": "source_changed"}
            try:
                connection = self._configure(wid, cid, candidate["generation"])
                result = self._call("verify_credentials", wid, cid, {"generation": candidate["generation"]}, connection)
                self._assert_current(wid, cid, connection)
                return result
            except CollectorClientError as exc:
                return {"ok": False, "reason_code": exc.reason_code, "blocked": exc.reason_code.startswith("collector_")}

    def heartbeat(self, wid, cid):
        with self.lock:
            try:
                connection = self._configure(wid, cid)
                result = self._call("keepalive", wid, cid, {}, connection)
                self._assert_current(wid, cid, connection)
                return result
            except CollectorClientError as exc:
                return {"status": "failed", "reason_code": exc.reason_code, "session_valid": False}

    def catalog(self, wid, cid):
        with self.lock:
            try:
                connection = self._configure(wid, cid)
                result = self._call("read_resource", wid, cid, {"kind": "catalog"}, connection)
                self._assert_current(wid, cid, connection)
                return result["items"]
            except CollectorClientError as exc:
                raise AppError(DOWNLOAD_FAILED, "无法读取 SDA 目录", reason_code=exc.reason_code)

    def read(self, source, cancel):
        wid, cid = source["workspace_id"], source["connection_id"]
        with self.lock:
            if cancel.is_set():
                return {"status": "failed", "reason_code": "canceled"}
            try:
                connection = self._configure(wid, cid)
                result = self._call("read_resource", wid, cid, {"kind": source["kind"][4:],
                    "resource_ref": source["resource_ref"], "name": source["name"]}, connection)
                self._assert_current(wid, cid, connection)
                if source["kind"] == "sda_catalog":
                    return result
                content = result["content"]
                if len(content) != result["size"] or hashlib.sha256(content).hexdigest() != result["sha256"]:
                    raise CollectorClientError("invalid_payload")
                result["content"] = content
                return result
            except CollectorClientError as exc:
                return {"status": "blocked" if exc.reason_code.startswith("collector_") else "failed", "reason_code": exc.reason_code,
                        "message": "SDA 读取未完成"}

    def start_heartbeats(self):
        def pump():
            while not self.stopping.wait(1):
                for workspace in workspace_catalog.list_workspaces():
                    if self.stopping.is_set():
                        return
                    try:
                        wid = workspace["id"]
                        for connection in sync_connections.list_connections(wid):
                            cid = connection["connection_id"]
                            if connection["enabled"] and sync_heartbeat.is_due(sync_heartbeat.get_state(wid, cid)):
                                sync_heartbeat.run_heartbeat(wid, cid, lambda: self.heartbeat(wid, cid))
                    except Exception:
                        # Individual workspace faults must not kill all heartbeat scheduling.
                        continue
        self.thread = threading.Thread(target=pump, daemon=True, name="collector-heartbeat")
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(0.2)


_BRIDGE = None


def stop():
    if _BRIDGE:
        _BRIDGE.stop()


def configure_from_environment():
    """Opt-in deployment bootstrap. Unconfigured installs keep the existing offline behavior."""
    global _BRIDGE
    from app.server import api_connections, api_sync_heartbeat
    from . import sync_catalog, sync_tasks
    if not os.environ.get("MCHECKER_COLLECTOR_CONFIG"):
        return None
    root = os.environ.get("MCHECKER_SECRET_ROOT")
    if not root or not os.environ.get("MCHECKER_SECRET_KEY"):
        raise AppError(CONFIG_DISABLED, "采集器需要独立秘密目录和密钥", field="collector")
    secret_root, data_root = os.path.realpath(root), os.path.realpath(storage.DATA_DIR)
    if os.path.commonpath([secret_root, data_root]) in (secret_root, data_root):
        raise AppError(CONFIG_DISABLED, "秘密目录必须与业务数据目录分离", field="collector")
    bridge = CollectorBridge()
    api_connections.configure_vault(bridge)
    api_connections.configure_probe(bridge.probe)
    api_sync_heartbeat.configure_probe(bridge.heartbeat)
    sync_catalog.register_reader(bridge.catalog)
    sync_tasks.register_sda_reader(bridge.read)
    _BRIDGE = bridge
    return bridge
