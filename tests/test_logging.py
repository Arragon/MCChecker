"""日志与错误（§13；M4-03 / INH-1905）。

验证：格式、控制台 + RotatingFileHandler(10 MB×5)、request_id、访问日志不含 query 凭据、
业务日志字段、未知异常响应无堆栈而日志有堆栈、日志目录在数据根下。
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from authz_fixtures import as_role, make_server, seed_roles, write_target

from app.config import Settings
from app.core import log_context, storage
from app.server.http import MCServer
from app.server.router import build_router
from run import LOG_FORMAT, configure_logging

FILE_A = "a.xml"


class Base:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        root = logging.getLogger()
        self._old_handlers = list(root.handlers)
        self._old_level = root.level
        configure_logging("INFO", str(self.tmp_path / "data"))
        self.log_path = self.tmp_path / "data" / "logs" / "mcchecker.log"

    def teardown_method(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in self._old_handlers:
            root.addHandler(handler)
        root.setLevel(self._old_level)
        self._tmp.cleanup()

    def flush(self):
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass

    def lines(self):
        self.flush()
        return self.log_path.read_text(encoding="utf-8").splitlines()


class TestHandlers(Base):
    def test_console_and_rotating_file_handlers(self):
        root = logging.getLogger()
        handlers = {type(handler).__name__: handler for handler in root.handlers}
        assert "StreamHandler" in handlers, handlers
        rotating = handlers.get("RotatingFileHandler")
        assert rotating is not None, handlers
        assert rotating.maxBytes == 10 * 1024 * 1024, rotating.maxBytes
        assert rotating.backupCount == 5, rotating.backupCount
        assert rotating.encoding.lower() == "utf-8", rotating.encoding
        assert rotating.baseFilename.startswith(str(self.tmp_path)), rotating.baseFilename
        assert rotating.formatter._fmt == LOG_FORMAT, rotating.formatter._fmt

    def test_request_id_is_injected(self):
        token = log_context.set_request_id("abc123def456")
        try:
            logging.getLogger("mcchecker.test").info("hello")
        finally:
            log_context.reset_request_id(token)
        assert any("[abc123def456] hello" in line for line in self.lines()), self.lines()

    def test_business_fields(self):
        log_context.business(
            "file_save", profile="p1", actor="甲", target=FILE_A, result="ok", duration_ms=1.0
        )
        line = [line for line in self.lines() if "event=file_save" in line][-1]
        assert "profile=p1" in line and "actor=甲" in line and "target=a.xml" in line, line
        assert "result=ok" in line and "duration_ms=1.0" in line, line


class TestAccessLog(Base):
    def test_access_log_has_no_query_credentials_and_carries_request_id(self):
        pid = seed_roles("logs")
        write_target(pid, FILE_A, b"<R/>")
        with make_server() as server:
            status, _h, _body = as_role(server, "visitor").request(
                "GET", "/api/v1/p/%s/files?token=SECRET" % pid
            )
            assert status == 200
        text = "\n".join(self.lines())
        assert "GET /api/v1/p/%s/files 200" % pid in text, text
        assert "token=SECRET" not in text, text
        assert re.search(r"\[\w{12}\] GET /api/v1/p/%s/files 200" % pid, text), text
        assert "ip=" in text and "pid=%s" % pid in text, text

    def test_file_save_emits_business_log(self):
        pid = seed_roles("logs2")
        with make_server() as server:
            status, _h, body = as_role(server, "deployer").request(
                "PUT",
                "/api/v1/p/%s/files/%s?overwrite=1" % (pid, FILE_A),
                body=b"<R><x>1</x></R>",
            )
            assert status == 201, body
        text = "\n".join(self.lines())
        assert "event=file_save" in text, text
        assert "profile=%s" % pid in text and "target=%s" % FILE_A in text, text
        assert "result=created" in text and "duration_ms=" in text, text


class TestUnknownException(Base):
    def test_response_has_no_stack_but_log_does(self):
        def boom(ctx, params, query, body):
            raise RuntimeError("boom-secret")

        router = build_router(
            Settings(
                host="127.0.0.1", port=0, data_dir=storage.DATA_DIR,
                web_dir="app/web", trusted_proxies=("127.0.0.1",),
            )
        )
        router.add("GET", "/api/v1/p/{pid}/boom", boom)
        pid = seed_roles("logs3")
        with make_server(router=lambda _settings: router) as server:
            status, _h, raw = as_role(server, "visitor").request(
                "GET", "/api/v1/p/%s/boom" % pid
            )
        assert status == 500, raw
        text = raw.decode("utf-8")
        assert "Traceback" not in text and "boom-secret" not in text, text
        log_text = "\n".join(self.lines())
        assert "Traceback" in log_text and "boom-secret" in log_text, log_text
