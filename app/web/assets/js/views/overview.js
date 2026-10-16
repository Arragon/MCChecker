/*
 * 速览：收藏变量的实时值（按文件分组）+ 最近更新 + 待审概况 + 定时任务状态。
 * 不是 Dashboard：只展示用户主动收藏的关键参数，以及需要关注的变化。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  MC.views.overview = function (app) {
    var el = h("div");
    var header = h(
      "div.view-header",
      h("div.view-title", h("h1", "速览"), h("div.view-sub", "收藏的关键变量会实时读取当前版本的值；文件被删除或节点消失时会明确标出。"))
    );
    var bodyEl = h("div.view-body");
    el.append(header, bodyEl);
    var unsub = [
      MC.bus.on("changed:favorites", function () { load(true); }),
      MC.bus.on("changed:files", function () { load(true); }),
      MC.bus.on("files", function () { renderSide(); }),
    ];
    var data = null;
    var sideHost = h("div.col", { style: "gap:16px" });

    function load(silent) {
      if (!silent) util.replace(bodyEl, MC.stateBlock({ kind: "loading" }));
      return Promise.all([api.get(app.p("/favorites")), api.get(app.p("/schedule")).then(null, function () { return null; })]).then(
        function (res) {
          data = { groups: res[0].groups, schedule: res[1] };
          render();
        },
        function (err) { if (!silent) util.replace(bodyEl, MC.errorBlock(err, load)); }
      );
    }

    function render() {
      var groups = data.groups;
      var main = h("div.col", { style: "gap:16px" });
      if (!groups.length) {
        main.append(
          h(
            "div.panel",
            MC.stateBlock({
              kind: "empty",
              icon: "star",
              title: "还没有收藏变量",
              desc: "打开任意配置文件，点击节点前的 ☆（或选中后按 S）即可收藏。收藏对本机型的所有用户可见。",
              actions: app.files[0] ? [h("button.btn.btn-primary", { type: "button", onclick: function () { app.openFile(app.files[0].name); } }, "打开 " + app.files[0].name)] : null,
            })
          )
        );
      }
      var active = groups.filter(function (g) { return g.exists; });
      var deleted = groups.filter(function (g) { return !g.exists; });
      active.forEach(function (g) { main.append(groupPanel(g)); });
      if (deleted.length) {
        main.append(
          h(
            "div.panel.fav-group",
            h("div.panel-header", icon("alert"), h("h3", "源文件已不存在"), h("span.small.muted", deleted.reduce(function (s, g) { return s + g.items.length; }, 0) + " 项")),
            h("div.panel-body", h("div.banner.banner-warning", icon("info"), h("div.banner-body", "以下收藏的源文件已被删除或改名，暂时移到这里；文件恢复后会自动回到原分组。")), h("div", { style: "margin-top:12px" }, deleted.map(function (g) { return groupTable(g, true); })))
          )
        );
      }
      renderSide();
      util.replace(bodyEl, h("div.overview-grid", main, sideHost));
    }

    function groupPanel(g) {
      var clearBtn = h("button.btn.btn-ghost.btn-sm", { type: "button" }, "清空");
      clearBtn.addEventListener("click", function () {
        MC.confirm({ title: "清空 " + g.file + " 的收藏", message: "将移除该文件下的 " + g.items.length + " 个收藏及其备注，影响本机型所有用户的速览。", confirmText: "清空", danger: true }).then(function (ok) {
          if (!ok) return;
          api.del(app.p("/favorites"), { file: g.file }).then(function () { MC.toast.success("已清空"); app.syncRevision(); load(true); }, function (err) { MC.toastError("操作失败", err); });
        });
      });
      return h(
        "section.panel.fav-group",
        h(
          "div.panel-header",
          h("div.fav-file", util.fmtTag(g.file), h("button.link-btn", { type: "button", style: "font-weight:600;color:var(--c-text)", onclick: function () { app.openFile(g.file); } }, g.file), h("span.badge", g.items.length + " 项")),
          h("span.xsmall.muted", "更新于 " + util.formatTime(g.updated_at)),
          clearBtn
        ),
        h("div.panel-body.flush", groupTable(g, false))
      );
    }

    function groupTable(g, isDeleted) {
      var rows = g.items.map(function (it) {
        var note = h("input.input.input-sm.note-input", { type: "text", value: it.note || "", placeholder: "添加备注…", "aria-label": it.label + " 的备注" });
        var saved = it.note || "";
        function saveNote() {
          var v = note.value.trim();
          if (v === saved) return;
          api.patch(app.p("/favorites"), { file: g.file, path: it.path, note: v }).then(function () { saved = v; MC.toast.success("备注已保存"); app.syncRevision(); }, function (err) { MC.toastError("备注保存失败", err); note.value = saved; });
        }
        note.addEventListener("blur", saveNote);
        note.addEventListener("keydown", function (e) { if (e.key === "Enter") note.blur(); if (e.key === "Escape") { note.value = saved; note.blur(); } });
        var valueCell;
        if (it.children && it.children.length) {
          valueCell = h("td", h("ul.fav-children", it.children.slice(0, 12).map(function (c) { return h("li", c.label, c.value !== null ? [" = ", h("span.v" + (c.missing ? ".muted" : ""), c.missing ? "未检测到" : c.value)] : null); }), it.children.length > 12 ? h("li.muted", "… 共 " + it.children.length + " 项") : null));
        } else if (it.missing) {
          valueCell = h("td.fav-value.is-missing", icon("alert"), " 未检测到，请检查", it.saved_value !== null && it.saved_value !== undefined ? h("div.xsmall.muted", "收藏时：" + it.saved_value) : null);
        } else {
          var changed = it.saved_value !== undefined && it.saved_value !== null && it.saved_value !== it.value;
          valueCell = h("td.fav-value", it.value, changed ? h("div.xsmall.muted", { style: "font-family:var(--font-ui)" }, "收藏时为 " + it.saved_value) : null);
        }
        var rm = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "取消收藏 " + it.label, "data-tip": "取消收藏", "data-tip-pos": "left" }, icon("x"));
        rm.addEventListener("click", function () {
          api.del(app.p("/favorites"), { file: g.file, path: it.path }).then(function () {
            MC.toast.info("已取消收藏 " + it.label, null, { action: { label: "撤销", onClick: function () { api.post(app.p("/favorites"), { file: g.file, id: it.path }).then(function (r) { if (it.note) api.patch(app.p("/favorites"), { file: g.file, path: it.path, note: it.note }); load(true); }); } } });
            app.syncRevision();
            load(true);
          }, function (err) { MC.toastError("操作失败", err); });
        });
        var locate = isDeleted ? null : h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.openFile(g.file, { node: it.path }); } }, "定位");
        return h("tr", h("td.fav-label", it.label), valueCell, h("td", { style: "min-width:160px" }, note), h("td.fav-path.truncate", { title: it.path }, it.path), h("td.cell-actions", locate, rm));
      });
      return h(
        "div.table-wrap",
        isDeleted ? h("div.small", { style: "font-weight:600;margin:8px 0 4px" }, g.file) : null,
        h("table.table.fav-table", h("thead", h("tr", h("th", "变量"), h("th", "当前值"), h("th", "备注"), h("th", "路径"), h("th.cell-actions", ""))), h("tbody", rows))
      );
    }

    function renderSide() {
      if (!data) return;
      var recent = app.files.slice().sort(function (a, b) { return (b.updated_at || "").localeCompare(a.updated_at || ""); }).slice(0, 6);
      var pending = app.files.filter(function (f) { return f.pending_remarks; });
      var sc = data.schedule;
      util.replace(
        sideHost,
        h(
          "section.panel",
          h("div.panel-header", h("h3", "最近更新")),
          recent.length
            ? h("ul.mini-list", recent.map(function (f) { return h("li", util.fmtTag(f.name), h("button.link-btn.truncate.grow", { type: "button", style: "text-align:left", onclick: function () { app.openFile(f.name, { view: f.archive_count ? "history" : "tree" }); } }, f.name), h("span.xsmall.muted.nowrap", util.formatTime(f.updated_at))); }))
            : MC.stateBlock({ kind: "empty", title: "暂无文件" })
        ),
        app.can("admin")
          ? h(
              "section.panel",
              h("div.panel-header", h("h3", "待审建议"), pending.length ? h("button.btn.btn-sm", { type: "button", onclick: function () { app.go({ kind: "review" }); } }, "去审阅") : null),
              pending.length
                ? h("ul.mini-list", pending.map(function (f) { return h("li", h("span.grow.truncate.mono.small", f.name), h("span.badge.badge-warning", f.pending_remarks + " 条")); }))
                : h("div.panel-body.small.muted", "没有待审的修改建议")
            )
          : null,
        sc
          ? h(
              "section.panel",
              h("div.panel-header", h("h3", "定时更新"), h("span.badge" + (sc.enabled ? ".badge-success" : ""), sc.enabled ? "已启用" : "未启用")),
              h(
                "div.panel-body",
                h(
                  "dl.kv",
                  h("dt", "更新间隔"),
                  h("dd", sc.interval_hours % 24 === 0 ? sc.interval_hours / 24 + " 天" : sc.interval_hours + " 小时"),
                  sc.enabled && sc.next_run_at ? [h("dt", "下次执行"), h("dd", util.formatTime(sc.next_run_at))] : null,
                  sc.last_run ? [h("dt", "上次结果"), h("dd", h("span.diff-stats", h("span.add", sc.last_run.success + " 更新"), h("span.none", sc.last_run.unchanged + " 无变化"), sc.last_run.failed ? h("span.del", sc.last_run.failed + " 失败") : null))] : null
                )
              )
            )
          : null
      );
    }

    load();
    return {
      el: el,
      title: "速览",
      icon: "overview",
      update: function () {},
      onShow: function () { if (data) load(true); },
      destroy: function () { unsub.forEach(function (f) { f(); }); },
    };
  };
})(window);
