/*
 * V3.1 更新管理工作区级入口（M16-31 / M16-32 / M16-33 / INH-2196、INH-2197、INH-2198）。
 *
 * 壳：来源/计划/连接/运行记录/候选五个 tab 与旧设置深链收口。
 * M16-32：来源真实列表、筛选、分页、排序、编辑抽屉与单项更新；不加载 mock。
 * M16-33：更新计划（全局/机型覆盖 CAS 保存）、工作区全量预览、失败筛选只做
 * 客户端显示过滤（请求始终 scope=workspace_all）、暂停自动后仍可手动全量更新。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var TABS = [
    { id: "sources", label: "更新源", desc: "工作区级来源、筛选、单项与批量更新。" },
    { id: "plan", label: "更新计划", desc: "默认频率、来源覆盖、全量预览与手动任务。" },
    { id: "connections", label: "连接设置", desc: "SDA 连接、保活、自动重登与凭据状态。" },
    { id: "runs", label: "运行记录", desc: "持久运行、阶段日志、失败重试与脱敏诊断。" },
    { id: "candidates", label: "更新候选", desc: "候选差异、冲突决议与写入 V3 草稿。" },
  ];

  //: 手动/批量任务记录跨视图保留：切换 tab 或工作区后仍能看见 run 最初归属的 W。
  var RUN_LOG = [];
  var RUN_LOG_MAX = 20;
  var FAILED_ITEM_STATUSES = ["failed", "blocked", "interrupted", "canceled"];

  var KIND_LABEL = { http_file: "HTTP 文件", http_record: "HTTP 修改记录", sda_catalog: "SDA 目录", sda_table: "SDA 表", sda_file: "SDA 文件" };
  var SOURCE_STATUS = [
    { id: "", label: "全部状态" },
    { id: "enabled", label: "已启用" },
    { id: "disabled", label: "已停用" },
  ];
  var SORTS = [
    { id: "name", label: "按名称" },
    { id: "updated", label: "按更新时间" },
  ];

  function tabById(id) {
    return TABS.filter(function (tab) { return tab.id === id; })[0] || TABS[0];
  }

  function workspaceId() {
    try {
      if (MC.workspaces && MC.workspaces.scopeId) return MC.workspaces.scopeId();
    } catch (e) { /* 测试页/旧壳 */ }
    return "default";
  }

  function sourcesPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/sources";
  }

  function policyPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/policy";
  }

  function previewsPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/previews";
  }

  function runsPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/runs";
  }

  function workspaceProfiles() {
    try {
      var current = MC.workspaces && MC.workspaces.current ? MC.workspaces.current() : null;
      var profiles = current && current.profiles;
      if (Array.isArray(profiles) && profiles.length) {
        return profiles.map(function (profile) {
          return { id: String(profile.id), name: profile.name || String(profile.id) };
        });
      }
    } catch (e) { /* 测试页/旧壳 */ }
    return [{ id: "default", name: "默认机型" }];
  }

  function draftFromPolicy(policy) {
    var overrides = {};
    var raw = (policy && policy.profile_overrides) || {};
    Object.keys(raw).forEach(function (pid) {
      var item = raw[pid] || {};
      overrides[pid] = {
        enabled: item.enabled === true ? true : (item.enabled === false ? false : undefined),
        interval_hours: item.interval_hours == null ? undefined : item.interval_hours,
      };
    });
    return {
      enabled: !!(policy && policy.enabled),
      default_interval_hours:
        policy && policy.default_interval_hours != null ? policy.default_interval_hours : 6,
      overrides: overrides,
    };
  }

  function planErrorText(err) {
    if (err && err.code === "REVISION_CONFLICT") {
      return "计划或预览已在其他客户端更新；当前草稿已保留，请刷新后重试";
    }
    if (err && err.code === "PLAN_STALE") return "预览已过期，请重新预览";
    return errorText(err);
  }

  function scheduleText(source) {
    if (source.schedule_mode === "manual") return "仅手动";
    if (source.schedule_mode === "custom") return String(source.interval_hours || "?") + " 小时";
    return "继承计划";
  }

  function sourcePath(source) {
    return sourcesPath() + "/" + encodeURIComponent(source.source_id);
  }

  function errorText(err) {
    if (!err) return "操作失败";
    if (err.code === "REVISION_CONFLICT") return "来源已在其他客户端更新；当前草稿已保留，请刷新后重试";
    return err.message || "操作失败";
  }

  MC.views.updates = function (app, route) {
    var state = {
      tab: tabById(route && route.arg).id,
      sources: {
        items: [],
        total: 0,
        next_cursor: null,
        query: "",
        status: "",
        sort: "name",
        loading: false,
        error: "",
        expanded: false,
      },
      runs: {
        items: [],
        total: 0,
        next_cursor: null,
        state: "",
        loading: false,
        error: "",
        expanded: false,
      },
      candidates: {
        items: [],
        total: 0,
        next_cursor: null,
        state: "",
        loading: false,
        error: "",
        loadedFor: "",
      },
      plan: {
        loadedFor: "",
        loading: false,
        error: "",
        policy: null,
        draft: null,
        sources: [],
        preview: null,
        previewWorkspaceId: "",
        previewing: false,
        validating: false,
        saving: false,
        running: false,
        failuresOnly: false,
        failedSourceIds: {},
      },
      connections: {
        loadedFor: "",
        loading: false,
        error: "",
        items: [],
        heartbeats: {},
        heartbeatErrors: {},
        operations: {},
        pollAttempts: {},
        pollTimers: [],
        loadSeq: 0,
      },
    };
    var el = h("div");
    var tabbar = h("div.row", { role: "tablist", "aria-label": "更新管理分类", style: "gap:6px;flex-wrap:wrap;margin:10px 0 14px" });
    var body = h("div");

    function renderTabs() {
      util.clear(tabbar);
      TABS.forEach(function (tab) {
        var active = state.tab === tab.id;
        var button = h(
          "button.btn" + (active ? ".btn-primary" : ""),
          {
            type: "button",
            role: "tab",
            "aria-selected": active ? "true" : "false",
            "data-sync-tab": tab.id,
          },
          tab.label
        );
        button.addEventListener("click", function () {
          state.tab = tab.id;
          renderTabs();
          renderBody();
          if (app && app.go) app.go({ kind: "updates", arg: tab.id });
        });
        tabbar.appendChild(button);
      });
    }

    function canWrite() {
      return !!(app && app.can && app.can("settings_write"));
    }

    function canRun() {
      return !!(app && app.can && app.can("tasks_run"));
    }

    function canSavePlan() {
      return !!(app && app.can && app.can("settings_write"));
    }

    function canRunPlan() {
      return !!(app && app.can && app.can("tasks_run"));
    }

    function loadSources(reset) {
      var S = state.sources;
      if (S.loading) return Promise.resolve();
      S.loading = true;
      S.error = "";
      if (reset) {
        S.items = [];
        S.next_cursor = null;
        S.expanded = false;
      }
      renderBody();
      var query = {
        q: S.query,
        status: S.status || undefined,
        limit: 50,
        cursor: S.expanded && S.next_cursor ? S.next_cursor : "0",
      };
      return MC.api.requestV3("GET", sourcesPath(), { query: query }).then(function (data) {
        var incoming = (data && data.items) || [];
        S.items = S.expanded ? S.items.concat(incoming) : incoming;
        S.total = data && typeof data.total === "number" ? data.total : S.items.length;
        S.next_cursor = data ? data.next_cursor : null;
        S.loading = false;
        S.error = "";
        renderBody();
      }, function (err) {
        S.loading = false;
        S.error = errorText(err);
        renderBody();
      });
    }

    function sortedItems() {
      var rows = state.sources.items.slice();
      if (state.sources.sort === "updated") {
        rows.sort(function (a, b) { return String(b.updated_at || "").localeCompare(String(a.updated_at || "")); });
      } else {
        rows.sort(function (a, b) { return String(a.name || "").localeCompare(String(b.name || "")); });
      }
      return rows;
    }

    function field(label, control) {
      return h("div.col", { style: "gap:4px" }, h("label.small.muted", label), control);
    }

    function openEditor(source) {
      var draft = {
        name: source.name || "",
        resource_ref: source.resource_ref || "",
        record_source: source.record_source || "",
        keyword: source.keyword || "",
        display_path: source.display_path || "",
        enabled: source.enabled !== false,
        schedule_mode: source.schedule_mode || "inherit",
        interval_hours: source.interval_hours == null ? "" : String(source.interval_hours),
      };
      var error = h("div.form-error", { style: "display:none" });
      var nameInput = h("input.input", { value: draft.name, autofocus: true });
      var refInput = h("input.input", { value: draft.resource_ref, placeholder: "下载 URL / SDA 资源引用" });
      var displayInput = h("input.input", { value: draft.display_path, placeholder: "业务路径，例如 /synthetic/A.xml" });
      var recordInput = h("input.input", { value: draft.record_source, placeholder: "修改记录链接（可选）" });
      var keywordInput = h("input.input", { value: draft.keyword, placeholder: "压缩包内文件名/正则（可选）" });
      var catalogItems = [];
      var catalogPicker = null;
      var wantedCatalogKind = source.kind === "sda_table" ? "table" : "file";
      if ((source.kind === "sda_table" || source.kind === "sda_file") && source.connection_id) {
        var catalogMsg = h("span.xsmall.muted", { "data-sync-catalog-msg": "1" }, "ID 与展示路径必须来自当前连接目录。");
        var catalogSelect = h("select.input", { style: "display:none", "data-sync-catalog-select": "1" },
          h("option", { value: "" }, "选择目录条目…")
        );
        catalogSelect.addEventListener("change", function () {
          var picked = catalogItems[Number(catalogSelect.value)];
          if (!picked) return;
          refInput.value = picked.resource_ref;
          displayInput.value = picked.display_path;
          catalogMsg.textContent = "已填入 " + picked.name + "（保存时服务端会再次校验）";
        });
        var catalogButton = h("button.btn.btn-sm", {
          type: "button",
          "data-sync-catalog": "1",
          onclick: function () {
            catalogButton.disabled = true;
            catalogMsg.textContent = "正在读取 SDA 目录…";
            var path = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/connections/" + encodeURIComponent(source.connection_id) + "/catalog";
            MC.api.requestV3("GET", path).then(function (data) {
              catalogItems = ((data && data.items) || []).filter(function (item) {
                return item.kind === wantedCatalogKind;
              });
              util.clear(catalogSelect);
              catalogSelect.appendChild(h("option", { value: "" }, "选择目录条目…"));
              catalogItems.forEach(function (item, index) {
                catalogSelect.appendChild(h("option", { value: String(index) },
                  (item.kind === "table" ? "表" : "文件") + " · " + item.name + " · " + item.display_path));
              });
              catalogSelect.style.display = "";
              catalogButton.disabled = false;
              catalogMsg.textContent = catalogItems.length
                ? "共 " + catalogItems.length + " 项；选中后自动填入 ID/路径。"
                : "当前目录没有可选项。";
            }, function (err) {
              catalogButton.disabled = false;
              catalogMsg.textContent = (err && (err.message || err.code)) || "目录读取失败";
            });
          },
        }, "从 SDA 目录选择");
        catalogPicker = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" },
          catalogButton, catalogSelect, catalogMsg);
      }
      var enabledInput = h("input", { type: "checkbox", checked: draft.enabled });
      var modeSelect = h("select.input", {},
        h("option", { value: "inherit" }, "继承工作区计划"),
        h("option", { value: "custom" }, "自定义间隔"),
        h("option", { value: "manual" }, "仅手动")
      );
      var intervalInput = h("input.input", { type: "number", min: "1", max: "2160", value: draft.interval_hours, placeholder: "小时" });
      modeSelect.value = draft.schedule_mode;
      var intervalWrap = h("div.col", { style: "gap:4px" }, h("label.small.muted", "间隔（小时）"), intervalInput);
      intervalWrap.style.display = draft.schedule_mode === "custom" ? "" : "none";
      modeSelect.addEventListener("change", function () {
        intervalWrap.style.display = modeSelect.value === "custom" ? "" : "none";
      });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "编辑更新源",
        subtitle: (KIND_LABEL[source.kind] || source.kind) + " · " + source.source_id,
        size: "wide",
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("资源引用", refInput),
          field("业务路径", displayInput),
          catalogPicker,
          field("修改记录链接", recordInput),
          field("关键词", keywordInput),
          h("div.row", { style: "gap:18px;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用")),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "计划"), modeSelect)
          ),
          intervalWrap,
          h("p.xsmall.muted", "修改会以来源 revision " + source.revision + " 做 CAS；409 时保留当前草稿。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-sync-save": "1",
              onclick: function () {
                var payload = {
                  name: nameInput.value.trim(),
                  resource_ref: refInput.value.trim() || null,
                  record_source: recordInput.value.trim() || null,
                  keyword: keywordInput.value.trim(),
                  display_path: displayInput.value.trim() || null,
                  enabled: !!enabledInput.checked,
                  schedule_mode: modeSelect.value,
                  interval_hours: modeSelect.value === "custom" ? Number(intervalInput.value) : null,
                };
                if (!payload.name) { showError("名称不能为空"); return; }
                if (payload.display_path && payload.display_path.charAt(0) !== "/") {
                  showError("业务路径必须以 / 开始（目录 ID 与路径不一致会被拒绝）");
                  return;
                }
                if (payload.schedule_mode === "custom" && (!payload.interval_hours || payload.interval_hours < 1 || payload.interval_hours > 2160)) {
                  showError("自定义间隔必须在 1–2160 小时");
                  return;
                }
                showError("");
                MC.api.requestV3("PATCH", sourcePath(source), {
                  body: payload,
                  headers: { "If-Match": String(source.revision) },
                }).then(function (data) {
                  close();
                  MC.toast.success("更新源已保存");
                  // 局部刷新：保留当前搜索/分页/排序，只替换这一行。
                  if (data && data.source_id) {
                    var items = state.sources.items;
                    var replaced = false;
                    for (var i = 0; i < items.length; i += 1) {
                      if (items[i].source_id === data.source_id) {
                        items[i] = data;
                        replaced = true;
                        break;
                      }
                    }
                    if (replaced) {
                      if (state.tab === "sources") renderBody();
                      return;
                    }
                  }
                  loadSources(true);
                }, function (err) {
                  showError(errorText(err));
                });
              },
            }, "保存"),
          ];
        },
      });
      return modal;
    }

    function runSource(source, button) {
      if (button) button.disabled = true;
      MC.api.requestV3("POST", sourcePath(source) + "/run", { body: {} }).then(function (data) {
        MC.toast.success("已提交单项更新");
        if (data && data.run && data.run.run_id) {
          MC.bus.emit("sync-run-started", data.run);
        }
        if (button) button.disabled = false;
      }, function (err) {
        MC.toast.error("单项更新提交失败", errorText(err));
        if (button) button.disabled = false;
      });
    }

    function rerenderPlan() {
      if (state.tab === "plan") renderPlan();
    }

    function loadPlan() {
      var P = state.plan;
      if (P.loading) return Promise.resolve();
      var wid = workspaceId();
      P.loading = true;
      P.loadedFor = wid;
      P.error = "";
      rerenderPlan();
      return Promise.all([
        MC.api.requestV3("GET", policyPath()),
        MC.api.requestV3("GET", sourcesPath(), { query: { status: "all", limit: 200 } }),
      ]).then(function (result) {
        if (P.loadedFor !== workspaceId()) return;
        P.policy = result[0] || {};
        P.draft = draftFromPolicy(P.policy);
        P.sources = (result[1] && result[1].items) || [];
        P.loading = false;
        P.error = "";
        rerenderPlan();
      }, function (err) {
        if (P.loadedFor !== workspaceId()) return;
        P.loading = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function setOverride(pid, key, value) {
      var P = state.plan;
      if (!P.draft) return;
      var item = P.draft.overrides[pid] || {};
      if (value === null || value === undefined || value === "") delete item[key];
      else item[key] = value;
      if (item.enabled === undefined && item.interval_hours === undefined) {
        delete P.draft.overrides[pid];
      } else {
        P.draft.overrides[pid] = item;
      }
    }

    function buildPolicyPayload() {
      var draft = state.plan.draft;
      var hours = Number(draft.default_interval_hours);
      if (!isFinite(hours) || hours < 1 || hours > 2160) {
        return { error: "默认间隔必须在 1–2160 小时" };
      }
      var overrides = {};
      var bad = "";
      Object.keys(draft.overrides).forEach(function (pid) {
        var item = draft.overrides[pid] || {};
        var clean = {};
        if (item.enabled === true || item.enabled === false) clean.enabled = item.enabled;
        if (item.interval_hours !== undefined && item.interval_hours !== null && String(item.interval_hours).trim() !== "") {
          var value = Number(item.interval_hours);
          if (!isFinite(value) || value < 1 || value > 2160) {
            bad = "机型覆盖间隔必须在 1–2160 小时";
          } else {
            clean.interval_hours = value;
          }
        }
        if (Object.keys(clean).length) overrides[pid] = clean;
      });
      if (bad) return { error: bad };
      return {
        body: {
          enabled: !!draft.enabled,
          default_interval_hours: hours,
          profile_overrides: overrides,
        },
      };
    }

    function savePlan() {
      var P = state.plan;
      if (!canSavePlan() || P.saving || !P.draft) return;
      var built = buildPolicyPayload();
      if (built.error) {
        P.error = built.error;
        rerenderPlan();
        return;
      }
      var revision = P.policy && P.policy.revision != null ? P.policy.revision : 0;
      P.saving = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("PUT", policyPath(), {
        body: built.body,
        headers: { "If-Match": String(revision) },
      }).then(function (policy) {
        P.policy = policy || P.policy;
        P.draft = draftFromPolicy(P.policy);
        P.saving = false;
        P.error = "";
        MC.toast.success("更新计划已保存");
        rerenderPlan();
      }, function (err) {
        P.saving = false;
        // 409：不覆盖 P.draft，输入框仍显示用户当前草稿。
        P.error = err && err.code === "REVISION_CONFLICT"
          ? "计划已在其他客户端更新；当前草稿已保留，请刷新后重试"
          : planErrorText(err);
        rerenderPlan();
      });
    }

    function requestPlanPreview() {
      var P = state.plan;
      if (!canSavePlan() || P.previewing) return;
      // 失败筛选只影响下方列表显示；预览请求始终冻结 workspace_all 全量 read_set。
      var wid = workspaceId();
      P.previewing = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", previewsPath(), { body: { scope: "workspace_all" } }).then(function (preview) {
        P.previewing = false;
        if (!preview || P.loadedFor !== workspaceId()) return;
        P.preview = preview;
        P.previewWorkspaceId = wid;
        P.error = "";
        rerenderPlan();
      }, function (err) {
        P.previewing = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function validatePlanPreview() {
      var P = state.plan;
      if (!canSavePlan() || !P.preview || P.validating) return;
      var wid = P.previewWorkspaceId || workspaceId();
      var path = "/api/v3/workspaces/" + encodeURIComponent(wid)
        + "/sync/previews/" + encodeURIComponent(P.preview.preview_id) + "/validate";
      P.validating = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", path, { body: {} }).then(function (fresh) {
        P.validating = false;
        if (fresh) {
          P.preview = fresh;
          P.previewWorkspaceId = wid;
        }
        MC.toast.success("预览仍然有效");
        rerenderPlan();
      }, function (err) {
        P.validating = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function rememberFailures(runs) {
      (runs || []).forEach(function (entry) {
        var task = entry && entry.task;
        var items = (task && task.result && task.result.items) || [];
        items.forEach(function (item) {
          if (item && item.source_id && FAILED_ITEM_STATUSES.indexOf(item.status) >= 0) {
            state.plan.failedSourceIds[item.source_id] = true;
          }
        });
      });
    }

    function runWorkspaceAll() {
      var P = state.plan;
      if (!canRunPlan() || P.running) return;
      var wid = workspaceId();
      var body = { scope: "workspace_all" };
      if (P.preview && P.preview.preview_id && P.previewWorkspaceId === wid) {
        body.preview_id = P.preview.preview_id;
      }
      P.running = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", runsPath(), { body: body }).then(function (data) {
        P.running = false;
        var runs = (data && data.runs) || [];
        runs.forEach(function (entry) {
          RUN_LOG.push({
            workspace_id: wid,
            profile_id: entry.profile_id,
            run: entry.run,
            task: entry.task,
          });
          if (MC.bus) MC.bus.emit("sync-run-started", entry.run);
        });
        while (RUN_LOG.length > RUN_LOG_MAX) RUN_LOG.shift();
        rememberFailures(runs);
        P.error = "";
        if (!runs.length) MC.toast.info("没有可更新的来源");
        else MC.toast.success("已提交全量更新", "共 " + runs.length + " 个机型任务");
        rerenderPlan();
      }, function (err) {
        P.running = false;
        P.error = err && err.code === "REVISION_CONFLICT"
          ? "预览或计划已变化，未提交任务；请重新预览后重试"
          : planErrorText(err);
        rerenderPlan();
      });
    }

    function renderPlan() {
      var P = state.plan;
      var save = canSavePlan();
      var run = canRunPlan();
      var draft = P.draft || draftFromPolicy(P.policy || {});
      var profiles = workspaceProfiles();

      if (P.error) body.appendChild(h("div.form-error", P.error));
      if (P.loading && !P.policy) {
        body.appendChild(h("p.muted", "正在读取更新计划…"));
        return;
      }
      if (!P.policy) {
        body.appendChild(h("div.card", h("div.card-b",
          h("p.muted", "暂时无法读取更新计划。"),
          h("button.btn.btn-sm", { type: "button", "data-plan-refresh": "1", onclick: loadPlan }, "刷新")
        )));
        return;
      }
      if (!save) {
        body.appendChild(h("p.small.muted", { "data-plan-readonly": "1" }, "只读身份：可查看更新计划，不能保存或启动任务。"));
      }

      var enabledInput = h("input", {
        type: "checkbox",
        checked: draft.enabled,
        disabled: !save,
        "data-plan-enabled": "1",
      });
      enabledInput.addEventListener("change", function () {
        draft.enabled = !!enabledInput.checked;
      });
      var intervalInput = h("input.input", {
        type: "number",
        min: "1",
        max: "2160",
        value: String(draft.default_interval_hours),
        disabled: !save,
        "data-plan-interval": "1",
        style: "max-width:120px",
      });
      intervalInput.addEventListener("change", function () {
        draft.default_interval_hours = intervalInput.value;
      });

      var overrideRows = profiles.map(function (profile) {
        var item = draft.overrides[profile.id] || {};
        var select = h("select.input", {
          disabled: !save,
          "data-plan-override-enabled": profile.id,
        },
          h("option", { value: "" }, "继承工作区"),
          h("option", { value: "true" }, "启用"),
          h("option", { value: "false" }, "暂停")
        );
        select.value = item.enabled === true ? "true" : (item.enabled === false ? "false" : "");
        select.addEventListener("change", function () {
          setOverride(profile.id, "enabled", select.value === "" ? null : select.value === "true");
        });
        var overrideInterval = h("input.input", {
          type: "number",
          min: "1",
          max: "2160",
          placeholder: "继承默认",
          value: item.interval_hours == null ? "" : String(item.interval_hours),
          disabled: !save,
          "data-plan-override-interval": profile.id,
          style: "max-width:120px",
        });
        overrideInterval.addEventListener("change", function () {
          setOverride(profile.id, "interval_hours", overrideInterval.value.trim());
        });
        return h("tr", { "data-plan-profile": profile.id },
          h("td", h("div", h("strong", profile.name)), h("div.xsmall.muted", profile.id)),
          h("td", select),
          h("td", overrideInterval)
        );
      });

      var planActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;margin-top:10px" });
      if (save) {
        planActions.appendChild(h("button.btn.btn-primary", {
          type: "button",
          "data-plan-save": "1",
          disabled: P.saving,
          onclick: savePlan,
        }, P.saving ? "保存中…" : "保存计划"));
      }
      planActions.appendChild(h("button.btn.btn-sm", {
        type: "button",
        "data-plan-refresh": "1",
        onclick: loadPlan,
      }, "刷新"));
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "自动更新计划"), h("span.muted.small", "工作区 " + workspaceId())),
        h("div.card-b",
          h("div.row", { style: "gap:18px;flex-wrap:wrap;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" },
              enabledInput, h("span", "启用自动更新")),
            h("div.row", { style: "gap:6px;align-items:center" },
              h("span.small.muted", "默认间隔"),
              intervalInput,
              h("span.small.muted", "小时")
            )
          ),
          h("p.xsmall.muted", "暂停自动更新只影响自动投递；手动全量更新仍可随时启动。"),
          h("table.table", { style: "margin-top:10px" },
            h("thead", h("tr", h("th", "机型"), h("th", "自动状态覆盖"), h("th", "间隔覆盖（小时）"))),
            h("tbody", overrideRows)
          ),
          planActions
        )
      ));

      var pv = P.preview;
      var previewActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      if (save) {
        previewActions.appendChild(h("button.btn.btn-primary", {
          type: "button",
          "data-plan-preview": "1",
          disabled: P.previewing,
          onclick: requestPlanPreview,
        }, P.previewing ? "预览中…" : "全量预览"));
      }
      var failures = h("input", {
        type: "checkbox",
        checked: P.failuresOnly,
        disabled: !save,
        "data-plan-failures": "1",
      });
      failures.addEventListener("change", function () {
        P.failuresOnly = !!failures.checked;
        rerenderPlan();
      });
      previewActions.appendChild(h("label.row", { style: "gap:6px;align-items:center" },
        failures, h("span.small", "仅显示失败来源")));
      if (save && pv) {
        previewActions.appendChild(h("button.btn.btn-sm", {
          type: "button",
          "data-plan-validate": "1",
          disabled: P.validating,
          onclick: validatePlanPreview,
        }, P.validating ? "校验中…" : "验证预览"));
      }

      var previewBody = h("div.card-b", previewActions);
      if (pv) {
        var fingerprint = pv.read_set_sha256 ? String(pv.read_set_sha256).slice(0, 16) : "—";
        previewBody.appendChild(h("p.small", { "data-plan-read-set": pv.read_set_sha256 || "" },
          "冻结来源 " + (pv.total || 0) + " 个 · 排除 " + ((pv.excluded || []).length)
          + " 个 · read_set " + fingerprint
        ));
        previewBody.appendChild(h("p.xsmall.muted",
          "失败筛选只影响本页显示；冻结 read_set 始终按 workspace_all 请求，不会缩成失败子集。"
        ));
        var rows = (pv.items || []).slice();
        if (P.failuresOnly) {
          rows = rows.filter(function (item) { return !!P.failedSourceIds[item.source_id]; });
        }
        if (!rows.length) {
          previewBody.appendChild(h("p.muted.small", P.failuresOnly
            ? "当前没有记录到失败来源；全量 read_set 仍包含上面的 " + (pv.total || 0) + " 个来源。"
            : "预览中没有可用来源。"));
        } else {
          rows.forEach(function (item) {
            previewBody.appendChild(h("div.row", {
              "data-plan-preview-source": item.source_id,
              style: "gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--border)",
            },
              h("span.small", item.name || item.source_id),
              h("span.xsmall.muted", (item.profile_id || "") + " · " + (item.schedule_mode || "inherit"))
            ));
          });
        }
      } else {
        previewBody.appendChild(h("p.muted.small", "尚未预览；全量预览会冻结所有机型下已启用来源。"));
      }
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "全量预览"), h("span.muted.small", "workspace_all")),
        previewBody
      ));

      var runRows = RUN_LOG.slice().reverse();
      var runBody = h("div.card-b");
      var runActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      if (run) {
        runActions.appendChild(h("button.btn.btn-primary", {
          type: "button",
          "data-plan-run": "1",
          disabled: P.running,
          onclick: runWorkspaceAll,
        }, P.running ? "提交中…" : "手动全量更新"));
      }
      runActions.appendChild(h("span.xsmall.muted", draft.enabled
        ? "自动计划已启用；手动任务独立提交。"
        : "自动计划已暂停；手动任务仍可启动。"));
      runBody.appendChild(runActions);
      if (runRows.length) {
        runBody.appendChild(h("p.small", { "data-plan-run-wid": runRows[0].workspace_id },
          "最近任务归属工作区：" + runRows[0].workspace_id));
        runRows.slice(0, 10).forEach(function (entry) {
          var runInfo = entry.run || {};
          var task = entry.task || {};
          runBody.appendChild(h("div.row", {
            "data-plan-run-row": entry.workspace_id,
            style: "gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--border)",
          },
            h("span.small", entry.profile_id),
            h("span.xsmall.muted", "工作区 " + entry.workspace_id),
            h("span.badge", runInfo.trigger || "manual"),
            h("span.small", runInfo.state || task.state || "queued"),
            h("span.xsmall.muted", runInfo.run_id || task.id || "")
          ));
        });
      } else {
        runBody.appendChild(h("p.muted.small", "尚未提交手动任务。"));
      }
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "手动全量更新"), h("span.muted.small", "按机型分组投递 sync_run")),
        runBody
      ));
    }

    /* ===================== M16-34 连接设置 / 保活 ===================== */

    var CREDENTIAL_LABELS = {
      unconfigured: "未配置",
      valid: "有效",
      locked: "已锁定",
      invalid: "无效",
    };
    var CREDENTIAL_ERROR_TEXT = {
      CONFIG_DISABLED: "凭据存储未配置",
      FORBIDDEN: "需要管理员能力与运维授权",
      INVALID_INPUT: "输入不符合要求",
      REVISION_CONFLICT: "状态已变化，请刷新后重试",
      NOT_FOUND: "连接或凭据候选不存在",
      STORAGE_FAILURE: "服务器写入失败，凭据未改变",
      BUSY: "该连接已有操作在进行",
    };
    var OPERATION_STATES = { queued: "排队中", running: "执行中", succeeded: "已完成", failed: "失败", blocked: "已阻止" };
    var OPERATION_REASONS = {
      disabled: "保活未启用",
      collector_offline: "collector 未连接",
      bad_credentials: "密码错误",
      locked: "凭据已锁定",
      auth_failed: "认证失败",
      network_error: "网络错误",
      revision_conflict: "revision 已变化",
      source_changed: "认证纪元已变化",
    };

    function connectionsPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/connections";
    }

    function connectionPath(cid) {
      return connectionsPath() + "/" + encodeURIComponent(cid);
    }

    function heartbeatPath(cid) {
      return connectionPath(cid) + "/heartbeat";
    }

    function canManageCredentials() {
      return !!(app && app.can && app.can("integration_credentials_manage"));
    }

    function credentialLabel(cred) {
      if (!cred || !cred.state) return "未配置";
      return CREDENTIAL_LABELS[cred.state] || String(cred.state);
    }

    function credentialBadge(cred) {
      if (cred && cred.state === "valid") return "badge badge-success";
      if (cred && cred.state === "locked") return "badge badge-danger";
      if (cred && cred.state && cred.state !== "unconfigured") return "badge badge-warning";
      return "badge badge-count";
    }

    function credentialErrorText(err) {
      var code = (err && err.code) || (err && err.status ? "HTTP_" + err.status : "UNKNOWN");
      var label = CREDENTIAL_ERROR_TEXT[code];
      return "凭据操作失败：" + (label ? label + "（" + code + "）" : code);
    }

    function heartbeatStatusText(hb) {
      if (!hb) return "未能读取保活状态";
      var parts = [hb.enabled ? "已启用" : "已停用"];
      parts.push("间隔 " + (hb.interval_minutes || 10) + " 分钟");
      if (hb.last_heartbeat_at) parts.push("上次 " + hb.last_heartbeat_at);
      if (hb.last_result) parts.push(hb.last_result === "success" ? "成功" : "失败");
      if (hb.reason_code) parts.push("原因 " + hb.reason_code);
      if (hb.next_due_at) parts.push("下次到期 " + hb.next_due_at);
      if (hb.heartbeat_validated) parts.push("已通过真实 idle 验证");
      else parts.push("未通过真实 idle 验证");
      return parts.join(" · ");
    }

    /** 自动重登没有独立服务端开关：由凭据状态/失败原因推导，只读展示。 */
    function autoReloginText(conn) {
      var cred = (conn && conn.credential) || {};
      if (cred.state === "valid") {
        return "自动重登：凭据有效，由 collector 在会话失效后续登；与保活开关相互独立。";
      }
      if (cred.state === "locked") {
        return "自动重登：已暂停（凭据锁定）；需人工更换凭据后恢复，系统不会自动重试。";
      }
      if (cred.last_error) {
        return "自动重登：已暂停（" + cred.last_error + "）；需人工重试或更换凭据。";
      }
      return "自动重登：未配置凭据，暂不自动重登；与保活开关相互独立。";
    }

    function refreshConnectionsView() {
      if (state.tab !== "connections") return;
      util.clear(body);
      renderConnections();
    }

    function updateCredential(cid, credential) {
      if (!credential) return;
      state.connections.items = state.connections.items.map(function (item) {
        if (item.connection_id !== cid) return item;
        return Object.assign({}, item, { credential: credential });
      });
    }

    function operationNote(cid) {
      var op = state.connections.operations[cid];
      if (!op) return null;
      var stateText = OPERATION_STATES[op.state] || op.state;
      var reason = op.reason_code ? (OPERATION_REASONS[op.reason_code] || op.reason_code) : "";
      return h("div.small.muted", { "data-sync-operation": cid },
        "最近操作：" + stateText + (op.kind ? " · " + op.kind : "") + (reason ? " · " + reason : "")
      );
    }

    function trackOperation(cid, op) {
      if (!op || !op.op_id) return;
      var C = state.connections;
      C.operations[cid] = op;
      var terminal = op.state !== "queued" && op.state !== "running";
      if (terminal) {
        var result = op.result || {};
        if (result.heartbeat) C.heartbeats[cid] = result.heartbeat;
        if (result.credential) updateCredential(cid, result.credential);
      }
      refreshConnectionsView();
      if (terminal) return;
      var attempts = C.pollAttempts[cid] || 0;
      if (attempts >= 10) return;
      C.pollAttempts[cid] = attempts + 1;
      var timer = setTimeout(function () {
        if (C.loadedFor !== workspaceId()) return;
        MC.api.requestV3("GET", connectionPath(cid) + "/operations/" + encodeURIComponent(op.op_id)).then(function (data) {
          if (C.loadedFor !== workspaceId()) return;
          trackOperation(cid, data && data.operation);
        }, function (err) {
          if (C.loadedFor !== workspaceId()) return;
          C.error = errorText(err);
          refreshConnectionsView();
        });
      }, 700);
      C.pollTimers.push(timer);
    }

    function loadConnections(reset) {
      var C = state.connections;
      if (C.loading && C.loadedFor === workspaceId()) return Promise.resolve();
      var wid = workspaceId();
      var seq = (C.loadSeq || 0) + 1;
      C.loadSeq = seq;
      C.loading = true;
      C.error = "";
      C.loadedFor = wid;
      if (reset) {
        C.items = [];
        C.heartbeats = {};
        C.heartbeatErrors = {};
        C.operations = {};
        C.pollAttempts = {};
      }
      function current() {
        return seq === C.loadSeq && C.loadedFor === workspaceId();
      }
      refreshConnectionsView();
      return MC.api.requestV3("GET", connectionsPath()).then(function (data) {
        if (!current()) return;
        C.items = (data && data.items) || [];
        var jobs = C.items.map(function (item) {
          var cid = item.connection_id;
          return MC.api.requestV3("GET", heartbeatPath(cid)).then(function (hb) {
            if (!current()) return;
            C.heartbeats[cid] = hb;
            delete C.heartbeatErrors[cid];
          }, function (err) {
            if (!current()) return;
            C.heartbeats[cid] = null;
            C.heartbeatErrors[cid] = errorText(err);
          });
        });
        return Promise.all(jobs).then(function () {
          if (!current()) return;
          C.loading = false;
          refreshConnectionsView();
        });
      }, function (err) {
        if (!current()) return;
        C.loading = false;
        C.error = errorText(err);
        refreshConnectionsView();
      });
    }

    function saveHeartbeat(conn, enabledInput, intervalInput, tokenInput, errorNode) {
      var cid = conn.connection_id;
      var hb = state.connections.heartbeats[cid] || { revision: 0 };
      var interval = Number(intervalInput.value);
      if (!isFinite(interval) || interval < 1 || interval > 60) {
        errorNode.textContent = "保活间隔必须在 1–60 分钟";
        errorNode.style.display = "";
        return;
      }
      var token = tokenInput.value.trim();
      if (!token) {
        errorNode.textContent = "请输入运维 token";
        errorNode.style.display = "";
        return;
      }
      errorNode.style.display = "none";
      MC.api.requestV3("PUT", heartbeatPath(cid), {
        body: { interval_minutes: interval, enabled: !!enabledInput.checked },
        headers: { "If-Match": String(hb.revision == null ? 0 : hb.revision), "X-Operator-Token": token },
      }).then(function (saved) {
        state.connections.heartbeats[cid] = saved;
        tokenInput.value = "";
        MC.toast.success("保活设置已保存");
        refreshConnectionsView();
      }, function (err) {
        errorNode.textContent = errorText(err);
        errorNode.style.display = "";
      });
    }

    function runHeartbeat(conn, tokenInput, errorNode) {
      var cid = conn.connection_id;
      var token = tokenInput.value.trim();
      if (!token) {
        errorNode.textContent = "请输入运维 token";
        errorNode.style.display = "";
        return;
      }
      errorNode.style.display = "none";
      MC.api.requestV3("POST", heartbeatPath(cid) + "/run", {
        body: {},
        headers: { "X-Operator-Token": token },
      }).then(function (data) {
        var op = data && data.operation;
        if (!op) {
          errorNode.textContent = "保活运行未返回操作状态";
          errorNode.style.display = "";
          return;
        }
        trackOperation(cid, op);
        if (op.state === "blocked") {
          MC.toast.warning("保活未执行", OPERATION_REASONS[op.reason_code] || op.reason_code || "");
        } else if (op.state === "failed") {
          MC.toast.error("保活失败", OPERATION_REASONS[op.reason_code] || op.reason_code || "");
        } else {
          MC.toast.success("保活已执行");
        }
      }, function (err) {
        errorNode.textContent = errorText(err);
        errorNode.style.display = "";
      });
    }

    function openCreateConnection() {
      var nameInput = h("input.input", { autofocus: true, "data-conn-name": "1" });
      var originInput = h("input.input", { placeholder: "SDA Origin（部署端内网地址）", "data-conn-origin": "1" });
      var enabledInput = h("input", { type: "checkbox", checked: true, "data-conn-enabled": "1" });
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-conn-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-conn-error": "1" });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "新建连接",
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("SDA Origin", originInput),
          h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用连接")),
          field("运维 token（仅本次输入，不保存）", tokenInput),
          h("p.xsmall.muted", "连接只保存名称、Origin、协议与启用状态，不内嵌任何密码/Cookie。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-conn-create-submit": "1",
              onclick: function () {
                var name = nameInput.value.trim();
                var origin = originInput.value.trim();
                var token = tokenInput.value.trim();
                if (!name || !origin) { showError("名称与 Origin 不能为空"); return; }
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                MC.api.requestV3("POST", connectionsPath(), {
                  body: { name: name, origin: origin, enabled: !!enabledInput.checked },
                  headers: { "X-Operator-Token": token },
                }).then(function () {
                  close();
                  MC.toast.success("连接已创建");
                  loadConnections(true);
                }, function (err) {
                  showError(errorText(err));
                });
              },
            }, "创建"),
          ];
        },
        onClose: function () { tokenInput.value = ""; },
      });
      return modal;
    }

    function openConnectionEditor(conn) {
      var nameInput = h("input.input", { value: conn.name || "", autofocus: true, "data-conn-name": "1" });
      var originInput = h("input.input", { value: conn.origin || "", "data-conn-origin": "1" });
      var enabledInput = h("input", { type: "checkbox", checked: conn.enabled !== false, "data-conn-enabled": "1" });
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-conn-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-conn-error": "1" });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "编辑连接",
        subtitle: conn.name + " · " + conn.connection_id,
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("SDA Origin", originInput),
          h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用连接")),
          field("运维 token（仅本次输入，不保存）", tokenInput),
          h("p.xsmall.muted", "保存使用连接 revision " + conn.policy_revision + " 做 CAS；过期时 409 并保留当前输入。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-conn-save": "1",
              onclick: function () {
                var name = nameInput.value.trim();
                var origin = originInput.value.trim();
                var token = tokenInput.value.trim();
                if (!name || !origin) { showError("名称与 Origin 不能为空"); return; }
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                MC.api.requestV3("PATCH", connectionPath(conn.connection_id), {
                  body: { name: name, origin: origin, enabled: !!enabledInput.checked },
                  headers: { "If-Match": String(conn.policy_revision), "X-Operator-Token": token },
                }).then(function (updated) {
                  var credential = conn.credential;
                  state.connections.items = state.connections.items.map(function (item) {
                    if (item.connection_id !== conn.connection_id) return item;
                    return Object.assign({}, item, updated || {}, { credential: credential });
                  });
                  close();
                  MC.toast.success("连接已保存");
                  refreshConnectionsView();
                }, function (err) {
                  showError(errorText(err));
                });
              },
            }, "保存"),
          ];
        },
        onClose: function () { tokenInput.value = ""; },
      });
      return modal;
    }

    function openCredentialForm(conn) {
      var usernameInput = h("input.input", { autofocus: true, autocomplete: "off", "data-cred-username": "1" });
      var passwordInput = h("input.input", { type: "password", autocomplete: "new-password", "data-cred-password": "1" });
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-cred-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-cred-error": "1" });
      var cred = conn.credential || {};

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "更换凭据",
        subtitle: conn.name + " · 当前状态 " + credentialLabel(cred),
        body: h("div.col", { style: "gap:12px" },
          error,
          field("账号", usernameInput),
          field("密码", passwordInput),
          field("运维 token（仅本次输入，不保存）", tokenInput),
          h("p.xsmall.muted", "提交只创建待验证候选；验证/激活需人工单独触发。失败时密码框清空，且不会回显明文。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-cred-submit": "1",
              onclick: function () {
                var username = usernameInput.value.trim();
                var password = passwordInput.value;
                var token = tokenInput.value.trim();
                if (!username || !password) { showError("账号与密码不能为空"); return; }
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                MC.api.requestV3("POST", connectionPath(conn.connection_id) + "/credentials/candidates", {
                  body: { username: username, password: password },
                  headers: { "X-Operator-Token": token },
                }).then(function (statePayload) {
                  passwordInput.value = "";
                  updateCredential(conn.connection_id, statePayload);
                  close();
                  MC.toast.success("已创建待验证凭据候选");
                  refreshConnectionsView();
                }, function (err) {
                  // 失败只显示安全错误码；密码框立即清空，绝不把明文带回界面。
                  passwordInput.value = "";
                  showError(credentialErrorText(err));
                });
              },
            }, "创建候选"),
          ];
        },
        onClose: function () {
          passwordInput.value = "";
          tokenInput.value = "";
        },
      });
      return modal;
    }

    function openOperatorAction(conn, title, description, submitLabel, action) {
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", autofocus: true, "data-cred-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-cred-error": "1" });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: title,
        subtitle: conn.name,
        body: h("div.col", { style: "gap:12px" },
          error,
          h("p.small", description),
          field("运维 token（仅本次输入，不保存）", tokenInput)
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-cred-action-submit": "1",
              onclick: function () {
                var token = tokenInput.value.trim();
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                action(token).then(function (op) {
                  close();
                  if (op) trackOperation(conn.connection_id, op);
                }, function (err) {
                  showError(credentialErrorText(err));
                });
              },
            }, submitLabel),
          ];
        },
        onClose: function () { tokenInput.value = ""; },
      });
      return modal;
    }

    function openProbe(conn) {
      var candidate = (conn.credential || {}).candidate || {};
      return openOperatorAction(
        conn,
        "验证凭据",
        "验证只提交一次探测；密码错误会锁存凭据，需要人工重试或更换凭据，不会自动重复尝试。",
        "开始验证",
        function (token) {
          return MC.api.requestV3("POST", connectionPath(conn.connection_id) + "/credentials/probe", {
            body: { candidate_id: candidate.candidate_id },
            headers: { "X-Operator-Token": token },
          }).then(function (data) { return data && data.operation; });
        }
      );
    }

    function openActivate(conn) {
      var candidate = (conn.credential || {}).candidate || {};
      return openOperatorAction(
        conn,
        "激活凭据",
        "激活会覆盖当前有效凭据；probe_epoch 必须与候选一致，晚到的验证结果不能激活。",
        "激活",
        function (token) {
          return MC.api.requestV3(
            "POST",
            connectionPath(conn.connection_id) + "/credentials/candidates/"
              + encodeURIComponent(candidate.candidate_id) + "/activate",
            {
              body: { probe_epoch: candidate.auth_epoch == null ? 0 : candidate.auth_epoch },
              headers: { "X-Operator-Token": token },
            }
          ).then(function (data) { return data && data.operation; });
        }
      );
    }

    function renderHeartbeatCard(conn, manage) {
      var cid = conn.connection_id;
      var C = state.connections;
      var hb = C.heartbeats[cid];
      var cred = conn.credential || {};
      var error = h("div.form-error", { style: "display:none" });
      var controls = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      var tokenInput = null;

      if (manage) {
        var enabledInput = h("input", { type: "checkbox", checked: !!(hb && hb.enabled), "data-hb-enabled": cid });
        var intervalInput = h("input.input", {
          type: "number", min: "1", max: "60",
          value: String((hb && hb.interval_minutes) || 10),
          "data-hb-interval": cid,
          style: "max-width:100px",
        });
        tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-hb-token": cid, style: "max-width:180px" });
        controls.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用保活")));
        controls.appendChild(h("span.small.muted", "间隔"));
        controls.appendChild(intervalInput);
        controls.appendChild(h("span.small.muted", "分钟"));
        controls.appendChild(tokenInput);
        controls.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-hb-save": cid,
          onclick: function () { saveHeartbeat(conn, enabledInput, intervalInput, tokenInput, error); },
        }, "保存保活"));
        controls.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-hb-run": cid,
          onclick: function () { runHeartbeat(conn, tokenInput, error); },
        }, "立即保活"));
      } else {
        controls.appendChild(h("span.small.muted", { "data-hb-readonly": cid },
          "只读身份：不能修改保活开关或立即运行。"));
      }

      var candidate = cred.candidate;
      return h("div.card", h("div.card-b",
        h("div.row", { style: "gap:10px;flex-wrap:wrap;align-items:center" },
          h("strong", "保活与自动重登"),
          h("span.muted.small", conn.name + " · " + cid)
        ),
        h("p.small", { "data-hb-status": cid }, heartbeatStatusText(hb)),
        C.heartbeatErrors[cid] ? h("p.form-error", C.heartbeatErrors[cid]) : null,
        h("p.small.muted", { "data-auto-relogin": cid }, autoReloginText(conn)),
        controls,
        error,
        (cred.state === "locked" || cred.last_error === "bad_credentials"
          || (C.operations[cid] && (C.operations[cid].reason_code === "bad_credentials" || C.operations[cid].reason_code === "locked")))
          ? h("p.small", { "data-cred-manual": cid }, "需人工重试/更换凭据：系统不会自动重复提交凭据。")
          : null,
        candidate && candidate.state === "pending"
          ? h("p.xsmall.muted", "候选 " + candidate.candidate_id + " 待验证；验证与激活需人工分别触发。")
          : null,
        operationNote(cid)
      ));
    }

    function renderConnections() {
      var C = state.connections;
      var manage = canManageCredentials();

      if (C.error) {
        body.appendChild(h("div.form-error", C.error));
      }
      if (C.loading && !C.items.length) {
        body.appendChild(h("p.muted", "正在读取连接与保活状态…"));
        return;
      }
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", "data-connections-refresh": "1", onclick: function () { loadConnections(true); } }, "刷新"));
      if (manage) {
        toolbar.appendChild(h("button.btn.btn-sm.btn-primary", {
          type: "button", "data-conn-create": "1",
          onclick: function () { openCreateConnection(); },
        }, "新建连接"));
      } else {
        body.appendChild(h("p.small.muted", { "data-connections-readonly": "1" },
          "只读身份：可查看连接、凭据与保活状态，不能编辑连接、更换凭据或修改保活。"));
      }

      if (!C.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar, h("p.muted", "当前工作区还没有连接。"))));
        return;
      }

      var tbody = h("tbody");
      C.items.forEach(function (conn) {
        var cred = conn.credential || {};
        var actions = h("td.row", { style: "gap:6px;flex-wrap:wrap" });
        if (manage) {
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button", "data-conn-edit": conn.connection_id,
            onclick: function () { openConnectionEditor(conn); },
          }, "编辑"));
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button", "data-conn-cred": conn.connection_id,
            onclick: function () { openCredentialForm(conn); },
          }, "更换凭据"));
          if (cred.candidate && cred.candidate.state === "pending") {
            actions.appendChild(h("button.btn.btn-sm", {
              type: "button", "data-cred-probe": conn.connection_id,
              onclick: function () { openProbe(conn); },
            }, "验证"));
            actions.appendChild(h("button.btn.btn-sm", {
              type: "button", "data-cred-activate": conn.connection_id,
              onclick: function () { openActivate(conn); },
            }, "激活"));
          }
        } else {
          actions.appendChild(h("span.muted.xsmall", "只读"));
        }
        tbody.appendChild(h("tr", { "data-connection": conn.connection_id },
          h("td", h("div", h("strong", conn.name)), h("div.xsmall.muted", conn.connection_id)),
          h("td", h("span.small", conn.origin || "—")),
          h("td", conn.enabled === false
            ? h("span.badge.badge-count", "已停用")
            : h("span.badge.badge-success", "已启用")),
          h("td", h("span", { class: credentialBadge(cred), "data-cred-state": conn.connection_id }, credentialLabel(cred))),
          h("td", h("span.small", { "data-cred-epoch": conn.connection_id }, String(cred.auth_epoch == null ? 0 : cred.auth_epoch))),
          h("td", h("span.xsmall.muted", { "data-cred-error": conn.connection_id }, cred.last_error || "—")),
          actions
        ));
      });

      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "连接"), h("span.muted.small", "工作区 " + workspaceId())),
        h("div.card-b", toolbar,
          h("table.table", h("thead", h("tr",
            h("th", "名称"), h("th", "Origin"), h("th", "启用"),
            h("th", "凭据状态"), h("th", "auth_epoch"), h("th", "最近错误"), h("th", "")
          )), tbody)
        )
      ));

      C.items.forEach(function (conn) {
        body.appendChild(renderHeartbeatCard(conn, manage));
      });
    }

    function renderSources() {
      var S = state.sources;
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var search = h("input.input", { type: "search", placeholder: "搜索名称/路径", value: S.query, style: "min-width:200px" });
      var status = h("select.input", {},
        SOURCE_STATUS.map(function (item) { return h("option", { value: item.id }, item.label); })
      );
      var sort = h("select.input", {},
        SORTS.map(function (item) { return h("option", { value: item.id }, item.label); })
      );
      function searchNow() {
        S.query = search.value.trim();
        loadSources(true);
      }
      search.addEventListener("keydown", function (event) {
        if (event.key === "Enter") searchNow();
      });
      var searchBtn = h("button.btn.btn-sm", { type: "button", onclick: searchNow }, "搜索");
      status.value = S.status;
      sort.value = S.sort;
      status.addEventListener("change", function () { S.status = status.value; loadSources(true); });
      sort.addEventListener("change", function () { S.sort = sort.value; renderBody(); });
      var refresh = h("button.btn.btn-sm", { type: "button", onclick: function () { loadSources(true); } }, "刷新");
      toolbar.appendChild(search);
      toolbar.appendChild(searchBtn);
      toolbar.appendChild(status);
      toolbar.appendChild(sort);
      toolbar.appendChild(refresh);

      if (S.error) {
        body.appendChild(h("div.form-error", S.error));
      }
      if (S.loading && !S.items.length) {
        body.appendChild(h("p.muted", "正在读取更新源…"));
        return;
      }
      if (!S.items.length) {
        body.appendChild(h("div.card", h("div.card-b", h("p.muted", "当前工作区还没有更新源。"))));
        return;
      }
      var tbody = h("tbody");
      sortedItems().forEach(function (source) {
        var actions = h("td.row", { style: "gap:6px" });
        if (canWrite()) {
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-edit": source.source_id,
            onclick: function () { openEditor(source); },
          }, "编辑"));
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-run": source.source_id,
            onclick: function (event) { runSource(source, event.currentTarget); },
          }, "更新"));
        } else {
          actions.appendChild(h("span.muted.xsmall", "只读"));
        }
        tbody.appendChild(h("tr", { "data-sync-source": source.source_id },
          h("td", h("div", h("strong", source.name)), h("div.xsmall.muted", source.source_id)),
          h("td", h("span.badge", KIND_LABEL[source.kind] || source.kind)),
          h("td", h("div.small", source.display_path || source.resource_ref || "—"), h("div.xsmall.muted", source.record_source ? "含修改记录" : "无记录链接")),
          h("td", source.enabled === false ? h("span.badge.badge-muted", "已停用") : h("span.badge.badge-ok", "已启用")),
          h("td", h("div.small", scheduleText(source)), h("div.xsmall.muted", source.keyword ? "关键词 " + source.keyword : "无关键词")),
          actions
        ));
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "更新源"), h("span.muted.small", "共 " + S.total + " 条")),
        h("div.card-b",
          toolbar,
          h("table.table", h("thead", h("tr", h("th", "名称"), h("th", "类型"), h("th", "路径/引用"), h("th", "状态"), h("th", "计划"), h("th", ""))), tbody),
          S.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { S.expanded = true; loadSources(false); } }, "加载更多") : null
        )
      ));
    }

    function runsPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/runs";
    }

    function runPath(runId) {
      return runsPath() + "/" + encodeURIComponent(runId);
    }

    function runStateText(state) {
      return {
        queued: "排队中",
        running: "进行中",
        succeeded: "已完成",
        partial: "部分失败",
        failed: "失败",
        canceled: "已取消",
        interrupted: "中断",
      }[state] || state;
    }

    function runCounts(run) {
      var counts = run.counts || {};
      return "共 " + (counts.total || 0) + " · 成功 " + (counts.succeeded || 0) +
        " · 失败 " + (counts.failed || 0) + " · 阻断 " + (counts.blocked || 0);
    }

    function loadRuns(reset) {
      var R = state.runs;
      if (R.loading) return Promise.resolve();
      R.loading = true;
      R.error = "";
      if (reset) {
        R.items = [];
        R.next_cursor = null;
        R.expanded = false;
      }
      renderBody();
      return MC.api.requestV3("GET", runsPath(), {
        query: {
          state: R.state || undefined,
          limit: 50,
          cursor: R.expanded && R.next_cursor ? R.next_cursor : "0",
        },
      }).then(function (data) {
        var incoming = (data && data.items) || [];
        R.items = R.expanded ? R.items.concat(incoming) : incoming;
        R.total = data && typeof data.total === "number" ? data.total : R.items.length;
        R.next_cursor = data ? data.next_cursor : null;
        R.loading = false;
        renderBody();
      }, function (err) {
        R.loading = false;
        R.error = errorText(err);
        renderBody();
      });
    }

    function downloadDiagnostics(run) {
      var eventsUrl = runPath(run.run_id) + "/events";
      MC.api.requestV3("GET", eventsUrl, {}).then(function (data) {
        var payload = {
          run: run,
          events: (data && data.items) || [],
          exported_at: new Date().toISOString(),
          note: "MCChecker sync diagnostics; no secrets are included",
        };
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "sync-run-" + run.run_id + "-diagnostics.json";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      }, function (err) {
        MC.toast.error("诊断下载失败", errorText(err));
      });
    }

    function retryRun(run, close) {
      MC.api.requestV3("POST", runPath(run.run_id) + "/retry", { body: {} }).then(function () {
        MC.toast.success("失败项已重新入队");
        if (close) close();
        loadRuns(true);
      }, function (err) {
        MC.toast.error("重试提交失败", errorText(err));
      });
    }

    function openRun(run) {
      var bodyNode = h("div.col", { style: "gap:12px" }, h("p.muted.small", "正在读取运行详情…"));
      var modal = MC.modal({
        title: "运行 " + run.run_id,
        subtitle: run.trigger + " · " + runStateText(run.state) + " · " + runCounts(run),
        size: "xwide",
        body: bodyNode,
        footer: function (close) {
          var buttons = [];
          if (canRun() && ["partial", "failed", "interrupted"].indexOf(run.state) >= 0) {
            buttons.push(h("button.btn.btn-primary", { type: "button", "data-sync-retry": "1", onclick: function () { retryRun(run, close); } }, "重试失败项"));
          }
          buttons.push(h("button.btn", { type: "button", onclick: function () { downloadDiagnostics(run); } }, "下载诊断"));
          buttons.push(h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭"));
          return buttons;
        },
      });
      Promise.all([
        MC.api.requestV3("GET", runPath(run.run_id), {}),
        MC.api.requestV3("GET", runPath(run.run_id) + "/events", { query: { after_seq: 0 } }),
      ]).then(function (results) {
        var detail = results[0] || {};
        var events = (results[1] && results[1].items) || [];
        var items = detail.items || [];
        util.clear(bodyNode);
        var itemRows = items.length ? items.map(function (item) {
          return h("tr",
            h("td", h("div.small", item.name || item.source_id), h("div.xsmall.muted", item.source_id || "")),
            h("td", runStateText(item.state)),
            h("td", item.stage || "—"),
            h("td", item.reason_code || "—"),
            h("td", item.committed ? "是" : "否")
          );
        }) : [h("tr", h("td", { colspan: "5" }, h("span.muted", "没有 item 记录。")))];
        bodyNode.appendChild(h("table.table",
          h("thead", h("tr", h("th", "来源"), h("th", "状态"), h("th", "阶段"), h("th", "原因"), h("th", "已提交"))),
          h("tbody", itemRows)
        ));
        bodyNode.appendChild(h("h3", "事件日志"));
        var eventRows = events.length ? events.map(function (event) {
          return h("li", h("span.muted.xsmall", event.at || ""), " · ",
            h("strong", event.status || event.level || ""), " ", event.message || "", event.reason_code ? h("span.xsmall.muted", " (" + event.reason_code + ")") : null);
        }) : [h("li.muted", "没有事件日志。")];
        bodyNode.appendChild(h("ul.mini-list", eventRows));
        if (detail.run) run = detail.run;
      }, function (err) {
        util.clear(bodyNode);
        bodyNode.appendChild(h("div.form-error", errorText(err)));
      });
    }

    function renderRuns() {
      var R = state.runs;
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var filter = h("select.input", {},
        h("option", { value: "" }, "全部状态"),
        ["running", "partial", "failed", "succeeded", "canceled", "interrupted"].map(function (value) {
          return h("option", { value: value }, runStateText(value));
        })
      );
      filter.value = R.state;
      filter.addEventListener("change", function () { R.state = filter.value; loadRuns(true); });
      toolbar.appendChild(filter);
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", onclick: function () { loadRuns(true); } }, "刷新"));
      if (R.error) body.appendChild(h("div.form-error", R.error));
      if (R.loading && !R.items.length) {
        body.appendChild(h("p.muted", "正在读取运行记录…"));
        return;
      }
      if (!R.items.length) {
        body.appendChild(h("div.card", h("div.card-b", h("p.muted", "还没有同步运行记录。"))));
        return;
      }
      var tbody = h("tbody");
      R.items.forEach(function (doc) {
        var run = doc.run || doc;
        var row = h("tr", { "data-sync-run": run.run_id, style: "cursor:pointer" },
          h("td", h("div.small", run.run_id), h("div.xsmall.muted", run.parent_run_id ? "父 " + run.parent_run_id : "")),
          h("td", run.trigger),
          h("td", runStateText(run.state)),
          h("td", runCounts(run)),
          h("td", h("div.small", util.formatTime(run.created_at)), h("div.xsmall.muted", run.finished_at ? util.formatTime(run.finished_at) : "未结束"))
        );
        row.addEventListener("click", function () { openRun(run); });
        tbody.appendChild(row);
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "运行记录"), h("span.muted.small", "共 " + R.total + " 条")),
        h("div.card-b", toolbar, h("table.table",
          h("thead", h("tr", h("th", "运行"), h("th", "触发"), h("th", "状态"), h("th", "计数"), h("th", "时间"))),
          tbody
        ), R.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { R.expanded = true; loadRuns(false); } }, "加载更多") : null)
      ));
    }

    function candidatesPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/candidates";
    }

    function loadCandidates(reset) {
      var C = state.candidates;
      if (C.loading) return Promise.resolve();
      C.loading = true;
      C.error = "";
      if (reset) { C.items = []; C.next_cursor = null; }
      renderBody();
      return MC.api.requestV3("GET", candidatesPath(), {
        query: { state: C.state || undefined, limit: 50, cursor: "0" },
      }).then(function (data) {
        C.items = (data && data.items) || [];
        C.total = data && typeof data.total === "number" ? data.total : C.items.length;
        C.next_cursor = data ? data.next_cursor : null;
        C.loading = false;
        C.loadedFor = workspaceId();
        renderBody();
      }, function (err) {
        C.loading = false;
        C.loadedFor = workspaceId();
        C.error = errorText(err);
        renderBody();
      });
    }

    function openCandidate(candidate) {
      var error = h("div.form-error", { style: "display:none" });
      var configs = [];
      var configSelect = h("select.input", {});
      var releaseSelect = h("select.input", {});
      var previewBox = h("div");
      var configPath = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
        "/profiles/" + encodeURIComponent(candidate.profile_id) + "/configs";
      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }
      function loadReleases() {
        var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
        if (!config) return;
        MC.api.requestV3("GET", configPath + "/" + encodeURIComponent(config.config_id) + "/releases", {})
          .then(function (data) {
            util.clear(releaseSelect);
            (data.items || []).forEach(function (release) {
              releaseSelect.appendChild(h("option", { value: release.release_id }, "版本 " + (release.version || release.release_id)));
            });
            releaseSelect.disabled = !releaseSelect.options.length;
          }, function (err) { showError(errorText(err)); });
      }
      var modal = MC.modal({
        title: "候选 " + candidate.candidate_id,
        subtitle: "来源 " + candidate.source_id + " · " + candidate.state,
        size: "xwide",
        body: h("div.col", { style: "gap:12px" },
          error,
          h("div.row", { style: "gap:8px;flex-wrap:wrap" },
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "固定配置"), configSelect),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "软件版本"), releaseSelect)
          ),
          h("div.row", { style: "gap:8px" },
            h("button.btn.btn-sm", { type: "button", "data-sync-preview": "1", onclick: function () {
              var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
              if (!config || !releaseSelect.value) { showError("先选择固定配置与软件版本"); return; }
              previewBox.appendChild(h("p.muted", "正在生成差异…"));
              MC.api.requestV3("POST", candidatesPath() + "/" + encodeURIComponent(candidate.candidate_id) + "/apply-preview", {
                body: { config_id: config.config_id, draft_id: "d0-" + releaseSelect.value },
              }).then(function (preview) { renderPreview(preview); }, function (err) { showError(errorText(err)); util.clear(previewBox); });
            } }, "生成差异预览")
          ),
          previewBox
        ),
        footer: function (close) {
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭")];
        },
      });
      if (!candidate.file_version_id) {
        showError("该候选没有关联文件版本，无法生成参数差异（SDA 表候选需后续协议支持）");
      }
      MC.api.requestV3("GET", configPath, {}).then(function (data) {
        configs = data.items || [];
        util.clear(configSelect);
        configs.forEach(function (config) {
          configSelect.appendChild(h("option", { value: config.config_id }, (config.code || "") + " " + (config.name || config.config_id)));
        });
        configSelect.addEventListener("change", loadReleases);
        if (configs.length) loadReleases();
      }, function (err) { showError(errorText(err)); });

      function renderPreview(preview) {
        util.clear(previewBox);
        var items = preview.items || [];
        var decisions = {};
        var tableRows = items.map(function (item) {
          var decision = null;
          if (item.action === "conflict") {
            decision = h("select.input", { "data-sync-decision": item.parameter_id },
              h("option", { value: "" }, "请选择"),
              h("option", { value: "use_new" }, "采用新值"),
              h("option", { value: "inherit_old" }, "保留当前"),
              h("option", { value: "manual" }, "人工处理")
            );
            decision.addEventListener("change", function () { decisions[item.parameter_id] = decision.value; });
          }
          return h("tr",
            h("td", h("div.small", item.name || item.parameter_id), h("div.xsmall.muted", item.parameter_id)),
            h("td", h("span.badge", item.action)),
            h("td", JSON.stringify(item.ours && item.ours.value || null)),
            h("td", JSON.stringify(item.theirs && item.theirs.value || null)),
            h("td", decision || h("span.muted", item.action === "keep" ? "保留" : "—"))
          );
        });
        previewBox.appendChild(h("table.table",
          h("thead", h("tr", h("th", "参数"), h("th", "计划"), h("th", "当前"), h("th", "候选"), h("th", "决议"))),
          h("tbody", tableRows)
        ));
        if (preview.blockers && preview.blockers.length) {
          previewBox.appendChild(h("div.form-error", preview.blockers.map(function (row) { return row.message || row.code; }).join("；")));
        }
        if (preview.d0_frozen) {
          previewBox.appendChild(h("p.form-error", "D0 已冻结，不提供覆盖；请选择新的 Bn 草稿。"));
        }
        var apply = h("button.btn.btn-primary", { type: "button", "data-sync-apply": "1", onclick: function () {
          if (!canWrite()) return;
          var requestId = "apply-" + Date.now();
          MC.api.requestV3("POST", candidatesPath() + "/" + encodeURIComponent(candidate.candidate_id) + "/apply", {
            body: { preview_id: preview.preview_id, request_id: requestId, decisions: decisions },
          }).then(function (result) {
            MC.toast.success("已写入 V3 草稿（rev " + result.draft_revision + "）；发布请到配置管理");
            loadCandidates(true);
          }, function (err) { showError(errorText(err)); });
        } }, "写入 V3 草稿");
        if (!canWrite()) apply.disabled = true;
        if (preview.d0_frozen) apply.disabled = true;
        previewBox.appendChild(h("div.row", { style: "gap:8px;margin-top:8px" }, apply,
          h("span.xsmall.muted", "只写草稿，不发布 D0/Bn/rn")
        ));
      }
    }

    function renderCandidates() {
      var C = state.candidates;
      if (C.error) body.appendChild(h("div.form-error", C.error));
      if (C.loading && !C.items.length) { body.appendChild(h("p.muted", "正在读取候选…")); return; }
      if (!C.items.length) { body.appendChild(h("div.card", h("div.card-b", h("p.muted", "还没有更新候选。")))); return; }
      var tbody = h("tbody");
      C.items.forEach(function (candidate) {
        var row = h("tr", { "data-sync-candidate": candidate.candidate_id, style: "cursor:pointer" },
          h("td", h("div.small", candidate.candidate_id), h("div.xsmall.muted", candidate.source_id)),
          h("td", candidate.state),
          h("td", h("div.xsmall", candidate.content_hash || "—")),
          h("td", h("div.small", util.formatTime(candidate.created_at)), h("div.xsmall.muted", candidate.file_version_id ? "关联文件版本" : "无文件版本"))
        );
        row.addEventListener("click", function () { openCandidate(candidate); });
        tbody.appendChild(row);
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "更新候选"), h("span.muted.small", "共 " + C.total + " 条")),
        h("div.card-b", h("table.table",
          h("thead", h("tr", h("th", "候选"), h("th", "状态"), h("th", "内容指纹"), h("th", "时间/来源"))),
          tbody
        ))
      ));
    }

    function renderPlaceholder(tab) {
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", tab.label), h("span.muted.small", "工作区级")),
        h("div.card-b", h("p", tab.desc), h("p.muted.small", "该子页的真实 API/交互将在后续卡接入；当前不加载原型 mock 数据。"))
      ));
    }

    function renderBody() {
      util.clear(body);
      var tab = tabById(state.tab);
      if (tab.id === "sources") {
        renderSources();
      } else if (tab.id === "plan") {
        renderPlan();
        if (state.plan.loadedFor !== workspaceId() && !state.plan.loading) loadPlan();
      } else if (tab.id === "runs") {
        renderRuns();
      } else if (tab.id === "candidates") {
        renderCandidates();
        if (state.candidates.loadedFor !== workspaceId() && !state.candidates.loading) {
          loadCandidates(true);
        }
      } else if (tab.id === "connections") {
        renderConnections();
        if (state.connections.loadedFor !== workspaceId() && !state.connections.loading) {
          loadConnections(true);
        }
      } else {
        renderPlaceholder(tab);
      }
    }

    el.appendChild(h("div.view-body",
      h("div.page-head", h("div", h("h2", "更新管理"), h("p.muted", "更新源、计划、连接、运行记录与候选的统一入口；旧设置里的更新源/定时更新入口已收口到这里。"))),
      tabbar,
      body
    ));
    renderTabs();
    renderBody();
    if (state.tab === "sources") loadSources(true);
    if (state.tab === "runs") loadRuns(true);

    return {
      el: el,
      title: "更新管理",
      icon: "settings",
      update: function (next) {
        state.tab = tabById(next && next.arg).id;
        renderTabs();
        renderBody();
        if (state.tab === "sources") loadSources(true);
        if (state.tab === "runs") loadRuns(true);
      },
      isDirty: function () { return false; },
      destroy: function () {
        (state.connections.pollTimers || []).forEach(function (timer) { clearTimeout(timer); });
        state.connections.pollTimers = [];
      },
    };
  };
})(typeof window !== "undefined" ? window : this);
