"""Loopback-only TLS SDA for integration tests and the isolated local review server."""
import base64
import hashlib
import json
import os
import select
import secrets
import socket
import ssl
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ORIGIN = "https://sda.sim.invalid"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
XML = b'<config><param_1 type="int" description="simulation">7</param_1></config>'


class SdaSimulator:
    def __init__(self, root):
        self.root = root
        self.sessions = {}
        self.logins = 0
        self.protected_reads = 0
        self.idle_seconds = 2
        self.fault = None
        self.delay = 0
        self.content = XML
        self.lock = threading.Lock()

    def __enter__(self):
        cert = self.root / "server.pem"
        key = self.root / "server.key"
        config = self.root / "openssl.cnf"
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=sda.sim.invalid\n[ext]\nsubjectAltName=DNS:sda.sim.invalid\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key), "-out", str(cert), "-config", str(config)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(key, 0o600)
        public = subprocess.check_output(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
        der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"], input=public, stdout=subprocess.PIPE, check=True).stdout
        spki = base64.b64encode(hashlib.sha256(der).digest()).decode()
        simulator = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def reply(self, body, status=200, content_type="application/json", cookie=None, location=None):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(raw)

            def form(self, message="", locked=False):
                body = '<form method="post" action="/login"><input id="username" name="username"><input id="password" name="password" type="password"><input type="hidden" name="csrf" value="simulation-only-csrf"><button id="submit">Login</button></form><p id="%s">%s</p>' % ("locked" if locked else "error", message)
                self.reply(body.encode(), content_type="text/html")

            def do_GET(self):
                if self.path == "/login":
                    self.form()
                elif self.path == "/app/home":
                    self.reply(b"<h1>Simulation only</h1>", content_type="text/html")
                elif self.path == "/synthetic/catalog":
                    self.reply({"ok": True, "resource_kind": "catalog", "items": [
                        {"name": "MachineConstant.xml", "kind": "file", "resource_ref": "file-1", "display_path": "/simulation/MachineConstant.xml"},
                        {"name": "Parameters.json", "kind": "table", "resource_ref": "table-1", "display_path": "/simulation/Parameters.json"}]})
                else:
                    self.reply({}, 404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if length > 1024 * 1024:
                    self.reply({}, 413)
                    return
                raw = self.rfile.read(length)
                if self.path == "/login":
                    fields = parse_qs(raw.decode())
                    simulator.logins += 1
                    if fields.get("username") == ["locked"]:
                        self.form("Locked", True)
                    elif fields.get("username") != ["sim-admin"] or fields.get("password") != ["sim-password"] or fields.get("csrf") != ["simulation-only-csrf"]:
                        self.form("Invalid credentials")
                    else:
                        token = secrets.token_hex(16)
                        with simulator.lock:
                            simulator.sessions[token] = time.monotonic() + simulator.idle_seconds
                        self.reply(b"", 303, cookie="sid=%s; Secure; HttpOnly; SameSite=Lax; Path=/" % token, location="/app/home")
                    return
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                token = cookie["sid"].value if "sid" in cookie else ""
                with simulator.lock:
                    valid = simulator.sessions.get(token, 0) > time.monotonic()
                    if valid:
                        simulator.sessions[token] = time.monotonic() + simulator.idle_seconds
                if not valid:
                    self.reply(b"", 500)
                    return
                simulator.protected_reads += 1
                if simulator.delay:
                    time.sleep(simulator.delay)
                if simulator.fault == "login_html":
                    self.reply(b"<html>login</html>", content_type="text/html")
                    return
                request = json.loads(raw)
                ref = request.get("resource_ref")
                renewal = "sid=%s; Secure; HttpOnly; SameSite=Lax; Path=/" % token
                if self.path == "/synthetic/table":
                    self.reply({"ok": True, "resource_kind": "table", "table": {"table_id": ref, "document": {"parameters": {"param_1": 7}}}}, cookie=renewal)
                elif self.path == "/synthetic/file":
                    body = simulator.content
                    self.reply({"ok": True, "resource_kind": "file", "file": {"resource_ref": ref, "name": "MachineConstant.xml", "size": len(body), "content_sha256": ("0" * 64 if simulator.fault == "bad_hash" else hashlib.sha256(body).hexdigest()), "content_base64": base64.b64encode(body).decode(), "device_id": "device-sim-1" if ref == "device-1" else None}}, cookie=renewal)
                else:
                    self.reply({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert), str(key))
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_CONNECT(self):
                if self.path != "sda.sim.invalid:443":
                    self.send_error(403)
                    return
                with socket.create_connection(simulator.server.server_address, timeout=5) as upstream:
                    self.send_response(200)
                    self.end_headers()
                    peers = [self.connection, upstream]
                    while True:
                        readable, _, _ = select.select(peers, [], [], 10)
                        if not readable:
                            return
                        for sock in readable:
                            data = sock.recv(65536)
                            if not data:
                                return
                            (upstream if sock is self.connection else self.connection).sendall(data)

        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        self.proxy.daemon_threads = True
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (self.server, self.proxy)]
        for thread in self.threads:
            thread.start()
        self.cert = cert
        self.config = self.root / "collector.json"
        self.config.write_text(json.dumps({"origins": {ORIGIN: {"login": {"login_path": "/login", "success_paths": ["/app/"], "username_selector": "#username", "password_selector": "#password", "submit_selector": "#submit", "error_selectors": ["#error"], "lock_selectors": ["#locked"], "timeout_ms": 1500}, "browser": {"proxy": {"server": "http://127.0.0.1:%d" % self.proxy.server_port}, "args": ["--ignore-certificate-errors-spki-list=" + spki]}}}}))
        return self

    def expire(self):
        with self.lock:
            self.sessions.clear()

    def __exit__(self, *args):
        for server in (self.proxy, self.server):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(3)
