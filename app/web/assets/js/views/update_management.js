/*
 * V3.1 更新管理工作区级入口（M16-31 / INH-2196）。
 *
 * 本卡只落壳：来源/计划/连接/运行记录/候选五个 tab 与旧设置深链收口。
 * 真实列表、抽屉、任务和候选交互由 M16-32–36 接入；不复制原型 mock 数据。
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

  function tabById(id) {
    return TABS.filter(function (tab) { return tab.id === id; })[0] || TABS[0];
  }

  MC.views.updates = function (app, route) {
    var state = { tab: tabById(route && route.arg).id };
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

    function renderBody() {
      util.clear(body);
      var tab = tabById(state.tab);
      body.appendChild(
        h(
          "div.card",
          h("div.card-h", h("strong", tab.label), h("span.muted.small", "工作区级")),
          h("div.card-b", h("p", tab.desc), h("p.muted.small", "该子页的真实 API/交互将在后续卡接入；当前不加载原型 mock 数据。"))
        )
      );
    }

    el.appendChild(
      h(
        "div.view-body",
        h(
          "div.page-head",
          h("div", h("h2", "更新管理"), h("p.muted", "更新源、计划、连接、运行记录与候选的统一入口；旧设置里的更新源/定时更新入口已收口到这里。"))
        ),
        tabbar,
        body
      )
    );
    renderTabs();
    renderBody();

    return {
      el: el,
      title: "更新管理",
      icon: "settings",
      update: function (next) {
        state.tab = tabById(next && next.arg).id;
        renderTabs();
        renderBody();
      },
      isDirty: function () { return false; },
      destroy: function () {},
    };
  };
})(typeof window !== "undefined" ? window : this);
