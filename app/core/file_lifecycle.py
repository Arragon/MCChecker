"""文件生命周期管理：temp / current / archive / record

提供 FileRef 全链路路径解析与临时文件存储管理。
- TempStorage：session 隔离 + TTL + 容量上限
- resolve_file_path：根据 FileRef.kind 解析到实际磁盘路径
- create_file_ref：工厂函数

依赖：
- T01 authorize（写操作授权）
- T02 validate_filename（文件名安全校验）
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from app.core.models import FileKind, FileRef
from app.utils.helpers import validate_filename

# 延迟导入 auth（依赖 nicegui），避免测试时强制加载
# 实际使用时通过函数内 import 获取

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TEMP_DIR_NAME = "temporary"
TEMP_TTL_SECONDS = 3600          # 1 小时
TEMP_MAX_CAPACITY = 500 * 1024 * 1024  # 500 MB
_META_SUFFIX = ".meta.json"


# ---------------------------------------------------------------------------
# TempStorage
# ---------------------------------------------------------------------------


class TempStorage:
    """临时文件存储：session 隔离 + TTL + 容量上限

    每个临时文件伴随一个 .meta.json 侧车文件，记录 owner_session 与
    创建时间，用于 TTL 判定和 session 隔离，且可在进程重启后恢复元数据。
    """

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self.temp_dir = self.data_root / TEMP_DIR_NAME
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    def create_temp(
        self,
        content: bytes,
        owner_session: str,
        original_name: Optional[str] = None,
        *,
        actor=None,
        profile_id: str = "temp",
    ) -> FileRef:
        """写入临时文件，返回对应 FileRef。

        Args:
            content: 文件内容
            owner_session: 所属 session ID（用于隔离）
            original_name: 原始文件名（仅用于可读性，实际存储名含随机 token）
            actor: 操作者身份（传入时进行授权检查）
            profile_id: FileRef 中记录的 profile_id

        Raises:
            AuthorizationError: actor 非 deployer/system 且尝试写操作
            ValueError: 容量超限或文件名不合法
        """
        # 授权：访客可上传（action="write" 需要 deployer），但此处仅记录身份
        # 访客上传场景：actor 为 guest 时不阻止（由调用方决定）
        # 此处仅做防御性校验：若 actor 显式传入且非 guest，必须通过 authorize
        if actor is not None and not getattr(actor, "is_guest", True):
            from app.utils.auth import authorize
            authorize(actor, "write", profile_id)

        # 容量检查
        current_size = self._get_temp_size()
        if current_size + len(content) > TEMP_MAX_CAPACITY:
            raise ValueError(
                f"Temp storage capacity exceeded: "
                f"current={current_size}, requested={len(content)}, "
                f"limit={TEMP_MAX_CAPACITY}"
            )

        # 生成唯一文件名
        token = uuid.uuid4().hex
        safe_name = self._safe_original_name(original_name)
        file_name = f"{token}_{safe_name}"
        temp_path = self.temp_dir / file_name

        # 写入内容
        temp_path.write_bytes(content)

        # 写入元数据侧车
        self._write_meta(file_name, owner_session=owner_session)

        logger.info(
            "Created temp file: %s (session=%s, size=%d)",
            file_name, owner_session, len(content),
        )

        return FileRef(
            profile_id=profile_id,
            kind=FileKind.TEMP,
            name=file_name,
            version_or_token=token,
        )

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_temp(self, ref: FileRef, session: str) -> Optional[Path]:
        """获取临时文件路径。

        规则：
        - kind 必须为 TEMP
        - session 必须与 owner_session 一致（隔离）
        - 超过 TTL 时删除文件并返回 None（不回退同名 persistent 文件）

        Raises:
            ValueError: ref.kind 不是 TEMP
        """
        if ref.kind != FileKind.TEMP:
            raise ValueError(f"Not a temp file: kind={ref.kind!r}")

        temp_path = self.temp_dir / ref.name

        if not temp_path.is_file():
            return None

        # Session 隔离检查
        meta = self._read_meta(ref.name)
        owner = meta.get("owner_session", "")
        if owner and owner != session:
            logger.warning(
                "Session isolation: ref owner=%r, requester=%r",
                owner, session,
            )
            return None

        # TTL 检查
        mtime = temp_path.stat().st_mtime
        if time.time() - mtime > TEMP_TTL_SECONDS:
            logger.info("Temp file expired, removing: %s", ref.name)
            temp_path.unlink(missing_ok=True)
            self._remove_meta(ref.name)
            return None

        return temp_path

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """清理所有过期临时文件，返回清理数量。"""
        count = 0
        now = time.time()
        for f in self.temp_dir.iterdir():
            if not f.is_file():
                continue
            if f.name.endswith(_META_SUFFIX):
                continue
            if now - f.stat().st_mtime > TEMP_TTL_SECONDS:
                f.unlink(missing_ok=True)
                self._remove_meta(f.name)
                count += 1
        if count:
            logger.info("Cleaned up %d expired temp files", count)
        return count

    def get_temp_usage(self) -> int:
        """返回当前临时文件总占用字节数。"""
        return self._get_temp_size()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get_temp_size(self) -> int:
        total = 0
        for f in self.temp_dir.iterdir():
            if f.is_file() and not f.name.endswith(_META_SUFFIX):
                total += f.stat().st_size
        return total

    def _meta_path(self, file_name: str) -> Path:
        return self.temp_dir / (file_name + _META_SUFFIX)

    def _write_meta(self, file_name: str, *, owner_session: str) -> None:
        meta = {
            "owner_session": owner_session,
            "created_at": time.time(),
        }
        self._meta_path(file_name).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

    def _read_meta(self, file_name: str) -> Dict:
        mp = self._meta_path(file_name)
        if not mp.is_file():
            return {}
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read temp meta %s: %s", mp, exc)
            return {}

    def _remove_meta(self, file_name: str) -> None:
        self._meta_path(file_name).unlink(missing_ok=True)

    @staticmethod
    def _safe_original_name(original_name: Optional[str]) -> str:
        """将原始文件名转为安全片段；无效时返回 'upload'。"""
        if not original_name:
            return "upload"
        try:
            validate_filename(original_name)
            # 只取最后一段（去除可能的路径前缀，虽然 validate 已拒绝）
            return original_name.replace(" ", "_")
        except ValueError:
            return "upload"


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def resolve_file_path(
    ref: FileRef,
    data_root: Path,
    profile_id: str,
) -> Optional[Path]:
    """根据 FileRef 解析实际磁盘路径。

    路径布局：
    - CURRENT : {data_root}/profiles/{profile_id}/configs/{name}
    - ARCHIVE : {data_root}/profiles/{profile_id}/archive/{name}
    - RECORD  : {data_root}/profiles/{profile_id}/records/{name}
    - TEMP    : {data_root}/temporary/{name}

    若解析出的路径不在预期目录下（路径穿越），返回 None。
    """
    data_root = Path(data_root)

    if ref.kind == FileKind.TEMP:
        base = data_root / TEMP_DIR_NAME
        target = base / ref.name
    else:
        profile_dir = data_root / "profiles" / profile_id
        if ref.kind == FileKind.CURRENT:
            base = profile_dir / "configs"
        elif ref.kind == FileKind.ARCHIVE:
            base = profile_dir / "archive"
        elif ref.kind == FileKind.RECORD:
            base = profile_dir / "records"
        else:
            return None
        target = base / ref.name

    # 路径穿越防护：确保 target 在 base 内
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        logger.warning("Path traversal blocked: ref=%s, resolved=%s", ref, target)
        return None

    # 文件名安全校验
    try:
        validate_filename(ref.name)
    except ValueError:
        logger.warning("Invalid filename in FileRef: %s", ref.name)
        return None

    return target if target.is_file() else None


# ---------------------------------------------------------------------------
# FileRef 工厂
# ---------------------------------------------------------------------------


def create_file_ref(
    kind: FileKind,
    name: str,
    profile_id: str,
    version: Optional[str] = None,
) -> FileRef:
    """创建 FileRef，自动校验文件名。

    Raises:
        ValueError: 文件名不合法
    """
    validate_filename(name)
    return FileRef(
        profile_id=profile_id,
        kind=kind,
        name=name,
        version_or_token=version,
    )
