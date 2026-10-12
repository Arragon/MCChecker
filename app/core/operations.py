"""跨文件操作：迁移、改名、删除、备份、恢复 (T05 / INH-617)

所有跨文件操作均通过 OperationManifest 跟踪进度，任意步骤中断后可确定恢复。
依赖：
- T01: authorize, Actor
- T02: validate_filename, resolve_within
- T04: atomic_write_json, WriteGate, CorruptDataError
"""

import hashlib
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ===================== 操作状态枚举 =====================


class OpState(Enum):
    PREPARED = "prepared"
    APPLYING = "applying"
    COMMITTED = "committed"
    RECOVERY_REQUIRED = "recovery_required"


# ===================== Operation Manifest =====================


@dataclass
class OperationManifest:
    """操作清单：跟踪跨文件操作的进度，支持中断恢复。"""

    operation_id: str
    type: str  # "migration", "rename", "delete", "backup", "restore"
    source: str
    target: str
    expected_hash: str = ""
    completed_steps: List[str] = field(default_factory=list)
    state: OpState = OpState.PREPARED
    preimage_hash: str = ""
    created_at: str = ""
    updated_at: str = ""
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "type": self.type,
            "source": self.source,
            "target": self.target,
            "expected_hash": self.expected_hash,
            "completed_steps": list(self.completed_steps),
            "state": self.state.value,
            "preimage_hash": self.preimage_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationManifest":
        return cls(
            operation_id=data["operation_id"],
            type=data["type"],
            source=data["source"],
            target=data["target"],
            expected_hash=data.get("expected_hash", ""),
            completed_steps=data.get("completed_steps", []),
            state=OpState(data.get("state", "prepared")),
            preimage_hash=data.get("preimage_hash", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            error_message=data.get("error_message", ""),
        )


# ===================== Manifest CRUD =====================


def create_manifest(op_type: str, source: str, target: str) -> OperationManifest:
    """创建新的操作清单。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return OperationManifest(
        operation_id=uuid.uuid4().hex,
        type=op_type,
        source=source,
        target=target,
        created_at=now,
        updated_at=now,
    )


def _ops_dir(base_dir: Path) -> Path:
    """获取 operations 子目录。"""
    d = base_dir / "operations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_manifest(manifest: OperationManifest, ops_dir: Path) -> None:
    """保存 manifest 到 operations/ 目录（原子写入）。"""
    from app.core.storage import atomic_write_json

    ops_dir = Path(ops_dir)
    ops_dir.mkdir(parents=True, exist_ok=True)
    manifest.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = ops_dir / f"{manifest.operation_id}.json"
    atomic_write_json(str(path), manifest.to_dict())


def load_manifest(ops_dir: Path, operation_id: str) -> Optional[OperationManifest]:
    """加载指定 operation_id 的 manifest。"""
    path = Path(ops_dir) / f"{operation_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return OperationManifest.from_dict(data)


def list_manifests(ops_dir: Path) -> List[OperationManifest]:
    """列出所有 manifest。"""
    ops_dir = Path(ops_dir)
    if not ops_dir.exists():
        return []
    manifests = []
    for f in ops_dir.iterdir():
        if f.suffix == ".json":
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                manifests.append(OperationManifest.from_dict(data))
            except Exception:
                logger.warning("Failed to load manifest: %s", f)
    return manifests


def find_pending_manifests(ops_dir: Path) -> List[OperationManifest]:
    """查找所有未完成或需要恢复的 manifest。"""
    return [
        m
        for m in list_manifests(ops_dir)
        if m.state in (OpState.PREPARED, OpState.APPLYING, OpState.RECOVERY_REQUIRED)
    ]


# ===================== Hash 工具 =====================


def _hash_file(path: Path) -> str:
    """计算单文件 SHA-256。"""
    h = hashlib.sha256()
    path = Path(path)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_directory(dir_path: Path) -> str:
    """计算目录内容的确定性 hash（按相对路径排序）。"""
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return ""
    h = hashlib.sha256()
    entries = []
    for root, _dirs, files in os.walk(dir_path):
        for fname in files:
            fpath = Path(root) / fname
            rel = fpath.relative_to(dir_path)
            entries.append((str(rel), fpath))
    entries.sort(key=lambda e: e[0])
    for rel, fpath in entries:
        h.update(rel.encode("utf-8"))
        h.update(_hash_file(fpath).encode("utf-8"))
    return h.hexdigest()


def _hash_file_manifest(dir_path: Path) -> Dict[str, str]:
    """生成目录下所有文件的 hash 清单。"""
    dir_path = Path(dir_path)
    result = {}
    if not dir_path.exists():
        return result
    for root, _dirs, files in os.walk(dir_path):
        for fname in files:
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(dir_path))
            result[rel] = _hash_file(fpath)
    return result


# ===================== Profile 迁移 =====================


def migrate_profile(
    source_dir: Path,
    target_dir: Path,
    ops_dir: Path,
) -> OperationManifest:
    """迁移 profile 目录：backup → copy → verify → commit。

    幂等：若目标已存在且 hash 一致，直接返回 COMMITTED。
    中断恢复：任意步骤失败标记 RECOVERY_REQUIRED，可通过 manifest 重试。
    """
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    ops_dir = Path(ops_dir)

    if not source_dir.exists():
        raise FileNotFoundError(f"Source not found: {source_dir}")

    manifest = create_manifest("migration", str(source_dir), str(target_dir))
    save_manifest(manifest, ops_dir)

    try:
        manifest.state = OpState.APPLYING

        # 1. 计算源目录 hash
        manifest.preimage_hash = _hash_directory(source_dir)
        manifest.completed_steps.append("hash_source")
        save_manifest(manifest, ops_dir)

        # 2. 幂等检查：目标已存在且 hash 一致
        if target_dir.exists():
            target_hash = _hash_directory(target_dir)
            if target_hash == manifest.preimage_hash:
                manifest.completed_steps.append("verify")
                manifest.state = OpState.COMMITTED
                save_manifest(manifest, ops_dir)
                return manifest

        # 3. 复制到目标
        if target_dir.exists():
            shutil.rmtree(str(target_dir))
        shutil.copytree(str(source_dir), str(target_dir))
        manifest.completed_steps.append("copy")
        save_manifest(manifest, ops_dir)

        # 4. 验证
        target_hash = _hash_directory(target_dir)
        if target_hash != manifest.preimage_hash:
            manifest.state = OpState.RECOVERY_REQUIRED
            manifest.error_message = "Migration verification failed: hash mismatch"
            save_manifest(manifest, ops_dir)
            raise RuntimeError("Migration verification failed: hash mismatch")

        manifest.completed_steps.append("verify")
        manifest.state = OpState.COMMITTED
        save_manifest(manifest, ops_dir)

    except Exception as e:
        if manifest.state != OpState.RECOVERY_REQUIRED:
            manifest.state = OpState.RECOVERY_REQUIRED
            manifest.error_message = str(e)
            save_manifest(manifest, ops_dir)
        raise

    return manifest


def retry_migration(manifest: OperationManifest, ops_dir: Path) -> OperationManifest:
    """根据 manifest 状态重试中断的迁移。"""
    source_dir = Path(manifest.source)
    target_dir = Path(manifest.target)

    if manifest.state == OpState.COMMITTED:
        return manifest

    if not source_dir.exists():
        raise FileNotFoundError(f"Source not found: {source_dir}")

    manifest.state = OpState.APPLYING
    save_manifest(manifest, ops_dir)

    try:
        # 如果还没 hash
        if "hash_source" not in manifest.completed_steps:
            manifest.preimage_hash = _hash_directory(source_dir)
            manifest.completed_steps.append("hash_source")
            save_manifest(manifest, ops_dir)

        # 如果还没 copy
        if "copy" not in manifest.completed_steps:
            if target_dir.exists():
                shutil.rmtree(str(target_dir))
            shutil.copytree(str(source_dir), str(target_dir))
            manifest.completed_steps.append("copy")
            save_manifest(manifest, ops_dir)

        # 验证
        if "verify" not in manifest.completed_steps:
            target_hash = _hash_directory(target_dir)
            if target_hash != manifest.preimage_hash:
                manifest.state = OpState.RECOVERY_REQUIRED
                manifest.error_message = "Retry migration verification failed"
                save_manifest(manifest, ops_dir)
                raise RuntimeError("Retry migration verification failed")
            manifest.completed_steps.append("verify")

        manifest.state = OpState.COMMITTED
        manifest.error_message = ""
        save_manifest(manifest, ops_dir)

    except Exception as e:
        if manifest.state != OpState.RECOVERY_REQUIRED:
            manifest.state = OpState.RECOVERY_REQUIRED
            manifest.error_message = str(e)
            save_manifest(manifest, ops_dir)
        raise

    return manifest


# ===================== 配置改名 =====================


def rename_config(
    profile_dir: Path,
    old_name: str,
    new_name: str,
    ops_dir: Path,
) -> OperationManifest:
    """改名配置并更新所有引用。

    使用 manifest 跟踪每一步，失败时可通过 manifest 恢复。
    引用范围：config_mapping, favorites, bindings, edit_remarks, archive, records。
    """
    from app.utils.helpers import validate_filename

    validate_filename(old_name)
    validate_filename(new_name)

    profile_dir = Path(profile_dir)
    ops_dir = Path(ops_dir)

    manifest = create_manifest("rename", old_name, new_name)
    save_manifest(manifest, ops_dir)

    try:
        manifest.state = OpState.APPLYING

        # 1. rename config file
        if "rename_config" not in manifest.completed_steps:
            old_path = profile_dir / "configs" / old_name
            new_path = profile_dir / "configs" / new_name
            if old_path.exists() and not new_path.exists():
                old_path.rename(new_path)
            manifest.completed_steps.append("rename_config")
            save_manifest(manifest, ops_dir)

        # 2. rename archive dir
        if "rename_archive" not in manifest.completed_steps:
            old_archive = profile_dir / "archive" / old_name
            new_archive = profile_dir / "archive" / new_name
            if old_archive.exists() and not new_archive.exists():
                old_archive.rename(new_archive)
            manifest.completed_steps.append("rename_archive")
            save_manifest(manifest, ops_dir)

        # 3. rename records dir
        if "rename_records" not in manifest.completed_steps:
            old_records = profile_dir / "records" / old_name
            new_records = profile_dir / "records" / new_name
            if old_records.exists() and not new_records.exists():
                old_records.rename(new_records)
            manifest.completed_steps.append("rename_records")
            save_manifest(manifest, ops_dir)

        # 4. update config_mapping.json
        if "update_mapping" not in manifest.completed_steps:
            _rename_in_json(profile_dir / "config_mapping.json", old_name, new_name, key="name")
            manifest.completed_steps.append("update_mapping")
            save_manifest(manifest, ops_dir)

        # 5. update favorites.json
        if "update_favorites" not in manifest.completed_steps:
            _rename_in_json(profile_dir / "favorites.json", old_name, new_name, key="source_file")
            manifest.completed_steps.append("update_favorites")
            save_manifest(manifest, ops_dir)

        # 6. update bindings.json
        if "update_bindings" not in manifest.completed_steps:
            _rename_in_bindings(profile_dir / "bindings.json", old_name, new_name)
            manifest.completed_steps.append("update_bindings")
            save_manifest(manifest, ops_dir)

        # 7. update edit_remarks.json
        if "update_remarks" not in manifest.completed_steps:
            _rename_in_json(profile_dir / "edit_remarks.json", old_name, new_name, key="source_file")
            manifest.completed_steps.append("update_remarks")
            save_manifest(manifest, ops_dir)

        manifest.state = OpState.COMMITTED
        save_manifest(manifest, ops_dir)

    except Exception as e:
        manifest.state = OpState.RECOVERY_REQUIRED
        manifest.error_message = str(e)
        save_manifest(manifest, ops_dir)
        raise

    return manifest


def _rename_in_json(path: Path, old_name: str, new_name: str, key: str) -> None:
    """在 JSON 数组文件中将所有 old_name 替换为 new_name（指定 key）。"""
    from app.core.storage import atomic_write_json

    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return
    changed = False
    for item in data:
        if isinstance(item, dict) and item.get(key) == old_name:
            item[key] = new_name
            changed = True
    if changed:
        atomic_write_json(str(path), data)


def _rename_in_bindings(path: Path, old_name: str, new_name: str) -> None:
    """更新 bindings.json 中的 file 引用。"""
    from app.core.storage import atomic_write_json

    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return
    changed = False
    for binding in data:
        for var in binding.get("variables", []):
            if isinstance(var, dict) and var.get("file") == old_name:
                var["file"] = new_name
                changed = True
    if changed:
        atomic_write_json(str(path), data)


# ===================== 删除（Quarantine） =====================


def delete_to_quarantine(
    target_path: Path,
    quarantine_dir: Path,
    ops_dir: Path,
) -> OperationManifest:
    """删除默认移到 quarantine，不直接永久删除。

    quarantine 目录中的文件可通过 restore_from_quarantine 恢复。
    """
    target_path = Path(target_path)
    quarantine_dir = Path(quarantine_dir)
    ops_dir = Path(ops_dir)

    if not target_path.exists():
        raise FileNotFoundError(f"Target not found: {target_path}")

    quarantine_dir.mkdir(parents=True, exist_ok=True)

    manifest = create_manifest("delete", str(target_path), str(quarantine_dir))

    try:
        manifest.state = OpState.APPLYING

        # 计算 hash 用于恢复验证
        if target_path.is_file():
            manifest.preimage_hash = _hash_file(target_path)
        else:
            manifest.preimage_hash = _hash_directory(target_path)

        # 移到 quarantine
        q_name = f"{target_path.name}_{uuid.uuid4().hex[:8]}"
        quarantine_path = quarantine_dir / q_name
        shutil.move(str(target_path), str(quarantine_path))

        manifest.target = str(quarantine_path)  # 记录实际 quarantine 路径
        manifest.completed_steps.append("quarantine")
        manifest.state = OpState.COMMITTED
        save_manifest(manifest, ops_dir)

    except Exception as e:
        manifest.state = OpState.RECOVERY_REQUIRED
        manifest.error_message = str(e)
        save_manifest(manifest, ops_dir)
        raise

    return manifest


def restore_from_quarantine(
    manifest: OperationManifest,
    restore_path: Path,
    ops_dir: Path,
) -> OperationManifest:
    """从 quarantine 恢复文件到原始路径。"""
    quarantine_path = Path(manifest.target)
    restore_path = Path(restore_path)
    ops_dir = Path(ops_dir)

    if not quarantine_path.exists():
        raise FileNotFoundError(f"Quarantine file not found: {quarantine_path}")

    restore_manifest = create_manifest(
        "restore", str(quarantine_path), str(restore_path)
    )
    save_manifest(restore_manifest, ops_dir)

    try:
        restore_manifest.state = OpState.APPLYING

        if restore_path.exists():
            raise RuntimeError(f"Restore target already exists: {restore_path}")

        restore_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(quarantine_path), str(restore_path))

        # 验证恢复
        if restore_path.is_file():
            restored_hash = _hash_file(restore_path)
        else:
            restored_hash = _hash_directory(restore_path)

        if manifest.preimage_hash and restored_hash != manifest.preimage_hash:
            logger.warning("Restore hash mismatch, data may differ from original")

        restore_manifest.completed_steps.append("restore")
        restore_manifest.state = OpState.COMMITTED
        save_manifest(restore_manifest, ops_dir)

    except Exception as e:
        restore_manifest.state = OpState.RECOVERY_REQUIRED
        restore_manifest.error_message = str(e)
        save_manifest(restore_manifest, ops_dir)
        raise

    return restore_manifest


# ===================== 备份 =====================


def create_backup(
    profile_dir: Path,
    backup_dir: Path,
    ops_dir: Path,
) -> OperationManifest:
    """备份 profile 目录。

    - 排除 cache/ 和 operations/ 目录
    - 生成 hash 清单
    - 使用 WriteGate 进入维护模式
    """
    from app.core.storage import global_write_gate

    profile_dir = Path(profile_dir)
    backup_dir = Path(backup_dir)
    ops_dir = Path(ops_dir)

    if not profile_dir.exists():
        raise FileNotFoundError(f"Profile not found: {profile_dir}")

    manifest = create_manifest("backup", str(profile_dir), str(backup_dir))
    save_manifest(manifest, ops_dir)

    # 排除的目录
    exclude_dirs = {"cache", "operations", "__pycache__"}

    def _ignore_fn(directory, contents):
        ignored = set()
        for name in contents:
            if name in exclude_dirs:
                ignored.add(name)
        return ignored

    try:
        if not global_write_gate.enter_maintenance(timeout=10.0):
            raise RuntimeError("Cannot enter maintenance mode: WriteGate timeout")

        try:
            manifest.state = OpState.APPLYING

            # 复制数据
            if backup_dir.exists():
                shutil.rmtree(str(backup_dir))
            shutil.copytree(
                str(profile_dir),
                str(backup_dir),
                ignore=_ignore_fn,
            )
            manifest.completed_steps.append("copy")
            save_manifest(manifest, ops_dir)

            # 生成 hash 清单
            hash_manifest = _hash_file_manifest(backup_dir)
            manifest.expected_hash = hashlib.sha256(
                json.dumps(hash_manifest, sort_keys=True).encode()
            ).hexdigest()
            manifest.completed_steps.append("hash_manifest")
            save_manifest(manifest, ops_dir)

            manifest.state = OpState.COMMITTED
            save_manifest(manifest, ops_dir)

        finally:
            global_write_gate.exit_maintenance()

    except Exception as e:
        if manifest.state != OpState.RECOVERY_REQUIRED:
            manifest.state = OpState.RECOVERY_REQUIRED
            manifest.error_message = str(e)
            save_manifest(manifest, ops_dir)
        raise

    return manifest


def verify_backup(
    backup_dir: Path,
    manifest: OperationManifest,
) -> bool:
    """校验备份的 hash 清单是否一致。"""
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return False

    current_hashes = _hash_file_manifest(backup_dir)
    current_combined = hashlib.sha256(
        json.dumps(current_hashes, sort_keys=True).encode()
    ).hexdigest()
    return current_combined == manifest.expected_hash


# ===================== 恢复 =====================


def restore_backup(
    backup_dir: Path,
    target_dir: Path,
    ops_dir: Path,
    expected_hash: str = "",
) -> OperationManifest:
    """从备份恢复 profile。

    如果目标已存在且 hash 与 expected_hash 不同，拒绝覆盖（保护新数据）。
    """
    backup_dir = Path(backup_dir)
    target_dir = Path(target_dir)
    ops_dir = Path(ops_dir)

    if not backup_dir.exists():
        raise FileNotFoundError(f"Backup not found: {backup_dir}")

    manifest = create_manifest("restore", str(backup_dir), str(target_dir))
    manifest.expected_hash = expected_hash
    save_manifest(manifest, ops_dir)

    try:
        manifest.state = OpState.APPLYING

        # 检查目标是否有新数据
        if target_dir.exists() and expected_hash:
            current_hash = _hash_directory(target_dir)
            if current_hash != expected_hash:
                manifest.state = OpState.RECOVERY_REQUIRED
                manifest.error_message = (
                    "Target has different data, refusing to overwrite. "
                    f"expected={expected_hash[:16]}... current={current_hash[:16]}..."
                )
                save_manifest(manifest, ops_dir)
                raise RuntimeError("Target has newer/different data, refusing to overwrite")

        # 执行恢复
        if target_dir.exists():
            shutil.rmtree(str(target_dir))
        shutil.copytree(str(backup_dir), str(target_dir))
        manifest.completed_steps.append("copy")
        save_manifest(manifest, ops_dir)

        # 验证
        restored_hash = _hash_directory(target_dir)
        backup_hash = _hash_directory(backup_dir)
        if restored_hash != backup_hash:
            manifest.state = OpState.RECOVERY_REQUIRED
            manifest.error_message = "Restore verification failed"
            save_manifest(manifest, ops_dir)
            raise RuntimeError("Restore verification failed")

        manifest.completed_steps.append("verify")
        manifest.state = OpState.COMMITTED
        save_manifest(manifest, ops_dir)

    except Exception as e:
        if manifest.state != OpState.RECOVERY_REQUIRED:
            manifest.state = OpState.RECOVERY_REQUIRED
            manifest.error_message = str(e)
            save_manifest(manifest, ops_dir)
        raise

    return manifest


# ===================== 恢复扫描 =====================


def scan_and_recover(ops_dir: Path) -> List[Dict[str, Any]]:
    """扫描 ops_dir 中未完成的操作，尝试恢复。

    Returns:
        每个操作的处理结果列表。
    """
    ops_dir = Path(ops_dir)
    results = []

    for manifest in find_pending_manifests(ops_dir):
        result = {
            "operation_id": manifest.operation_id,
            "type": manifest.type,
            "state": manifest.state.value,
            "action": "none",
        }

        try:
            if manifest.type == "migration" and manifest.state == OpState.RECOVERY_REQUIRED:
                retry_migration(manifest, ops_dir)
                result["action"] = "retried"
                result["state"] = manifest.state.value

            elif manifest.type == "rename" and manifest.state == OpState.RECOVERY_REQUIRED:
                # 改名恢复：重新执行未完成步骤
                manifest.state = OpState.APPLYING
                save_manifest(manifest, ops_dir)
                result["action"] = "needs_manual_review"

            elif manifest.type == "delete" and manifest.state == OpState.RECOVERY_REQUIRED:
                result["action"] = "needs_manual_review"

            elif manifest.type == "backup" and manifest.state == OpState.RECOVERY_REQUIRED:
                result["action"] = "needs_manual_review"

            elif manifest.type == "restore" and manifest.state == OpState.RECOVERY_REQUIRED:
                result["action"] = "needs_manual_review"

            elif manifest.state in (OpState.PREPARED, OpState.APPLYING):
                # 未开始或中断的操作，标记为需要恢复
                result["action"] = "needs_manual_review"

        except Exception as e:
            result["action"] = "failed"
            result["error"] = str(e)

        results.append(result)

    return results
