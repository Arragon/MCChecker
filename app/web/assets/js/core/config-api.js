/*
 * 配置管理 API 客户端（M5-11）：统一请求、错误归类、草稿与冲突保护。
 *
 * 设计约束（见 docs/MCChecker_CONFIG_DOMAIN_SPEC.md §6）：
 * - 只在 `MC.api` 之上做封装，不自己发 fetch，CSRF 头仍由 api.js 统一加；
 * - 失败一律转成 `{kind, message, field, status, code}`，UI 只按 kind 分支，
 *   不再散落 `err.code === "..."` 判断；
 * - 草稿存在 `MC.configApi.drafts` 里，不落 localStorage：权威数据在服务端，
 *   本地只保存"用户还没提交的输入"，关标签前由视图的 isDirty() 触发提示。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var api = MC.api;

  var KIND_BY_CODE = {
    CONFIG_DISABLED: "disabled",
    FORBIDDEN: "forbidden",
    NOT_FOUND: "notfound",
    REVISION_CONFLICT: "conflict",
    PLAN_STALE: "conflict",
    IDEMPOTENCY_MISMATCH: "conflict",
    POLICY_BLOCKED: "forbidden",
    INVALID_INPUT: "invalid",
    MAPPING_AMBIGUOUS: "invalid",
    VALUE_OUT_OF_RANGE: "invalid",
    VALUE_LOSSY_CONVERSION: "invalid",
    DEFINITION_CONFLICT: "conflict",
    ARTIFACT_TEMPLATE_INCOMPATIBLE: "invalid",
    OBSERVATION_UNKNOWN_RELEASE: "invalid",
    CORRUPT_DATA: "corrupt",
    STORAGE_FAILURE: "server",
    MAINTENANCE: "server",
  };

  function failure(err) {
    if (!err) return { kind: "server", message: "未知错误", status: 0, code: "" };
    var code = err.code || "";
    var kind = KIND_BY_CODE[code] || (err.status === 0 ? "network" : "server");
    var field = err.detail && err.detail.field ? err.detail.field : null;
    return {
      kind: kind,
      code: code,
      status: err.status || 0,
      message: err.message || "请求失败",
      field: field,
      detail: err.detail || null,
    };
  }

  function unwrap(promise) {
    return promise.then(
      function (body) { return body; },
      function (err) { throw failure(err); }
    );
  }

  var drafts = {};
  function draftKey(pid, objectId) { return (pid || "-") + "::" + (objectId || "new"); }

  /* 动作幂等键：每次**用户动作**一个新键。
   *
   * 不用"动作+对象"做固定键：同一对象的 approve 会因为备注不同而算出不同请求体，
   * 固定键会把"改了理由再批一次"误判成 `IDEMPOTENCY_MISMATCH`。真正要防的重复
   * 发布已经由状态机挡住（第二次 approve 拿到 `CHANGE_STATE_INVALID`），幂等键
   * 在这里只负责"同一次点击的重试不要变成两次动作"。 */
  function actionKey(action, cid) {
    return "cfg-" + action + "-" + String(cid || "new") + "-" + MC.util.uid("k").slice(-8);
  }

  var configApi = {
    /* ---- 只读 ---- */
    status: function () { return unwrap(api.get("/config/status")); },
    revision: function () { return unwrap(api.get("/config/revision")); },
    capabilities: function () { return unwrap(api.get("/config/capabilities")); },
    profileStatus: function (pid) { return unwrap(api.get(api.p(pid, "/config/status"))); },
    parameters: function (query) { return unwrap(api.get("/config/parameters", query)); },
    parameter: function (parameterId) {
      return unwrap(api.get("/config/parameters/" + api.seg(parameterId)));
    },
    definitionSets: function (query) { return unwrap(api.get("/config/definition-sets", query)); },
    releases: function () { return unwrap(api.get("/config/releases")); },
    families: function () { return unwrap(api.get("/config/families")); },
    audit: function (pid, query) { return unwrap(api.get(api.p(pid, "/config/audit"), query)); },
    // 文件内现场对账（M6-10）：现场原值 / 批准基线 / 拟值三栏由服务端算好。
    reconcile: function (pid, query) {
      return unwrap(api.get(api.p(pid, "/config/reconcile"), query));
    },
    observations: function (query) { return unwrap(api.get("/config/observations", query)); },
    baselines: function (query) { return unwrap(api.get("/config/baselines", query)); },
    // 变更单（M6-11）：路径以机型为坐标，通用层变更在任一机型下可见
    changeSets: function (pid, query) {
      return unwrap(api.get(api.p(pid, "/config/changes"), query));
    },
    changeSet: function (pid, cid) {
      return unwrap(api.get(api.p(pid, "/config/changes/" + api.seg(cid))));
    },
    // 交付产物（M6-14）：按机型路径列出；`baseline_id` 可选过滤
    artifacts: function (pid, query) {
      return unwrap(api.get(api.p(pid, "/config/artifacts"), query));
    },
    artifact: function (pid, aid) {
      return unwrap(api.get(api.p(pid, "/config/artifacts/" + api.seg(aid))));
    },
    /* 跨版本升级（M7-04）：计划是读、落地是写；候选值全部来自服务端 MergePlan，
     * 前端不自己算合并。 */
    upgradeCommonPlan: function (query) {
      return unwrap(api.get("/config/upgrade/common", query));
    },
    upgradeModelPlan: function (pid, query) {
      return unwrap(api.get(api.p(pid, "/config/upgrade/model"), query));
    },
    upgradeRebasePlan: function (pid) {
      return unwrap(api.get(api.p(pid, "/config/upgrade/rebase")));
    },
    /* 经验上提（M7-05）：计划只读；设备层入口会返回 `DEVICE_LAYER_DISABLED`，
     * 由 UI 如实展示，不在这里吞掉。 */
    promoteCommonPlan: function (pid, query) {
      return unwrap(api.get(api.p(pid, "/config/promote/common"), query));
    },
    promoteModelPlan: function (pid, query) {
      return unwrap(api.get(api.p(pid, "/config/promote/model"), query));
    },
    /* 历史回滚 / 跨变体迁移 / 出口补发（M7-06）：都挂在基线下面。 */
    restorePlan: function (bid, query) {
      return unwrap(api.get("/config/baselines/" + api.seg(bid) + "/restore", query));
    },
    migrationPlan: function (bid, query) {
      return unwrap(api.get("/config/baselines/" + api.seg(bid) + "/migrate", query));
    },
    reissuePlan: function (bid) {
      return unwrap(api.get("/config/baselines/" + api.seg(bid) + "/reissue"));
    },
    baselineDetail: function (bid) {
      return unwrap(api.get("/config/baselines/" + api.seg(bid)));
    },
    variants: function (query) { return unwrap(api.get("/config/variants", query)); },

    /* ---- 写（M6-04 起真实接入）---- */
    createParameter: function (payload, headers) {
      return unwrap(api.post("/config/parameters", payload, null, headers));
    },
    patchParameter: function (parameterId, payload, headers) {
      return unwrap(api.patch("/config/parameters/" + api.seg(parameterId), payload, headers));
    },
    addDefinition: function (parameterId, payload, headers) {
      return unwrap(
        api.post("/config/parameters/" + api.seg(parameterId) + "/definitions", payload, null, headers)
      );
    },
    retireDefinition: function (definitionId, payload, headers) {
      return unwrap(
        api.post("/config/definitions/" + api.seg(definitionId) + "/retire", payload, null, headers)
      );
    },
    createDefinitionSet: function (payload, headers) {
      return unwrap(api.post("/config/definition-sets", payload, null, headers));
    },
    createChangeSet: function (pid, payload, headers) {
      return unwrap(api.post(api.p(pid, "/config/changes"), payload, null, headers));
    },
    updateChangeSet: function (pid, cid, payload, headers) {
      return unwrap(api.post(api.p(pid, "/config/changes/" + api.seg(cid)), payload, null, headers));
    },
    /* `POST /submit` 等动作**必须**带幂等键：双击/重试不能变成"提交两次"。
     * 调用方不传时这里补一个，同一次点击内复用同一个键。 */
    submitChangeSet: function (pid, cid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/changes/" + api.seg(cid) + "/submit"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("submit", cid) }, headers || {}))
      );
    },
    withdrawChangeSet: function (pid, cid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/changes/" + api.seg(cid) + "/withdraw"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("withdraw", cid) }, headers || {}))
      );
    },
    reopenChangeSet: function (pid, cid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/changes/" + api.seg(cid) + "/reopen"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("reopen", cid) }, headers || {}))
      );
    },
    approveChangeSet: function (pid, cid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/changes/" + api.seg(cid) + "/approve"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("approve", cid) }, headers || {}))
      );
    },
    rejectChangeSet: function (pid, cid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/changes/" + api.seg(cid) + "/reject"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("reject", cid) }, headers || {}))
      );
    },
    /* 生成产物：同基线修订 + 同模板 → 服务端返回既有产物（200 + reused）。
     * 幂等键仍带着：同一次点击的重试不该变成第二次写动作。 */
    generateArtifact: function (pid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/artifacts"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("artifact", payload && payload.baseline_id) }, headers || {}))
      );
    },
    createCommonUpgrade: function (payload, headers) {
      return unwrap(
        api.post("/config/upgrade/common", payload, null,
          Object.assign({ "Idempotency-Key": actionKey("up-common", payload && payload.release_id) }, headers || {}))
      );
    },
    createModelUpgrade: function (pid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/upgrade/model"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("up-model", payload && payload.release_id) }, headers || {}))
      );
    },
    createRebase: function (pid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/upgrade/rebase"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("up-rebase", pid) }, headers || {}))
      );
    },
    /* 上提：产出的是**变更单**，不是直接发基线。 */
    createPromoteCommon: function (pid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/promote/common"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("promote", pid) }, headers || {}))
      );
    },
    createPromoteModel: function (pid, payload, headers) {
      return unwrap(
        api.post(api.p(pid, "/config/promote/model"), payload, null,
          Object.assign({ "Idempotency-Key": actionKey("promote-model", pid) }, headers || {}))
      );
    },
    restoreBaseline: function (bid, payload, headers) {
      return unwrap(
        api.post("/config/baselines/" + api.seg(bid) + "/restore", payload, null,
          Object.assign({ "Idempotency-Key": actionKey("restore", bid) }, headers || {}))
      );
    },
    createMigration: function (bid, payload, headers) {
      return unwrap(
        api.post("/config/baselines/" + api.seg(bid) + "/migrate", payload, null,
          Object.assign({ "Idempotency-Key": actionKey("migrate", bid) }, headers || {}))
      );
    },
    createReissue: function (bid, payload, headers) {
      return unwrap(
        api.post("/config/baselines/" + api.seg(bid) + "/reissue", payload, null,
          Object.assign({ "Idempotency-Key": actionKey("reissue", bid) }, headers || {}))
      );
    },

    /* ---- 草稿 ---- */
    setDraft: function (pid, objectId, value) { drafts[draftKey(pid, objectId)] = value; },
    getDraft: function (pid, objectId) { return drafts[draftKey(pid, objectId)]; },
    clearDraft: function (pid, objectId) { delete drafts[draftKey(pid, objectId)]; },
    hasDraft: function (pid) {
      var prefix = (pid || "-") + "::";
      return Object.keys(drafts).some(function (key) { return key.indexOf(prefix) === 0; });
    },
    draftCount: function () { return Object.keys(drafts).length; },

    /* ---- 供 UI 直接用的纯函数 ---- */
    failure: failure,
    isRetryable: function (err) {
      var info = err && err.kind ? err : failure(err);
      return info.kind === "network" || info.kind === "server";
    },
    conflictMessage: function (info) {
      if (!info || info.kind !== "conflict") return null;
      if (info.code === "IDEMPOTENCY_MISMATCH") return "同一操作键被用于不同请求，请刷新后重试";
      if (info.code === "PLAN_STALE") return "目标对象已变化，当前草稿已保留，请刷新后重新决议";
      return "内容已被他人修改，当前输入已保留，请刷新后对比再提交";
    },
  };

  MC.configApi = configApi;
})(window);
