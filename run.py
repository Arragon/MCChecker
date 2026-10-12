"""MCChecker - Configuration File Parser & Checker

A web tool built with NiceGUI for parsing and inspecting XML/JSON config files.
"""

import logging
import os
import pathlib
import secrets

from nicegui import app as nice_app, ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from app.core import storage, scheduler as sched
from app.utils.auth import is_deployer, get_client_ip
from app.pages.home import render_home_page
from app.pages.file_downloads import register_download_routes
import app.pages.record_view

nice_app.add_static_files(
    "/static",
    str(pathlib.Path(__file__).parent / "app" / "static"),
)
register_download_routes()

storage.ensure_profile_layout(migrate_legacy=True)
storage._ensure_dirs()

try:
    sched.setup_scheduled_update()
    sched.setup_cache_cleanup()
except Exception as e:
    logger.warning("Failed to initialize scheduler: %s", e)


@ui.page("/")
def index():
    render_home_page()


def _get_storage_secret() -> str:
    env_secret = os.getenv("NICEGUI_STORAGE_SECRET")
    if env_secret:
        return env_secret

    secret_file = os.path.join(storage.DATA_DIR, "nicegui_storage_secret.txt")
    try:
        if os.path.exists(secret_file):
            with open(secret_file, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                return s
    except OSError:
        pass

    s = secrets.token_urlsafe(32)
    try:
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(s)
    except OSError:
        pass
    return s


def _get_port() -> int:
    """从环境变量获取端口，默认 50001"""
    raw = os.getenv("MCHECKER_PORT")
    if not raw:
        return 50001
    try:
        port = int(str(raw).strip())
    except ValueError:
        logger.warning("Invalid MCHECKER_PORT: %s, using default 50001", raw)
        return 50001
    if port < 1 or port > 65535:
        logger.warning("MCHECKER_PORT out of range: %s, using default 50001", port)
        return 50001
    return port


ui.run(
    title="MCChecker",
    favicon=pathlib.Path(__file__).parent / "app" / "static" / "favicon.ico",
    host="0.0.0.0",
    port=_get_port(),
    reload=False,
    show=False,
    storage_secret=_get_storage_secret(),
)
