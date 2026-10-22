/*
 * 配置变更（M6-11 / M6-12）。
 *
 * 一张变更单是"把这些参数从 A 改成 B"的可审阅承诺。这个视图只做三件事：
 * 列出来、看清楚、按状态机允许的那条边操作。**不自己判断能不能批准**——
 * 服务端在批准时重算过期与范围，前端只把 `allowed` 与 `staleness` 摆出来。
 *
 * 权限分两层：提交面 `config_change_submit`、批准面 `config_change_approve`。
 * 按钮禁用只是体验，服务端仍然会 403（§4.3）。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var STATE_LABELS = {
    draft: "草稿",
    submitted: "待批准",
    approved: "已发布",
    rejected: "已驳回",
    withdrawn: "已撤回",
  };

  /* 列表顺序：**要人动手的排前面**。已发布放最后，它是"完成了"而不是"待办"。 */
  var STATE_ORDER = ["draft", "submitted", "rejected", "withdrawn", "approved"];

  var DECISION_LABELS = {
    use_new: "采用新值",
    inherit_old: "保持现值",
    manual: "手工改",
    pin: "钉住",
    clear_pin: "取消钉住",
  };

  var REASON_LABELS = {
    baseline_missing: "目标基线不存在了",
    baseline_revision_changed: "基线在提交之后又改过",
    definition_set_changed: "定义集合变了",
    baseline_superseded: "基线已被新版本取代",
  };

  /* ===================== 纯函数 ===================== */

  function stateLabel(state) {
    return STATE_LABELS[state] || String(state || "未知");
  }

  /** 状态徽标：类名单独拼，别用 `span.badge.` + 空串拼出 `badge.badge`。 */
  function stateBadge(state) {
    var tone = stateTone(state);
    return h("span" + (tone ? ".badge." + tone : ".badge"), stateLabel(state));
  }

  function stateTone(state) {
    if (state === "approved") return "badge-success";
    if (state === "submitted") return "badge-warning";
    if (state === "rejected") return "badge-danger";
    return "";
  }

  function valueText(value) {
    if (MC.configUi && MC.configUi.valueText) return MC.configUi.valueText(value);
    if (!value) return "—";
    return value.unit ? value.text + " " + value.unit : String(value.text);
  }

  function sameValue(left, right) {
    if (!left && !right) return true;
    if (!left || !right) return false;
    return left.type === right.type && left.text === right.text && (left.unit || null) === (right.unit || null);
  }

  /** 真正写入新值的行：`writes_value` 且值与意图至少一项变了。 */
  function changedCount(item) {
    return ((item && item.changes) || []).filter(function (row) {
      if (!row.writes_value) return false;
      return !sameValue(row.from, row.to) || (row.from_intent || "") !== (row.to_intent || "");
    }).length;
  }

  /** 列表行模型：只取列表要显示的字段，别把整个对象摊到 DOM 里。 */
  function summary(item) {
    var source = item || {};
    return {
      id: source.id,
      title: source.title || source.id || "（未命名变更）",
      reason: source.reason || "",
      state: source.state,
      stateLabel: stateLabel(source.state),
      tone: stateTone(source.state),
      baselineId: source.baseline_id,
      baselineRevision: source.baseline_revision,
      changed: changedCount(source),
      total: (source.changes || []).length,
      submittedBy: source.submitted_by || "",
      submittedAt: source.submitted_at || "",
      decidedBy: source.decided_by || "",
      decidedAt: source.decided_at || "",
      decideNote: source.decide_note || "",
      publishedRevision: source.published_baseline_revision || "",
      algorithm: (source.plan_ref || {}).algorithm || "",
      createdBy: source.created_by || "",
      createdAt: source.created_at || "",
    };
  }

  function groupByState(items) {
    var buckets = {};
    (items || []).forEach(function (item) {
      var key = String(item.state || "draft");
      (buckets[key] = buckets[key] || []).push(item);
    });
    var groups = [];
    STATE_ORDER.forEach(function (state) {
      if (!buckets[state] || !buckets[state].length) return;
      groups.push({ state: state, label: stateLabel(state), items: buckets[state] });
      delete buckets[state];
    });
    Object.keys(buckets).forEach(function (state) {
      groups.push({ state: state, label: stateLabel(state), items: buckets[state] });
    });
    return groups;
  }

  function rowModel(row) {
    var source = row || {};
    var writes = !!source.writes_value;
    return {
      parameter_id: source.parameter_id,
      name: source.name || source.parameter_id || "",
      category: source.category || "",
      known: !!source.known,
      decision: source.decision,
      decisionLabel: DECISION_LABELS[source.decision] || String(source.decision || ""),
      fromText: valueText(source.from),
      toText: valueText(source.to),
      fromIntent: source.from_intent || "",
      toIntent: source.to_intent || "",
      reason: source.reason || "",
      writesValue: writes,
      changed: writes && (!sameValue(source.from, source.to) || (source.from_intent || "") !== (source.to_intent || "")),
    };
  }

  /** 批准前为什么不能批：只列**当下**能看到的阻碍，真正的判定在服务端。 */
  function approveBlockers(detail) {
    var data = detail || {};
    var item = data.item || {};
    var out = [];
    if (item.state !== "submitted") {
      out.push({ level: "info", text: "当前状态是「" + stateLabel(item.state) + "」，只有「待批准」的单可以批准。" });
    }
    var stale = data.staleness || {};
    if (stale.stale) {
      (stale.reasons || []).forEach(function (code) {
        out.push({ level: "warn", text: REASON_LABELS[code] || code });
      });
    }
    if (!data.changed_rows) {
      out.push({ level: "warn", text: "这张单没有任何实际改动，不能发布。" });
    }
    return out;
  }

  function canApprove(detail) {
    return approveBlockers(detail).length === 0;
  }

  /* ===================== 视图 ===================== */

  function stateBlockOf(info) {
    if (!info) return null;
    if (info.kind === "disabled") {
      return MC.stateBlock({
        kind: "empty",
        icon: "settings",
        title: "配置管理未开启",
        desc: "服务端特性门禁处于关闭状态，配置变更不可用。",
      });
    }
    if (info.kind === "forbidden") {
      return MC.stateBlock({
        kind: "locked",
        icon: "lock",
        title: "当前身份无权查看配置变更",
        desc: "配置变更需要配置域读能力；请联系管理员。",
      });
    }
    if (info.kind === "notfound") {
      return MC.stateBlock({
        kind: "empty",
        icon: "settings",
        title: "配置变更接口不可用",
        desc: "服务端没有对应接口（可能是版本不匹配）。",
      });
    }
    return MC.errorBlock
      ? MC.errorBlock({ message: info.message || "加载失败" }, function () { global.location.reload(); })
      : MC.stateBlock({ kind: "error", icon: "alert", title: "加载失败", desc: info.message });
  }

  /** 把一个动作的失败翻成人话；409 类错误要顺手刷新，别让界面停在旧状态。 */
  function actionError(err, reload) {
    if (!err) return;
    if (err.kind === "conflict" || err.code === "PLAN_STALE" || err.code === "CHANGE_STATE_INVALID") {
      MC.toast.error(err.message || "状态已变化，请刷新后重试", null, { duration: 9000 });
      reload();
      return;
    }
    if (err.kind === "forbidden") {
      MC.toast.error("当前身份无权执行该操作", err.message);
      return;
    }
    MC.toastError ? MC.toastError("操作失败", err) : MC.toast.error("操作失败", err.message);
  }

  MC.changesUi = {
    STATE_LABELS: STATE_LABELS,
    STATE_ORDER: STATE_ORDER,
    DECISION_LABELS: DECISION_LABELS,
    stateLabel: stateLabel,
    stateTone: stateTone,
    changedCount: changedCount,
    summary: summary,
    groupByState: groupByState,
    rowModel: rowModel,
    approveBlockers: approveBlockers,
    canApprove: canApprove,
  };

  /**
   * 渲染"配置变更"分段。`host` 由调用方（审阅页）提供；这里不假设任何全局布局。
   */
  MC.changesUi.render = function (app, host) {
    var S = { loading: true, items: [], filter: "all", detail: null, busy: false, note: "" };
    var body = h("div");

    function reload(keepDetail) {
      util.replace(body, MC.stateBlock({ kind: "loading", title: "正在读取配置变更…" }));
      MC.configApi.changeSets(app.pid).then(
        function (res) {
          S.items = res.items || [];
          S.loading = false;
          if (!keepDetail) S.detail = null;
          draw();
        },
        function (err) {
          util.replace(host, body);
          util.replace(body, stateBlockOf(err));
        }
      );
    }

    function openDetail(id) {
      MC.configApi.changeSet(app.pid, id).then(
        function (detail) {
          S.detail = detail;
          S.note = "";
          draw();
        },
        function (err) { actionError(err, reload); }
      );
    }

    function draw() {
      var rows = S.items.filter(function (item) {
        return S.filter === "all" || item.state === S.filter;
      });
      var filters = h("div.row", { style: "gap:6px;flex-wrap:wrap;margin-bottom:12px" });
      var options = [{ state: "all", label: "全部 " + S.items.length }].concat(
        groupByState(S.items).map(function (group) {
          return { state: group.state, label: group.label + " " + group.items.length };
        })
      );
      util.replace(
        filters,
        options.map(function (option) {
          var active = S.filter === option.state;
          return h(
            "button.btn.btn-sm" + (active ? ".btn-primary" : ""),
            {
              type: "button",
              "aria-pressed": active ? "true" : "false",
              onclick: function () { S.filter = option.state; draw(); },
            },
            option.label
          );
        })
      );

      var table = null;
      if (!rows.length) {
        table = MC.stateBlock({
          kind: "empty",
          icon: "compare",
          title: S.items.length ? "这一组没有变更单" : "还没有配置变更",
          desc: S.items.length
            ? "换个状态筛选看看。"
            : "在文件页的「配置管理」分段把现场值采纳为拟值后，即可在这里发起变更。",
        });
      } else {
        table = h(
          "table.table",
          h(
            "thead",
            h(
              "tr",
              h("th", "变更单"),
              h("th", "状态"),
              h("th", "改动"),
              h("th", "目标基线"),
              h("th", "提交"),
              h("th", "")
            )
          ),
          h(
            "tbody",
            rows.map(function (item) {
              var model = summary(item);
              return h(
                "tr",
                { class: S.detail && S.detail.item && S.detail.item.id === model.id ? "is-selected" : null },
                h(
                  "td",
                  h("div", model.title),
                  h("div.small.muted", model.reason || model.algorithm || model.id)
                ),
                h("td", stateBadge(model.state)),
                h("td.num", model.changed + " / " + model.total),
                h("td.mono.small", model.baselineId + " " + (model.baselineRevision || "")),
                h(
                  "td.small.muted",
                  model.submittedBy
                    ? model.submittedBy + " · " + util.formatTime(model.submittedAt)
                    : model.createdBy + " · " + util.formatTime(model.createdAt)
                ),
                h(
                  "td",
                  h(
                    "button.btn.btn-sm",
                    { type: "button", onclick: function () { openDetail(model.id); } },
                    "查看"
                  )
                )
              );
            })
          )
        );
      }

      util.replace(
        body,
        h(
          "div.panel",
          h(
            "div.panel-header",
            h("h2", "配置变更"),
            h("span.muted.small", S.items.length + " 张单"),
            h("button.btn.btn-sm", { type: "button", onclick: function () { reload(true); } }, icon("refresh"), "刷新")
          ),
          h("div", { style: "padding:14px 16px" }, filters, table, S.detail ? detailPanel() : null)
        )
      );
      util.replace(host, body);
    }

    function detailPanel() {
      var detail = S.detail;
      var item = detail.item || {};
      var blockers = approveBlockers(detail);
      var maySubmit = app.can && app.can("config_change_submit");
      var mayApprove = app.can && app.can("config_change_approve");
      var state = item.state || "draft";

      var note = h("textarea.input", {
        rows: "2",
        placeholder: state === "submitted" ? "批准/驳回意见（驳回必填）" : "说明（撤回/提交理由）",
        "aria-label": "变更单说明",
      });
      note.value = S.note;
      note.addEventListener("input", function () { S.note = note.value; });

      function act(name, call, options) {
        var confirmText = (options && options.confirmText) || name;
        if (options && options.needNote && !String(S.note || "").trim()) {
          MC.toast.error(options.needNote);
          return;
        }
        MC.confirm({ title: confirmText, detail: options && options.detail, confirmText: confirmText }).then(function (ok) {
          if (!ok) return;
          call().then(
            function () {
              MC.toast.success(confirmText + "成功");
              S.note = "";
              reload(true);
              openDetail(item.id);
            },
            function (err) { actionError(err, function () { reload(true); }); }
          );
        });
      }

      var actions = h("div.row", { style: "gap:8px;flex-wrap:wrap;margin-top:10px" });
      if (state === "draft" && maySubmit) {
        actions.append(
          h(
            "button.btn.btn-primary.btn-sm",
            {
              type: "button",
              onclick: function () {
                act("提交", function () {
                  return MC.configApi.submitChangeSet(app.pid, item.id, {
                    reason: S.note,
                    expected_revision: item.revision,
                  });
                }, { needNote: "提交必须写明理由" });
              },
            },
            "提交"
          )
        );
      }
      if ((state === "draft" || state === "submitted") && maySubmit) {
        actions.append(
          h(
            "button.btn.btn-sm",
            {
              type: "button",
              onclick: function () {
                act("撤回", function () {
                  return MC.configApi.withdrawChangeSet(app.pid, item.id, {
                    reason: S.note,
                    expected_revision: item.revision,
                  });
                }, { needNote: "撤回要写原因（会留在历史里）" });
              },
            },
            "撤回"
          )
        );
      }
      if ((state === "rejected" || state === "withdrawn") && maySubmit) {
        actions.append(
          h(
            "button.btn.btn-sm",
            {
              type: "button",
              onclick: function () {
                act("重开一版", function () {
                  return MC.configApi.reopenChangeSet(app.pid, item.id, { reason: S.note });
                }, { confirmText: "重开一版" });
              },
            },
            "重开一版"
          )
        );
      }
      if (state === "submitted" && mayApprove) {
        actions.append(
          h(
            "button.btn.btn-primary.btn-sm",
            {
              type: "button",
              disabled: !canApprove(detail),
              title: canApprove(detail) ? null : "先把上面的阻碍处理掉",
              onclick: function () {
                act("批准并发布", function () {
                  return MC.configApi.approveChangeSet(app.pid, item.id, {
                    note: S.note,
                    expected_revision: item.revision,
                    expected_baseline_revision: item.baseline_revision,
                  });
                }, { confirmText: "批准并发布" });
              },
            },
            "批准并发布"
          ),
          h(
            "button.btn.btn-sm",
            {
              type: "button",
              onclick: function () {
                act("驳回", function () {
                  return MC.configApi.rejectChangeSet(app.pid, item.id, {
                    note: S.note,
                    expected_revision: item.revision,
                  });
                }, { needNote: "驳回必须写明意见", confirmText: "驳回" });
              },
            },
            "驳回"
          )
        );
      }
      if (!maySubmit && !mayApprove) {
        actions.append(h("span.small.muted", "当前身份只读：提交需要 config_change_submit，批准需要 config_change_approve。"));
      }

      return h(
        "div.panel",
        { style: "margin-top:14px" },
        h(
          "div.panel-header",
          h("h2", item.title || item.id),
          stateBadge(state),
          h("span.muted.small", item.id + " · " + item.baseline_id + " " + (item.baseline_revision || ""))
        ),
        h(
          "div",
          { style: "padding:14px 16px" },
          item.published_baseline_revision
            ? h("div.banner.banner-neutral", { style: "margin-bottom:10px" }, icon("check-circle"),
                h("div.banner-body", "已发布为基线 " + item.baseline_id + " " + item.published_baseline_revision + "。"))
            : null,
          blockers.map(function (blocker) {
            return h(
              "div.banner." + (blocker.level === "warn" ? "banner-warning" : "banner-neutral"),
              { style: "margin-bottom:8px" },
              icon("info"),
              h("div.banner-body", blocker.text)
            );
          }),
          h(
            "table.table",
            h("thead", h("tr", h("th", "参数"), h("th", "现值"), h("th", ""), h("th", "拟值"), h("th", "决议"))),
            h(
              "tbody",
              (detail.rows || []).map(function (row) {
                var model = rowModel(row);
                return h(
                  "tr",
                  h("td", h("div", model.name), h("div.small.muted", model.category)),
                  h("td.mono", model.fromText),
                  h("td", model.changed ? icon("arrow-right") : ""),
                  h("td.mono", model.toText),
                  h("td", h("span.badge", model.decisionLabel), model.reason ? h("div.small.muted", model.reason) : null)
                );
              })
            )
          ),
          h("div.field", { style: "margin-top:12px" }, h("span.field-label", "说明"), note),
          actions,
          h(
            "p.small.muted",
            { style: "margin-top:10px" },
            "批准即发布：服务端在同一个事务里写新基线修订与这张单的决议，不会出现" +
              "「已批准但未发布」的中间状态。过期判定（基线又改过、定义集合变了）在批准那一刻重算。"
          )
        )
      );
    }

    reload();
    return body;
  };
})(window);
