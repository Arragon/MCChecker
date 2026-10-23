/*
 * 审计与谱系（M7-11）。
 *
 * 只读：按条件查 AuditEvent，点任意对象看修订链与来源。退役对象默认仍可查；
 * 页面不提供删除/改写入口，也不把普通日志当审计。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var FILTERS = ["action", "category", "parameter_id", "actor", "reason"];

  function buildQuery(form) {
    var query = {};
    (form || {});
    FILTERS.forEach(function (key) {
      var value = String((form && form[key]) || "").trim();
      if (value) query[key] = value;
    });
    query.limit = 30;
    return query;
  }

  function eventTitle(event) {
    return (event && (event.reason || event.action)) || "（无说明）";
  }

  function eventMeta(event) {
    if (!event) return "";
    return (
      (event.actor_label || "未知操作人") +
      " · " +
      (event.at || "") +
      (event.retired ? " · 含退役对象" : "")
    );
  }

  function revisionRows(timeline) {
    return ((timeline && timeline.revisions) || []).map(function (row) {
      return {
        revision: row.revision || "",
        createdAt: row.created_at || "",
        createdBy: row.created_by || "",
        state: row.state || "",
      };
    });
  }

  function provenanceGroups(timeline) {
    var related = (timeline && timeline.provenance && timeline.provenance.related) || {};
    return Object.keys(related)
      .sort()
      .map(function (kind) {
        return {
          kind: kind,
          label: (related[kind][0] && related[kind][0].kind_label) || kind,
          items: related[kind] || [],
        };
      });
  }

  function render(app, host, ctx) {
    var pid = (ctx && ctx.pid) || app.pid;
    var S = { items: [], cursor: null, selected: null, timeline: null, loading: false };
    var listHost = h("div");
    var detailHost = h("div");
    var moreHost = h("div");
    var moreBtn = h("button.btn.btn-sm", { type: "button" }, "加载更多");

    if (!MC.configApi) {
      util.replace(host, MC.stateBlock({ kind: "empty", title: "审计模块未接入", desc: "config-api.js 未加载。" }));
      return;
    }

    function filters() {
      var form = {};
      FILTERS.forEach(function (key) {
        var input = host.querySelector('[data-audit="' + key + '"]');
        form[key] = input ? input.value : "";
      });
      return form;
    }

    function drawList() {
      if (!S.items.length) {
        util.replace(listHost, MC.stateBlock({ kind: "empty", icon: "clock", title: "没有匹配的审计事件", desc: "换一个条件，或清空筛选后重试。" }));
        util.replace(moreHost, S.cursor ? moreBtn : null);
        return;
      }
      util.replace(
        listHost,
        h(
          "table.table",
          h("thead", h("tr", h("th", "时间"), h("th", "动作"), h("th", "说明"), h("th", "操作人"), h("th", "对象"))),
          h(
            "tbody",
            S.items.map(function (event) {
              var objects = (event.objects_detail || []).map(function (obj) {
                return h(
                  "button.linkish",
                  {
                    type: "button",
                    title: obj.missing ? "对象在当前修订里已不存在" : "查看来源谱系",
                    onclick: function () { loadHistory(obj.id, obj.revision); },
                  },
                  (obj.kind_label || obj.kind || "") + " " + (obj.label || obj.id || "")
                );
              });
              return h(
                "tr",
                h("td.small", event.at || ""),
                h("td", h("code.small", event.action || "")),
                h("td", eventTitle(event)),
                h("td.small", event.actor_label || ""),
                h("td.small", objects.length ? h("div.row", { style: "gap:4px;flex-wrap:wrap" }, objects) : "—")
              );
            })
          )
        )
      );
      util.replace(moreHost, S.cursor ? moreBtn : null);
    }

    function load(reset) {
      if (S.loading) return;
      S.loading = true;
      var query = buildQuery(filters());
      if (!reset && S.cursor) query.cursor = S.cursor;
      MC.configApi.audit(pid, query).then(
        function (body) {
          S.loading = false;
          S.items = reset ? (body.items || []) : S.items.concat(body.items || []);
          S.cursor = body.next_cursor || null;
          drawList();
        },
        function (err) {
          S.loading = false;
          util.replace(listHost, MC.errorBlock(err, function () { load(true); }));
        }
      );
    }

    function drawHistory() {
      var timeline = S.timeline;
      if (!timeline) {
        util.replace(detailHost, null);
        return;
      }
      var revisions = revisionRows(timeline);
      var groups = provenanceGroups(timeline);
      util.replace(
        detailHost,
        h(
          "div.panel",
          { style: "margin-top:12px" },
          h(
            "div.panel-header",
            h("h3", (timeline.object_kind_label || timeline.object_kind) + " · " + (timeline.object && (timeline.object.name || timeline.object.filename || timeline.object.id))),
            h("button.btn.btn-sm", { type: "button", onclick: function () { S.timeline = null; drawHistory(); } }, "收起")
          ),
          h("div", { style: "padding:12px 16px" },
            h("h4", "修订链"),
            h("table.table", h("tbody", revisions.map(function (row) {
              return h("tr", h("td", h("code.small", row.revision)), h("td.small", row.createdAt), h("td.small", row.createdBy), h("td.small", row.state));
            }))),
            h("h4", { style: "margin-top:12px" }, "来源与关联"),
            groups.length ? groups.map(function (group) {
              return h("div", { style: "margin:8px 0" },
                h("div.small.muted", group.label),
                h("div.row", { style: "gap:6px;flex-wrap:wrap" }, group.items.map(function (item) {
                  return h("button.linkish", {
                    type: "button",
                    onclick: function () { loadHistory(item.id, item.revision); },
                  }, (item.label || item.id) + (item.state ? "（" + item.state + "）" : ""));
                }))
              );
            }) : h("p.small.muted", "没有找到直接关联对象。")
          )
        )
      );
    }

    function loadHistory(objectId, revision) {
      if (!objectId) return;
      S.selected = objectId;
      MC.configApi.history(objectId, revision).then(
        function (timeline) {
          S.timeline = timeline;
          drawHistory();
        },
        function (err) {
          util.replace(detailHost, MC.errorBlock(err, function () { loadHistory(objectId, revision); }));
        }
      );
    }

    moreBtn.addEventListener("click", function () { load(false); });
    var form = h(
      "div.panel",
      { style: "margin-top:10px" },
      h("div.panel-header", h("h3", "审计与谱系"), h("span.small.muted", "只读 · 退役对象默认仍可查")),
      h("div", { style: "padding:12px 16px" },
        h("div.row", { style: "gap:8px;flex-wrap:wrap" },
          h("input.input", { "data-audit": "action", placeholder: "动作，如 baseline.approve", "aria-label": "动作" }),
          h("input.input", { "data-audit": "category", placeholder: "类别，如 baseline", "aria-label": "类别" }),
          h("input.input", { "data-audit": "parameter_id", placeholder: "参数 ID", "aria-label": "参数 ID" }),
          h("input.input", { "data-audit": "actor", placeholder: "操作人", "aria-label": "操作人" }),
          h("input.input", { "data-audit": "reason", placeholder: "理由关键字", "aria-label": "理由" }),
          h("button.btn.btn-sm.btn-primary", { type: "button", onclick: function () { S.cursor = null; load(true); } }, icon("search"), "查询")
        ),
        h("p.small.muted", { style: "margin:8px 0" }, "审计事件按 seq 倒序；筛选条件变化后游标会失效，需要重新查询。"),
        listHost,
        h("div.row", { style: "gap:8px;margin-top:8px" }, moreHost),
        detailHost
      )
    );
    util.replace(host, form);
    load(true);
  }

  MC.auditUi = {
    buildQuery: buildQuery,
    eventTitle: eventTitle,
    eventMeta: eventMeta,
    revisionRows: revisionRows,
    provenanceGroups: provenanceGroups,
    render: render,
  };
})(window);
