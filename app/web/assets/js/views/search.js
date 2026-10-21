/*
 * 全局搜索：在当前机型所有文件的当前版本中搜索变量名 / 值 / 收藏备注。
 * 结果按文件分组、分页加载；点击结果打开文件并定位到节点。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;
  var PAGE = 200;

  MC.views.search = function (app, route) {
    var S = { q: (route.query && route.query.q) || "", file: (route.query && route.query.file) || "", items: [], total: 0, files: {}, truncated: false, loading: false };
    var input = h("input.input", { type: "search", placeholder: "输入变量名、值或备注关键词", value: S.q, "aria-label": "搜索关键词" });
    var scope = h("select.select", { "aria-label": "搜索范围", style: "width:220px" });
    var go = h("button.btn.btn-primary", { type: "button", style: "height:38px" }, "搜索");
    var resultHost = h("div", { style: "margin-top:16px" });
    var el = h(
      "div",
      h("div.view-header", h("div.view-title", h("h1", "全局搜索"), h("div.view-sub", "在当前机型全部配置文件的当前版本中查找；匹配变量名、值以及收藏备注（不区分大小写）。"))),
      h("div.view-body", h("div.container", h("div.search-bar", h("div.input-wrap", icon("search"), input), scope, go), resultHost))
    );

    function fillScope() {
      util.replace(scope, h("option", { value: "" }, "全部文件"), app.files.map(function (f) { return h("option", { value: f.name, selected: f.name === S.file }, f.name); }));
    }
    fillScope();
    var unsub = MC.bus.on("files", fillScope);

    function submit() {
      var q = input.value.trim();
      if (!q) {
        input.focus();
        MC.toast.warning("请输入搜索关键词");
        return;
      }
      app.go({ kind: "search", query: { q: q, file: scope.value || null } });
    }
    go.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(); });

    function run(append) {
      if (!S.q) {
        util.replace(resultHost, h("div.panel", MC.stateBlock({ kind: "empty", icon: "search", title: "输入关键词开始搜索", desc: "提示：按 Ctrl+K 可在任何页面快速搜索或跳转文件。" })));
        return;
      }
      if (!append) util.replace(resultHost, MC.stateBlock({ kind: "loading", title: "正在搜索…" }));
      S.loading = true;
      api.get(app.p("/search"), { q: S.q, file: S.file || null, limit: PAGE, offset: append ? S.items.length : 0 }).then(
        function (res) {
          S.loading = false;
          S.items = append ? S.items.concat(res.items) : res.items;
          S.total = res.total;
          S.files = res.files;
          S.truncated = res.truncated;
          render();
        },
        function (err) { S.loading = false; util.replace(resultHost, MC.errorBlock(err, function () { run(false); })); }
      );
    }

    function render() {
      if (!S.items.length) {
        util.replace(resultHost, h("div.panel", MC.stateBlock({ kind: "empty", icon: "search", title: "没有找到“" + S.q + "”", desc: "检查拼写，或缩短关键词再试。搜索只覆盖当前版本，历史版本请在文件的「对比」中查看。" })));
        return;
      }
      var groups = {};
      var order = [];
      S.items.forEach(function (it) {
        if (!groups[it.file]) { groups[it.file] = []; order.push(it.file); }
        groups[it.file].push(it);
      });
      var summary = h("div.row.small.muted", { style: "margin-bottom:10px" }, "共 " + S.total + " 个匹配，分布在 " + Object.keys(S.files).length + " 个文件", S.items.length < S.total ? "（已显示 " + S.items.length + "）" : null);
      var panels = order.map(function (file) {
        return h(
          "section.panel",
          { style: "margin-bottom:12px" },
          h("div.panel-header", util.fmtTag(file), h("h3", file), h("span.badge", S.files[file] + " 个匹配"), h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.openFile(file); } }, "打开文件")),
          h(
            "div",
            groups[file].map(function (it) {
              var row = h(
                "div.hit",
                { tabindex: "0", role: "link", "aria-label": "打开 " + file + " 中的 " + it.label },
                h("div.hit-main", util.highlight(it.label, S.q), it.value !== null ? [h("span.tr-eq", " = "), h("span", { style: "color:#0b57d0" }, util.highlight(it.value, S.q))] : null),
                h("span.badge", { "aria-label": "匹配字段" }, { label: "名称", value: "值", note: "备注" }[it.match]),
                h("div.hit-path", it.path),
                it.note ? h("div.hit-note", icon("star"), " ", util.highlight(it.note, S.q)) : null
              );
              function open() { app.openFile(file, { node: it.loc }); }
              row.addEventListener("click", open);
              row.addEventListener("keydown", function (e) { if (e.key === "Enter") open(); });
              return row;
            })
          )
        );
      });
      util.replace(
        resultHost,
        summary,
        panels,
        S.truncated ? h("div", { style: "text-align:center;margin-top:8px" }, h("button.btn", { type: "button", onclick: function (e) { MC.withBusy(e.currentTarget, function () { run(true); }); } }, "加载更多结果")) : null
      );
    }

    run(false);
    return {
      el: el,
      title: S.q ? "搜索：" + S.q : "全局搜索",
      icon: "search",
      update: function (r) {
        var q = (r.query && r.query.q) || "";
        var file = (r.query && r.query.file) || "";
        if (q === S.q && file === S.file) return;
        S.q = q;
        S.file = file;
        input.value = q;
        fillScope();
        // activate() 会用 inst.title 回写标签名，所以两者都要更新，否则标签停在第一次的关键词。
        this.title = q ? "搜索：" + q : "全局搜索";
        app.setTabTitle(this, this.title);
        run(false);
      },
      onShow: function () { setTimeout(function () { if (!S.q) input.focus(); }, 0); },
      destroy: unsub,
    };
  };
})(window);
