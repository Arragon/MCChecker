/*
 * 外部工具：侧栏按钮打开的一页。链接以紧凑网格铺开，点击在新窗口打开。
 * 部署者可在本页增删改；设置 · 工具菜单仍保留同一份数据。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  function fieldError(fields, err) {
    var f = err.detail && err.detail.field;
    if (f && fields[f]) fields[f].setError(MC.errorText(err));
    else MC.toastError("保存失败", err);
  }

  MC.views.tools = function (app) {
    var items = [];
    var query = "";
    var el = h("div.tools-view");
    var header = h("div.view-header");
    var body = h("div.view-body.tools-body");
    el.append(header, body);
    var unsub = MC.bus.on("tools", function (list) {
      if (!list) return;
      items = list;
      render();
    });

    function load() {
      util.replace(body, MC.stateBlock({ kind: "loading" }));
      api.get(app.p("/tools")).then(function (res) {
        items = res.items || [];
        render();
      }, function (err) {
        util.replace(body, MC.errorBlock(err, load));
      });
    }

    function filtered() {
      var q = query.trim().toLowerCase();
      if (!q) return items;
      return items.filter(function (t) {
        return (t.name || "").toLowerCase().indexOf(q) >= 0
          || (t.description || "").toLowerCase().indexOf(q) >= 0
          || (t.url || "").toLowerCase().indexOf(q) >= 0;
      });
    }

    function renderHeader() {
      var ro = !app.can("deployer");
      var filter = h("input.input.input-sm.tools-filter", {
        type: "search",
        placeholder: "筛选名称、说明或地址",
        "aria-label": "筛选外部工具",
        value: query,
      });
      filter.addEventListener("input", function () {
        query = filter.value;
        renderGrid();
      });
      var add = ro ? null : h("button.btn.btn-primary", { type: "button", onclick: function () { toolDialog(null); } }, icon("plus"), "添加");
      util.replace(
        header,
        h("div.view-title", h("h1", "外部工具"), h("div.view-sub", "共 " + items.length + " 个链接，点击后在新窗口打开。")),
        filter,
        add
      );
    }

    function renderGrid() {
      var ro = !app.can("deployer");
      var list = filtered();
      if (!items.length) {
        util.replace(body, MC.stateBlock({
          kind: "empty",
          icon: "tool",
          title: "暂无外部工具",
          desc: ro ? "由管理员添加后，此处将列出可打开的链接。" : "请通过「添加」登记常用的 http 或 https 地址。",
        }));
        return;
      }
      if (!list.length) {
        util.replace(body, MC.stateBlock({ kind: "empty", icon: "search", title: "没有匹配的工具", desc: "请调整名称或地址后重新筛选。" }));
        return;
      }
      util.replace(body, h("div.tools-grid", { role: "list" }, list.map(function (t) { return tile(t, ro); })));
    }

    function render() {
      renderHeader();
      renderGrid();
    }

    function tile(t, ro) {
      var href = util.safeUrl(t.url);
      var open = href
        ? h("a.tool-open", { href: href, target: "_blank", rel: "noopener noreferrer" }, icon("external"), h("span.tool-name", t.name))
        : h("button.tool-open", { type: "button", onclick: function () { MC.toast.warning("该工具地址无效", "仅支持 http/https 链接"); } }, icon("external"), h("span.tool-name", t.name));
      var note = h("p.tool-desc", t.description ? t.description : h("span.muted", "无备注"));
      var address = href
        ? h("a.tool-url", { href: href, target: "_blank", rel: "noopener noreferrer" }, t.url)
        : h("p.tool-url.is-invalid", t.url || "地址无效");
      var ops = ro ? null : h(
        "div.tool-ops",
        h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "编辑 " + t.name, title: "编辑", onclick: function () { toolDialog(t); } }, icon("edit")),
        h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "删除 " + t.name, title: "删除", onclick: function () { removeTool(t); } }, icon("trash"))
      );
      return h("article.tool-tile", { role: "listitem" }, h("div.tool-tile-head", open, ops), note, address);
    }

    function toolDialog(t) {
      var name = h("input.input", { type: "text", value: t ? t.name : "" });
      var desc = h("input.input", { type: "text", value: t ? t.description : "" });
      var url = h("input.input.input-mono", { type: "url", value: t ? t.url : "", placeholder: "填写访问地址" });
      var F = {
        name: MC.field({ label: "名称", required: true, control: name }),
        description: MC.field({ label: "说明", control: desc }),
        url: MC.field({ label: "访问地址", required: true, control: url, help: "仅支持 http / https，将在新窗口打开" }),
      };
      MC.modal({
        title: t ? "编辑工具" : "添加工具",
        body: h("div.col", { style: "gap:12px" }, F.name, F.description, F.url),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "保存");
          save.addEventListener("click", function () {
            Object.keys(F).forEach(function (k) { F[k].setError(""); });
            MC.withBusy(save, function () {
              return api.put(app.p("/tools/" + api.seg(t ? t.name : name.value.trim() || "_")), {
                name: name.value.trim(),
                description: desc.value.trim(),
                url: url.value.trim(),
              }).then(function () {
                close();
                MC.toast.success("已保存");
                app.syncRevision();
                if (app.refreshTools) app.refreshTools();
                load();
              }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function removeTool(t) {
      MC.confirm({ title: "删除工具 " + t.name, confirmText: "删除", danger: true }).then(function (ok) {
        if (!ok) return;
        api.del(app.p("/tools/" + api.seg(t.name))).then(function () {
          MC.toast.success("已删除");
          app.syncRevision();
          if (app.refreshTools) app.refreshTools();
          load();
        }, function (err) { MC.toastError("删除失败", err); });
      });
    }

    load();
    return {
      el: el,
      title: "外部工具",
      icon: "tool",
      update: function () {},
      destroy: function () { unsub(); },
    };
  };
})(window);
