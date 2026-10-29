/*
 * V3 配置管理工作台（M12-07 / INH-2142）。
 * 唯一配置域入口：固定配置 → 软件版本 → canonical D0 → Bn / 机台 rn。
 * 参数树/总览/导出/历史在 M13/M14 接入，先保留五个 tab 与错误/空态。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  function enc(v) { return encodeURIComponent(String(v)); }

  MC.views.configv3 = function (app) {
    var el = h("div.view");
    var S = {
      configs: [], cid: null, detail: null, graph: null, params: null, branch: null,
      machines: [], releaseId: null, tab: "versions", draft: null, loading: true, error: null,
      filter: "", overview: null, overviewMachine: "", overviewCompare: "",
      paramMode: "list", selectedParam: null, paramTree: null,
      exportFiles: null, exportFileId: "", exportRevisionId: "", exportPlan: null,
      exportArtifact: null, exportBusy: false, exportError: null,
      historyEvents: null, historyLoading: false, historyError: null, historyQ: "", dirty: false,
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

    function load(selectId) {
      S.loading = true;
      S.error = null;
      render();
      return api.getV3(root("/configs")).then(function (res) {
        S.configs = res.items || [];
        var next = selectId || S.cid || (S.configs[0] && S.configs[0].config_id);
        S.cid = next || null;
        return S.cid ? loadDetail() : null;
      }).then(function () {
        S.loading = false;
        render();
      }, function (err) {
        S.loading = false;
        S.error = err;
        render();
      });
    }

    function overviewQuery() {
      var q = {};
      if (S.overviewMachine) q.machines = S.overviewMachine;
      if (S.overviewCompare) q.compare_release_id = S.overviewCompare;
      return q;
    }
    function loadOverview() {
      if (!S.releaseId) { S.overview = null; return Promise.resolve(); }
      return api.getV3(cfgPath("/releases/" + enc(S.releaseId) + "/overview"), overviewQuery())
        .then(function (res) { S.overview = res; }, function () { S.overview = null; });
    }

    function loadDetail() {
      return api.getV3(cfgPath("")).then(function (res) {
        S.detail = res.config;
        var releases = (S.detail.releases || []).slice().sort(function (a, b) { return a.order - b.order; });
        if (!S.releaseId || !releases.some(function (r) { return r.release_id === S.releaseId; })) {
          S.releaseId = releases.length ? releases[releases.length - 1].release_id : null;
        }
        var jobs = [
          api.getV3(cfgPath("/graph")).catch(function () { return { nodes: [], edges: [] }; }),
          api.getV3(cfgPath("/parameters")).catch(function () { return { revision: 0, items: [] }; }),
          api.getV3(cfgPath("/branches/main")).catch(function () { return { head_id: null, baselines: [] }; }),
          api.getV3(cfgPath("/machines")).catch(function () { return { items: [] }; }),
          S.releaseId ? api.getV3(cfgPath("/drafts/" + enc(draftId()))).catch(function () { return null; }) : Promise.resolve(null),
          loadOverview(),
        ];
        return Promise.all(jobs).then(function (rows) {
          S.graph = rows[0];
          S.params = rows[1];
          S.branch = rows[2];
          S.machines = rows[3].items || [];
          S.draft = rows[4];
        });
      });
    }

    function reloadDetail() {
      return loadDetail().then(function () { render(); }, function (err) { MC.toastError("刷新失败", err); });
    }

    function render() {
      if (S.paramTree) { S.paramTree.destroy(); S.paramTree = null; }
      util.clear(el);
      el.appendChild(pageHead());
      if (S.loading) { el.appendChild(MC.stateBlock({ kind: "loading", title: "加载配置…" })); return; }
      if (S.error) { el.appendChild(MC.errorBlock(S.error, load)); return; }
      if (!canWrite()) {
        el.appendChild(h("div.banner.banner-warning", icon("info"), h("div.banner-body", "当前身份可查看配置，编辑需要管理员权限。所见数据仍按当前工作区与机型隔离。")));
      }
      el.appendChild(h("div.config-shell", configsPanel(), mainPanel()));
    }

    function pageHead() {
      var add = h("button.btn", { type: "button", disabled: !canWrite(), title: canWrite() ? null : "只有管理员可以新建配置", onclick: createConfigDialog }, icon("plus"), "新建固定配置");
      return h("div.view-header", h("div.view-title", h("h1", "配置管理"), h("div.view-sub", "固定配置、软件版本、默认 D0、基线 Bn 与机台 rn。这里只管理配置，不宣称已下发真实机台。")), add);
    }

    function configsPanel() {
      var search = h("input.input.input-sm", { type: "search", name: "config_filter", placeholder: "搜索配置名称 / 标识", value: S.filter });
      search.addEventListener("input", function () { S.filter = search.value.trim().toLowerCase(); render(); });
      var rows = S.configs.filter(function (c) {
        return !S.filter || (c.name + " " + c.code).toLowerCase().indexOf(S.filter) >= 0;
      }).map(function (c) {
        return h("button.config-item" + (c.config_id === S.cid ? ".is-active" : ""), {
          type: "button", onclick: function () {
            confirmDiscard(function () {
              S.cid = c.config_id; S.releaseId = null; S.loading = true; render();
              loadDetail().then(function () { S.loading = false; render(); });
            });
          },
        }, h("strong", c.name), h("span.mono.xsmall", c.code), h("span.badge", (c.release_count || 0) + " 版本"));
      });
      return h("aside.panel.configs", h("div.panel-header", h("h3", "固定配置"), h("span.badge", S.configs.length + " 项")),
        h("div.panel-body", h("div.col", { style: "gap:10px" }, search,
          rows.length ? h("div.col", { style: "gap:4px" }, rows) : MC.stateBlock({ kind: "empty", title: "还没有固定配置", desc: "新建一个固定配置后，添加第一个软件版本。" }))));
    }

    function changeTab(value) {
      if (S.tab === value) return;
      if (S.dirty && canWrite()) {
        MC.confirm({ title: "有未保存的值", message: "切换会丢失当前草稿中输入但未保存的修改。", confirmText: "放弃修改" }).then(function (ok) {
          if (!ok) return;
          S.dirty = false;
          S.tab = value;
          render();
        });
        return;
      }
      S.tab = value;
      render();
    }

    function confirmDiscard(then) {
      if (!S.dirty) { then(); return; }
      MC.confirm({ title: "有未保存的值", message: "离开会丢失当前草稿中输入但未保存的修改。", confirmText: "放弃修改" }).then(function (ok) {
        if (!ok) return;
        S.dirty = false;
        then();
      });
    }

    function mainPanel() {
      if (!S.detail) {
        return h("section.panel.config-main", h("div.panel-body", MC.stateBlock({ kind: "empty", icon: "list", title: "添加第一个软件版本", desc: "先建固定配置，再创建软件版本；每个版本有唯一的 canonical D0。", actions: canWrite() ? [h("button.btn.btn-primary", { type: "button", onclick: createConfigDialog }, "新建固定配置")] : null })));
      }
      var rel = release();
      var releaseSel = h("select.select", { "aria-label": "软件版本", name: "release_id" },
        (S.detail.releases || []).map(function (r) {
          return h("option", { value: r.release_id, selected: r.release_id === S.releaseId }, r.version + (r.default_revision_id ? " · D0" : " · 未冻结"));
        }));
      releaseSel.addEventListener("change", function () {
        var next = releaseSel.value;
        releaseSel.value = S.releaseId;
        confirmDiscard(function () { S.releaseId = next; render(); reloadDetail(); });
      });
      var addRel = h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: createReleaseDialog }, icon("plus"), "新建软件版本");
      var extract = h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), title: canWrite() ? "从 JSON/XML 提取参数名称与层级，不导入值" : "只有管理员可以提取参数", onclick: extractDialog }, "从文件提取参数");
      var mergeBtn = h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), title: canWrite() ? "比较 O/L/N 并生成新草稿" : "只有管理员可以合并", onclick: mergeDialog }, "合并配置");
      var tabs = MC.segmented([
        { value: "versions", label: "版本演进" },
        { value: "workbench", label: "参数工作台" },
        { value: "overview", label: "参数总览" },
        { value: "export", label: "模板导出" },
        { value: "history", label: "配置历史" },
      ], S.tab, changeTab, "配置管理视图");
      var body;
      if (S.tab === "versions") body = versionsView(rel);
      else if (S.tab === "workbench") body = workbenchView(rel);
      else if (S.tab === "overview") body = overviewView(rel);
      else if (S.tab === "export") body = exportView(rel);
      else if (S.tab === "history") body = historyView();
      else body = placeholder(S.tab);
      return h("section.panel.config-main",
        h("div.panel-header.config-title", h("div.col", { style: "gap:2px" }, h("h2", S.detail.name), h("span.xsmall.muted", "固定配置 / " + S.detail.code)), h("span.spacer"), releaseSel, extract, mergeBtn, addRel),
        h("div.panel-body", h("div.col", { style: "gap:14px" }, tabs, body)));
    }

    function versionsView(rel) {
      var nodes = (S.graph && S.graph.nodes || []).map(function (n) {
        return h("div.config-node", h("div.row", h("strong", n.version), h("span.badge", "order " + n.order),
          n.predecessor_id ? h("span.xsmall.muted", "父 " + labelOf(n.predecessor_id)) : h("span.badge", "首个版本"),
          n.d0_frozen ? h("span.badge.badge-success", "D0 已冻结") : h("span.badge.badge-warning", "D0 未冻结")));
      });
      var actions = h("div.row", { style: "gap:8px;flex-wrap:wrap" },
        h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite() || !rel || !!rel.default_revision_id, onclick: freezeD0 }, "冻结 canonical D0"),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite() || !rel || !rel.default_revision_id, onclick: publishNextBaseline }, "发布下一条基线"),
        h("button.btn.btn-sm", { type: "button", disabled: !canWrite() || !rel || !rel.default_revision_id, onclick: machineDialog }, "新建机台分支"));
      var branch = S.branch || { head_id: null, baselines: [] };
      var machines = (S.machines || []).map(function (m) {
        return h("li", h("span.mono", m.machine_no), h("span.xsmall.muted", m.published ? "已发布 " + (m.head_id || "") : "草稿 · 未发布"),
          m.published ? null : h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: function () { publishMachine(m); } }, "发布 r" + ((m.revisions || []).length + 1)));
      });
      return h("div.col", { style: "gap:14px" },
        h("div.banner.banner-info", icon("info"), h("div.banner-body", "发布只移动管理系统的 head，不会下发或核验真实机台。")),
        h("div.row", { style: "gap:8px;flex-wrap:wrap" }, actions),
        h("div.config-graph", nodes.length ? nodes : MC.stateBlock({ kind: "empty", title: "还没有软件版本" })),
        h("div.grid-2", h("div.panel", h("div.panel-header", h("h3", "主线")), h("div.panel-body", h("div.col", { style: "gap:6px" },
          h("div", "head：", h("span.mono", branch.head_id || "—")),
          (branch.baselines || []).length ? h("div.col", { style: "gap:2px" }, branch.baselines.map(function (b) { return h("div", b.label + " · " + b.baseline_id); })) : h("div.muted", "还没有基线"))))
        , h("div.panel", h("div.panel-header", h("h3", "机台")), h("div.panel-body", machines.length ? h("ul.mini-list", machines) : h("div.muted", "还没有机台分支"))))
      );
    }

    function labelOf(id) {
      var r = (S.detail.releases || []).filter(function (x) { return x.release_id === id; })[0];
      return r ? r.version : id;
    }

    function workbenchView(rel) {
      if (!rel) return MC.stateBlock({ kind: "empty", title: "先创建软件版本" });
      var values = (S.draft && S.draft.values) || {};
      var params = (S.params && S.params.items || []);
      var rows = params.map(function (p) {
        var cur = values[p.parameter_id] || { membership: "not_introduced", value_state: "unset", enabled: true };
        var input = h("input.input.input-sm", { type: "text", value: cur.value == null ? "" : cur.value, disabled: !canWrite() || !!rel.d0.frozen });
        input.addEventListener("focus", function () { S.selectedParam = p; renderParamDetail(detailHost); });
        input.addEventListener("input", function () { S.dirty = true; });
        return { p: p, input: input, cur: cur };
      });
      var save = h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite() || !!rel.d0.frozen, onclick: function () {
        var items = rows.map(function (r) {
          return { parameter_id: r.p.parameter_id, membership: "included", enabled: true, value_state: "present", value: r.input.value };
        });
        MC.withBusy(save, function () {
          return api.requestV3("PATCH", cfgPath("/drafts/" + enc(draftId()) + "/values"), {
            body: { request_id: "ui-" + Date.now(), expected_revision: S.draft ? S.draft.revision : 0, items: items },
          }).then(function () { S.dirty = false; MC.toast.success("草稿已保存"); reloadDetail(); }, function (err) { MC.toastError("保存失败", err, err.code === "REVISION_CONFLICT" ? { action: { label: "重新加载", onClick: function () { reloadDetail(); } } } : null); });
        });
      } }, "保存值");
      var detailHost = h("div");
      var mode = MC.segmented([
        { value: "list", label: "列表" }, { value: "tree", label: "参数树" },
      ], S.paramMode, function (v) { S.paramMode = v; render(); }, "参数视图");
      var listWrap = h("div.col", { style: "gap:8px" });
      var bodyRows = rows.length ? rows.map(function (r) {
        return h("tr", h("td.cell-strong", r.p.name), h("td.mono.xsmall", (r.p.directory || []).join(" / ") || "—"),
          h("td.xsmall", (r.p.type || "—") + (r.p.unit ? " · " + r.p.unit : "")), h("td", r.input));
      }) : h("tr", h("td", { colspan: 4 }, h("div.muted", "还没有参数；参数目录提取在 M13 接入。")));
      var list = h("div.table-wrap", h("table.table",
        h("thead", h("tr", h("th", "参数"), h("th", "目录"), h("th", "类型/单位"), h("th", "值"))),
        h("tbody", bodyRows)));
      var treeHost = h("div", { style: "height:420px;border:1px solid var(--c-border);border-radius:8px;overflow:hidden" });
      var search = h("input.input.input-sm", { type: "search", placeholder: "筛选参数名称 / 完整层级" });
      var expandAll = h("button.btn.btn-sm", { type: "button" }, "全部展开");
      var collapseAll = h("button.btn.btn-sm", { type: "button" }, "全部折叠");
      listWrap.append(h("div.row", { style: "gap:8px;flex-wrap:wrap" }, search, expandAll, collapseAll), S.paramMode === "list" ? list : treeHost, detailHost);
      search.addEventListener("input", function () {
        var kw = search.value.trim().toLowerCase();
        if (S.paramMode === "tree" && S.paramTree) S.paramTree.setFilter(kw, function () { return ""; });
        if (S.paramMode === "list") {
          [].slice.call(list.querySelectorAll("tbody tr")).forEach(function (tr) {
            tr.style.display = !kw || tr.textContent.toLowerCase().indexOf(kw) >= 0 ? "" : "none";
          });
        }
      });
      expandAll.addEventListener("click", function () { if (S.paramTree) S.paramTree.expandAll(); });
      collapseAll.addEventListener("click", function () { if (S.paramTree) S.paramTree.collapseAll(); });
      if (S.paramMode === "tree") {
        var doc = paramDoc(params, values);
        Promise.resolve().then(function () {
          if (!treeHost.isConnected || !MC.Tree) return;
          S.paramTree = new MC.Tree(treeHost, MC.doc.buildIndex(doc), {
            label: "参数目录",
            onSelect: function (i) {
              var p = doc.metaParams[i];
              if (p) { S.selectedParam = p; renderParamDetail(detailHost); }
            },
          });
          var sel = null;
          if (S.selectedParam) {
            Object.keys(doc.metaParams).forEach(function (k) { if (doc.metaParams[k].parameter_id === S.selectedParam.parameter_id) sel = +k; });
          }
          if (sel != null) S.paramTree.select(sel);
        });
      }
      renderParamDetail(detailHost);
      return h("div.col", { style: "gap:12px" },
        h("div.row", h("span.badge", rel.d0.frozen ? "D0 已冻结" : "D0 草稿"), h("span.xsmall.muted", "修订 " + (S.draft ? S.draft.revision : 0)), mode, h("span.spacer"), save),
        listWrap);
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
      var modal = MC.modal({
        title: "从配置文件提取参数",
        subtitle: "只提取名称与完整层级，不导入值；服务端保存全量计划，浏览器只按页取行。",
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
          return h("option", { value: f.file_id, selected: f.file_id === state.fileId }, f.name + " · " + f.version_id);
        }));
        sel.addEventListener("change", function () { state.fileId = sel.value; });
        var f = selectedFile();
        body.appendChild(h("div.row", { style: "gap:10px;flex-wrap:wrap" },
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
      var branch = S.branch || { head_id: null, baselines: [] };
      var d0 = S.releaseId ? "d0-" + S.releaseId : "";
      var state = { plan: null, page: null, loading: false, error: null, filter: "conflict", custom: {} };
      var body = h("div.col", { style: "gap:12px" });
      var pendingText = h("span.xsmall.muted", { role: "status", "aria-live": "polite" }, "尚未预览");
      var commitBtn = h("button.btn.btn-primary", { type: "button", disabled: true }, "生成新草稿");
      var modal = MC.modal({
        title: "预览合并（O / L / N）", size: "xwide", body: body,
        subtitle: "计划只读；提交只创建新草稿，不移动 head，也不发布。",
        footer: function (close) {
          return [pendingText, h("span.spacer"),
            h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭"), commitBtn];
        },
      });
      function cfgMerge(rest) { return cfgPath("/merge-plans" + (rest || "")); }
      function refreshFooter() {
        if (!state.page) { pendingText.textContent = "尚未预览"; commitBtn.disabled = true; return; }
        pendingText.textContent = state.page.stale ? "计划已过期，请重算" : state.page.pending ? "待决议 " + state.page.pending + " 行" : "冲突已全部决议";
        commitBtn.disabled = !canWrite() || state.page.stale || state.page.pending > 0;
      }
      function loadRows() {
        if (!state.plan) return;
        state.loading = true;
        api.getV3(cfgMerge("/" + enc(state.plan.plan_id) + "/rows?limit=100&filter=" + enc(state.filter))).then(function (res) {
          state.page = res; state.loading = false; render();
        }, function (err) { state.loading = false; state.error = err; render(); });
      }
      function preview() {
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
        if (commitBtn.disabled || !state.plan) return;
        var custom = {};
        (state.page.items || []).forEach(function (row) {
          if (row.decision === "custom" && state.custom[row.parameter_id] != null) {
            custom[row.parameter_id] = state.custom[row.parameter_id];
          }
        });
        MC.withBusy(commitBtn, function () {
          return api.requestV3("POST", cfgMerge("/" + enc(state.plan.plan_id) + "/commit"), { body: { custom_values: custom } }).then(function (res) {
            MC.toast.success("已生成新草稿", res.draft ? res.draft.draft_id : "");
            modal.close(); reloadDetail();
          }, function (err) {
            MC.toastError("生成草稿失败", err, err.code === "PLAN_STALE" ? { action: { label: "刷新", onClick: loadRows } } : null);
          });
        });
      });
      var baseInput = h("input.input.input-sm.input-mono", { name: "base_revision_id", value: branch.baselines && branch.baselines.length ? branch.baselines[0].baseline_id : "", placeholder: "基准 O（可留空则禁止一键合并）" });
      var oursInput = h("input.input.input-sm.input-mono", { name: "ours_revision_id", value: branch.head_id || d0, placeholder: "本地 L（Bn/rn）" });
      var theirsInput = h("input.input.input-sm.input-mono", { name: "theirs_revision_id", value: d0, placeholder: "目标 N（D0）" });
      function render() {
        var hadFocus = document.activeElement && document.activeElement !== document.body;
        util.clear(body);
        body.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          h("label.xsmall.muted", "O"), baseInput, h("label.xsmall.muted", "L"), oursInput,
          h("label.xsmall.muted", "N"), theirsInput,
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
            customInput = h("input.input.input-sm", { value: state.custom[row.parameter_id] || "" });
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
      var machineSel = h("select.select", { "aria-label": "机台列" }, h("option", { value: "" }, "不显示机台"),
        (S.machines || []).map(function (m) {
          return h("option", { value: m.machine_id, selected: S.overviewMachine === m.machine_id }, m.machine_no);
        }));
      machineSel.addEventListener("change", function () {
        S.overviewMachine = machineSel.value;
        loadOverview().then(render);
      });
      var compareSel = h("select.select", { "aria-label": "对比版本" }, h("option", { value: "" }, "不对比前版"),
        (S.detail.releases || []).filter(function (r) { return r.release_id !== S.releaseId; }).map(function (r) {
          return h("option", { value: r.release_id, selected: S.overviewCompare === r.release_id }, r.version);
        }));
      compareSel.addEventListener("change", function () {
        S.overviewCompare = compareSel.value;
        loadOverview().then(render);
      });
      if (!ov) return MC.stateBlock({ kind: "empty", title: "正在读取参数总览…" });
      var head = h("div.row", { style: "gap:8px;flex-wrap:wrap" }, machineSel, compareSel,
        h("button.btn.btn-sm", { type: "button", disabled: !rel.default_revision_id, onclick: showDiff }, "与前一修订对比"),
        h("span.xsmall.muted", ov.has_previous ? "" : "第一版无前一版本"));
      if (!(ov.columns || []).length) {
        return h("div.col", { style: "gap:12px" }, head,
          MC.stateBlock({ kind: "empty", title: "该版本还没有可显示的 D0/Bn/机台快照", desc: "先冻结 D0、发布基线或本版机台 rn。" }));
      }
      var labels = { ok: null, unset: "未设", null: "null", removed: "已移除", disabled: "已停用", not_introduced: "未纳入" };
      var header = h("tr", h("th", "参数"), h("th", "目录 / 类型"), (ov.columns || []).map(function (c) { return h("th", c.label); }));
      var bodyRows = (ov.rows || []).map(function (r) {
        return h("tr", h("td.cell-strong", r.name, h("button.link-btn.small", { type: "button", style: "margin-left:8px", onclick: function () { showHistory(r); } }, "历程")), h("td.xsmall.mono", (r.directory || []).join(" / ") + (r.type ? " · " + r.type : "")),
          (ov.columns || []).map(function (c) {
            var cell = r.cells[c.column_id] || {};
            var shown = cell.status === "ok" || cell.status === "disabled" ? (cell.value == null ? "—" : String(cell.value)) : (labels[cell.status] || cell.status || "—");
            return h("td", { title: (cell.revision_id || "") + " · " + (cell.status || "") }, shown);
          }));
      });
      return h("div.col", { style: "gap:12px" }, head,
        h("div.table-wrap", h("table.table", h("thead", header), h("tbody", bodyRows))),
        h("p.xsmall.muted", "每格绑定来源 revision；未形成/未纳入/未设/null/已移除/已停用分开显示。"));
    }

    function showDiff() {
      var rid = "d0-" + S.releaseId;
      api.getV3(cfgPath("/revisions/" + enc(rid) + "/diff")).then(function (res) {
        var rows = (res.rows || []).map(function (r) {
          return h("li", h("strong", r.name_after || r.name_before || r.parameter_id), " · ",
            r.categories.join(" / "),
            h("span.xsmall.muted", "  " + (r.before && r.before.status || "—") + " → " + (r.after && r.after.status || "—")));
        });
        MC.modal({ title: "与前一修订对比", body: rows.length ? h("ul.mini-list", rows) : h("div.muted", "前一版本无差异或没有前一版本。") });
      }, function (err) { MC.toastError("读取差异失败", err); });
    }

    function showHistory(r) {
      api.getV3(cfgPath("/parameters/" + enc(r.parameter_id) + "/history")).then(function (res) {
        var rows = (res.items || []).map(function (it) {
          return h("li", h("span.mono", it.revision_id), " · ", it.label,
            h("span.badge", it.value.status), it.value.value != null ? h("span.mono", " = " + it.value.value) : null,
            it.previous_id ? h("span.xsmall.muted", "  前驱 " + it.previous_id) : null);
        });
        MC.modal({ title: "参数历程 · " + r.name, body: rows.length ? h("ul.mini-list", rows) : h("div.muted", "没有历史。") });
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
      if (!S.exportRevisionId || !revisions.some(function (row) { return row.id === S.exportRevisionId; })) {
        S.exportRevisionId = revisions[0] && revisions[0].id;
      }
      if (S.exportFiles === null) {
        S.exportFiles = [];
        S.exportError = null;
        api.getV3(root("/file-selector")).then(function (res) {
          S.exportFiles = (res.items || []).filter(function (f) {
            return ["json", "xml", "ini"].indexOf(String(f.name || "").split(".").pop().toLowerCase()) >= 0;
          });
          render();
        }, function (err) { S.exportError = err; render(); });
      }
      var revSel = h("select.select", { "aria-label": "导出修订" }, revisions.map(function (row) {
        return h("option", { value: row.id, selected: row.id === S.exportRevisionId }, row.label);
      }));
      revSel.addEventListener("change", function () { S.exportRevisionId = revSel.value; S.exportPlan = null; S.exportArtifact = null; render(); });
      if (!S.exportFileId && S.exportFiles && S.exportFiles.length) S.exportFileId = S.exportFiles[0].version_id;
      var fileSel = h("select.select", { "aria-label": "模板文件", name: "version_id" },
        (S.exportFiles || []).map(function (f) {
          return h("option", { value: f.version_id, selected: f.version_id === S.exportFileId },
            f.name + " · " + f.version_id);
        }));
      fileSel.addEventListener("change", function () { S.exportFileId = fileSel.value; S.exportPlan = null; S.exportArtifact = null; });
      var confirm = h("input", { type: "checkbox", name: "confirm_removed" });
      var previewBtn = h("button.btn.btn-primary", { type: "button", disabled: !canWrite() || !S.exportFileId || S.exportBusy, onclick: function () {
        S.exportBusy = true; S.exportPlan = null; S.exportArtifact = null; render();
        api.requestV3("POST", cfgPath("/export-plans"), { body: {
          release_id: S.releaseId, revision_id: revSel.value, version_id: fileSel.value,
          confirm_removed: confirm.checked,
        } }).then(function (res) { S.exportPlan = res.plan; S.exportBusy = false; render(); },
          function (err) { S.exportBusy = false; render(); MC.toastError("导出预览失败", err); });
      } }, "预览导出");
      var body = h("div.col", { style: "gap:12px" },
        h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          h("label.xsmall.muted", "配置修订"), revSel,
          h("label.xsmall.muted", "模板文件"), fileSel,
          h("label.xsmall.row", { style: "gap:4px" }, confirm, "确认停用/移除字段"),
          previewBtn));
      if (S.exportError) {
        body.appendChild(MC.stateBlock({ kind: "error", title: "读取模板文件失败", desc: MC.errorText(S.exportError),
          actions: [h("button.btn.btn-sm", { type: "button", onclick: function () { S.exportFiles = null; S.exportError = null; render(); } }, "重试")] }));
      } else if (!(S.exportFiles || []).length) {
        body.appendChild(MC.stateBlock({ kind: "empty", title: "没有 JSON/XML/INI 模板", desc: "先在左下文件列表上传模板。" }));
      }
      if (S.exportPlan) {
        var plan = S.exportPlan;
        body.appendChild(h("div.banner.banner-info", icon("info"), h("div.banner-body",
          "已应用 " + plan.applied + " 个参数；digest " + String(plan.digest).slice(0, 12) + "…；停用/移除项 " + (plan.blocked || []).length + "。")));
        body.appendChild(h("pre.mono.xsmall", { style: "max-height:260px;overflow:auto;white-space:pre-wrap" }, plan.preview || ""));
        var commit = h("button.btn.btn-primary", { type: "button", disabled: S.exportBusy, onclick: function () {
          S.exportBusy = true; render();
          api.requestV3("POST", cfgPath("/export-plans/" + enc(plan.plan_id) + "/commit"), { body: { preview_token: plan.preview_token } })
            .then(function (res) { S.exportArtifact = res.artifact; S.exportBusy = false; render(); },
              function (err) { S.exportBusy = false; render(); MC.toastError("生成导出文件失败", err); });
        } }, "生成不可变文件");
        body.appendChild(h("div.row", { style: "gap:8px" }, commit));
      }
      if (S.exportArtifact) {
        var art = S.exportArtifact;
        var download = h("button.btn.btn-primary", { type: "button", onclick: function () {
          api.download("/api/v3/workspaces/" + enc(MC.workspaces.scopeId()) + "/profiles/" + enc(app.pid) + "/exports/" + enc(art.artifact_id) + "/download");
        } }, "下载 " + art.filename);
        body.appendChild(h("div.row", { style: "gap:8px" }, download,
          h("span.xsmall.muted", "仅表示内部导出，不代表真实机台已部署")));
      }
      return h("div.col", { style: "gap:12px" }, body);
    }

    function historyView() {
      if (!S.cid) return MC.stateBlock({ kind: "empty", title: "先选择配置" });
      if (S.historyEvents === null && !S.historyLoading && !S.historyError) {
        S.historyLoading = true;
        S.historyError = null;
        api.getV3(cfgPath("/events") + (S.historyQ ? "?q=" + enc(S.historyQ) : "")).then(function (res) {
          S.historyEvents = res.items || []; S.historyLoading = false; render();
        }, function (err) { S.historyLoading = false; S.historyError = err; render(); });
      }
      var q = h("input.input.input-sm", { type: "search", name: "q", placeholder: "搜索事件/参数/操作人", value: S.historyQ });
      var search = h("button.btn.btn-sm", { type: "button", onclick: function () {
        S.historyQ = q.value.trim(); S.historyEvents = null; render();
      } }, "搜索");
      q.addEventListener("keydown", function (e) { if (e.key === "Enter") search.click(); });
      var rows = (S.historyEvents || []).slice(0, 100).map(function (ev) {
        var restore = null;
        var source = ev.source || {};
        if (canWrite() && source.revision_id) {
          restore = h("button.btn.btn-sm", { type: "button", onclick: function () {
            api.requestV3("POST", cfgPath("/restore-plans"), { body: {
              revision_id: source.revision_id, request_id: "ui-restore-" + Date.now(),
            } }).then(function () { MC.toast.success("已生成恢复草稿"); reloadDetail(); },
              function (err) { MC.toastError("恢复失败", err); });
          } }, "从该修订恢复");
        }
        return h("tr",
          h("td.xsmall", ev.at), h("td", h("span.badge", ev.type)),
          h("td", ev.principal || "—"), h("td.mono.xsmall", ev.ip || "—"),
          h("td.xsmall", ev.request_id || ev.undo_of || ""), h("td", restore));
      });
      return h("div.col", { style: "gap:10px" },
        h("div.row", { style: "gap:8px" }, q, search,
          h("span.spacer"), h("button.btn.btn-sm", { type: "button", onclick: function () { S.historyEvents = null; render(); } }, "刷新")),
        S.historyLoading ? MC.stateBlock({ kind: "loading", title: "读取事件…" })
          : S.historyError ? MC.stateBlock({ kind: "error", title: "读取配置事件失败", desc: MC.errorText(S.historyError),
            actions: [h("button.btn.btn-sm", { type: "button", onclick: function () { S.historyEvents = null; S.historyError = null; render(); } }, "重试")] })
          : (rows.length ? h("div.table-wrap", h("table.table",
            h("thead", h("tr", h("th", "时间"), h("th", "类型"), h("th", "操作人"), h("th", "IP"), h("th", "请求/撤销"), h("th", ""))),
            h("tbody", rows))) : MC.stateBlock({ kind: "empty", title: "没有配置事件", desc: "提交提取/合并/导入/导出/恢复后会在这里出现。" })));
    }

    function placeholder(tab) {
      var text = { overview: "参数总览（D0/Bn/机台矩阵）在 M13 接入。", export: "模板导出步骤在 M14 接入。", history: "配置历史与撤销在 M14 接入。" }[tab] || "";
      return MC.stateBlock({ kind: "empty", title: "该分段尚未接入", desc: text });
    }

    function createConfigDialog() {
      var code = h("input.input.input-mono", { type: "text", placeholder: "MC-001" });
      var name = h("input.input", { type: "text", placeholder: "例如 一号机配置" });
      var F = { code: MC.field({ label: "配置标识", required: true, control: code }), name: MC.field({ label: "配置名称", required: true, control: name }) };
      MC.modal({ title: "新建固定配置", body: h("div.col", { style: "gap:12px" }, F.code, F.name), footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            return api.requestV3("POST", root("/configs"), { body: { code: code.value.trim(), name: name.value.trim() } }).then(function (res) { close(); MC.toast.success("已创建 " + res.config.name); S.cid = res.config.config_id; S.releaseId = null; load(S.cid); }, function (err) { (err.detail && err.detail.field === "code" ? F.code : F.name).setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function createReleaseDialog() {
      var version = h("input.input.input-mono", { type: "text", placeholder: "例如 690" });
      var F = { version: MC.field({ label: "软件版本号", required: true, control: version }) };
      MC.modal({ title: "新建软件版本", body: F.version, footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            return api.requestV3("POST", cfgPath("/releases"), { body: { version: version.value.trim() } }).then(function (res) { close(); MC.toast.success("已创建版本 " + res.release.version); S.releaseId = res.release.release_id; reloadDetail(); }, function (err) { F.version.setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function freezeD0() {
      MC.confirm({ title: "冻结 canonical D0", message: "冻结后该版本的默认值不可再修改；每个版本只能冻结一次。", confirmText: "冻结", danger: true }).then(function (ok) {
        if (!ok) return;
        api.requestV3("POST", cfgPath("/drafts/" + enc(draftId()) + "/freeze"), { body: { request_id: "ui-freeze-" + Date.now() } }).then(function () { MC.toast.success("D0 已冻结"); reloadDetail(); }, function (err) { MC.toastError("冻结失败", err); });
      });
    }

    function publishNextBaseline() {
      if (!canWrite()) return;
      api.requestV3("POST", cfgPath("/releases/" + enc(S.releaseId) + "/baseline-drafts"), { body: {} }).then(function (res) {
        var did = res.draft.draft_id;
        return api.requestV3("POST", cfgPath("/baseline-drafts/" + enc(did) + "/publish"), { body: { request_id: "ui-b-" + Date.now(), expected_revision: 0 } });
      }).then(function (res) { MC.toast.success("已发布 " + res.baseline.label); reloadDetail(); }, function (err) { MC.toastError("发布基线失败", err); });
    }

    function machineDialog() {
      var no = h("input.input", { type: "text", placeholder: "例如 MC-001" });
      var F = { no: MC.field({ label: "机台编号", required: true, control: no, help: "机台是管理配置，不代表已下发真实设备" }) };
      MC.modal({ title: "新建机台分支", body: F.no, footer: function (close) {
        var save = h("button.btn.btn-primary", { type: "button" }, "创建");
        save.addEventListener("click", function () {
          MC.withBusy(save, function () {
            return api.requestV3("POST", cfgPath("/machine-branches"), { body: { machine_no: no.value.trim(), release_id: S.releaseId, base_kind: "default" } }).then(function () { close(); MC.toast.success("机台草稿已创建，发布后才会得到 r1"); reloadDetail(); }, function (err) { F.no.setError(MC.errorText(err)); });
          });
        });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
      } });
    }

    function publishMachine(m) {
      api.requestV3("POST", cfgPath("/machines/" + enc(m.machine_id) + "/publish"), { body: { expected_revision: 0 } }).then(function () { MC.toast.success("机台已发布"); reloadDetail(); }, function (err) { MC.toastError("机台发布失败", err); });
    }

    var unsub = [MC.bus.on("changed:settings", function () { load(S.cid); })];
    load();
    return {
      el: el, title: "配置管理", icon: "list",
      update: function () {},
      destroy: function () { if (S.paramTree) S.paramTree.destroy(); unsub.forEach(function (f) { f(); }); },
    };
  };
})(window);
