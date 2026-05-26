"""MCChecker - Configuration File Parser & Checker

A web tool built with NiceGUI for parsing and inspecting XML/JSON config files.
"""

import logging
import os
import pathlib

from nicegui import app, ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from app.core import storage, scheduler as sched
from app.utils.auth import is_deployer, get_client_ip
from app.pages.home import render_home_page

storage._ensure_dirs()

try:
    sched.setup_scheduled_update()
    sched.setup_cache_cleanup()
except Exception as e:
    logger.warning("Failed to initialize scheduler: %s", e)


@ui.page("/")
def index():
    render_home_page()


ui.run(
    title="MCChecker",
    favicon=pathlib.Path(__file__).parent / "app" / "static" / "favicon.ico",
    host="0.0.0.0",
    port=50001,
    reload=False,
    show=False,
)
