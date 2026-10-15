/*
 * 审阅修改（仅管理员）。
 * - 左侧按文件列出待审节点；右侧逐个节点选择一条建议或“不通过”。
 * - 选择即时保存为本机草稿（切换页面不丢失）；按文件提交，允许只提交已决定的节点，其余保持待审。
 * - 提交携带源文件指纹，源文件变化时服务端拒绝（REVIEW_CONFLICT），避免把建议应用到新版本上。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  MC.views.review = function (app) {
    var draftKey = "mc.reviewDraft." + app.pid;
    var S = { data: null, sel: null, draft: util.storageGet(draftKey, {}), lastResult: null };
    var el = h("div");
    var header = h("div.view-header");
    var host = h("div.view-body.is-flush");
    el.append(header, host);
    var unsub = MC.bus.on("changed:remarks", function () { load(true); });

    function dkey(file, g) { return file + "|" + g; }
    function saveDraft() { util.storageSet(draftKey, S.draft); app.renderTabbar(); }

    function renderHeader() {
      var total = 0;
      var decided = 0;
      if (S.data) S.data.files.forEach(function (f) { f.nodes.forEach(function (n) { total++; if (S.draft[dkey(f.file, n.group_key)]) decided++; }); });
      util.replace(
        header,
        h("div.view-title", h("h1", "审阅修改"), h("div.view-sub", "为每个节点选择一条建议或“不通过”。通过的建议会生成一个新的配置文件（带时间戳），原文件保持不变。")),
        S.data ? h("span.small.muted", "已决定 " + decided + " / " + total) : null,
        h("button.btn", { type: "button", onclick: function () { load(); } }, icon("refresh"), "刷新")
      );
    }

    function load(silent) {
      if (!app.can("admin")) {
        util.replace(host, MC.stateBlock({ kind: "locked", title: "仅管理员可审阅", desc: "当前身份为「" + app.identity.role_label + "」。如需审阅权限，请联系部署者将你加入「管理员列表」。" }));
        renderHeader();
        return;
      }
      if (!silent) util.replace(host, MC.stateBlock({ kind: "loading" }));
      api.get(app.p("/review")).then(
        function (res) {
          S.data = res;
          var all = [];
          res.files.forEach(function (f) { f.nodes.forEach(function (n) { all.push(dkey(f.file, n.group_key)); }); });
          Object.keys(S.draft).forEach(function (k) { if (all.indexOf(k) < 0) delete S.draft[k]; });
          saveDraft();
          if (!S.sel || all.indexOf(S.sel) < 0) S.sel = all[0] || null;
          render();
        },
        function (err) { util.replace(host, MC.errorBlock(err, load)); }
      );
    }

    function find(key) {
      var out = null;
      S.data.files.forEach(function (f) { f.nodes.forEach(function (n) { if (dkey(f.file, n.group_key) === key) out = { f: f, n: n }; }); });
      return out;
    }

    function render() {
      renderHeader();
      var files = S.data.files;
      if (!files.length) {
        util.replace(
          host,
          h("div.view-body", h("div.container.is-narrow", S.lastResult ? resultBanner(S.lastResult) : null, h("div.panel", MC.stateBlock({ kind: "empty", icon: "check-circle", title: "没有待审的修改建议", desc: "成员在文件详情面板中提交建议后会出现在这里。" }))))
        );
        return;
      }
      var nav = h("nav.review-nav", { "aria-label": "待审节点" });
      files.forEach(function (f) {
        var decided = f.nodes.filter(function (n) { return S.draft[dkey(f.file, n.group_key)]; }).length;
        nav.append(h("div.rn-file", h("div.row", util.fmtTag(f.file), h("span.grow.truncate", f.file), h("span.num", decided + "/" + f.nodes.length))));
        f.nodes.forEach(function (n) {
          var k = dkey(f.file, n.group_key);
          var choice = S.draft[k];
          var b = h(
            "button.rn-item" + (S.sel === k ? ".is-active" : ""),
            { type: "button", "aria-current": S.sel === k ? "true" : null },
            h("span.rn-label", n.label),
            n.stale ? h("span.badge.badge-danger", "已变化") : n.legacy ? h("span.badge", "旧版") : null,
            h("span.badge" + (choice === "reject" ? ".badge-danger" : choice ? ".badge-success" : ""), choice === "reject" ? "不通过" : choice ? "通过" : n.remarks.length + " 条")
          );
          b.addEventListener("click", function () { S.sel = k; render(); });
          nav.append(b);
        });
      });
      var cur = find(S.sel);
      util.replace(host, h("div.review-layout", nav, h("div.review-main", h("div.container.is-narrow", S.lastResult ? resultBanner(S.lastResult) : null, cur ? detail(cur.f, cur.n) : null))));
    }

    function detail(f, n) {
      var k = dkey(f.file, n.group_key);
      var choice = S.draft[k] || null;
      var list = h("div.choice-list", { role: "radiogroup", "aria-label": "选择审阅结果" });
      n.remarks.forEach(function (r) {
        var disabled = n.stale || !n.resolvable;
        list.append(choiceRow(r.id, h("div.grow", h("div.c-value", r.proposed_value), h("div.c-meta", r.actor_display + " · " + util.formatTime(r.created_at))), choice === r.id, false, disabled));
      });
      list.append(choiceRow("reject", h("div.grow", h("div", { style: "font-weight:500" }, "不通过"), h("div.c-meta", "驳回该节点的全部建议")), choice === "reject", true, false));

      function choiceRow(value, content, checked, isReject, disabled) {
        var input = h("input", { type: "radio", name: "rv-" + k, checked: checked, disabled: disabled });
        input.addEventListener("change", function () {
          S.draft[k] = value;
          saveDraft();
          render();
        });
        return h("label.choice" + (checked ? ".is-checked" : "") + (isReject ? ".is-reject" : ""), { style: disabled ? "opacity:.55;cursor:not-allowed" : null }, input, content);
      }

      var warn = null;
      if (n.stale) warn = h("div.banner.banner-danger", icon("alert"), h("div.banner-body", "该节点的当前值已从 ", h("code", n.original_value), " 变为 ", h("code", n.current_value), "。建议基于旧值提交，只能驳回；如仍需修改，请让提交人基于新版本重新提交。"));
      else if (!n.resolvable) warn = h("div.banner.banner-danger", icon("alert"), h("div.banner-body", "无法在当前文件中唯一定位该节点（可能已删除或存在同名节点），只能驳回。"));
      else if (n.legacy) warn = h("div.banner.banner-neutral", icon("info"), h("div.banner-body", "这是旧版本系统提交的建议（无精确定位信息），已按路径唯一匹配并核对原值一致。"));

      var fileDecisions = f.nodes.filter(function (x) { return S.draft[dkey(f.file, x.group_key)]; });
      var submit = h("button.btn.btn-primary", { type: "button", disabled: !fileDecisions.length }, icon("check"), "提交 " + f.file + " 的审阅结果（" + fileDecisions.length + "/" + f.nodes.length + "）");
      submit.addEventListener("click", function () { confirmSubmit(f, submit); });

      return h(
        "div.col",
        { style: "gap:16px" },
        h(
          "section.panel",
          h("div.panel-header", h("h2.mono", n.label), h("span.badge", n.node_type || "-")),
          h(
            "div.panel-body.col",
            { style: "gap:14px" },
            h("div.small.muted", f.file + " › " + n.path),
            h("div.value-compare", h("div", h("div.field-help", "提交时的原值"), h("div.insp-value", n.original_value)), h("span.muted", icon("arrow-right")), h("div", h("div.field-help", "文件当前值"), h("div.insp-value", n.current_value === null ? "—" : n.current_value))),
            warn,
            h("div.field", h("span.field-label", "审阅结果"), list),
            h("div.row", h("button.link-btn.small", { type: "button", onclick: function () { app.openFile(f.file, { node: n.loc || n.id }); } }, "在文件中查看该节点"), h("span.spacer"), choice ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { delete S.draft[k]; saveDraft(); render(); } }, "清除选择") : null)
          )
        ),
        h(
          "div.panel",
          h("div.panel-body.row.wrap", h("div.grow.small.muted", "可以只提交已决定的节点，未决定的节点保持待审。通过的建议会合并生成一个新文件，原文件不会被覆盖。"), submit)
        )
      );
    }

    function confirmSubmit(f, btn) {
      var decisions = f.nodes.filter(function (n) { return S.draft[dkey(f.file, n.group_key)]; }).map(function (n) { return { group_key: n.group_key, choice: S.draft[dkey(f.file, n.group_key)], node: n }; });
      var approve = decisions.filter(function (d) { return d.choice !== "reject"; });
      var reject = decisions.filter(function (d) { return d.choice === "reject"; });
      var detailEl = h(
        "div.col",
        { style: "gap:10px" },
        approve.length
          ? h("div", h("div.section-title", "通过（" + approve.length + "）"), h("table.table.is-compact", h("tbody", approve.map(function (d) {
              var r = d.node.remarks.filter(function (x) { return x.id === d.choice; })[0];
              return h("tr", h("td.mono", d.node.label), h("td.mono", { style: "color:var(--c-danger-text)" }, d.node.original_value), h("td", "→"), h("td.mono", { style: "color:var(--c-success-text)" }, r ? r.proposed_value : ""));
            }))))
          : null,
        reject.length ? h("div.small", "驳回 " + reject.length + " 个节点的全部建议") : null,
        approve.length ? h("div.banner.banner-neutral", icon("info"), h("div.banner-body", "将生成新文件 ", h("code", f.file.replace(/(\.[^.]+)?$/, "_<时间戳>$1")), "，原文件 ", h("code", f.file), " 保持不变。")) : null
      );
      MC.confirm({ title: "提交审阅结果", detail: detailEl, confirmText: "确认提交" }).then(function (ok) {
        if (!ok) return;
        var requestId = util.uid("rv");
        MC.withBusy(btn, function () {
          return api.post(app.p("/review/submit"), { request_id: requestId, file: f.file, source_sha: f.current_sha, decisions: decisions.map(function (d) { return { group_key: d.group_key, choice: d.choice }; }) }).then(
            function (res) {
              decisions.forEach(function (d) { delete S.draft[dkey(f.file, d.group_key)]; });
              saveDraft();
              S.lastResult = Object.assign({ file: f.file }, res);
              MC.toast.success("审阅已提交", "通过 " + res.approved + " 条，驳回 " + res.rejected + " 条");
              app.syncRevision();
              app.refreshFiles();
              load(true);
            },
            function (err) {
              if (err.code === "REVIEW_CONFLICT") {
                MC.toast.error("源文件已变化，未提交", "已刷新最新内容，请重新核对标红的节点", { duration: 10000 });
                load(true);
              } else MC.toastError("提交失败", err);
            }
          );
        });
      });
    }

    function resultBanner(r) {
      return h(
        "div.banner.banner-neutral",
        { style: "margin-bottom:16px" },
        icon("check-circle"),
        h(
          "div.banner-body",
          h("strong", r.file + " 审阅完成："),
          "通过 " + r.approved + " 条，驳回 " + r.rejected + " 条。",
          r.generated_file ? [" 已生成 ", h("button.link-btn", { type: "button", onclick: function () { app.openFile(r.generated_file); } }, r.generated_file), "。"] : " 未生成新文件。"
        ),
        r.generated_file ? h("button.btn.btn-sm", { type: "button", onclick: function () { app.downloadFile(r.generated_file); } }, icon("download"), "下载") : null,
        h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "关闭", onclick: function () { S.lastResult = null; render(); } }, icon("x"))
      );
    }

    load();
    return {
      el: el,
      title: "审阅修改",
      icon: "review",
      update: function () {},
      onShow: function () { if (S.data) load(true); },
      isDirty: function () { return false; },
      destroy: unsub,
    };
  };
})(window);
