"""不可变产物与下载（M6-14 / INH-1959）。

产物的三条规矩：

1. **不可变**：生成一次就落成内容寻址 blob，之后下载读的就是那串字节，
   **绝不动态重生成**——否则"上次下发的文件和这次不一样"永远说不清。
2. **同输入同版本**：`determinism_key` 相同即返回已知产物，不新建对象。
   键里必须带**基线的修订号**，否则同一个基线改了一版值还会命中旧产物。
3. **生成即自证**：`validation` 随产物一起存，包含检查项与告警（"不宣称逐字节保真"）。

已知缺口（D6）：没有真实系统的 XML 样本，"真实系统导入并记录操作者/环境/结果"
这一步做不了，标记为待验（`delivery_status=unverified`），卡不关闭。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.config_repository import ConfigRepository, now_iso, sha256_bytes
from ..core.errors import INVALID_INPUT, NOT_FOUND, AppError
from . import config_baselines, config_export

#: 产物生成器版本：算法变了就让旧键失效（宁可重新生成，也不要拿旧算法的产物冒充新的）。
GENERATOR = "config_export/v1"

#: 真实系统导入状态。D6 未解除前恒为 `unverified`。
DELIVERY_UNVERIFIED = "unverified"


def _text(value: Any, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise AppError(INVALID_INPUT, "%s 不能为空" % field, field=field)
    return text


def _optional_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _label(actor: Any) -> str:
    if actor is None:
        return ""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("ip") or "")
    return str(getattr(actor, "name", "") or getattr(actor, "ip", "") or "")


def determinism_key(
    *,
    baseline_id: str,
    baseline_revision: str,
    variant_id: str,
    definition_set_id: str,
    template_sha256: str,
) -> str:
    """同输入 → 同键。空值也参与（`""` 与 `None` 视为同一档）。"""
    parts = [
        GENERATOR,
        _optional_text(baseline_id),
        _optional_text(baseline_revision),
        _optional_text(variant_id),
        _optional_text(definition_set_id),
        _optional_text(template_sha256),
    ]
    return sha256_bytes(("|".join(parts)).encode("utf-8"))


def get_artifact(repo: ConfigRepository, artifact_id: Any) -> Dict[str, Any]:
    object_id = _text(artifact_id, "artifact_id")
    payload = repo.get("artifact", object_id)
    if payload is None:
        raise AppError(NOT_FOUND, "产物不存在", artifact_id=object_id)
    return payload


def artifact_bytes(repo: ConfigRepository, artifact_id: Any) -> bytes:
    """取产物字节。**不重生成**：只读 blob。"""
    payload = get_artifact(repo, artifact_id)
    return repo.read_blob(str(payload.get("blob_sha256")))


def list_artifacts(
    repo: ConfigRepository,
    *,
    profile_id: Optional[str] = None,
    baseline_id: Optional[str] = None,
    include_common: bool = True,
) -> List[Dict[str, Any]]:
    pid = _optional_text(profile_id) or None
    bid = _optional_text(baseline_id) or None
    items = []
    for payload in repo.list_objects("artifact"):
        if not isinstance(payload, dict):
            continue
        owner = _optional_text(payload.get("profile_id"))
        if pid and owner != pid and not (include_common and owner == ""):
            continue
        if bid and _optional_text(payload.get("baseline_id")) != bid:
            continue
        items.append(payload)
    items.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    items.reverse()
    return items


def _find_by_key(repo: ConfigRepository, key: str) -> Optional[Dict[str, Any]]:
    for payload in repo.list_objects("artifact"):
        if isinstance(payload, dict) and payload.get("determinism_key") == key:
            return payload
    return None


def generate_artifact(
    repo: ConfigRepository,
    actor: Any,
    *,
    profile_id: Any,
    baseline_id: Any,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """生成（或复用）一份产物。返回 `{"item", "reused"}`。"""
    bid = _text(baseline_id, "baseline_id")
    baseline = config_baselines.get_baseline(repo, bid)
    if baseline.get("state") != config_baselines.STATE_APPROVED:
        raise AppError(
            INVALID_INPUT,
            "只有已批准的基线才能生成产物（当前 %s）" % baseline.get("state"),
            field="baseline_id",
            state=baseline.get("state"),
        )
    # 机型路径只是坐标：通用基线（无 profile）从任一机型都能生成，
    # 机型基线只在自己的机型下生成。
    owner = _optional_text(baseline.get("profile_id"))
    pid = _optional_text(profile_id) or None
    if owner and pid and owner != pid:
        raise AppError(NOT_FOUND, "基线不属于该机型", baseline_id=bid)

    produced = config_export.generate(repo, baseline_id=bid)
    template = produced["template"]
    key = determinism_key(
        baseline_id=bid,
        baseline_revision=_optional_text(baseline.get("revision")),
        variant_id=_optional_text(template.get("variant_id")),
        definition_set_id=_optional_text(template.get("definition_set_id")),
        template_sha256=_optional_text(template.get("sha256")),
    )
    known = _find_by_key(repo, key)
    if known is not None:
        return {"item": known, "reused": True}

    content: bytes = produced["content"]
    blob_sha = sha256_bytes(content)
    stamp = now_iso()
    payload = {
        "profile_id": owner or None,
        "baseline_id": bid,
        "baseline_revision": _optional_text(baseline.get("revision")),
        "variant_id": template.get("variant_id"),
        "definition_set_id": template.get("definition_set_id"),
        "template_snapshot_id": template.get("snapshot_id"),
        "template_sha256": template.get("sha256"),
        "format": produced["format"],
        "blob_sha256": blob_sha,
        "filename": produced["filename"],
        "byte_size": len(content),
        "generator": GENERATOR,
        "determinism_key": key,
        "validation": produced["validation"],
        "edits": [
            {
                "parameter_id": item["parameter_id"],
                "loc": item["loc"],
                "from": item["from"],
                "to": item["to"],
            }
            for item in produced["edits"]
        ],
        "same_count": len(produced["same"]),
        "skipped": produced["skipped"],
        "delivery_status": DELIVERY_UNVERIFIED,
        "created_at": stamp,
        "created_by": _label(actor),
    }
    # blob 先落盘再建对象：内容寻址、重复写入幂等，孤儿 blob 无害且可复用；
    # 顺序反过来会出现"对象指向一个还不存在的 blob"。
    repo.put_blob(content)
    tx = repo.transaction(
        actor,
        "生成配置文件 %s" % produced["filename"],
        action="artifact.generate",
        idempotency_key=idempotency_key,
    )
    ref = tx.put("artifact", payload)
    tx.commit()
    return {"item": get_artifact(repo, str(ref.id)), "reused": False}


def detail(repo: ConfigRepository, artifact_id: Any) -> Dict[str, Any]:
    """产物详情：附带基线与模板的形状，前端不用二次拉取。"""
    item = get_artifact(repo, artifact_id)
    return {
        "item": item,
        "edits": item.get("edits") or [],
        "validation": item.get("validation") or {},
        "delivery_status": item.get("delivery_status") or DELIVERY_UNVERIFIED,
        "revisions": repo.history("artifact", str(item["id"])),
    }
