from __future__ import annotations

from typing import Any, Dict, List, Optional

from nicegui import app

from app.core import device_models, storage
from app.core import tab_manager


def tabs_storage_key(profile_id: str) -> str:
    pid = device_models.normalize_selected(profile_id)
    return f"tabs_state:{pid}"


def load_state(profile_id: str) -> Optional[Dict[str, Any]]:
    key = tabs_storage_key(profile_id)
    state = app.storage.user.get(key)
    if isinstance(state, dict):
        return state
    return None


def save_state(
    profile_id: str,
    tabs: List[Dict[str, Any]],
    active: str,
    history: Optional[List[str]] = None,
) -> None:
    key = tabs_storage_key(profile_id)
    tab_manager.ensure_opened_at(tabs)
    app.storage.user[key] = {
        "tabs": [{k: v for k, v in t.items() if not str(k).startswith("_")} for t in tabs],
        "active": active,
        "history": list(history or [])[-100:],
    }


def save_current(
    tabs: List[Dict[str, Any]],
    active: str,
    history: Optional[List[str]] = None,
) -> None:
    save_state(storage.get_active_profile(), tabs, active, history)

