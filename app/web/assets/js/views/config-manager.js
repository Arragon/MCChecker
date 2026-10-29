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
      var search = h("input.input.input-sm", { type: "search", placeholder: "搜索配置名称 / 标识", value: S.filter });
      search.addEventListener("input", function () { S.filter = search.value.trim().toLowerCase(); render(); });
      var rows = S.configs.filter(function (c) {
        return !S.filter || (c.name + " " + c.code).toLowerCase().indexOf(S.filter) >= 0;
      }).map(function (c) {
        return h("button.config-item" + (c.config_id === S.cid ? ".is-active" : ""), {
          type: "button", onclick: function () { S.cid = c.config_id; S.releaseId = null; S.loading = true; render(); loadDetail().then(function () { S.loading = false; render(); }); },
        }, h("strong", c.name), h("span.mono.xsmall", c.code), h("span.badge", (c.release_count || 0) + " 版本"));
      });
      return h("aside.panel.configs", h("div.panel-header", h("h3", "固定配置"), h("span.badge", S.configs.length + " 项")),
        h("div.panel-body", h("div.col", { style: "gap:10px" }, search,
          rows.length ? h("div.col", { style: "gap:4px" }, rows) : MC.stateBlock({ kind: "empty", title: "还没有固定配置", desc: "新建一个固定配置后，添加第一个软件版本。" }))));
    }

    function mainPanel() {
      if (!S.detail) {
        return h("section.panel.config-main", h("div.panel-body", MC.stateBlock({ kind: "empty", icon: "list", title: "添加第一个软件版本", desc: "先建固定配置，再创建软件版本；每个版本有唯一的 canonical D0。", actions: canWrite() ? [h("button.btn.btn-primary", { type: "button", onclick: createConfigDialog }, "新建固定配置")] : null })));
      }
      var rel = release();
      var releaseSel = h("select.select", { "aria-label": "软件版本" },
        (S.detail.releases || []).map(function (r) {
          return h("option", { value: r.release_id, selected: r.release_id === S.releaseId }, r.version + (r.default_revision_id ? " · D0" : " · 未冻结"));
        }));
      releaseSel.addEventListener("change", function () { S.releaseId = releaseSel.value; S.tab = S.tab; render(); reloadDetail(); });
      var addRel = h("button.btn.btn-sm", { type: "button", disabled: !canWrite(), onclick: createReleaseDialog }, icon("plus"), "新建软件版本");
      var tabs = MC.segmented([
        { value: "versions", label: "版本演进" },
        { value: "workbench", label: "参数工作台" },
        { value: "overview", label: "参数总览" },
        { value: "export", label: "模板导出" },
        { value: "history", label: "配置历史" },
      ], S.tab, function (v) { S.tab = v; render(); }, "配置管理视图");
      var body;
      if (S.tab === "versions") body = versionsView(rel);
      else if (S.tab === "workbench") body = workbenchView(rel);
      else if (S.tab === "overview") body = overviewView(rel);
      else body = placeholder(S.tab);
      return h("section.panel.config-main",
        h("div.panel-header.config-title", h("div.col", { style: "gap:2px" }, h("h2", S.detail.name), h("span.xsmall.muted", "固定配置 / " + S.detail.code)), h("span.spacer"), releaseSel, addRel),
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
      var rows = (S.params && S.params.items || []).map(function (p) {
        var cur = values[p.parameter_id] || {};
        var input = h("input.input.input-sm", { type: "text", value: cur.value == null ? "" : cur.value, disabled: !canWrite() || !!rel.d0.frozen });
        return { p: p, input: input, cur: cur };
      });
      var save = h("button.btn.btn-sm.btn-primary", { type: "button", disabled: !canWrite() || !!rel.d0.frozen, onclick: function () {
        var items = rows.map(function (r) {
          return { parameter_id: r.p.parameter_id, membership: "included", enabled: true, value_state: "present", value: r.input.value };
        });
        MC.withBusy(save, function () {
          return api.requestV3("PATCH", cfgPath("/drafts/" + enc(draftId()) + "/values"), {
            body: { request_id: "ui-" + Date.now(), expected_revision: S.draft ? S.draft.revision : 0, items: items },
          }).then(function () { MC.toast.success("草稿已保存"); reloadDetail(); }, function (err) { MC.toastError("保存失败", err, err.code === "REVISION_CONFLICT" ? { action: { label: "重新加载", onClick: function () { reloadDetail(); } } } : null); });
        });
      } }, "保存值");
      var bodyRows = rows.length ? rows.map(function (r) {
        return h("tr", h("td.cell-strong", r.p.name), h("td.mono.xsmall", (r.p.directory || []).join(" / ") || "—"),
          h("td.xsmall", (r.p.type || "—") + (r.p.unit ? " · " + r.p.unit : "")), h("td", r.input));
      }) : h("tr", h("td", { colspan: 4 }, h("div.muted", "还没有参数；参数目录提取在 M13 接入。")));
      return h("div.col", { style: "gap:12px" },
        h("div.row", h("span.badge", rel.d0.frozen ? "D0 已冻结" : "D0 草稿"), h("span.xsmall.muted", "修订 " + (S.draft ? S.draft.revision : 0)), h("span.spacer"), save),
        h("div.table-wrap", h("table.table",
          h("thead", h("tr", h("th", "参数"), h("th", "目录"), h("th", "类型/单位"), h("th", "值"))),
          h("tbody", bodyRows))));
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
        h("span.xsmall.muted", ov.has_previous ? "" : "第一版无前一版本"));
      if (!(ov.columns || []).length) {
        return h("div.col", { style: "gap:12px" }, head,
          MC.stateBlock({ kind: "empty", title: "该版本还没有可显示的 D0/Bn/机台快照", desc: "先冻结 D0、发布基线或本版机台 rn。" }));
      }
      var labels = { ok: null, unset: "未设", null: "null", removed: "已移除", disabled: "已停用", not_introduced: "未纳入" };
      var header = h("tr", h("th", "参数"), h("th", "目录 / 类型"), (ov.columns || []).map(function (c) { return h("th", c.label); }));
      var bodyRows = (ov.rows || []).map(function (r) {
        return h("tr", h("td.cell-strong", r.name), h("td.xsmall.mono", (r.directory || []).join(" / ") + (r.type ? " · " + r.type : "")),
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
      destroy: function () { unsub.forEach(function (f) { f(); }); },
    };
  };
})(window);
