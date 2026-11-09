"""Run an isolated MCChecker + actual collector + loopback TLS SDA for hands-on review."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.sda_simulator import CHROME, ORIGIN, SdaSimulator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=50009)
    parser.add_argument("--collector-python", default=str(ROOT / ".venv-collector/bin/python"))
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="mcc-sda-review-"))
    os.chmod(root, 0o700)
    data = root / "data"
    configs = data / "profiles/default/configs"
    configs.mkdir(parents=True)
    (configs / "Simulation.xml").write_bytes(b"<config/>")
    (data / "ip_mapping.json").write_text(json.dumps([{"ip": "127.0.0.1", "name": "模拟环境管理员"}], ensure_ascii=False))
    (data / "admin_users.json").write_text(json.dumps([{"name": "模拟环境管理员"}], ensure_ascii=False))
    base = "http://127.0.0.1:%d" % args.port
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(method, path, body=None, extra_headers=None):
        headers = {"X-Requested-With": "MCChecker", "X-Workspace-Id": "default", "Content-Type": "application/json"}
        headers.update(extra_headers or {})
        request = urllib.request.Request(base + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        with opener.open(request, timeout=35) as response:
            return json.load(response)
    with SdaSimulator(root) as sim, (root / "app.log").open("wb") as log:
        sim.idle_seconds = 75
        env = dict(os.environ, MCHECKER_COLLECTOR_CONFIG=str(sim.config), MCHECKER_COLLECTOR_PYTHON=args.collector_python,
                   MCHECKER_SECRET_ROOT=str(root / "vault"), MCHECKER_SECRET_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode(),
                   MCHECKER_COLLECTOR_CHROMIUM=CHROME, NODE_EXTRA_CA_CERTS=str(sim.cert))
        proc = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--host", "127.0.0.1", "--port", str(args.port),
                                 "--no-reload", "--data-dir", str(data)], cwd=str(ROOT), env=env, stdout=log, stderr=log)
        try:
            ready = False
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError("isolated application exited; see " + str(root / "app.log"))
                try:
                    files = api("GET", "/api/v3/workspaces/default/profiles/default/file-selector")
                    ready = True
                    break
                except OSError:
                    time.sleep(.1)
            if not ready:
                raise RuntimeError("isolated application did not become ready")
            fid = next(row["file_id"] for row in files["items"] if row["name"] == "Simulation.xml")
            connection = api("POST", "/api/v3/workspaces/default/connections", {"name": "SDA 模拟环境", "origin": ORIGIN})
            path = "/api/v3/workspaces/default/connections/" + connection["connection_id"]
            candidate = api("POST", path + "/credentials/candidates", {"username": "sim-admin", "password": "sim-password"})["candidate"]
            probe = api("POST", path + "/credentials/probe", {"candidate_id": candidate["candidate_id"]})["operation"]
            if probe["state"] != "succeeded":
                raise RuntimeError("simulation login did not verify: " + str(probe.get("reason_code")))
            activated = api("POST", path + "/credentials/candidates/" + candidate["candidate_id"] + "/activate", {"probe_epoch": candidate["auth_epoch"]})["operation"]
            if activated["state"] != "succeeded":
                raise RuntimeError("simulation credential activation failed")
            api("PUT", path + "/heartbeat", {"enabled": True, "interval_minutes": 1}, {"If-Match": "0"})
            api("POST", "/api/v3/workspaces/default/sync/sources", {"profile_id": "default", "name": "SDA 模拟配置.xml", "kind": "sda_file", "connection_id": connection["connection_id"], "resource_ref": "file-1", "display_path": "/simulation/MachineConstant.xml", "target": {"kind": "file_archive", "file_id": fid}})
            print("SIMULATION ONLY: " + base + "/index.html#/p/default/updates/sources?workspace=default", flush=True)
            print("Isolated data: " + str(data), flush=True)
            started = time.monotonic()
            while proc.poll() is None:
                (root / "simulation-status.json").write_text(json.dumps({"simulation": True, "elapsed_seconds": round(time.monotonic() - started, 1), "idle_seconds": sim.idle_seconds, "logins": sim.logins, "protected_reads": sim.protected_reads}))
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
