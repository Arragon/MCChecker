/*
 * 参数登记矩阵（M5-09）+ 文件页配置管理分段（M5-10）。
 *
 * 数据来源：`/api/v1/config/*`（M5-08 骨架）。接口尚未接入时服务端回 404，
 * 这里**明确显示"接口尚未接入"**，不回退 mock——mock 只在模板模式（MC.config.mock）下由
 * api.js 分流，生产页永远走真实后端。
 *
 * 视觉：沿用现有 tokens/组件（.panel/.table/.badge/.stateBlock），不引入框架。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  /* ===================== 纯函数（前端测试直接覆盖） ===================== */

  function lower(value) { return String(value == null ? "" : value).toLowerCase(); }

  /** 四种检索维度：用户名称、稳定 ID、XML 名称、JSON 键（含历史名）。 */
  function filterParameters(items, q) {
    var needle = lower(q).trim();
    if (!needle) return (items || []).slice();
    return (items || []).filter(function (item) {
      var haystack = [item.name, item.id, item.category];
      (item.aliases || []).forEach(function (alias) { haystack.push(alias.name); });
      (item.definitions || []).forEach(function (def) {
        haystack.push(def.xml_name, def.json_key, def.unit, def.type);
      });
      return haystack.some(function (value) { return lower(value).indexOf(needle) >= 0; });
    });
  }

  /** 版本列：所有定义出现过的软件版本，按标签排序；label 取首个非空值。 */
  function versionColumns(items) {
    var seen = {};
    (items || []).forEach(function (item) {
      (item.definitions || []).forEach(function (def) {
        if (!def.release_id) return;
        if (!seen[def.release_id]) {
          seen[def.release_id] = { id: def.release_id, label: def.release_label || def.release_id };
        }
      });
    });
    return Object.keys(seen)
      .map(function (key) { return seen[key]; })
      .sort(function (a, b) { return String(a.label).localeCompare(String(b.label)); });
  }

  function cellText(definition) {
    if (!definition) return "—";
    var parts = [];
    if (definition.xml_name) parts.push(definition.xml_name);
    if (definition.json_key) parts.push(definition.json_key);
    var head = parts.join(" / ") || "—";
    var tail = [];
    if (definition.unit) tail.push(definition.unit);
    if (definition.revision_no) tail.push("r" + definition.revision_no);
    return tail.length ? head + " (" + tail.join(", ") + ")" : head;
  }

  /** 行模型：`[{parameter, cells: {release_id: cellText}}]`。 */
  function buildRows(items, columns) {
    return (items || []).map(function (item) {
      var cells = {};
      (item.definitions || []).forEach(function (def) {
        if (!def.release_id) return;
        cells[def.release_id] = cellText(def);
      });
      return { parameter: item, cells: cells, columns: columns };
    });
  }

  MC.configUi = {
    filterParameters: filterParameters,
    versionColumns: versionColumns,
    cellText: cellText,
    buildRows: buildRows,
  };

  /* ===================== 状态块 ===================== */

  function stateFor(info, app) {
    if (!info) return null;
    if (info.kind === "disabled") {
      return MC.stateBlock({
        kind: "empty",
        icon: "settings",
        title: "配置管理未开启",
        desc: "服务端特性门禁处于关闭状态。由管理员设置 MCHECKER_CONFIG_MANAGEMENT=on 并重启服务后可用。",
      });
    }
    if (info.kind === "forbidden") {
      return MC.stateBlock({
        kind: "locked",
        icon: "lock",
        title: "当前身份无权访问",
        desc: "配置管理需要具备 read 能力；请联系管理员确认 IP 管理员名单。",
      });
    }
    if (info.kind === "notfound") {
      return MC.stateBlock({
        kind: "empty",
        icon: "settings",
        title: "参数登记接口尚未接入",
        desc: "前端已按 M5-11 的合同对接，实体接口随 M6-04 上线。此处不放行 mock 数据。",
      });
    }
    if (info.kind === "corrupt") {
      return MC.stateBlock({
        kind: "error",
        icon: "alert",
        title: "配置数据损坏，已进入只读保护",
        desc: info.message || "请用备份恢复流程处理，不要直接删除 state.json。",
      });
    }
    return MC.stateBlock({
      kind: "error",
      icon: "alert",
      title: "加载失败",
      desc: info.message,
      actions: [h("button.btn.btn-sm", { type: "button", onclick: function () { global.location.reload(); } }, "重新加载")],
    });
  }

  /* ===================== 参数登记视图（M5-09） ===================== */

  MC.views.registry = function (app, route) {
    var query = (route && route.query) || {};
    var S = {
      loading: true,
      info: null,
      items: [],
      q: query.q || "",
      file: query.file || "",
      mode: null,
      writable: false,
      formOpen: false,
    };

    var search = h("input.input", {
      type: "search",
      placeholder: "按名称、稳定 ID、XML 名称或 JSON 键检索",
      value: S.q,
      "aria-label": "检索参数",
    });
    var statusLine = h("div.small.muted");
    var body = h("div");
    var draftNote = h("div.small.muted");

    var el = h(
      "div",
      h(
        "div.view-header",
        h(
          "div.view-title",
          h("h1", "参数登记"),
          h("div.view-sub", "一个参数主档对应多个软件版本定义；横向对比各版本的 XML 名称、JSON 键、类型与单位。")
        )
      ),
      h(
        "div.view-body",
        h(
          "div.container",
          h("div.row", { style: "gap:10px;align-items:center" }, search, statusLine),
          S.file ? h("div.banner.banner-info", icon("file"), h("div.banner-body", "已按文件筛选：" + S.file + "（关联关系随 M6-02 上线，当前显示全部参数）")) : null,
          body,
          draftNote
        )
      )
    );

    function paramsDraftKey() { return "registry:new"; }

    function updateDraftNote() {
      var api = MC.configApi;
      var draft = api.getDraft(app.pid, paramsDraftKey());
      util.replace(
        draftNote,
        draft
          ? h("span", "有未提交的参数草稿（", h("strong", draft.name || "未命名"), "）。切换页面前会提示。")
          : ""
      );
    }

    function render() {
      if (S.loading) {
        util.replace(body, MC.stateBlock({ kind: "loading", title: "正在读取参数登记…" }));
        return;
      }
      if (S.info) {
        util.replace(body, stateFor(S.info, app));
        return;
      }
      var items = MC.configUi.filterParameters(S.items, S.q);
      if (!S.items.length) {
        util.replace(
          body,
          MC.stateBlock({
            kind: "empty",
            icon: "list",
            title: "还没有参数主档",
            desc: "参数主档由管理员在参数登记里创建；接口随 M6-04 上线后此处可直接新建。",
          })
        );
        return;
      }
      if (!items.length) {
        util.replace(body, MC.stateBlock({ kind: "empty", icon: "search", title: "没有匹配的参数", desc: "换个关键词试试，检索覆盖历史名称。" }));
        return;
      }
      var columns = MC.configUi.versionColumns(items);
      var rows = MC.configUi.buildRows(items, columns);
      util.replace(
        body,
        h(
          "div.panel",
          h("div.panel-header", h("h2", "参数 × 软件版本"), h("span.muted.small", items.length + " 个参数 / " + columns.length + " 个版本")),
          h(
            "div",
            { style: "overflow:auto" },
            h(
              "table.table",
              h(
                "thead",
                h(
                  "tr",
                  h("th", { style: "min-width:200px" }, "参数"),
                  h("th", "类别"),
                  h("th", "稳定 ID"),
                  columns.map(function (column) { return h("th", column.label); }),
                  h("th", "状态")
                )
              ),
              h(
                "tbody",
                rows.map(function (row) {
                  return h(
                    "tr",
                    h("td", h("strong", row.parameter.name || "—")),
                    h("td", row.parameter.category || "—"),
                    h("td", h("code", row.parameter.id)),
                    columns.map(function (column) {
                      return h("td", row.cells[column.id] || "—");
                    }),
                    h("td", row.parameter.status === "retired" ? h("span.badge", "已退役") : h("span.badge.badge-ok", "启用"))
                  );
                })
              )
            )
          ),
          h("p.xsmall.muted", { style: "margin-top:10px" }, "只读视图：写入入口随 M6-04 开放，届时按 config_registry_write 能力启用。")
        )
      );
      updateDraftNote();
    }

    function load() {
      S.loading = true;
      S.info = null;
      render();
      var api = MC.configApi;
      return Promise.all([api.status(), api.parameters({ q: "" })]).then(
        function (results) {
          S.mode = results[0].mode;
          S.writable = !!results[0].writable;
          S.items = (results[1] && results[1].items) || [];
          S.loading = false;
          util.replace(
            statusLine,
            h(
              "span",
              "档位 " + (S.mode || "off") + " · " + S.items.length + " 个参数" + (S.writable ? " · 可写" : " · 只读")
            )
          );
          render();
        },
        function (info) {
          S.loading = false;
          S.info = info;
          util.replace(statusLine, "");
          render();
        }
      );
    }

    var timer = null;
    search.addEventListener("input", function () {
      S.q = search.value;
      if (timer) global.clearTimeout(timer);
      timer = global.setTimeout(render, 120);
    });
    search.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); render(); }
    });

    load();
    var offRoute = MC.bus.on("route", function () { updateDraftNote(); });

    return {
      el: el,
      title: "参数登记",
      icon: "list",
      update: function () { updateDraftNote(); },
      destroy: function () {
        if (timer) global.clearTimeout(timer);
        offRoute();
      },
      isDirty: function () { return MC.configApi.hasDraft(app.pid); },
    };
  };

  /* ===================== 文件页配置管理分段（M5-10） ===================== */

  /**
   * 在文件页"配置管理"分段渲染关联状态。
   * 关联关系本身属 M6-02，这里只显示服务端给出的事实，不猜、不补。
   */
  MC.configUi.renderFileSection = function (app, ctx, host) {
    var pid = (ctx && ctx.pid) || app.pid;
    var name = (ctx && ctx.name) || "";
    var revision = (ctx && ctx.version) || "current";
    var key = "file:" + name;

    util.replace(host, MC.stateBlock({ kind: "loading", title: "正在读取配置关联…" }));

    MC.configApi.profileStatus(pid).then(
      function (status) {
        var note = h("textarea.input", {
          rows: "3",
          placeholder: "记录这次现场核对的口径（例如：与 1.1.0 出口 XML 对照，脉冲宽度按秒计）",
          "aria-label": "关联说明草稿",
        });
        var existing = MC.configApi.getDraft(pid, key);
        if (existing) note.value = existing.note || "";
        note.addEventListener("input", function () {
          MC.configApi.setDraft(pid, key, { note: note.value, filename: name, version: revision });
        });
        util.replace(
          host,
          h(
            "div.panel",
            h(
              "div.panel-header",
              h("h2", "配置管理"),
              h("span.muted.small", status.baselines + " 条基线 / " + status.observations + " 条观测")
            ),
            h(
              "div",
              { style: "padding:14px 16px" },
              h(
                "p.small.muted",
                "文件：" + name + " · 版本：" + revision + "。此文件的参数登记关联随 M6-02 上线；在此之前只能记录核对说明，不会写入基线。"
              ),
              note,
              h(
                "div.row",
                { style: "margin-top:10px;gap:8px" },
                h(
                  "button.btn.btn-sm",
                  {
                    type: "button",
                    onclick: function () {
                      MC.configApi.clearDraft(pid, key);
                      note.value = "";
                      MC.toast.success("已清除本地草稿");
                    },
                  },
                  "清除草稿"
                ),
                h(
                  "a.btn.btn-sm",
                  { href: MC.router.format({ pid: pid, kind: "registry", query: { file: name } }) },
                  "在参数登记中查看"
                )
              )
            )
          )
        );
      },
      function (info) {
        util.replace(host, stateFor(info, app));
      }
    );
  };
})(window);
