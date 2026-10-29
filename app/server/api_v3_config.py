"""V3 固定配置 / 软件版本 / 版本图 API（M12-01 / INH-2136）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.errors import NOT_FOUND, AppError
from ..services import (
    authz,
    config_v3_baselines,
    config_v3_catalog,
    config_v3_diff,
    config_v3_drafts,
    config_v3_extract,
    config_v3_machines,
    config_v3_projection,
    workspace_catalog,
)
from ..services.context import RequestContext
from .router import Response, Router, json_body, query_param


def register(router: Router) -> None:
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/configs", list_configs)
    router.add("POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs", create_config)
    router.add("GET", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}", get_config)
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases",
        list_releases,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases",
        create_release,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/graph",
        graph,
    )
    router.add(
        "GET", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/parameters",
        list_parameters,
    )
    router.add(
        "POST", "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/parameters",
        create_parameter,
    )
    router.add(
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/parameters/{parameter_id}",
        update_parameter,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/extract-preview",
        extract_preview,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/definition-revisions",
        definition_revisions,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/definition-revisions/{revision}",
        definition_revision,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases/{rid}/bindings",
        bindings,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases/{rid}/bindings",
        create_binding,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/drafts/{did}",
        get_draft,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/drafts/{did}/values",
        get_draft,
    )
    router.add(
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/drafts/{did}/values",
        patch_values,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/drafts/{did}/freeze",
        freeze_default,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases/{rid}/baseline-drafts",
        create_baseline_draft,
    )
    router.add(
        "PATCH",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/baseline-drafts/{did}/values",
        patch_baseline_values,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/baseline-drafts/{did}/publish",
        publish_baseline,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/branches/main",
        main_branch,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/baselines/{bid}",
        baseline_detail,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machines",
        list_machines,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machine-branches",
        create_machine,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machines/{mid}",
        get_machine,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machines/{mid}/drafts",
        new_machine_draft,
    )
    router.add(
        "POST",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/machines/{mid}/publish",
        publish_machine,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/releases/{rid}/overview",
        overview,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/revisions/{rid}/diff",
        revision_diff,
    )
    router.add(
        "GET",
        "/api/v3/workspaces/{wid}/profiles/{pid}/configs/{cid}/parameters/{pmid}/history",
        parameter_history,
    )


def _scope(wid: str, pid: str) -> None:
    workspace_catalog.get_workspace(wid)
    if not any(str(p.get("id")) == pid for p in workspace_catalog.list_profiles(wid)):
        raise AppError(NOT_FOUND, "机型不存在", profile=pid)


def list_configs(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_catalog.list_configs(params["pid"])})


def create_config(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(status=201, payload={"config": config_v3_catalog.create_config(params["pid"], json_body(body))})


def get_config(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"config": config_v3_catalog.get_config(params["pid"], params["cid"])})


def list_releases(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_catalog.list_releases(params["pid"], params["cid"])})


def create_release(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={"release": config_v3_catalog.create_release(params["pid"], params["cid"], json_body(body))},
    )


def graph(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_catalog.graph(params["pid"], params["cid"]))


def list_parameters(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_catalog.list_parameters(params["pid"], params["cid"]))


def create_parameter(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={"parameter": config_v3_catalog.create_parameter(params["pid"], params["cid"], json_body(body))},
    )


def update_parameter(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={
            "parameter": config_v3_catalog.update_parameter(
                params["pid"], params["cid"], params["parameter_id"], json_body(body)
            )
        }
    )


def definition_revisions(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={"items": config_v3_catalog.definition_revisions(params["pid"], params["cid"])}
    )


def definition_revision(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={
            "revision": config_v3_catalog.definition_revision(
                params["pid"], params["cid"], int(params["revision"])
            )
        }
    )


def bindings(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={"binding_set": config_v3_catalog.bindings_for(params["pid"], params["cid"], params["rid"])}
    )


def create_binding(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={
            "binding_set": config_v3_catalog.create_binding(
                params["pid"], params["cid"], params["rid"], json_body(body)
            )
        },
    )


def get_draft(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    draft = config_v3_drafts.get_draft(params["pid"], params["cid"], params["did"])
    return Response(payload=draft, headers={"ETag": config_v3_drafts._etag(draft["revision"])})


def patch_values(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    if_match = (ctx.headers or {}).get("if-match")
    result, etag = config_v3_drafts.patch_values(
        params["pid"], params["cid"], params["did"], json_body(body), if_match
    )
    return Response(payload=result, headers={"ETag": etag})


def freeze_default(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    result = config_v3_drafts.freeze_default(
        params["pid"], params["cid"], params["did"], json_body(body)
    )
    return Response(payload=result, headers={"ETag": config_v3_drafts._etag(result["revision"])})


def create_baseline_draft(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={
            "draft": config_v3_baselines.create_baseline_draft(
                params["pid"], params["cid"], params["rid"]
            )
        },
    )


def patch_baseline_values(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    result, etag = config_v3_baselines.patch_baseline_values(
        params["pid"], params["cid"], params["did"], json_body(body),
        (ctx.headers or {}).get("if-match"),
    )
    return Response(payload=result, headers={"ETag": etag})


def publish_baseline(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    result, etag = config_v3_baselines.publish_baseline(
        params["pid"], params["cid"], params["did"], json_body(body),
        (ctx.headers or {}).get("if-match"),
    )
    return Response(payload={"baseline": result}, headers={"ETag": etag})


def main_branch(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload=config_v3_baselines.get_branch(params["pid"], params["cid"]))


def baseline_detail(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload={"baseline": config_v3_baselines.get_baseline(params["pid"], params["cid"], params["bid"])}
    )


def list_machines(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"items": config_v3_machines.list_machines(params["pid"], params["cid"])})


def create_machine(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={"machine": config_v3_machines.create_machine(
            params["pid"], params["cid"], json_body(body)
        )},
    )


def get_machine(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(payload={"machine": config_v3_machines.get_machine(
        params["pid"], params["cid"], params["mid"]
    )})


def new_machine_draft(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    return Response(
        status=201,
        payload={"machine": config_v3_machines.new_draft(
            params["pid"], params["cid"], params["mid"], json_body(body)
        )},
    )


def publish_machine(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_registry_write")
    _scope(params["wid"], params["pid"])
    result, seq = config_v3_machines.publish_machine(
        params["pid"], params["cid"], params["mid"], json_body(body),
        (ctx.headers or {}).get("if-match"),
    )
    return Response(payload={"machine": result}, headers={"ETag": '"%d"' % seq})


def _csv(raw: str) -> List[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def extract_preview(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload=config_v3_extract.preview(
            params["pid"],
            params["cid"],
            query_param(query, "version_id"),
            _int(query_param(query, "offset"), 0),
            _int(query_param(query, "limit"), 100),
            query_param(query, "filter") or config_v3_extract.FILTER_ALL,
        )
    )


def overview(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload=config_v3_projection.overview(
            params["pid"], params["cid"], params["rid"],
            query_param(query, "compare_release_id"),
            _csv(query_param(query, "baselines")),
            _csv(query_param(query, "machines")),
        )
    )


def revision_diff(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload=config_v3_diff.diff(
            params["pid"], params["cid"], params["rid"], query_param(query, "against")
        )
    )


def parameter_history(
    ctx: RequestContext, params: Dict[str, str], query: Dict[str, List[str]], body: Optional[bytes]
) -> Response:
    authz.authorize(ctx, "config_read")
    _scope(params["wid"], params["pid"])
    return Response(
        payload=config_v3_diff.parameter_history(
            params["pid"], params["cid"], params["pmid"]
        )
    )
