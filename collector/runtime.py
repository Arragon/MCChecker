"""Collector-owned browser/session/vault runtime. No imports from the host application."""
import base64
import hashlib
import json
import os
import re

from . import ipc
from .login_flow import Credentials, LoginConfig, SdaLogin
from .playwright_session import PlaywrightBrowser
from .sda_adapter import SdaAdapter
from .secret_store import EnvKeyProvider, SecretStore
from .session_manager import SessionManager


class RuntimeErrorCode(Exception):
    def __init__(self, reason_code):
        self.reason_code = reason_code


class Runtime:
    def __init__(self):
        self.connections = {}
        self.managers = {}
        self.browsers = {}
        self.verified = set()
        self._vault = None
        path = os.environ.get("MCHECKER_COLLECTOR_CONFIG")
        self.config = {}
        if path:
            with open(path, encoding="utf-8") as handle:
                self.config = json.load(handle)

    def vault(self):
        if self._vault is None:
            root = os.environ.get("MCHECKER_SECRET_ROOT")
            if not root:
                raise RuntimeErrorCode("collector_offline")
            self._vault = SecretStore(root, EnvKeyProvider())
        return self._vault

    def _scope(self, request):
        key = (request.get("workspace_id"), request.get("connection_id"))
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", value) for value in key):
            raise RuntimeErrorCode("invalid_payload")
        return key

    def _connection(self, key, request):
        connection = self.connections.get(key)
        if connection is None:
            raise RuntimeErrorCode("collector_offline")
        for field, expected in (("policy_revision", "expected_policy_revision"), ("auth_epoch", "expected_auth_epoch")):
            if expected in request and request[expected] != connection[field]:
                raise RuntimeErrorCode("source_changed")
        return connection

    def _manager(self, key, generation):
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise RuntimeErrorCode("auth_failed")
        cache_key = key + (generation,)
        if cache_key not in self.managers:
            connection = self.connections[key]
            template = (self.config.get("origins") or {}).get(connection["origin"])
            if not isinstance(template, dict):
                raise RuntimeErrorCode("collector_offline")
            browser = self.browsers.get(key)
            if browser is None:
                options = template.get("browser") or {}
                browser = PlaywrightBrowser.launch(
                    executable_path=os.environ.get("MCHECKER_COLLECTOR_CHROMIUM", ""),
                    args=options.get("args") or (), proxy=options.get("proxy"))
                self.browsers[key] = browser
            config = LoginConfig(origin=connection["origin"], **template["login"])
            def credentials():
                payload = json.loads(self.vault().get(key[0], key[1], generation))
                return Credentials(payload["username"], payload["password"])
            self.managers[cache_key] = SessionManager(SdaLogin(browser, config), credentials)
        manager = self.managers[cache_key]
        manager.auto_relogin = self.connections[key].get("auto_relogin", True)
        return manager

    def _execute(self, key, generation, operation, manual=False):
        manager = self._manager(key, generation)
        automatic = manager.auto_relogin
        if manual:
            manager.auto_relogin = True
        try:
            outcome = manager.execute(operation)
        finally:
            manager.auto_relogin = automatic
        if outcome.status != "succeeded":
            raise RuntimeErrorCode(outcome.reason_code or "auth_failed")
        return outcome

    def handle(self, request, cancel):
        key = self._scope(request)
        params = request.get("parameters") or {}
        if not isinstance(params, dict):
            raise RuntimeErrorCode("invalid_payload")
        operation = request.get("operation")
        if cancel.is_set():
            raise RuntimeErrorCode("canceled")
        if operation == "store_credentials":
            credentials = params["credentials"]
            Credentials(credentials["username"], credentials["password"])
            self.vault().put(*key, params["generation"], json.dumps(credentials).encode())
            return {"stored": True}
        if operation == "delete_credentials":
            generation = params["generation"]
            deleted = self.vault().delete(*key, generation)
            manager = self.managers.pop(key + (generation,), None)
            if manager:
                manager.close()
            self.verified = {ticket for ticket in self.verified if ticket[:3] != key + (generation,)}
            return {"deleted": deleted}
        if operation == "configure_connection":
            origin = params["origin"]
            if origin not in (self.config.get("origins") or {}):
                raise RuntimeErrorCode("collector_offline")
            for field in ("policy_revision", "auth_epoch"):
                if isinstance(params.get(field), bool) or not isinstance(params.get(field), int) or params[field] < 0:
                    raise RuntimeErrorCode("invalid_payload")
            old = self.connections.get(key)
            if old and (old["origin"] != origin or old["policy_revision"] != params["policy_revision"]):
                self._close_connection(key)
            self.connections[key] = dict(params)
            return {"configured": True}
        connection = self._connection(key, request)
        generation = params.get("generation", connection.get("generation"))
        if operation == "activate_credentials":
            if key + (generation, connection["auth_epoch"]) not in self.verified:
                raise RuntimeErrorCode("source_changed")
            return {"verified": True}
        if operation == "disable":
            self._close_connection(key)
            self.connections.pop(key, None)
            return {"disabled": True}
        if operation in ("verify_credentials", "ensure_session", "keepalive"):
            def probe(session):
                return SdaAdapter(session.origin, session.request_session()).probe("table_read")
            outcome = self._execute(key, generation, probe, manual=operation == "verify_credentials")
            if operation == "verify_credentials":
                self.verified.add(key + (generation, connection["auth_epoch"]))
            return {"ok": True, "status": "succeeded", "session_valid": True,
                    "renewal_observed": outcome.result.renewal_observed, "replayed": outcome.replayed}
        if operation == "read_resource":
            kind = params.get("kind")
            def read(session):
                adapter = SdaAdapter(session.origin, session.request_session())
                if kind == "catalog":
                    return adapter.read_catalog()
                if kind == "table":
                    return adapter.read_table(params.get("resource_ref"))
                if kind == "file":
                    return adapter.read_file(params.get("resource_ref"))
                raise RuntimeErrorCode("invalid_payload")
            outcome = self._execute(key, generation, read)
            data = outcome.result.data
            if kind == "catalog":
                return {"status": "succeeded", "items": data["items"], "session_valid": False}
            if kind == "table":
                if data.get("table_id") != params.get("resource_ref"):
                    raise RuntimeErrorCode("source_changed")
                document = data.get("document")
                if not isinstance(document, dict):
                    raise RuntimeErrorCode("invalid_payload")
                content = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                name = params.get("name") or "Parameters.json"
            else:
                if data.get("resource_ref") != params.get("resource_ref"):
                    raise RuntimeErrorCode("source_changed")
                encoded = data.get("content_base64")
                if not isinstance(encoded, str) or len(encoded) > ipc.MAX_CONTENT_BYTES * 4 // 3 + 4:
                    raise RuntimeErrorCode("too_large")
                content = base64.b64decode(encoded, validate=True)
                if len(content) != data.get("size") or hashlib.sha256(content).hexdigest() != data.get("content_sha256"):
                    raise RuntimeErrorCode("invalid_payload")
                name = data.get("name")
            if len(content) > ipc.MAX_CONTENT_BYTES:
                raise RuntimeErrorCode("too_large")
            if cancel.is_set():
                raise RuntimeErrorCode("canceled")
            return {"status": "succeeded", "content": content,
                    "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                    "name": name, "device_id": data.get("device_id"), "session_valid": True}
        raise RuntimeErrorCode("unsupported_operation")

    def _close_connection(self, key):
        for cache_key in list(self.managers):
            if cache_key[:2] == key:
                self.managers.pop(cache_key).close()
        browser = self.browsers.pop(key, None)
        if browser:
            browser.close()
        self.verified = {ticket for ticket in self.verified if ticket[:2] != key}

    def close(self):
        for key in list(self.browsers):
            self._close_connection(key)
