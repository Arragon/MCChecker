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
    { id: "sources", label: "更新源" },
    { id: "runs", label: "运行记录" },
    { id: "candidates", label: "更新候选" },
  ];

  //: 手动/批量任务记录跨视图保留：切换 tab 或工作区后仍能看见 run 最初归属的 W。
  var RUN_LOG = [];
  var RUN_LOG_MAX = 20;
  var FAILED_ITEM_STATUSES = ["failed", "blocked", "interrupted", "canceled"];

  //: 与服务端 SOURCE_KINDS 严格一致；不列出不存在的类型（旧 http_record 已废弃）。
  var KIND_LABEL = { http_file: "HTTP 文件", sda_catalog: "SDA 目录", sda_table: "SDA 表", sda_file: "SDA 文件" };
  var KIND_OPTIONS = ["http_file", "sda_catalog", "sda_table", "sda_file"];
  var TARGET_LABEL = {
    file_archive: "文件归档",
    default_candidate: "默认配置候选",
    machine_observation: "机台实际值观测",
  };
  var TARGET_OPTIONS = ["file_archive", "default_candidate", "machine_observation"];
  var EXCLUDED_REASON = {
    NOT_FOUND: "目标或连接不存在",
    SOURCE_CHANGED: "catalog 引用与路径不一致",
    INVALID_INPUT: "配置不完整",
    DOWNLOAD_FAILED: "采集器不可用（离线）",
  };
  var DECISION_LABEL = {
    use_new: "采用新值",
    inherit_old: "保留当前",
    manual: "人工处理",
    pin: "固定",
    clear_pin: "取消固定",
  };
  var CANDIDATE_STATE_LABEL = {
    pending: "待决议",
    applied: "已写入草稿",
    dismissed: "已忽略",
    superseded: "已被新内容取代",
    conflicted: "有冲突",
    failed: "失败",
  };
  var SOURCE_STATUS = [
    { id: "", label: "全部状态" },
    { id: "failed", label: "仅失败" },
    { id: "candidates", label: "有候选" },
    { id: "enabled", label: "已启用" },
    { id: "disabled", label: "已停用" },
    { id: "deleted", label: "已删除" },
  ];
  var SORTS = [
    { id: "name", label: "按名称" },
    { id: "updated", label: "按更新时间（已加载）" },
  ];

  function tabById(id) {
    if (id === "plan" || id === "schedule" || id === "connections") return TABS[0];
    return TABS.filter(function (tab) { return tab.id === id; })[0] || TABS[0];
  }

  function sheetFor(route) {
    var arg = route && route.arg;
    if (arg === "plan" || arg === "schedule") return "plan";
    if (arg === "connections") return "connections";
    return "";
  }

  function workspaceId() {
    try {
      if (MC.workspaces && MC.workspaces.scopeId) return MC.workspaces.scopeId();
    } catch (e) { /* 测试页/旧壳 */ }
    return "default";
  }

  function captureWorkspace() {
    var wid = workspaceId();
    var epoch = MC.workspaces && MC.workspaces.epoch ? MC.workspaces.epoch() : null;
    return {
      id: wid,
      current: function () {
        return workspaceId() === wid && (epoch === null || !MC.workspaces || !MC.workspaces.epoch || MC.workspaces.epoch() === epoch);
      },
    };
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
    return source.schedule && source.schedule.configured_interval_hours
      ? "继承 · 每 " + source.schedule.configured_interval_hours + " 小时" : "继承计划";
  }

  function sourcePath(source) {
    return sourcesPath() + "/" + encodeURIComponent(source.source_id);
  }

  function errorText(err) {
    if (!err) return "操作失败";
    if (err.code === "REVISION_CONFLICT") return "数据已在其他客户端更新；当前草稿已保留，请刷新后重试";
    return err.message || "操作失败";
  }

  MC.views.updates = function (app, route) {
    var disposed = false, contextVersion = 0, dialogs = [];
    function viewModal(options) {
      var closed = options.onClose, modal;
      options.onClose = function (result) { dialogs = dialogs.filter(function (d) { return d !== modal; }); if (closed) closed(result); };
      modal = MC.modal(options); if (fileConfig) modal.el.classList.add("cv-dialog"); dialogs.push(modal); return modal;
    }
    function captureOrigin() {
      var origin = captureWorkspace(), version = contextVersion;
      return Object.assign({}, origin, { current: function () { return !disposed && version === contextVersion && origin.current(); } });
    }
    var fileConfig = route && route.query && route.query.config || "", fileDetail = null, fileError = null, fileSeq = 0;
    var fileNav = h("div");
    var requestedPreview = "";
    function maybeRequestedPreview(next) {
      var key = workspaceId() + ":preview_all:" + (next && next.query && next.query.request || "initial");
      if (!fileConfig && next && next.query && next.query.action === "preview_all" && requestedPreview !== key && canRun()) {
        requestedPreview = key; previewSources(null);
      }
    }
    function fileQuery(query) { return fileConfig ? Object.assign({}, query || {}, { config: fileConfig }) : query; }
    function scopeQuery(query) { return fileConfig ? Object.assign({}, query || {}, { configuration_id: fileConfig, profile_id: app.pid }) : query; }
    function filePreviewScope() { return fileConfig ? { scope: "current_configuration", configuration_id: fileConfig, profile_id: app.pid } : { scope: "workspace_all" }; }
    function loadFileContext() {
      var seq = ++fileSeq, id = fileConfig, wid = workspaceId(); fileDetail = null; fileError = null;
      paintFileNav();
      if (!id) return;
      MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(wid) + "/profiles/" + encodeURIComponent(app.pid) + "/configs/" + encodeURIComponent(id), {}).then(function (res) {
        if (seq !== fileSeq || wid !== workspaceId()) return;
        fileDetail = res.config; paintFileNav(); paintChrome(); renderBody();
      }, function (err) { if (seq === fileSeq && wid === workspaceId()) { fileError = err; paintChrome(); renderBody(); } });
    }
    function paintFileNav() {
      util.clear(fileNav);
      fileNav.classList.toggle("sync-file-context", !!fileConfig);
      el.classList.toggle("is-file-config", !!fileConfig);
      if (!fileConfig) { fileNav.appendChild(MC.configWorkspaceNav(app, "updates")); return; }
      var filename = fileDetail && (fileDetail.file && fileDetail.file.name || fileDetail.name) || "正在读取文件…";
      fileNav.appendChild(h("nav.cv-breadcrumb", { "aria-label": "面包屑" }, h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "files" }); } }, "配置管理"), MC.icon("chevron-right"), h("span", filename)));
      fileNav.appendChild(h("div.sync-file-heading", h("div.col", h("h1", filename), h("span.small.muted.sync-file-description", fileDetail ? fileDetail.name : "")), headActions));
      var tabs = MC.segmented([{ value: "versions", label: "版本演进" }, { value: "updates", label: "更新管理" }, { value: "history", label: "操作记录" }], "updates", function (value) {
        if (value !== "updates") app.go({ kind: "configv3", arg: "configs", query: { config: fileConfig, tab: value } });
      }, "配置文件页面");
      tabs.classList.add("cv-tabs"); fileNav.appendChild(tabs);
    }
    function freshPlan() {
      return { loadedFor: "", loading: false, error: "", policy: null, draft: null, sources: [], preview: null,
        previewWorkspaceId: "", previewing: false, validating: false, saving: false, conflict: false,
        conflictLoading: false, conflictComparison: null, running: false, failuresOnly: false, failedSourceIds: {} };
    }
    var state = {
      recentRun: null,
      tab: tabById(route && route.arg).id,
      sheet: sheetFor(route),
      sources: {
        items: [],
        total: 0,
        next_cursor: null,
        query: "",
        configuration_id: "",
        release_id: "",
        filters: { configurations: [], releases: [] },
        status: "",
        scope: "profile",
        loadSeq: 0,
        sort: "name",
        loading: false,
        error: "",
        expanded: false,
        selected: {},
        testing: {},
        busy: "",
        testResults: {},
      },
      runs: {
        query: "",
        failedOnly: false,
        items: [],
        total: 0,
        next_cursor: null,
        state: "",
        loading: false,
        error: "",
        expanded: false,
        loadSeq: 0,
      },
      candidates: {
        items: [],
        total: 0,
        next_cursor: null,
        state: "",
        profile_id: "",
        loading: false,
        error: "",
        loadedFor: "",
        selected: {},
        busy: "",
        loadSeq: 0,
      },
      plan: freshPlan(),
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
    var planErrorId = util.uid("plan-error");
    var el = h("div.sync-root");
    var tabbar = h("div.sync-subnav", { role: "tablist", "aria-label": "更新管理内容" });
    var body = h("div.sync-content", { role: "tabpanel", id: util.uid("sync-panel"), tabindex: "0" });
    //: 编辑抽屉是否有未保存草稿：驱动切 tab / 关抽屉 / 切工作区的提示。
    var activeEditorDirty = false;

    function renderTabs() {
      util.clear(tabbar);
      var tabs = fileConfig ? [tabById("sources"), tabById("candidates"), tabById("runs")] : TABS;
      var fileLabels = { sources: "更新来源", candidates: "待处理变化", runs: "运行记录" };
      tabs.forEach(function (tab) {
        var active = state.tab === tab.id;
        var button = h(
          "button.btn" + (active ? ".btn-primary" : ""),
          {
            type: "button",
            role: "tab",
            "aria-selected": active ? "true" : "false",
            "aria-controls": body.id,
            id: body.id + "-" + tab.id,
            tabindex: active ? "0" : "-1",
            "data-sync-tab": tab.id,
          },
          fileConfig ? fileLabels[tab.id] : tab.label
        );
        button.addEventListener("click", function () {
          if (activeEditorDirty) {
            MC.confirm({
              title: "放弃未保存的修改？",
              message: "切换分类会丢弃更新源编辑抽屉里未保存的草稿。",
              confirmText: "放弃并切换",
              danger: true,
            }).then(function (ok) {
              if (!ok) return;
              activeEditorDirty = false;
          state.sheet = "";
          state.tab = tab.id;
          renderTabs();
          renderBody();
          if (app && app.go) app.go({ kind: "updates", arg: tab.id, query: fileQuery() });
            });
            return;
          }
          state.sheet = "";
          state.tab = tab.id;
          renderTabs();
          renderBody();
          if (app && app.go) app.go({ kind: "updates", arg: tab.id, query: fileQuery() });
        });
        button.addEventListener("keydown", function (event) {
          var index = tabs.indexOf(tab);
          var target = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
            : event.key === "ArrowRight" ? (index + 1) % tabs.length
            : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : -1;
          if (target < 0) return;
          event.preventDefault();
          var next = tabbar.querySelector('[data-sync-tab="' + tabs[target].id + '"]');
          next.click();
          var focused = tabbar.querySelector('[data-sync-tab="' + tabs[target].id + '"]');
          if (focused) focused.focus();
        });
        if (active) body.setAttribute("aria-labelledby", button.id);
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
      if (S.loading && !reset) return Promise.resolve();
      var seq = ++S.loadSeq;
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
        configuration_id: S.configuration_id || undefined,
        release_id: S.release_id || undefined,
        status: S.status || undefined,
        profile_id: S.scope === "profile" ? ((app && app.pid) || (route && route.pid) || "default") : undefined,
        limit: 50,
        cursor: S.expanded && S.next_cursor ? S.next_cursor : "0",
      };
      return MC.api.requestV3("GET", sourcesPath(), { query: scopeQuery(query) }).then(function (data) {
        if (seq !== S.loadSeq) return;
        var incoming = (data && data.items) || [];
        S.items = S.expanded ? S.items.concat(incoming) : incoming;
        S.total = data && typeof data.total === "number" ? data.total : S.items.length;
        S.next_cursor = data ? data.next_cursor : null;
        S.filters = data && data.filters || S.filters;
        // 选中集合只保留仍在当前结果里的来源，避免"幽灵选中"。
        var alive = {};
        S.items.forEach(function (row) { alive[row.source_id] = true; });
        Object.keys(S.selected).forEach(function (sid) { if (!alive[sid]) delete S.selected[sid]; });
        S.loading = false;
        S.error = "";
        renderBody();
      }, function (err) {
        if (seq !== S.loadSeq) return;
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
      if (!control.id) control.id = util.uid("sync-field");
      return h("div.col", { style: "gap:4px" }, h("label.small.muted", { for: control.id }, label), control);
    }

    function fieldErrors(node) {
      node.id = util.uid("field-error");
      node.classList.add("sync-form-error");
      node.setAttribute("role", "alert");
      var invalid = null;
      function clearInvalid() {
        if (!invalid) return;
        invalid.removeAttribute("aria-invalid");
        invalid.removeAttribute("aria-describedby");
        invalid.removeEventListener("input", onChange);
        invalid.removeEventListener("change", onChange);
        invalid = null;
      }
      function onChange() { clearInvalid(); node.textContent = ""; node.style.display = "none"; }
      return function (message, control) {
        clearInvalid();
        node.textContent = message || "";
        node.style.display = message ? "" : "none";
        if (message && control && !control.disabled) {
          invalid = control;
          control.setAttribute("aria-invalid", "true");
          control.setAttribute("aria-describedby", node.id);
          control.addEventListener("input", onChange);
          control.addEventListener("change", onChange);
          control.focus();
        }
      };
    }

    function emptyState(title, description) {
      return h("div.sync-empty", h("h3", title), h("p.muted.small", description));
    }

    function sourceText(value, kind) {
      var text = String(value || "—");
      var isPath = kind === "path";
      var shortLimit = isPath ? 56 : 42;
      if (text.length <= shortLimit) return isPath ? h("code.xsmall.sync-path", text) : h("strong", text);
      var attrs = {};
      attrs[isPath ? "data-sync-long-path" : "data-sync-long-name"] = "1";
      return h("details.sync-long-text", attrs,
        h("summary", { "aria-label": (isPath ? "来源路径：" : "来源名称：") + text },
          isPath ? h("code.xsmall", text.slice(0, shortLimit) + "…") : h("strong", text.slice(0, shortLimit) + "…")),
        isPath ? h("code.xsmall.sync-path", text) : h("div.small", text));
    }

    function targetOfSource(source) {
      var target = (source && source.target) || null;
      return {
        kind: (target && target.kind) || "file_archive",
        file_id: (target && target.file_id) || "",
        configuration_id: (target && target.configuration_id) || "",
        release_id: (target && target.release_id) || "",
        device_id: (target && target.device_id) || "",
      };
    }

    function openEditor(source) {
      if (fileConfig && (!fileDetail || fileError)) return;
      var isCreate = !source;
      source = source || {};
      var hadTarget = !!(source && source.target);
      var targetDirty = false;
      var target0 = targetOfSource(source);
      if (isCreate && fileConfig) target0 = { kind: "default_candidate", configuration_id: fileConfig, file_id: fileDetail.file_id || "", release_id: "" };
      var draft = {
        name: source.name || "",
        profile_id: source.profile_id || (fileConfig ? app.pid : workspaceProfiles()[0].id),
        kind: source.kind || "http_file",
        connection_id: source.connection_id || "",
        resource_ref: source.resource_ref || "",
        record_source: source.record_source || "",
        keyword: source.keyword || "",
        display_path: source.display_path || "",
        enabled: source.enabled !== false,
        schedule_mode: source.schedule_mode || "inherit",
        interval_hours: source.interval_hours == null ? "" : String(source.interval_hours),
        target_kind: target0.kind,
        target_file_id: target0.file_id,
        target_config_id: target0.configuration_id,
        target_release_id: target0.release_id,
        target_device_id: target0.device_id,
      };
      var error = h("div.form-error.sync-form-error", { id: util.uid("source-error"), role: "alert", style: "display:none" });
      var invalidControl = null;
      var hint = h("p.xsmall.muted", { "data-sync-hint": "1" });
      var nameInput = h("input.input", { value: draft.name, placeholder: "例如：MachineConstant.xml", autofocus: true });
      var refInput = h("input.input", { value: draft.resource_ref, placeholder: "下载 URL / SDA 资源引用" });
      var displayInput = h("input.input", { value: draft.display_path, placeholder: "业务路径，例如 /synthetic/A.xml" });
      var recordInput = h("input.input", { value: draft.record_source, placeholder: "修改记录链接（可选）" });
      var keywordInput = h("input.input", { value: draft.keyword, placeholder: "压缩包内文件名/正则（可选）" });
      var profileSelect = h("select.input", {},
        workspaceProfiles().map(function (profile) { return h("option", { value: profile.id }, profile.name); })
      );
      profileSelect.value = draft.profile_id;
      if (!isCreate || fileConfig) profileSelect.disabled = true;
      var kindSelect = h("select.input", { "data-sync-kind": "1" },
        KIND_OPTIONS.map(function (kind) { return h("option", { value: kind }, KIND_LABEL[kind]); })
      );
      kindSelect.value = draft.kind;
      var connectionSelect = h("select.input", { "data-sync-connection": "1" },
        h("option", { value: "" }, "（HTTP 来源无需连接）")
      );
      connectionSelect.value = draft.connection_id;
      var catalogBlock = h("div");
      var connectionField = field("连接", connectionSelect);
      var resourceField = field("下载地址", refInput);
      var pathField = field("业务路径", displayInput);
      var resourceFields = h("div.col", { style: "gap:12px" }, resourceField, pathField, catalogBlock);
      var advancedFields = h("div.col", { style: "gap:12px;padding-top:12px" }, field("修改记录链接", recordInput), field("文件匹配规则（正则）", keywordInput));
      var advanced = h("details.sync-source-advanced", { open: !!(draft.record_source || draft.keyword || draft.display_path && !needsConnection()) }, h("summary.small", "高级设置"), advancedFields);
      var targetBlock = h("div.col", { style: "gap:8px" });
      var connections = [];
      var catalogItems = [];
      var fileItems = [];
      var configItems = [];
      var releaseItems = [];

      function currentKind() { return kindSelect.value; }
      function needsConnection() { return currentKind().indexOf("sda_") === 0; }
      function isCatalogKind() { return currentKind() === "sda_table" || currentKind() === "sda_file"; }

      function renderCatalogBlock() {
        util.clear(catalogBlock);
        if (!isCatalogKind()) return;
        var msg = h("span.xsmall.muted", { "data-sync-catalog-msg": "1" }, "ID 与展示路径必须来自当前连接目录。");
        var select = h("select.input", { style: "display:none", "data-sync-catalog-select": "1" },
          h("option", { value: "" }, "选择目录条目…")
        );
        select.addEventListener("change", function () {
          if (select.value === "") return;
          var picked = catalogItems[Number(select.value)];
          if (!picked) return;
          refInput.value = picked.resource_ref;
          displayInput.value = picked.display_path;
          msg.textContent = "已填入 " + picked.name + "（保存时服务端会再次校验）";
        });
        var button = h("button.btn.btn-sm", {
          type: "button",
          "data-sync-catalog": "1",
          onclick: function () {
            // 连接列表读取失败时回退到来源原有连接，避免"目录按钮点了没反应"。
            var connectionId = connectionSelect.value || draft.connection_id;
            if (!connectionId) { msg.textContent = "先选择连接。"; return; }
            button.disabled = true;
            msg.textContent = "正在读取 SDA 目录…";
            var path = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/connections/" + encodeURIComponent(connectionId) + "/catalog";
            var wanted = currentKind() === "sda_table" ? "table" : "file";
            MC.api.requestV3("GET", path).then(function (data) {
              catalogItems = ((data && data.items) || []).filter(function (item) { return item.kind === wanted; });
              util.clear(select);
              select.appendChild(h("option", { value: "" }, "选择目录条目…"));
              catalogItems.forEach(function (item, index) {
                select.appendChild(h("option", { value: String(index) },
                  (item.kind === "table" ? "表" : "文件") + " · " + item.name + " · " + item.display_path));
              });
              select.style.display = catalogItems.length ? "" : "none";
              button.disabled = false;
              msg.textContent = catalogItems.length
                ? "共 " + catalogItems.length + " 项；选中后自动填入 ID/路径。"
                : "当前目录没有可选项（采集器离线时不返回目录）。";
            }, function (err) {
              button.disabled = false;
              msg.textContent = (err && (err.message || err.code)) || "目录读取失败";
            });
          },
        }, "从 SDA 目录选择");
        catalogBlock.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" }, button, select, msg));
      }

      function renderTargetBlock() {
        util.clear(targetBlock);
        var kindField = h("select.input", { "data-sync-target-kind": "1" },
          TARGET_OPTIONS.filter(function (kind) { return !fileConfig || kind === "default_candidate" || kind === "file_archive" && fileDetail.file_id; }).map(function (kind) { return h("option", { value: kind }, TARGET_LABEL[kind]); })
        );
        kindField.value = draft.target_kind;
        kindField.addEventListener("change", function () {
          draft.target_kind = kindField.value;
          targetDirty = true;
          renderTargetBlock();
          markDirty();
        });
        targetBlock.appendChild(field("目标类型", kindField));
        if (draft.target_kind === "file_archive") {
          var fileSelect = h("select.input", { "data-sync-target-file": "1" },
            h("option", { value: "" }, fileItems.length ? "选择目标文件…" : "正在读取文件…"),
            fileItems.filter(function (item) { return !fileConfig || item.file_id === fileDetail.file_id; }).map(function (item) { return h("option", { value: item.file_id }, item.name); })
          );
          fileSelect.value = draft.target_file_id;
          fileSelect.disabled = !!fileConfig;
          if (fileConfig) draft.target_file_id = fileDetail.file_id;
          fileSelect.addEventListener("change", function () {
            draft.target_file_id = fileSelect.value;
            targetDirty = true;
            markDirty();
          });
          targetBlock.appendChild(field("目标文件", fileSelect));
          targetBlock.appendChild(h("p.xsmall.muted", "更新该文件并保留历史版本。"));
          if (!fileItems.length) {
            MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/profiles/" + encodeURIComponent(profileSelect.value) + "/file-selector", {})
              .then(function (data) {
                fileItems = (data && data.items) || [];
                var keep = fileConfig ? fileDetail.file_id : draft.target_file_id;
                util.clear(fileSelect);
                fileSelect.appendChild(h("option", { value: "" }, fileItems.length ? "选择目标文件…" : "当前机型没有文件资源"));
                fileItems.forEach(function (item) {
                  fileSelect.appendChild(h("option", { value: item.file_id }, item.name + (item.current_version ? " · v" + item.current_version : "")));
                });
                fileSelect.value = keep;
              }, function (err) {
                util.clear(fileSelect);
                fileSelect.appendChild(h("option", { value: "" }, "文件列表读取失败：" + errorText(err)));
              });
          }
        } else if (draft.target_kind === "default_candidate") {
          var configSelect = h("select.input", { "data-sync-target-config": "1" },
            h("option", { value: "" }, configItems.length ? "选择固定配置…" : "正在读取配置…")
          );
          configSelect.value = draft.target_config_id;
          configSelect.disabled = !!fileConfig;
          var releaseSelect = h("select.input", { "data-sync-target-release": "1" },
            h("option", { value: "" }, "选择软件版本…")
          );
          function fillReleases() {
            util.clear(releaseSelect);
            var rows = releaseItems.filter(function (row) { return row.config_id === configSelect.value; });
            if (!rows.length) {
              releaseSelect.appendChild(h("option", { value: "" }, "该配置没有版本"));
              return;
            }
            rows.forEach(function (row) {
              releaseSelect.appendChild(h("option", { value: row.release_id }, "版本 " + (row.version || row.release_id)));
            });
            releaseSelect.value = draft.target_release_id;
          }
          configSelect.addEventListener("change", function () {
            draft.target_config_id = configSelect.value;
            draft.target_release_id = "";
            targetDirty = true;
            fillReleases();
            markDirty();
          });
          releaseSelect.addEventListener("change", function () {
            draft.target_release_id = releaseSelect.value;
            targetDirty = true;
            markDirty();
          });
          targetBlock.appendChild(field("固定配置", configSelect));
          targetBlock.appendChild(field("软件版本", releaseSelect));
          targetBlock.appendChild(h("p.xsmall.muted", "获取参数变化供您核对，不会自动保存配置或下发机台。"));
          if (!configItems.length) {
            MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/profiles/" + encodeURIComponent(profileSelect.value) + "/configs", {})
              .then(function (data) {
                configItems = ((data && data.items) || []).filter(function (item) { return !fileConfig || item.config_id === fileConfig; });
                util.clear(configSelect);
                configSelect.appendChild(h("option", { value: "" }, configItems.length ? "选择固定配置…" : "当前机型没有固定配置"));
                configItems.forEach(function (item) {
                  configSelect.appendChild(h("option", { value: item.config_id }, (item.code || "") + " " + (item.name || item.config_id)));
                });
                configSelect.value = draft.target_config_id;
                var pending = configItems.map(function (item) {
                  return MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
                    "/profiles/" + encodeURIComponent(profileSelect.value) + "/configs/" +
                    encodeURIComponent(item.config_id) + "/releases", {})
                    .then(function (rows) {
                      ((rows && rows.items) || []).forEach(function (row) {
                        releaseItems.push({ config_id: item.config_id, release_id: row.release_id, version: row.version });
                      });
                    }, function () { /* 单个配置读取失败不影响其他 */ });
                });
                return Promise.all(pending);
              }).then(fillReleases, function (err) {
                util.clear(configSelect);
                configSelect.appendChild(h("option", { value: "" }, "配置读取失败：" + errorText(err)));
              });
          } else {
            fillReleases();
          }
        } else {
          var deviceInput = h("input.input", {
            value: draft.target_device_id,
            placeholder: "可信 device_id（不接受按文件名/IP 猜测）",
            "data-sync-target-device": "1",
          });
          deviceInput.addEventListener("input", function () {
            draft.target_device_id = deviceInput.value.trim();
            targetDirty = true;
            markDirty();
          });
          targetBlock.appendChild(field("device_id", deviceInput));
          targetBlock.appendChild(h("p.xsmall.muted", "没有可信 device_id 时不允许保存：不会伪造 rn，也不会把观测写进机型基线。"));
        }
      }

      function syncConditional() {
        draft.kind = currentKind();
        connectionSelect.disabled = !needsConnection();
        connectionField.hidden = !needsConnection();
        resourceField.querySelector("label").textContent = needsConnection() ? "资源标识（从目录选择）" : "下载地址";
        refInput.placeholder = needsConnection() ? "选择 SDA 资源后自动填入" : "粘贴 XML、JSON 或压缩包的直接下载地址";
        refInput.readOnly = isCatalogKind();
        displayInput.readOnly = isCatalogKind();
        if (needsConnection()) resourceFields.insertBefore(pathField, catalogBlock);
        else advancedFields.insertBefore(pathField, advancedFields.firstChild);
        if (needsConnection()) {
          connectionSelect.options[0].textContent = "选择已批准连接…";
        } else {
          connectionSelect.options[0].textContent = "（HTTP 来源无需连接）";
        }
        renderCatalogBlock();
        renderTargetBlock();
        markDirty();
      }

      function fillConnections() {
        MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/connections", {})
          .then(function (data) {
            connections = (data && data.items) || [];
            util.clear(connectionSelect);
            connectionSelect.appendChild(h("option", { value: "" },
              connections.length ? (needsConnection() ? "选择已批准连接…" : "（HTTP 来源无需连接）") : "还没有连接"));
            connections.forEach(function (conn) {
              connectionSelect.appendChild(h("option", { value: conn.connection_id },
                conn.name + (conn.enabled === false ? "（已停用）" : "")));
            });
            connectionSelect.value = draft.connection_id;
          }, function (err) {
            util.clear(connectionSelect);
            connectionSelect.appendChild(h("option", { value: "" }, "连接列表读取失败：" + errorText(err)));
          });
      }

      var baseline = "";
      function readDraft() {
        return JSON.stringify({
          name: nameInput.value.trim(),
          kind: kindSelect.value,
          connection_id: connectionSelect.value,
          resource_ref: refInput.value.trim(),
          display_path: displayInput.value.trim(),
          record_source: recordInput.value.trim(),
          keyword: keywordInput.value.trim(),
          enabled: !!enabledInput.checked,
          schedule_mode: modeSelect.value,
          interval_hours: intervalInput.value,
          target_kind: draft.target_kind,
          target_file_id: draft.target_file_id,
          target_config_id: draft.target_config_id,
          target_release_id: draft.target_release_id,
          target_device_id: draft.target_device_id,
        });
      }
      function isDirtyNow() {
        return baseline !== "" && readDraft() !== baseline;
      }
      function markDirty() {
        if (baseline === "") return;
        activeEditorDirty = isDirtyNow();
      }
      var enabledInput = h("input", { type: "checkbox", checked: draft.enabled });
      var modeSelect = h("select.input", {},
        h("option", { value: "inherit" }, "继承工作区计划"),
        h("option", { value: "custom" }, "自定义间隔"),
        h("option", { value: "manual" }, "仅手动")
      );
      var intervalInput = h("input.input", { type: "number", min: "1", max: "2160", value: draft.interval_hours, placeholder: "小时" });
      modeSelect.value = draft.schedule_mode;
      var intervalWrap = field("间隔（小时）", intervalInput);
      intervalWrap.style.display = draft.schedule_mode === "custom" ? "" : "none";
      modeSelect.addEventListener("change", function () {
        intervalWrap.style.display = modeSelect.value === "custom" ? "" : "none";
        markDirty();
      });
      [nameInput, refInput, displayInput, recordInput, keywordInput, intervalInput].forEach(function (input) {
        input.addEventListener("input", markDirty);
      });
      enabledInput.addEventListener("change", markDirty);
      kindSelect.addEventListener("change", function () {
        draft.kind = kindSelect.value;
        if (needsConnection() && !connectionSelect.value && connections.length === 1) {
          connectionSelect.value = connections[0].connection_id;
          draft.connection_id = connectionSelect.value;
        }
        syncConditional();
      });
      connectionSelect.addEventListener("change", function () {
        draft.connection_id = connectionSelect.value;
        renderCatalogBlock();
        markDirty();
      });
      profileSelect.addEventListener("change", function () {
        draft.profile_id = profileSelect.value;
        fileItems = [];
        configItems = [];
        releaseItems = [];
        renderTargetBlock();
        markDirty();
      });

      function clearInvalidControl() {
        if (!invalidControl) return;
        invalidControl.removeAttribute("aria-invalid");
        invalidControl.removeAttribute("aria-describedby");
        invalidControl.removeEventListener("input", onInvalidChange);
        invalidControl.removeEventListener("change", onInvalidChange);
        invalidControl = null;
      }
      function onInvalidChange() {
        clearInvalidControl();
        error.textContent = "";
        error.style.display = "none";
      }
      function showError(message, control) {
        clearInvalidControl();
        error.textContent = message;
        error.style.display = message ? "" : "none";
        if (message && control && !control.disabled) {
          var details = control.closest("details");
          if (details) details.open = true;
          invalidControl = control;
          control.setAttribute("aria-invalid", "true");
          control.setAttribute("aria-describedby", error.id);
          control.addEventListener("input", onInvalidChange);
          control.addEventListener("change", onInvalidChange);
          control.focus();
        }
      }

      function buildPayload() {
        var target = {
          kind: draft.target_kind,
          file_id: draft.target_kind === "file_archive" ? (draft.target_file_id || null) : null,
          configuration_id: draft.target_kind === "default_candidate" ? (draft.target_config_id || null) : null,
          release_id: draft.target_kind === "default_candidate" ? (draft.target_release_id || null) : null,
          device_id: draft.target_kind === "machine_observation" ? (draft.target_device_id || null) : null,
        };
        // 旧来源可能从未绑定目标：不改目标时不发 target 键，交给服务端保留原值。
        var wantsTarget = hadTarget || targetDirty || isCreate;
        var payload = {
          name: nameInput.value.trim(),
          kind: kindSelect.value,
          resource_ref: refInput.value.trim() || null,
          record_source: recordInput.value.trim() || null,
          keyword: keywordInput.value.trim(),
          display_path: displayInput.value.trim() || null,
          enabled: !!enabledInput.checked,
          schedule_mode: modeSelect.value,
          interval_hours: modeSelect.value === "custom" ? Number(intervalInput.value) : null,
          connection_id: needsConnection() ? (connectionSelect.value || draft.connection_id || null) : null,
        };
        if (wantsTarget) payload.target = target;
        if (isCreate) payload.profile_id = profileSelect.value;
        return payload;
      }

      function validate(payload) {
        if (!payload.name) return { message: "名称不能为空", control: nameInput };
        if (needsConnection() && !payload.connection_id) return { message: "SDA 来源必须选择已批准的连接", control: connectionSelect };
        if (isCatalogKind() && (!payload.resource_ref || !payload.display_path)) {
          return { message: "SDA 表/文件必须从目录选择资源 ID 与业务路径", control: !payload.resource_ref ? refInput : displayInput };
        }
        if (isCreate && payload.kind === "http_file" && !payload.resource_ref) {
          return { message: "HTTP 来源必须填写下载 URL", control: refInput };
        }
        if (payload.display_path && payload.display_path.charAt(0) !== "/") {
          return { message: "业务路径必须以 / 开始（目录 ID 与路径不一致会被拒绝）", control: displayInput };
        }
        if (payload.schedule_mode === "custom" && (!payload.interval_hours || payload.interval_hours < 1 || payload.interval_hours > 2160)) {
          return { message: "自定义间隔必须在 1–2160 小时", control: intervalInput };
        }
        if (!payload.target) return "";
        if (payload.target.kind === "file_archive" && !payload.target.file_id) return { message: "请选择目标文件（FileResource）", control: targetBlock.querySelector("[data-sync-target-file]") };
        if (payload.target.kind === "default_candidate" && (!payload.target.configuration_id || !payload.target.release_id)) {
          return { message: "请选择固定配置与软件版本", control: targetBlock.querySelector(payload.target.configuration_id ? "[data-sync-target-release]" : "[data-sync-target-config]") };
        }
        if (payload.target.kind === "machine_observation" && !payload.target.device_id) {
          return { message: "机台观测必须填写可信 device_id（不允许按文件名/IP 猜测）", control: targetBlock.querySelector("[data-sync-target-device]") };
        }
        return "";
      }

      var reloadButton = h("button.btn.btn-sm", {
        type: "button",
        "data-sync-reload": "1",
        style: "display:none",
      }, "重新载入最新版本");
      reloadButton.addEventListener("click", function () {
        if (isCreate) return;
        reloadButton.disabled = true;
        hint.textContent = "正在查找服务端最新版本…";
        var seenCursors = {};
        var listingRevision = null;
        function findSource(cursor) {
          if (seenCursors[cursor]) return Promise.reject(new Error("来源列表分页异常，请重试"));
          seenCursors[cursor] = true;
          return MC.api.requestV3("GET", sourcesPath(), { query: {
            profile_id: source.profile_id || undefined, status: "all", limit: 200, cursor: cursor,
          } }).then(function (page) {
            if (page.query_revision == null) throw new Error("来源列表未提供版本号，无法安全核对");
            if (listingRevision === null) listingRevision = page.query_revision;
            else if (listingRevision !== page.query_revision) throw new Error("来源列表在读取时发生变化，请重试");
            var fresh = ((page && page.items) || []).filter(function (row) { return row.source_id === source.source_id; })[0];
            if (fresh) return fresh;
            if (page.next_cursor != null && String(page.next_cursor) !== "") return findSource(String(page.next_cursor));
            throw new Error("来源已不存在或不在当前机型，草稿仍保留");
          });
        }
        findSource("0").then(function (fresh) {
          reloadButton.disabled = false;
          if (fresh.deleted) { showError("该来源已删除；草稿仍保留，不能覆盖已删除的来源。"); return; }
          var payload = buildPayload();
          var labels = { name: "名称", kind: "类型", resource_ref: "资源引用", record_source: "修改记录链接", keyword: "关键词", display_path: "业务路径", enabled: "启用状态", schedule_mode: "更新计划", interval_hours: "间隔", connection_id: "连接", target: "接收目标" };
          var changes = Object.keys(labels).filter(function (key) {
            return Object.prototype.hasOwnProperty.call(payload, key) && JSON.stringify(payload[key] == null ? null : payload[key]) !== JSON.stringify(fresh[key] == null ? null : fresh[key]);
          });
          var details = h("div.sync-conflict-comparison", h("p.small.muted", "以下是服务端最新值与当前输入；确认后再次保存将按当前输入覆盖这些字段。"));
          changes.forEach(function (key) {
            function shown(value) { return value == null ? "未设" : (typeof value === "object" ? JSON.stringify(value) : String(value)); }
            details.appendChild(h("div.sync-conflict-field", h("strong.small", labels[key]),
              h("div.small", "服务端：", h("code", shown(fresh[key]))),
              h("div.small", "当前输入：", h("code", shown(payload[key])))));
          });
          if (!changes.length) details.appendChild(h("p.small", "当前输入与最新版本一致。"));
          return MC.confirm({ title: "核对最新来源", message: "请确认本地输入与服务端最新版本的差异。", detail: details,
            confirmText: "保留当前输入", cancelText: "取消", danger: true }).then(function (ok) {
            if (!ok) { hint.textContent = "尚未采用最新版本；当前草稿保持不变。"; return; }
            source.revision = fresh.revision;
            hint.textContent = "已核对 revision " + fresh.revision + "；再次保存会按当前输入覆盖所示字段。";
            showError("");
          });
        }, function (err) {
          reloadButton.disabled = false;
          hint.textContent = "未采用最新版本；当前草稿保持不变。";
          showError("重新载入失败：" + errorText(err));
        });
      });

      var casNote = isCreate
        ? h("p.xsmall.muted", "保存来源后，可点击“更新”获取内容。")
        : h("p.xsmall.muted", "保存设置后，下次更新使用新设置。发生冲突时会保留你的输入。");

      var requestDismiss = function () {};
      var modal = viewModal({
        title: isCreate ? "新建更新源" : "编辑更新源",
        subtitle: isCreate
          ? "工作区 " + workspaceId()
          : (KIND_LABEL[source.kind] || source.kind) + " · " + source.source_id,
        size: "wide",
        dismissible: false,
        onDismiss: function () { requestDismiss(); },
        body: h("div.col", { style: "gap:12px" },
          error,
          hint,
          h("div.row", { style: "gap:12px;flex-wrap:wrap" },
            field("机型", profileSelect),
            field("类型", kindSelect)
          ),
          connectionField,
          field("名称", nameInput),
          resourceFields,
          advanced,
          h("div.card", h("div.card-b", h("div.col", { style: "gap:8px" },
            h("strong.small", "写入目标"),
            targetBlock
          ))),
          h("div.row", { style: "gap:18px;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用")),
            field("计划", modeSelect)
          ),
          intervalWrap,
          casNote,
          reloadButton
        ),
        footer: function (close) {
          function tryClose() {
            if (!isDirtyNow()) { activeEditorDirty = false; close(); return; }
            MC.confirm({
              title: "保留这次修改？",
              message: "抽屉里有未保存的内容。保留会留在编辑框里；放弃会丢掉这些修改。",
              confirmText: "放弃修改",
              cancelText: "保留",
              danger: true,
            }).then(function (ok) {
              if (ok) { activeEditorDirty = false; close(); }
            });
          }
          requestDismiss = tryClose;
          return [
            h("button.btn", { type: "button", "data-sync-cancel": "1", onclick: tryClose }, "取消"),
            h("button.btn", { type: "button", "data-sync-test-input": "1", onclick: function (event) {
              var payload = buildPayload();
              var problem = validate(payload);
              if (problem) { showError(problem.message, problem.control); return; }
              payload.profile_id = profileSelect.value;
              if (!Object.prototype.hasOwnProperty.call(payload, "target")) payload.target = source.target || null;
              var testButton = event.currentTarget;
              testButton.disabled = true; testButton.textContent = "测试中…";
              showError(""); hint.textContent = "正在读取当前输入指向的内容；不会保存或更新业务文件…";
              MC.api.requestV3("POST", sourcesPath().replace(/\/sources$/, "/source-tests"), { body: payload }).then(function (result) {
                testButton.disabled = false; testButton.textContent = "测试来源";
                hint.textContent = result.message || "来源测试未返回说明";
                if (result.status !== "succeeded") showError(result.message || "来源测试未通过");
              }, function (err) {
                testButton.disabled = false; testButton.textContent = "测试来源";
                hint.textContent = "输入已保留，可修改后重试。"; showError("来源测试失败：" + errorText(err));
              });
            } }, "测试来源"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-sync-save": "1",
              onclick: function () {
                var payload = buildPayload();
                var problem = validate(payload);
                if (problem) { showError(problem.message, problem.control); return; }
                showError("");
                reloadButton.style.display = "none";
                var request = isCreate
                  ? MC.api.requestV3("POST", sourcesPath(), { body: payload })
                  : MC.api.requestV3("PATCH", sourcePath(source), {
                    body: payload,
                    headers: { "If-Match": String(source.revision) },
                  });
                request.then(function (data) {
                  activeEditorDirty = false;
                  close();
                  MC.toast.success(isCreate ? "更新源已创建" : "更新源已保存");
                  if (!isCreate && data && data.source_id) {
                    // 局部刷新：保留当前搜索/分页/排序，只替换这一行。
                    var items = state.sources.items;
                    for (var i = 0; i < items.length; i += 1) {
                      if (items[i].source_id === data.source_id) {
                        items[i] = Object.assign({}, items[i], data);
                        if (state.tab === "sources") renderBody();
                        return;
                      }
                    }
                  }
                  loadSources(true);
                }, function (err) {
                  if (err && err.code === "REVISION_CONFLICT") {
                    showError("该来源已在其他客户端更新（revision 冲突）；草稿已保留，可先重新载入最新版本再保存。");
                    if (!isCreate) reloadButton.style.display = "";
                    return;
                  }
                  showError(errorText(err));
                  // 网络类失败：保留草稿，允许直接重试保存（同一按钮）。
                  if (err && err.code === "NETWORK") hint.textContent = "网络不可用；恢复后可直接再次点击保存重试。";
                });
              },
            }, isCreate ? "创建" : "保存"),
          ];
        },
        onClose: function () { activeEditorDirty = false; },
      });
      modal.el.classList.add("sync-source-dialog");
      baseline = readDraft();
      activeEditorDirty = false;
      syncConditional();
      fillConnections();
      return modal;
    }

    function runSource(source, button) {
      if (button) button.disabled = true;
      MC.api.requestV3("POST", sourcePath(source) + "/run", { body: {} }).then(function (data) {
        MC.toast.success("已提交单项更新");
        if (data && data.run && data.run.run_id) {
          MC.bus.emit("sync-run-started", data.run);
        }
        loadRecentRun();
        if (button) button.disabled = false;
      }, function (err) {
        MC.toast.error("单项更新提交失败", errorText(err));
        if (button) button.disabled = false;
      });
    }

    function dropSourceRow(sourceId) {
      var S = state.sources;
      S.items = S.items.filter(function (row) { return row.source_id !== sourceId; });
      S.total = Math.max(0, S.total - 1);
      delete S.selected[sourceId];
      if (state.tab === "sources") renderBody();
    }

    function deleteSource(source) {
      var origin = captureOrigin();
      MC.confirm({
        title: "删除更新源「" + source.name + "」？",
        message: "此来源将停止参与更新。已保存的文件、历史版本、候选和运行记录都会保留。",
        confirmText: "删除来源",
        danger: true,
      }).then(function (ok) {
        if (!ok) return;
        if (!origin.current()) { MC.toast.warning("工作区已切换", "来源未删除，请返回原工作区重新确认。"); return; }
        var path = "/api/v3/workspaces/" + encodeURIComponent(origin.id) + "/sync/sources/" + encodeURIComponent(source.source_id);
        MC.api.requestV3("DELETE", path, {
          headers: { "If-Match": String(source.revision) },
        }).then(function () {
          if (!origin.current()) { MC.toast.warning("工作区 " + origin.id + " 的删除已提交", "请返回原工作区核对结果。"); return; }
          MC.toast.success("更新源已删除");
          // 删除后保持当前搜索/筛选/分页，只局部移除该行。
          dropSourceRow(source.source_id);
        }, function (err) {
          if (!origin.current() || (err && err.code === "STALE_CONTEXT")) { MC.toast.warning("工作区 " + origin.id + " 的删除结果待核对", "请返回原工作区确认来源状态。"); return; }
          MC.toast.error("删除失败", errorText(err));
        });
      });
    }

    function testSource(source, button) {
      var S = state.sources;
      if (S.testing[source.source_id]) return;
      S.testing[source.source_id] = true;
      if (button) button.disabled = true;
      function finish(text, ok) {
        S.testing[source.source_id] = false;
        S.testResults[source.source_id] = { text: text, ok: ok };
        if (button) button.disabled = false;
        if (state.tab === "sources") renderBody();
      }
      var payload = {};
      ["profile_id", "name", "kind", "connection_id", "resource_ref", "display_path", "target", "record_source", "keyword", "enabled", "schedule_mode", "interval_hours"].forEach(function (key) { payload[key] = source[key]; });
      MC.api.requestV3("POST", sourcesPath().replace(/\/sources$/, "/source-tests"), {
        body: payload,
      }).then(function (result) {
        finish(result.message || "来源测试未返回说明", result.status === "succeeded");
      }, function (err) {
        finish("测试失败：" + errorText(err), false);
      });
    }

    function selectedSourceRows() {
      var S = state.sources;
      return S.items.filter(function (row) { return S.selected[row.source_id]; });
    }

    function editSelectedSchedule() {
      var rows = selectedSourceRows().filter(function (row) { return !row.deleted; });
      if (!rows.length) return;
      var origin = captureOrigin();
      var error = h("div.form-error", { role: "alert", hidden: true });
      var mode = h("select.input", { "aria-label": "所选来源更新频率" },
        h("option", { value: "inherit" }, "继承工作区计划"), h("option", { value: "custom" }, "独立频率"), h("option", { value: "manual" }, "仅手动"));
      var hours = h("input.input", { type: "number", min: "1", max: "2160", value: "6", disabled: true, "aria-label": "间隔小时" });
      var dirty = false, saving = false;
      function changed() { dirty = true; activeEditorDirty = true; hours.disabled = mode.value !== "custom"; }
      mode.addEventListener("change", changed); hours.addEventListener("input", changed);
      function dismiss(close) {
        if (saving) return;
        if (!dirty) { close(); return; }
        MC.confirm({ title: "放弃未保存的计划？", message: "尚未保存任何来源。", confirmText: "放弃修改", cancelText: "继续编辑" }).then(function (ok) { if (ok) close(); });
      }
      viewModal({ title: "调整 " + rows.length + " 个来源的更新计划", size: "wide", onDismiss: dismiss,
        onClose: function () { activeEditorDirty = false; },
        body: h("div.col", { style: "gap:12px" }, h("p.small.muted", "只修改所选来源的频率。任一来源发生变化时，整批均不保存。"),
          field("更新频率", mode), field("间隔（小时）", hours), h("div.sync-card-scroll", rows.map(function (row) { return h("div.small", row.name); })), error),
        footer: function (close) { return [h("button.btn", { type: "button", onclick: function () { dismiss(close); } }, "取消"),
          h("button.btn.btn-primary", { type: "button", "data-sync-batch-plan-save": "1", onclick: function (event) {
            if (saving || !origin.current()) return;
            if (mode.value === "custom" && (!Number.isInteger(Number(hours.value)) || Number(hours.value) < 1 || Number(hours.value) > 2160)) {
              error.textContent = "请输入 1–2160 之间的整数小时。"; error.hidden = false; hours.focus(); return;
            }
            var button = event.currentTarget; saving = true; button.disabled = true; error.hidden = true;
            MC.api.requestV3("POST", "/api/v3/workspaces/" + encodeURIComponent(origin.id) + "/sync/source-policy-batches", { body: {
              updates: rows.map(function (row) { return { source_id: row.source_id, expected_revision: row.revision, schedule_mode: mode.value, interval_hours: mode.value === "custom" ? Number(hours.value) : null }; })
            } }).then(function () {
              saving = false; close();
              if (origin.current()) { MC.toast.success("所选来源计划已保存"); loadSources(true); }
            }, function (err) {
              saving = false; button.disabled = false; error.hidden = false;
              error.textContent = "整批未保存：" + errorText(err) + (err.code === "REVISION_CONFLICT" ? "。当前输入保留，请关闭后刷新来源并重新选择。" : "");
            });
          } }, "保存计划")]; }
      });
    }

    function previewSources(rows) {
      var all = rows === null;
      rows = (rows || []).filter(function (row) { return !row.deleted; });
      if (!all && !rows.length) return;
      var origin = captureOrigin(), preview = null;
      var content = h("div.col", { style: "gap:10px" });
      var error = h("div.form-error", { role: "alert", hidden: true });
      var start;
      function load() {
        preview = null; start.disabled = true; error.hidden = true; util.clear(content); content.appendChild(h("p.muted", "正在核对所选来源及目标…"));
        MC.api.requestV3("POST", previewsPath(), { body: all ? filePreviewScope() : { scope: "selected_sources", source_ids: rows.map(function (row) { return row.source_id; }) } }).then(function (data) {
          if (!origin.current()) return;
          preview = data; util.clear(content);
          content.appendChild(h("p.small", "将更新 " + data.total + " 个来源；不发布配置。"));
          (data.items || []).forEach(function (item) { content.appendChild(h("div.small", item.name || item.source_id, " · ", item.profile_id)); });
          (data.excluded || []).forEach(function (item) { content.appendChild(h("div.small", "未纳入：", item.name || item.source_id, " · ", EXCLUDED_REASON[item.reason_code] || item.reason_code)); });
          start.disabled = !data.total;
        }, function (err) { util.clear(content); error.textContent = errorText(err); error.hidden = false; });
      }
      viewModal({ title: all ? fileConfig ? "更新当前文件" : "工作区全量更新" : "更新所选来源", subtitle: all ? fileConfig ? "仅包含当前文件的已启用来源，不受列表筛选影响" : "包含工作区内所有已启用来源，不受列表筛选影响" : "仅处理当前勾选的来源", size: "wide", body: h("div.col", content, error), footer: function (close) {
        start = h("button.btn.btn-primary", { type: "button", disabled: true, "data-sync-selected-start": "1", onclick: function () {
          if (!preview || !origin.current()) return;
          start.disabled = true; error.hidden = true;
          MC.api.requestV3("POST", runsPath(), { body: { preview_id: preview.preview_id } }).then(function () {
            close(); if (origin.current()) { MC.toast.success("所选来源已入队"); state.sources.selected = {}; loadSources(true); }
          }, function (err) { error.textContent = errorText(err); error.hidden = false; start.disabled = err.code === "REVISION_CONFLICT"; });
        } }, "开始更新");
        return [h("button.btn", { type: "button", onclick: close }, "关闭"), h("button.btn", { type: "button", onclick: load }, "重新预览"), start];
      } }); load();
    }

    function batchSources(action) {
      var rows = selectedSourceRows().filter(function (row) { return !row.deleted; });
      var S = state.sources;
      if (!rows.length || S.busy) return;
      var origin = captureOrigin();
      var label = action === "enable" ? "启用" : (action === "disable" ? "停用" : "删除");
      MC.confirm({
        title: "批量" + label + " " + rows.length + " 个来源？",
        message: action === "delete"
          ? "所选来源将停止参与更新，已保存的文件和历史版本都会保留。"
          : "逐个按各自 revision 做 CAS，失败项会单独列出，不影响其他项。",
        confirmText: "批量" + label,
        danger: action === "delete",
      }).then(function (ok) {
        if (!ok) return;
        if (!origin.current()) { MC.toast.warning("工作区已切换", "未开始批量操作，请返回原工作区重新选择。"); return; }
        S.busy = action;
        if (state.tab === "sources") renderBody();
        var failures = [];
        var stopped = false;
        var seq = Promise.resolve();
        rows.forEach(function (row) {
          seq = seq.then(function () {
            if (stopped || !origin.current()) { stopped = true; return; }
            var path = "/api/v3/workspaces/" + encodeURIComponent(origin.id) + "/sync/sources/" + encodeURIComponent(row.source_id);
            if (action === "delete") {
              return MC.api.requestV3("DELETE", path, {
                headers: { "If-Match": String(row.revision) },
              }).then(function () { if (!origin.current()) { stopped = true; return; } delete S.selected[row.source_id]; }, function (err) {
                if (!origin.current() || (err && err.code === "STALE_CONTEXT")) { stopped = true; return; }
                failures.push(row.name + "：" + errorText(err));
              });
            }
            return MC.api.requestV3("PATCH", path, {
              headers: { "If-Match": String(row.revision) },
              body: { enabled: action === "enable" },
            }).then(function (data) {
              if (!origin.current()) { stopped = true; return; }
              row.enabled = data.enabled;
              row.revision = data.revision;
              delete S.selected[row.source_id];
            }, function (err) {
              if (!origin.current() || (err && err.code === "STALE_CONTEXT")) { stopped = true; return; }
              failures.push(row.name + "：" + errorText(err));
            });
          });
        });
        seq.then(function () {
          S.busy = "";
          if (stopped || !origin.current()) {
            MC.toast.warning("工作区 " + origin.id + " 的批量操作已停止", "尚未提交的来源不会继续处理；请返回原工作区核对已提交项。");
            return;
          }
          if (failures.length) {
            MC.toast.error("批量" + label + "部分失败（" + failures.length + "/" + rows.length + "）", failures.join("；"));
          } else {
            MC.toast.success("已批量" + label + " " + rows.length + " 个来源");
          }
          loadSources(true);
        });
      });
    }

    function rerenderPlan() {
      if (state.sheet === "plan") paintSheet();
    }

    function planDirty() {
      var P = state.plan;
      if (!P.policy || !P.draft) return false;
      function snapshot(draft) {
        var overrides = draft.overrides || {};
        return JSON.stringify({
          enabled: !!draft.enabled,
          hours: String(draft.default_interval_hours),
          overrides: Object.keys(overrides).sort().map(function (pid) {
            var item = overrides[pid];
            return [pid, item.enabled == null ? null : item.enabled,
              item.interval_hours == null ? null : String(item.interval_hours)];
          }),
        });
      }
      return snapshot(P.draft) !== snapshot(draftFromPolicy(P.policy));
    }

    function refreshPlan() {
      if (!planDirty()) return loadPlan();
      var P = state.plan;
      var origin = captureOrigin();
      return MC.confirm({
        title: "刷新更新计划？",
        message: "刷新将放弃当前尚未保存的计划修改。",
        confirmText: "放弃修改并刷新",
        cancelText: "继续编辑",
        danger: true,
      }).then(function (ok) { if (ok && state.plan === P && origin.current()) return loadPlan(); });
    }

    function loadPlan() {
      var P = state.plan;
      if (P.loading) return Promise.resolve();
      var origin = captureOrigin();
      var wid = origin.id;
      P.loading = true;
      P.loadedFor = wid;
      P.error = "";
      rerenderPlan();
      return Promise.all([
        MC.api.requestV3("GET", policyPath()),
        MC.api.requestV3("GET", sourcesPath(), { query: { status: "all", limit: 200 } }),
      ]).then(function (result) {
        if (state.plan !== P || !origin.current() || P.loadedFor !== wid) return;
        P.policy = result[0] || {};
        P.draft = draftFromPolicy(P.policy);
        P.invalidField = "";
        P.conflict = false;
        P.conflictComparison = null;
        P.sources = (result[1] && result[1].items) || [];
        P.loading = false;
        if (typeof paintChrome === "function") paintChrome();
        P.error = "";
        rerenderPlan();
      }, function (err) {
        if (state.plan !== P || !origin.current() || P.loadedFor !== wid) return;
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
        return { error: "默认间隔必须在 1–2160 小时", field: "global" };
      }
      var overrides = {};
      var bad = "";
      var badProfile = "";
      Object.keys(draft.overrides).forEach(function (pid) {
        var item = draft.overrides[pid] || {};
        var clean = {};
        if (item.enabled === true || item.enabled === false) clean.enabled = item.enabled;
        if (item.interval_hours !== undefined && item.interval_hours !== null && String(item.interval_hours).trim() !== "") {
          var value = Number(item.interval_hours);
          if (!isFinite(value) || value < 1 || value > 2160) {
            bad = "机型覆盖间隔必须在 1–2160 小时";
            badProfile = pid;
          } else {
            clean.interval_hours = value;
          }
        }
        if (Object.keys(clean).length) overrides[pid] = clean;
      });
      if (bad) return { error: bad, field: "profile:" + badProfile };
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
      if (!canSavePlan() || P.saving || P.conflict || !P.draft) return;
      var built = buildPolicyPayload();
      if (built.error) {
        P.error = built.error;
        P.invalidField = built.field;
        rerenderPlan();
        var inputs = sheetHost ? sheetHost.querySelectorAll("[data-plan-interval], [data-plan-override-interval]") : [];
        Array.prototype.some.call(inputs, function (input) {
          if ((built.field === "global" && input.hasAttribute("data-plan-interval")) || "profile:" + input.getAttribute("data-plan-override-interval") === built.field) { input.focus(); return true; }
          return false;
        });
        return;
      }
      P.invalidField = "";
      var origin = captureOrigin();
      var revision = P.policy && P.policy.revision != null ? P.policy.revision : 0;
      P.saving = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("PUT", policyPath(), {
        body: built.body,
        headers: { "If-Match": String(revision) },
      }).then(function (policy) {
        if (state.plan !== P || !origin.current()) { MC.toast.warning("工作区 " + origin.id + " 的计划保存结果待核对", "请返回原工作区查看计划状态。"); return; }
        P.policy = policy || P.policy;
        P.draft = draftFromPolicy(P.policy);
        P.saving = false;
        P.error = "";
        P.conflict = false;
        P.conflictComparison = null;
        MC.toast.success("更新计划已保存");
        rerenderPlan();
      }, function (err) {
        if (state.plan !== P || !origin.current() || (err && err.code === "STALE_CONTEXT")) { MC.toast.warning("工作区 " + origin.id + " 的计划保存结果待核对", "请返回原工作区查看计划状态。"); return; }
        P.saving = false;
        // 409：不覆盖 P.draft，输入框仍显示用户当前草稿。
        P.conflict = !!(err && err.code === "REVISION_CONFLICT");
        P.conflictComparison = null;
        P.error = P.conflict ? "计划已在其他客户端更新；当前草稿已保留，请查看最新计划后决定如何继续。" : planErrorText(err);
        rerenderPlan();
      });
    }

    function loadPlanConflict() {
      var P = state.plan;
      if (!P.conflict || P.conflictLoading) return;
      var origin = captureOrigin();
      var wid = origin.id;
      P.conflictLoading = true;
      P.conflictComparison = null;
      rerenderPlan();
      MC.api.requestV3("GET", policyPath()).then(function (latest) {
        if (state.plan !== P || !origin.current() || P.loadedFor !== wid) return;
        P.conflictLoading = false;
        if (!latest || !Number.isSafeInteger(latest.revision) || latest.revision < 0) {
          P.error = "最新计划缺少有效修订号，无法恢复；草稿仍保留。";
        } else {
          P.conflictComparison = { policy: latest, draft: JSON.stringify(P.draft) };
          P.error = "请对照两份计划。确认后草稿仍未保存，下一次保存会替换服务端计划。";
        }
        rerenderPlan();
      }, function (err) {
        if (state.plan !== P || !origin.current() || P.loadedFor !== wid) return;
        P.conflictLoading = false;
        P.error = "读取最新计划失败：" + errorText(err) + "；草稿仍保留。";
        rerenderPlan();
      });
    }

    function adoptPlanRevision() {
      var P = state.plan;
      var comparison = P.conflictComparison;
      if (!P.conflict || !comparison) return;
      if (comparison.draft !== JSON.stringify(P.draft)) {
        P.conflictComparison = null;
        P.error = "草稿已变化，请重新查看最新计划并确认对比。";
      } else {
        P.policy = comparison.policy;
        P.conflict = false;
        P.conflictComparison = null;
        P.error = "已采用显示的服务端修订，草稿仍未保存；请检查后保存。";
      }
      rerenderPlan();
    }

    function requestPlanPreview() {
      var P = state.plan;
      if (!canSavePlan() || P.previewing) return;
      // 失败筛选只影响下方列表显示；预览请求始终冻结 workspace_all 全量 read_set。
      var origin = captureOrigin();
      var wid = origin.id;
      P.previewing = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", previewsPath(), { body: { scope: "workspace_all" } }).then(function (preview) {
        if (state.plan !== P || !origin.current()) return;
        P.previewing = false;
        if (!preview || P.loadedFor !== wid) return;
        P.preview = preview;
        P.previewWorkspaceId = wid;
        P.error = "";
        rerenderPlan();
      }, function (err) {
        if (state.plan !== P || !origin.current()) return;
        P.previewing = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function validatePlanPreview() {
      var P = state.plan;
      if (!canSavePlan() || !P.preview || P.validating) return;
      var origin = captureOrigin();
      var wid = P.previewWorkspaceId || workspaceId();
      if (wid !== origin.id) return;
      var path = "/api/v3/workspaces/" + encodeURIComponent(wid)
        + "/sync/previews/" + encodeURIComponent(P.preview.preview_id) + "/validate";
      P.validating = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", path, { body: {} }).then(function (fresh) {
        if (state.plan !== P || !origin.current()) return;
        P.validating = false;
        if (fresh) {
          P.preview = fresh;
          P.previewWorkspaceId = wid;
        }
        MC.toast.success("预览仍然有效");
        rerenderPlan();
      }, function (err) {
        if (state.plan !== P || !origin.current()) return;
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
      var origin = captureOrigin();
      var wid = origin.id;
      var body = { scope: "workspace_all" };
      if (P.preview && P.preview.preview_id && P.previewWorkspaceId === wid) {
        body.preview_id = P.preview.preview_id;
      }
      P.running = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", runsPath(), { body: body }).then(function (data) {
        if (state.plan !== P || !origin.current()) { MC.toast.warning("工作区 " + wid + " 的全量更新结果待核对", "请返回原工作区查看运行记录。"); return; }
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
        if (state.plan !== P || !origin.current() || (err && err.code === "STALE_CONTEXT")) { MC.toast.warning("工作区 " + wid + " 的全量更新结果待核对", "请返回原工作区查看运行记录。"); return; }
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
      function clearFieldError(control, key) {
        if (P.invalidField !== key) return;
        P.invalidField = "";
        P.error = "";
        control.removeAttribute("aria-invalid");
        control.removeAttribute("aria-describedby");
        var errorNode = sheetHost && sheetHost.querySelector("#" + planErrorId);
        if (errorNode) errorNode.remove();
      }

      if (P.error) body.appendChild(h("div.form-error", { id: planErrorId, role: "alert" }, P.error));
      if (P.conflict) {
        body.appendChild(h("button.btn.btn-sm", { type: "button", "data-plan-conflict-load": "1", disabled: P.conflictLoading, onclick: loadPlanConflict }, P.conflictLoading ? "读取中…" : "查看最新计划"));
        if (P.conflictComparison) {
          var comparison = P.conflictComparison;
          var displayDraft = function (value) {
            var overrides = value.overrides || {};
            var lines = ["自动更新：" + (value.enabled ? "启用" : "暂停"), "默认间隔：" + value.default_interval_hours + " 小时"];
            var ids = Object.keys(overrides).sort();
            if (!ids.length) lines.push("机型覆盖：无");
            ids.forEach(function (id) {
              var item = overrides[id];
              lines.push("机型 " + id + "：" + (item.enabled == null ? "继承状态" : (item.enabled ? "启用" : "暂停")) + "，" + (item.interval_hours == null ? "继承间隔" : item.interval_hours + " 小时"));
            });
            return lines.join("\n");
          };
          body.appendChild(h("div.sync-conflict-comparison", { "data-plan-conflict-compare": "1" },
            h("div.sync-conflict-field", h("strong", "服务端最新计划 · 修订 " + comparison.policy.revision), h("pre", displayDraft(draftFromPolicy(comparison.policy)))),
            h("div.sync-conflict-field", h("strong", "当前草稿"), h("pre", displayDraft(JSON.parse(comparison.draft)))),
            h("button.btn.btn-sm", { type: "button", "data-plan-conflict-adopt": "1", onclick: adoptPlanRevision }, "保留草稿并使用此修订")
          ));
        }
      }
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
        disabled: !save || P.saving,
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
        disabled: !save || P.saving,
        "data-plan-interval": "1",
        "aria-label": "默认间隔（小时）",
        "aria-invalid": P.invalidField === "global" ? "true" : null,
        "aria-describedby": P.invalidField === "global" ? planErrorId : null,
        style: "max-width:120px",
      });
      intervalInput.addEventListener("input", function () {
        draft.default_interval_hours = intervalInput.value;
        clearFieldError(intervalInput, "global");
      });

      var overrideRows = profiles.map(function (profile) {
        var item = draft.overrides[profile.id] || {};
        var select = h("select.input", {
          disabled: !save || P.saving,
          "data-plan-override-enabled": profile.id,
          "aria-label": profile.name + " 自动状态覆盖",
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
          disabled: !save || P.saving,
          "data-plan-override-interval": profile.id,
          "aria-label": profile.name + " 间隔覆盖（小时）",
          "aria-invalid": P.invalidField === "profile:" + profile.id ? "true" : null,
          "aria-describedby": P.invalidField === "profile:" + profile.id ? planErrorId : null,
          style: "max-width:120px",
        });
        overrideInterval.addEventListener("input", function () {
          setOverride(profile.id, "interval_hours", overrideInterval.value.trim());
          clearFieldError(overrideInterval, "profile:" + profile.id);
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
          disabled: P.saving || P.conflict,
          onclick: savePlan,
        }, P.saving ? "保存中…" : "保存计划"));
      }
      planActions.appendChild(h("button.btn.btn-sm", {
        type: "button",
        "data-plan-refresh": "1",
        onclick: refreshPlan,
      }, "刷新"));
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "自动更新计划"), h("span.muted.small", "工作区 " + workspaceId())),
        h("div.card-b",
          h("div.row", { style: "gap:18px;flex-wrap:wrap;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" },
              enabledInput, h("span", "启用自动更新")),
            h("label.row.sync-plan-interval", { style: "gap:6px;align-items:center" },
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
        previewActions.appendChild(h("button.btn", {
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
          + " 个 · 范围指纹 " + fingerprint
        ));
        previewBody.appendChild(h("p.xsmall.muted",
          "失败筛选只影响本页显示；冻结的更新范围始终包含所有机型下已启用的来源。"
        ));
        var rows = (pv.items || []).slice();
        if (P.failuresOnly) {
          rows = rows.filter(function (item) { return !!P.failedSourceIds[item.source_id]; });
        }
        if (!rows.length) {
          previewBody.appendChild(h("p.muted.small", P.failuresOnly
            ? "当前没有记录到失败来源；全量范围仍包含上面的 " + (pv.total || 0) + " 个来源。"
            : "预览中没有可用来源。"));
        } else {
          rows.forEach(function (item) {
            previewBody.appendChild(h("div.row", {
              "data-plan-preview-source": item.source_id,
              style: "gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--c-border)",
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
        h("div.card-h", h("strong", "全量预览"), h("span.muted.small", "工作区所有机型")),
        previewBody
      ));

      var runRows = RUN_LOG.slice().reverse();
      var runBody = h("div.card-b");
      var runActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      if (run) {
        runActions.appendChild(h("button.btn", {
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
            style: "gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--c-border)",
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
        h("div.card-h", h("strong", "手动全量更新"), h("span.muted.small", "按机型分组提交任务")),
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
      FORBIDDEN: "需要管理员身份",
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

    /** 凭据可用性与自动登录开关分别展示。 */
    function autoReloginText(conn) {
      var cred = (conn && conn.credential) || {};
      if (cred.state === "valid") {
        return "登录失效后，可使用已保存的凭据自动重新登录；此设置与保活开关相互独立。";
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
      if (state.sheet !== "connections") return;
      paintSheet();
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
          if (typeof paintChrome === "function") paintChrome();
        });
      }, function (err) {
        if (!current()) return;
        C.loading = false;
        C.error = errorText(err);
        refreshConnectionsView();
      });
    }

    function saveHeartbeat(conn, enabledInput, intervalInput, showError, autoInput) {
      var cid = conn.connection_id;
      var draft = connectionDrafts()[cid];
      var hb = draft || state.connections.heartbeats[cid] || { revision: 0 };
      var origin = captureOrigin();
      var interval = Number(intervalInput.value);
      if (!isFinite(interval) || interval < 1 || interval > 60) {
        showError("保活间隔必须在 1–60 分钟", intervalInput);
        return;
      }

      showError("");
      MC.api.requestV3("PUT", heartbeatPath(cid), {
        body: {
          interval_minutes: interval,
          enabled: !!enabledInput.checked,
          auto_relogin: !!(autoInput && autoInput.checked),
        },
        headers: { "If-Match": String(hb.revision == null ? 0 : hb.revision) },
      }).then(function (saved) {
        if (!origin.current()) return;
        state.connections.heartbeats[cid] = saved;
        if (connectionDrafts()[cid] === draft) delete connectionDrafts()[cid];

        MC.toast.success("保活设置已保存");
        refreshConnectionsView();
      }, function (err) {
        showError(errorText(err));
      });
    }

    function runHeartbeat(conn, showError) {
      var cid = conn.connection_id;

      showError("");
      MC.api.requestV3("POST", heartbeatPath(cid) + "/run", {
        body: {},

      }).then(function (data) {
        var op = data && data.operation;
        if (!op) {
          showError("保活运行未返回操作状态");
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
        showError(errorText(err));
      });
    }

    function openCreateConnection() {
      var nameInput = h("input.input", { autofocus: true, "data-conn-name": "1" });
      var originInput = h("input.input", { placeholder: "SDA Origin（部署端内网地址）", "data-conn-origin": "1" });
      var enabledInput = h("input", { type: "checkbox", checked: true, "data-conn-enabled": "1" });
      var error = h("div.form-error", { style: "display:none", "data-conn-error": "1" });

      var showError = fieldErrors(error);

      var modal = viewModal({
        title: "新建连接",
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("SDA Origin", originInput),
          h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用连接")),
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
                if (!name || !origin) { showError("名称与 Origin 不能为空", !name ? nameInput : originInput); return; }
                showError("");
                MC.api.requestV3("POST", connectionsPath(), {
                  body: { name: name, origin: origin, enabled: !!enabledInput.checked },
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
      });
      return modal;
    }

    function openConnectionEditor(conn) {
      var nameInput = h("input.input", { value: conn.name || "", autofocus: true, "data-conn-name": "1" });
      var originInput = h("input.input", { value: conn.origin || "", "data-conn-origin": "1" });
      var enabledInput = h("input", { type: "checkbox", checked: conn.enabled !== false, "data-conn-enabled": "1" });
      var error = h("div.form-error", { style: "display:none", "data-conn-error": "1" });

      var showError = fieldErrors(error);

      var modal = viewModal({
        title: "编辑连接",
        subtitle: conn.name + " · " + conn.connection_id,
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("SDA Origin", originInput),
          h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用连接")),
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
                if (!name || !origin) { showError("名称与 Origin 不能为空", !name ? nameInput : originInput); return; }
                showError("");
                MC.api.requestV3("PATCH", connectionPath(conn.connection_id), {
                  body: { name: name, origin: origin, enabled: !!enabledInput.checked },
                  headers: { "If-Match": String(conn.policy_revision) },
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
      });
      return modal;
    }

    function openCredentialForm(conn) {
      var usernameInput = h("input.input", { autofocus: true, autocomplete: "off", "data-cred-username": "1" });
      var passwordInput = h("input.input", { type: "password", autocomplete: "new-password", "data-cred-password": "1" });
      var error = h("div.form-error", { style: "display:none", "data-cred-error": "1" });
      var cred = conn.credential || {};

      var showError = fieldErrors(error);

      var modal = viewModal({
        title: "更换凭据",
        subtitle: conn.name + " · 当前状态 " + credentialLabel(cred),
        body: h("div.col", { style: "gap:12px" },
          error,
          field("账号", usernameInput),
          field("密码", passwordInput),
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
                if (!username || !password) { showError("账号与密码不能为空", !username ? usernameInput : passwordInput); return; }
                showError("");
                MC.api.requestV3("POST", connectionPath(conn.connection_id) + "/credentials/candidates", {
                  body: { username: username, password: password },
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

        },
      });
      return modal;
    }

    function openCredentialAction(conn, title, description, submitLabel, action) {
      var error = h("div.form-error", { style: "display:none", "data-cred-error": "1" });

      var showError = fieldErrors(error);

      var modal = viewModal({
        title: title,
        subtitle: conn.name,
        body: h("div.col", { style: "gap:12px" },
          error,
          h("p.small", description)
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-cred-action-submit": "1",
              onclick: function () {
                showError("");
                action().then(function (op) {
                  close();
                  if (op) trackOperation(conn.connection_id, op);
                }, function (err) {
                  showError(credentialErrorText(err));
                });
              },
            }, submitLabel),
          ];
        },
      });
      return modal;
    }

    function openProbe(conn) {
      var candidate = (conn.credential || {}).candidate || {};
      return openCredentialAction(
        conn,
        "验证凭据",
        "验证只提交一次探测；密码错误会锁存凭据，需要人工重试或更换凭据，不会自动重复尝试。",
        "开始验证",
        function () {
          return MC.api.requestV3("POST", connectionPath(conn.connection_id) + "/credentials/probe", {
            body: { candidate_id: candidate.candidate_id },

          }).then(function (data) { return data && data.operation; });
        }
      );
    }

    function openActivate(conn) {
      var candidate = (conn.credential || {}).candidate || {};
      return openCredentialAction(
        conn,
        "激活凭据",
        "激活会覆盖当前有效凭据；probe_epoch 必须与候选一致，晚到的验证结果不能激活。",
        "激活",
        function () {
          return MC.api.requestV3(
            "POST",
            connectionPath(conn.connection_id) + "/credentials/candidates/"
              + encodeURIComponent(candidate.candidate_id) + "/activate",
            {
              body: { probe_epoch: candidate.auth_epoch == null ? 0 : candidate.auth_epoch },

            }
          ).then(function (data) { return data && data.operation; });
        }
      );
    }

    function connectionDrafts() {
      var C = state.connections;
      if (C.draftWorkspace !== workspaceId()) { C.draftWorkspace = workspaceId(); C.drafts = {}; }
      return C.drafts;
    }

    function renderHeartbeatCard(conn, manage) {
      var cid = conn.connection_id;
      var C = state.connections;
      var hb = C.heartbeats[cid];
      var draft = connectionDrafts()[cid] || hb;
      var cred = conn.credential || {};
      var error = h("div.form-error", { style: "display:none" });
      var showError = fieldErrors(error);
      var controls = h("div.sync-heartbeat-controls");

      if (manage) {
        var enabledInput = h("input", { type: "checkbox", checked: !!(draft && draft.enabled), "data-hb-enabled": cid });
        var autoInput = h("input", {
          type: "checkbox",
          checked: !draft || draft.auto_relogin !== false,
          "data-auto-relogin-enabled": cid,
        });
        var intervalInput = h("input.input", {
          type: "number", min: "1", max: "60",
          value: String((draft && draft.interval_minutes) || 10),
          "data-hb-interval": cid,
          style: "max-width:100px",
        });
        controls.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用保活")));
        controls.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, autoInput, h("span", "登录失效后自动重新登录")));
        controls.appendChild(h("span.small.muted", "间隔"));
        intervalInput.setAttribute("aria-label", "保活间隔（分钟）");
        controls.appendChild(intervalInput);
        controls.appendChild(h("span.small.muted", "分钟"));
        controls.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-hb-save": cid,
          onclick: function () { saveHeartbeat(conn, enabledInput, intervalInput, showError, autoInput); },
        }, "保存设置"));
        controls.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-hb-run": cid,
          onclick: function () { runHeartbeat(conn, showError); },
        }, "立即保活"));
        function rememberDraft() {
          var next = { enabled: enabledInput.checked, auto_relogin: autoInput.checked,
            interval_minutes: intervalInput.value, revision: draft && draft.revision || 0 };
          if (hb && next.enabled === !!hb.enabled && next.auto_relogin === (hb.auto_relogin !== false) && Number(next.interval_minutes) === Number(hb.interval_minutes)) delete connectionDrafts()[cid];
          else connectionDrafts()[cid] = next;
        }
        enabledInput.addEventListener("change", rememberDraft);
        autoInput.addEventListener("change", rememberDraft);
        intervalInput.addEventListener("input", rememberDraft);
      } else {
        controls.appendChild(h("span.small.muted", { "data-hb-readonly": cid },
          "只读身份：不能修改保活开关或立即运行。"));
      }

      var candidate = cred.candidate;
      var conflict = null;
      if (draft && hb && String(draft.revision) !== String(hb.revision)) {
        function settingsText(value) {
          return (value.enabled ? "保活开启" : "保活关闭") + " · " + value.interval_minutes + " 分钟 · 自动登录" + (value.auto_relogin !== false ? "开启" : "关闭");
        }
        conflict = h("div.col", { style: "gap:8px", role: "status" },
          h("strong.small", "设置已被其他管理员修改"),
          h("p.small", "当前服务器：" + settingsText(hb)),
          h("p.small", "您的设置：" + settingsText(draft)),
          h("button.btn.btn-sm", { type: "button", "data-hb-confirm-revision": cid,
            onclick: function () {
              if (connectionDrafts()[cid] !== draft || C.heartbeats[cid] !== hb) return;
              connectionDrafts()[cid] = Object.assign({}, draft, { revision: hb.revision });
              refreshConnectionsView();
            } }, "确认保留我的设置，再保存"));
      }
      return h("section.sync-connection-section", h("div.col", { style: "gap:12px" },
        h("div.sync-connection-section-head",
          h("strong", "保活与自动登录")
        ),
        h("p.small.muted", hb ? (hb.enabled ? "保活已启用" : "保活已关闭") + (hb.last_heartbeat_at ? " · 最近 " + util.formatTime(hb.last_heartbeat_at) + " · " + (hb.last_result === "success" ? "成功" : "未成功") : " · 尚未运行") : "保活状态读取失败"),
        C.heartbeatErrors[cid] ? h("p.form-error", C.heartbeatErrors[cid]) : null,
        h("p.small.muted", { "data-auto-relogin": cid }, autoReloginText(conn)),
        controls,
        error,
        conflict,
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
      var toolbar = h("div.row.sync-filters", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
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

      if (!C.items.some(function (item) { return item.connection_id === C.selectedId; })) C.selectedId = C.items[0].connection_id;
      var picker = h("select.input", { "aria-label": "选择连接", onchange: function () { C.selectedId = picker.value; paintSheet(); } },
        C.items.map(function (item) { return h("option", { value: item.connection_id }, item.name); }));
      picker.value = C.selectedId;
      toolbar.insertBefore(picker, toolbar.firstChild);
      body.appendChild(h("div.sync-connection-picker", toolbar));
      C.items.filter(function (conn) { return conn.connection_id === C.selectedId; }).forEach(function (conn) {
        var cred = conn.credential || {};
        var actions = h("div.row.sync-actions", { style: "gap:6px;flex-wrap:wrap" });
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
        var edit = actions.querySelector("[data-conn-edit]");
        if (edit) edit.textContent = "编辑连接";
        var technical = h("details.sync-connection-technical", h("summary", "技术详情"),
          h("div.sync-connection-meta", h("div", h("span.small.muted", "连接编号"), h("div.mono", conn.connection_id)),
            h("div", h("span.small.muted", "认证纪元"), h("div.mono", { "data-cred-epoch": conn.connection_id }, String(cred.auth_epoch == null ? 0 : cred.auth_epoch)))),
          h("p.small", "最近错误：", h("span", { "data-cred-error": conn.connection_id }, cred.last_error || "无")),
          h("p.small", { "data-hb-status": conn.connection_id }, heartbeatStatusText(C.heartbeats[conn.connection_id])));
        body.appendChild(h("section.sync-connection-settings", { "data-connection": conn.connection_id },
          h("section.sync-connection-section",
            h("div.sync-connection-section-head", h("h3", "连接信息"), edit),
            h("div.sync-connection-name", h("strong", conn.name), h("span.badge" + (conn.enabled === false ? ".badge-count" : ".badge-success"), conn.enabled === false ? "已停用" : "已启用")),
            h("div.sync-connection-origin.mono", conn.origin || "—"),
            h("p.small.muted", "连接属于当前工作区，切换机型不会更改连接设置。")),
          h("section.sync-connection-section",
            h("div.sync-connection-section-head", h("h3", "登录凭据"), h("span", { class: credentialBadge(cred), "data-cred-state": conn.connection_id }, credentialLabel(cred))),
            h("p.small.muted", "用于读取 SDA 中受保护的配置内容。"), actions),
          renderHeartbeatCard(conn, manage), technical));
      });
    }

    function fileSourceFrame(rows) {
      var S = state.sources;
      return h("div.card", h("div.card-b",
        h("div.sync-table-wrap", h("table.table.sync-source-table",
          h("thead", h("tr", ["来源", "目标版本", "更新计划", "最近结果", "操作"].map(function (name) { return h("th", name); }))), rows)),
        h("div.sync-list-footer", h("span.muted.xsmall", "获取更新后先核对，再保存为版本标准"),
          h("div.row", h("span.muted.xsmall", "已加载 " + S.items.length + " / " + S.total), S.next_cursor ? h("button.btn.btn-sm", { type: "button", disabled: S.loading, onclick: function () { S.expanded = true; loadSources(false); } }, "加载更多") : null))));
    }
    function fileSourceState(content) {
      return fileSourceFrame(h("tbody", h("tr", h("td", { colspan: 5 }, content))));
    }
    function renderSources() {
      var S = state.sources;
      var toolbar = h("div.row.sync-filters", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var search = h("input.input", { type: "search", "aria-label": "搜索更新源名称或路径", placeholder: "搜索名称或资源路径", value: S.query });
      var status = h("select.input", { "aria-label": "来源状态" },
        SOURCE_STATUS.map(function (item) { return h("option", { value: item.id }, item.label); })
      );
      var scope = h("select.input", { "aria-label": "来源机型范围", "data-sync-scope": "1" },
        h("option", { value: "profile" }, "当前机型"),
        h("option", { value: "workspace" }, "全部机型")
      );
      var sort = h("select.input", { "aria-label": "来源排序" },
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
      scope.value = S.scope;
      sort.value = S.sort;
      status.addEventListener("change", function () { S.status = status.value; loadSources(true); });
      scope.addEventListener("change", function () {
        S.scope = scope.value;
        S.configuration_id = "";
        S.release_id = "";
        S.selected = {};
        loadSources(true);
      });
      sort.addEventListener("change", function () { S.sort = sort.value; MC.closePopover(); renderBody(); });
      var refresh = h("button.btn.btn-sm", { type: "button", "data-sync-refresh": "1", onclick: function () { loadSources(true); loadRecentRun(); } }, "刷新");
      toolbar.appendChild(search);
      toolbar.appendChild(searchBtn);
      if (!fileConfig) toolbar.appendChild(scope);
      function targetFilter(key, items, title) {
        var control = h("select.input", { "aria-label": title }, h("option", { value: "" }, title),
          items.map(function (item) { return h("option", { value: item.id }, item.label || item.id); }));
        control.value = S[key];
        control.addEventListener("change", function () { S[key] = control.value; S.selected = {}; loadSources(true); });
        return control;
      }
      if (!fileConfig) toolbar.appendChild(targetFilter("configuration_id", S.filters.configurations || [], "全部固定配置"));
      toolbar.appendChild(targetFilter("release_id", S.filters.releases || [], "全部软件版本"));
      toolbar.appendChild(status);
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", "aria-label": "来源排序", onclick: function (event) {
        MC.popover(event.currentTarget, field("来源排序", sort), { align: "end" });
      } }, "排序"));
      toolbar.appendChild(refresh);
      if (S.query || S.status || S.configuration_id || S.release_id) toolbar.appendChild(h("button.btn.btn-sm.btn-ghost", {
        type: "button", onclick: function () { S.query = ""; S.status = ""; S.configuration_id = ""; S.release_id = ""; S.selected = {}; loadSources(true); },
      }, "清除筛选"));

      if (S.error) {
        // 失败也要保留工具栏：给显式重试入口，不让用户刷新整页。
        body.appendChild(h("div.form-error", { "data-sync-error": "1" },
          S.error + " ",
          h("button.btn.btn-sm", { type: "button", "data-sync-retry": "1", onclick: function () { loadSources(true); } }, "重试")
        ));
      }
      if (S.loading && !S.items.length) {
        var loading = h("p.muted.sync-loading", { role: "status" }, "正在读取更新源…");
        body.appendChild(fileConfig ? fileSourceState(loading) : h("div.card", h("div.card-b", toolbar, loading)));
        return;
      }
      if (S.error && !S.items.length) {
        body.appendChild(fileConfig ? fileSourceState(h("p.muted.small", "更新来源读取失败，请重试。")) : h("div.card", h("div.card-b", toolbar)));
        return;
      }
      var rows = sortedItems();
      var selectable = rows.filter(function (row) { return !row.deleted; });
      var selectedRows = selectable.filter(function (row) { return S.selected[row.source_id]; });
      var batchBar = h("div.row.sync-selection", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px", "data-sync-batch": "1" });
      batchBar.hidden = !selectedRows.length;
      if (canWrite() && selectable.length) {
        batchBar.appendChild(h("span.small.muted", "已选 " + selectedRows.length + " / " + selectable.length));
        if (canRun()) batchBar.appendChild(h("button.btn.btn-sm.btn-primary", { type: "button", "data-sync-selected-run": "1", onclick: function () { previewSources(selectedSourceRows()); } }, "更新所选"));
        batchBar.appendChild(h("button.btn.btn-sm", { type: "button", "data-sync-batch-plan": "1", disabled: selectedRows.length > 100,
          title: selectedRows.length > 100 ? "每批最多调整 100 个来源" : "只修改频率，不改变来源与目标", onclick: editSelectedSchedule }, "调整计划"));
        batchBar.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-sync-batch-enable": "1", disabled: !selectedRows.length || !!S.busy,
          onclick: function () { batchSources("enable"); },
        }, "批量启用"));
        batchBar.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-sync-batch-disable": "1", disabled: !selectedRows.length || !!S.busy,
          onclick: function () { batchSources("disable"); },
        }, "批量停用"));
        batchBar.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-sync-batch-delete": "1", disabled: !selectedRows.length || !!S.busy,
          onclick: function () { batchSources("delete"); },
        }, "批量删除"));
        if (S.busy) batchBar.appendChild(h("span.xsmall.muted", "正在批量处理…"));
      }

      if (!S.items.length) {
        if (fileConfig) { body.appendChild(fileSourceState(emptyState("尚未配置更新来源", "在更新设置中添加此文件的来源。"))); return; }
        body.appendChild(h("div.card",
          h("div.card-b", fileConfig ? null : toolbar,
            emptyState(fileConfig ? "尚未配置更新来源" : S.query || S.status || S.configuration_id || S.release_id ? "没有匹配的更新源" : (S.scope === "profile" ? "当前机型没有更新源" : "添加第一个更新源"), fileConfig ? "在更新设置中添加此文件的来源。" : S.query || S.status || S.configuration_id || S.release_id ? "调整搜索或筛选条件后重试。" : (S.scope === "profile" ? "切换到全部机型查看，或从右上角新增更新源。" : "从右上角新增更新源，绑定来源与接收目标。")))
        ));
        return;
      }

      var headerBox = h("input", {
        type: "checkbox",
        "data-sync-select-all": "1",
        "aria-label": "全选当前页来源",
        checked: selectable.length > 0 && selectedRows.length === selectable.length,
      });
      headerBox.addEventListener("change", function () {
        selectable.forEach(function (row) {
          if (headerBox.checked) S.selected[row.source_id] = true;
          else delete S.selected[row.source_id];
        });
        renderBody();
      });

      var tbody = h("tbody");
      rows.forEach(function (source) {
        var actions = h("div.row.sync-actions", { style: "gap:6px;flex-wrap:wrap" });
        var extras = h("div.sync-source-extra-actions");
        var deleted = !!source.deleted;
        if (!deleted && canWrite()) {
          actions.appendChild(h("button.btn.btn-sm.btn-ghost", {
            type: "button",
            "data-sync-edit": source.source_id,
            onclick: function () { openEditor(source); },
          }, "设置"));
        }
        if (!deleted && canRun()) {
          var runBtn = h("button.btn.btn-sm.btn-ghost", {
            type: "button",
            "data-sync-run": source.source_id,
            title: source.enabled === false ? "来源已停用：启用后才能更新" : "立即更新此来源",
            onclick: function (event) { runSource(source, event.currentTarget); },
          }, "更新");
          if (source.enabled === false) runBtn.disabled = true;
          actions.insertBefore(runBtn, actions.firstChild);
          extras.appendChild(h("button.btn.btn-sm.btn-ghost", {
            type: "button",
            "data-sync-test": source.source_id,
            disabled: !!S.testing[source.source_id],
            onclick: function (event) { testSource(source, event.currentTarget); },
          }, S.testing[source.source_id] ? "测试中…" : "测试"));
        }
        if (!deleted && canWrite()) {
          extras.appendChild(h("button.btn.btn-sm.btn-ghost", {
            type: "button",
            "data-sync-delete": source.source_id,
            onclick: function () { deleteSource(source); },
          }, "删除"));
        }
        if (extras.childNodes.length) actions.appendChild(h("details.sync-source-more",
          h("summary.btn.btn-sm.btn-ghost", { "aria-label": "更多操作：" + source.name }, "更多"), extras));
        if (!canWrite() && !canRun()) actions.appendChild(h("span.muted.xsmall", "只读"));
        var testResult = S.testResults[source.source_id];
        var checkbox = deleted ? h("td") : h("td",
          h("input", {
            type: "checkbox",
            "data-sync-select": source.source_id,
            "aria-label": "选择来源 " + source.name,
            checked: !!S.selected[source.source_id],
            onchange: function (event) {
              if (event.currentTarget.checked) S.selected[source.source_id] = true;
              else delete S.selected[source.source_id];
              renderBody();
            },
          })
        );
        var target = (source.target && source.target.kind) || "";
        var freshness = source.freshness;
        var reason = freshness && freshness.reason_code;
        var failed = reason && ["unknown", "validated", "unchanged", "disabled", "canceled"].indexOf(reason) < 0;
        var resultText = source.has_candidate ? "有更新候选" : reason === "source_changed" ? "需重新核验" : failed ? "更新失败" : reason === "unchanged" ? "内容一致" : freshness && freshness.last_verified_at ? "已核验" : freshness && freshness.last_attempt_at ? "尚未有效核验" : "尚未运行";
        if (fileConfig) {
          var display = source.target_display || {};
          var version = display.software_version;
          if (source.has_candidate) actions.insertBefore(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { state.tab = "candidates"; renderTabs(); loadCandidates(true); if (app.go) app.go({ kind: "updates", arg: "candidates", query: fileQuery() }); } }, "核对变化"), actions.firstChild);
          tbody.appendChild(h("tr", { "data-sync-source": source.source_id },
            h("td", h("div.row", icon("cloud"), h("div", sourceText(source.name, "name"), sourceText(source.display_path || source.resource_ref, "path")))),
            h("td", version ? "软件 " + version : target === "file_archive" ? "文件归档" : "尚未指定版本"),
            h("td", h("div.small", scheduleText(source)), h("div.xsmall.muted", source.enabled === false ? "已停用" : source.schedule && source.schedule.automatic_enabled ? "已启用" : "自动更新已暂停")),
            h("td", h("span.badge" + (source.has_candidate || failed ? ".badge-warning" : ".badge-muted"), source.has_candidate ? "待核对" : resultText), freshness && freshness.last_attempt_at ? h("div.xsmall.muted", util.formatTime(freshness.last_attempt_at)) : null),
            h("td", actions)));
          return;
        }
        tbody.appendChild(h("tr", { "data-sync-source": source.source_id, class: source.enabled === false ? "sync-paused" : "" },
          checkbox,
          h("td",
            h("div", sourceText(source.name, "name")),
            h("div.xsmall.muted", [(source.target_display || {}).software_version, (source.target_display || {}).configuration_name, source.profile_id].filter(Boolean).join(" · "))
          ),
          h("td",
            h("div.small", (KIND_LABEL[source.kind] || "") + (target ? " → " + ({ file_archive: "文件归档", default_candidate: "默认配置候选", machine_observation: "机台观测" }[target] || target) : "")),
            sourceText(source.display_path || source.resource_ref, "path")
          ),
          h("td",
            h("div.small", scheduleText(source)),
            h("div.xsmall.muted", !source.enabled ? "来源已停用" : source.schedule_mode === "manual" ? "可手动立即更新"
              : source.schedule ? (source.schedule.next_due_at ? "下次 " + util.formatTime(source.schedule.next_due_at) : source.schedule.automatic_enabled ? "等待调度" : "自动更新已暂停")
              : source.schedule_mode === "custom" ? "独立频率" : "跟随工作区计划")
          ),
          h("td", source.freshness
            ? h("div", h("div.small", source.freshness.last_verified_at ? util.formatTime(source.freshness.last_verified_at) : "尚未有效核验"),
                h("div.xsmall.muted", failed && freshness.last_verified_at ? "保留上次有效内容" : "来源内容核验时间"))
            : h("span.xsmall.muted", "核验状态未提供")),
          h("td", deleted
            ? h("span.badge.badge-muted", "已删除")
            : testResult
              ? h("span.xsmall", { class: testResult.ok ? "badge badge-ok" : "badge badge-muted", "data-sync-test-result": source.source_id }, testResult.text)
              : source.enabled === false ? h("span.badge.badge-muted", "已停用")
              : freshness ? h("div", h("span.badge" + (failed ? ".badge-warning" : freshness.last_verified_at && !source.has_candidate ? ".sync-status-enabled" : ".badge-muted"), resultText),
                  failed ? h("div.xsmall.muted", OPERATION_REASONS[reason] || ({ network_error: "网络不可达，请检查地址后重试", invalid_payload: "来源内容未通过校验", storage_failure: "结果保存失败", target_missing: "目标不存在或未绑定", source_changed: "来源或绑定已变化，请重新核对" }[reason] || "更新未完成，请查看运行详情")) : null)
              : h("span.badge.sync-status-enabled", "已启用")),
          h("td", actions)
        ));
      });
      if (fileConfig) { body.appendChild(fileSourceFrame(tbody)); return; }
      body.appendChild(h("div.card",
        h("div.card-b",
          toolbar,
          batchBar,
          h("div.sync-table-wrap", h("table.table.sync-source-table", h("thead", h("tr",
            h("th", headerBox), h("th", "配置 / 文件"), h("th", "更新来源 / 写入目标"),
            h("th", "更新计划"), h("th", "最近有效核验"), h("th", "最近结果"), h("th", "操作")
          )), tbody)),
          h("div.sync-list-footer",
            h("span.muted.xsmall", "全量更新不受当前筛选条件影响"),
            h("div.row", { style: "gap:8px;align-items:center" },
              h("span.muted.xsmall", "已加载 " + S.items.length + " / " + S.total),
              S.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { S.expanded = true; loadSources(false); } }, "加载更多") : null
            )
          )
        )
      ));
    }

    function runsPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/runs";
    }

    function loadRecentRun() {
      var origin = captureOrigin();
      var selectedConfig = fileConfig;
      return MC.api.requestV3("GET", runsPath(), { query: scopeQuery({ limit: 1, cursor: "0" }) }).then(function (data) {
        if (!origin.current() || selectedConfig !== fileConfig) return;
        var first = data && data.items && data.items[0];
        state.recentRun = first && (first.run || first);
        if (state.tab === "sources") renderBody();
      }, function () { /* The full run list retains its explicit retry path. */ });
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
      if (R.loading && !reset) return Promise.resolve();
      var seq = ++R.loadSeq;
      var wid = workspaceId();
      R.loading = true;
      R.error = "";
      if (reset) {
        R.items = [];
        R.next_cursor = null;
        R.expanded = false;
      }
      renderBody();
      var append = !reset && R.expanded && !!R.next_cursor;
      return MC.api.requestV3("GET", runsPath(), {
        query: scopeQuery({
          state: R.state || undefined,
          q: R.query || undefined,
          failed_only: R.failedOnly || undefined,
          limit: 50,
          cursor: append ? R.next_cursor : "0",
        }),
      }).then(function (data) {
        if (seq !== R.loadSeq || wid !== workspaceId()) return;
        var incoming = (data && data.items) || [];
        R.items = append ? R.items.concat(incoming) : incoming;
        R.total = data && typeof data.total === "number" ? data.total : R.items.length;
        R.next_cursor = data ? data.next_cursor : null;
        R.loading = false;
        renderBody();
      }, function (err) {
        if (seq !== R.loadSeq || wid !== workspaceId()) return;
        R.loading = false;
        R.error = errorText(err);
        renderBody();
      });
    }

    function readEventPage(runId, after, path) {
      return MC.api.requestV3("GET", (path || runPath(runId)) + "/events", { query: { after_seq: after } }).then(function (data) {
        if (!data || !Array.isArray(data.items) || data.items.length > 200) throw new Error("事件列表格式无效");
        var previous = after;
        data.items.forEach(function (event) {
          if (!event || !Number.isSafeInteger(event.seq) || event.seq <= previous) throw new Error("事件序号无效");
          previous = event.seq;
        });
        return data.items;
      });
    }

    function exportDiagnostics(run, onFailure, onSuccess, copy) {
      var allEvents = [];
      var path = runPath(run.run_id);
      var maxEvents = 20000;
      function collect(after) {
        return readEventPage(run.run_id, after, path).then(function (page) {
          allEvents = allEvents.concat(page);
          if (page.length === 200 && allEvents.length < maxEvents) return collect(page[page.length - 1].seq);
          return page.length === 200 && allEvents.length >= maxEvents;
        });
      }
      return collect(0).then(function (truncated) {
        var payload = {
          run: run,
          events: allEvents,
          exported_at: new Date().toISOString(),
          note: truncated ? "事件达到 20000 条读取上限，诊断可能不完整；文件不含凭据。" : "MCChecker sync diagnostics; no secrets are included",
        };
        if (copy) return util.copyText(JSON.stringify(payload, null, 2)).then(function (ok) {
          if (!ok) throw new Error("剪贴板不可用，可改用下载诊断");
          if (onSuccess) onSuccess(truncated);
        });
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "sync-run-" + run.run_id + "-diagnostics.json";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        if (onSuccess) onSuccess(truncated);
      }).catch(function (err) {
        if (onFailure) onFailure(err);
        else MC.toast.error("诊断下载失败", errorText(err));
      });
    }

    function retryRun(run, close) {
      MC.api.requestV3("POST", runPath(run.run_id) + "/retry", { body: {} }).then(function () {
        MC.toast.success("失败项已重新入队");
        if (close) close();
        else loadRuns(true);
      }, function (err) {
        MC.toast.error("重试提交失败", errorText(err));
      });
    }

    function openRun(run) {
      if (fileConfig && !fileDetail) return;
      var runOrigin = captureOrigin(), detailPath = runPath(run.run_id), detailSeq = 0;
      var returnFocusText = document.activeElement && document.activeElement.tagName === "BUTTON" ? document.activeElement.textContent : "";
      var refreshButton = h("button.btn", { type: "button", "data-sync-detail-refresh": "1", onclick: function () { loadDetails(); } }, "刷新详情");
      var bodyNode = h("div.col", { style: "gap:12px" }, h("p.muted.small", "正在读取运行详情…"));
      var downloadError = h("div.form-error", { "data-sync-download-error": "1", role: "alert", hidden: true });
      var modal = viewModal({
        title: "运行 " + run.run_id,
        subtitle: run.trigger + " · " + runStateText(run.state) + " · " + runCounts(run),
        size: "xwide",
        body: h("div.col", downloadError, bodyNode),
        onClose: function () {
          if (!el.isConnected || !runOrigin.current()) return;
          var reads = [loadRecentRun()];
          if (state.tab === "sources" && !state.sources.expanded) reads.push(loadSources(true));
          if (state.tab === "runs" && !state.runs.expanded) reads.push(loadRuns(true));
          Promise.all(reads).then(function () {
            if (!returnFocusText || !el.isConnected || !runOrigin.current() || document.querySelector(".modal-backdrop") || document.activeElement !== document.body) return;
            var target = Array.prototype.filter.call(el.querySelectorAll("button"), function (button) { return button.textContent === returnFocusText; })[0];
            if (target) target.focus();
          });
        },
        footer: function (close) {
          var buttons = [];
          if (canRun() && !fileConfig) {
            var cancelOrigin = captureOrigin();
            var cancelButton = h("button.btn", { type: "button", "data-sync-run-cancel": "1", onclick: function () {
              MC.confirm({ title: "取消本次运行？", message: "已完成的结果会保留；尚未完成的来源将停止处理。", confirmText: "取消运行", cancelText: "继续运行" }).then(function (ok) {
                if (!ok || !cancelOrigin.current()) return;
                cancelButton.disabled = true;
                MC.api.requestV3("POST", runPath(run.run_id) + "/cancel", { body: {} }).then(function () {
                  if (!cancelOrigin.current()) return;
                  MC.toast.success("取消请求已提交", "请刷新运行记录查看最终结果。");
                  close();
                }, function (err) {
                  cancelButton.disabled = false;
                  downloadError.textContent = "取消请求失败：" + errorText(err); downloadError.hidden = false;
                });
              });
            } }, "取消运行");
            cancelButton.hidden = ["queued", "running"].indexOf(run.state) < 0;
            buttons.push(cancelButton);
          }
          if (canRun() && !fileConfig) {
            buttons.push(h("button.btn.btn-primary", { type: "button", "data-sync-retry": "1", hidden: ["partial", "failed", "interrupted"].indexOf(run.state) < 0, onclick: function () { retryRun(run, close); } }, "重试失败项"));
          }
          if (fileConfig) buttons.push(h("button.btn", { type: "button", onclick: function () { close(); app.go({ kind: "updates", arg: "runs" }); } }, "在工作区管理完整任务"));
          var downloadButton = h("button.btn", { type: "button", onclick: function () {
            downloadButton.disabled = true;
            exportDiagnostics(run, function (err) {
              downloadButton.disabled = false;
              downloadError.textContent = "诊断下载失败：" + errorText(err) + "。请重试。";
              downloadError.hidden = false;
            }, function (truncated) {
              downloadButton.disabled = false;
              downloadError.hidden = !truncated;
              downloadError.textContent = truncated ? "事件达到 20000 条上限，已下载的诊断可能不完整。" : "";
            });
          } }, fileConfig ? "下载完整任务诊断" : "下载诊断");
          buttons.push(downloadButton);
          var copyButton = h("button.btn", { type: "button", "data-sync-copy-diagnostics": "1", onclick: function () {
            copyButton.disabled = true;
            exportDiagnostics(run, function (err) {
              copyButton.disabled = false; downloadError.hidden = false;
              downloadError.textContent = "复制诊断失败：" + errorText(err);
            }, function (truncated) {
              copyButton.disabled = false; downloadError.hidden = !truncated;
              downloadError.textContent = truncated ? "已复制，但事件达到读取上限，诊断可能不完整。" : "";
              MC.toast.success("诊断已复制");
            }, true);
          } }, "复制诊断");
          buttons.push(copyButton);
          buttons.push(refreshButton);
          buttons.push(h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭"));
          return buttons;
        },
      });
      modal.el.classList.add("sync-run-dialog");
      function loadDetails() {
      if (!runOrigin.current()) return Promise.resolve();
      var seq = ++detailSeq;
      var previousFilter = bodyNode.querySelector("[data-sync-run-failures]");
      var failuresSelected = !!(previousFilter && previousFilter.checked);
      var previousItem = bodyNode.querySelector('[data-sync-run-item][aria-pressed="true"]');
      var previousItemId = previousItem && previousItem.getAttribute("data-sync-run-item");
      refreshButton.disabled = true;
      util.clear(bodyNode);
      bodyNode.appendChild(h("p.muted.small", "正在读取运行详情…"));
      return Promise.all([
        MC.api.requestV3("GET", detailPath, {}),
        readEventPage(run.run_id, 0, detailPath),
      ]).then(function (results) {
        if (seq !== detailSeq || !runOrigin.current() || !modal.el.isConnected) return;
        refreshButton.disabled = false;
        var detail = results[0] || {};
        if (detail.run) run = detail.run;
        modal.el.querySelector(".modal-sub").textContent = run.trigger + " · " + runStateText(run.state) + " · " + runCounts(run);
        var cancelControl = modal.el.querySelector("[data-sync-run-cancel]");
        var retryControl = modal.el.querySelector("[data-sync-retry]");
        if (cancelControl) cancelControl.hidden = ["queued", "running"].indexOf(run.state) < 0;
        if (retryControl) retryControl.hidden = ["partial", "failed", "interrupted"].indexOf(run.state) < 0;
        var events = results[1];
        var items = detail.items || [];
        if (fileConfig) {
          var owned = {};
          ((detail.frozen || {}).source_targets || []).forEach(function (row) {
            var target = row.target || {};
            if (row.profile_id === app.pid && (target.configuration_id === fileConfig || target.kind === "file_archive" && target.file_id === fileDetail.file_id)) owned[row.source_id] = true;
          });
          items = items.filter(function (row) { return owned[row.source_id]; });
        }
        util.clear(bodyNode);
        if (fileConfig) bodyNode.appendChild(h("p.small.muted", "此处显示当前文件的来源。任务汇总及诊断包含整个任务；取消或重试请到工作区管理。"));
        var itemList = h("div.sync-log-items", { "aria-label": "运行来源" });
        var log = h("div.sync-log-body", { "aria-live": "polite" });
        var buttons = [];
        var selectedItem = null;
        var eventError = h("div.form-error", { role: "alert", hidden: true });
        var moreButton = null;
        function showEvents(item) {
          selectedItem = item;
          buttons.forEach(function (button) {
            var selected = button.getAttribute("data-sync-run-item") === (item ? item.item_id : "all");
            button.setAttribute("aria-pressed", String(selected));
          });
          util.clear(log);
          log.appendChild(h("h3", item ? (item.name || item.source_id) : "全部事件"));
          var visible = item ? events.filter(function (event) {
            return event.item_id === item.item_id || (!event.item_id && event.source_id === item.source_id);
          }) : events.filter(function (event) { return !fileConfig || !event.source_id && !event.item_id || items.some(function (row) { return event.source_id && event.source_id === row.source_id || event.item_id && event.item_id === row.item_id; }); });
          if (!item && failuresOnly && failuresOnly.checked) visible = visible.filter(function (event) {
            if (!event.item_id && !event.source_id) return true;
            return items.some(function (row) { return ["failed", "blocked", "interrupted"].indexOf(row.state) >= 0 && (event.item_id === row.item_id || !event.item_id && event.source_id === row.source_id); });
          });
          if (!visible.length) {
            log.appendChild(h("p.small.muted", moreButton ? "已加载的事件中没有匹配项；可加载更多事件。" : "没有对应的事件日志。"));
            return;
          }
          visible.forEach(function (event) {
            log.appendChild(h("div.sync-log-line",
              h("time.mono", event.at || "—"),
              h("strong", event.status || event.level || "—"),
              h("span", event.message || "—", event.reason_code ? h("span.muted", " · " + event.reason_code) : null)));
          });
        }
        var allButton = h("button.sync-log-item", { type: "button", "data-sync-run-item": "all", onclick: function () { showEvents(null); } },
          h("strong", "全部来源"), h("span.small.muted", items.length + " 项"));
        buttons.push(allButton);
        itemList.appendChild(allButton);
        items.forEach(function (item) {
          var button = h("button.sync-log-item", { type: "button", "data-sync-run-item": item.item_id, onclick: function () { showEvents(item); } },
            h("strong", item.name || item.source_id),
            h("span.small", runStateText(item.state) + " · " + (item.stage || "—")),
            item.reason_code ? h("span.xsmall.muted", item.reason_code) : null);
          buttons.push(button);
          itemList.appendChild(button);
        });
        var failuresOnly = h("input", { type: "checkbox", "data-sync-run-failures": "1", onchange: function () {
          buttons.forEach(function (button, index) { if (index) button.hidden = failuresOnly.checked && ["failed", "blocked", "interrupted"].indexOf(items[index - 1].state) < 0; });
          var selected = selectedItem && failuresOnly.checked && ["failed", "blocked", "interrupted"].indexOf(selectedItem.state) < 0 ? null : selectedItem;
          showEvents(selected);
        } });
        bodyNode.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, failuresOnly, "只看失败来源"));
        bodyNode.appendChild(h("div.sync-log-layout", itemList, log));
        bodyNode.appendChild(eventError);
        function addMoreButton() {
          if (moreButton) return;
          moreButton = h("button.btn.btn-sm", { type: "button", "data-sync-events-more": "1", onclick: function () {
            var after = events.length ? Number(events[events.length - 1].seq) : 0;
            if (!isFinite(after) || after <= 0) {
              eventError.textContent = "事件序号无效，无法继续读取。"; eventError.hidden = false; return;
            }
            moreButton.disabled = true;
            eventError.hidden = true;
            readEventPage(run.run_id, after, detailPath).then(function (next) {
              events = events.concat(next);
              if (next.length < 200) { moreButton.remove(); moreButton = null; }
              else moreButton.disabled = false;
              showEvents(selectedItem);
            }).catch(function (err) {
              moreButton.disabled = false;
              eventError.textContent = "读取后续事件失败：" + errorText(err) + "。请重试。";
              eventError.hidden = false;
            });
          } }, "加载更多事件");
          bodyNode.appendChild(moreButton);
        }
        if (events.length >= 200) addMoreButton();
        showEvents(items.filter(function (item) { return item.item_id === previousItemId; })[0] || null);
        if (failuresSelected) { failuresOnly.checked = true; failuresOnly.dispatchEvent(new Event("change")); }
      }, function (err) {
        if (seq !== detailSeq || !runOrigin.current() || !modal.el.isConnected) return;
        refreshButton.disabled = false;
        util.clear(bodyNode);
        bodyNode.appendChild(h("div.form-error", { role: "alert" }, "读取运行详情失败：" + errorText(err), " ",
          h("button.btn.btn-sm", { type: "button", "data-sync-detail-retry": "1", onclick: loadDetails }, "重试")));
      });
      }
      loadDetails();
    }

    function renderRuns() {
      var R = state.runs;
      var toolbar = h("div.row.sync-filters", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var search = h("input.input", { type: "search", "aria-label": "搜索运行或来源文件", placeholder: "搜索运行编号、文件或来源", value: R.query });
      function searchRuns() { R.query = search.value.trim(); loadRuns(true); }
      search.addEventListener("keydown", function (event) { if (event.key === "Enter") searchRuns(); });
      toolbar.appendChild(search);
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", onclick: searchRuns }, "搜索"));
      var filter = h("select.input", { "aria-label": "运行结果" },
        h("option", { value: "" }, "全部状态"),
        ["queued", "running", "partial", "failed", "succeeded", "canceled", "interrupted"].map(function (value) {
          return h("option", { value: value }, runStateText(value));
        })
      );
      filter.value = R.state;
      filter.addEventListener("change", function () { R.state = filter.value; loadRuns(true); });
      toolbar.appendChild(filter);
      var failedOnly = h("input", { type: "checkbox", checked: R.failedOnly, onchange: function () { R.failedOnly = failedOnly.checked; loadRuns(true); } });
      toolbar.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, failedOnly, "仅看失败"));
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", onclick: function () { loadRuns(true); } }, "刷新"));
      if (!R.error && !R.loading) toolbar.appendChild(h("span.sync-result-count.muted.small", "共 " + R.total + " 条"));
      if (R.error) body.appendChild(h("div.form-error", R.error));
      if (R.loading && !R.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar, h("p.muted", "正在读取运行记录…"))));
        return;
      }
      if (R.error && !R.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar)));
        return;
      }
      if (!R.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar, emptyState(R.state || R.query || R.failedOnly ? "没有匹配的运行记录" : "还没有运行记录", "更新任务的处理结果与脱敏日志会显示在这里。"))));
        return;
      }
      var tbody = h("tbody");
      R.items.forEach(function (doc) {
        var run = doc.run || doc;
        var row = h("tr", { "data-sync-run": run.run_id, style: "cursor:pointer" },
          h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function (event) { event.stopPropagation(); openRun(run); } }, run.run_id), h("div.xsmall.muted", run.parent_run_id ? "父 " + run.parent_run_id : "")),
          h("td", run.trigger),
          h("td", runStateText(run.state)),
          h("td", runCounts(run)),
          h("td", h("div.small", util.formatTime(run.created_at)), h("div.xsmall.muted", run.finished_at ? util.formatTime(run.finished_at) : "未结束"))
        );
        row.addEventListener("click", function () { openRun(run); });
        tbody.appendChild(row);
      });
      body.appendChild(h("div.card",
        h("div.card-b", toolbar, h("div.sync-table-wrap", h("table.table.sync-run-table",
          h("thead", h("tr", h("th", "运行"), h("th", "触发"), h("th", "状态"), h("th", "计数"), h("th", "时间"))),
          tbody
        )), h("div.sync-list-footer", h("span.muted.xsmall", "已加载 " + R.items.length + " / " + R.total),
          R.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { R.expanded = true; loadRuns(false); } }, "加载更多") : null))
      ));
    }

    function candidatesPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/candidates";
    }

    function loadCandidates(reset, more) {
      var C = state.candidates;
      if (C.loading && !reset) return Promise.resolve();
      var seq = ++C.loadSeq;
      var wid = workspaceId();
      C.loading = true;
      C.loadedFor = wid;
      C.error = "";
      if (reset) { C.items = []; C.next_cursor = null; C.selected = {}; }
      renderBody();
      return MC.api.requestV3("GET", candidatesPath(), {
        query: scopeQuery({
          state: C.state === "all" ? undefined : C.state || undefined,
          pending_only: fileConfig && (!C.state || C.state === "pending") ? true : undefined,
          profile_id: C.profile_id || undefined,
          limit: 50,
          cursor: more && C.next_cursor ? C.next_cursor : "0",
        }),
      }).then(function (data) {
        if (seq !== C.loadSeq || wid !== workspaceId()) return;
        var incoming = (data && data.items) || [];
        C.items = more ? C.items.concat(incoming) : incoming;
        C.total = data && typeof data.total === "number" ? data.total : C.items.length;
        C.next_cursor = data ? data.next_cursor : null;
        var alive = {};
        C.items.forEach(function (row) { alive[row.candidate_id] = true; });
        Object.keys(C.selected).forEach(function (cid) { if (!alive[cid]) delete C.selected[cid]; });
        C.loading = false;
        C.loadedFor = workspaceId();
        renderBody();
      }, function (err) {
        if (seq !== C.loadSeq || wid !== workspaceId()) return;
        C.loading = false;
        C.loadedFor = workspaceId();
        C.error = errorText(err);
        renderBody();
      });
    }

    function openCandidate(candidate) {
      var error = h("div.form-error.sync-form-error", { style: "display:none" });
      var configs = [];
      var configSelect = h("select.input", { "aria-label": "固定配置" });
      configSelect.disabled = !!fileConfig;
      var releaseSelect = h("select.input", { "aria-label": "软件版本" });
      var previewBox = h("div");
      var previewSeq = 0;
      var compareOffset = 0;
      var releaseSeq = 0;
      var configPath = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
        "/profiles/" + encodeURIComponent(candidate.profile_id) + "/configs";
      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }
      function loadReleases() {
        var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
        var seq = ++releaseSeq;
        util.clear(releaseSelect);
        releaseSelect.disabled = true;
        if (!config) return;
        MC.api.requestV3("GET", configPath + "/" + encodeURIComponent(config.config_id) + "/releases", {})
          .then(function (data) {
            if (seq !== releaseSeq || configSelect.value !== config.config_id) return;
            util.clear(releaseSelect);
            (data.items || []).forEach(function (release) {
              releaseSelect.appendChild(h("option", { value: release.release_id }, "版本 " + (release.version || release.release_id)));
            });
            releaseSelect.disabled = !releaseSelect.options.length;
          }, function (err) { if (seq === releaseSeq) showError(errorText(err)); });
      }
      function invalidatePreview() {
        previewSeq += 1;
        previewBox._decisions = {};
        util.clear(previewBox);
      }
      configSelect.addEventListener("change", function () { invalidatePreview(); loadReleases(); });
      releaseSelect.addEventListener("change", invalidatePreview);
      var modal = viewModal({
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
            h("button.btn.btn-sm", { type: "button", "data-sync-preview": "1" }, "生成差异预览")
          ),
          previewBox
        ),
        footer: function (close) {
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭")];
        },
      });
      modal.el.classList.add("sync-candidate-dialog");
      if (!candidate.file_version_id) {
        showError("该候选没有关联文件版本，无法生成参数差异（SDA 表候选需后续协议支持）");
      }
      MC.api.requestV3("GET", configPath, {}).then(function (data) {
        configs = (data.items || []).filter(function (item) { return !fileConfig || item.config_id === fileConfig; });
        util.clear(configSelect);
        configs.forEach(function (config) {
          configSelect.appendChild(h("option", { value: config.config_id }, (config.code || "") + " " + (config.name || config.config_id)));
        });
        if (configs.length) loadReleases();
      }, function (err) { showError(errorText(err)); });

      function renderPreview(preview, keepDecisions, targetConfig, targetRelease) {
        util.clear(previewBox);
        var items = preview.items || [];
        var decisions = keepDecisions ? (previewBox._decisions || {}) : {};
        previewBox._decisions = decisions;
        var selects = {};
        var conflicts = items.filter(function (item) { return item.action === "conflict"; })
          .map(function (item) { return item.parameter_id; });
        var base = preview.base || [];
        var applying = false;
        Object.keys(decisions).forEach(function (pid) {
          var row = items.filter(function (it) { return it.parameter_id === pid; })[0];
          if (!row || (row.allowed && row.allowed.length && row.allowed.indexOf(decisions[pid]) < 0)) delete decisions[pid];
        });

        function pendingCount() {
          return conflicts.filter(function (pid) { return !decisions[pid]; }).length;
        }
        var applyBtn = null;
        var clearBtn = h("button.btn.btn-sm", {
          type: "button",
          "data-sync-clear-decisions": "1",
          disabled: true,
          onclick: function () { clearAll(); },
        }, "清空决议");
        var pendingHint = h("span.xsmall.muted", { "data-sync-pending": "1" });
        function refreshApplyState() {
          var left = pendingCount();
          if (pendingHint) {
            pendingHint.textContent = left
              ? "还有 " + left + " 项冲突待决议（可逐项选择，或用上方批量决议）"
              : "冲突已全部决议，可以写入草稿";
          }
          if (clearBtn) clearBtn.disabled = applying || !Object.keys(decisions).length;
          if (!applyBtn) return;
          var blocked = (preview.blockers || []).filter(function (row) {
            return row.code !== "d0_frozen";
          });
          applyBtn.disabled = applying || !canWrite() || left > 0 ||
            (preview.can_apply === false && !preview.d0_frozen && conflicts.length === 0) ||
            blocked.length > 0;
        }

        function setAll(value) {
          if (applying) return;
          var skipped = 0;
          conflicts.forEach(function (pid) {
            var select = selects[pid];
            var row = items.filter(function (item) { return item.parameter_id === pid; })[0];
            var allowed = ((row.allowed && row.allowed.length) ? row.allowed : ["use_new", "inherit_old", "manual"]).indexOf(value) >= 0;
            if (!allowed) { skipped++; return; }
            decisions[pid] = value;
            if (select) select.value = value;
          });
          refreshApplyState();
          batchHint.textContent = skipped ? skipped + " 项不支持此决议，保留原有选择；请逐项确认。" : "已应用到所有冲突项。";
        }
        function clearAll() {
          if (applying) return;
          Object.keys(decisions).forEach(function (pid) { delete decisions[pid]; });
          Object.keys(selects).forEach(function (pid) { selects[pid].value = ""; });
          batchHint.textContent = "";
          refreshApplyState();
        }

        var rows = items.slice(compareOffset, compareOffset + 30).map(function (item) {
          var cell;
          if (item.action === "conflict") {
            var allowed = (item.allowed && item.allowed.length)
              ? item.allowed
              : ["use_new", "inherit_old", "manual"];
            var sel = h("select.input", { "data-sync-decision": item.parameter_id, "aria-label": "参数 " + (item.name || item.parameter_id) + " 的冲突决议" },
              h("option", { value: "" }, "请选择"),
              allowed.map(function (value) {
                return h("option", { value: value }, DECISION_LABEL[value] || value);
              })
            );
            if (decisions[item.parameter_id]) {
              sel.value = decisions[item.parameter_id];
              if (!sel.value) delete decisions[item.parameter_id];
            }
            sel.addEventListener("change", function () {
              if (sel.value) decisions[item.parameter_id] = sel.value;
              else delete decisions[item.parameter_id];
              refreshApplyState();
            });
            selects[item.parameter_id] = sel;
            cell = sel;
          } else if (item.action === "manual") {
            cell = h("span.badge.badge-warn", "需人工处理");
          } else if (item.action === "keep") {
            cell = h("span.muted.small", "保留当前");
          } else if (item.action === "drop") {
            cell = h("span.small", "移除");
          } else if (item.action === "from_new") {
            cell = h("span.muted.small", "采用新值");
          } else {
            cell = h("span.muted.small", "待确认");
          }
          var baseValue = (item.base && item.base.value !== undefined)
            ? item.base.value
            : (Array.isArray(base) ? (base.filter(function (row) { return row.parameter_id === item.parameter_id; })[0] || {}).value : (base[item.parameter_id] || {}).value);
          function valueText(value) {
            if (value === undefined) return "未设";
            if (value === null) return "null";
            if (typeof value === "object" && typeof value.type === "string" && typeof value.text === "string") {
              if (value.type === "null") return "null";
              var shown = value.type === "string" ? JSON.stringify(value.text) : value.text;
              return shown + (value.unit ? " " + value.unit : "");
            }
            return JSON.stringify(value);
          }
          return h("tr", { "data-sync-diff": item.parameter_id },
            h("td", h("div.small", item.name || item.parameter_id), h("div.xsmall.muted", item.parameter_id)),
            h("td", h("span.badge", { keep: "保留", from_new: "采用新值", drop: "移除", conflict: "冲突", manual: "人工处理" }[item.action] || item.action)),
            h("td", h("span.xsmall.mono", preview.base_known === false ? "原来源未知" : valueText(baseValue))),
            h("td", h("span.xsmall.mono", valueText((preview.saved && preview.saved[item.parameter_id] || {}).value))),
            h("td", h("span.xsmall.mono", valueText(item.ours ? item.ours.value : undefined))),
            h("td", h("span.xsmall.mono", valueText(item.theirs ? item.theirs.value : undefined))),
            h("td", cell)
          );
        });

        var batchHint = h("span.xsmall.muted", { "aria-live": "polite", "data-sync-batch-hint": "1" });
        if (preview.base_known === false) previewBox.appendChild(h("p.banner.banner-warning", "此候选缺少原来源快照。与本地不同的值需要明确选择，不能自动覆盖。"));
        previewBox.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" },
          h("span.small.muted", "计划 " + (preview.summary ? JSON.stringify(preview.summary) : "")),
          h("button.btn.btn-sm", { type: "button", "data-sync-all-new": "1", disabled: !conflicts.length, onclick: function () { setAll("use_new"); } }, "全部采用新值"),
          h("button.btn.btn-sm", { type: "button", "data-sync-all-old": "1", disabled: !conflicts.length, onclick: function () { setAll("inherit_old"); } }, "全部保留当前"),
          clearBtn, batchHint
        ));
        previewBox.appendChild(h("div.sync-diff-wrap", h("table.table",
          h("thead", h("tr",
            h("th", "参数"), h("th", "变化"), h("th", "原来源"), h("th", "正式配置"), h("th", "当前草稿"), h("th", "新来源"), h("th", "处理方式")
          )),
          h("tbody", rows)
        )));
        var pages = Math.max(1, Math.ceil(items.length / 30));
        if (pages > 1) previewBox.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          h("button.btn.btn-sm", { type: "button", disabled: compareOffset <= 0, onclick: function () { compareOffset = Math.max(0, compareOffset - 30); renderPreview(preview, true, targetConfig, targetRelease); } }, "上一页"),
          h("span.small.muted", "第 " + (Math.floor(compareOffset / 30) + 1) + " / " + pages + " 页 · 共 " + items.length + " 项"),
          h("button.btn.btn-sm", { type: "button", disabled: compareOffset + 30 >= items.length, onclick: function () { compareOffset += 30; renderPreview(preview, true, targetConfig, targetRelease); } }, "下一页")));
        if (preview.blockers && preview.blockers.length) {
          previewBox.appendChild(h("div.form-error", { "data-sync-blockers": "1" },
            preview.blockers.map(function (row) { return row.message || row.code; }).join("；")));
        }
        if (preview.d0_frozen) {
          previewBox.appendChild(h("p.small", preview.editing_draft_id ? "当前草稿已纳入比较。确认后只写入选中的值，冲突项目需要明确选择。" : "确认后建立编辑草稿；已保存的配置和历史保持不变。"));
        }

        var frozen = !!preview.d0_frozen;
        applyBtn = h("button.btn.btn-primary", {
          type: "button",
          "data-sync-apply": "1",
          onclick: function () {
            if (!canWrite() || applying) return;
            var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
            if (!config || config.config_id !== targetConfig || releaseSelect.value !== targetRelease) { invalidatePreview(); showError("目标已变化，请重新预览后写入。"); return; }
            var payload = {};
            Object.keys(decisions).forEach(function (pid) { payload[pid] = decisions[pid]; });
            applying = true;
            refreshApplyState();
            Object.keys(selects).forEach(function (pid) { selects[pid].disabled = true; });
            Array.prototype.forEach.call(previewBox.querySelectorAll("[data-sync-all-new], [data-sync-all-old]"), function (button) { button.disabled = true; });
            modal.el.querySelector("[data-sync-preview]").disabled = true;
            configSelect.disabled = true;
            releaseSelect.disabled = true;
            // 同一预览复用同一 request_id：重试变成幂等重放，不会重复写入。
            var requestId = "apply-" + preview.preview_id;
            MC.api.requestV3("POST", candidatesPath() + "/" + encodeURIComponent(candidate.candidate_id) + "/apply", {
              body: {
                preview_id: preview.preview_id,
                request_id: requestId,
                decisions: payload,
                create_baseline: frozen,
              },
            }).then(function (result) {
              configSelect.disabled = !!fileConfig;
              releaseSelect.disabled = false;
              modal.el.querySelector("[data-sync-preview]").disabled = false;
              showError("");
              var done = h("div.card", h("div.card-b", h("div.col", { style: "gap:8px" },
                h("p", { "data-sync-applied": "1" },
                  "已写入编辑草稿"),
                h("p.xsmall.muted", "请核对并保存配置；未保存的参数变化仍保留在待处理中。"),
                h("button.btn.btn-sm", {
                  type: "button",
                  "data-sync-goto-v3": "1",
                  onclick: function () {
                    if (app && app.go) {
                      modal.close();
                      app.go({
                        kind: "configv3",
                        query: {
                          config: config.config_id,
                          release: targetRelease,
                          draft: result.draft_id || "d0-" + targetRelease,
                          tab: "workbench",
                        },
                      });
                    }
                  },
                }, "核对并保存配置")
              )));
              previewBox.appendChild(done);
              loadCandidates(true);
            }, function (err) {
              applying = false;
              Object.keys(selects).forEach(function (pid) { selects[pid].disabled = false; });
              Array.prototype.forEach.call(previewBox.querySelectorAll("[data-sync-all-new], [data-sync-all-old]"), function (button) { button.disabled = !conflicts.length; });
              configSelect.disabled = !!fileConfig;
              releaseSelect.disabled = false;
              modal.el.querySelector("[data-sync-preview]").disabled = false;
              refreshApplyState();
              if (err && err.code === "REVISION_CONFLICT") {
                showError("草稿或 D0 已在其他客户端变化（409）；已选决议仍保留，请重新预览后继续。");
                var rePreview = h("button.btn.btn-sm", {
                  type: "button",
                  "data-sync-repreview": "1",
                  onclick: function () {
                    requestPreview(true);
                  },
                }, "重新预览并套用原决议");
                previewBox.appendChild(rePreview);
                return;
              }
              showError(errorText(err));
            });
          },
        }, "写入编辑草稿");
        previewBox.appendChild(h("div.row", { style: "gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center" },
          applyBtn, pendingHint,
          h("span.xsmall.muted", "写入草稿后仍需核对并保存")
        ));
        refreshApplyState();
      }

      function requestPreview(keepDecisions) {
        var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
        if (!config || !releaseSelect.value) { showError("先选择固定配置与软件版本"); return; }
        var targetConfig = config.config_id;
        var targetRelease = releaseSelect.value;
        var seq = ++previewSeq;
        showError("");
        if (!keepDecisions) util.clear(previewBox);
        compareOffset = 0;
        MC.api.requestV3("POST", candidatesPath() + "/" + encodeURIComponent(candidate.candidate_id) + "/apply-preview", {
          body: { config_id: targetConfig, draft_id: "d0-" + targetRelease },
        }).then(function (preview) {
          if (seq !== previewSeq || configSelect.value !== targetConfig || releaseSelect.value !== targetRelease) return;
          var kept = keepDecisions ? (previewBox._decisions || {}) : {};
          previewBox._decisions = kept;
          renderPreview(preview, true, targetConfig, targetRelease);
        }, function (err) {
          if (seq !== previewSeq) return;
          showError(errorText(err));
          if (!keepDecisions) util.clear(previewBox);
        });
      }

      modal.el.querySelector("[data-sync-preview]").addEventListener("click", function () { requestPreview(false); });
    }

    function selectedCandidates() {
      var C = state.candidates;
      return C.items.filter(function (row) { return C.selected[row.candidate_id]; });
    }

    function batchApplyCandidates() {
      var C = state.candidates;
      var rows = selectedCandidates();
      if (!rows.length || C.busy) return;
      var profiles = [];
      rows.forEach(function (row) {
        if (profiles.indexOf(row.profile_id) < 0) profiles.push(row.profile_id);
      });
      if (profiles.length > 1) {
        MC.toast.error("批量写入要求同一机型", "已选候选分属 " + profiles.length + " 个机型，请分开处理");
        return;
      }
      var pid = profiles[0];
      var batchWid = workspaceId();
      var batchEpoch = MC.workspaces && MC.workspaces.epoch ? MC.workspaces.epoch() : null;
      function batchCurrent() {
        return workspaceId() === batchWid && (batchEpoch === null || !MC.workspaces || !MC.workspaces.epoch || MC.workspaces.epoch() === batchEpoch);
      }
      var configPath = "/api/v3/workspaces/" + encodeURIComponent(batchWid) +
        "/profiles/" + encodeURIComponent(pid) + "/configs";
      var configSelect = h("select.input", { "data-sync-batch-config": "1" },
        h("option", { value: "" }, "正在读取配置…"));
      var releaseSelect = h("select.input", { "data-sync-batch-release": "1" },
        h("option", { value: "" }, "选择软件版本…"));
      var configs = [];
      var releases = [];
      function fillReleases() {
        util.clear(releaseSelect);
        var rows2 = releases.filter(function (row) { return row.config_id === configSelect.value; });
        if (!rows2.length) { releaseSelect.appendChild(h("option", { value: "" }, "该配置没有版本")); return; }
        rows2.forEach(function (row) {
          releaseSelect.appendChild(h("option", { value: row.release_id }, "版本 " + (row.version || row.release_id)));
        });
      }
      configSelect.addEventListener("change", fillReleases);
      var modal = viewModal({
        title: "批量写入草稿（" + rows.length + " 个候选）",
        subtitle: "目标机型 " + pid,
        body: h("div.col", { style: "gap:12px" },
          h("p.small.muted", "批量写入只处理无未决冲突的候选；有冲突的候选会列出来，请在单候选抽屉里逐项决议。"),
          h("div.row", { style: "gap:8px;flex-wrap:wrap" },
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "固定配置"), configSelect),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "软件版本"), releaseSelect)
          )
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-sync-batch-apply": "1",
              onclick: function () {
                if (!configSelect.value || !releaseSelect.value) return;
                if (!batchCurrent()) { close(); MC.toast.warning("工作区已切换", "请在目标工作区重新选择候选。"); return; }
                close();
                runBatchApply(rows, configSelect.value, "d0-" + releaseSelect.value, batchWid, batchCurrent);
              },
            }, "开始批量写入"),
          ];
        },
      });
      MC.api.requestV3("GET", configPath, {}).then(function (data) {
        configs = ((data && data.items) || []).filter(function (item) { return !fileConfig || item.config_id === fileConfig; });
        configSelect.disabled = !!fileConfig;
        util.clear(configSelect);
        configSelect.appendChild(h("option", { value: "" }, configs.length ? "选择固定配置…" : "该机型没有固定配置"));
        configs.forEach(function (config) {
          configSelect.appendChild(h("option", { value: config.config_id }, (config.code || "") + " " + (config.name || config.config_id)));
        });
        var pending = configs.map(function (config) {
          return MC.api.requestV3("GET", configPath + "/" + encodeURIComponent(config.config_id) + "/releases", {})
            .then(function (res) {
              ((res && res.items) || []).forEach(function (row) {
                releases.push({ config_id: config.config_id, release_id: row.release_id, version: row.version });
              });
            }, function () { /* 单个配置读取失败不影响其他 */ });
        });
        return Promise.all(pending);
      }).then(fillReleases, function (err) {
        MC.toast.error("配置读取失败", errorText(err));
      });
      return modal;
    }

    function runBatchApply(rows, configId, draftId, wid, isCurrent) {
      var C = state.candidates;
      C.busy = "apply";
      renderBody();
      var stats = { ok: 0, skipped: [], failed: [], stopped: false };
      var seq = Promise.resolve();
      rows.forEach(function (row) {
        seq = seq.then(function () {
          if (stats.stopped || !isCurrent()) { stats.stopped = true; return; }
          var base = "/api/v3/workspaces/" + encodeURIComponent(wid) + "/sync/candidates/" + encodeURIComponent(row.candidate_id);
          return MC.api.requestV3("POST", base + "/apply-preview", {
            body: { config_id: configId, draft_id: draftId },
          }).then(function (preview) {
            if (!isCurrent()) { stats.stopped = true; return; }
            if (!preview) return;
            if (preview.d0_frozen) { stats.skipped.push(row.candidate_id + "：D0 已冻结"); return; }
            var needs = (preview.needs_decision || []).length ||
              (preview.items || []).filter(function (item) { return item.action === "conflict"; }).length;
            if (needs) { stats.skipped.push(row.candidate_id + "：有未决冲突"); return; }
            if (preview.can_apply === false) { stats.skipped.push(row.candidate_id + "：预览不可应用"); return; }
            return MC.api.requestV3("POST", base + "/apply", {
              body: { preview_id: preview.preview_id, request_id: "batch-" + preview.preview_id, decisions: {} },
            }).then(function () {
              if (!isCurrent()) { stats.stopped = true; return; }
              stats.ok += 1;
              row.state = "applied";
            }, function (err) {
              if (!isCurrent() || (err && err.code === "STALE_CONTEXT")) { stats.stopped = true; return; }
              stats.failed.push(row.candidate_id + "：" + errorText(err));
            });
          }, function (err) {
            if (!isCurrent() || (err && err.code === "STALE_CONTEXT")) { stats.stopped = true; return; }
            stats.failed.push(row.candidate_id + "：" + errorText(err));
          });
        });
      });
      seq.then(function () {
        C.busy = "";
        if (stats.stopped || !isCurrent()) {
          MC.toast.warning("工作区 " + wid + " 的批量写入已停止", "尚未提交的候选不会继续处理；请回到原工作区核对已提交项的结果。");
          return;
        }
        var parts = ["成功 " + stats.ok + " 个"];
        if (stats.skipped.length) parts.push("需人工 " + stats.skipped.length + " 个");
        if (stats.failed.length) parts.push("失败 " + stats.failed.length + " 个");
        var detail = stats.skipped.concat(stats.failed).join("；");
        if (stats.failed.length) {
          MC.toast.error("批量写入完成（" + parts.join("，") + "）", detail);
        } else if (stats.skipped.length) {
          MC.toast.error("批量写入完成，部分候选需人工决议", detail);
        } else {
          MC.toast.success("批量写入完成（" + parts.join("，") + "）");
        }
        C.selected = {};
        loadCandidates(true);
      });
    }

    function renderCandidates() {
      var C = state.candidates;
      var toolbar = h("div.row.sync-filters", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var stateSelect = h("select.input", { "data-sync-candidate-state": "1", "aria-label": "候选状态" },
        h("option", { value: "" }, fileConfig ? "待处理" : "全部状态"),
        fileConfig ? h("option", { value: "all" }, "全部状态") : null,
        Object.keys(CANDIDATE_STATE_LABEL).map(function (key) {
          return h("option", { value: key }, CANDIDATE_STATE_LABEL[key]);
        })
      );
      stateSelect.value = C.state;
      stateSelect.addEventListener("change", function () { C.state = stateSelect.value; loadCandidates(true); });
      var profileSelect = h("select.input", { "data-sync-candidate-profile": "1", "aria-label": "候选机型" },
        h("option", { value: "" }, "全部机型"),
        workspaceProfiles().map(function (profile) { return h("option", { value: profile.id }, profile.name); })
      );
      profileSelect.value = C.profile_id;
      profileSelect.addEventListener("change", function () { C.profile_id = profileSelect.value; loadCandidates(true); });
      toolbar.appendChild(stateSelect);
      if (!fileConfig) toolbar.appendChild(profileSelect);
      toolbar.appendChild(h("button.btn.btn-sm", {
        type: "button", "data-sync-candidate-refresh": "1", onclick: function () { loadCandidates(true); },
      }, "刷新"));

      if (!C.error && !C.loading) toolbar.appendChild(h("span.sync-result-count.muted.small", "共 " + C.total + " 条"));
      if (C.error) body.appendChild(h("div.form-error", C.error));
      if (C.loading && !C.items.length) { body.appendChild(h("div.card", h("div.card-b", toolbar, h("p.muted", "正在读取候选…")))); return; }
      if (C.error && !C.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar)));
        return;
      }

      var selectable = C.items.filter(function (row) { return row.state !== "superseded"; });
      var chosen = selectable.filter(function (row) { return C.selected[row.candidate_id]; });
      var batchBar = h("div.row.sync-selection", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px", "data-sync-candidate-batch": "1" });
      batchBar.hidden = !chosen.length;
      if (canWrite() && selectable.length) {
        batchBar.appendChild(h("span.small.muted", "已选 " + chosen.length + " / " + selectable.length));
        batchBar.appendChild(h("button.btn.btn-sm.btn-primary", {
          type: "button",
          "data-sync-candidate-batch-apply": "1",
          disabled: !chosen.length || !!C.busy,
          onclick: batchApplyCandidates,
        }, C.busy ? "正在批量写入…" : "批量写入草稿"));
      }
      if (!C.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar, batchBar,
          emptyState(C.state ? "没有匹配的更新候选" : "没有待处理的更新候选", "来源出现新内容后在此审阅；确认只写入草稿，发布仍需显式操作。"))));
        return;
      }
      var headerBox = h("input", {
        type: "checkbox",
        "data-sync-candidate-select-all": "1",
        "aria-label": "全选当前页候选",
        checked: selectable.length > 0 && chosen.length === selectable.length,
      });
      headerBox.addEventListener("change", function () {
        selectable.forEach(function (row) {
          if (headerBox.checked) C.selected[row.candidate_id] = true;
          else delete C.selected[row.candidate_id];
        });
        renderBody();
      });
      var cards = h("div.sync-candidate-cards");
      C.items.forEach(function (candidate) {
        var checkbox = candidate.state === "superseded" ? null : h("input", {
            type: "checkbox",
            "data-sync-candidate-select": candidate.candidate_id,
            "aria-label": "选择候选 " + candidate.candidate_id,
            checked: !!C.selected[candidate.candidate_id],
            onclick: function (event) { event.stopPropagation(); },
            onchange: function (event) {
              if (event.currentTarget.checked) C.selected[candidate.candidate_id] = true;
              else delete C.selected[candidate.candidate_id];
              renderBody();
            },
          });
        var title = h("button.btn.btn-ghost.sync-candidate-title", { type: "button", onclick: function () { openCandidate(candidate); } }, candidate.source_id || candidate.candidate_id);
        var card = h("article.sync-candidate-card", { "data-sync-candidate": candidate.candidate_id },
          h("div.sync-candidate-heading", checkbox, title,
            h("span.badge", { awaiting_save: "草稿待保存", saved: "已保存配置", needs_review: "待核对" }[candidate.review_state] || CANDIDATE_STATE_LABEL[candidate.state] || candidate.state)),
          h("div.small.muted", "机型 " + (candidate.profile_id || "—") + " · " + util.formatTime(candidate.created_at)),
          h("div.xsmall.muted", candidate.file_version_id ? "关联文件版本 " + candidate.file_version_id : "无文件版本"),
          h("div.xsmall.muted", "内容指纹 ", h("code", candidate.content_hash || "—")),
          h("div.xsmall.muted", candidate.applied_at ? "已写入修订 " + candidate.applied_revision : "尚未写入草稿"),
          h("div.sync-candidate-foot", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { openCandidate(candidate); } }, "查看参数差异"),
            h("code.xsmall.muted", candidate.candidate_id))
        );
        card.addEventListener("click", function (event) {
          if (event.target.closest("button, input, label")) return;
          openCandidate(candidate);
        });
        cards.appendChild(card);
      });
      body.appendChild(h("div.card",
        h("div.card-b",
          toolbar,
          batchBar,
          h("label.row.sync-candidate-select-all", headerBox, h("span.small.muted", "选择当前已加载候选")),
          h("div.sync-card-scroll", cards),
          h("div.sync-list-footer", h("span.muted.xsmall", "已加载 " + C.items.length + " / " + C.total),
          C.next_cursor ? h("button.btn.btn-sm", {
            type: "button", onclick: function () { loadCandidates(false, true); },
          }, "加载更多") : null
        ))
      ));
    }

    var sheet = h("div.sync-sheet");
    sheet.hidden = true;
    var sheetModal = null;
    var sheetHost = null;

    function paintSheet() {
      if (!state.sheet) {
        if (sheetModal) sheetModal.close();
        sheet.hidden = true;
        return;
      }
      sheet.hidden = false;
      if (!sheetModal) {
        sheetHost = h("div.sync-sheet-card");
        sheetModal = viewModal({
          title: state.sheet === "plan" ? "更新计划与全量预览" : "连接设置",
          body: sheetHost,
          size: "wide",
          onClose: function () {
            sheetModal = null;
            sheetHost = null;
            state.sheet = "";
            sheet.hidden = true;
          },
          footer: function (close) {
            return [h("button.btn", { type: "button", "data-sync-sheet-close": "1", onclick: close }, "关闭")];
          },
        });
        sheetModal.el.classList.add("sync-settings-dialog");
        sheet.appendChild(sheetModal.el.parentNode);
      }
      // Keep the dialog and its focus scope stable across async updates.
      util.clear(sheetHost);
      var previous = body;
      body = sheetHost;
      if (state.sheet === "plan") renderPlan();
      else if (state.sheet === "connections") renderConnections();
      body = previous;
      var footer = sheetModal.el.querySelector(".modal-footer");
      Array.prototype.forEach.call(footer.querySelectorAll("[data-hb-save]"), function (button) { button.remove(); });
      if (state.sheet === "connections") {
        var save = sheetHost.querySelector("[data-hb-save]");
        if (save) { save.classList.add("btn-primary"); footer.appendChild(save); }
      }
    }

    function showSheet(kind) {
      if (sheetModal && kind && state.sheet !== kind) sheetModal.close();
      state.sheet = kind || "";
      paintSheet();
      if (state.sheet === "plan" && state.plan.loadedFor !== workspaceId() && !state.plan.loading) {
        loadPlan();
      }
      if (state.sheet === "connections" && state.connections.loadedFor !== workspaceId() && !state.connections.loading) {
        loadConnections(true);
      }
    }

    function renderBody() {
      util.clear(body);
      if (fileConfig && fileError) { body.appendChild(MC.stateBlock({ kind: "error", title: "无法打开此文件的更新管理", desc: errorText(fileError), actions: [h("button.btn", { type: "button", onclick: loadFileContext }, "重试")] })); return; }
      var tab = tabById(state.tab);
      if (tab.id === "runs") {
        renderRuns();
      } else if (tab.id === "candidates") {
        renderCandidates();
        if (state.candidates.loadedFor !== workspaceId() && !state.candidates.loading) {
          loadCandidates(true);
        }
      } else {
        renderSources();
        var recent = state.recentRun;
        if (!fileConfig && recent && recent.run_id && (!recent.workspace_id || recent.workspace_id === workspaceId())) body.appendChild(h("div.sync-recent-run",
          h("strong.small", "最近运行"), h("span.small.muted", util.formatTime(recent.created_at)),
          h("span.small", ({ manual: "手动更新", schedule: "定时更新", retry: "失败重试", collector: "采集更新" }[recent.trigger] || "更新任务") + " · " + runStateText(recent.state)),
          h("span.xsmall.muted", runCounts(recent)), h("span.spacer"),
          h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { openRun(recent); } }, "查看详情")));
      }
      if (state.sheet) paintSheet();
    }

    var headActions = h("div.sync-head-actions");
    function renderHead() {
      util.clear(headActions);
      if (canWrite()) {
        headActions.appendChild(h("button.btn.btn-sm", {
          type: "button",
          "data-sync-new": "1",
          disabled: !!fileConfig && !fileDetail,
          onclick: function () {
            if (!fileConfig) { openEditor(null); return; }
            var modal = viewModal({ title: "更新设置", body: h("div.col", { style: "gap:10px" },
              state.sources.items.map(function (source) { return h("button.btn", { type: "button", onclick: function () { modal.close(); openEditor(source); } }, source.name); }),
              h("button.btn", { type: "button", onclick: function () { modal.close(); openEditor(null); } }, "添加更新来源"),
              h("button.btn.btn-ghost", { type: "button", onclick: function () { modal.close(); showSheet("connections"); } }, "连接设置"),
              h("button.btn.btn-ghost", { type: "button", onclick: function () { modal.close(); showSheet("plan"); } }, "工作区计划")) });
          },
        }, fileConfig ? "更新设置" : "新增更新源"));
      }
      if (canRun()) {
        headActions.appendChild(h("button.btn.btn-sm.btn-primary", {
          type: "button",
          "data-sync-all": "1",
          disabled: !!fileConfig && !fileDetail,
          onclick: function () { previewSources(null); },
        }, fileConfig ? "立即更新" : "工作区全量更新"));
      }
    }
    function renderStrip() {
      var plan = state.plan.policy;
      var hours = plan && plan.default_interval_hours != null ? plan.default_interval_hours : 6;
      var auto = plan ? !!plan.enabled : false;
      var conn = (state.connections.items || [])[0];
      var hb = conn && state.connections.heartbeats[conn.connection_id];
      var connText = !conn
        ? (state.connections.error ? "读取失败" : (state.connections.loading ? "正在读取…" : "未配置"))
        : (conn.name + (hb && hb.enabled ? " · 保活 " + (hb.interval_minutes || 10) + " 分钟" : " · 保活已关闭"));
      return h("div.sync-header-settings",
        h("button.btn.btn-sm", { type: "button", "data-sync-connections": "1", title: connText,
          onclick: function () { showSheet("connections"); } }, "连接设置"),
        h("button.btn.btn-sm", { type: "button", "data-sync-plan": "1",
          title: state.plan.error || "配置工作区自动更新计划", onclick: function () { showSheet("plan"); } },
          (fileConfig ? "工作区计划 · " : "自动更新 · ") + (plan ? (auto ? "每 " + hours + " 小时" : "已暂停") : state.plan.error ? "读取失败" : "读取中…")));
    }

    var stripHost = h("div");
    function paintChrome() {
      renderHead();
      util.clear(stripHost);
      if (!fileConfig) stripHost.appendChild(renderStrip());
    }
    el.appendChild(h("div.view-body.sync-page",
      fileNav,
      h("div.sync-head",
        h("div.sync-title", h("strong", "更新管理"), h("span.sync-caption", workspaceId())),
        h("div.sync-head-tools", stripHost, headActions)
      ),
      h("div.sync-workbench", tabbar, body),
      sheet
    ));
    var stopTaskUpdates = MC.bus.on("task:done", function (task) {
      if (!task || task.type !== "sync_run" || !el.isConnected || activeEditorDirty || planDirty() || document.querySelector(".modal-backdrop")) return;
      // Read the current workspace again; never insert the event's potentially stale result data.
      if (state.tab === "sources" && !state.sources.expanded) { loadSources(true); loadRecentRun(); }
      if (state.tab === "runs" && !state.runs.expanded) loadRuns(true);
      if (state.tab === "candidates") loadCandidates(true);
    });
    loadFileContext();
    renderTabs();
    paintChrome();
    renderBody();
    if (state.sheet) showSheet(state.sheet);
    if (state.tab === "sources") { loadSources(true); loadRecentRun(); }
    if (state.tab === "runs") loadRuns(true);
    if (!state.connections.loading) loadConnections(true);
    if (state.plan.loadedFor !== workspaceId() && !state.plan.loading) loadPlan();
    maybeRequestedPreview(route);

    return {
      el: el,
      title: "更新管理",
      icon: "settings",
      update: function update(next, approved) {
        var nextConfig = next && next.query && next.query.config || "";
        if (nextConfig !== fileConfig && activeEditorDirty && !approved) {
          MC.confirm({ title: "放弃未保存的更新来源？", message: "当前输入尚未保存，切换文件后将丢失。", confirmText: "放弃并切换", cancelText: "继续编辑", danger: true }).then(function (ok) { if (ok && !disposed) { activeEditorDirty = false; update(next, true); } }); return;
        }
        if (nextConfig !== fileConfig) {
          contextVersion++; dialogs.slice().forEach(function (dialog) { dialog.close(); });
          fileConfig = nextConfig; state.sources.loadSeq++; state.runs.loadSeq++; state.candidates.loadSeq++;
          if (fileConfig) { state.sources.query = ""; state.sources.status = ""; state.sources.failedOnly = false; state.sources.candidateOnly = false; state.sources.expanded = false; state.sources.scope = "profile"; }
          state.sources.selected = {}; state.sources.items = []; state.sources.loading = false; state.sources.configuration_id = ""; state.sources.release_id = "";
          state.runs.items = []; state.runs.loading = false; state.recentRun = null;
          state.candidates.items = []; state.candidates.loading = false; state.candidates.loadedFor = ""; state.candidates.selected = {};
          loadFileContext();
        }
        state.tab = tabById(next && next.arg).id;
        if (sheetModal) sheetModal.close();
        if (state.plan.loadedFor !== workspaceId()) { state.plan = freshPlan(); state.recentRun = null; }
        state.sheet = sheetFor(next);
        renderTabs();
        paintChrome();
        renderBody();
        if (state.sheet) showSheet(state.sheet);
        if (state.tab === "sources") { loadSources(true); loadRecentRun(); }
        if (state.tab === "runs") loadRuns(true);
        if (state.tab === "candidates" && state.candidates.loadedFor !== workspaceId()) loadCandidates(true);
        if (state.plan.loadedFor !== workspaceId() && !state.plan.loading) loadPlan();
        maybeRequestedPreview(next);
      },
      isDirty: function () { return !!activeEditorDirty || planDirty() || Object.keys(connectionDrafts()).length > 0; },
      destroy: function () {
        disposed = true; contextVersion++; fileSeq++;
        state.sources.loadSeq++; state.runs.loadSeq++; state.candidates.loadSeq++; state.connections.loadSeq++;
        dialogs.slice().forEach(function (dialog) { dialog.close(); });
        stopTaskUpdates();
        if (sheetModal) sheetModal.close();
        activeEditorDirty = false;
        (state.connections.pollTimers || []).forEach(function (timer) { clearTimeout(timer); });
        state.connections.pollTimers = [];
      },
    };
  };
})(typeof window !== "undefined" ? window : this);
