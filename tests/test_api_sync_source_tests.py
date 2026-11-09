"""Unsaved source input is read through the real downloader without committing data."""
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from authz_fixtures import as_role, make_server, seed_roles
from app.core import storage
from app.services import file_versions, sync_sources, sync_status


def test_source_input_reads_actual_content_without_saving(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            content = {"/good": b"<R><x>1</x></R>", "/login": b"<html>login</html>"}.get(self.path, b"<broken")
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        seed_roles("source-input-test")
        with make_server() as server:
            admin, visitor = as_role(server, "admin"), as_role(server, "visitor")
            _, _, created = admin.json("POST", "/api/v3/workspaces", {"name": "Source tests"})
            wid = created["workspace"]["id"]
            with storage.use_workspace(wid), storage.use_profile("default"):
                storage.save_config_file("Existing.xml", b"<R/>")
                file_id = file_versions.current_identity("default", "Existing.xml")["file_id"]
            url = "/api/v3/workspaces/%s/sync/source-tests" % wid
            payload = {"profile_id": "default", "name": "Unstored.xml", "kind": "http_file",
                       "target": {"kind": "file_archive", "file_id": file_id},
                       "resource_ref": "http://127.0.0.1:%s/good" % httpd.server_address[1]}
            assert visitor.json("POST", url, payload)[0] == 403
            status, _, checked = admin.json("POST", url, payload)
            assert status == 200, checked
            assert checked["status"] == "succeeded" and checked["items"][0]["size"] == 15
            assert "content" not in checked["items"][0]
            payload["resource_ref"] = payload["resource_ref"].replace("/good", "/login")
            status, _, blocked = admin.json("POST", url, payload)
            assert status == 200 and blocked["status"] == "failed", blocked
            assert blocked["reason_code"] == "invalid_payload"
            payload["resource_ref"] = payload["resource_ref"].replace("/login", "/bad")
            assert admin.json("POST", url, payload)[0] == 422
            assert sync_sources.list_sources(wid)["total"] == 0
            assert sync_status.list_status(wid) == []
            with storage.use_workspace(wid), storage.use_profile("default"):
                assert storage.list_config_files() == ["Existing.xml"]
                assert storage.load_config_file("Existing.xml") == b"<R/>"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
