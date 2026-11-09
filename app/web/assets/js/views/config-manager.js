/*
 * V3 配置管理工作台（M12-07 / INH-2142）。
 * 唯一配置域入口：固定配置 → 软件版本 → canonical D0 → Bn / 机台 rn。
 * 页面结构与所选修订遵循用户指定的 V3.1 HTML；数据来自配置域 API。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  function enc(v) { return encodeURIComponent(String(v)); }

  MC.views.configv3 = function (app, route) {
    var el = h("div.view.config-root");
    // M16-36：支持从更新候选"写入草稿"后深链直达指定配置/版本（不自动发布）。
    var q = (route && route.query) || {};
    var surface = route && route.arg || q.surface || "configs";
    if (["configs", "files", "records"].indexOf(surface) < 0) surface = "configs";
    var catalogPage = { loaded: "", loading: false, items: [], total: 0, offset: 0, query: "", error: null, seq: 0 };
    var disposed = false, dialogs = [], formDrafts = [];
    function viewModal(options) {
      var onClose = options.onClose, modal;
      options.onClose = function (result) { dialogs = dialogs.filter(function (item) { return item !== modal; }); if (onClose) onClose(result); };
      modal = MC.modal(options); modal.el.classList.add("cv-dialog"); modal.el.parentNode.classList.add("cv-dialog-backdrop"); dialogs.push(modal); return modal;
    }
    function viewConfirm(options) {
      var result = MC.confirm(options);
      var layers = document.querySelectorAll(".modal-backdrop"), layer = layers[layers.length - 1];
      if (layer) { layer.classList.add("cv-dialog-backdrop"); var dialog = layer.querySelector(".modal"); if (dialog) dialog.classList.add("cv-dialog"); }
      return result;
    }
    var loadSeq = 0, detailSeq = 0, selectedSeq = 0, overviewSeq = 0, parameterSeq = 0, directorySeq = 0, historySeq = 0;
    var S = {
      configs: [], cid: q.config || null, opened: !!q.config, selected: q.revision || q.draft || null, revisions: [], edits: {}, contextEpoch: 0,
      missingConfig: null, detail: null, graph: null, params: null, branch: null,
      machines: [], releaseId: q.release || null, tab: q.tab || "versions", draft: null, loading: true, error: null,
      filter: "", overview: null, overviewMachine: "", overviewCompare: "",
      overviewOffset: 0, overviewLimit: 100, overviewTotal: 0,
      paramMode: "list", selectedParam: null, paramTree: null,
      paramPage: null, paramTotal: 0, paramOffset: 0, paramLimit: 100, paramLoading: false, allParams: null,
      dirParent: "", dirData: null, dirLoading: false, dirError: null, dirOffset: 0,
      allLoading: false,
      exportFiles: null, exportFileId: "", exportRevisionId: "", exportPlan: null,
      exportArtifact: null, exportBusy: false, exportError: null,
      historyEvents: null, historyLoading: false, historyError: null, historyQ: "", dirty: false, onlyDiff: false,
    };
    var canWrite = function () { return app.can("config_registry_write"); };
    function root(rest) {
      return "/api/v3/workspaces/" + enc(MC.workspaces.scopeId()) + "/profiles/" + enc(app.pid) + rest;
    }
    function cfgPath(rest) { return root("/configs/" + enc(S.cid) + (rest || "")); }
    function release() {
      return (S.detail && S.detail.releases || []).filter(function (r) { return r.release_id === S.releaseId; })[0] || null;
    }
    function draftId() { return S.releaseId ? "d0-" + S.releaseId : ""; }
    function revision() { return S.revisions.filter(function (item) { return item.id === S.selected; })[0] || null; }
    function editable() { var item = revision(); return canWrite() && (!item || item.status === "draft"); }
    function revisionLabel(item) { return item ? (item.machine_no || "主线") + " / " + labelOf(item.release_id) + " / " + item.label : "尚无修订"; }
    function buildRevisions() {
      var result = [];
      (S.detail.releases || []).forEach(function (r) {
        result.push({ id: "d0-" + r.release_id, release_id: r.release_id, kind: "default", label: "D0", status: r.default_revision_id ? "published" : "draft", at: r.default_frozen_at || r.created_at });
        var draft = r.baseline_draft;
        if (draft && draft.status === "draft") result.push({ id: draft.draft_id, release_id: r.release_id, kind: "baseline", label: "基线草稿", status: "draft", source: draft.source_revision_id, at: draft.created_at });
      });
      ((S.branch && S.branch.baselines) || []).forEach(function (b) { result.push({ id: b.baseline_id, release_id: b.release_id, kind: "baseline", label: b.label, status: "published", source: b.source_revision_id, parent: b.parent_id, at: b.published_at }); });
      (S.machines || []).forEach(function (m) {
        (m.revisions || []).forEach(function (r) { result.push({ id: r.revision_id, release_id: r.release_id || m.release_id, kind: "machine", machine_id: m.machine_id, machine_no: m.machine_no, label: r.label, status: "published", source: r.base_revision_id, parent: r.parent_id, at: r.published_at }); });
        if (m.draft && m.draft.status === "draft") result.push({ id: m.draft.draft_id, release_id: m.release_id, kind: "machine", machine_id: m.machine_id, machine_no: m.machine_no, label: "机台草稿", status: "draft", source: m.draft.base_revision_id, parent: m.head_id, at: m.draft.created_at });
      });
      S.revisions = result;
      if (!revision()) S.selected = draftId();
      if (revision()) S.releaseId = revision().release_id;
    }
    function loadSelected() {
      var item = revision();
      if (!item) { S.draft = null; return Promise.resolve(); }
      var selectedId = item.id, scope = cfgPath("");
      var request = ++selectedSeq;
      function current() { return !disposed && request === selectedSeq && S.selected === selectedId && cfgPath("") === scope; }
      var basis = item.source ? api.getV3(scope + "/revisions/" + enc(item.source)).then(function (res) { if (current()) S.basis = (res.snapshot || {}).values || {}; }) : Promise.resolve().then(function () { if (current()) S.basis = null; });
      var bindings = api.getV3(scope + "/releases/" + enc(item.release_id) + "/bindings").then(function (res) { if (current()) S.bindingRevision = (res.binding_set || {}).revision; S.bindings = Object.keys((res.binding_set || {}).bindings || {}).map(function (id) { return res.binding_set.bindings[id]; }); });
      var snapshot;
      if (item.kind === "machine" && item.status === "draft") {
        S.draft = S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0].draft;
        snapshot = Promise.resolve();
      } else {
        var path = item.kind === "default" ? "/drafts/" + enc(item.id)
          : item.status === "draft" ? "/baseline-drafts/" + enc(item.id)
          : "/revisions/" + enc(item.id);
        snapshot = api.getV3(scope + path).then(function (data) { if (current()) S.draft = data.snapshot || data; });
      }
      return Promise.all([snapshot, basis, bindings]);
    }
    function chooseRevision(item, tab) {
      confirmDiscard(function () {
        if (S.releaseId !== item.release_id) { S.paramOffset = 0; S.overviewOffset = 0; S.overviewDirectory = ""; S.overviewQ = ""; S.overviewCompare = ""; S.overviewOnly = false; S.overviewColumns = {}; S.exportMappings = {}; }
        S.contextEpoch++; S.savingValues = false; S.valueError = null;
        S.selected = item.id; S.releaseId = item.release_id; S.edits = {}; S.exportPlan = null; S.exportArtifact = null;
        if (tab) S.tab = tab;
        S.loading = true; render();
        reloadDetail().then(function () { if (disposed) return; S.loading = false; S.focusRevision = S.tab === "versions"; render(); });
      });
    }
    function openConfig(id) {
      confirmDiscard(function () { S.contextEpoch++; clearConfigData(); S.opened = true; S.cid = id; S.tab = "versions";
        app.go({ kind: "configv3", arg: "configs", query: { config: id } }); load(id); });
    }
    function clearConfigData() {
      S.detail = null; S.selected = null; S.releaseId = null; S.revisions = []; S.draft = null; S.edits = {}; S.dirty = false; S.savingValues = false; S.valueError = null;
      S.paramOffset = 0; S.paramTotal = 0; S.paramPage = null; S.allParams = null; S.selectedParam = null; S.folderActive = false; S.dirData = null; S.dirParent = ""; S.dirOffset = 0; S.dirError = null;
      S.overview = null; S.overviewOffset = 0; S.overviewQ = ""; S.overviewDirectory = ""; S.overviewOnly = false; S.overviewCompare = ""; S.overviewMachine = ""; S.overviewColumns = {};
      S.historyEvents = null; S.historyLoading = false; S.historyError = null; S.historyQ = ""; S.historyOffset = 0; S.historyTotal = 0;
      S.exportFiles = null; S.exportFileId = ""; S.exportRevisionId = ""; S.exportPlan = null; S.exportArtifact = null; S.exportMappings = {}; S.exportError = null; S.exportBusy = false; S.exportDirty = false;
      S.paramLoading = false; S.dirLoading = false; S.allLoading = false; S.overviewError = null; S.folderCaches = null; S.parameterContext = null; S.bindingRevision = null; historySeq++;
    }

    function load(selectId) {
      if (typeof selectId !== "string") selectId = undefined;
      var seq = ++loadSeq, scope = root("");
      function current() { return !disposed && seq === loadSeq && scope === root(""); }
      S.loading = true;
      S.error = null;
      render();
      return api.getV3(scope + "/configs").then(function (res) {
        if (!current()) return;
        S.configs = res.items || [];
        var requested = selectId || S.cid;
        S.missingConfig = requested && !S.configs.some(function (item) { return item.config_id === requested; }) ? requested : null;
        var next = S.missingConfig ? null : requested || (S.configs[0] && S.configs[0].config_id);
        S.cid = next || null;
        if (!S.cid) { S.detail = null; S.releaseId = null; S.draft = null; }
        return S.opened && S.cid ? loadDetail() : null;
      }).then(function () {
        if (!current()) return;
        S.loading = false;
        render();
      }, function (err) {
        if (!current()) return;
        S.loading = false;
        S.error = err;
        render();
      });
    }

    function overviewQuery() {
      var q = {};
      if (S.overviewMachine) q.machines = S.overviewMachine;
      if (S.overviewCompare) q.compare_release_id = S.overviewCompare;
      q.offset = S.overviewOffset || 0;
      q.limit = S.overviewLimit || 100;
      q.catalog = "all";
      if (S.overviewQ) q.q = S.overviewQ;
      if (S.overviewDirectory) q.directory = S.overviewDirectory;
      if (S.overviewOnly && S.overviewCompare) q.only_changes = "true";
      if (S.overviewColumns) {
        if (S.overviewColumns.baseline) q.baselines = S.overviewColumns.baseline;
        var machineRevisions = Object.keys(S.overviewColumns).filter(function (key) { return key !== "baseline"; }).map(function (key) { return S.overviewColumns[key]; });
        if (machineRevisions.length) q.machine_revisions = machineRevisions.join(",");
      }
      return q;
    }
    function loadOverview() {
      if (!S.releaseId) { S.overview = null; return Promise.resolve(); }
      var scope = cfgPath("/releases/" + enc(S.releaseId) + "/overview"), seq = ++overviewSeq;
      function current() { return !disposed && seq === overviewSeq && scope === cfgPath("/releases/" + enc(S.releaseId) + "/overview"); }
      S.overviewError = null;
      return api.getV3(scope, overviewQuery())
        .then(function (res) {
          if (!current()) return;
          S.overview = res;
          S.overviewOffset = res.offset || 0;
          S.overviewTotal = res.total == null ? (res.rows || []).length : res.total;
        }, function (err) { if (current()) { S.overview = null; S.overviewError = err; } });
    }

    function loadDetail() {
      var seq = ++detailSeq, scope = cfgPath("");
      function current() { return !disposed && seq === detailSeq && scope === cfgPath(""); }
      return api.getV3(scope).then(function (res) {
        if (!current()) return;
        S.detail = res.config;
        var releases = (S.detail.releases || []).slice().sort(function (a, b) { return a.order - b.order; });
        if (!S.releaseId || !releases.some(function (r) { return r.release_id === S.releaseId; })) {
          S.releaseId = releases.length ? releases[releases.length - 1].release_id : null;
        }
        var jobs = [
          api.getV3(cfgPath("/graph")),
          api.getV3(cfgPath("/branches/main")),
          api.getV3(cfgPath("/machines")),
        ];
        return Promise.all(jobs).then(function (rows) {
          if (!current()) return;
          S.graph = rows[0];
          S.allParams = null;
          S.allLoading = false;
          S.dirData = null;
          S.branch = rows[1];
          S.machines = rows[2].items || [];
          buildRevisions();
          var parameterContext = scope + "|" + S.releaseId;
          if (S.parameterContext !== parameterContext) { S.paramOffset = 0; S.dirParent = ""; S.dirOffset = 0; S.folderActive = false; S.overviewColumns = {}; }
          S.parameterContext = parameterContext;
          return Promise.all([loadSelected(), api.getV3(cfgPath("/parameters") + "?offset=" + S.paramOffset + "&limit=" + S.paramLimit + "&release_id=" + enc(S.releaseId || "")).then(function (page) {
            if (!current()) return;
            S.paramPage = page; S.params = page; S.paramTotal = page.total == null ? (page.items || []).length : page.total; S.paramOffset = page.offset || 0;
          }), loadOverview(), S.folderActive ? loadDir(S.dirParent, S.dirOffset) : Promise.resolve()]);
        });
      });
    }

    function reloadDetail() {
      if (disposed) return Promise.resolve();
      var epoch = S.contextEpoch;
      return loadDetail().then(function () { if (!disposed && epoch === S.contextEpoch) { S.error = null; render(); } }, function (err) { if (!disposed && epoch === S.contextEpoch) { S.error = err; S.loading = false; render(); } });
    }

    function render() {
      if (S.paramTree) { S.paramTree.destroy(); S.paramTree = null; }
      util.clear(el);
      el.appendChild(MC.configWorkspaceNav(app, surface));
      if (surface !== "configs") { renderCatalogPage(); return; }
      if (S.opened && S.detail) el.appendChild(h("div.cv-breadcrumb", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { confirmDiscard(function () { S.opened = false; S.cid = null; S.detail = null; S.edits = {}; app.go({ kind: "configv3", arg: "configs", query: {} }); render(); }); } }, "固定配置"), h("span.muted", "/"), h("strong", S.detail.name), h("span.mono.small.muted", S.detail.code)));
      else el.appendChild(h("div.cv-page-heading", h("h1", "固定配置")));
      if (S.loading) { el.appendChild(MC.stateBlock({ kind: "loading", title: "加载配置…" })); return; }
      if (S.error) { el.appendChild(MC.errorBlock(S.error, load)); return; }
      if (S.missingConfig) {
        el.appendChild(MC.stateBlock({ kind: "empty", title: "该配置已不存在", desc: "可能已被删除，或不属于当前工作区与机型。", actions: [h("button.btn", { type: "button", onclick: function () { S.cid = null; S.opened = false; S.missingConfig = null; app.go({ kind: "configv3", arg: "configs", query: {} }); load(); } }, "查看配置列表")] }));
        return;
      }
      if (!canWrite()) {
        el.appendChild(h("div.banner.banner-warning", icon("info"), h("div.banner-body", "只读模式，编辑需要管理员权限。")));
      }
      if (!S.opened) { el.appendChild(configsPanel()); return; }
      el.appendChild(mainPanel());
      if (S.focusRevision) { S.focusRevision = false; var selectedNode = el.querySelector('.cv-graph-node[aria-pressed="true"]'); if (selectedNode) selectedNode.focus(); }
      if (S.focusTab) { S.focusTab = false; var activeTab = el.querySelector('.cv-tabs [aria-selected="true"]'); if (activeTab) activeTab.focus(); }
    }

    function loadCatalogPage(reset) {
      var target = surface, wid = MC.workspaces.scopeId();
      if (reset) catalogPage.offset = 0;
      var seq = ++catalogPage.seq;
      catalogPage.loading = true; catalogPage.error = null; catalogPage.loaded = target; render();
      api.getV3(root(target === "files" ? "/file-selector" : "/events"), {
        q: catalogPage.query, offset: catalogPage.offset, limit: 50,
      }).then(function (res) {
        if (disposed || seq !== catalogPage.seq || target !== surface || wid !== MC.workspaces.scopeId()) return;
        catalogPage.items = res.items || []; catalogPage.total = res.total == null ? catalogPage.items.length : res.total;
        catalogPage.loading = false; render();
      }, function (err) { if (disposed || seq !== catalogPage.seq || target !== surface) return; catalogPage.loading = false; catalogPage.error = err; render(); });
    }

    function renderCatalogPage() {
      var files = surface === "files", P = catalogPage;
      var query = h("input.input", { type: "search", "aria-label": files ? "搜索配置文件" : "搜索配置记录", placeholder: files ? "搜索文件名" : "搜索事件、参数或操作人", value: P.query });
      function search() { P.query = query.value.trim(); loadCatalogPage(true); }
      query.addEventListener("keydown", function (event) { if (event.key === "Enter") search(); });
      el.appendChild(h("div.view-header", h("h1", files ? "配置文件" : "配置记录")));
      var card = h("div.config-resource-surface", h("div.row.config-resource-toolbar", query,
        h("button.btn.btn-sm", { type: "button", onclick: search }, "搜索"),
        h("button.btn.btn-sm", { type: "button", onclick: function () { loadCatalogPage(false); } }, "刷新"),
        h("span.spacer"), h("span.small.muted", "共 " + P.total + " 项")));
      el.appendChild(card);
      if (P.loaded !== surface) { loadCatalogPage(true); return; }
      if (P.loading) { card.appendChild(MC.stateBlock({ kind: "loading", title: "正在读取…" })); return; }
      if (P.error) { card.appendChild(MC.errorBlock(P.error, function () { loadCatalogPage(false); })); return; }
      if (!P.items.length) { card.appendChild(MC.stateBlock({ kind: "empty", title: P.query ? "没有匹配结果" : (files ? "还没有配置文件" : "还没有配置记录"), desc: files ? "从左侧配置文件列表导入文件后，即可在这里查看。" : "配置的修改、导入、导出与恢复会记录在这里。" })); return; }
      var visible = P.items;
      var rows = visible.map(function (item) {
        if (files) return h("tr", h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.openFile(item.name); } }, item.name)),
          h("td", (item.format || "").toUpperCase()), h("td.mono.xsmall", item.version_id), h("td.xsmall", util.formatTime(item.updated_at)));
        return h("tr", h("td.xsmall", item.at), h("td", item.type), h("td", item.principal || "—"),
          h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "configs", query: { config: item.config_id } }); } }, item.config_id || "配置")));
      });
      card.appendChild(h("div.config-resource-scroll", h("table.table", h("thead", h("tr", (files ? ["文件名", "格式", "文件版本", "更新时间"] : ["时间", "事件", "操作人", "配置"]).map(function (label) { return h("th", label); }))), h("tbody", rows))));
      card.appendChild(h("div.row.config-resource-toolbar", h("span.small.muted", "第 " + (P.offset + 1) + "–" + Math.min(P.offset + visible.length, P.total) + " 项"), h("span.spacer"),
        h("button.btn.btn-sm", { type: "button", disabled: P.offset === 0, onclick: function () { P.offset = Math.max(0, P.offset - 50); loadCatalogPage(false); } }, "上一页"),
        h("button.btn.btn-sm", { type: "button", disabled: P.offset + 50 >= P.total, onclick: function () { P.offset += 50; loadCatalogPage(false); } }, "下一页")));
    }

    function configsPanel() {
      var search = h("input.input", { type: "search", "aria-label": "搜索固定配置", placeholder: "搜索配置名称或标识", value: S.filter });
      var results = h("div.cv-catalog-results");
      function draw() {
        var items = S.configs.filter(function (c) { return !S.filter || (c.name + " " + c.code).toLowerCase().indexOf(S.filter.toLowerCase()) >= 0; });
        util.clear(results);
        if (!items.length) {
          results.appendChild(MC.stateBlock({ kind: "empty", title: S.configs.length ? "没有匹配的配置" : "创建第一组固定配置", desc: S.configs.length ? "调整搜索条件后重试。" : "为需要长期维护的一组参数命名，再添加软件版本。",
            actions: S.configs.length ? [h("button.btn", { type: "button", onclick: function () { S.filter = ""; search.value = ""; draw(); } }, "清除搜索")] : null })); return;
        }
        var rows = items.map(function (c) {
          var r = c.latest_release;
          return h("tr", h("td", h("div.cv-resource-label", h("span.cv-resource-icon", icon("compare")), h("div.col", h("button.btn.btn-ghost.cv-object-link", { type: "button", onclick: function () { openConfig(c.config_id); } }, c.name), h("span.mono.small.muted", c.code)))),
            h("td", r ? h("div.col", h("div.row", { style: "gap:6px" }, h("strong", r.version), h("span.badge" + (r.frozen ? "" : ".badge-warning"), r.frozen ? "已确认" : "准备中")), h("span.small.muted", c.release_count + " 个软件版本")) : "尚无版本"),
            h("td", c.parameter_count == null ? "—" : c.parameter_count + " 项"), h("td", c.machine_count == null ? "—" : c.machine_count + " 台"),
            h("td", h("div.col", util.formatTime(c.updated_at), c.draft_count ? h("span.small.cv-warning", c.draft_count + " 份草稿待完成") : null)),
            h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { openConfig(c.config_id); } }, "打开配置", icon("arrow-right"))));
        });
        results.appendChild(h("div.cv-catalog-table", h("table.table", h("thead", h("tr", ["配置名称", "最新软件版本", "参数身份", "机台分支", "更新于", "操作"].map(function (label) { return h("th", label); }))), h("tbody", rows))));
        results.appendChild(h("div.cv-resource-footer", h("span.small.muted", "共 " + items.length + " 组配置"), h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "files" }); } }, "上传文件", icon("arrow-right"))));
      }
      search.addEventListener("input", function () { S.filter = search.value; draw(); }); draw();
      return h("div.cv-catalog", h("div.cv-toolbar", search, h("span.small.muted", S.configs.length + " 组配置"), h("span.spacer"),
        h("button.btn.btn-primary", { type: "button", disabled: !canWrite(), onclick: createConfigDialog }, icon("plus"), "新建固定配置")), results);
    }

    function changeTab(value) {
      if (S.tab === value) return;
      confirmDiscard(function () { S.tab = value; S.focusTab = true; render(); });
    }

    function confirmDiscard(then) {
      if (!S.dirty && !S.exportDirty) { then(); return; }
      viewConfirm({ title: S.exportDirty ? "有未确认的导出映射" : "有未保存的值", message: "离开会丢失当前尚未保存或确认的修改。", confirmText: "放弃修改", cancelText: "继续编辑" }).then(function (ok) {
        if (!ok) return;
        S.dirty = false; S.edits = {}; S.exportDirty = false; then();
      });
    }

    function revisionPicker() {
      var item = revision();
      return h("button.cv-revision-picker", { type: "button", title: revisionLabel(item), "aria-label": "按主线或机台、版本、类型和修订选择配置", onclick: function () {
        var selected = item, owner = item && item.machine_id || "main", rid = S.releaseId, kind = item && item.kind || "default";
        var host = h("div.cv-picker-grid");
        function list(title, options, active, onChoose) { return h("section", h("strong.small", title), h("div.col", options.map(function (o) { return h("button.btn" + (o.value === active ? ".btn-primary" : ".btn-ghost"), { type: "button", "aria-pressed": o.value === active ? "true" : "false", onclick: function () { onChoose(o.value); draw(); } }, o.label); }))); }
        function draw() {
          var focused = document.activeElement && document.activeElement.closest("section"), sectionIndex = focused ? Array.prototype.indexOf.call(host.children, focused) : -1;
          util.clear(host);
          host.appendChild(list("分支", [{ value: "main", label: "主线" }].concat(S.machines.map(function (m) { return { value: m.machine_id, label: m.machine_no }; })), owner, function (value) { owner = value; kind = value === "main" ? "default" : "machine"; }));
          host.appendChild(list("软件版本", (S.detail.releases || []).map(function (r) { return { value: r.release_id, label: r.version }; }), rid, function (value) { rid = value; }));
          host.appendChild(list("类型", owner === "main" ? [{ value: "default", label: "版本默认" }, { value: "baseline", label: "版本基线" }] : [{ value: "machine", label: "机台配置" }], kind, function (value) { kind = value; }));
          var options = S.revisions.filter(function (r) { return (r.machine_id || "main") === owner && r.release_id === rid && r.kind === kind; });
          if (!options.some(function (r) { return selected && r.id === selected.id; })) selected = options[options.length - 1];
          host.appendChild(list("修订", options.map(function (r) { return { value: r.id, label: r.label + (r.status === "draft" ? " · 草稿" : " · 已固化") }; }), selected && selected.id, function (value) { selected = options.filter(function (r) { return r.id === value; })[0]; }));
          if (!options.length) host.lastChild.appendChild(h("p.small.muted", "此范围尚无修订"));
          if (sectionIndex >= 0 && host.children[sectionIndex]) { var activeChoice = host.children[sectionIndex].querySelector('[aria-pressed="true"]'); if (activeChoice) activeChoice.focus(); }
        }
        draw(); viewModal({ title: "选择配置修订", body: host, size: "wide", footer: function (close) { return [h("button.btn", { type: "button", onclick: close }, "取消"), h("button.btn.btn-primary", { type: "button", onclick: function () { if (!selected) { MC.toast.warning("请选择已有修订"); return; } close(); chooseRevision(selected); } }, "使用此修订")]; } });
      } }, h("span.row", { style: "gap:8px;min-width:0;flex:1" }, h("strong.small", revisionLabel(item)), h("span.badge", item ? item.status === "draft" ? S.savingValues ? "保存中…" : canWrite() ? "草稿" : "草稿 · 只读" : "历史值只读" : "尚无修订")), h("span.small", "更换 ›"));
    }

    function mainPanel() {
      if (!S.detail) return MC.stateBlock({ kind: "empty", title: "先选择固定配置" });
      var tabs = MC.segmented([
        { value: "versions", label: "版本演进" }, { value: "workbench", label: "参数工作台" },
        { value: "overview", label: "参数总览" }, { value: "export", label: "模板导出" }, { value: "history", label: "配置历史" },
      ], S.tab, changeTab, "配置管理视图");
      tabs.classList.add("cv-tabs");
      var panelId = util.uid("config-panel"), tabButtons = Array.prototype.slice.call(tabs.querySelectorAll('[role="tab"]'));
      tabButtons.forEach(function (button, index) {
        button.id = panelId + "-tab-" + index; button.setAttribute("aria-controls", panelId); button.tabIndex = button.getAttribute("aria-selected") === "true" ? 0 : -1;
        button.addEventListener("keydown", function (event) {
          var target = event.key === "ArrowRight" ? (index + 1) % tabButtons.length : event.key === "ArrowLeft" ? (index + tabButtons.length - 1) % tabButtons.length : event.key === "Home" ? 0 : event.key === "End" ? tabButtons.length - 1 : -1;
          if (target < 0) return; event.preventDefault(); tabButtons[target].click();
        });
      });
      var rel = release(), body;
      if (!(S.detail.releases || []).length && S.tab !== "history") body = MC.stateBlock({ kind: "empty", title: "添加第一个软件版本", desc: S.detail.name + "尚无版本。创建后可手工填写默认参数，或从文件导入。", actions: [h("button.btn.btn-primary", { type: "button", disabled: !canWrite(), onclick: createReleaseDialog }, icon("plus"), "新建软件版本")] });
      else if (S.tab === "versions") body = versionsView(rel);
      else if (S.tab === "workbench") body = workbenchView(rel);
      else if (S.tab === "overview") body = overviewView(rel);
      else if (S.tab === "export") body = exportView(rel);
      else if (S.tab === "history") body = historyView();
      var active = tabs.querySelector('[aria-selected="true"]');
      var panel = h("section.cv-workspace", tabs, h("div.cv-tab-content", { id: panelId, role: "tabpanel", "aria-labelledby": active && active.id }, body));
      return panel;
    }

    function versionsView(rel) {
      var releases = (S.detail.releases || []).slice().sort(function (a, b) { return a.order - b.order; });
      var item = revision(), width = 126 + releases.length * 226, height = Math.max(334, 358 + S.machines.length * 112 - 42);
      var canvas = h("div.cv-graph-canvas", { style: "width:" + width + "px;height:" + height + "px" });
      var locations = {};
      function node(row, x, y) {
        var source = S.revisions.filter(function (r) { return r.id === row.source; })[0];
        var title = row.kind === "default" ? "版本默认" : row.kind === "baseline" ? "版本基线" : "机台配置";
        locations[row.id] = { x: x, y: y };
        return h("button.cv-graph-node" + (row.id === S.selected ? ".is-selected" : "") + (row.status === "draft" ? ".is-draft" : ""), {
          type: "button", style: "left:" + x + "px;top:" + y + "px", "aria-pressed": row.id === S.selected ? "true" : "false", "aria-label": "查看 " + revisionLabel(row), "data-config-revision": row.id,
          onclick: function () { chooseRevision(row); },
        }, h("span.cv-node-line", h("strong", title), h("span.mono.small", row.label)), h("span.small.muted", icon(row.status === "draft" ? "edit" : "lock"), row.status === "draft" ? "草稿 · 待确认" : source ? "基于 " + labelOf(source.release_id) + " · " + source.label : "软件版本默认值"));
      }
      releases.forEach(function (r, index) {
        var x = 126 + index * 226 + 113;
        canvas.appendChild(h("div.cv-graph-column", { style: "left:" + (126 + index * 226) + "px;width:226px" }));
        canvas.appendChild(h("div.cv-release-band", { style: "left:" + (126 + index * 226) + "px;width:226px" }, h("div.row", h("strong", r.version), h("span.spacer"), h("span.badge" + (r.default_revision_id ? "" : ".badge-warning"), r.default_revision_id ? "已确认" : "准备中")), h("span.small.muted", r.parameter_count == null ? "定义修订 " + r.definition_revision : r.parameter_count + " 项参数")));
        var def = S.revisions.filter(function (n) { return n.release_id === r.release_id && n.kind === "default"; })[0];
        if (def) canvas.appendChild(node(def, x, 132));
        var bases = S.revisions.filter(function (n) { return n.release_id === r.release_id && n.kind === "baseline"; });
        var base = bases.filter(function (n) { return n.id === S.selected; })[0] || bases[bases.length - 1];
        if (base) canvas.appendChild(node(base, x, 242));
        else canvas.appendChild(h("button.cv-graph-node.is-planned", { type: "button", style: "left:" + x + "px;top:242px", disabled: !canWrite(), onclick: function () {
          if (r.default_revision_id) { S.releaseId = r.release_id; publishNextBaseline(); } else if (def) chooseRevision(def, "workbench");
        } }, h("strong.small", r.default_revision_id ? "建立版本基线" : "先确认版本默认"), h("span.small.muted", r.default_revision_id ? "创建变更草稿" : "尚有默认值草稿")));
        S.machines.forEach(function (m, j) {
          var candidates = S.revisions.filter(function (n) { return n.kind === "machine" && n.machine_id === m.machine_id && n.release_id === r.release_id; });
          var selected = candidates.filter(function (n) { return n.id === S.selected; })[0] || candidates[candidates.length - 1];
          if (selected) canvas.appendChild(node(selected, x, 358 + j * 112));
        });
      });
      [{ y: 132, name: "版本默认", desc: "软件原始默认值" }, { y: 242, name: "主线基线", desc: "调试后的标准值" }].concat(S.machines.map(function (m, i) { return { y: 358 + i * 112, name: m.machine_no, desc: "机台配置" }; })).forEach(function (lane) {
        canvas.appendChild(h("div.cv-lane-label", { style: "top:" + (lane.y - 20) + "px" }, h("strong", lane.name), h("span.small.muted", lane.desc)));
      });
      var svg = document.createElementNS(MC.SVG_NS, "svg");
      svg.setAttribute("class", "cv-connections"); svg.setAttribute("width", width); svg.setAttribute("height", height); svg.setAttribute("aria-hidden", "true");
      S.revisions.forEach(function (n) {
        var target = locations[n.id]; if (!target) return;
        [n.parent, n.source].forEach(function (source, i) {
          var from = locations[source]; if (!from || source === n.id) return;
          var line = document.createElementNS(svg.namespaceURI, "path");
          line.setAttribute("d", from.y === target.y ? "M" + (from.x + 84) + " " + from.y + "H" + (target.x - 84) : "M" + from.x + " " + (from.y + 32) + "V" + (target.y - 32) + "H" + target.x);
          line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "1.5"); if (i) line.setAttribute("stroke-dasharray", "3 4"); svg.appendChild(line);
        });
      }); canvas.appendChild(svg);
      var graph = h("section.cv-graph-panel", { "aria-label": "配置版本演进图" }, h("div.cv-graph-scroll", { tabindex: "0", "aria-label": "版本图，可左右滚动" }, canvas),
        h("div.cv-graph-caption", h("span", "— 修订演进"), h("span", "┄ 采用关系"), h("span.spacer"), h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { viewModal({ title: "读图说明", body: h("p", "每列为一个软件版本。实线表示真实前序修订，虚线表示采用的精确基准；空白表示尚无修订。点击节点查看详情或进入参数工作台。") }); } }, "读图说明")));
      var detail = h("aside.cv-node-detail", { "aria-label": "所选修订详情" }, h("div.cv-detail-head", h("h2", "修订详情"), h("span.badge", item && item.status === "draft" ? "草稿" : "已固化")));
      if (item) {
        var src = S.revisions.filter(function (r) { return r.id === item.source; })[0];
        detail.appendChild(h("div.cv-detail-body", h("span.small.muted", item.kind === "machine" ? "机台分支" : item.kind === "baseline" ? "版本基线" : "版本默认"), h("h3", (item.machine_no || "主线") + " / " + labelOf(item.release_id)), h("span.mono", item.label),
          h("dl.cv-facts", h("dt", "参数身份"), h("dd", S.paramTotal + " 项"), h("dt", "精确基准"), h("dd", src ? labelOf(src.release_id) + " · " + src.label : item.source || "无上游"), h("dt", "前序修订"), h("dd", item.parent ? revisionName(item.parent) : "初始修订"), h("dt", "创建时间"), h("dd", util.formatTime(item.at))),
          h("div.cv-detail-actions", h("button.btn.btn-primary", { type: "button", onclick: function () { changeTab("workbench"); } }, item.status === "draft" ? "继续编辑草稿" : "查看参数"),
            item.status === "published" ? h("button.btn", { type: "button", disabled: !canWrite(), onclick: createEditingDraft }, "创建编辑草稿") : null,
            item.status === "published" ? h("button.btn", { type: "button", onclick: function () { S.exportRevisionId = item.id; changeTab("export"); } }, "按模板导出") : null,
            item.status === "draft" ? h("button.btn", { type: "button", disabled: !canWrite(), onclick: publishSelected }, item.kind === "default" ? "确认版本默认" : "发布修订") : null),
          h("details.cv-help", h("summary", "发布范围"), h("p.small.muted", "发布只更新管理系统中的配置修订，不表示已下发或核验真实机台。"))));
      }
      var machineRows = S.machines.map(function (m) { var current = S.revisions.filter(function (r) { return r.id === m.head_id; })[0]; var draft = S.revisions.filter(function (r) { return r.machine_id === m.machine_id && r.status === "draft"; })[0]; return h("tr", h("td", m.machine_no), h("td", current ? revisionLabel(current) : "尚未发布"), h("td", current ? revisionName(current.source) : "—"), h("td", draft ? "草稿待完成" : "已固化"), h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { if (draft || current) chooseRevision(draft || current, "workbench"); } }, draft ? "继续编辑" : "查看参数"))); });
      return h("div.cv-version-view", h("div.cv-toolbar", h("span.small.muted", releases.length + " 个软件版本"), h("span.small.muted", S.machines.length + " 台机台"), h("span.spacer"),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite() || !S.revisions.some(function (r) { return r.status === "published" && r.kind !== "machine"; }), onclick: machineDialog }, icon("compare"), "新建机台分支"), h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite(), onclick: createReleaseDialog }, icon("plus"), "新建软件版本")),
        h("div.cv-overview-layout", graph, detail), h("details.cv-machine-table", h("summary", "机台当前修订 · " + S.machines.length + " 台"), h("div.table-wrap", h("table.table", h("thead", h("tr", ["机台", "当前已发布修订", "采用基准", "状态", "操作"].map(function (t) { return h("th", t); }))), h("tbody", machineRows.length ? machineRows : h("tr", h("td", { colspan: 5 }, "还没有机台分支")))))));
    }

    function revisionName(id) { var r = S.revisions.filter(function (n) { return n.id === id; })[0]; return r ? labelOf(r.release_id) + " · " + r.label : id || "—"; }
    function operationContext() {
      var epoch = S.contextEpoch, scope = cfgPath(""), selected = S.selected;
      return { path: scope, current: function () { return !disposed && canWrite() && epoch === S.contextEpoch && scope === cfgPath("") && selected === S.selected; } };
    }
    function createEditingDraft() {
      var current = revision(), context = operationContext(); if (!current || !canWrite()) return;
      var existing = S.revisions.filter(function (r) { return r.status === "draft" && (current.kind === "machine" ? r.machine_id === current.machine_id : r.kind === "baseline" && r.release_id === current.release_id); })[0];
      if (existing) { chooseRevision(existing, "workbench"); return; }
      api.requestV3("POST", context.path + "/restore-plans", { body: { revision_id: current.id, request_id: util.uid("draft") } }).then(function (res) {
        if (!context.current()) return;
        S.selected = res.draft.draft_id; S.tab = "workbench"; reloadDetail(); MC.toast.success("已从所选修订创建编辑草稿");
      }, function (err) { MC.toastError("创建草稿失败", err); });
    }
    function publishSelected() {
      var item = revision(), context = operationContext(), expected = S.draft && S.draft.revision;
      if (!item || !editable()) return;
      if (S.dirty) { MC.toast.warning("请先保存当前修改"); return; }
      if (item.kind === "default") { freezeD0(); return; }
      viewConfirm({ title: "发布" + item.label, message: "将固化当前草稿并更新管理系统的修订；不会下发真实机台。", confirmText: "发布" }).then(function (ok) {
        if (!ok || !context.current() || S.dirty || S.savingValues) return;
        var path = item.kind === "machine" ? "/machines/" + enc(item.machine_id) + "/publish" : "/baseline-drafts/" + enc(item.id) + "/publish";
        api.requestV3("POST", context.path + path, { body: { request_id: util.uid("publish"), expected_revision: expected } }).then(function (res) {
          if (!context.current()) return;
          S.historyEvents = null; S.selected = item.kind === "machine" ? res.machine.head_id : res.baseline.baseline_id;
          MC.toast.success("修订已发布"); reloadDetail();
        }, function (err) { MC.toastError("发布失败", err); });
      });
    }

    function labelOf(id) {
      var r = (S.detail.releases || []).filter(function (x) { return x.release_id === id; })[0];
      return r ? r.version : id;
    }

    function pagerRow(total, offset, limit, go, label) {
      var pages = Math.max(1, Math.ceil((total || 0) / limit));
      var page = Math.floor((offset || 0) / limit) + 1;
      return h("div.row", { style: "gap:8px;flex-wrap:wrap" },
        h("button.btn.btn-sm", { type: "button", disabled: offset <= 0, onclick: function () { go(Math.max(0, offset - limit)); } }, "上一页"),
        h("span.xsmall.muted", (label || "") + "第 " + page + " / " + pages + " 页 · 共 " + (total || 0) + " 行"),
        h("button.btn.btn-sm", { type: "button", disabled: page >= pages, onclick: function () { go(offset + limit); } }, "下一页"));
    }

    function loadParamPage(offset) {
      var scope = cfgPath(""), selected = S.selected, seq = ++parameterSeq;
      function current() { return !disposed && seq === parameterSeq && scope === cfgPath("") && selected === S.selected; }
      S.paramLoading = true;
      api.getV3(scope + "/parameters?offset=" + Math.max(0, offset) + "&limit=" + S.paramLimit + "&release_id=" + enc(S.releaseId || "")).then(function (res) {
        if (!current()) return;
        S.paramPage = res;
        S.paramTotal = res.total == null ? (res.items || []).length : res.total;
        S.paramOffset = res.offset || 0;
        S.params = res;
        S.paramLoading = false;
        render();
      }, function (err) { if (current()) { S.paramLoading = false; MC.toastError("读取参数失败", err); render(); } });
    }

    function loadAllParams() {
      if (S.allLoading) return;
      S.allLoading = true;
      var scope = cfgPath(""), rid = S.releaseId;
      api.getV3(scope + "/parameters?release_id=" + enc(rid || "")).then(function (res) {
        if (disposed || scope !== cfgPath("") || rid !== S.releaseId) return;
        S.allParams = res.items || [];
        S.allLoading = false;
        S.paramTotal = res.total == null ? S.allParams.length : res.total;
        render();
      }, function (err) { if (!disposed && scope === cfgPath("") && rid === S.releaseId) { S.allLoading = false; MC.toastError("读取参数失败", err); } });
    }

    function loadDir(parent, offset) {
      var scope = cfgPath(""), rid = S.releaseId, seq = ++directorySeq;
      function current() { return !disposed && seq === directorySeq && scope === cfgPath("") && rid === S.releaseId; }
      S.dirLoading = true;
      S.dirError = null;
      var request;
      if (S.paramTotal <= 1000) {
        request = (S.allParams ? Promise.resolve({ items: S.allParams }) : api.getV3(scope + "/parameters?release_id=" + enc(rid || ""))).then(function (res) {
          var parts = (parent || "").split("/").filter(Boolean), all = res.items || [], items = all.filter(function (p) { return parts.every(function (part, i) { return (p.directory || [])[i] === part; }); });
          if (current()) S.allParams = all;
          return { items: items.slice(offset || 0, (offset || 0) + S.paramLimit), parameter_total: items.length, offset: offset || 0 };
        });
      } else request = api.getV3(scope + "/parameter-tree?parent=" + enc(parent || "")
        + "&recursive=true&offset=" + Math.max(0, offset || 0) + "&limit=" + S.paramLimit + "&release_id=" + enc(rid || ""));
      return request.then(function (res) {
        if (!current()) return;
        S.dirParent = parent || "";
        S.dirData = res;
        S.dirLoading = false;
        render();
      }, function (err) { if (current()) { S.dirLoading = false; S.dirError = err; render(); } });
    }

    function memberText(row) {
      if (!row || row.membership === "not_introduced") return "未纳入";
      if (row.membership === "removed") return "已移除";
      if (row.enabled === false) return "已停用";
      if (row.value_state === "null") return "null";
      if (row.value_state === "unset") return "未设";
      return row.value === "" ? '""' : row.value == null ? "未设" : String(row.value);
    }
    function memberKey(row) { row = row || {}; return JSON.stringify([row.membership || "not_introduced", row.enabled !== false, row.value_state || "unset", row.value]); }

    function folderBrowser(releaseId, activePath, total, onSelect) {
      var scope = cfgPath(""), version = releaseId ? release() && release().definition_revision : S.overview && S.overview.catalog_revision;
      var key = scope + "|" + releaseId + "|" + version;
      if (!S.folderCaches) S.folderCaches = Object.create(null);
      var cache = S.folderCaches[key];
      if (!cache) {
        cache = S.folderCaches[key] = { nodes: Object.create(null), expanded: Object.create(null), pages: Object.create(null) };
        var ancestors = (activePath || "").split("/");
        ancestors.forEach(function (_, i) { cache.expanded[ancestors.slice(0, i).join("/")] = true; });
      }
      if (cache.expanded[""] === undefined) cache.expanded[""] = true;
      var host = h("div.cv-folder-tree");
      function fetch(path) {
        var node = cache.nodes[path]; if (node && (node.loading || node.items)) return;
        node = cache.nodes[path] = { loading: true };
        api.getV3(scope + "/parameter-tree?parent=" + enc(path) + "&offset=0&limit=1&release_id=" + enc(releaseId || "")).then(function (res) {
          if (disposed || scope !== cfgPath("")) return;
          node.loading = false; node.items = res.directories || []; draw();
        }, function (err) { if (disposed || scope !== cfgPath("")) return; node.loading = false; node.error = err; draw(); });
      }
      function draw() {
        if (!host.isConnected && host.childNodes.length) return;
        var focused = host.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
        util.clear(host);
        function branch(path, name, count, depth, hasChildren) {
          var node = cache.nodes[path], expanded = !!cache.expanded[path];
          // A directory's parameter count is not its child-directory count.
          if (hasChildren === undefined && !node) Promise.resolve().then(function () { fetch(path); });
          var expandable = hasChildren !== false && !(node && node.items && !node.items.length);
          var wrap = h("div", h("div.cv-folder-row" + (path === activePath ? ".is-active" : ""), { style: "padding-left:" + Math.min(depth * 12, 120) + "px" },
            expandable ? h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": (expanded ? "折叠 " : "展开 ") + name, "aria-expanded": String(expanded), onclick: function () { cache.expanded[path] = !expanded; if (!expanded) fetch(path); draw(); } }, icon(expanded ? "chevron-down" : "chevron-right")) : h("span", { style: "width:24px;flex:none", "aria-hidden": "true" }),
            h("button.btn.btn-ghost.btn-sm.cv-folder-name", { type: "button", title: path || "全部参数", "aria-label": "选择目录 " + (path || "全部参数"), onclick: function () { onSelect(path); } }, name + " · " + count)));
          if (!expanded || !expandable) return wrap;
          if (!node) { Promise.resolve().then(function () { fetch(path); }); return wrap; }
          if (node.loading) wrap.appendChild(h("p.small.muted", "读取目录…"));
          else if (node.error) wrap.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { delete cache.nodes[path]; fetch(path); } }, "目录读取失败，重试"));
          else {
            var offset = cache.pages[path] || 0, items = node.items || [];
            items.slice(offset, offset + 50).forEach(function (item) { wrap.appendChild(branch(item.path, item.name, item.count, depth + 1, item.has_children)); });
            if (items.length > 50) wrap.appendChild(h("div.row", h("button.btn.btn-ghost.btn-sm", { type: "button", disabled: offset === 0, onclick: function () { cache.pages[path] = Math.max(0, offset - 50); draw(); } }, "上一组目录"), h("button.btn.btn-ghost.btn-sm", { type: "button", disabled: offset + 50 >= items.length, onclick: function () { cache.pages[path] = offset + 50; draw(); } }, "下一组目录")));
          }
          return wrap;
        }
        host.appendChild(branch("", "全部参数", total, 0));
        if (focused) Array.prototype.some.call(host.querySelectorAll("button"), function (button) { if (button.getAttribute("aria-label") === focused) { button.focus(); return true; } return false; });
      }
      var actions = h("div.row", { style: "gap:4px" });
      if (!releaseId && canWrite()) {
        actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { directoryDialog("create"); } }, "新增目录"));
        if (activePath) actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", "aria-label": "当前目录操作", onclick: function (event) { MC.menu(event.currentTarget, [
          { label: "重命名 / 移动目录", onClick: function () { directoryDialog("move"); } },
          { label: "删除空目录", danger: true, onClick: function () { directoryDialog("delete"); } }
        ]); } }, icon("more")));
      }
      draw(); return h("aside.cv-param-folders", { "aria-label": "参数目录" }, h("div.cv-folder-header", h("strong.small", "参数目录"), h("span.badge", total)), actions, host);
    }

    function directoryDialog(action) {
      var original = S.overviewDirectory || "", expected = S.overview && S.overview.catalog_revision;
      var input = h("input.input", { value: action === "move" ? original : "", placeholder: "例如 设备控制/运动参数" });
      var field = MC.field({ label: action === "move" ? "新的目录路径" : "目录路径", required: true, control: input, help: "使用 / 分隔层级" });
      var targetRelease = release(), expectedDraft = S.draft && S.draft.revision;
      var adopt = h("input", { type: "checkbox", checked: !!targetRelease && !targetRelease.default_revision_id, disabled: !targetRelease || !!targetRelease.default_revision_id });
      var body = h("div.col", { style: "gap:12px" }, action !== "create" ? h("p.small.mono", original) : null,
        action === "delete" ? h("p", "删除此空目录及其空子目录。目录中有参数时会停止操作；已固化快照保持不变。") : field,
        h("label.row.small", { style: "gap:6px" }, adopt, "同步到当前准备版本的定义"), h("div", { role: "alert", "data-directory-error": "1" }));
      configFormModal({ title: action === "create" ? "新增目录" : action === "move" ? "重命名 / 移动目录" : "删除空目录", body: body, footer: function (close) {
        var save = h("button.btn" + (action === "delete" ? ".btn-danger" : ".btn-primary"), { type: "button", onclick: function () {
          var path = input.value.split("/").filter(Boolean), payload = { path: action === "create" ? path : original.split("/"), expected_definition_revision: expected };
          if (action === "move") payload.new_path = path;
          if (adopt.checked && targetRelease && !targetRelease.default_revision_id) { payload.release_id = targetRelease.release_id; payload.expected_draft_revision = expectedDraft; }
          MC.withBusy(save, function () { return api.requestV3(action === "move" ? "PATCH" : "POST", cfgPath("/parameter-directories" + (action === "delete" ? "/delete" : "")), { body: payload }).then(function () {
            if (close(true) === false) return; S.overviewDirectory = action === "delete" ? "" : path.join("/"); S.overviewOffset = 0; reloadDetail(); MC.toast.success("目录已更新");
          }, function (err) { if (action === "delete") body.querySelector("[data-directory-error]").textContent = MC.errorText(err); else field.setError(MC.errorText(err)); }); });
        } }, action === "delete" ? "删除空目录" : "保存");
        return [h("button.btn", { type: "button", onclick: close }, "取消"), save];
      } });
    }

    function workbenchView(rel) {
      if (!rel) return MC.stateBlock({ kind: "empty", title: "先创建软件版本" });
      var current = revision(), values = (S.draft && S.draft.values) || {}, canEdit = editable();
      var save = h("button.btn.btn-sm", { type: "button", disabled: !canEdit, onclick: function () {
        if (S.savingValues) return;
        var submitted = Object.assign({}, S.edits), scope = cfgPath(""), selected = S.selected, epoch = S.contextEpoch;
        var items = Object.keys(S.edits).map(function (id) { return Object.assign({ parameter_id: id }, S.edits[id]); });
        if (!items.length) { MC.toast.warning("没有未保存的修改"); return; }
        if (items.length > 100) { MC.toast.warning("单次最多保存 100 项，请先保存当前修改"); return; }
        var path = current && current.kind === "machine" ? "/machines/" + enc(current.machine_id) + "/draft/values"
          : current && current.kind === "baseline" ? "/baseline-drafts/" + enc(current.id) + "/values" : "/drafts/" + enc(draftId()) + "/values";
        S.savingValues = true; S.valueError = null;
        MC.withBusy(save, function () {
          return api.requestV3("PATCH", cfgPath(path), { body: { request_id: util.uid("values"), expected_revision: S.draft ? S.draft.revision : 0, items: items } }).then(function () {
            if (disposed || epoch !== S.contextEpoch || scope !== cfgPath("") || selected !== S.selected) return;
            S.savingValues = false;
            Object.keys(submitted).forEach(function (id) { if (S.edits[id] === submitted[id]) delete S.edits[id]; });
            S.dirty = Object.keys(S.edits).length > 0; MC.toast.success("草稿已保存"); reloadDetail();
          }, function (err) { if (disposed || epoch !== S.contextEpoch || scope !== cfgPath("") || selected !== S.selected) return; S.savingValues = false; S.valueError = err; render(); });
        });
      } }, "保存值");
      save.disabled = !canEdit || !!S.savingValues;
      var search = h("input.input", { type: "search", "aria-label": "搜索当前页参数", placeholder: "搜索当前页的参数名称、身份或实际名" });
      var body = h("tbody");
      var defaultValues = (rel.d0 && (rel.d0.frozen_snapshot || rel.d0.values)) || {};
      function makeRow(p) {
        var original = values[p.parameter_id] || { membership: "not_introduced", enabled: true, value_state: "unset" };
        var row = S.edits[p.parameter_id] || original;
        var control = h("input.input.input-sm", { value: row.value == null ? "" : String(row.value), disabled: !canEdit || row.value_state !== "present", "aria-label": p.name + "的配置值" });
        var state = h("select.select", { "aria-label": p.name + "的值状态", disabled: !canEdit },
          [["present", "有值"], ["unset", "未设"], ["null", "null"]].map(function (o) { return h("option", { value: o[0], selected: (row.value_state || "unset") === o[0] }, o[1]); }));
        var basis = (S.basis || {})[p.parameter_id];
        var intent = h("select.select", { "aria-label": p.name + "的继承方式", disabled: !canEdit }, [["inherit", "跟随基准"], ["override", "独立覆盖"], ["pin", "固定值"]].map(function (o) { return h("option", { value: o[0], selected: (row.intent || (memberKey(row) === memberKey(basis) ? "inherit" : "override")) === o[0], disabled: o[0] === "pin" && state.value !== "present" }, o[1]); }));
        function remember(intentChanged) {
          var update = { membership: "included", enabled: row.enabled !== false, value_state: state.value };
          if (state.value === "present") update.value = control.value;
          if (current && current.kind !== "default") {
            if (!intentChanged && intent.value !== "pin") intent.value = "override";
            if (intent.value === "pin" && state.value !== "present") intent.value = "override";
            update.intent = intent.value;
            if (intentChanged && intent.value === "inherit") {
              update = Object.assign({ membership: "not_introduced", enabled: true, value_state: "unset" }, basis || {}, { intent: "inherit" });
              state.value = update.value_state; control.value = update.value == null ? "" : String(update.value);
            }
          }
          intent.options[2].disabled = state.value !== "present";
          S.edits[p.parameter_id] = update;
          S.dirty = true; control.disabled = !canEdit || state.value !== "present";
        }
        control.addEventListener("input", function () { remember(false); }); state.addEventListener("change", function () { remember(false); }); intent.addEventListener("change", function () { remember(true); });
        var binding = (S.bindings || []).filter(function (b) { return b.parameter_id === p.parameter_id; })[0];
        var different = memberKey(row) !== memberKey(basis);
        return h("tr", { "data-different": different ? "true" : "false" }, h("td", h("strong", p.name), h("div.mono.xsmall.muted", p.parameter_id)),
          h("td.small", binding ? binding.locator : "待绑定", h("div.muted.xsmall", (p.directory || []).join(" / "))),
          h("td.mono.small", memberText(defaultValues[p.parameter_id])), h("td.mono.small", memberText(S.basis && S.basis[p.parameter_id])),
          h("td", canEdit ? h("div.col", { style: "gap:4px" }, control, state) : h("strong.mono", memberText(row))), h("td.muted", p.unit || "—"),
          h("td.small", current && current.kind === "default" ? "版本默认" : canEdit ? intent : ({ pin: "固定值", override: "独立覆盖", inherit: "跟随基准" }[row.intent] || (different ? "独立覆盖" : "跟随基准"))));
      }
      var page = S.folderActive ? S.dirData : S.paramPage || S.params;
      var rows = S.paramMode === "tree" && S.selectedParam ? [S.selectedParam] : page && page.items || [];
      rows.forEach(function (p) { body.appendChild(makeRow(p)); });
      if (!rows.length) body.appendChild(h("tr", h("td", { colspan: 7 }, S.paramLoading || S.dirLoading ? "正在读取参数…" : "没有匹配的参数")));
      function filterRows() { var term = search.value.toLowerCase(); Array.prototype.forEach.call(body.children, function (row) { row.hidden = row.textContent.toLowerCase().indexOf(term) < 0 || S.onlyDiff && row.getAttribute("data-different") !== "true"; }); }
      search.addEventListener("input", filterRows);
      var onlyDiff = h("input", { type: "checkbox", checked: S.onlyDiff, onchange: function () { S.onlyDiff = onlyDiff.checked; filterRows(); } });
      filterRows();
      var folders = folderBrowser(S.releaseId, S.folderActive ? S.dirParent : "", S.paramTotal, function (path) {
        S.selectedParam = null; S.folderActive = !!path; S.dirOffset = 0;
        if (path) loadDir(path, 0); else loadParamPage(0);
      });
      var mode = MC.segmented([{ value: "list", label: "列表" }, { value: "tree", label: "参数树" }], S.paramMode, function (value) { S.paramMode = value; render(); }, "参数视图");
      folders.insertBefore(mode, folders.firstChild);
      if (S.paramMode === "tree" && S.paramTotal <= 1000) {
        var directoryTree = folders.querySelector(".cv-folder-tree"); if (directoryTree) directoryTree.hidden = true;
        if (S.allParams === null) loadAllParams();
        else {
          var treeHost = h("div", { style: "height:360px;margin-top:12px;display:flex;flex-direction:column;min-height:0" });
          folders.appendChild(treeHost);
          var doc = paramDoc(S.allParams, Object.assign({}, values, S.edits));
          Promise.resolve().then(function () {
            if (!treeHost.isConnected || !MC.Tree) return;
            S.paramTree = new MC.Tree(treeHost, MC.doc.buildIndex(doc), { label: "参数目录", onSelect: function (index) { var p = doc.metaParams[index]; if (p) { if (S.selectedParam && S.selectedParam.parameter_id === p.parameter_id) return; S.selectedParam = p; render(); } else { var path = doc.nodes.loc[index] || ""; if (!S.selectedParam && S.dirParent === path && S.folderActive === !!path) return; S.selectedParam = null; S.folderActive = !!path; if (path) loadDir(path, 0); else loadParamPage(0); } } });
            if (S.selectedParam) Object.keys(doc.metaParams).some(function (index) { if (doc.metaParams[index].parameter_id === S.selectedParam.parameter_id) { S.paramTree.select(+index); return true; } return false; });
          });
        }
      }
      var tools = h("div.cv-toolbar", revisionPicker(), h("span.spacer"),
        h("button.btn.btn-sm", { type: "button", "data-sync-config-now": "1", onclick: function () { app.go({ kind: "updates", arg: "plan" }); } }, "立即更新"),
        h("button.btn.btn-sm", { type: "button", "data-sync-config-settings": "1", onclick: function () { app.go({ kind: "updates", arg: "sources" }); } }, "更新设置"),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: extractDialog }, "从文件提取参数"),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: mergeDialog }, "合并配置"), save,
        current && current.status === "draft" ? h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite(), onclick: publishSelected }, current.kind === "default" ? "确认版本默认" : "发布修订")
          : h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite(), onclick: createEditingDraft }, "创建编辑草稿"));
      return h("div.cv-workbench", tools,
        S.valueError ? h("div.banner.banner-warning", { role: "alert" }, h("div.banner-body", "保存失败，修改已保留。", MC.errorText(S.valueError)), S.valueError.code === "REVISION_CONFLICT" ? h("button.btn.btn-sm", { type: "button", onclick: recoverValues }, "核对最新草稿") : null) : null,

        h("div.cv-param-browser", folders, h("section.cv-param-data", h("div.cv-toolbar", search, h("span.spacer"), h("label.row.small", { style: "gap:6px" }, onlyDiff, "仅看与基准的差异")), h("div.cv-folder-trail.small", S.paramMode === "tree" && S.selectedParam ? S.selectedParam.name : S.folderActive ? "/" + S.dirParent : "全部参数"),
          h("div.table-wrap", h("table.table", h("thead", h("tr", ["参数 / 稳定身份", "本版实际名", "版本默认", "采用基准", "当前值", "单位", "继承方式"].map(function (t) { return h("th", t); }))), body)),
          h("div.cv-param-footer", pagerRow(S.paramMode === "tree" && S.selectedParam ? 1 : S.folderActive ? (page && page.parameter_total || 0) : S.paramTotal, S.paramMode === "tree" && S.selectedParam ? 0 : S.folderActive ? S.dirOffset : S.paramOffset, S.paramLimit, function (offset) { if (S.folderActive) { S.dirOffset = offset; loadDir(S.dirParent, offset); } else loadParamPage(offset); })))),
        h("details.cv-help", h("summary", "来源与历史修订"), h("div.cv-toolbar", S.revisions.filter(function (r) { return current && r.kind === current.kind && r.machine_id === current.machine_id; }).map(function (r) { return h("button.btn.btn-sm", { type: "button", onclick: function () { chooseRevision(r, "workbench"); } }, revisionName(r.id)); }))));
    }

    function recoverValues() {
      var item = revision(); if (!item) return;
      var scope = cfgPath(""), selected = S.selected, edits = JSON.stringify(S.edits);
      var path = item.kind === "machine" ? "/machines/" + enc(item.machine_id) : item.kind === "baseline" ? "/baseline-drafts/" + enc(item.id) : "/drafts/" + enc(item.id);
      api.getV3(scope + path).then(function (response) {
        if (disposed || scope !== cfgPath("") || selected !== S.selected) return;
        var latest = response.machine ? response.machine.draft : response;
        if (!latest || latest.frozen || latest.status === "published") { MC.toast.warning("该修订已固化，请保留修改并创建新的编辑草稿"); return; }
        var detail = h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "参数"), h("th", "服务器最新值"), h("th", "您的修改"))), h("tbody", Object.keys(S.edits).map(function (id) { return h("tr", h("td.mono.small", id), h("td", memberText((latest.values || {})[id])), h("td", memberText(S.edits[id]))); }))));
        viewConfirm({ title: "核对最新草稿", message: "确认后保留您的修改，并以此服务器修订再次保存。", detail: detail, confirmText: "保留修改，采用此修订" }).then(function (ok) {
          if (!ok || disposed || scope !== cfgPath("") || selected !== S.selected) return;
          if (JSON.stringify(S.edits) !== edits) { MC.toast.warning("修改已变化，请重新核对"); return; }
          S.draft = latest; S.valueError = null; render();
        });
      }, function (err) { MC.toastError("读取最新草稿失败", err); });
    }

    function paramDoc(params, values) {
      var nodes = { p: [-1], t: ["file"], l: ["参数"], v: [null], id: ["root"], loc: [""] };
      var metaParams = {};
      var dirIndex = { "": 0 };
      function ensureDir(parts, parent) {
        var key = "";
        var idx = parent;
        for (var k = 0; k < parts.length; k++) {
          key = key ? key + "/" + parts[k] : parts[k];
          if (dirIndex[key] === undefined) {
            nodes.p.push(idx); nodes.t.push("element"); nodes.l.push(parts[k]); nodes.v.push(null);
            nodes.id.push("dir:" + key); nodes.loc.push(key);
            idx = nodes.p.length - 1;
            dirIndex[key] = idx;
          } else idx = dirIndex[key];
        }
        return idx;
      }
      params.slice().sort(function (a, b) {
        return (a.directory || []).join("/").localeCompare((b.directory || []).join("/")) || String(a.name).localeCompare(String(b.name));
      }).forEach(function (p) {
        var parent = ensureDir(p.directory || [], 0);
        var cur = values[p.parameter_id] || { membership: "not_introduced", value_state: "unset", enabled: true };
        var text = cur.value != null ? String(cur.value)
          : cur.value_state === "null" ? "null"
          : cur.value_state === "unset" ? "未设"
          : cur.membership === "removed" ? "已移除"
          : cur.membership === "not_introduced" ? "未纳入"
          : cur.enabled === false ? "已停用" : "—";
        nodes.p.push(parent); nodes.t.push("element"); nodes.l.push(p.name); nodes.v.push(text);
        nodes.id.push(p.parameter_id); nodes.loc.push((p.directory || []).join("/"));
        metaParams[nodes.p.length - 1] = p;
      });
      return { file: { name: "参数" }, nodes: nodes, metaParams: metaParams };
    }

    function renderParamDetail(host) {
      if (!host) return;
      var p = S.selectedParam;
      if (!p) return util.clear(host);
      var cur = ((S.draft && S.draft.values) || {})[p.parameter_id] || {};
      util.replace(host, h("div.panel", h("div.panel-header", h("h3", p.name), h("span.xsmall.muted", (p.directory || []).join(" / ") || "根目录")),
        h("div.panel-body", h("dl.kv",
          h("dt", "类型/单位"), h("dd", (p.type || "—") + (p.unit ? " · " + p.unit : "")),
          h("dt", "属性"), h("dd.mono.xsmall", JSON.stringify(p.attrs || {})),
          h("dt", "来源"), h("dd.mono.xsmall", (p.source && p.source.locator) || "—"),
          h("dt", "当前"), h("dd", cur.value != null ? String(cur.value) : (cur.value_state || "unset"))))));
    }

    var EXTRACT_STATUS = {
      new: "新增", existing: "已存在", same_name_other_path: "同名异路径",
      ambiguous: "身份歧义", type_conflict: "类型冲突", null_uncertain: "null 待定",
      unsupported: "不支持",
    };
    var EXTRACT_CONFLICT = ["same_name_other_path", "ambiguous", "type_conflict", "null_uncertain"];

    function isExtractConflict(status) { return EXTRACT_CONFLICT.indexOf(status) >= 0; }

    function extractDialog() {
      if (!S.cid) return;
      var state = {
        files: [], fileId: "", plan: null, page: null, filter: "all", q: "",
        size: 50, offset: 0, loading: false, error: null, types: {},
        includeBindings: false, addMembers: false,
        requestId: "ui-extract-" + Date.now() + "-" + Math.random().toString(16).slice(2, 8),
      };
      var body = h("div.col", { style: "gap:12px" });
      var pendingText = h("span.xsmall.muted", { role: "status", "aria-live": "polite" }, "尚未加载计划");
      var applyBtn = h("button.btn.btn-primary", { type: "button", disabled: true }, "提交新增");
      applyBtn.addEventListener("click", function () {
        if (applyBtn.disabled || !state.plan) return;
        var payload = {
          request_id: state.requestId,
          include_existing_missing_bindings: state.includeBindings,
          add_members: state.addMembers,
        };
        if (S.releaseId) payload.release_id = S.releaseId;
        MC.withBusy(applyBtn, function () {
          return api.requestV3("POST", cfgPlan("/" + enc(state.plan.plan_id) + "/commit"), { body: payload }).then(function (res) {
            var row = res.result || {};
            MC.toast.success("已提交提取计划", "新增 " + (row.created || 0) + " 个参数，未变 " + (row.unchanged || 0) + " 个" + (row.bound ? "，补绑 " + row.bound : ""));
            if (state.close) state.close();
            reloadDetail();
          }, function (err) {
            MC.toastError("提交失败", err, err.code === "PLAN_STALE" ? { action: { label: "重算", onClick: recomputePlan } } : null);
          });
        });
      });
      var modal = viewModal({
        title: "从配置文件提取参数",
        subtitle: "提取参数名称与目录，不导入参数值。",
        size: "xwide",
        body: body,
        footer: function (close) {
          return [pendingText, h("span.spacer"), h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭"), applyBtn];
        },
      });
      state.close = modal.close;

      function cfgPlan(rest) {
        return cfgPath("/extraction-plans" + (rest || ""));
      }

      function selectedFile() {
        return state.files.filter(function (f) { return f.file_id === state.fileId; })[0] || state.files[0] || null;
      }

      function loadFiles() {
        state.loading = true;
        state.error = null;
        renderDialog();
        api.getV3(root("/file-selector")).then(function (res) {
          state.files = (res.items || []).filter(function (f) {
            var ext = String(f.name || "").split(".").pop().toLowerCase();
            return ext === "json" || ext === "xml";
          });
          if (state.files.length) state.fileId = state.files[0].file_id;
          state.loading = false;
          renderDialog();
        }, function (err) {
          state.loading = false;
          state.error = err;
          renderDialog();
        });
      }

      function createPlan() {
        var f = selectedFile();
        if (!f) return;
        state.loading = true;
        state.error = null;
        renderDialog();
        api.requestV3("POST", cfgPlan(""), { body: { version_id: f.version_id } }).then(function (res) {
          state.plan = res.plan;
          state.page = null;
          state.types = {};
          state.offset = 0;
          return loadRows();
        }, function (err) {
          state.loading = false;
          state.error = err;
          renderDialog();
        });
      }

      function loadRows() {
        if (!state.plan) return;
        state.loading = true;
        state.error = null;
        var url = cfgPlan("/" + enc(state.plan.plan_id) + "/rows")
          + "?offset=" + state.offset + "&limit=" + state.size
          + "&filter=" + enc(state.filter) + "&q=" + enc(state.q);
        return api.getV3(url).then(function (res) {
          state.page = res;
          state.plan = Object.assign({}, state.plan, {
            status: res.status, stale: res.stale, counts: res.counts,
            pending: res.pending, revision: res.revision,
          });
          state.loading = false;
          renderDialog();
        }, function (err) {
          state.loading = false;
          state.error = err;
          renderDialog();
        });
      }

      function recomputePlan() {
        if (!state.plan) return;
        api.requestV3("POST", cfgPlan("/" + enc(state.plan.plan_id) + "/recompute"), { body: {} }).then(function (res) {
          state.plan = res.plan;
          MC.toast.success("计划已重算", "保留 " + (res.preserved || 0) + " 条仍匹配的决议");
          return loadRows();
        }, function (err) { MC.toastError("重算失败", err); });
      }

      function patchDecision(rows, decision) {
        return api.requestV3("PATCH", cfgPlan("/" + enc(state.plan.plan_id) + "/decisions"), {
          body: { scope: "rows", row_ids: rows.map(function (row) { return row.row_id; }), decision: decision },
        }).then(function () { return loadRows(); }, function (err) {
          MC.toastError("决议未保存", err);
          return loadRows();
        });
      }

      function ensureDigest(filter) {
        if (state.page && state.page.filter === filter && state.page.q === state.q) {
          return Promise.resolve(state.page.filter_digest);
        }
        return api.getV3(cfgPlan("/" + enc(state.plan.plan_id) + "/rows")
          + "?offset=0&limit=1&filter=" + enc(filter) + "&q=" + enc(state.q)).then(function (res) {
          return res.filter_digest;
        });
      }

      function batchFilterNew() {
        if (!state.plan || state.page.stale) return;
        ensureDigest("new").then(function (digest) {
          return api.requestV3("PATCH", cfgPlan("/" + enc(state.plan.plan_id) + "/decisions"), {
            body: { scope: "filter", filter: "new", q: state.q, filter_digest: digest, decision: { action: "add" } },
          });
        }).then(function (res) {
          MC.toast.success("已把当前筛选的新增行设为新增", "更新 " + (res.updated || 0) + " 行");
          return loadRows();
        }, function (err) { MC.toastError("批量决议失败", err); });
      }

      function batchPageNew() {
        if (!state.plan || !state.page || state.page.stale) return;
        var rows = (state.page.items || []).filter(function (row) { return row.status === "new"; });
        if (!rows.length) { MC.toast.warning("本页没有新增行"); return; }
        patchDecision(rows, { action: "add" });
      }

      function refreshFooter() {
        var page = state.page;
        if (!page) {
          pendingText.textContent = "尚未加载计划";
          applyBtn.disabled = true;
          return;
        }
        pendingText.textContent = page.stale
          ? "计划已过期，需重算"
          : page.pending ? "待决议 " + page.pending + " 行" : "冲突已全部决议";
        applyBtn.disabled = !canWrite() || page.stale || !(state.plan && state.plan.plan_id) || page.pending > 0;
        applyBtn.title = page.pending > 0 ? "先处理所有冲突行" : "原子提交新增参数";
      }

      function focusBodyFirst() {
        if (document.activeElement && document.activeElement !== document.body) return;
        var target = body.querySelector("select, input, button");
        if (target) target.focus();
      }

      function renderDialog() {
        if (state.plan && state.page && !state.loading && !state.error) renderPlan();
        else renderPicker();
        focusBodyFirst();
      }

      function renderPicker() {
        util.clear(body);
        if (state.loading) {
          body.appendChild(MC.stateBlock({ kind: "loading", title: "正在读取提取计划…" }));
          refreshFooter();
          return;
        }
        if (state.error) {
          body.appendChild(MC.stateBlock({
            kind: "error", title: "提取预览失败", desc: MC.errorText(state.error),
            actions: [
              h("button.btn.btn-sm", { type: "button", onclick: function () { state.error = null; renderDialog(); } }, "返回选择"),
              h("button.btn.btn-sm", { type: "button", onclick: createPlan }, "重新预览"),
            ],
          }));
          refreshFooter();
          return;
        }
        if (!state.files.length) {
          body.appendChild(MC.stateBlock({ kind: "empty", title: "没有可提取的 JSON/XML 文件", desc: "先在左下文件列表上传或保存一个 JSON/XML 配置文件。" }));
          refreshFooter();
          return;
        }
        var sel = h("select.select", { "aria-label": "配置文件", id: "extract-file", name: "file" }, state.files.map(function (f) {
          return h("option", { value: f.file_id, selected: f.file_id === state.fileId }, f.name + " · " + (f.version_label || "当前版本"));
        }));
        sel.addEventListener("change", function () { state.fileId = sel.value; });
        var f = selectedFile();
        body.appendChild(h("div.cv-extract-source",
          h("label.xsmall.muted", "配置文件"), sel,
          h("button.btn.btn-primary", { type: "button", onclick: createPlan }, "预览参数")));
        body.appendChild(h("div.muted.xsmall",
          "目标：固定配置 " + (S.detail ? S.detail.code : "") + " · 当前版本 " + (f ? f.version_id : "—")
          + " · 只读取该不可变版本；创建/切来源/刷新只改计划，不产生参数。"));
      }

      function typeControl(row, d) {
        var value = state.types[row.row_id] !== undefined ? state.types[row.row_id] : (d.type || "");
        var options = [["", "选择类型…"], ["string", "string"], ["number", "number"], ["boolean", "boolean"], ["decimal", "decimal"]];
        var sel = h("select.select", { "aria-label": "null 类型", name: "type", disabled: state.page.stale },
          options.map(function (pair) {
            return h("option", { value: pair[0], selected: pair[0] === value }, pair[1]);
          }));
        sel.addEventListener("change", function () {
          state.types[row.row_id] = sel.value;
          if (d.action === "add" || d.action === "skip") {
            patchDecision([row], { action: d.action, type: sel.value, parameter_id: d.parameter_id || "" });
          } else {
            renderPlan();
          }
        });
        return sel;
      }

      function actionOptions(row, d) {
        if (row.status === "unsupported") return [["skip", "跳过"]];
        if (row.status === "new") return [["add", "新增参数"], ["skip", "跳过"]];
        if (row.status === "existing") return [["link", "关联已有"], ["skip", "跳过"]];
        var options = [];
        if (d.action === "pending") options.push(["pending", "待决议"]);
        if (row.status !== "null_uncertain" && (row.existing_parameter_id || (row.candidates || []).length)) {
          options.push(["link", "关联已有"]);
        }
        options.push(["add", "新增参数"], ["skip", "跳过"]);
        return options;
      }

      function actionControl(row, d) {
        var sel = h("select.select", { "aria-label": "逐行决议", name: "decision", disabled: state.page.stale },
          actionOptions(row, d).map(function (pair) {
            return h("option", { value: pair[0], selected: pair[0] === d.action }, pair[1]);
          }));
        sel.addEventListener("change", function () {
          var action = sel.value;
          var type = state.types[row.row_id] !== undefined ? state.types[row.row_id] : (d.type || "");
          if (action === "add" && row.status === "null_uncertain" && !type) {
            MC.toast.warning("先选择类型", "null 行必须先选类型或跳过。");
            renderPlan();
            return;
          }
          var target = d.parameter_id || row.existing_parameter_id
            || (row.candidates && row.candidates.length ? row.candidates[0].parameter_id : "");
          patchDecision([row], { action: action, type: type, parameter_id: target });
        });
        return sel;
      }

      function linkControl(row, d) {
        if (d.action !== "link") return null;
        var cands = row.candidates || (row.existing_parameter ? [row.existing_parameter] : []);
        if (!cands.length) return null;
        if (cands.length === 1) {
          return h("span.xsmall.muted", "→ " + cands[0].name + " / " + ((cands[0].directory || []).join(" / ") || "根目录"));
        }
        var current = d.parameter_id || cands[0].parameter_id;
        var sel = h("select.select", { "aria-label": "关联目标", name: "parameter_id", disabled: state.page.stale }, cands.map(function (c) {
          return h("option", { value: c.parameter_id, selected: c.parameter_id === current },
            c.name + " / " + ((c.directory || []).join(" / ") || "根目录"));
        }));
        sel.addEventListener("change", function () {
          patchDecision([row], { action: "link", type: d.type || "", parameter_id: sel.value });
        });
        return sel;
      }

      function renderPlan() {
        util.clear(body);
        var page = state.page;
        var counts = page.counts || {};
        body.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          h("span.badge", "计划 " + (counts.total || 0) + " 行"),
          h("span.badge.badge-success", "新增 " + (counts.new || 0)),
          h("span.badge", "已存在 " + (counts.existing || 0)),
          h("span.badge.badge-warning", "冲突 " + (counts.conflict || 0)),
          h("span.badge.badge-muted", "不支持 " + (counts.unsupported || 0)),
          h("span.spacer"),
          h("span.xsmall.muted", "定义修订 r" + (page.revision || 0))));
        if (page.stale) {
          body.appendChild(h("div.banner.banner-warning", icon("info"),
            h("div.banner-body", "来源或目录修订已变化；旧决议仍保留。重算后按 row_id 合并仍合法的决议。",
              h("button.btn.btn-sm", { type: "button", style: "margin-left:10px", onclick: recomputePlan }, "重算并保留决议"))));
        }
        var filter = MC.segmented([
          { value: "all", label: "全部" }, { value: "new", label: "新增" },
          { value: "conflict", label: "冲突" }, { value: "existing", label: "已有" },
        ], state.filter, function (v) { state.filter = v; state.offset = 0; loadRows(); }, "提取筛选");
        var size = h("select.select", { "aria-label": "每页行数" },
          [50, 100].map(function (n) { return h("option", { value: String(n), selected: n === state.size }, n + " 行/页"); }));
        size.addEventListener("change", function () { state.size = +size.value; state.offset = 0; loadRows(); });
        var q = h("input.input.input-sm", { type: "search", name: "q", placeholder: "按名称 / 层级筛选", value: state.q });
        var search = h("button.btn.btn-sm", { type: "button", onclick: function () { state.q = q.value.trim(); state.offset = 0; loadRows(); } }, "搜索");
        q.addEventListener("keydown", function (e) { if (e.key === "Enter") search.click(); });
        body.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" }, filter, size, q, search,
          h("span.spacer"),
          h("button.btn.btn-sm", { type: "button", disabled: page.stale, onclick: batchPageNew }, "本页新增全选"),
          h("button.btn.btn-sm", { type: "button", disabled: page.stale, onclick: batchFilterNew }, "当前筛选全部新增"),
          h("button.btn.btn-sm", { type: "button", onclick: loadRows }, "刷新")));
        var relLabel = release() ? release().version : "未选择软件版本";
        var bindToggle = h("input", { type: "checkbox", checked: state.includeBindings, disabled: page.stale || !S.releaseId });
        bindToggle.addEventListener("change", function () { state.includeBindings = bindToggle.checked; });
        var memberToggle = h("input", { type: "checkbox", checked: state.addMembers, disabled: page.stale || !S.releaseId });
        memberToggle.addEventListener("change", function () { state.addMembers = memberToggle.checked; });
        body.appendChild(h("div.row", { style: "gap:14px;flex-wrap:wrap" },
          h("label.xsmall.row", { style: "gap:4px" }, bindToggle, "补齐已有参数缺失绑定"),
          h("label.xsmall.row", { style: "gap:4px" }, memberToggle, "加入当前软件版本（未设值）"),
          h("span.xsmall.muted", "目标版本：" + relLabel)));

        if (!(page.items || []).length) {
          body.appendChild(MC.stateBlock({ kind: "empty", title: "当前筛选没有行", desc: "换一个筛选条件，或确认来源文件是否包含叶节点。" }));
          refreshFooter();
          return;
        }
        var rows = page.items.map(function (row) {
          var d = row.decision || { action: "pending", type: "", parameter_id: "" };
          var badgeClass = row.status === "new" ? ".badge-success"
            : isExtractConflict(row.status) ? ".badge-warning"
            : row.status === "unsupported" ? ".badge-muted" : "";
          var typeCell = row.status === "null_uncertain" ? typeControl(row, d)
            : h("span.xsmall", d.type || row.suggested_type || "—");
          return h("tr",
            h("td", h("span.badge" + badgeClass, EXTRACT_STATUS[row.status] || row.status)),
            h("td", h("div.cell-strong", row.leaf_name || "—"), h("div.mono.xsmall", (row.directory || []).join(" / ") || "根目录")),
            h("td", typeCell),
            h("td", h("div.col", { style: "gap:4px" }, actionControl(row, d), linkControl(row, d))),
            h("td.mono.xsmall", row.display_path || ""));
        });
        body.appendChild(h("div.table-wrap", h("table.table",
          h("thead", h("tr", h("th", "状态"), h("th", "名称 / 完整层级"), h("th", "类型"), h("th", "决议"), h("th", "来源路径"))),
          h("tbody", rows))));

        var pages = Math.max(1, Math.ceil((page.total || 0) / state.size));
        var current = Math.floor(state.offset / state.size) + 1;
        body.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          h("button.btn.btn-sm", { type: "button", disabled: state.offset <= 0, onclick: function () { state.offset = Math.max(0, state.offset - state.size); loadRows(); } }, "上一页"),
          h("span.xsmall.muted", "第 " + current + " / " + pages + " 页 · 共 " + (page.total || 0) + " 行"),
          h("button.btn.btn-sm", { type: "button", disabled: current >= pages, onclick: function () { state.offset += state.size; loadRows(); } }, "下一页")));
        refreshFooter();
      }

      loadFiles();
    }


    var MERGE_DECISIONS = [
      ["keep_local", "保留本地"], ["adopt_target", "采用目标"], ["custom", "自定义值"],
      ["deactivate", "停用"], ["remove", "移除"], ["pin", "钉住本地"], ["pending", "待决议"],
    ];

    function mergeDialog() {
      if (!S.cid) return;
      var branch = S.branch || { head_id: null, baselines: [] }, context = operationContext();
      var d0 = S.releaseId ? "d0-" + S.releaseId : "";
      var state = { plan: null, page: null, loading: false, error: null, filter: "conflict", custom: {} };
      var body = h("div.col", { style: "gap:12px" });
      var pendingText = h("span.xsmall.muted", { role: "status", "aria-live": "polite" }, "尚未预览");
      var commitBtn = h("button.btn.btn-primary", { type: "button", disabled: true }, "生成新草稿");
      var modal = viewModal({
        title: "预览合并（O / L / N）", size: "xwide", body: body,
        subtitle: "核对三方差异并处理冲突，确认后生成编辑草稿，不自动发布。",
        footer: function (close) {
          return [pendingText, h("span.spacer"),
            h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭"), commitBtn];
        },
      });
      function cfgMerge(rest) { return context.path + "/merge-plans" + (rest || ""); }
      function refreshFooter() {
        if (!state.page) { pendingText.textContent = "尚未预览"; commitBtn.disabled = true; return; }
        pendingText.textContent = state.page.stale ? "计划已过期，请重算" : state.page.pending ? "待决议 " + state.page.pending + " 行" : "冲突已全部决议";
        commitBtn.disabled = !canWrite() || state.page.stale || state.page.pending > 0;
      }
      function loadRows() {
        if (!state.plan) return;
        state.loading = true;
        api.getV3(cfgMerge("/" + enc(state.plan.plan_id) + "/rows?limit=100&filter=" + enc(state.filter))).then(function (res) {
          if (!context.current() || !body.isConnected) return; state.page = res; state.loading = false; render();
        }, function (err) { if (context.current() && body.isConnected) { state.loading = false; state.error = err; render(); } });
      }
      function preview() {
        if (!context.current()) return;
        state.loading = true; state.error = null; render();
        api.requestV3("POST", cfgMerge(""), { body: {
          base_revision_id: baseInput.value.trim() || null,
          ours_revision_id: oursInput.value.trim(),
          theirs_revision_id: theirsInput.value.trim(),
        } }).then(function (res) { state.plan = res.plan; state.page = null; return loadRows(); }, function (err) {
          state.loading = false; state.error = err; render();
        });
      }
      function decide(row, value) {
        api.requestV3("PATCH", cfgMerge("/" + enc(state.plan.plan_id) + "/decisions"), {
          body: { scope: "rows", row_ids: [row.parameter_id], decision: value },
        }).then(function () { return loadRows(); }, function (err) { MC.toastError("决议失败", err); });
      }
      commitBtn.addEventListener("click", function () {
        if (commitBtn.disabled || !state.plan || !context.current()) return;
        var custom = {};
        (state.page.items || []).forEach(function (row) {
          if (row.decision === "custom" && state.custom[row.parameter_id] != null) {
            custom[row.parameter_id] = state.custom[row.parameter_id];
          }
        });
        MC.withBusy(commitBtn, function () {
          return api.requestV3("POST", cfgMerge("/" + enc(state.plan.plan_id) + "/commit"), { body: { custom_values: custom } }).then(function (res) {
            if (!context.current()) return;
            MC.toast.success("已生成新草稿", res.draft ? res.draft.draft_id : "");
            S.selected = res.draft ? res.draft.draft_id : S.selected; S.tab = "workbench"; modal.close(); reloadDetail();
          }, function (err) {
            MC.toastError("生成草稿失败", err, err.code === "PLAN_STALE" ? { action: { label: "刷新", onClick: loadRows } } : null);
          });
        });
      });
      var choices = S.revisions.filter(function (r) { return r.status === "published"; });
      function revisionSelect(name, label, value, optional) {
        return h("select.select", { name: name, "aria-label": label }, optional ? h("option", { value: "" }, "未指定共同基准") : null, choices.map(function (r) { return h("option", { value: r.id, selected: r.id === value }, revisionLabel(r)); }));
      }
      var baseInput = revisionSelect("base_revision_id", "共同基准 O", revision() && revision().source || (branch.baselines && branch.baselines[0] || {}).baseline_id || "", true);
      var oursInput = revisionSelect("ours_revision_id", "当前配置 L", revision() && revision().status === "published" ? S.selected : branch.head_id || d0);
      var theirsInput = revisionSelect("theirs_revision_id", "目标配置 N", d0);
      [baseInput, oursInput, theirsInput].forEach(function (select) { select.addEventListener("change", function () { state.plan = null; state.page = null; state.custom = {}; refreshFooter(); }); });
      function render() {
        var hadFocus = document.activeElement && document.activeElement !== document.body;
        util.clear(body);
        body.appendChild(h("div.cv-merge-sources",
          MC.field({ label: "共同基准 O", control: baseInput }), MC.field({ label: "当前配置 L", control: oursInput }),
          MC.field({ label: "目标配置 N", control: theirsInput }),
          h("button.btn.btn-primary", { type: "button", onclick: preview }, "预览合并")));
        if (state.loading) { body.appendChild(MC.stateBlock({ kind: "loading", title: "读取合并计划…" })); refreshFooter(); return; }
        if (state.error) { body.appendChild(MC.stateBlock({ kind: "error", title: "合并预览失败", desc: MC.errorText(state.error) })); refreshFooter(); return; }
        if (!state.page) {
          refreshFooter();
          if (!hadFocus) {
            var initial = body.querySelector("select, input, button");
            if (initial) initial.focus();
          }
          return;
        }
        var filter = MC.segmented([
          { value: "conflict", label: "仅冲突" }, { value: "all", label: "全部" },
          { value: "resolved", label: "已决议" },
        ], state.filter, function (v) { state.filter = v; loadRows(); }, "合并筛选");
        body.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          filter, h("span.xsmall.muted", "模式 " + state.page.mode + " · " + (state.page.total || 0) + " 行")));
        if (state.page.stale) {
          body.appendChild(h("div.banner.banner-warning", icon("info"), h("div.banner-body", "O/L/N 来源已变化，旧决议仍保留；请重新预览后再提交。")));
        }
        var rows = (state.page.items || []).map(function (row) {
          var sel = h("select.select", { "aria-label": "合并决议", name: "merge_decision", disabled: state.page.stale },
            row.allowed_decisions.map(function (value) {
              var label = value;
              MERGE_DECISIONS.forEach(function (pair) { if (pair[0] === value) label = pair[1]; });
              return h("option", { value: value, selected: row.decision === value }, label);
            }));
          sel.addEventListener("change", function () { decide(row, sel.value); });
          var customInput = null;
          if (row.decision === "custom") {
            customInput = h("input.input.input-sm", { "aria-label": (row.name || row.parameter_id) + "的合并自定义值", value: state.custom[row.parameter_id] || "" });
            customInput.addEventListener("input", function () { state.custom[row.parameter_id] = customInput.value; });
          }
          return h("tr",
            h("td", h("div.cell-strong", row.name || row.parameter_id), h("div.mono.xsmall", row.parameter_id)),
            h("td.xsmall", row.base ? row.base.value.text : "—"),
            h("td.xsmall", row.ours ? row.ours.value.text : "—"),
            h("td.xsmall", row.theirs ? row.theirs.value.text : "—"),
            h("td", h("div.col", { style: "gap:4px" }, sel, customInput)));
        });
        body.appendChild(h("div.table-wrap", h("table.table",
          h("thead", h("tr", h("th", "参数"), h("th", "O 基准"), h("th", "L 本地"), h("th", "N 目标"), h("th", "决议"))),
          h("tbody", rows))));
        refreshFooter();
        if (!hadFocus) {
          var first = body.querySelector("select, input, button");
          if (first) first.focus();
        }
      }
      render();
    }

    function overviewView(rel) {
      if (!rel) return MC.stateBlock({ kind: "empty", title: "先创建软件版本" });
      var ov = S.overview;
      var releaseSelect = h("select.select", { "aria-label": "查看的软件版本", onchange: function () { var def = S.revisions.filter(function (n) { return n.id === "d0-" + releaseSelect.value; })[0]; if (def) chooseRevision(def, "overview"); } }, (S.detail.releases || []).map(function (r) { return h("option", { value: r.release_id, selected: r.release_id === S.releaseId }, r.version); }));
      var compareSel = h("select.select", { "aria-label": "对比版本", onchange: function () { S.overviewCompare = compareSel.value; S.overviewOffset = 0; loadOverview().then(render); } }, h("option", { value: "" }, "不对比前版"),
        (S.detail.releases || []).filter(function (r) { return r.release_id !== S.releaseId; }).map(function (r) { return h("option", { value: r.release_id, selected: r.release_id === S.overviewCompare }, r.version); }));
      var diff = h("input", { type: "checkbox", checked: S.overviewOnly, disabled: !S.overviewCompare, onchange: function () { S.overviewOnly = diff.checked; S.overviewOffset = 0; loadOverview().then(render); } });
      var head = h("div.cv-toolbar", h("label.row.small", { style: "gap:6px" }, "软件版本", releaseSelect), h("label.row.small", { style: "gap:6px" }, "对比", compareSel), h("label.row.small", { style: "gap:6px" }, diff, "仅看变更"), h("span.spacer"),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: function () { parameterDialog(); } }, icon("plus"), "手动新增"),
        h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite(), onclick: extractDialog }, icon("file"), "从配置文件提取"));
      if (S.overviewError) return h("div.col", head, MC.errorBlock(S.overviewError, function () { loadOverview().then(render); }));
      if (!ov) return h("div.col", head, MC.stateBlock({ kind: "loading", title: "读取参数总览…" }));
      var search = h("input.input", { type: "search", "aria-label": "搜索参数总览", placeholder: "搜索参数名、层级或实际名", value: S.overviewQ || "" });
      var searchButton = h("button.btn.btn-sm", { type: "button", onclick: function () { S.overviewQ = search.value; S.overviewOffset = 0; loadOverview().then(render); } }, "搜索");
      search.addEventListener("keydown", function (e) { if (e.key === "Enter") searchButton.click(); });
      if (!S.overviewColumns) S.overviewColumns = {};
      var allColumns = ov.columns || [], baselineColumns = allColumns.filter(function (c) { return c.kind === "baseline"; });
      var selectedBaseline = baselineColumns.filter(function (c) { return c.column_id === S.overviewColumns.baseline; })[0] || baselineColumns[baselineColumns.length - 1];
      if (selectedBaseline) S.overviewColumns.baseline = selectedBaseline.column_id;
      var previousColumn = allColumns.filter(function (c) { return c.kind === "compare"; })[0];
      var columns = allColumns.filter(function (c) { return c.kind !== "compare" && (c.kind !== "baseline" || c === selectedBaseline); });
      var header = h("tr", h("th", "参数 / 来源层级"), h("th", "本版名称与绑定"));
      columns.forEach(function (c) {
        var options = [], key = null, title = c.label;
        if (c.kind === "default") { options = [{ value: c.column_id, label: c.label }]; title = "默认值"; }
        else if (c.kind === "baseline") { key = "baseline"; title = "基线值"; options = ((S.branch && S.branch.baselines) || []).filter(function (b) { return b.release_id === S.releaseId; }).map(function (b) { return { value: b.baseline_id, label: b.label }; }); }
        else if (c.kind === "machine") { key = c.machine_id; title = c.machine_no || c.label; var machine = S.machines.filter(function (m) { return m.machine_id === c.machine_id; })[0]; options = (machine && machine.revisions || []).filter(function (r) { return !r.release_id || r.release_id === S.releaseId; }).map(function (r) { return { value: r.revision_id, label: r.label }; }); }
        var choice = h("select.select", { "aria-label": title + "修订", onchange: function () { if (!key) return; S.overviewColumns[key] = choice.value; S.overviewOffset = 0; loadOverview().then(render); } }, options.map(function (option) { return h("option", { value: option.value, selected: option.value === c.column_id }, option.label); }));
        header.appendChild(h("th", h("div.col", { style: "gap:8px" }, h("span", title), choice)));
      });
      header.appendChild(h("th", "历史"));
      var rows = (ov.rows || []).map(function (r) {
        var binding = r.binding || (S.bindings || []).filter(function (b) { return b.parameter_id === r.parameter_id; })[0];
        var tr = h("tr", h("td.cell-strong", h("button.btn.btn-ghost.cv-object-link", { type: "button", onclick: function () { parameterDialog(r); } }, r.name), h("div.mono.xsmall.muted", r.parameter_id), h("div.small.muted", (r.directory || []).join(" / "))),
          h("td", h("button.btn.btn-ghost.cv-binding-link", { type: "button", onclick: function () { bindingDialog(r, binding); } }, binding ? binding.locator : "本版未绑定")));
        columns.forEach(function (c) {
          var cell = r.cells[c.column_id] || {}, type = cell.type == null ? r.type : cell.type, unit = cell.unit == null ? r.unit : cell.unit;
          tr.appendChild(h("td", h("strong.mono", cell.status === "ok" ? cell.value === "" ? '""' : String(cell.value == null ? "未设" : cell.value) : ({ unset: "未设", null: "null", not_introduced: "本版不包含", removed: "已移除", disabled: "已停用" }[cell.status] || "未形成修订")), h("div.small.muted", (type || "") + (unit ? " · " + unit : "")),
            cell.intent === "pin" ? h("span.badge.badge-warning", "固定值") : null,
            c.kind === "default" && previousColumn ? h("div.small.muted", "对比版默认：" + memberText(r.cells[previousColumn.column_id])) : null));
        });
        tr.appendChild(h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { showHistory(r); } }, "查看历程"))); return tr;
      });
      var folder = folderBrowser("", S.overviewDirectory || "", S.detail.parameter_count == null ? S.overviewTotal : S.detail.parameter_count, function (path) {
        S.overviewDirectory = path; S.overviewOffset = 0; loadOverview().then(render);
      });
      return h("div.cv-workbench", head, h("div.cv-param-browser", folder, h("section.cv-param-data", h("div.cv-toolbar", search, searchButton, h("span.spacer"), h("span.small.muted", S.overviewTotal + " 项")), h("div.cv-folder-trail.small", S.overviewDirectory || "全部参数"),
        h("div.table-wrap", { "aria-label": "参数版本总览，可横向滚动", tabindex: "0" }, h("table.table", h("thead", header), h("tbody", rows.length ? rows : h("tr", h("td", { colspan: columns.length + 3 }, "没有匹配的参数"))))),
        h("div.cv-param-footer", pagerRow(S.overviewTotal, S.overviewOffset, S.overviewLimit, function (offset) { S.overviewOffset = offset; loadOverview().then(render); })))));
    }

    function parameterDialog(parameter) {
      if (!canWrite()) { viewModal({ title: parameter.name, body: h("pre.small", { style: "white-space:pre-wrap;overflow-wrap:anywhere" }, JSON.stringify(parameter, null, 2)) }); return; }
      var name = h("input.input", { value: parameter && parameter.name || "" }), path = h("input.input", { value: parameter ? (parameter.directory || []).join("/") : S.overviewDirectory || "" }), unit = h("input.input", { value: parameter && parameter.unit || "" });
      var types = ["number", "string", "bool", "integer"];
      if (parameter && parameter.type && types.indexOf(parameter.type) < 0) types.push(parameter.type);
      var type = h("select.select", types.map(function (value) { return h("option", { value: value, selected: value === (parameter && parameter.type || "number") }, value); }));
      var desc = h("textarea.input", { value: parameter && parameter.description || "" });
      var targetRelease = release(), expectedDraft = S.draft && S.draft.revision, expectedDefinition = S.overview && S.overview.catalog_revision;
      var adopt = h("input", { type: "checkbox", checked: !!targetRelease && !targetRelease.default_revision_id, disabled: !targetRelease || !!targetRelease.default_revision_id });
      var fields = { name: MC.field({ label: "参数名称", required: true, control: name }), directory: MC.field({ label: "来源层级", control: path, help: "使用 / 分隔目录层级" }), type: MC.field({ label: "类型", control: type }), unit: MC.field({ label: "单位", control: unit }), description: MC.field({ label: "说明", control: desc }) };
      configFormModal({ title: parameter ? "参数身份属性" : "手动新增参数", body: h("div.col", { style: "gap:12px" }, fields.name, fields.directory, fields.type, fields.unit, fields.description, h("label.row.small", { style: "gap:6px" }, adopt, "同步到当前准备版本的定义"), h("p.small.muted", "不改变已有参数值；已固化版本保留原定义快照。")), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button", onclick: function () {
          var payload = { name: name.value, directory: path.value.split("/").filter(Boolean), type: type.value, unit: unit.value, description: desc.value };
          if (adopt.checked && targetRelease && !targetRelease.default_revision_id) { payload.release_id = targetRelease.release_id; payload.expected_draft_revision = expectedDraft; payload.expected_definition_revision = expectedDefinition; }
          MC.withBusy(save, function () { return api.requestV3(parameter ? "PATCH" : "POST", cfgPath("/parameters" + (parameter ? "/" + enc(parameter.parameter_id) : "")), { body: payload }).then(function () { if (close(true) === false) return; MC.toast.success("参数身份已保存"); reloadDetail(); }, function (err) { (fields[err.detail && err.detail.field] || fields.name).setError(MC.errorText(err)); }); });
        } }, "保存");
        return [h("button.btn", { type: "button", onclick: close }, "取消"), save];
      } });
    }

    function sourceFieldPicker(file, onPick) {
      var body = h("div.col", { style: "gap:12px" }), seq = 0, offset = 0, keyword = "", closed = false, epoch = S.contextEpoch;
      var modal = viewModal({ title: "选择文件字段 · " + file.name, size: "wide", body: body });
      body.addEventListener("removed", function () { closed = true; });
      function draw() {
        var request = ++seq;
        util.clear(body);
        var search = h("input.input", { type: "search", "aria-label": "搜索文件字段", value: keyword, placeholder: "搜索字段名称或目录" });
        function find() { keyword = search.value.trim(); offset = 0; draw(); }
        search.addEventListener("keydown", function (e) { if (e.key === "Enter") find(); });
        body.appendChild(h("div.cv-toolbar", search, h("button.btn", { type: "button", onclick: find }, "搜索")));
        var content = h("div", MC.stateBlock({ kind: "loading", title: "读取文件字段…" })); body.appendChild(content);
        api.getV3(cfgPath("/extract-preview"), { version_id: file.version_id, offset: offset, limit: 50, q: keyword }).then(function (res) {
          if (closed || disposed || request !== seq || epoch !== S.contextEpoch || !body.isConnected) return;
          util.clear(content);
          var rows = (res.items || []).filter(function (row) { return row.locator_kind !== "attribute" && row.locator_kind !== "element_text"; });
          content.appendChild(rows.length ? h("div.table-wrap.cv-field-picker", h("table.table", h("thead", h("tr", h("th", "字段"), h("th", "文件路径"), h("th", "操作"))), h("tbody", rows.map(function (row) {
            return h("tr", h("td", h("strong", row.leaf_name), h("span.small.muted", " · " + (row.suggested_type || row.value_kind))), h("td.mono", row.display_path), h("td", h("button.btn.btn-sm", { type: "button", onclick: function () { closed = true; modal.close(); onPick(row.display_path); } }, "选择字段")));
          })))) : MC.stateBlock({ kind: "empty", title: "没有可选字段", desc: keyword ? "调整搜索条件后重试。" : "此页没有可作为参数的叶子字段。" }));
          content.appendChild(pagerRow(res.total, offset, 50, function (value) { offset = value; draw(); }));
        }, function (err) { if (request === seq && body.isConnected) { util.clear(content); content.appendChild(MC.errorBlock(err, draw)); } });
      }
      draw();
    }

    function bindingDialog(parameter, binding) {
      var rel = release(), epoch = S.contextEpoch, scope = cfgPath(""), readonly = !canWrite() || !!rel.default_revision_id;
      var locator = h("input.input.input-mono", { value: binding && binding.locator || "", readOnly: readonly });
      var field = MC.field({ label: "本版文件字段", required: true, control: locator });
      var files = [], select = h("select.select", { "aria-label": "绑定来源文件", disabled: true }, h("option", { value: "" }, "读取配置文件…"));
      var picker = h("button.btn", { type: "button", disabled: true, onclick: function () { var f = files.filter(function (row) { return row.version_id === select.value; })[0]; if (f) sourceFieldPicker(f, function (path) { locator.value = path; locator.dispatchEvent(new Event("input", { bubbles: true })); field.setError(""); }); } }, "从文件选择字段");
      var body = h("div.col", { style: "gap:12px" }, h("strong", parameter.name + " · " + rel.version), readonly ? null : MC.field({ label: "来源配置文件", control: select }), readonly ? null : picker, field,
        readonly ? h("p.small.muted", "版本已固化，绑定只读。新版本可建立自己的绑定。") : h("p.small.muted", "选择对应字段后确认，不会读取或覆盖草稿值。INI 可填写 section/key 精确位置。"));
      var expected = S.bindingRevision;
      configFormModal({ title: "版本绑定", body: body, footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button", disabled: readonly, onclick: function () { var f = files.filter(function (row) { return row.version_id === select.value; })[0];
          MC.withBusy(save, function () { return api.requestV3("POST", scope + "/releases/" + enc(rel.release_id) + "/bindings", { body: { parameter_id: parameter.parameter_id, locator: locator.value.trim(), confirmed: true, verify_source: !!f, expected_revision: expected, binding_id: binding && binding.binding_id || "", file_id: f ? f.file_id : binding && binding.file_id || "", version_id: f ? f.version_id : binding && binding.version_id || "" } }).then(function () { if (close(true) === false) return; reloadDetail(); MC.toast.success("版本绑定已确认"); }, function (err) { field.setError(MC.errorText(err)); }); });
        } }, "确认绑定");
        return readonly ? [h("button.btn", { type: "button", onclick: close }, "关闭")] : [h("button.btn", { type: "button", onclick: close }, "取消"), save];
      } });
      if (!readonly) api.getV3(root("/file-selector")).then(function (res) {
        if (disposed || epoch !== S.contextEpoch || !body.isConnected) return;
        files = (res.items || []).filter(function (f) { return /\.(xml|json)$/i.test(f.name); });
        util.clear(select); select.appendChild(h("option", { value: "" }, files.length ? "选择来源文件" : "暂无 XML / JSON 文件"));
        files.forEach(function (f) { select.appendChild(h("option", { value: f.version_id, selected: binding && binding.version_id === f.version_id }, f.name + " · " + (f.version_label || "当前版本"))); });
        select.disabled = !files.length; picker.disabled = !select.value;
        select.addEventListener("change", function () { picker.disabled = !select.value; locator.value = ""; });
      }, function (err) { if (body.isConnected) { field.setError(MC.errorText(err)); util.clear(select); select.appendChild(h("option", "文件读取失败，可重新打开后重试")); } });
    }

    function showDiff() {
      var rid = "d0-" + S.releaseId;
      api.getV3(cfgPath("/revisions/" + enc(rid) + "/diff")).then(function (res) {
        var rows = (res.rows || []).map(function (r) {
          return h("li", h("strong", r.name_after || r.name_before || r.parameter_id), " · ",
            r.categories.join(" / "),
            h("span.xsmall.muted", "  " + (r.before && r.before.status || "—") + " → " + (r.after && r.after.status || "—")));
        });
        viewModal({ title: "与前一修订对比", body: rows.length ? h("ul.mini-list", rows) : h("div.muted", "前一版本无差异或没有前一版本。") });
      }, function (err) { MC.toastError("读取差异失败", err); });
    }

    function showHistory(r) {
      api.getV3(cfgPath("/parameters/" + enc(r.parameter_id) + "/history")).then(function (res) {
        var rows = (res.items || []).map(function (it) {
          return h("li", h("div.col", { style: "gap:4px" }, h("strong", it.label), h("span.mono.xsmall.muted", it.revision_id),
            it.previous_id ? h("span.xsmall.muted", "前序修订：" + it.previous_id) : null),
            h("div.col", { style: "gap:4px" }, h("span.small.muted", { ok: "有值", unset: "未设", null: "null", removed: "已移除", disabled: "已停用", not_introduced: "未纳入" }[it.value.status] || it.value.status), h("strong.mono", memberText(it.value))));
        });
        viewModal({ title: "参数历程 · " + r.name, body: rows.length ? h("ul.cv-param-history", rows) : h("div.muted", "没有历史。") });
      }, function (err) { MC.toastError("读取历程失败", err); });
    }


    function exportRevisions(rel) {
      var options = [{ id: "d0-" + S.releaseId, label: "D0 " + rel.version }];
      ((S.branch && S.branch.baselines) || []).forEach(function (row) {
        if (!row.release_id || row.release_id === S.releaseId) {
          options.push({ id: row.baseline_id, label: row.label + " · " + (row.published_at || "") });
        }
      });
      (S.machines || []).forEach(function (m) {
        if (m.release_id && m.release_id !== S.releaseId) return;
        (m.revisions || []).forEach(function (row) {
          options.push({ id: row.revision_id, label: "机台 " + m.machine_no + " · " + row.label });
        });
      });
      return options;
    }

    function exportView(rel) {
      if (!rel) return MC.stateBlock({ kind: "empty", title: "先创建软件版本" });
      var revisions = exportRevisions(rel);
      if (!S.exportRevisionId || !revisions.some(function (r) { return r.id === S.exportRevisionId; })) S.exportRevisionId = S.selected || revisions[0] && revisions[0].id;
      if (S.exportFiles === null) {
        S.exportFiles = [];
        api.getV3(root("/file-selector")).then(function (res) { if (disposed) return; S.exportFiles = (res.items || []).filter(function (f) { return ["json", "xml", "ini"].indexOf(String(f.name || "").split(".").pop().toLowerCase()) >= 0; }); render(); }, function (err) { if (!disposed) { S.exportError = err; render(); } });
      }
      if (!S.exportFileId && S.exportFiles.length) S.exportFileId = S.exportFiles[0].version_id;
      var revSel = h("select.select", { "aria-label": "导出修订", disabled: S.exportBusy }, revisions.map(function (r) { return h("option", { value: r.id, selected: r.id === S.exportRevisionId }, r.label); }));
      revSel.addEventListener("change", function () { S.exportRevisionId = revSel.value; S.exportPlan = null; S.exportArtifact = null; var target = S.revisions.filter(function (r) { return r.id === revSel.value; })[0]; if (target) chooseRevision(target, "export"); else render(); });
      var fileSel = h("select.select", { "aria-label": "模板文件", name: "version_id", disabled: S.exportBusy }, h("option", { value: "" }, "请选择文件"), S.exportFiles.map(function (f) { return h("option", { value: f.version_id, selected: f.version_id === S.exportFileId }, f.name); }));
      fileSel.addEventListener("change", function () { S.exportFileId = fileSel.value; S.exportMappings = {}; S.exportPlan = null; S.exportArtifact = null; render(); });
      var confirm = h("input", { type: "checkbox", checked: S.exportConfirm, disabled: S.exportBusy, onchange: function () { S.exportConfirm = confirm.checked; invalidate(); } });
      var message = h("p.small.cv-warning", { role: "status" });
      function invalidate() { S.exportDirty = true; S.exportPlan = null; S.exportArtifact = null; message.textContent = "设置已修改，请重新预览。"; Array.prototype.forEach.call(el.querySelectorAll("[data-export-commit], [data-export-download]"), function (b) { b.disabled = true; }); }
      var exportReady = revision() && revision().status === "published";
      var preview = h("button.btn.btn-primary", { type: "button", disabled: !canWrite() || !exportReady || !S.exportFileId || S.exportBusy, onclick: function () {
        var scope = cfgPath(""), rev = S.exportRevisionId, version = S.exportFileId;
        S.exportBusy = true; S.exportPlan = null; S.exportArtifact = null; S.exportError = null; render();
        api.requestV3("POST", scope + "/export-plans", { body: { release_id: S.releaseId, revision_id: rev, version_id: version, confirm_removed: !!S.exportConfirm, mappings: S.exportMappings || {} } }).then(function (res) {
          if (disposed || scope !== cfgPath("") || rev !== S.exportRevisionId || version !== S.exportFileId) return;
          S.exportPlan = res.plan; S.exportBusy = false; S.exportDirty = false; render();
        }, function (err) { if (!disposed && scope === cfgPath("")) { S.exportBusy = false; S.exportError = err; render(); } });
      } }, S.exportBusy ? "正在生成预览…" : "预览导出");
      var source = h("section.cv-export-source", h("div.cv-detail-head", h("h2", "导出来源")), h("div.cv-detail-body.col", { style: "gap:16px" },
        MC.field({ label: "配置修订", control: revSel }), MC.field({ label: "配置文件中的模板", control: fileSel }), h("button.btn", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "files" }); } }, "查看配置文件"),
        h("label.row.small", { style: "gap:6px" }, confirm, "确认停用 / 移除字段保持原样"), !exportReady ? h("p.small.cv-warning", "此修订尚未固化，请先确认修订再导出。") : null, preview, h("details.cv-help", h("summary", "文件保留规则"), h("p.small.muted", "仅写入已确认的精确落点；未匹配字段保持原样。XML / INI 保留注释，JSON 会重新排版；生成新文件，不覆盖原文件。"))));
      var main = h("section.cv-export-preview", h("div.cv-detail-head", h("h2", S.exportPlan ? "字段映射与预览" : "核对模板映射")));
      var content = h("div.cv-detail-body.col", { style: "gap:14px" }); main.appendChild(content);
      if (S.exportError) content.appendChild(MC.errorBlock(S.exportError, function () { S.exportError = null; S.exportFiles = null; render(); }));
      if (!S.exportFiles.length && !S.exportError) content.appendChild(MC.stateBlock({ kind: "empty", title: "没有 JSON/XML/INI 模板", desc: "先从左侧配置文件列表导入模板文件。" }));
      var plan = S.exportPlan, mapping = plan && plan.mapping || [];
      var parameters = mapping.length ? mapping : (S.paramPage && S.paramPage.items || []).slice(0, 50).map(function (p) { var binding = (S.bindings || []).filter(function (b) { return b.parameter_id === p.parameter_id; })[0]; return { parameter_id: p.parameter_id, name: p.name, locator: binding && binding.locator || "", value: S.draft && S.draft.values && S.draft.values[p.parameter_id] && S.draft.values[p.parameter_id].value }; });
      if (parameters.length) content.appendChild(h("div.table-wrap", h("table.table.cv-export-mapping", h("thead", h("tr", h("th", "参数"), h("th", "本次模板落点"), h("th", "输出值"))), h("tbody", parameters.map(function (p) {
        var input = h("input.input.input-sm.input-mono", { "aria-label": p.name + "的模板落点", placeholder: "填写精确路径；留空则不输出", disabled: !canWrite() || S.exportBusy, value: S.exportMappings && Object.prototype.hasOwnProperty.call(S.exportMappings, p.parameter_id) ? S.exportMappings[p.parameter_id] : p.locator || "", oninput: function () { if (!S.exportMappings) S.exportMappings = {}; S.exportMappings[p.parameter_id] = input.value; invalidate(); } });
        return h("tr", h("td", h("strong", p.name), h("div.mono.xsmall.muted", p.parameter_id)), h("td", h("div.row", { style: "gap:6px" }, input, h("button.btn.btn-sm", { type: "button", disabled: !canWrite() || S.exportBusy || !/\.(json|xml)$/i.test((S.exportFiles.filter(function (f) { return f.version_id === S.exportFileId; })[0] || {}).name || ""), onclick: function () { var f = S.exportFiles.filter(function (row) { return row.version_id === S.exportFileId; })[0]; if (f) sourceFieldPicker(f, function (path) { input.value = path; input.dispatchEvent(new Event("input")); }); } }, "选字段"))), h("td.mono", p.value_state === "null" ? "null" : p.value === "" ? '""' : p.value == null ? "未设" : String(p.value)));
      })))));
      content.appendChild(message);
      if (plan) {
        content.appendChild(h("div.small", "已匹配 " + plan.applied + " 项 · 未输出 " + Math.max(0, (plan.mapping_total || plan.applied) - plan.applied) + " 项 · 其他模板字段保持原样"));
        if (plan.mapping_total > 50) content.appendChild(pagerRow(plan.mapping_total, plan.mapping_offset || 0, 50, function (offset) { api.getV3(cfgPath("/export-plans/" + enc(plan.plan_id)), { offset: offset, limit: 50 }).then(function (res) { if (S.exportPlan === plan) { S.exportPlan = res.plan; render(); } }, function (err) { MC.toastError("读取映射失败", err); }); }));
        content.appendChild(h("pre.cv-export-code.mono", plan.preview || ""));
        content.appendChild(h("span.small.muted", "文本预览最多显示 4000 字符；生成和下载使用完整文件。"));
        var commit = h("button.btn.btn-primary", { type: "button", "data-export-commit": "1", disabled: !canWrite() || S.exportBusy || !plan.applied, onclick: function () {
          MC.withBusy(commit, function () { return api.requestV3("POST", cfgPath("/export-plans/" + enc(plan.plan_id) + "/commit"), { body: { preview_token: plan.preview_token } }).then(function (res) { if (S.exportPlan !== plan || disposed) return; S.exportArtifact = res.artifact; render(); }, function (err) { MC.toastError("生成文件失败", err); }); });
        } }, "生成不可变文件"); content.appendChild(commit);
      }
      if (S.exportArtifact) {
        var art = S.exportArtifact;
        content.appendChild(h("button.btn.btn-primary", { type: "button", "data-export-download": "1", onclick: function () { api.download(root("/exports/" + enc(art.artifact_id) + "/download")); } }, icon("download"), "下载 " + art.filename));
      }
      return h("div.cv-export", h("div.cv-export-progress", h("span", "1 配置修订"), icon("chevron-right"), h("span", "2 选择模板"), icon("chevron-right"), h("span", "3 核对映射"), icon("chevron-right"), h("strong", "4 预览并下载")), h("div.cv-export-workflow", source, main));
    }

    function historyView() {
      if (!S.cid) return MC.stateBlock({ kind: "empty", title: "先选择配置" });
      if (S.historyEvents === null && !S.historyLoading && !S.historyError) {
        S.historyLoading = true;
        var scope = cfgPath("/events"), request = ++historySeq;
        api.getV3(scope, { q: S.historyQ, offset: S.historyOffset || 0, limit: 50 }).then(function (res) {
          if (disposed || request !== historySeq || scope !== cfgPath("/events")) return;
          S.historyEvents = res.items || []; S.historyTotal = res.total == null ? S.historyEvents.length : res.total; S.historyLoading = false; render();
        }, function (err) { if (disposed || request !== historySeq || scope !== cfgPath("/events")) return; S.historyLoading = false; S.historyError = err; render(); });
      }
      var search = h("input.input", { type: "search", "aria-label": "搜索配置历史", placeholder: "搜索操作、参数、操作人或 IP", value: S.historyQ });
      var button = h("button.btn.btn-sm", { type: "button", onclick: function () { historySeq++; S.historyLoading = false; S.historyQ = search.value.trim(); S.historyOffset = 0; S.historyEvents = null; S.historyError = null; render(); } }, "搜索");
      search.addEventListener("keydown", function (e) { if (e.key === "Enter") button.click(); });
      var labels = { restore: "从历史修订恢复草稿", export: "生成导出文件", extract: "提取参数", merge: "合并配置", import: "导入参数值", freeze: "确认版本默认", baseline: "发布版本基线", machine: "发布机台修订", create_config: "创建固定配置", create_release: "创建软件版本" };
      var events = (S.historyEvents || []).map(function (ev) {
        var source = ev.source || {}, actions = h("div.cv-toolbar", { style: "padding-bottom:0" });
        if (source.revision_id) {
          var r = S.revisions.filter(function (item) { return item.id === source.revision_id; })[0];
          if (r) actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { chooseRevision(r, "workbench"); } }, "查看修订"));
          if (canWrite()) actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () {
            var context = operationContext();
            api.requestV3("POST", context.path + "/restore-plans", { body: { revision_id: source.revision_id, request_id: util.uid("restore") } }).then(function (res) {
              if (!context.current()) return;
              S.selected = res.draft.draft_id; S.tab = "workbench"; S.historyEvents = null; reloadDetail(); MC.toast.success("已生成恢复草稿");
            }, function (err) { MC.toastError("恢复失败", err); });
          } }, "从该修订恢复"));
        }
        return h("article.cv-history-event", h("time", util.formatTime(ev.at)), h("div", h("h3", labels[ev.type] || ev.type), h("span.mono.xsmall.muted", ev.event_id || ""),
          h("details.cv-help", h("summary", "操作详情"), h("pre.mono.small", JSON.stringify({ before: ev.before, after: ev.after, source: ev.source, request_id: ev.request_id, undo_of: ev.undo_of }, null, 2))), actions),
          h("div.col.small", h("strong", ev.principal || "—"), h("span.mono.muted", ev.ip || "—")));
      });
      return h("div.cv-history", h("div.cv-toolbar", h("span.small.muted", (S.historyTotal || 0) + " 条记录 · 最新优先"), h("span.spacer"), search, button, h("button.btn.btn-sm", { type: "button", onclick: function () { historySeq++; S.historyLoading = false; S.historyEvents = null; S.historyError = null; render(); } }, "刷新")),
        S.historyLoading ? MC.stateBlock({ kind: "loading", title: "读取事件…" }) : S.historyError ? MC.stateBlock({ kind: "error", title: "读取配置事件失败", desc: MC.errorText(S.historyError), actions: [h("button.btn", { type: "button", onclick: function () { S.historyError = null; S.historyEvents = null; render(); } }, "重试")] })
          : events.length ? h("div.cv-history-events", events) : MC.stateBlock({ kind: "empty", title: "没有配置事件", desc: "配置的导入、提取、发布、导出和恢复操作会记录在这里。" }),
        pagerRow(S.historyTotal || 0, S.historyOffset || 0, 50, function (offset) { historySeq++; S.historyLoading = false; S.historyOffset = offset; S.historyEvents = null; S.historyError = null; render(); }));
    }

    function configFormModal(options) {
      var dirty = false, nativeClose, footer = options.footer, epoch = S.contextEpoch, wid = MC.workspaces.scopeId(), form = { dirty: false };
      formDrafts.push(form);
      var onClose = options.onClose; options.onClose = function () { formDrafts = formDrafts.filter(function (item) { return item !== form; }); if (onClose) onClose(); };
      function current() { return !disposed && epoch === S.contextEpoch && wid === MC.workspaces.scopeId(); }
      options.body.addEventListener("input", function () { dirty = true; form.dirty = true; });
      options.body.addEventListener("change", function () { dirty = true; form.dirty = true; });
      function close(saved) {
        if (saved === true && !current()) return false;
        if (saved === true || !dirty) { nativeClose(); return true; }
        viewConfirm({ title: "放弃未保存的修改？", message: "当前表单尚未保存。", confirmText: "放弃修改", cancelText: "继续编辑" }).then(function (ok) { if (ok) nativeClose(); });
      }
      options.onDismiss = function () { close(); };
      options.footer = function (done) { nativeClose = done; var buttons = footer(close); buttons.forEach(function (button) { if (button.classList.contains("btn-primary") || button.classList.contains("btn-danger")) button.addEventListener("click", function (event) { if (!current()) { event.preventDefault(); event.stopImmediatePropagation(); MC.toast.warning("上下文已变化，请重新打开表单"); } }, true); }); return buttons; };
      return viewModal(options);
    }

    function createConfigDialog() {
      var code = h("input.input.input-mono", { type: "text", placeholder: "MC-001" });
      var name = h("input.input", { type: "text", placeholder: "例如 一号机配置" });
      var F = { code: MC.field({ label: "配置标识", required: true, control: code }), name: MC.field({ label: "配置名称", required: true, control: name }) };
      configFormModal({ title: "新建固定配置", body: h("div.col", { style: "gap:12px" }, F.code, F.name), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            return api.requestV3("POST", root("/configs"), { body: { code: code.value.trim(), name: name.value.trim() } }).then(function (res) { if (close(true) === false) return; MC.toast.success("已创建 " + res.config.name); openConfig(res.config.config_id); }, function (err) { (err.detail && err.detail.field === "code" ? F.code : F.name).setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function createReleaseDialog() {
      var version = h("input.input.input-mono", { type: "text", placeholder: "例如 690" });
      var predecessor = h("select.select", h("option", { value: "" }, "无前驱版本"), (S.detail.releases || []).map(function (r) { return h("option", { value: r.release_id, selected: r.release_id === S.releaseId }, r.version); }));
      var F = { version: MC.field({ label: "软件版本号", required: true, control: version }) };
      configFormModal({ title: "新建软件版本", body: h("div.col", { style: "gap:12px" }, F.version, MC.field({ label: "前驱软件版本", control: predecessor, help: "只记录版本关系；新版本默认值从空草稿开始。" })), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            return api.requestV3("POST", cfgPath("/releases"), { body: { version: version.value.trim(), predecessor_id: predecessor.value || null } }).then(function (res) { if (close(true) === false) return; MC.toast.success("已创建版本 " + res.release.version); S.releaseId = res.release.release_id; S.selected = "d0-" + S.releaseId; reloadDetail(); }, function (err) { F.version.setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function freezeD0() {
      var context = operationContext(), did = draftId(), expected = S.draft && S.draft.revision;
      viewConfirm({ title: "确认版本默认", message: "确认后该版本的 D0 默认值将固化，不可再修改；此操作不下发真实机台。", confirmText: "确认并固化", danger: true }).then(function (ok) {
        if (!ok || !context.current() || S.dirty || S.savingValues) return;
        api.requestV3("POST", context.path + "/drafts/" + enc(did) + "/freeze", { body: { request_id: util.uid("freeze"), expected_revision: expected } }).then(function () { if (!context.current()) return; S.historyEvents = null; MC.toast.success("版本默认已固化"); reloadDetail(); }, function (err) { MC.toastError("冻结失败", err); });
      });
    }

    function publishNextBaseline() {
      if (!canWrite()) return;
      var context = operationContext(), rid = S.releaseId;
      var existing = release() && release().baseline_draft;
      if (existing && existing.status === "draft") { S.selected = existing.draft_id; S.tab = "workbench"; reloadDetail(); return; }
      api.requestV3("POST", context.path + "/releases/" + enc(rid) + "/baseline-drafts", { body: {} }).then(function (res) {
        if (!context.current()) return;
        S.selected = res.draft.draft_id; S.tab = "workbench";
        MC.toast.success("已创建基线草稿"); reloadDetail(); }, function (err) { MC.toastError("创建基线草稿失败", err); });
    }

    function machineDialog() {
      var no = h("input.input", { type: "text", placeholder: "例如 MC-001" });
      var bases = S.revisions.filter(function (r) { return r.status === "published" && r.kind !== "machine"; });
      var basis = h("select.select", { "aria-label": "机台采用基准" }, bases.map(function (r) { return h("option", { value: r.id, selected: r.id === S.selected }, labelOf(r.release_id) + " · " + r.label); }));
      var F = { no: MC.field({ label: "机台编号", required: true, control: no }), basis: MC.field({ label: "采用基准", required: true, control: basis }) };
      configFormModal({ title: "新建机台分支", body: h("div.col", { style: "gap:12px" }, F.no, F.basis), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            var source = bases.filter(function (r) { return r.id === basis.value; })[0];
            if (!source) { F.basis.setError("请先确认一个版本默认或基线"); return; }
            return api.requestV3("POST", cfgPath("/machine-branches"), { body: { machine_no: no.value.trim(), release_id: source.release_id, base_kind: source.kind === "default" ? "default" : "baseline", base_revision_id: source.id } }).then(function (res) { if (close(true) === false) return; S.selected = res.machine.draft.draft_id; S.releaseId = source.release_id; S.tab = "workbench"; MC.toast.success("机台草稿已创建，发布后才会得到 r1"); reloadDetail(); }, function (err) { (err.detail && err.detail.field === "base_revision_id" ? F.basis : F.no).setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function publishMachine(m) {
      api.requestV3("POST", cfgPath("/machines/" + enc(m.machine_id) + "/publish"), { body: { expected_revision: 0 } }).then(function () { MC.toast.success("机台已发布"); reloadDetail(); }, function (err) { MC.toastError("机台发布失败", err); });
    }

    var unsub = [MC.bus.on("changed:settings", function () { load(S.cid); })];
    load(S.cid || undefined);
    return {
      el: el, title: "配置管理", icon: "list",
      update: function (next) {
        var nextSurface = next && (next.arg || (next.query || {}).surface) || "configs";
        if (["configs", "files", "records"].indexOf(nextSurface) < 0) nextSurface = "configs";
        confirmDiscard(function () {
          if (nextSurface !== surface) { surface = nextSurface; catalogPage.seq++; catalogPage.loaded = ""; catalogPage.items = []; catalogPage.total = 0; catalogPage.query = ""; catalogPage.offset = 0; }
          var query = next && next.query || {}, changed = query.config && query.config !== S.cid;
          S.opened = !!query.config;
          if (changed) { S.contextEpoch++; clearConfigData(); S.cid = query.config; S.selected = query.revision || query.draft || null; S.releaseId = query.release || null; S.tab = query.tab || "versions"; load(S.cid); }
          else if (query.revision || query.draft || query.release) {
            var id = query.revision || query.draft || ("d0-" + query.release), target = S.revisions.filter(function (r) { return r.id === id; })[0];
            if (target && target.id !== S.selected) chooseRevision(target, query.tab); else render();
          } else render();
        });
      },
      isDirty: function () { return S.dirty || S.exportDirty || formDrafts.some(function (form) { return form.dirty; }); },
      destroy: function () { disposed = true; dialogs.slice().forEach(function (modal) { modal.close(); }); formDrafts = []; catalogPage.seq++; if (S.paramTree) S.paramTree.destroy(); unsub.forEach(function (f) { f(); }); },
    };
  };
})(window);
