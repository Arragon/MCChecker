/*
 * V3 配置管理工作台（M12-07 / INH-2142）。
 * 唯一配置域入口：固定配置 → 软件版本 → canonical D0 → Bn / 机台 rn。
 * 页面逐步对齐用户指定的 V3.2 HTML；数据来自配置域 API。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  function enc(v) { return encodeURIComponent(String(v)); }
  var eventLabels = { restore: "从历史记录继续编辑", export: "导出配置文件", extract: "提取参数", extract_commit: "导入参数名称", merge: "合并配置", merge_commit: "更新编辑草稿", import: "导入参数值", value_import: "导入参数值", freeze: "首次保存版本标准", baseline: "保存版本标准", machine: "保存机台配置", create_config: "创建配置分组", create_release: "创建软件版本", associate_file: "关联配置文件", discard_draft: "放弃编辑草稿", directory: "管理参数目录" };

  MC.views.configv3 = function (app, route) {
    var el = h("div.view.config-root.v32");
    // M16-36：支持从更新候选"写入草稿"后深链直达指定配置/版本（不自动发布）。
    var q = (route && route.query) || {};
    var surface = route && route.arg || q.surface || (q.config ? "configs" : "files");
    if (["configs", "files", "records"].indexOf(surface) < 0) surface = "configs";
    var catalogPage = { loaded: "", loading: false, items: [], total: 0, offset: 0, query: "", error: null, seq: 0 };
    var disposed = false, dialogs = [], formDrafts = [], fileOpenSeq = 0;
    function focusParameterSearch(event) {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || disposed || !el.isConnected || el.classList.contains("hidden") || document.querySelector(".modal-backdrop")) return;
      var target = event.target;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      var search = el.querySelector('[aria-label="搜索参数"]');
      if (search) { event.preventDefault(); search.focus(); search.select(); }
    }
    document.addEventListener("keydown", focusParameterSearch);
    var activeMenu = null;
    function viewMenu(anchor, items) {
      activeMenu = MC.menu(anchor, items); activeMenu.el.classList.add("cv-menu");
      var box = activeMenu.el.getBoundingClientRect();
      if (box.bottom > global.innerHeight - 8) activeMenu.el.style.top = Math.max(8, global.innerHeight - box.height - 8) + "px";
      return activeMenu;
    }
    function viewModal(options) {
      var onClose = options.onClose, modal;
      options.onClose = function (result) { dialogs = dialogs.filter(function (item) { return item !== modal; }); if (onClose) onClose(result); };
      modal = MC.modal(options); modal.el.classList.add("cv-dialog"); modal.el.parentNode.classList.add("cv-dialog-backdrop");
      if (options.focusTitle) { var heading = modal.el.querySelector("h2"); heading.tabIndex = 0; heading.setAttribute("autofocus", ""); }
      dialogs.push(modal); return modal;
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
      objectRole: q.role || null,
      expectedMachine: q.machine || null, expectedStandard: q.standard || null, missingRevision: null,
      missingConfig: null, detail: null, graph: null, params: null, branch: null,
      machines: [], releaseId: q.release || q.standard || null, tab: q.tab || (q.machine || q.standard ? "workbench" : "versions"), draft: null, loading: true, error: null,
      evoView: q.view === "list" ? "list" : "graph", evoQuery: "", evoFilter: "all", evoZoom: 1,
      evoCollapsed: new Set(), evoSearchCollapsed: new Set(), evoPositions: { graph: { left: 0, top: 0 }, list: { left: 0, top: 0 } },
      filter: "", overview: null, overviewMachine: "", overviewCompare: "",
      parameterFilter: "all", showFileField: true, parameterDensity: "comfortable",
      overviewOffset: 0, overviewLimit: 100, overviewTotal: 0,
      paramMode: "list", selectedParam: null, paramTree: null,
      paramPage: null, paramTotal: 0, paramOffset: 0, paramLimit: 30, paramLoading: false, allParams: null, workbenchQuery: null, definitionTotal: 0,
      dirParent: "", dirData: null, dirLoading: false, dirError: null, dirOffset: 0,
      allLoading: false,
      exportFiles: null, exportFileId: "", exportRevisionId: "", exportPlan: null,
      exportArtifact: null, exportBusy: false, exportError: null,
      historyEvents: null, historyLoading: false, historyError: null, historyQ: "", dirty: false, onlyDiff: false, editErrors: {}, explicitModes: new Set(),
    };
    var canWrite = function () { return app.can("config_registry_write"); };
    function root(rest) {
      return "/api/v3/workspaces/" + enc(MC.workspaces.scopeId()) + "/profiles/" + enc(app.pid) + rest;
    }
    function cfgPath(rest) { return root("/configs/" + enc(S.cid) + (rest || "")); }
    function parameterRecordQuery(releaseId) { return releaseId && S.selected ? "&revision_id=" + enc(S.selected) : ""; }
    function release() {
      return (S.detail && S.detail.releases || []).filter(function (r) { return r.release_id === S.releaseId; })[0] || null;
    }
    function draftId() { return S.releaseId ? "d0-" + S.releaseId : ""; }
    function revision() { return S.revisions.filter(function (item) { return item.id === S.selected; })[0] || null; }
    function editable() { var item = revision(); return canWrite() && !S.basisMissing && !S.discarding && S.tab !== "initial" && S.objectRole !== "historical" && (!item || item.status === "draft"); }
    function revisionLabel(item) { return item ? (item.machine_no || "版本标准") + " / 软件 " + labelOf(item.release_id) + " / " + (item.status === "draft" ? "编辑草稿" : item.at ? util.formatTime(item.at) : "已保存配置") : "没有配置记录"; }
    function latestStandard(releaseId) {
      var candidates = S.revisions.filter(function (r) { return r.release_id === releaseId && r.kind === "baseline" && r.status === "published"; });
      candidates.sort(function (a, b) { return Number(b.label.slice(1)) - Number(a.label.slice(1)); });
      return candidates[0] || S.revisions.filter(function (r) { return r.release_id === releaseId && r.kind === "default" && r.status === "published"; })[0];
    }
    function parameterReference(item) {
      if (!item) return null;
      if (item.kind !== "machine") return release() && release().initial_revision_id || null;
      var machine = S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0];
      // Old records compare with their actual historical source; the current object
      // compares against the latest saved standard without changing inheritance.
      if (item.status === "published" && machine && machine.head_id !== item.id) return item.source || null;
      var standard = latestStandard(item.release_id); return standard && standard.id || null;
    }
    function historicalRecord(item) {
      if (!item || item.status !== "published") return false;
      if (S.objectRole === "historical") return true;
      if (S.tab === "initial" || S.objectRole === "initial") return false;
      if (item.kind === "machine") {
        var machine = S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0];
        return !!machine && machine.head_id !== item.id;
      }
      var latest = latestStandard(item.release_id); return !!latest && latest.id !== item.id;
    }
    function buildRevisions() {
      var result = [];
      (S.detail.releases || []).forEach(function (r) {
        result.push({ id: "d0-" + r.release_id, release_id: r.release_id, kind: "default", label: "D0", note: (r.d0 || {}).note || "", status: r.default_revision_id ? "published" : "draft", at: r.default_frozen_at || r.created_at });
        var draft = r.baseline_draft;
        if (draft && draft.status === "draft") result.push({ id: draft.draft_id, release_id: r.release_id, kind: "baseline", label: "基线草稿", status: "draft", source: draft.source_revision_id, at: draft.created_at });
      });
      ((S.branch && S.branch.baselines) || []).forEach(function (b) { result.push({ id: b.baseline_id, release_id: b.release_id, kind: "baseline", label: b.label, note: b.note || "", status: "published", source: b.source_revision_id, parent: b.parent_id, at: b.published_at }); });
      (S.machines || []).forEach(function (m) {
        (m.revisions || []).forEach(function (r) { result.push({ id: r.revision_id, release_id: r.release_id || m.release_id, kind: "machine", machine_id: m.machine_id, machine_no: m.machine_no, label: r.label, note: r.note || "", status: "published", source: r.base_revision_id, parent: r.parent_id, at: r.published_at }); });
        if (m.draft && m.draft.status === "draft") result.push({ id: m.draft.draft_id, release_id: m.release_id, kind: "machine", machine_id: m.machine_id, machine_no: m.machine_no, label: "机台草稿", status: "draft", source: m.draft.base_revision_id, parent: m.head_id, at: m.draft.created_at });
      });
      S.revisions = result;
      if (!S.selected && S.expectedMachine) {
        var owner = S.machines.filter(function (m) { return m.machine_id === S.expectedMachine; })[0];
        S.selected = owner && (owner.head_id || owner.draft && owner.draft.draft_id);
        if (!S.selected) { S.missingRevision = "机台"; return; }
      }
      if (!S.selected && S.expectedStandard) {
        var standard = latestStandard(S.expectedStandard);
        S.selected = standard ? standard.id : "d0-" + S.expectedStandard;
      }
      if (S.selected && !validRecord(revision())) { S.missingRevision = S.selected; return; }
      if (!revision()) S.selected = draftId();
      if (revision()) S.releaseId = revision().release_id;
    }
    function validRecord(item) {
      return !!item && (!S.expectedMachine || item.machine_id === S.expectedMachine) &&
        (!S.expectedStandard || item.kind !== "machine" && item.release_id === S.expectedStandard);
    }
    function loadSelected() {
      var item = revision();
      if (!item) { S.draft = null; return Promise.resolve(); }
      var selectedId = item.id, scope = cfgPath("");
      var request = ++selectedSeq;
      function current() { return !disposed && request === selectedSeq && S.selected === selectedId && cfgPath("") === scope; }
      S.basisMissing = item.kind === "machine" && !item.source;
      var basis = item.source ? api.getV3(scope + "/revisions/" + enc(item.source)).then(function (res) { if (current()) S.basis = (res.snapshot || {}).values || {}; }, function (err) {
        if (item.kind !== "machine" || err.status !== 404) throw err;
        if (current()) { S.basis = null; S.basisMissing = true; }
      }) : Promise.resolve().then(function () { if (current()) S.basis = null; });
      var bindings = api.getV3(scope + "/releases/" + enc(item.release_id) + "/bindings").then(function (res) { if (current()) S.bindingRevision = (res.binding_set || {}).revision; S.bindings = Object.keys((res.binding_set || {}).bindings || {}).map(function (id) { return res.binding_set.bindings[id]; }); });
      var savedId = item.status === "published" ? item.id : item.kind === "machine" ? item.parent : (latestStandard(item.release_id) || {}).id;
      var saved = savedId ? api.getV3(scope + "/revisions/" + enc(savedId)).then(function (res) { if (current()) { S.lastSavedId = savedId; S.lastSavedValues = (res.snapshot || {}).values || {}; } })
        : Promise.resolve().then(function () { if (current()) { S.lastSavedId = null; S.lastSavedValues = {}; } });
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
      return Promise.all([snapshot, basis, bindings, saved]);
    }
    function chooseRevision(item, tab) {
      return new Promise(function (resolve) { confirmDiscard(function () {
        if (S.releaseId !== item.release_id) { S.paramOffset = 0; S.overviewOffset = 0; S.overviewDirectory = ""; S.overviewQ = ""; S.overviewCompare = ""; S.overviewOnly = false; S.overviewColumns = {}; S.exportMappings = {}; }
        S.contextEpoch++; directorySeq++; parameterSeq++; S.savingValues = false; S.valueError = null;
        S.discardError = null; S.pendingDiscard = null;
        S.missingRevision = null; S.expectedMachine = item.machine_id || null; S.expectedStandard = item.kind === "machine" ? null : item.release_id;
        S.pendingSave = null; S.parameterFilter = "all"; S.selected = item.id; S.releaseId = item.release_id; S.edits = {}; S.editErrors = {}; S.explicitModes.clear(); S.workbenchQuery = null; S.exportPlan = null; S.exportArtifact = null;
        if (tab) S.tab = tab;
        S.loading = true; render();
        reloadDetail().then(function () { if (disposed) { resolve(); return; } S.loading = false; S.focusRevision = S.tab === "versions"; render(); routeSelection(false); resolve(); });
      }); });
    }
    function openConfig(id) {
      confirmDiscard(function () {
        rememberEvolution();
        if (S.cid !== id) {
          S.evoQuery = ""; S.evoFilter = "all"; S.evoCollapsed.clear(); S.evoSearchCollapsed.clear();
          S.evoPositions = { graph: { left: 0, top: 0 }, list: { left: 0, top: 0 } };
        }
        S.contextEpoch++; clearConfigData(); S.opened = true; S.cid = id; S.tab = "versions";
        app.go({ kind: "configv3", arg: "configs", query: { config: id } }); load(id); });
    }
    function clearConfigData() {
      S.basisMissing = false;
      S.recordHistoryObject = null; S.recordHistoryOffset = 0;
      S.discarding = false; S.discardError = null; S.pendingDiscard = null;
      S.pendingSave = null; S.afterSave = null; S.exportFromEdit = false; S.internalRoute = null; S.lastSavedId = null; S.lastSavedValues = {}; S.missingRevision = null; S.expectedMachine = null; S.expectedStandard = null; S.detail = null; S.selected = null; S.releaseId = null; S.revisions = []; S.draft = null; S.edits = {}; S.editErrors = {}; S.explicitModes.clear(); S.dirty = false; S.savingValues = false; S.valueError = null;
      S.paramOffset = 0; S.paramTotal = 0; S.paramPage = null; S.allParams = null; S.workbenchQuery = null; S.selectedParam = null; S.folderActive = false; S.dirData = null; S.dirParent = ""; S.dirOffset = 0; S.dirError = null;
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
          if (S.missingRevision) return;
          var parameterContext = scope + "|" + S.releaseId + "|" + S.selected;
          if (S.parameterContext !== parameterContext) { S.paramOffset = 0; S.dirParent = ""; S.dirOffset = 0; S.folderActive = false; S.overviewColumns = {}; }
          S.parameterContext = parameterContext;
          return loadSelected().then(function () {
          if (!current()) return;
          var parameters = S.workbenchQuery ? api.requestV3("POST", scope + "/parameter-query", { body: Object.assign({}, S.workbenchQuery, { revision_id: S.selected,
            directory: S.folderActive ? S.dirParent : "", changed_ids: editedParameterIds(), edits: S.edits, offset: S.paramOffset, limit: S.paramLimit }) })
            : api.getV3(cfgPath("/parameters") + "?offset=" + S.paramOffset + "&limit=" + S.paramLimit + "&release_id=" + enc(S.releaseId || "") + parameterRecordQuery(S.releaseId));
          return Promise.all([parameters.then(function (page) {
            if (!current()) return;
            S.paramPage = page; S.params = page; S.paramTotal = page.total == null ? (page.items || []).length : page.total; S.definitionTotal = S.workbenchQuery ? page.parameter_total : S.paramTotal; S.paramOffset = page.offset || 0;
          }), loadOverview(), S.folderActive ? loadDir(S.dirParent, S.dirOffset) : Promise.resolve()]);
          });
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
      var activeFile = S.detail && S.detail.file && S.detail.file.name || "";
      if (el.dataset.managedFileName !== activeFile) { el.dataset.managedFileName = activeFile; if (app.renderFileList) app.renderFileList(); }
      if (surface === "records") el.appendChild(MC.configWorkspaceNav(app, surface));
      if (surface !== "configs") { renderCatalogPage(); return; }
      if (S.opened && S.detail && S.detail.file_id) {
        var file = S.detail.file || {};
        el.appendChild(h("nav.cv-breadcrumb", { "aria-label": "面包屑" },
          h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "files" }); } }, "配置管理"),
          icon("chevron-right"), objectPage() ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { changeTab("versions"); } }, file.name || "文件已移除") : h("span", file.name || "文件已移除"),
          objectPage() ? h("span.small.muted", "/ " + (revision() && revision().machine_no || "软件 " + labelOf(S.releaseId))) : null));
        if (!objectPage()) el.appendChild(MC.configFileHeading(Object.assign({}, S.detail, { savedMachineCount: S.machines.filter(function (m) { return !!m.head_id; }).length }), S.tab === "versions" ? versionActions() : null));
      } else if (S.opened && S.detail) {
        el.appendChild(h("div.cv-breadcrumb", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () {
          confirmDiscard(function () { S.opened = false; S.cid = null; S.detail = null; S.edits = {}; app.go({ kind: "configv3", arg: "configs", query: {} }); render(); });
        } }, "其他配置"), h("span.muted", "/"), objectPage() ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { changeTab("versions"); } }, S.detail.name) : h("strong", S.detail.name),
          h("details.cv-help", h("summary", "技术详情"), h("span.mono.small", S.detail.code))));
      } else el.appendChild(h("div.cv-page-heading", h("h1", "其他配置")));
      if (S.loading) { el.appendChild(MC.stateBlock({ kind: "loading", title: "加载配置…" })); return; }
      if (S.error) { el.appendChild(MC.errorBlock(S.error, load)); return; }
      if (S.missingRevision || S.tab === "initial" && release() && release().initial_revision_id && S.selected !== release().initial_revision_id || S.objectRole === "historical" && revision() && revision().status !== "published") {
        el.appendChild(MC.stateBlock({ kind: "empty", title: "此配置记录不可用", desc: "记录不存在，或不属于当前配置与对象。", actions: [h("button.btn", { type: "button", onclick: function () { S.missingRevision = null; S.expectedMachine = null; S.expectedStandard = null; S.selected = null; S.objectRole = null; S.tab = "versions"; load(S.cid); routeSelection(true); } }, "返回版本演进")] })); return;
      }
      if (S.missingConfig) {
        el.appendChild(MC.stateBlock({ kind: "empty", title: "该配置已不存在", desc: "可能已被删除，或不属于当前工作区与机型。", actions: [h("button.btn", { type: "button", onclick: function () { S.cid = null; S.opened = false; S.missingConfig = null; app.go({ kind: "configv3", arg: "configs", query: {} }); load(); } }, "查看配置列表")] }));
        return;
      }
      if (!canWrite()) {
        el.appendChild(h("div.banner.banner-warning", icon("info"), h("div.banner-body", "只读模式，编辑需要管理员权限。")));
      }
      if (S.basisMissing && objectPage()) el.appendChild(h("div.banner.banner-warning", { role: "alert" }, h("div.banner-body", "原来采用的标准不可用。参数仍可查看，继续编辑前请明确选择原来的标准和目标标准。"),
        canWrite() && !historicalRecord(revision()) ? h("button.btn.btn-sm", { type: "button", onclick: upgradeDialog }, "核对参照标准") : null));
      if (S.discardError) el.appendChild(h("div.banner.banner-warning", { role: "alert" }, h("div.banner-body", "放弃草稿尚未确认，输入已保留。", MC.errorText(S.discardError)),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite() || S.discarding, onclick: function () {
          if (S.discardError.status === 409) recheckDiscard(); else if (S.pendingDiscard) performDiscard(S.pendingDiscard, true);
        } }, S.discardError.status === 409 ? "重新核对草稿" : "重试确认")));
      if (!S.opened) { el.appendChild(configsPanel()); return; }
      el.appendChild(mainPanel());
      if (S.tab === "versions") {
        var evoScroll = el.querySelector(".cv32-evo-scroll"), evoPosition = S.evoPositions[S.evoView];
        if (evoScroll) { evoScroll.scrollTo(evoPosition.left, evoPosition.top); MC.configEvolution.configureAxes(evoScroll, S.evoZoom); }
      }
      if (S.focusRevision) { S.focusRevision = false; var selectedNode = el.querySelector('.cv-graph-node[aria-pressed="true"]'); if (selectedNode) selectedNode.focus({ preventScroll: true }); }
      if (S.focusTab) { S.focusTab = false; var activeTab = el.querySelector('.cv-tabs [aria-selected="true"]'); if (activeTab) activeTab.focus(); }
    }

    function loadCatalogPage(reset) {
      var target = surface, wid = MC.workspaces.scopeId();
      if (reset) catalogPage.offset = 0;
      var seq = ++catalogPage.seq;
      catalogPage.loading = true; catalogPage.error = null; catalogPage.loaded = target; render();
      api.getV3(root(target === "files" ? "/config-files" : "/events"), {
        q: catalogPage.query, offset: catalogPage.offset, limit: 50,
      }).then(function (res) {
        if (disposed || seq !== catalogPage.seq || target !== surface || wid !== MC.workspaces.scopeId()) return;
        catalogPage.items = res.items || []; catalogPage.total = res.total == null ? catalogPage.items.length : res.total;
        catalogPage.loading = false; render();
      }, function (err) { if (disposed || seq !== catalogPage.seq || target !== surface) return; catalogPage.loading = false; catalogPage.error = err; render(); });
    }

    function renderCatalogPage() {
      var files = surface === "files", P = catalogPage;
      var query = h("input.input", { type: "search", "aria-label": files ? "搜索配置文件" : "搜索配置记录", placeholder: files ? "搜索文件名或配置名称" : "搜索事件、参数或操作人", value: P.query });
      function search() { P.query = query.value.trim(); loadCatalogPage(true); }
      query.addEventListener("keydown", function (event) { if (event.key === "Enter") search(); });
      if (files) el.appendChild(h("nav.cv-breadcrumb", { "aria-label": "面包屑" }, h("span", "配置管理"), icon("chevron-right"), h("span", "配置文件")));
      el.appendChild(h("div.view-header", h("div.col", h("h1", files ? "配置文件" : "操作记录"), files ? h("span.small.muted", "当前工作区 · " + (app.session && app.session.profile && app.session.profile.name || app.pid)) : null),
        files && app.can("tasks_run") ? h("button.btn", { type: "button", title: "预览整个工作区的更新", onclick: function () { app.go({ kind: "updates", arg: "sources", query: { action: "preview_all", request: util.uid("preview") } }); } }, icon("refresh"), "全部更新") : null));
      var card = h("div.config-resource-surface", h("div.row.config-resource-toolbar", h("label.cv32-searchbox", icon("search"), query),
        h("span.spacer"), h("span.small.muted", P.total + (files ? " 个文件" : " 条记录"))));
      var searchTimer;
      query.addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(function () { if (query.isConnected) search(); }, 200); });
      el.appendChild(card);
      if (files) el.appendChild(h("div.cv32-library-foot", h("span.small.muted", "所有版本、机台和记录均限定于当前工作区"), h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "configs" }); } }, "其他配置")));
      if (P.loaded !== surface) { loadCatalogPage(true); return; }
      if (P.loading) { card.appendChild(MC.stateBlock({ kind: "loading", title: "正在读取…" })); return; }
      if (P.error) { card.appendChild(MC.errorBlock(P.error, function () { loadCatalogPage(false); })); return; }
      if (!P.items.length) { card.appendChild(MC.stateBlock({ kind: "empty", title: P.query ? "没有匹配结果" : (files ? "还没有配置文件" : "还没有配置记录"), desc: files ? "从左侧配置文件列表导入文件后，即可在这里查看。" : "配置的修改、导入、导出与恢复会记录在这里。" })); return; }
      var visible = P.items;
      var rows = visible.map(function (item) {
        if (files) {
          var config = item.configuration;
          return h("tr", h("td", h("div.cv-file-label", h("span.cv-file-type", icon("file"), (item.format || "").toUpperCase()),
            h("div.col", h("button.btn.btn-ghost.cv-object-link", { type: "button", onclick: function () { openManagedFile(item); } }, item.name),
              config ? h("span.small.muted", config.name) : null))),
            h("td", config && config.latest_release ? h("div.row", { style: "gap:4px;flex-wrap:wrap" }, (config.recent_versions || [config.latest_release.version]).map(function (v) { return h("span.badge", v); }), config.release_count > 3 ? h("span.small.muted", "+" + (config.release_count - 3)) : null) : h("span.muted", "—")),
            h("td", config ? config.machine_count + " 台" : "—"),
            h("td", config && (config.draft_count || config.pending_updates) ? h("div.col", config.draft_count ? h("span.badge.badge-warning", config.draft_count + " 份草稿") : null,
              config.pending_updates ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "updates", arg: "candidates", query: { config: config.config_id } }); } }, config.pending_updates + " 项更新待核对") : null) : h("span.muted", "—")),
            h("td.small", util.formatTime(config ? config.updated_at : item.updated_at)),
            h("td", h("button.btn.btn-ghost.btn-icon", { type: "button", "aria-label": "管理 " + item.name, onclick: function () { openManagedFile(item); } }, icon("chevron-right"))));
        }
        var owner = S.configs.filter(function (c) { return c.config_id === item.config_id; })[0];
        return h("tr", h("td.xsmall", item.at), h("td", eventLabels[item.type] || "配置操作"), h("td", item.principal || "—"),
          h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.go({ kind: "configv3", arg: "configs", query: { config: item.config_id } }); } }, owner ? owner.name : "查看配置")));
      });
      card.appendChild(h("div.config-resource-scroll", h("table.table", h("thead", h("tr", (files ? ["文件名", "软件版本", "机台", "待处理", "最近保存", "操作"] : ["时间", "事件", "操作人", "配置"]).map(function (label) { return h("th", label); }))), h("tbody", rows))));
      card.appendChild(h("div.row.config-resource-toolbar", h("span.small.muted", "第 " + (P.offset + 1) + "–" + Math.min(P.offset + visible.length, P.total) + " 项"), h("span.spacer"),
        h("button.btn.btn-sm", { type: "button", disabled: P.offset === 0, onclick: function () { P.offset = Math.max(0, P.offset - 50); loadCatalogPage(false); } }, "上一页"),
        h("button.btn.btn-sm", { type: "button", disabled: P.offset + 50 >= P.total, onclick: function () { P.offset += 50; loadCatalogPage(false); } }, "下一页")));
    }

    function openManagedFile(file) {
      if (file.configuration) { openConfig(file.configuration.config_id); return; }
      if (!canWrite()) {
        viewModal({ title: file.name, body: h("p", "此文件尚未设置版本配置，请联系管理员完成设置。") });
        return;
      }
      var choices = h("select.select", { "aria-label": "配置关联方式" }, h("option", { value: "" }, "为此文件建立配置"),
        S.configs.filter(function (config) { return !config.file_id; }).map(function (config) { return h("option", { value: config.config_id }, "关联已有配置 · " + config.name); }));
      var name = h("input.input", { type: "text", value: file.name.slice(0, 100), maxlength: 100 });
      var field = MC.field({ label: "配置名称", required: true, control: name });
      var error = h("div.small", { role: "alert" });
      choices.addEventListener("change", function () { field.hidden = !!choices.value; error.textContent = ""; });
      var code = util.uid("cfg");
      configFormModal({ title: "设置 " + file.name, body: h("div.col", { style: "gap:12px" },
        MC.field({ label: "配置关联方式", control: choices }), field, error), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button", onclick: function () {
          MC.withBusy(save, function () {
            var path = choices.value ? root("/configs/" + enc(choices.value) + "/file") : root("/configs");
            var payload = choices.value ? { file_id: file.file_id } : { file_id: file.file_id, code: code, name: name.value.trim() };
            return api.requestV3(choices.value ? "PUT" : "POST", path, { body: payload }).then(function (res) {
              if (close(true) === false) return;
              MC.toast.success("已设置文件配置"); openConfig(res.config.config_id);
            }, function (err) { error.textContent = MC.errorText(err); });
          });
        } }, "进入配置管理");
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function configsPanel() {
      var search = h("input.input", { type: "search", "aria-label": "搜索配置分组", placeholder: "搜索配置名称或标识", value: S.filter });
      var results = h("div.cv-catalog-results");
      function draw() {
        var items = S.configs.filter(function (c) { return !S.filter || (c.name + " " + c.code).toLowerCase().indexOf(S.filter.toLowerCase()) >= 0; });
        util.clear(results);
        if (!items.length) {
          results.appendChild(MC.stateBlock({ kind: "empty", title: S.configs.length ? "没有匹配的配置" : "创建第一组配置", desc: S.configs.length ? "调整搜索条件后重试。" : "为需要长期维护的一组参数命名，再添加软件版本。",
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
        h("button.btn.btn-primary", { type: "button", disabled: !canWrite(), onclick: createConfigDialog }, icon("plus"), "新建配置分组")), results);
    }

    function changeTab(value) {
      if (S.tab === value) return;
      if (S.tab === "export" && S.exportFromEdit && value === "workbench") { S.tab = value; S.exportFromEdit = false; render(); routeSelection(false); return; }
      confirmDiscard(function () { rememberEvolution(); S.tab = value; S.focusTab = true; render(); routeSelection(false); });
    }

    function confirmDiscard(then, cancelled) {
      cancelled = cancelled || function () {};
      if (S.discarding) { MC.toast.info("正在放弃草稿，请等待结果"); cancelled(); return; }
      if (S.savingValues) { MC.toast.info("正在保存，请等待结果"); cancelled(); return; }
      var formDirty = formDrafts.some(function (form) { return form.dirty; });
      if (!S.dirty && !S.exportDirty && !formDirty) { then(); return; }
      if (S.dirty && !S.exportDirty && !formDirty && editable()) {
        var context = operationContext();
        viewModal({ title: "有未暂存的修改", body: h("p", "暂存后可以稍后继续编辑；放弃本次修改不会删除已暂存草稿。"), onClose: function (result) { if (result !== "proceed" && result !== "saving") cancelled(); }, footer: function (close) {
          return [h("button.btn", { type: "button", onclick: close }, "继续编辑"),
            h("button.btn", { type: "button", onclick: function () { close("proceed"); S.dirty = false; S.edits = {}; S.editErrors = {}; S.explicitModes.clear(); then(); } }, "放弃修改"),
            h("button.btn.btn-primary", { type: "button", onclick: function () { if (!context.current()) return; close("saving"); stashValues().then(function (ok) { if (ok && context.current()) then(); else cancelled(); }); } }, "暂存并离开")];
        } }); return;
      }
      viewConfirm({ title: formDirty ? "有未完成的表单" : S.exportDirty ? "有未确认的导出字段" : "有未保存的值", message: "离开会丢失当前尚未保存或确认的修改。", confirmText: "放弃修改", cancelText: "继续编辑", danger: true }).then(function (ok) {
        if (!ok) { cancelled(); return; }
        S.dirty = false; S.edits = {}; S.editErrors = {}; S.explicitModes.clear(); S.exportDirty = false; then();
      });
    }

    function objectPage() { return ["versions", "history"].indexOf(S.tab) < 0; }
    function routeSelection(replace) {
      if (!S.cid) return;
      var query = { config: S.cid };
      if (S.tab !== "versions") query.tab = S.tab;
      if (objectPage() && S.selected) { query.revision = S.selected; query.release = S.releaseId; if (S.objectRole) query.role = S.objectRole; }
      if (objectPage() && revision()) { if (revision().kind === "machine") query.machine = revision().machine_id; else query.standard = revision().release_id; }
      S.internalRoute = { tab: S.tab, selected: objectPage() ? S.selected : null, scope: cfgPath("") };
      app.go({ kind: "configv3", arg: "configs", query: query }, !!replace);
    }
    function objectHeader() {
      var item = revision(), rel = release(); if (!item || !rel) return null;
      var machine = item.kind === "machine" && S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0];
      var history = historicalRecord(item);
      var status = S.tab === "initial" ? "初始参数 · 只读" : history ? "历史快照 · 只读" : item.status === "draft" ? "编辑中" : "已保存";
      var draft = S.revisions.filter(function (r) { return r.status === "draft" && (machine ? r.machine_id === machine.machine_id : r.release_id === rel.release_id && r.kind !== "machine"); })[0];
      return h("div.cv32-object-heading", h("div.col", h("div.row", icon(machine ? "machine" : "compare"), h("h1", { tabindex: -1, "data-object-title": "1" }, machine ? machine.machine_no : "软件 " + rel.version),
        h("span.badge" + (item.status === "draft" ? ".badge-warning" : ""), status)),
        h("div.small.muted", machine ? "软件 " + rel.version + " · " : "标准参数 · ", item.at ? util.formatTime(item.at) : "尚未保存")),
        h("div.row.cv32-object-actions", h("button.btn.btn-sm", { type: "button", disabled: !!S.savingValues, onclick: startExport }, icon("download"), "导出文件"),
          item.status !== "draft" && canWrite() && S.tab !== "initial" && !history ? h("button.btn.btn-sm.btn-primary", { type: "button", onclick: createEditingDraft }, icon("edit"), draft ? "继续编辑草稿" : "编辑参数") : null,
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "当前配置的更多操作", disabled: !!S.savingValues, onclick: function (event) { viewMenu(event.currentTarget, [
            { label: "对比参数", disabled: S.tab === "initial" && !rel.initial_revision_id, onClick: compareParameters },
            { label: "管理参数", disabled: history || S.tab === "initial", onClick: function () { changeTab("overview"); } },
            { label: "从文件提取参数", disabled: !canWrite() || history || S.tab === "initial", onClick: extractDialog },
            { label: machine ? "更新到其他标准" : "合并配置", disabled: !canWrite() || history || S.tab === "initial", onClick: machine ? upgradeDialog : mergeDialog },
            draft ? { label: "放弃已暂存草稿", danger: true, disabled: !canWrite() || history || S.tab === "initial" || !!(S.pendingSave && S.pendingSave.attempted), onClick: function () { discardStoredDraft(draft); } } : null,
            { label: "版本关系", onClick: function () { changeTab("versions"); } },
            { label: "技术详情", onClick: function () { viewModal({ title: "配置技术详情", body: h("pre.small.mono", JSON.stringify({ revision_id: item.id, release_id: item.release_id, machine_id: item.machine_id, source_revision_id: item.source, previous_id: item.parent }, null, 2)) }); } }
          ].filter(Boolean)); } }, icon("more"))));
    }
    function discardStoredDraft(item, latest) {
      if (!item || !canWrite() || S.savingValues || S.discarding) return;
      var rel = (S.detail.releases || []).filter(function (r) { return r.release_id === item.release_id; })[0];
      var machine = item.machine_id && S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0];
      var stored = latest || (item.kind === "machine" ? machine && machine.draft : item.kind === "baseline" ? rel && rel.baseline_draft : rel && rel.d0);
      if (!stored) return;
      var pending = { context: operationContext(), body: { draft_id: item.id, expected_revision: stored.revision, request_id: util.uid("discard") } };
      viewConfirm({ title: "放弃已暂存草稿？", message: "已暂存内容和本次未暂存的修改都会清除。已保存配置及其历史保持不变。尚未保存过的新机台或软件版本会一并移除。", confirmText: "放弃草稿", cancelText: "保留草稿", danger: true }).then(function (ok) {
        if (ok && pending.context.current()) performDiscard(pending, false);
      });
    }
    function recheckDiscard() {
      var pending = S.pendingDiscard, item = pending && S.revisions.filter(function (r) { return r.id === pending.body.draft_id; })[0];
      if (!item || !pending.context.current()) return;
      readEditingDraft(pending.context, item).then(function (latest) {
        if (!pending.context.current()) return;
        if (!latest || latest.frozen || latest.status === "published" || latest.draft_id && latest.draft_id !== item.id) { MC.toast.warning("该草稿已保存或被替换，请返回版本演进核对。"); return; }
        discardStoredDraft(item, latest);
      }, function (err) { MC.toastError("读取最新草稿失败", err); });
    }
    function performDiscard(pending, retry) {
      var context = pending.context;
      if (!context.current() || S.discarding) return;
      S.discarding = true; S.pendingDiscard = pending; S.discardError = null; render();
      var read = retry ? api.getV3(context.path) : Promise.resolve();
      read.then(function () {
        if (!context.current()) throw { code: "CONTEXT_CHANGED" };
        return api.requestV3("POST", context.path + "/discard-draft", { body: pending.body });
      }).then(function (res) {
        if (!context.readCurrent()) return;
        S.discarding = false; S.pendingDiscard = null; S.pendingSave = null; S.afterSave = null; S.edits = {}; S.editErrors = {}; S.explicitModes.clear(); S.dirty = false;
        S.selected = res.return_revision_id || null; S.expectedMachine = null; S.expectedStandard = null; S.objectRole = null; S.historyEvents = null; S.releaseId = null;
        S.tab = res.removed_object ? "versions" : "workbench";
        MC.toast.success("草稿已放弃"); reloadDetail().then(function () { if (!disposed && context.path === cfgPath("")) routeSelection(true); });
      }, function (err) { if (context.readCurrent()) { S.discarding = false; S.discardError = err; render(); } });
    }
    function objectHistoryView() {
      var current = revision();
      if (!current) return MC.stateBlock({ kind: "empty", title: "没有保存记录" });
      var records = S.revisions.filter(function (r) { return r.status === "published" && (current.kind === "machine" ? r.machine_id === current.machine_id : r.kind !== "machine" && r.release_id === current.release_id); });
      var machine = current.kind === "machine" && S.machines.filter(function (m) { return m.machine_id === current.machine_id; })[0];
      var latest = machine ? machine.head_id : (latestStandard(current.release_id) || {}).id;
      records.sort(function (a, b) { return a.id === latest ? -1 : b.id === latest ? 1 : String(b.at || "").localeCompare(String(a.at || "")) || (parseInt(b.label.slice(1), 10) || 0) - (parseInt(a.label.slice(1), 10) || 0); });
      if (!records.length) return MC.stateBlock({ kind: "empty", title: "还没有保存记录" });
      var key = current.machine_id || current.release_id;
      if (S.recordHistoryObject !== key) { S.recordHistoryObject = key; S.recordHistoryOffset = 0; }
      var offset = S.recordHistoryOffset || 0;
      return h("section.cv32-object-records", h("div.cv-toolbar", h("span.small.muted", records.length + " 条保存记录"), h("span.spacer"), h("span.small.muted", "历史快照只读")),
        h("div.cv32-snapshot-list", records.slice(offset, offset + 30).map(function (record) {
          return h("article.cv32-history-entry", h("span.cv32-record-dot"), h("div.col.grow", h("div.row", { style: "gap:8px;flex-wrap:wrap" }, h("strong", "软件 " + labelOf(record.release_id)), record.id === latest ? h("span.badge", "当前记录") : null),
            h("p.small", record.note || "保存配置"), h("span.small.muted", util.formatTime(record.at) + " · " + ((S.detail.saved_authors || {})[record.id] || "操作人未记录")),
            h("details.cv-help", h("summary", "记录详情"), h("code.small.mono", record.id))),
            h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { S.objectRole = "historical"; chooseRevision(record, "workbench"); } }, "查看快照"));
        })), h("div.cv-toolbar", pagerRow(records.length, offset, 30, function (start) { S.recordHistoryOffset = start; render(); })));
    }
    function versionMachinesView() {
      var rows = S.machines.filter(function (machine) { var head = S.revisions.filter(function (r) { return r.id === machine.head_id; })[0]; return head && head.release_id === S.releaseId; });
      if (!rows.length) return MC.stateBlock({ kind: "empty", title: "还没有使用此版本的机台" });
      return h("div.table-wrap.cv32-object-records", h("table.table", h("thead", h("tr", ["机台编号", "保存时间", "草稿", "操作"].map(function (name) { return h("th", name); }))), h("tbody", rows.map(function (machine) {
        var head = S.revisions.filter(function (r) { return r.id === machine.head_id; })[0], draft = machine.draft && machine.draft.status === "draft";
        return h("tr", h("td", h("strong", machine.machine_no)), h("td", util.formatTime(head.at)),
          h("td", draft ? h("span.badge.badge-warning", "有草稿" + (machine.release_id !== head.release_id ? " · 目标 " + labelOf(machine.release_id) : "")) : "—"),
          h("td", h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { S.objectRole = "machine"; chooseRevision(head, "workbench"); } }, "查看参数")));
      }))));
    }
    function mainPanel() {
      if (!S.detail) return MC.stateBlock({ kind: "empty", title: "先选择配置文件" });
      var object = objectPage(), item = revision(), options;
      if (object) options = [{ value: "workbench", label: item && item.kind === "machine" ? "参数" : "标准参数" }]
        .concat(item && item.kind === "machine" ? [] : [{ value: "initial", label: "初始参数" }, { value: "machines", label: "使用此版本的机台" }])
        .concat([{ value: "object-history", label: "修改记录" }]);
      else options = [{ value: "versions", label: "版本演进" }, { value: "updates", label: "更新管理" }, { value: "history", label: "操作记录" }];
      var tabs = MC.segmented(options, S.tab, function (value) {
        if (value === "updates") { confirmDiscard(function () { app.go({ kind: "updates", arg: "sources", query: { config: S.cid } }); }); return; }
        if (value === "initial") {
          var initial = release() && release().initial_revision_id, target = S.revisions.filter(function (r) { return r.id === initial; })[0];
          if (target) { S.objectRole = "initial"; chooseRevision(target, "initial"); } else changeTab(value);
        } else if (value === "workbench" && S.tab === "initial") {
          var standard = latestStandard(S.releaseId) || S.revisions.filter(function (r) { return r.release_id === S.releaseId && r.kind === "default"; })[0];
          S.objectRole = "standard"; if (standard) chooseRevision(standard, "workbench");
        } else changeTab(value);
      }, object ? "当前配置页面" : "配置文件页面");
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
      if (!(S.detail.releases || []).length && S.tab !== "history") body = MC.stateBlock({ kind: "empty", title: "添加第一个软件版本", desc: "创建软件版本后，可填写参数或从文件导入。", actions: [h("button.btn.btn-primary", { type: "button", disabled: !canWrite(), onclick: createReleaseDialog }, icon("plus"), "新建软件版本")] });
      else if (S.tab === "versions") body = versionsView(rel);
      else if (S.tab === "workbench" || S.tab === "initial") body = S.tab === "initial" && !rel.initial_revision_id ? MC.stateBlock({ kind: "empty", title: "初始参数尚未导入", desc: "新建软件版本不会复制其他版本的初始参数。" }) : workbenchView(rel);
      else if (S.tab === "machines") body = versionMachinesView();
      else if (S.tab === "object-history") body = objectHistoryView();
      else if (S.tab === "overview") body = historicalRecord(item) || S.objectRole === "initial" ? MC.stateBlock({ kind: "empty", title: "历史参数只读", actions: [h("button.btn", { type: "button", onclick: function () { changeTab("workbench"); } }, "查看参数")] }) : overviewView(rel);
      else if (S.tab === "export") body = exportView(rel);
      else if (S.tab === "history") body = historyView();
      else body = MC.stateBlock({ kind: "empty", title: "页面不存在" });
      var active = tabs.querySelector('[aria-selected="true"]');
      return h("section.cv-workspace", object ? objectHeader() : null, tabs, h("div.cv-tab-content", { id: panelId, role: "tabpanel", "aria-labelledby": active && active.id, "aria-label": active ? null : S.tab === "export" ? "导出文件" : "管理参数" }, body));
    }

    function rememberEvolution() {
      var scroll = el.querySelector(".cv32-evo-scroll");
      if (scroll) S.evoPositions[S.evoView] = { left: scroll.scrollLeft, top: scroll.scrollTop };
    }

    function refreshEvolution(focusSelector) {
      rememberEvolution();
      render();
      var scroll = el.querySelector(".cv32-evo-scroll"), position = S.evoPositions[S.evoView];
      if (scroll) scroll.scrollTo(position.left, position.top);
      var control = focusSelector && el.querySelector(focusSelector);
      if (control) control.focus({ preventScroll: true });
    }

    function versionsView() {
      var groups = MC.configEvolution.project(S.detail.releases || [], S.revisions, S.machines, S.evoQuery, S.evoFilter);
      var collapsed = S.evoQuery ? S.evoSearchCollapsed : S.evoCollapsed;
      function open(record, role) {
        if (!record) return;
        rememberEvolution();
        S.objectRole = role;
        chooseRevision(record, role === "initial" ? "initial" : "workbench");
      }
      function edit(record) {
        if (!record || !canWrite()) return;
        rememberEvolution();
        S.objectRole = record.kind === "machine" ? "machine" : "standard";
        if (record.status === "draft") { chooseRevision(record, "workbench"); return; }
        var scope = cfgPath("");
        chooseRevision(record, "workbench").then(function () { if (!disposed && cfgPath("") === scope && S.selected === record.id && !S.error) createEditingDraft(); });
      }
      var options = {
        releases: S.detail.releases || [], records: S.revisions, machines: S.machines,
        selected: S.selected, selectedRole: S.objectRole, collapsed: collapsed, canWrite: canWrite(), zoom: S.evoZoom,
        open: open, edit: edit, versionName: labelOf,
        toggle: function (id) {
          if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
          refreshEvolution('[data-version-toggle="' + id + '"]');
        }
      };
      var search = h("input.input", { type: "search", value: S.evoQuery, "aria-label": "搜索版本或机台", placeholder: "搜索软件版本或机台" });
      function applySearch() { S.evoQuery = search.value.trim(); S.evoSearchCollapsed.clear(); refreshEvolution('[aria-label="搜索版本或机台"]'); }
      search.addEventListener("keydown", function (event) { if (event.key === "Enter") applySearch(); });
      var filter = h("select.select", { "aria-label": "筛选配置状态", onchange: function () { S.evoFilter = filter.value; refreshEvolution('[aria-label="筛选配置状态"]'); } },
        h("option", { value: "all", selected: S.evoFilter === "all" }, "全部状态"), h("option", { value: "draft", selected: S.evoFilter === "draft" }, "有草稿的配置"));
      var toggle = h("div.segmented", { role: "group", "aria-label": "版本显示方式" }, [
        { value: "graph", label: "关系图" }, { value: "list", label: "层级列表" }
      ].map(function (choice) {
        return h("button.btn.btn-sm" + (S.evoView === choice.value ? ".is-active" : ""), {
          type: "button", "aria-pressed": String(S.evoView === choice.value), onclick: function () {
            rememberEvolution(); S.evoView = choice.value; render();
            var scroll = el.querySelector(".cv32-evo-scroll"), position = S.evoPositions[S.evoView];
            if (scroll) scroll.scrollTo(position.left, position.top);
          }
        }, choice.label);
      }));
      var body = !groups.length ? MC.stateBlock({ kind: "empty", title: "没有匹配的版本或机台", desc: "调整搜索或状态筛选后重试。" })
        : S.evoView === "list" ? MC.configEvolution.hierarchy(groups, options) : MC.configEvolution.graph(groups, options);
      var footer = h("div.cv32-evo-footer");
      if (S.evoView === "graph") {
        footer.appendChild(h("div.row.small.muted", h("span", "— 保存演进"), h("span", "┄ 参数来源"), h("span", "草稿以虚线标记")));
        footer.appendChild(h("div.row",
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", disabled: S.evoZoom <= 0.75, "aria-label": "缩小关系图", onclick: function () { S.evoZoom = Math.max(0.75, S.evoZoom - 0.1); refreshEvolution(); } }, MC.icon("minus")),
          h("span.small.mono", Math.round(S.evoZoom * 100) + "%"),
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", disabled: S.evoZoom >= 1.25, "aria-label": "放大关系图", onclick: function () { S.evoZoom = Math.min(1.25, S.evoZoom + 0.1); refreshEvolution(); } }, MC.icon("plus")),
          h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { viewModal({ title: "读图说明", body: h("p", "每列是一个软件版本，每行为标准参数或机台。实线表示已记录的保存演进，虚线表示参数来源。来源标准快照表示机台实际采用的历史标准。点击版本号或节点打开完整参数页面。") }); } }, "读图说明")));
      } else {
        footer.appendChild(h("span.small.muted", groups.length + " 个软件版本 · " + groups.reduce(function (total, group) { return total + group.machines.filter(function (row) { return !!row.saved; }).length; }, 0) + " 台机台 · " + groups.reduce(function (total, group) { return total + group.machines.filter(function (row) { return !row.saved; }).length; }, 0) + " 台待保存"));
        footer.appendChild(h("div.row", [true, false].map(function (expand) {
          return h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () {
            groups.forEach(function (group) { if (expand) collapsed.delete(group.release.release_id); else collapsed.add(group.release.release_id); });
            refreshEvolution();
          } }, expand ? "展开全部" : "收起全部");
        })));
      }
      var toolbar = h("div.cv-toolbar.cv32-evo-toolbar", h("label.cv32-searchbox", icon("search"), search), filter, h("span.spacer"), toggle);
      return h("div.cv-version-view.cv32-version-view", S.detail.file_id ? null : versionActions(),
        h("section.cv32-evo-surface", toolbar, h("div.cv32-evo-scroll.cv-graph-scroll", { tabindex: 0, "aria-label": S.evoView === "graph" ? "版本关系图，可横向和纵向滚动" : "软件版本与机台层级列表" }, body), footer));
    }

    function versionActions() {
      return h("div.cv32-version-actions",
        h("button.btn", { type: "button", disabled: !canWrite() || !S.revisions.some(function (record) { return record.status === "published" && record.kind !== "machine"; }), onclick: machineDialog }, icon("plus"), "新建机台"),
        h("button.btn.btn-primary", { type: "button", disabled: !canWrite(), onclick: createReleaseDialog }, icon("plus"), "新建软件版本"));
    }

    function revisionName(id) { var r = S.revisions.filter(function (n) { return n.id === id; })[0]; return r ? labelOf(r.release_id) + " · " + r.label : id || "—"; }
    function operationContext() {
      var epoch = S.contextEpoch, scope = cfgPath(""), selected = S.selected;
      function readCurrent() { return !disposed && epoch === S.contextEpoch && scope === cfgPath("") && selected === S.selected; }
      return { path: scope, readCurrent: readCurrent, current: function () { return canWrite() && readCurrent(); } };
    }
    function createEditingDraft() {
      var current = revision(), context = operationContext(); if (!current || !canWrite()) return;
      var existing = S.revisions.filter(function (r) { return r.status === "draft" && (current.kind === "machine" ? r.machine_id === current.machine_id : r.kind === "baseline" && r.release_id === current.release_id); })[0];
      if (existing) { chooseRevision(existing, "workbench"); return; }
      api.requestV3("POST", context.path + "/restore-plans", { body: { revision_id: current.id, request_id: util.uid("draft") } }).then(function (res) {
        if (!context.current()) return;
        S.selected = res.draft.draft_id; S.tab = "workbench"; reloadDetail().then(function () { if (!disposed && context.path === cfgPath("")) routeSelection(false); }); MC.toast.success("已进入编辑，可修改参数后保存配置");
      }, function (err) { MC.toastError("创建草稿失败", err); });
    }
    function draftPaths(item) {
      var path = item.kind === "machine" ? "/machines/" + enc(item.machine_id)
        : item.kind === "baseline" ? "/baseline-drafts/" + enc(item.id) : "/drafts/" + enc(item.id);
      return { read: path, values: path + (item.kind === "machine" ? "/draft/values" : "/values"),
        publish: path + (item.kind === "default" ? "/freeze" : "/publish") };
    }
    function readEditingDraft(context, item) {
      return api.getV3(context.path + draftPaths(item).read).then(function (res) { return res.machine ? res.machine.draft : res; });
    }
    function cancelEditing() {
      if (S.savingValues) return;
      confirmDiscard(function () {
        var item = revision(), target;
        if (!item) return;
        if (item.kind === "machine") {
          var machine = S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0];
          target = machine && S.revisions.filter(function (r) { return r.id === machine.head_id; })[0];
        } else target = S.revisions.filter(function (r) { return r.id === item.source; })[0];
        if (target) chooseRevision(target, "workbench"); else changeTab("versions");
      });
    }
    function validValues() {
      if (!Object.keys(S.editErrors).length) return true;
      MC.toast.warning("请先修正参数值"); var invalid = el.querySelector('[aria-invalid="true"]'); if (invalid) invalid.focus(); return false;
    }
    function persistValues(context, item) {
      var submitted = Object.assign({}, S.edits), paths = draftPaths(item);
      return MC.configEditor.persist({ edits: submitted, draft: S.draft, branch: item.kind !== "default", current: context.current,
        write: function (items, expected) {
          return api.requestV3("PATCH", context.path + paths.values, { body: { request_id: util.uid("values"), expected_revision: expected,
            expected_draft_id: item.id, items: items } }).then(function (res) { return res.draft || res; });
        }, read: function () { return readEditingDraft(context, item); }, saved: function (ids, draft) {
          if (!context.readCurrent()) return;
          S.draft = draft;
          ids.forEach(function (id) { if (S.edits[id] === submitted[id]) { delete S.edits[id]; S.explicitModes.delete(id); } });
          S.dirty = Object.keys(S.edits).length > 0;
        } });
    }
    function stashValues() {
      if (S.savingValues || !editable() || !validValues()) return Promise.resolve(false);
      var context = operationContext(), item = revision();
      if (!S.dirty) { MC.toast.info("草稿已经暂存"); return Promise.resolve(true); }
      S.savingValues = true; S.valueError = null; render();
      return persistValues(context, item).then(function () {
        if (!context.readCurrent()) return false;
        MC.toast.success("草稿已暂存"); return true;
      }, function (err) { if (context.readCurrent()) S.valueError = err; return false; }).then(function (ok) {
        if (context.readCurrent()) { S.savingValues = false; render(); } return ok;
      });
    }
    function reviewSave(afterSave) {
      if (S.savingValues || !editable() || !validValues()) return;
      var context = operationContext(), item = revision(), captured = JSON.stringify(S.edits);
      var pending = S.pendingSave && S.pendingSave.selected === item.id ? S.pendingSave : null;
      var note = h("textarea.input", { rows: 2, maxlength: 1000, "aria-label": "修改说明", placeholder: "选填，说明本次修改", value: pending ? pending.note : "", disabled: !!(pending && pending.attempted) });
      var values = Object.assign({}, S.draft.values || {}, S.edits), saved = S.lastSavedValues || {}, branch = item.kind !== "default";
      var ids = editedParameterIds();
      var table = h("div"), reviewOffset = 0, reviewSeq = 0, loading = false, confirm;
      var modes = { inherit: "使用标准值", override: "单独设置", pin: "保持此值" };
      var body = h("div.col", { style: "gap:12px" }, h("p.small.muted", "保存为新的独立记录，仅保存在系统中，不下发机台。"),
        h("p.small", "软件 " + labelOf(item.release_id) + " · " + ids.length + " 项变化（包括已暂存的修改）"), table, MC.field({ label: "修改说明", control: note }));
      function loadReview(offset) {
        reviewOffset = offset; var seq = ++reviewSeq; loading = true; if (confirm) confirm.disabled = true;
        util.clear(table); table.appendChild(MC.stateBlock({ kind: "loading", title: "正在核对变化…" }));
        api.requestV3("POST", context.path + "/parameter-query", { body: { revision_id: item.id, filter: "modified", changed_ids: ids, edits: S.edits, offset: offset, limit: 30 } }).then(function (res) {
          if (!context.current() || seq !== reviewSeq || !table.isConnected) return;
          loading = false; if (confirm) confirm.disabled = false; util.clear(table);
          table.appendChild(h("div.table-wrap.cv32-review-table", h("table.table", h("thead", h("tr", ["参数", "上次保存", "本次配置", "取值方式"].map(function (name) { return h("th", name); }))),
            h("tbody", (res.items || []).map(function (p) { var id = p.parameter_id;
              return h("tr", h("td", p.name), h("td.mono", memberText(saved[id])), h("td.mono", memberText(values[id])),
                h("td", branch ? (modes[(saved[id] || {}).intent || "inherit"] + " → " + modes[(values[id] || {}).intent || "inherit"]) : "标准参数"));
            })))));
          if (!ids.length) table.appendChild(h("p.small.muted", "参数值没有变化，将保存当前配置与来源。"));
          table.appendChild(pagerRow(ids.length, reviewOffset, 30, loadReview));
        }, function (err) {
          if (!context.current() || seq !== reviewSeq || !table.isConnected) return;
          loading = false; util.clear(table); table.appendChild(MC.errorBlock(err, function () { loadReview(reviewOffset); }));
        });
      }
      var modal = viewModal({ title: "核对并保存配置", size: "wide", focusTitle: true, body: body, footer: function (close) {
        confirm = h("button.btn.btn-primary", { type: "button", onclick: function () {
          if (loading) return;
          if (!context.current() || S.savingValues || captured !== JSON.stringify(S.edits)) { MC.toast.warning("配置已变化，请重新核对"); return; }
          close(); S.afterSave = typeof afterSave === "function" ? afterSave : null; saveConfiguration(context, item, note.value);
        } }, pending ? "重试保存配置" : "保存配置");
        return [h("button.btn", { type: "button", onclick: close }, "取消"), confirm];
      } });
      loadReview(0);
    }
    function saveConfiguration(context, item, note) {
      S.savingValues = true; S.valueError = null;
      var pending = S.pendingSave && S.pendingSave.selected === item.id ? S.pendingSave : null;
      S.pendingSave = pending || { selected: item.id, requestId: util.uid("save"), note: note };
      pending = S.pendingSave; if (!pending.attempted) pending.note = note;
      render();
      // Re-read first on retries. Every formal write reuses its original receipt key.
      var prepare = pending.attempted ? readEditingDraft(context, item).catch(function (err) {
        if (err.status !== 404) throw err;
        // A newer standard draft may have replaced this one. The server can
        // still return its original immutable save receipt; it cannot save a
        // replacement draft through this ID.
      }) : Promise.resolve();
      prepare.then(function () { return pending.attempted ? { revision: pending.expectedRevision } : persistValues(context, item); }).then(function (draft) {
        if (!context.current()) throw { code: "CONTEXT_CHANGED" };
        pending.expectedRevision = draft.revision;
        pending.attempted = true;
        return api.requestV3("POST", context.path + draftPaths(item).publish, { body: { request_id: pending.requestId,
          expected_revision: draft.revision, expected_draft_id: item.id, note: pending.note } });
      }).then(function (res) {
        if (!context.readCurrent()) return;
        S.pendingSave = null; S.historyEvents = null;
        S.parameterFilter = "all"; if (S.workbenchQuery) S.workbenchQuery.filter = "all";
        S.selected = item.kind === "machine" ? res.machine.published_revision_id || res.machine.head_id : item.kind === "baseline" ? res.baseline.baseline_id : item.id;
        MC.toast.success("配置已保存");
        S.savingValues = false; reloadDetail().then(function () {
          if (disposed || context.path !== cfgPath("")) return;
          var after = S.afterSave; S.afterSave = null; if (after) after(); else routeSelection(true);
        });
      }, function (err) {
        if (!context.readCurrent()) return;
        S.savingValues = false; S.valueError = err; render();
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
      var request = S.workbenchQuery ? api.requestV3("POST", scope + "/parameter-query", { body: Object.assign({}, S.workbenchQuery, {
        revision_id: S.selected, directory: S.folderActive ? S.dirParent : "", changed_ids: editedParameterIds(), edits: S.edits, offset: Math.max(0, offset), limit: S.paramLimit
      }) }) : api.getV3(scope + "/parameters?offset=" + Math.max(0, offset) + "&limit=" + S.paramLimit + "&release_id=" + enc(S.releaseId || "") + parameterRecordQuery(S.releaseId));
      request.then(function (res) {
        if (!current()) return;
        S.paramPage = res;
        if (S.folderActive && S.workbenchQuery) { S.dirData = Object.assign({}, res, { parameter_total: res.total }); S.dirOffset = res.offset || 0; }
        S.paramTotal = res.total == null ? (res.items || []).length : res.total;
        S.definitionTotal = S.workbenchQuery ? res.parameter_total || S.definitionTotal : S.paramTotal;
        S.paramOffset = res.offset || 0;
        S.params = res;
        S.paramLoading = false;
        render();
      }, function (err) { if (current()) { S.paramLoading = false; MC.toastError("读取参数失败", err); render(); } });
    }

    function loadAllParams() {
      if (S.allLoading) return;
      S.allLoading = true;
      var scope = cfgPath(""), rid = S.releaseId, selected = S.selected;
      api.getV3(scope + "/parameters?release_id=" + enc(rid || "") + parameterRecordQuery(rid)).then(function (res) {
        if (disposed || scope !== cfgPath("") || rid !== S.releaseId || selected !== S.selected) return;
        S.allParams = res.items || [];
        S.allLoading = false;
        S.paramTotal = res.total == null ? S.allParams.length : res.total;
        render();
      }, function (err) { if (!disposed && scope === cfgPath("") && rid === S.releaseId) { S.allLoading = false; MC.toastError("读取参数失败", err); } });
    }

    function loadDir(parent, offset) {
      if (S.workbenchQuery) { S.folderActive = !!parent; S.dirParent = parent || ""; S.dirOffset = offset || 0; return loadParamPage(offset || 0); }
      var scope = cfgPath(""), rid = S.releaseId, seq = ++directorySeq;
      function current() { return !disposed && seq === directorySeq && scope === cfgPath("") && rid === S.releaseId; }
      S.dirLoading = true;
      S.dirError = null;
      var request;
      if (S.definitionTotal <= 1000) {
        request = (S.allParams ? Promise.resolve({ items: S.allParams }) : api.getV3(scope + "/parameters?release_id=" + enc(rid || "") + parameterRecordQuery(rid))).then(function (res) {
          var parts = (parent || "").split("/").filter(Boolean), all = res.items || [], items = all.filter(function (p) { return parts.every(function (part, i) { return (p.directory || [])[i] === part; }); });
          if (current()) S.allParams = all;
          return { items: items.slice(offset || 0, (offset || 0) + S.paramLimit), parameter_total: items.length, offset: offset || 0 };
        });
      } else request = api.getV3(scope + "/parameter-tree?parent=" + enc(parent || "")
        + "&recursive=true&offset=" + Math.max(0, offset || 0) + "&limit=" + S.paramLimit + "&release_id=" + enc(rid || "") + parameterRecordQuery(rid));
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
      var key = scope + "|" + releaseId + "|" + version + (releaseId ? "|" + S.selected : "");
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
        api.getV3(scope + "/parameter-tree?parent=" + enc(path) + "&offset=0&limit=1&release_id=" + enc(releaseId || "") + parameterRecordQuery(releaseId)).then(function (res) {
          if (disposed || scope !== cfgPath("")) return;
          node.loading = false; node.items = res.directories || []; if (cache.draw) cache.draw();
        }, function (err) { if (disposed || scope !== cfgPath("")) return; node.loading = false; node.error = err; if (cache.draw) cache.draw(); });
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
        if (activePath) actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", "aria-label": "当前目录操作", onclick: function (event) { viewMenu(event.currentTarget, [
          { label: "重命名 / 移动目录", onClick: function () { directoryDialog("move"); } },
          { label: "删除空目录", danger: true, onClick: function () { directoryDialog("delete"); } }
        ]); } }, icon("more")));
      }
      cache.draw = draw;
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

    function editedParameterIds() {
      var values = Object.assign({}, S.draft && S.draft.values || {}, S.edits), saved = S.lastSavedValues || {}, branch = revision() && revision().kind !== "default";
      return Array.from(new Set(Object.keys(values).concat(Object.keys(saved)))).filter(function (id) { return MC.configEditor.key(values[id], branch) !== MC.configEditor.key(saved[id], branch); });
    }

    function workbenchView(rel) {
      if (!rel) return MC.stateBlock({ kind: "empty", title: "先创建软件版本" });
      var current = revision(), values = (S.draft && S.draft.values) || {}, canEdit = editable() && !S.savingValues && !(S.pendingSave && S.pendingSave.attempted);
      var save = h("button.btn.btn-primary.btn-sm", { type: "button", disabled: !editable() || !!S.savingValues, onclick: reviewSave }, "核对并保存");
      var search = h("input.input", { type: "search", "aria-label": "搜索参数", placeholder: "搜索参数名称或文件字段", value: S.workbenchQuery && S.workbenchQuery.q || "" });
      var body = h("tbody");
      var defaultValues = (rel.d0 && (rel.d0.frozen_snapshot || rel.d0.values)) || {};
      var editing = current && current.status === "draft" && canWrite();
      function parameterDetails(p, binding) {
        var facts = h("dl.cv32-parameter-facts");
        [["数据类型", p.type || "文本"], ["单位", p.unit || "—"], ["目录", (p.directory || []).join(" / ") || "未分组"],
          ["文件字段", binding && binding.locator || "尚未对应文件字段"]].forEach(function (pair) { facts.appendChild(h("dt", pair[0])); facts.appendChild(h("dd", pair[1])); });
        viewModal({ title: p.name, body: h("div.col", { style: "gap:12px" }, facts,
          h("details", h("summary.small.muted", "技术详情"), h("pre.small.mono", JSON.stringify({ parameter_id: p.parameter_id, attrs: p.attrs || {}, source: p.source || {} }, null, 2)))) });
      }
      function makeRow(p) {
        var original = values[p.parameter_id] || { membership: "not_introduced", enabled: true, value_state: "unset" };
        var row = S.edits[p.parameter_id] || original;
        var boolean = ["bool", "boolean"].indexOf(String(p.type).toLowerCase()) >= 0;
        var control = boolean ? h("select.select", { "aria-label": p.name + "的配置值" },
          h("option", { value: "", selected: row.value == null }, "请选择"),
          h("option", { value: "true", selected: String(row.value) === "true" }, "true"),
          h("option", { value: "false", selected: String(row.value) === "false" }, "false"))
          : h("input.input.input-sm", { type: "text", inputmode: /^(number|decimal|float|int|integer)$/.test(p.type) ? "decimal" : "text",
            value: row.value == null ? "" : String(row.value), "aria-label": p.name + "的配置值" });
        control.disabled = !canEdit || row.value_state !== "present";
        var state = h("select.select", { "aria-label": p.name + "的值状态", disabled: !canEdit },
          [["present", "有值"], ["unset", "未设"], ["null", "null"]].map(function (o) { return h("option", { value: o[0], selected: (row.value_state || "unset") === o[0] }, o[1]); }));
        var basis = (S.basis || {})[p.parameter_id], branch = current && current.kind !== "default";
        var intent = h("select.select", { "aria-label": p.name + "的取值方式", disabled: !canEdit },
          [["inherit", "使用标准值"], ["override", "单独设置"], ["pin", "保持此值"]].map(function (o) {
            return h("option", { value: o[0], selected: (row.intent || "inherit") === o[0], disabled: o[0] === "pin" && state.value !== "present" }, o[1]);
          }));
        var errorId = util.uid("parameter-error"), validation = h("div.small.cv-value-error", { id: errorId, role: "alert" });
        control.setAttribute("aria-describedby", errorId);
        function validate(update) {
          var message = MC.configEditor.error(p, update);
          control.setAttribute("aria-invalid", message ? "true" : "false");
          validation.textContent = message;
          if (message) S.editErrors[p.parameter_id] = message; else delete S.editErrors[p.parameter_id];
        }
        function remember(intentChanged) {
          var update = { membership: "included", enabled: row.enabled !== false, value_state: state.value };
          if (state.value === "present") update.value = control.value;
          if (branch) {
            if (intentChanged) S.explicitModes.add(p.parameter_id);
            update.intent = intent.value;
            if (intentChanged && intent.value === "inherit") {
              update = Object.assign({ membership: "not_introduced", enabled: true, value_state: "unset" }, basis || {}, { intent: "inherit" });
              state.value = update.value_state; control.value = update.value == null ? "" : String(update.value);
            }
            if (update.intent === "pin" && state.value !== "present") update.intent = "override";
          }
          var changed = MC.configEditor.change(original, update, branch, S.explicitModes.has(p.parameter_id));
          if (changed) S.edits[p.parameter_id] = changed; else delete S.edits[p.parameter_id];
          if (branch) intent.value = (changed || original).intent || "inherit";
          intent.options[2].disabled = state.value !== "present";
          S.dirty = Object.keys(S.edits).length > 0;
          control.disabled = !canEdit || state.value !== "present";
          validate(changed || original);
          var status = el.querySelector("[data-edit-count]");
          if (status) status.textContent = Object.keys(S.edits).length + " 项修改尚未暂存";
          var tableRow = control.closest("tr"); if (tableRow) tableRow.classList.toggle("cv32-changed", !!changed);
        }
        control.addEventListener("input", function () {
          if (branch && intent.value === "inherit") S.explicitModes.delete(p.parameter_id);
          remember(false);
        });
        state.addEventListener("change", function () { remember(false); }); intent.addEventListener("change", function () { remember(true); });
        if (S.edits[p.parameter_id]) validate(S.edits[p.parameter_id]);
        var binding = (S.bindings || []).filter(function (b) { return b.parameter_id === p.parameter_id; })[0];
        var different = memberKey(row) !== memberKey(basis);
        return h("tr", { "data-parameter-id": p.parameter_id, "data-different": different ? "true" : "false", class: S.edits[p.parameter_id] ? "cv32-changed" : "" },
          h("td", h("button.btn.btn-ghost.cv32-parameter-name", { type: "button", onclick: function () { parameterDetails(p, binding); } }, p.name), h("div.muted.xsmall.cv32-parameter-directory", (p.directory || []).join(" / "))),
          editing ? h("td.mono.small.cv32-old-value", memberText((S.lastSavedValues || {})[p.parameter_id])) : null,
          h("td", canEdit ? h("div.col", { style: "gap:4px" }, control, state, validation) : h("strong.mono", memberText(row))), h("td.muted", p.unit || "—"),
          h("td.small", current && current.kind !== "machine" ? "版本设置" : canEdit ? intent : ({ pin: "保持此值", override: "单独设置", inherit: "使用标准值" }[row.intent || "inherit"])),
          S.showFileField ? h("td.small.mono.cv32-file-field", binding ? binding.locator : "尚未对应") : null);
      }
      var page = S.folderActive ? S.dirData : S.paramPage || S.params;
      var rows = S.paramMode === "tree" && S.selectedParam ? [S.selectedParam] : page && page.items || [];
      rows.forEach(function (p) { body.appendChild(makeRow(p)); });
      if (!rows.length) body.appendChild(h("tr", h("td", { colspan: 7 }, S.paramLoading || S.dirLoading ? "正在读取参数…" : "没有匹配的参数")));
      function searchParameters() {
        directorySeq++; S.dirLoading = false;
        S.workbenchQuery = { q: search.value.trim(), filter: S.parameterFilter, reference_id: parameterReference(current) };
        S.selectedParam = null; S.paramMode = "list"; loadParamPage(0);
      }
      search.addEventListener("keydown", function (event) { if (event.key === "Enter") searchParameters(); });
      var display = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "参数表显示设置", title: "显示列与行密度", onclick: function () {
        var fields = h("input", { type: "checkbox", checked: S.showFileField });
        var density = h("select.select", { "aria-label": "行密度" }, ["comfortable", "compact"].map(function (value) { return h("option", { value: value, selected: S.parameterDensity === value }, value === "compact" ? "紧凑" : "标准"); }));
        var mode = MC.segmented([{ value: "list", label: "列表" }, { value: "tree", label: "参数树" }], S.paramMode, function (value) { S.paramMode = value; render(); }, "参数视图");
        fields.addEventListener("change", function () { S.showFileField = fields.checked; render(); });
        density.addEventListener("change", function () { S.parameterDensity = density.value; render(); });
        viewModal({ title: "参数表显示设置", body: h("div.col", { style: "gap:16px" }, h("label.row", fields, "显示文件字段"), MC.field({ label: "行密度", control: density }), h("div.col", h("strong.small", "参数视图"), mode)) });
      } }, icon("settings"));
      var pageSize = h("select.select", { "aria-label": "每页参数数", onchange: function () { S.paramLimit = Number(pageSize.value); if (S.folderActive) loadDir(S.dirParent, 0); else loadParamPage(0); } },
        [30, 50, 100].map(function (size) { return h("option", { value: String(size), selected: size === S.paramLimit }, size + " 项 / 页"); }));
      var folders = folderBrowser(S.releaseId, S.folderActive ? S.dirParent : "", S.definitionTotal, function (path) {
        S.selectedParam = null; S.folderActive = !!path; S.dirOffset = 0;
        if (path) loadDir(path, 0); else loadParamPage(0);
      });
      if (S.paramMode === "tree" && S.definitionTotal <= 1000) {
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
      var editbar = current && current.status === "draft" && canWrite() ? h("div.cv32-editbar",
        h("span.small", { role: "status", "data-edit-count": "1" }, S.savingValues ? "正在保存…" : Object.keys(S.edits).length + " 项修改尚未暂存"), h("span.spacer"),
        h("button.btn.btn-sm", { type: "button", disabled: !!S.savingValues, onclick: cancelEditing }, "取消编辑"),
        h("button.btn.btn-sm", { type: "button", disabled: !!S.savingValues || !!(S.pendingSave && S.pendingSave.attempted), onclick: stashValues }, "暂存草稿"), save) : null;
      return h("div.cv-workbench.cv32-workbench" + (S.parameterDensity === "compact" ? ".is-compact" : ""),
        S.valueError ? h("div.banner.banner-warning", { role: "alert" }, h("div.banner-body", "保存未完成，已暂存的内容和本次输入均已保留。", MC.errorText(S.valueError)), S.valueError.code === "REVISION_CONFLICT" ? h("button.btn.btn-sm", { type: "button", onclick: recoverValues }, "核对最新草稿") : null) : null,

        h("div.cv-param-browser", folders, h("section.cv-param-data", h("div.cv-toolbar", h("label.cv32-searchbox", icon("search"), search, h("span.small.muted", "/")), h("span.spacer"),
          h("div.cv32-parameter-actions", h("button.btn.btn-ghost", { type: "button", onclick: compareParameters }, icon("compare"), "对比"),
          current && current.kind !== "machine" && !historicalRecord(current) && S.tab !== "initial" ? h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: importParameters }, icon("upload"), "导入") : null, display)),
          S.paramMode === "tree" && S.selectedParam ? h("div.cv-folder-trail.small", S.selectedParam.name) : null,
          h("div.cv32-filterbar", { role: "group", "aria-label": "筛选参数" }, [
            { value: "all", label: "全部" },
            parameterReference(current) ? { value: "different", label: current.kind === "machine" ? "与标准值不同" : "与初始值不同" } : null,
            editing ? { value: "modified", label: "本次修改" } : null
          ].filter(Boolean).map(function (option) { return h("button", { type: "button", "aria-pressed": String(S.parameterFilter === option.value), onclick: function () { S.parameterFilter = option.value; searchParameters(); } }, option.label); })),
          h("div.table-wrap", { tabindex: 0, "aria-label": "参数表滚动区域" }, h("table.table.cv32-parameter-table", h("thead", h("tr", h("th", "参数名称"), editing ? h("th", "上次保存") : null,
            h("th", editing ? "编辑值" : "当前值"), h("th", "单位"), h("th", current && current.kind === "machine" ? "取值方式" : "值状态"), S.showFileField ? h("th", "文件字段") : null)), body)),
          h("div.cv-param-footer", pageSize, pagerRow(S.paramMode === "tree" && S.selectedParam ? 1 : S.folderActive ? (page && page.parameter_total || 0) : S.paramTotal, S.paramMode === "tree" && S.selectedParam ? 0 : S.folderActive ? S.dirOffset : S.paramOffset, S.paramLimit, function (offset) { if (S.folderActive) { S.dirOffset = offset; loadDir(S.dirParent, offset); } else loadParamPage(offset); })))), editbar);
    }

    function recoverValues() {
      var item = revision(); if (!item) return;
      var scope = cfgPath(""), selected = S.selected, edits = JSON.stringify(S.edits), before = S.draft;
      var path = item.kind === "machine" ? "/machines/" + enc(item.machine_id) : item.kind === "baseline" ? "/baseline-drafts/" + enc(item.id) : "/drafts/" + enc(item.id);
      api.getV3(scope + path).then(function (response) {
        if (disposed || scope !== cfgPath("") || selected !== S.selected) return;
        var latest = response.machine ? response.machine.draft : response;
        if (!latest || latest.frozen || latest.status === "published" || latest.draft_id && latest.draft_id !== item.id) { MC.toast.warning("该草稿已保存或被替换，本次输入仍保留。请返回版本演进核对。"); return; }
        var sourceChanged = ["base_revision_id", "source_revision_id", "definition_revision"].some(function (key) { return before[key] !== latest[key]; }) || response.machine && response.machine.release_id !== item.release_id;
        var recovery = MC.configEditor.rebase(before.values || {}, S.edits, latest.values || {}, item.kind !== "default", sourceChanged);
        var choices = {}, offset = 0, body = h("div.col", { style: "gap:12px" }), confirm;
        var modal = viewModal({ title: "配置已被其他人修改", size: "wide", focusTitle: true, body: body, footer: function (close) {
          confirm = h("button.btn.btn-primary", { type: "button", onclick: function () {
            if (disposed || scope !== cfgPath("") || selected !== S.selected || !canWrite()) return;
            if (JSON.stringify(S.edits) !== edits) { MC.toast.warning("修改已变化，请重新核对"); return; }
            var next = Object.assign({}, recovery.edits);
            recovery.conflicts.forEach(function (r) { if (choices[r.parameter_id] === "mine") next[r.parameter_id] = r.mine; });
            S.pendingSave = null; S.draft = latest; S.edits = next; S.dirty = Object.keys(next).length > 0; S.valueError = null; S.editErrors = {}; S.explicitModes = new Set(Object.keys(next));
            close(); reloadDetail();
          } }, "继续编辑");
          return [h("button.btn", { type: "button", onclick: close }, "取消"), confirm];
        } });
        function draw() {
          util.clear(body);
          body.appendChild(h("p.small", sourceChanged ? "草稿的参照配置或目标版本已变化，请逐项选择要保留的修改。" : "未冲突的修改自动保留；双方都修改的参数需要选择。此步不会保存或覆盖服务器。"));
          body.appendChild(h("div.table-wrap.cv32-review-table", h("table.table", h("thead", h("tr", ["参数", "编辑前", "服务器最新", "您的修改", "保留哪份"].map(function (label) { return h("th", label); }))),
            h("tbody", recovery.conflicts.slice(offset, offset + 30).map(function (row) {
              var select = h("select.select", { "aria-label": row.parameter_id + "的冲突处理", onchange: function () { choices[row.parameter_id] = select.value; update(); } },
                h("option", { value: "" }, "请选择"), h("option", { value: "mine", selected: choices[row.parameter_id] === "mine" }, "我的修改"), h("option", { value: "server", selected: choices[row.parameter_id] === "server" }, "服务器最新"));
              return h("tr", h("td", row.parameter_id), h("td.mono", memberText(row.before)), h("td.mono", memberText(row.latest)), h("td.mono", memberText(row.mine)), h("td", select));
            })))));
          if (!recovery.conflicts.length) body.appendChild(h("p", "没有同时修改的参数，可以继续编辑。"));
          body.appendChild(pagerRow(recovery.conflicts.length, offset, 30, function (v) { offset = v; draw(); })); update();
        }
        function update() { confirm.disabled = recovery.conflicts.some(function (r) { return !choices[r.parameter_id]; }); }
        draw();
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

    function importParameters() {
      if (!canWrite() || S.savingValues || !revision() || revision().kind === "machine" || historicalRecord(revision()) || S.tab === "initial") return;
      var context = operationContext(), releaseId = S.releaseId, selectedId = S.selected;
      var state = { files: [], file: "", namesOnly: false, plan: null, choices: {}, offset: 0, busy: true, error: null, requestId: null, attempted: false };
      var body = h("div"), modal, closeButton, nextButton;
      function alive() { return !disposed && context.path === cfgPath("") && selectedId === S.selected && modal && modal.el.isConnected; }
      function selectedFile() { return state.files.filter(function (file) { return file.version_id === state.file; })[0]; }
      function draw() {
        if (!alive()) return;
        util.clear(body);
        closeButton.disabled = state.busy; nextButton.disabled = state.busy;
        var dismiss = modal.el.querySelector('.modal-header [aria-label="关闭"]'); if (dismiss) dismiss.disabled = state.busy;
        nextButton.textContent = state.plan ? state.namesOnly ? "完成核对" : state.attempted ? "确认导入结果" : "导入到草稿" : "下一步";
        if (state.error) body.appendChild(h("div.banner.banner-warning", { role: "alert" }, h("div.banner-body", MC.errorText(state.error)),
          !state.attempted || state.error.status === 409 ? h("button.btn.btn-sm", { type: "button", disabled: state.busy, onclick: function () { if (selectedFile()) preview(); else loadFiles(); } }, "重试") : null));
        if (state.busy) { body.appendChild(MC.stateBlock({ kind: "loading", title: state.plan ? "正在导入…" : "正在读取文件…" })); return; }
        if (!state.plan) {
          var select = h("select.select", { "aria-label": "导入文件", onchange: function () { state.file = select.value; } }, state.files.map(function (file) { return h("option", { value: file.version_id, selected: file.version_id === state.file }, file.name); }));
          var namesOnly = h("input", { type: "checkbox", checked: state.namesOnly, onchange: function () { state.namesOnly = namesOnly.checked; } });
          body.appendChild(h("div.col", { style: "gap:16px" }, MC.field({ label: "配置文件", control: select }), h("label.row.small", namesOnly, "仅导入参数名称（不修改值）")));
          if (!state.files.length) { body.appendChild(MC.stateBlock({ kind: "empty", title: "还没有 XML / JSON 配置文件", desc: "从左侧配置文件入口导入文件后，再回来选择。" })); nextButton.disabled = true; }
          return;
        }
        var rows = state.plan.items || [], unresolved = rows.filter(function (row) { return row.status === "unbound" && !state.choices[row.row_id]; });
        body.appendChild(h("p.small", state.plan.source.name + " · " + rows.length + " 个字段 · 软件 " + labelOf(releaseId)));
        body.appendChild(h("p.small.muted", state.namesOnly ? "本次仅核对已有参数名称和字段，参数值保持原样。" : "已确认的文件字段将读取名称和数值；未找到对应参数的字段须选择已有参数或明确忽略。"));
        function rowError(row) {
          if (state.namesOnly) return "";
          var choice = state.choices[row.row_id] || (row.status === "bound" ? "import" : "skip");
          if (choice === "skip") return "";
          var candidate = (row.candidates || []).filter(function (p) { return choice === "link:" + p.parameter_id; })[0];
          return MC.configEditor.error({ type: candidate ? candidate.type : row.data_type }, { membership: "included", enabled: true, value_state: row.value === null ? "null" : "present", value: row.value });
        }
        var invalid = rows.filter(function (row) { return !!rowError(row); });
        body.appendChild(h("div.table-wrap.cv32-import-table", h("table.table", h("thead", h("tr", (state.namesOnly ? ["文件字段", "数据类型", "对应参数 / 处理方式"] : ["文件字段", "文件值", "数据类型", "对应参数 / 处理方式"]).map(function (label) { return h("th", label); }))),
          h("tbody", rows.slice(state.offset, state.offset + 30).map(function (row) {
            var choice = state.choices[row.row_id] || (row.status === "bound" ? "import" : ""), select = h("select.select", { "aria-label": row.leaf_name + "的导入处理", "data-import-decision": row.row_id, disabled: state.attempted,
              onchange: function () { state.choices[row.row_id] = select.value; draw(); } },
              row.status === "bound" ? h("option", { value: "import", selected: choice === "import" }, "导入到已对应参数") : h("option", { value: "", selected: !choice }, "请选择处理方式"),
              (row.candidates || []).map(function (candidate) { return h("option", { value: "link:" + candidate.parameter_id, selected: choice === "link:" + candidate.parameter_id }, candidate.name + " · " + candidate.directory.join(" / ")); }),
              h("option", { value: "skip", selected: choice === "skip" }, "忽略此字段"));
            return h("tr", h("td.mono.small", row.display_path || row.canonical_locator), state.namesOnly ? null : h("td.mono.small", row.value === null ? "null" : typeof row.value === "string" ? JSON.stringify(row.value) : String(row.value)),
              h("td.small", { decimal: "数值", bool: "布尔值", string: "文本", null: "空值" }[row.value_kind] || row.value_kind), h("td", select, rowError(row) ? h("div.small.cv-value-error", { role: "alert" }, rowError(row)) : null));
          })))));
        if (rows.some(function (row) { return row.status === "unbound"; })) body.appendChild(h("p.small.muted", "新增参数请在「管理参数」中处理。"));
        var applying = rows.filter(function (row) { return (state.choices[row.row_id] || (row.status === "bound" ? "import" : "skip")) !== "skip"; }).length;
        body.appendChild(h("div.row.small", { role: "status" }, invalid.length ? invalid.length + " 个字段的值不符合参数类型，可忽略或更正来源文件。" : unresolved.length ? "还有 " + unresolved.length + " 个字段需要处理" : state.namesOnly ? "已核对 " + applying + " 个字段，数值保持原样。" : "将导入 " + applying + " 个字段，不会正式保存或下发机台。"));
        body.appendChild(pagerRow(rows.length, state.offset, 30, function (offset) { state.offset = offset; draw(); }));
        nextButton.disabled = !canWrite() || unresolved.length > 0 || applying === 0 || invalid.length > 0;
        if (!state.attempted) body.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { state.plan = null; state.error = null; draw(); } }, "返回选择文件"));
      }
      function preview() {
        if (!alive() || state.busy || !selectedFile()) return;
        if (!state.namesOnly && S.dirty) {
          viewConfirm({ title: "先暂存当前编辑？", message: "暂存后继续核对文件，当前输入将保留在草稿中。", confirmText: "暂存并继续", cancelText: "继续编辑" }).then(function (ok) {
            if (ok && alive()) stashValues().then(function (saved) { if (saved && alive()) preview(); });
          }); return;
        }
        state.busy = true; state.error = null; state.plan = null; state.attempted = false; draw();
        api.requestV3("POST", context.path + "/value-import-plans", { body: { release_id: releaseId, version_id: state.file } }).then(function (res) {
          if (!alive()) return; state.plan = res.plan; state.offset = 0; state.requestId = util.uid("import"); state.busy = false; draw();
        }, function (err) { if (alive()) { state.error = err; state.busy = false; draw(); } });
      }
      function commit() {
        if (!alive() || state.busy || !state.plan || nextButton.disabled) return;
        if (state.namesOnly) { modal.close(); MC.toast.success("参数名称和字段已核对", "参数值保持原样。"); return; }
        var plan = state.plan, path = context.path + "/value-import-plans/" + enc(plan.plan_id), groups = {};
        (plan.items || []).forEach(function (row) {
          var choice = state.choices[row.row_id]; if (!choice) return;
          if (!groups[choice]) groups[choice] = []; groups[choice].push(row.row_id);
        });
        state.busy = true; state.error = null; draw();
        var chain = Promise.resolve();
        Object.keys(groups).forEach(function (choice) { chain = chain.then(function () {
          if (!alive()) throw { code: "CONTEXT_CHANGED", message: "已离开当前配置" };
          if (!canWrite()) throw { status: 403, code: "FORBIDDEN", message: "管理员权限已撤回，导入选择已保留。" };
          return api.requestV3("PATCH", path + "/decisions", { body: { scope: "rows", row_ids: groups[choice], decision: choice.indexOf("link:") === 0 ? "link" : choice, parameter_id: choice.indexOf("link:") === 0 ? choice.slice(5) : undefined } });
        }); });
        chain.then(function () {
          if (!alive()) throw { code: "CONTEXT_CHANGED" };
          if (!canWrite()) throw { status: 403, code: "FORBIDDEN", message: "管理员权限已撤回，导入选择已保留。" };
          state.attempted = true;
          return api.requestV3("POST", path + "/commit", { body: { request_id: state.requestId, expected_draft_id: plan.target.draft_id, expected_revision: plan.target.revision } });
        }).then(function (res) {
          if (!alive()) return;
          modal.close(); S.selected = res.draft_id; S.expectedMachine = null; S.expectedStandard = releaseId; S.releaseId = releaseId; S.objectRole = "standard"; S.tab = "workbench";
          S.edits = {}; S.editErrors = {}; S.explicitModes.clear(); S.dirty = false; S.valueError = null;
          MC.toast.success("已导入草稿", "核对参数后可保存配置。"); reloadDetail().then(function () { if (!disposed && context.path === cfgPath("")) routeSelection(false); });
        }, function (err) { if (alive()) { state.error = err; if (err.status && err.status < 500) state.attempted = false; state.busy = false; draw(); } });
      }
      modal = viewModal({ title: "导入参数", size: "wide", body: body, onDismiss: function () { if (!state.busy) modal.close(); }, footer: function (close) {
        closeButton = h("button.btn", { type: "button", onclick: function () { if (!state.busy) close(); } }, "取消");
        nextButton = h("button.btn.btn-primary", { type: "button", onclick: function () { if (state.plan) commit(); else preview(); } }, "下一步");
        return [closeButton, nextButton];
      } });
      function loadFiles() {
        state.busy = true; state.error = null; draw();
        return api.getV3(root("/file-selector")).then(function (res) {
        if (!alive()) return;
        state.files = (res.items || []).filter(function (file) { return /\.(json|xml)$/i.test(file.name); });
        var associated = state.files.filter(function (file) { return file.file_id === S.detail.file_id; })[0];
        state.file = (associated || state.files[0] || {}).version_id || ""; state.busy = false; draw();
        }, function (err) { if (alive()) { state.error = err; state.busy = false; draw(); } });
      }
      loadFiles();
    }

    function extractDialog(options) {
      if (!S.cid) return;
      options = options && options.file ? options : {};
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
          if (options.file && !state.files.some(function (file) { return file.file_id === options.file.file_id && file.version_id === options.file.version_id; })) {
            options = {}; state.error = { message: "所选文件已变化，请重新选择文件。" }; state.loading = false; renderDialog(); return;
          }
          if (state.files.length) state.fileId = options.file ? options.file.file_id : state.files[0].file_id;
          state.loading = false;
          renderDialog();
          if (options.file) { options = {}; createPlan(); }
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
            h("div.banner-body", "文件或参数目录已变化。请重新核对，仍适用的处理方式会保留。",
              h("button.btn.btn-sm", { type: "button", style: "margin-left:10px", onclick: recomputePlan }, "重新核对"))));
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
      ["keep_local", "保留当前配置"], ["adopt_target", "使用新的标准"], ["custom", "自定义值"],
      ["deactivate", "停用"], ["remove", "移除"], ["pin", "保持此值"], ["pending", "请选择"],
    ];

    function compareParameters() {
      var item = revision(); if (!item) return;
      var context = operationContext(), seq = 0, offset = 0, size = 30, data = null, error = null, busy = false;
      var edits = Object.assign({}, S.edits), choices = S.revisions.filter(function (r) { return r.status === "published" && r.id !== item.id; });
      var initial = parameterReference(item);
      var reference = h("select.select", { "aria-label": "对比参照配置" }, h("option", { value: "" }, "选择已保存的配置"), choices.map(function (r) {
        return h("option", { value: r.id, selected: r.id === initial }, revisionLabel(r) + (r.kind === "default" ? " · 首次标准" : r.kind === "baseline" ? " · 标准记录 " + r.label.slice(1) : " · 机台记录 " + r.label.slice(1)));
      }));
      var only = h("input", { type: "checkbox", checked: true }), keyword = h("input.input", { type: "search", "aria-label": "搜索对比参数", placeholder: "参数名称或文件字段" });
      var body = h("div.col", { style: "gap:12px" });
      var modal = viewModal({ title: "对比参数", size: "xwide", focusTitle: true, body: body });
      function alive() { return context.readCurrent() && body.isConnected; }
      function load(start) {
        if (!reference.value) { data = null; error = null; draw(); return; }
        offset = start || 0; var request = ++seq; busy = true; error = null; draw();
        api.requestV3("POST", context.path + "/parameter-query", { body: { revision_id: item.id, reference_id: reference.value,
          edits: edits, q: keyword.value, filter: only.checked ? "different" : "all", offset: offset, limit: size } }).then(function (result) {
          if (!alive() || request !== seq) return; data = result; busy = false; draw();
        }, function (err) { if (alive() && request === seq) { error = err; busy = false; draw(); } });
      }
      reference.addEventListener("change", function () { seq++; load(0); });
      only.addEventListener("change", function () { load(0); });
      keyword.addEventListener("keydown", function (event) { if (event.key === "Enter") load(0); });
      function draw() {
        util.clear(body);
        body.appendChild(MC.field({ label: "参照配置", control: reference }));
        body.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" }, keyword, h("button.btn.btn-sm", { type: "button", onclick: function () { load(0); } }, "搜索"), h("label.row.small", { style: "gap:6px" }, only, "只看不同")));
        if (busy) body.appendChild(MC.stateBlock({ kind: "loading", title: "正在对比参数…" }));
        else if (error) body.appendChild(MC.errorBlock(error, function () { load(offset); }));
        else if (!reference.value) body.appendChild(MC.stateBlock({ kind: "empty", title: choices.length ? "选择参照配置后查看差异" : "还没有其他已保存的配置" }));
        else if (data) {
          body.appendChild(h("div.table-wrap.cv32-review-table", h("table.table", h("thead", h("tr", ["参数", "参照值", item.status === "draft" ? "当前草稿及编辑值" : "当前配置", "取值方式"].map(function (label) { return h("th", label); }))),
            h("tbody", (data.items || []).map(function (p) { return h("tr", h("td", p.name), h("td.mono", memberText(p.reference)), h("td.mono", memberText(p.member)),
              h("td", item.kind === "machine" ? { inherit: "使用标准值", override: "单独设置", pin: "保持此值" }[(p.member || {}).intent || "inherit"] : "版本标准")); })))));
          if (!data.total) body.appendChild(h("p.small.muted", only.checked ? "没有不同的参数" : "没有匹配的参数"));
          body.appendChild(pagerRow(data.total, offset, size, load));
        }
      }
      draw(); if (reference.value) load(0);
    }

    function upgradeDialog(ready) {
      var item = revision(); if (!item || item.kind !== "machine" || !canWrite() || S.savingValues) return;
      if (S.dirty && ready !== true) {
        viewConfirm({ title: "先暂存当前修改？", message: "比较将使用当前草稿，包括已修改的值和取值方式。", confirmText: "暂存并继续", cancelText: "继续编辑" }).then(function (ok) {
          if (ok) stashValues().then(function (saved) { if (saved) upgradeDialog(true); });
        }); return;
      }
      var stored = S.revisions.filter(function (r) { return r.machine_id === item.machine_id && r.status === "draft"; })[0];
      if (stored && stored.id !== item.id) {
        viewConfirm({ title: "此机台已有编辑草稿", message: "更新将继续此草稿，保留其中的参数修改。", confirmText: "继续草稿", cancelText: "取消" }).then(function (ok) {
          if (ok) chooseRevision(stored, "workbench").then(function () { upgradeDialog(true); });
        }); return;
      }
      var context = operationContext(), state = { plan: null, page: null, offset: 0, filter: "all", busy: false, error: null, requestId: null };
      var standards = (S.detail.releases || []).map(function (r) { return latestStandard(r.release_id); }).filter(Boolean);
      var machine = S.machines.filter(function (m) { return m.machine_id === item.machine_id; })[0];
      var saved = machine && S.revisions.filter(function (r) { return r.id === machine.head_id; })[0];
      var originalRelease = saved ? saved.release_id : item.release_id;
      var target = h("select.select", { "aria-label": "新的版本标准" }, standards.map(function (r) { return h("option", { value: r.id, selected: r.release_id === item.release_id }, "软件 " + labelOf(r.release_id) + " · " + (r.at ? util.formatTime(r.at) : "已保存")); }));
      var original = h("select.select", { "aria-label": "原来的版本标准" }, h("option", { value: "" }, "请选择原来采用的标准"), S.revisions.filter(function (r) { return r.kind !== "machine" && r.status === "published" && r.release_id === originalRelease; }).map(function (r) { return h("option", { value: r.id }, "软件 " + labelOf(r.release_id) + " · " + util.formatTime(r.at)); }));
      var body = h("div.col", { style: "gap:12px" }), footerText = h("span.small.muted", { role: "status", "aria-live": "polite" });
      var apply = h("button.btn.btn-primary", { type: "button", disabled: true, onclick: commit }, "继续编辑");
      var modal = viewModal({ title: "更新到其他标准 · " + item.machine_no, size: "xwide", focusTitle: true, body: body, footer: function (close) {
        return [footerText, h("span.spacer"), h("button.btn", { type: "button", onclick: close }, "取消"), apply];
      } });
      function alive() { return context.readCurrent() && body.isConnected; }
      function path(rest) { return context.path + "/merge-plans" + (rest || ""); }
      function rows() {
        state.busy = true; draw();
        return api.getV3(path("/" + enc(state.plan.plan_id) + "/rows?limit=30&offset=" + state.offset + "&filter=" + state.filter)).then(function (res) {
          if (!alive()) return; state.page = res; state.busy = false; draw();
        }, failed);
      }
      function failed(err) { if (alive()) { state.error = err; state.busy = false; draw(); } }
      function preview() {
        if (!alive() || !canWrite() || state.busy || !target.value) return;
        state.busy = true; state.error = null; state.page = null; state.requestId = null; state.offset = 0; draw();
        var payload = { machine_id: item.machine_id, ours_revision_id: item.id, theirs_revision_id: target.value };
        if (original.value) payload.base_revision_id = original.value;
        api.requestV3("POST", path(), { body: payload }).then(function (res) {
          if (!alive()) return; state.plan = res.plan; return rows();
        }, failed);
      }
      function decide(row, value) {
        if (!alive() || !canWrite()) return;
        state.busy = true; state.error = null; draw();
        api.requestV3("PATCH", path("/" + enc(state.plan.plan_id) + "/decisions"), { body: { row_ids: [row.parameter_id], decision: value } }).then(function () { if (alive()) rows(); }, failed);
      }
      function commit() {
        if (apply.disabled || !alive() || !canWrite()) return;
        state.busy = true; state.error = null; state.requestId = state.requestId || util.uid("upgrade"); draw();
        api.requestV3("POST", path("/" + enc(state.plan.plan_id) + "/commit"), { body: { request_id: state.requestId } }).then(function (res) {
          if (!context.readCurrent() || !body.isConnected) return;
          S.edits = {}; S.dirty = false; S.pendingSave = null; S.historyEvents = null; S.expectedMachine = item.machine_id; S.expectedStandard = null;
          S.selected = res.draft.draft_id; S.releaseId = res.draft.release_id; S.tab = "workbench"; modal.close();
          reloadDetail().then(function () { if (!disposed && context.path === cfgPath("")) routeSelection(true); });
          MC.toast.success("已更新草稿，请核对后保存配置");
        }, failed);
      }
      target.addEventListener("change", function () { state.plan = null; state.page = null; state.error = null; state.requestId = null; draw(); });
      function draw() {
        util.clear(body); target.disabled = state.busy || !!state.requestId; original.disabled = state.busy || !!state.requestId;
        body.appendChild(h("div.cv32-upgrade-source", MC.field({ label: "新的版本标准", control: target }), h("button.btn", { type: "button", disabled: !canWrite() || state.busy || !target.value || !!state.requestId, onclick: preview }, "比较变化")));
        if (state.error && state.error.detail && state.error.detail.field === "base_revision_id") body.appendChild(MC.field({ label: "原来的版本标准", control: original }));
        if (state.error) body.appendChild(MC.errorBlock(state.error, state.requestId ? commit : preview));
        if (state.busy) body.appendChild(MC.stateBlock({ kind: "loading", title: "正在核对配置…" }));
        else if (state.page) {
          body.appendChild(h("div.row", MC.segmented([{ value: "all", label: "全部参数" }, { value: "conflict", label: "需要处理" }], state.filter, function (v) { state.filter = v; state.offset = 0; rows(); }, "升级比较筛选"),
            h("span.small.muted", "原标准：软件 " + state.page.base.release_version + " · 当前" + (item.status === "draft" ? "草稿" : "配置"))));
          if (state.page.stale) body.appendChild(h("div.banner.banner-warning", "配置已被其他人修改，请重新比较。"));
          body.appendChild(h("div.table-wrap.cv32-review-table", h("table.table", h("thead", h("tr", ["参数", "原来的标准", "当前配置", "新的标准", "处理方式"].map(function (label) { return h("th", label); }))),
            h("tbody", (state.page.items || []).map(function (r) {
              var select = h("select.select", { "aria-label": r.name + "的处理方式", disabled: state.page.stale || !!state.requestId, onchange: function () { decide(r, select.value); } },
                r.decision === "pending" ? h("option", { value: "pending" }, "请选择") : null,
                r.allowed_decisions.map(function (value) { return h("option", { value: value, selected: value === r.decision }, value === "keep_local" ? "保留当前配置" : "使用新的标准"); }));
              return h("tr", h("td", r.name, r.definition_changed ? h("div.small.cv-warning", "参数定义有变化") : null), h("td.mono", memberText(r.base_member)), h("td.mono", memberText(r.ours_member)), h("td.mono", memberText(r.theirs_member)), h("td", select));
            })))));
          body.appendChild(pagerRow(state.page.total, state.offset, 30, function (offset) { state.offset = offset; rows(); }));
        }
        footerText.textContent = state.page ? state.page.pending ? state.page.pending + " 项需要选择处理方式" : "确认后更新草稿，不会自动保存配置" : "保留机台单独设置的值，冲突项目由您选择";
        apply.disabled = !canWrite() || state.busy || !state.page || state.page.stale || state.page.pending > 0;
      }
      draw();
    }

    function mergeDialog() {
      if (!S.cid) return;
      var branch = S.branch || { head_id: null, baselines: [] }, context = operationContext();
      var d0 = S.releaseId ? "d0-" + S.releaseId : "";
      var state = { plan: null, page: null, loading: false, error: null, filter: "conflict", custom: {} };
      var body = h("div.col", { style: "gap:12px" });
      var pendingText = h("span.xsmall.muted", { role: "status", "aria-live": "polite" }, "尚未预览");
      var commitBtn = h("button.btn.btn-primary", { type: "button", disabled: true }, "生成新草稿");
      var modal = viewModal({
        title: "比较并合并配置", size: "xwide", body: body,
        subtitle: "核对变化后生成编辑草稿，再通过保存配置完成。",
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
            S.expectedMachine = null; S.expectedStandard = null; S.selected = res.draft ? res.draft.draft_id : S.selected; S.tab = "workbench"; modal.close(); reloadDetail();
          }, function (err) {
            MC.toastError("生成草稿失败", err, err.code === "PLAN_STALE" ? { action: { label: "刷新", onClick: loadRows } } : null);
          });
        });
      });
      var choices = S.revisions.filter(function (r) { return r.status === "published"; });
      function revisionSelect(name, label, value, optional) {
        return h("select.select", { name: name, "aria-label": label }, optional ? h("option", { value: "" }, "未指定原来的标准") : null, choices.map(function (r) { return h("option", { value: r.id, selected: r.id === value }, revisionLabel(r)); }));
      }
      var baseInput = revisionSelect("base_revision_id", "原来的标准", revision() && revision().source || (branch.baselines && branch.baselines[0] || {}).baseline_id || "", true);
      var oursInput = revisionSelect("ours_revision_id", "当前配置", revision() && revision().status === "published" ? S.selected : branch.head_id || d0);
      var theirsInput = revisionSelect("theirs_revision_id", "新的标准", d0);
      [baseInput, oursInput, theirsInput].forEach(function (select) { select.addEventListener("change", function () { state.plan = null; state.page = null; state.custom = {}; refreshFooter(); }); });
      function render() {
        var hadFocus = document.activeElement && document.activeElement !== document.body;
        util.clear(body);
        body.appendChild(h("div.cv-merge-sources",
          MC.field({ label: "原来的标准", control: baseInput }), MC.field({ label: "当前配置", control: oursInput }),
          MC.field({ label: "新的标准", control: theirsInput }),
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
          body.appendChild(h("div.banner.banner-warning", icon("info"), h("div.banner-body", "参照的配置已变化，已选处理方式仍保留。请重新比较后继续。")));
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
          h("thead", h("tr", h("th", "参数"), h("th", "原来的标准"), h("th", "当前配置"), h("th", "新的标准"), h("th", "决议"))),
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
      configFormModal({ title: parameter ? "参数属性" : "手动新增参数", body: h("div.col", { style: "gap:12px" }, fields.name, fields.directory, fields.type, fields.unit, fields.description, h("label.row.small", { style: "gap:6px" }, adopt, "同步到当前准备版本的定义"), h("p.small.muted", "不改变已有参数值；已固化版本保留原定义快照。")), footer: function (close) {
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
        readonly ? h("p.small.muted", "此版本的文件字段已保存，只能查看。可在新软件版本中设置新的对应字段。") : h("p.small.muted", "选择对应字段后确认，不会读取或覆盖草稿值。INI 可填写 section/key 精确位置。"));
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
        viewModal({ title: "与上一次保存对比", body: rows.length ? h("ul.mini-list", rows) : h("div.muted", "前一版本无差异或没有前一版本。") });
      }, function (err) { MC.toastError("读取差异失败", err); });
    }

    function showHistory(r) {
      api.getV3(cfgPath("/parameters/" + enc(r.parameter_id) + "/history")).then(function (res) {
        var rows = (res.items || []).map(function (it) {
          return h("li", h("div.col", { style: "gap:4px" }, h("strong", it.label), h("span.mono.xsmall.muted", it.revision_id),
            it.previous_id ? h("span.xsmall.muted", "上一份配置：" + it.previous_id) : null),
            h("div.col", { style: "gap:4px" }, h("span.small.muted", { ok: "有值", unset: "未设", null: "null", removed: "已移除", disabled: "已停用", not_introduced: "未纳入" }[it.value.status] || it.value.status), h("strong.mono", memberText(it.value))));
        });
        viewModal({ title: "参数历程 · " + r.name, body: rows.length ? h("ul.cv-param-history", rows) : h("div.muted", "没有历史。") });
      }, function (err) { MC.toastError("读取历程失败", err); });
    }


    function exportRevisions(rel) {
      var current = revision(), saved = S.revisions.filter(function (r) { return r.id === (current && current.status === "published" ? current.id : S.lastSavedId); })[0];
      return saved ? [{ id: saved.id, label: (saved.machine_no || "软件 " + labelOf(saved.release_id)) + " · " + (saved.at ? util.formatTime(saved.at) : "已保存") }] : [];
    }

    function startExport() {
      var item = revision(); if (!item || S.savingValues) return;
      if (item.status === "published" && !S.dirty) { S.exportRevisionId = item.id; changeTab("export"); return; }
      var context = operationContext(), saved = S.lastSavedId;
      viewModal({ title: "选择要导出的配置", body: h("p", "当前有编辑草稿。请选择保存后导出，或使用上次保存的配置。"), footer: function (close) {
        return [h("button.btn", { type: "button", onclick: close }, "取消"),
          h("button.btn", { type: "button", disabled: !saved, onclick: function () {
            if (disposed || context.path !== cfgPath("") || S.selected !== item.id) return;
            close(); S.exportRevisionId = saved; S.exportPlan = null; S.exportArtifact = null; S.exportFromEdit = true; S.tab = "export"; render(); routeSelection(false);
          } }, "导出上次保存的配置"),
          h("button.btn.btn-primary", { type: "button", disabled: !editable(), onclick: function () {
            if (!context.current()) return; close(); reviewSave(function () { S.exportRevisionId = S.selected; S.exportFromEdit = false; changeTab("export"); });
          } }, "保存并继续导出")];
      } });
    }

    function exportView(rel) {
      if (!rel) return MC.stateBlock({ kind: "empty", title: "先创建软件版本" });
      var revisions = exportRevisions(rel);
      if (!S.exportRevisionId || !revisions.some(function (r) { return r.id === S.exportRevisionId; })) S.exportRevisionId = revisions[0] && revisions[0].id || "";
      if (S.exportFiles === null) {
        S.exportFiles = [];
        api.getV3(root("/file-selector")).then(function (res) { if (disposed) return; S.exportFiles = (res.items || []).filter(function (f) { return ["json", "xml", "ini"].indexOf(String(f.name || "").split(".").pop().toLowerCase()) >= 0; }); render(); }, function (err) { if (!disposed) { S.exportError = err; render(); } });
      }
      if (!S.exportFileId && S.exportFiles.length) S.exportFileId = S.exportFiles[0].version_id;
      var revSel = h("select.select", { "aria-label": "导出配置", disabled: S.exportBusy }, revisions.map(function (r) { return h("option", { value: r.id, selected: r.id === S.exportRevisionId }, r.label); }));
      revSel.addEventListener("change", function () { S.exportRevisionId = revSel.value; S.exportPlan = null; S.exportArtifact = null; var target = S.revisions.filter(function (r) { return r.id === revSel.value; })[0]; if (target) chooseRevision(target, "export"); else render(); });
      var fileSel = h("select.select", { "aria-label": "模板文件", name: "version_id", disabled: S.exportBusy }, h("option", { value: "" }, "请选择文件"), S.exportFiles.map(function (f) { return h("option", { value: f.version_id, selected: f.version_id === S.exportFileId }, f.name); }));
      fileSel.addEventListener("change", function () { S.exportFileId = fileSel.value; S.exportMappings = {}; S.exportPlan = null; S.exportArtifact = null; render(); });
      var confirm = h("input", { type: "checkbox", checked: S.exportConfirm, disabled: S.exportBusy, onchange: function () { S.exportConfirm = confirm.checked; invalidate(); } });
      var message = h("p.small.cv-warning", { role: "status" });
      function invalidate() { S.exportDirty = true; S.exportPlan = null; S.exportArtifact = null; message.textContent = "设置已修改，请重新预览。"; Array.prototype.forEach.call(el.querySelectorAll("[data-export-commit], [data-export-download]"), function (b) { b.disabled = true; }); }
      var exportRecord = S.revisions.filter(function (r) { return r.id === S.exportRevisionId; })[0];
      var exportReady = exportRecord && exportRecord.status === "published";
      var preview = h("button.btn.btn-primary", { type: "button", disabled: !exportReady || !S.exportFileId || S.exportBusy, onclick: function () {
        var scope = cfgPath(""), rev = S.exportRevisionId, version = S.exportFileId;
        S.exportBusy = true; S.exportPlan = null; S.exportArtifact = null; S.exportError = null; render();
        api.requestV3("POST", scope + "/export-plans", { body: { release_id: exportRecord.release_id, revision_id: rev, version_id: version, confirm_removed: !!S.exportConfirm, mappings: S.exportMappings || {} } }).then(function (res) {
          if (disposed || scope !== cfgPath("") || rev !== S.exportRevisionId || version !== S.exportFileId) return;
          S.exportPlan = res.plan; S.exportBusy = false; S.exportDirty = false; render();
        }, function (err) { if (!disposed && scope === cfgPath("")) { S.exportBusy = false; S.exportError = err; render(); } });
      } }, S.exportBusy ? "正在生成预览…" : "预览导出");
      var source = h("section.cv-export-source", h("div.cv-detail-head", h("h2", "导出设置")), h("div.cv-detail-body.col", { style: "gap:20px" },
        MC.field({ label: "导出模板", control: fileSel }), MC.field({ label: "导出对象", control: revSel }),
        MC.field({ label: "参数与字段", control: h("button.btn.btn-ghost", { type: "button", onclick: function () { viewModal({ title: "参数与文件字段", size: "xwide", body: mappingPanel || h("p", "没有可对应的参数") }); } }, "查看对应字段") }),
        S.exportFromEdit ? h("p.small", "正在导出上次保存的配置，编辑内容仍保留。") : null,
        h("label.row.small", { style: "gap:6px" }, confirm, "保留模板中已停用或移除的字段"), !exportReady ? h("p.small.cv-warning", "尚未保存配置，请先保存后导出。") : null, preview, h("details.cv-help", h("summary", "文件保留规则"), h("p.small.muted", "仅写入已确认的文件字段；未匹配字段保持原样。XML / INI 保留注释，JSON 会重新排版；生成新文件，不覆盖原文件。"))));
      var main = h("section.cv-export-preview", h("div.cv-detail-head", h("h2", S.exportPlan ? "导出文件预览" : "导出文件")));
      var content = h("div.cv-detail-body.col", { style: "gap:14px" }); main.appendChild(content);
      if (S.exportError) content.appendChild(MC.errorBlock(S.exportError, function () { S.exportError = null; S.exportFiles = null; render(); }));
      if (!S.exportFiles.length && !S.exportError) content.appendChild(MC.stateBlock({ kind: "empty", title: "没有 JSON/XML/INI 模板", desc: "先从左侧配置文件列表导入模板文件。" }));
      var plan = S.exportPlan, mapping = plan && plan.mapping || [];
      var parameters = mapping.length ? mapping : (S.paramPage && S.paramPage.items || []).slice(0, 50).map(function (p) { var binding = (S.bindings || []).filter(function (b) { return b.parameter_id === p.parameter_id; })[0]; return { parameter_id: p.parameter_id, name: p.name, locator: binding && binding.locator || "", value: S.draft && S.draft.values && S.draft.values[p.parameter_id] && S.draft.values[p.parameter_id].value }; });
      var mappingPanel = parameters.length ? h("div.table-wrap", h("table.table.cv-export-mapping", h("thead", h("tr", h("th", "参数"), h("th", "文件字段"), h("th", "输出值"))), h("tbody", parameters.map(function (p) {
        var input = h("input.input.input-sm.input-mono", { "aria-label": p.name + "的文件字段", placeholder: "填写精确路径；留空则不输出", disabled: !canWrite() || S.exportBusy, value: S.exportMappings && Object.prototype.hasOwnProperty.call(S.exportMappings, p.parameter_id) ? S.exportMappings[p.parameter_id] : p.locator || "", oninput: function () { if (!S.exportMappings) S.exportMappings = {}; S.exportMappings[p.parameter_id] = input.value; invalidate(); } });
        return h("tr", h("td", h("strong", p.name), h("div.mono.xsmall.muted", p.parameter_id)), h("td", h("div.row", { style: "gap:6px" }, input, h("button.btn.btn-sm", { type: "button", disabled: !canWrite() || S.exportBusy || !/\.(json|xml)$/i.test((S.exportFiles.filter(function (f) { return f.version_id === S.exportFileId; })[0] || {}).name || ""), onclick: function () { var f = S.exportFiles.filter(function (row) { return row.version_id === S.exportFileId; })[0]; if (f) sourceFieldPicker(f, function (path) { input.value = path; input.dispatchEvent(new Event("input")); }); } }, "选字段"))), h("td.mono", p.value_state === "null" ? "null" : p.value === "" ? '""' : p.value == null ? "未设" : String(p.value)));
      })))) : null;
      if (!plan && !S.exportError && S.exportFiles.length) content.appendChild(MC.stateBlock({ kind: "empty", title: "生成预览后下载", desc: "核对文件格式、参数和字段层级。" }));
      content.appendChild(message);
      if (plan) {
        content.appendChild(h("div.small", "已匹配 " + plan.applied + " 项 · 未输出 " + Math.max(0, (plan.mapping_total || plan.applied) - plan.applied) + " 项 · 其他模板字段保持原样"));
        if (plan.mapping_total > 50) content.appendChild(pagerRow(plan.mapping_total, plan.mapping_offset || 0, 50, function (offset) { api.getV3(cfgPath("/export-plans/" + enc(plan.plan_id)), { offset: offset, limit: 50 }).then(function (res) { if (S.exportPlan === plan) { S.exportPlan = res.plan; render(); } }, function (err) { MC.toastError("读取映射失败", err); }); }));
        content.appendChild(h("pre.cv-export-code.mono", plan.preview || ""));
        content.appendChild(h("span.small.muted", "文本预览最多显示 4000 字符；生成和下载使用完整文件。"));
        var commit = h("button.btn.btn-primary", { type: "button", "data-export-commit": "1", disabled: S.exportBusy || !plan.applied, onclick: function () {
          var scope = cfgPath(""); S.exportBusy = true; render();
          api.requestV3("POST", scope + "/export-plans/" + enc(plan.plan_id) + "/commit", { body: { preview_token: plan.preview_token } }).then(function (res) {
            if (disposed || scope !== cfgPath("") || S.exportPlan !== plan) return;
            S.exportArtifact = res.artifact;
            return api.download(root("/exports/" + enc(res.artifact.artifact_id) + "/download")).catch(function (err) { MC.toastError("下载失败，文件已生成，可重试下载", err); });
          }, function (err) { if (scope === cfgPath("") && !disposed) { S.exportError = err; MC.toastError("导出失败", err); } }).then(function () { if (!disposed && scope === cfgPath("")) { S.exportBusy = false; render(); } });
        } }, "下载文件"); if (!S.exportArtifact) main.querySelector(".cv-detail-head").appendChild(commit);
      }
      if (S.exportArtifact) {
        var art = S.exportArtifact;
        main.querySelector(".cv-detail-head").appendChild(h("button.btn.btn-primary", { type: "button", "data-export-download": "1", onclick: function () { api.download(root("/exports/" + enc(art.artifact_id) + "/download")).catch(function (err) { MC.toastError("下载失败，请重试", err); }); } }, icon("download"), "再次下载文件"));
      }
      return h("div.cv-export", h("div.cv-toolbar", h("button.btn.btn-ghost", { type: "button", onclick: function () { changeTab("workbench"); } }, icon("chevron-left"), "返回参数"), h("span.spacer"), h("span.small.muted", "使用已保存的配置")), h("div.cv-export-workflow", source, main));
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
      var labels = eventLabels;
      var events = (S.historyEvents || []).map(function (ev) {
        var source = ev.source || {}, actions = h("div.cv-toolbar", { style: "padding-bottom:0" });
        if (source.revision_id) {
          var r = S.revisions.filter(function (item) { return item.id === source.revision_id; })[0];
          if (r) actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { chooseRevision(r, "workbench"); } }, "查看配置"));
          if (canWrite()) actions.appendChild(h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () {
            var context = operationContext();
            api.requestV3("POST", context.path + "/restore-plans", { body: { revision_id: source.revision_id, request_id: util.uid("restore") } }).then(function (res) {
              if (!context.current()) return;
              S.expectedMachine = null; S.expectedStandard = null; S.selected = res.draft.draft_id; S.tab = "workbench"; S.historyEvents = null; reloadDetail(); MC.toast.success("已生成恢复草稿");
            }, function (err) { MC.toastError("恢复失败", err); });
          } }, "从此配置继续编辑"));
        }
        var record = S.revisions.filter(function (item) { return item.id === source.revision_id; })[0];
        return h("tr.cv-history-event", h("td", h("div.row", icon("history"), h("div", h("strong", labels[ev.type] || "配置操作"), h("div.small.muted", record ? revisionLabel(record) : S.detail.name)))),
          h("td", h("details.cv-help", h("summary", "操作详情"), h("pre.mono.small", JSON.stringify({ before: ev.before, after: ev.after, source: ev.source, request_id: ev.request_id, undo_of: ev.undo_of }, null, 2)))),
          h("td", h("strong", ev.principal || "—"), h("div.mono.small.muted", ev.ip || "—")),
          h("td", h("time", util.formatTime(ev.at))), h("td", actions));
      });
      return h("div.cv-history", h("div.cv-toolbar", h("span.small.muted", (S.historyTotal || 0) + " 条记录 · 最新优先"), h("span.spacer"), search, button, h("button.btn.btn-sm", { type: "button", onclick: function () { historySeq++; S.historyLoading = false; S.historyEvents = null; S.historyError = null; render(); } }, "刷新")),
        S.historyLoading ? MC.stateBlock({ kind: "loading", title: "读取事件…" }) : S.historyError ? MC.stateBlock({ kind: "error", title: "读取配置事件失败", desc: MC.errorText(S.historyError), actions: [h("button.btn", { type: "button", onclick: function () { S.historyError = null; S.historyEvents = null; render(); } }, "重试")] })
          : events.length ? h("div.cv-history-events.table-wrap", h("table.table.cv32-history-table", h("thead", h("tr", ["操作 / 对象", "变更内容", "操作人", "时间", "查看"].map(function (label) { return h("th", label); }))), h("tbody", events))) : MC.stateBlock({ kind: "empty", title: "没有配置事件", desc: "保存配置后，操作记录将显示在这里。" }),
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
      configFormModal({ title: "新建配置分组", body: h("div.col", { style: "gap:12px" }, F.code, F.name), footer: function (close) {
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
      var predecessor = h("select.select", h("option", { value: "" }, "不指定"), (S.detail.releases || []).map(function (r) { return h("option", { value: r.release_id }, r.version); }));
      var F = { version: MC.field({ label: "软件版本号", required: true, control: version }) };
      configFormModal({ title: "新建软件版本", body: h("div.col", { style: "gap:12px" }, F.version, h("details.cv-help", h("summary", "可选设置"), MC.field({ label: "上一个软件版本", control: predecessor, help: "用于版本关系图，不会自动复制参数。" }))), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            return api.requestV3("POST", cfgPath("/releases"), { body: { version: version.value.trim(), predecessor_id: predecessor.value || null, initial_mode: "separate" } }).then(function (res) { if (close(true) === false) return; MC.toast.success("已创建版本 " + res.release.version); S.expectedMachine = null; S.expectedStandard = res.release.release_id; S.releaseId = res.release.release_id; S.selected = "d0-" + S.releaseId; S.tab = "workbench"; S.objectRole = "standard"; reloadDetail().then(function () { if (!disposed && S.releaseId === res.release.release_id) routeSelection(true); }); }, function (err) { F.version.setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function machineDialog() {
      var no = h("input.input", { type: "text", placeholder: "例如 MC-001" });
      var bases = (S.detail.releases || []).map(function (r) { return latestStandard(r.release_id); }).filter(Boolean);
      var basis = h("select.select", { "aria-label": "机台的软件版本" }, bases.map(function (r) { return h("option", { value: r.id, selected: r.release_id === S.releaseId }, "软件 " + labelOf(r.release_id) + (r.at ? " · " + util.formatTime(r.at) : " · 已保存标准")); }));
      var F = { no: MC.field({ label: "机台编号", required: true, control: no }), basis: MC.field({ label: "软件版本", required: true, control: basis, help: "采用所选版本最新保存的标准参数。" }) };
      configFormModal({ title: "新建机台", body: h("div.col", { style: "gap:12px" }, F.no, F.basis), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            var source = bases.filter(function (r) { return r.id === basis.value; })[0];
            if (!source) { F.basis.setError("请先保存该软件版本的标准参数"); return; }
            return api.requestV3("POST", cfgPath("/machine-branches"), { body: { machine_no: no.value.trim(), release_id: source.release_id, base_kind: source.kind === "default" ? "default" : "baseline", base_revision_id: source.id } }).then(function (res) { if (close(true) === false) return; S.expectedMachine = res.machine.machine_id; S.expectedStandard = null; S.selected = res.machine.draft.draft_id; S.releaseId = source.release_id; S.tab = "workbench"; MC.toast.success("机台已创建，可以编辑并保存配置"); reloadDetail(); }, function (err) { (err.detail && err.detail.field === "base_revision_id" ? F.basis : F.no).setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    var unsub = [MC.bus.on("changed:settings", function () { load(S.cid); })];
    load(S.cid || undefined);
    return {
      el: el, title: "配置管理", icon: "list",
      openFile: function (name) {
        var request = ++fileOpenSeq;
        confirmDiscard(function () {
          var epoch = S.contextEpoch, scope = root("");
          function readPage(offset) { api.getV3(scope + "/config-files", { q: name, limit: 100, offset: offset }).then(function (res) {
            if (disposed || request !== fileOpenSeq || epoch !== S.contextEpoch || scope !== root("") || el.classList.contains("hidden")) return;
            var file = (res.items || []).filter(function (item) { return item.name === name; })[0];
            if (file) openManagedFile(file);
            else if (res.items && res.items.length && offset + res.items.length < res.total) readPage(offset + res.items.length);
            else MC.toast.warning("配置文件已不存在，请刷新文件列表。");
          }, function (err) { if (!disposed && request === fileOpenSeq && epoch === S.contextEpoch && scope === root("")) MC.toastError("读取配置文件失败", err); }); }
          readPage(0);
        });
      },
      update: function (next) {
        var nextSurface = next && (next.arg || (next.query || {}).surface) || (next && next.query && next.query.config ? "configs" : "files");
        if (["configs", "files", "records"].indexOf(nextSurface) < 0) nextSurface = "configs";
        var queryNow = next && next.query || {}, sync = S.internalRoute;
        S.internalRoute = null;
        var ownNavigation = sync && nextSurface === "configs" && queryNow.config === S.cid && sync.scope === cfgPath("") &&
          (queryNow.tab || "versions") === sync.tab && (queryNow.revision || queryNow.draft || null) === sync.selected;
        function applyRoute() {
          if (!ownNavigation) dialogs.slice().forEach(function (dialog) { dialog.close(); });
          if (nextSurface !== surface) { surface = nextSurface; catalogPage.seq++; catalogPage.loaded = ""; catalogPage.items = []; catalogPage.total = 0; catalogPage.query = ""; catalogPage.offset = 0; }
          var query = next && next.query || {}, changed = query.config && query.config !== S.cid;
          S.opened = !!query.config;
          if (changed) { S.contextEpoch++; clearConfigData(); S.cid = query.config; S.expectedMachine = query.machine || null; S.expectedStandard = query.standard || null; S.objectRole = query.role || null; S.selected = query.revision || query.draft || null; S.releaseId = query.release || query.standard || null; S.tab = query.tab || (query.machine || query.standard ? "workbench" : "versions"); load(S.cid); }
          else if (query.revision || query.draft || query.release) {
            var id = query.revision || query.draft || ("d0-" + query.release), target = S.revisions.filter(function (r) { return r.id === id; })[0];
            S.expectedMachine = query.machine || null; S.expectedStandard = query.standard || null;
            if (!validRecord(target)) { S.missingRevision = id; render(); return; }
            S.missingRevision = null;
            S.objectRole = query.role || null;
            if (target && target.id !== S.selected) chooseRevision(target, query.tab || "workbench"); else { S.tab = query.tab || "workbench"; render(); }
          } else { S.missingRevision = null; S.tab = query.tab || "versions"; render(); }
        }
        if (ownNavigation) applyRoute(); else confirmDiscard(applyRoute);
      },
      confirmLeave: function () { return new Promise(function (resolve) { confirmDiscard(function () { resolve(true); }, function () { resolve(false); }); }); },
      isDirty: function () { return S.dirty || S.savingValues || S.discarding || S.exportDirty || formDrafts.some(function (form) { return form.dirty; }); },
      destroy: function () { disposed = true; document.removeEventListener("keydown", focusParameterSearch); if (activeMenu && activeMenu.el.isConnected) MC.closePopover(); dialogs.slice().forEach(function (modal) { modal.close(); }); formDrafts = []; catalogPage.seq++; if (S.paramTree) S.paramTree.destroy(); unsub.forEach(function (f) { f(); }); },
    };
  };
})(window);
