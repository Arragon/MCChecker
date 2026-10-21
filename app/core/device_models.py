"""机型配置空间管理

用于为站点核心配置提供“机型维度”的隔离与切换。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from . import fsutil, locks, storage

DEFAULT_MODEL_ID = storage.DEFAULT_PROFILE
DEFAULT_MODEL_NAME = "默认机型"


def _models_file() -> str:
    return os.path.join(storage.DATA_DIR, "device_models.json")


def _ensure_default(models: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if any(m.get("id") == DEFAULT_MODEL_ID for m in models):
        return models
    return [{"id": DEFAULT_MODEL_ID, "name": DEFAULT_MODEL_NAME}] + models


def _load_models_unlocked() -> List[Dict[str, str]]:
    """读取机型清单（调用方已持注册表锁）。

    损坏的 JSON 交给 `fsutil.read_json` 抛 `CorruptDataError`：把注册表读成空表
    再写回等于静默丢掉所有机型（§5.4）。`default` 由本函数保证一定出现在结果里。
    """
    models = fsutil.read_json(_models_file(), [])
    if not isinstance(models, list):
        models = []
    models = [m for m in models if isinstance(m, dict) and m.get("id")]
    return _ensure_default(models)


def load_models() -> List[Dict[str, str]]:
    with locks.registry_lock:
        return _load_models_unlocked()


def _save_models_unlocked(models: List[Dict[str, str]]) -> None:
    """写机型清单（调用方已持注册表锁），走原子写。"""
    models = _ensure_default(models)
    unique: Dict[str, Dict[str, str]] = {}
    for m in models:
        mid = str(m.get("id") or "").strip()
        name = str(m.get("name") or "").strip() or mid
        if mid:
            unique[mid] = {"id": mid, "name": name}
    ordered = [unique[DEFAULT_MODEL_ID]] + [v for k, v in unique.items() if k != DEFAULT_MODEL_ID]
    fsutil.atomic_write_json(_models_file(), ordered)


def save_models(models: List[Dict[str, str]]) -> None:
    with locks.registry_lock:
        _save_models_unlocked(models)


def model_options() -> Dict[str, str]:
    return {m["id"]: m["name"] for m in load_models()}


def exists(model_id: str) -> bool:
    mid = str(model_id or "").strip()
    return any(m.get("id") == mid for m in load_models())


def _slugify(name: str) -> str:
    base = re.sub(r"\s+", "-", (name or "").strip().lower())
    base = re.sub(r"[^a-z0-9\\-_]", "", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base or "model"


def _unique_id(base: str, existing: set) -> str:
    if base not in existing and base != DEFAULT_MODEL_ID:
        return base
    i = 2
    while True:
        cand = f"{base}-{i}"
        if cand not in existing and cand != DEFAULT_MODEL_ID:
            return cand
        i += 1


def add_model(name: str, copy_from: Optional[str] = None) -> str:
    with locks.registry_lock:
        models = _load_models_unlocked()
        existing = {m["id"] for m in models}
        mid = _unique_id(_slugify(name), existing)
        models.append({"id": mid, "name": (name or "").strip() or mid})
        _save_models_unlocked(models)

    # 机型目录是数据空间。人员表已不在这里，不能再靠写 ip_mapping 把目录带出来。
    os.makedirs(storage.get_profile_data_dir(mid), exist_ok=True)

    # 复制数据放在注册表锁之外：那是重 I/O，§3.5 要求锁内不做大操作。
    if copy_from:
        storage.copy_profile_data(copy_from, mid)

    return mid


def remove_model(model_id: str, delete_data: bool = False) -> bool:
    mid = str(model_id or "").strip()
    if mid == DEFAULT_MODEL_ID:
        return False

    with locks.registry_lock:
        models = _load_models_unlocked()
        new_models = [m for m in models if m.get("id") != mid]
        if len(new_models) == len(models):
            return False
        _save_models_unlocked(new_models)

    if delete_data:
        storage.delete_profile_data(mid)

    return True


def rename_model(model_id: str, new_name: str) -> bool:
    mid = str(model_id or "").strip()
    name = str(new_name or "").strip()
    if not mid or not name:
        return False

    with locks.registry_lock:
        models = _load_models_unlocked()
        changed = False
        for m in models:
            if m.get("id") == mid:
                m["name"] = name
                changed = True
                break
        if not changed:
            return False
        _save_models_unlocked(models)
    return True


def normalize_selected(model_id: Optional[str]) -> str:
    mid = str(model_id or "").strip()
    if exists(mid):
        return mid
    return DEFAULT_MODEL_ID


def get_model_name(model_id: str) -> str:
    mid = str(model_id or "").strip()
    for m in load_models():
        if m.get("id") == mid:
            return m.get("name") or mid
    return mid or DEFAULT_MODEL_NAME


def get_created_at() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
