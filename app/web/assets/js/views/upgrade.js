/*
 * 跨版本升级合并（M7-04）。
 *
 * 左三栏是**事实**：旧共同基准 / 旧本地值 / 新上游；右两栏是**决定**：候选值与决议。
 * 三条约束：
 *
 * 1. **不自己算合并**：候选值、阻断项、允许动作全部来自服务端 MergePlan。
 *    前端一旦自己比字符串，就会和服务端的单位换算/范围校验打架。
 * 2. **不以颜色替代文本**：每个状态都带文字（"冲突""需人工""跟随新版"），
 *    颜色只是辅助；色盲用户与打印稿同样可读。
 * 3. **无共同基准不许一键合并**：三方不齐时按钮禁用并说明原因——
 *    "新建"是另一件事，不该伪装成"合并成功"。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var ACTION_LABELS = {
    keep: "保持",
    from_new: "跟随新版",
    drop: "移除",
    conflict: "冲突",
    manual: "需人工",
  };

  var DECISION_LABELS = {
    use_new: "采用新值",
    inherit_old: "保留本地",
    manual: "手工处理",
    pin: "钉住",
    clear_pin: "取消钉住",
    drop: "确认移除",
  };

  /* 过滤维度：新增/删除/冲突/固定值/类型/单位/需人工/未变。
   * 顺序按"越可能需要人动手越靠前"。 */
  var FILTERS = [
    { key: "all", label: "全部" },
    { key: "conflict", label: "冲突" },
    { key: "manual", label: "需人工" },
    { key: "pinned", label: "固定值" },
    { key: "new", label: "新增" },
    { key: "removed", label: "删除" },
    { key: "type_changed", label: "类型" },
    { key: "unit_changed", label: "单位" },
    { key: "same", label: "未变" },
  ];

  function valueText(value) {
    if (MC.configUi && MC.configUi.valueText) return MC.configUi.valueText(value);
    if (!value) return "—";
    return value.unit ? value.text + " " + value.unit : String(value.text);
  }

  function entryValue(entry) {
    return entry && entry.value ? entry.value : null;
  }

  /** 分类：一行可以有多个标签（既是冲突、又是单位变了）。 */
  function classify(item) {
    var tags = [];
    var source = item || {};
    if (source.action === "conflict") {
      tags.push("conflict");
      if ((source.reasons || []).indexOf("pin_needs_confirm") >= 0) tags.push("pinned");
    }
    if (source.action === "manual") tags.push("manual");
    if (source.action === "drop") tags.push("removed");
    var ours = entryValue(source.ours);
    var theirs = entryValue(source.theirs);
    var base = entryValue(source.base);
    if (!base && (theirs || ours)) tags.push("new");
    if (ours && theirs && ours.type && theirs.type && ours.type !== theirs.type) tags.push("type_changed");
    if (ours && theirs && (ours.unit || null) !== (theirs.unit || null)) tags.push("unit_changed");
    if (source.action === "keep") tags.push("same");
    return tags;
  }

  function matchFilter(item, key) {
    if (!key || key === "all") return true;
    return classify(item).indexOf(key) >= 0;
  }

  function countByFilter(items) {
    var counts = {};
    FILTERS.forEach(function (filter) {
      counts[filter.key] = (items || []).filter(function (item) {
        return matchFilter(item, filter.key);
      }).length;
    });
    return counts;
  }

  function rowModel(item) {
    var source = item || {};
    var tags = classify(source);
    return {
      parameterId: source.parameter_id,
      name: source.name || source.parameter_id || "",
      action: source.action,
      actionLabel: ACTION_LABELS[source.action] || source.action || "—",
      decision: source.decision || "",
      decisionLabel: DECISION_LABELS[source.decision || ""] || "待决定",
      baseText: valueText(entryValue(source.base)),
      oursText: valueText(entryValue(source.ours)),
      theirsText: valueText(entryValue(source.theirs)),
      candidateText: valueText(source.candidate),
      tags: tags,
      tagLabels: tags.map(function (tag) {
        var found = FILTERS.filter(function (filter) { return filter.key === tag; })[0];
        return found ? found.label : tag;
      }),
      reasons: source.reasons || [],
      allowed: source.allowed || [],
      blockers: source.blockers || [],
      needsDecision: source.action === "conflict",
    };
  }

  /** 某一行当前该算"已决议"还是"仍待决"。 */
  function effectiveDecision(item, decisions) {
    var picked = (decisions || {})[item.parameter_id];
    return picked || item.decision || "";
  }

  /** 一键填：只处理**不需要人判断**的行（非冲突、非人工）。 */
  function bulkResolve(items, decisions) {
    var next = Object.assign({}, decisions || {});
    (items || []).forEach(function (item) {
      if (item.action === "conflict" || item.action === "manual") return;
      if (next[item.parameter_id]) return;
      if (item.decision) next[item.parameter_id] = item.decision;
    });
    return next;
  }

  function pendingOf(plan, decisions) {
    var pending = [];
    ((plan && plan.items) || []).forEach(function (item) {
      if (item.action === "conflict" && !effectiveDecision(item, decisions)) {
        pending.push(item.parameter_id);
      }
      if (item.action === "manual" && effectiveDecision(item, decisions) !== "drop") {
        pending.push(item.parameter_id);
      }
    });
    return pending;
  }

  /** 能不能落地：三方齐 + 无阻断 + 无待决。理由要说人话。 */
  function canApply(plan, decisions) {
    if (!plan) return { allowed: false, reason: "先算一次合并计划" };
    if (plan.three_way === false) {
      return { allowed: false, reason: "没有共同基准（三方不齐），只能新建，不能一键合并发布" };
    }
    if ((plan.blockers || []).length) {
      var dropped = (plan.items || []).filter(function (item) {
        return effectiveDecision(item, decisions) === "drop";
      }).map(function (item) { return item.parameter_id; });
      var left = (plan.blockers || []).filter(function (blocker) {
        return dropped.indexOf(blocker.parameter_id) < 0;
      });
      if (left.length) {
        return { allowed: false, reason: "还有 " + left.length + " 个阻断项需要处理" };
      }
    }
    var pending = pendingOf(plan, decisions);
    if (pending.length) return { allowed: false, reason: "还有 " + pending.length + " 项没有决议" };
    return { allowed: true, reason: "" };
  }

  function summaryText(plan) {
    if (!plan) return "尚未计算";
    var counts = { from_new: 0, keep: 0, conflict: 0, manual: 0, drop: 0 };
    (plan.items || []).forEach(function (item) {
      counts[item.action] = (counts[item.action] || 0) + 1;
    });
    var parts = [];
    if (counts.from_new) parts.push("跟随新版 " + counts.from_new);
    if (counts.keep) parts.push("保持 " + counts.keep);
    if (counts.conflict) parts.push("冲突 " + counts.conflict);
    if (counts.manual) parts.push("需人工 " + counts.manual);
    if (counts.drop) parts.push("移除 " + counts.drop);
    return parts.length ? parts.join(" · ") : "没有需要处理的参数";
  }

  MC.upgradeUi = {
    ACTION_LABELS: ACTION_LABELS,
    DECISION_LABELS: DECISION_LABELS,
    FILTERS: FILTERS,
    valueText: valueText,
    classify: classify,
    matchFilter: matchFilter,
    countByFilter: countByFilter,
    rowModel: rowModel,
    effectiveDecision: effectiveDecision,
    bulkResolve: bulkResolve,
    pendingOf: pendingOf,
    canApply: canApply,
    summaryText: summaryText,
  };

  /* ===================== 视图 ===================== */

  var MODES = [
    { key: "common", label: "通用层升版", hint: "旧出口默认 / 旧通用 / 新出口默认 三方合并" },
    { key: "model", label: "机型升版", hint: "旧共同基准 / 旧机型值 / 新通用 三方合并" },
    { key: "rebase", label: "同版本刷新", hint: "上游通用出了新修订，机型显式跟进" },
  ];

  MC.upgradeUi.render = function (app, host, ctx) {
    var pid = (ctx && ctx.pid) || app.pid;
    var S = {
      mode: "common",
      releases: [],
      releaseId: "",
      plan: null,
      decisions: {},
      filter: "all",
      busy: false,
      error: null,
    };

    var headHost = h("div");
    var planHost = h("div");
    var footerHost = h("div");

    function modeHint() {
      var found = MODES.filter(function (mode) { return mode.key === S.mode; })[0];
      return found ? found.hint : "";
    }

    function drawHead() {
      var tabs = MODES.map(function (mode) {
        var active = S.mode === mode.key;
        return h(
          "button.btn.btn-sm" + (active ? ".btn-primary" : ""),
          {
            type: "button",
            "aria-pressed": active ? "true" : "false",
            onclick: function () {
              if (S.mode === mode.key) return;
              S.mode = mode.key;
              S.plan = null;
              S.decisions = {};
              drawHead();
              if (S.mode === "rebase") compute();
              else drawPlan();
            },
          },
          mode.label
        );
      });
      var releaseSelect = h(
        "select.input",
        { "aria-label": "目标版本", disabled: S.mode === "rebase" },
        h("option", { value: "" }, S.releases.length ? "选择目标版本" : "（没有可用的版本）"),
        S.releases.map(function (item) {
          return h("option", { value: item.id }, item.version_label || item.id);
        })
      );
      releaseSelect.value = S.releaseId;
      releaseSelect.addEventListener("change", function () {
        S.releaseId = releaseSelect.value;
        S.plan = null;
        S.decisions = {};
        drawPlan();
        drawFooter();
      });

      var run = h("button.btn.btn-sm.btn-primary", { type: "button" }, icon("refresh"), "计算合并");
      run.disabled = S.mode !== "rebase" && !S.releaseId;
      run.addEventListener("click", function () {
        MC.withBusy(run, function () {
          return compute();
        });
      });

      util.replace(
        headHost,
        h(
          "div",
          h("div.row", { style: "gap:6px;flex-wrap:wrap;margin-bottom:8px" }, tabs),
          h("p.small.muted", modeHint()),
          h(
            "div.row",
            { style: "gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px" },
            releaseSelect,
            run
          )
        )
      );
    }

    function compute() {
      S.error = null;
      util.replace(planHost, MC.stateBlock({ kind: "loading", title: "正在计算三方合并…" }));
      var promise;
      if (S.mode === "common") {
        promise = MC.configApi.upgradeCommonPlan({ release_id: S.releaseId });
      } else if (S.mode === "model") {
        promise = MC.configApi.upgradeModelPlan(pid, { release_id: S.releaseId });
      } else {
        promise = MC.configApi.upgradeRebasePlan(pid);
      }
      return promise.then(
        function (plan) {
          S.plan = plan;
          S.decisions = bulkResolve(plan.items, {});
          drawPlan();
          drawFooter();
          return plan;
        },
        function (err) {
          var info = MC.configApi.failure(err) || {};
          S.plan = null;
          S.error = info;
          util.replace(planHost, blockFor(info));
          drawFooter();
          throw err;
        }
      ).catch(function (err) {
        // withBusy 需要 reject 才能解除忙碌，但这里已经画了错误块，不再向上抛
        return null;
      });
    }

    function blockFor(info) {
      if (info.kind === "disabled") {
        return MC.stateBlock({
          kind: "empty",
          icon: "settings",
          title: "配置管理未开启",
          desc: "服务端特性门禁处于关闭状态。由管理员设置 MCHECKER_CONFIG_MANAGEMENT=on 并重启服务后可用。",
        });
      }
      if (info.kind === "stale" || info.code === "PLAN_STALE") {
        return MC.stateBlock({
          kind: "error",
          icon: "alert",
          title: "计划已过期",
          desc: info.message || "相关对象在你确认之后发生了变化，重新计算一次。",
          actions: [h("button.btn.btn-sm", { type: "button", onclick: function () { compute(); } }, "重新计算")],
        });
      }
      return MC.stateBlock({
        kind: "error",
        icon: "alert",
        title: "无法计算合并计划",
        desc: info.message || "服务端返回了未预期的响应。",
        actions: [h("button.btn.btn-sm", { type: "button", onclick: function () { compute(); } }, "重试")],
      });
    }

    function drawPlan() {
      if (!S.plan) {
        util.replace(
          planHost,
          MC.stateBlock({
            kind: "empty",
            icon: "upload",
            title: S.mode === "rebase" ? "点「计算合并」看上游有没有新修订" : "先选目标版本再计算",
            desc: "升级计划只出候选，不会自己发布；落地产出的是基线草稿，仍需走变更单批准。",
          })
        );
        return;
      }
      var plan = S.plan;
      var counts = countByFilter(plan.items || []);
      var chips = h(
        "div.row",
        { style: "gap:6px;flex-wrap:wrap;margin-bottom:10px" },
        FILTERS.map(function (filter) {
          var active = S.filter === filter.key;
          return h(
            "button.btn.btn-sm" + (active ? ".btn-primary" : ""),
            {
              type: "button",
              "aria-pressed": active ? "true" : "false",
              disabled: counts[filter.key] === 0 && filter.key !== "all",
              onclick: function () {
                S.filter = filter.key;
                drawPlan();
              },
            },
            filter.label + " " + counts[filter.key]
          );
        })
      );

      var notice = plan.three_way === false
        ? h(
            "div.banner.banner-warning",
            { style: "margin-bottom:10px" },
            icon("alert"),
            h("div.banner-body", "没有共同基准：这是**新建**而不是三方合并，一键合并发布已禁用。")
          )
        : null;

      var rows = (plan.items || [])
        .filter(function (item) { return matchFilter(item, S.filter); })
        .map(function (item) { return drawRow(item); });

      var table = rows.length
        ? h(
            "div.table-wrap",
            h(
              "table.table",
              h(
                "thead",
                h(
                  "tr",
                  h("th", "参数"),
                  h("th", "旧共同基准"),
                  h("th", "旧本地值"),
                  h("th", "新上游"),
                  h("th", "候选值"),
                  h("th", "状态"),
                  h("th", "决定")
                )
              ),
              h("tbody", rows)
            )
          )
        : MC.stateBlock({
            kind: "empty",
            icon: "search",
            title: "这一组没有参数",
            desc: "换个过滤条件看看。",
          });

      var warns = (plan.warnings || []).map(function (text) {
        return h("li", text);
      });

      util.replace(
        planHost,
        h(
          "div",
          h("p.small.muted", summaryText(plan)),
          notice,
          warns.length
            ? h(
                "div.banner.banner-neutral",
                { style: "margin-bottom:10px" },
                icon("info"),
                h("div.banner-body", h("ul", { style: "margin:0;padding-left:18px" }, warns))
              )
            : null,
          chips,
          table
        )
      );
    }

    function drawRow(item) {
      var row = rowModel(item);
      var decision = effectiveDecision(item, S.decisions);
      var options = row.allowed.length ? row.allowed : ["use_new", "inherit_old", "manual", "pin", "clear_pin"];
      if (item.action === "manual") options = ["drop", "use_new", "inherit_old"];

      var select = h(
        "select.input",
        { "aria-label": "决议：" + row.name },
        h("option", { value: "" }, row.needsDecision ? "待决定" : "按建议"),
        options.map(function (key) {
          return h("option", { value: key }, DECISION_LABELS[key] || key);
        })
      );
      select.value = decision && options.indexOf(decision) >= 0 ? decision : "";
      select.addEventListener("change", function () {
        if (select.value) S.decisions[item.parameter_id] = select.value;
        else delete S.decisions[item.parameter_id];
        drawFooter();
      });

      var tagLine = h(
        "div.small.muted",
        row.tagLabels.join(" · ") || "—"
      );

      var blockerLine = row.blockers.length
        ? h(
            "div.small",
            { style: "color:var(--c-danger-text)" },
            row.blockers.map(function (blocker) { return blocker.message || blocker.code; }).join("；")
          )
        : null;

      return h(
        "tr",
        h("td", h("div", row.name), h("div.small.muted", h("code.small", row.parameterId))),
        h("td", h("div", row.baseText)),
        h("td", h("div", row.oursText)),
        h("td", h("div", row.theirsText)),
        h("td", h("div", row.candidateText)),
        h("td", h("div", row.actionLabel), tagLine, blockerLine),
        h("td", select)
      );
    }

    function drawFooter() {
      var state = canApply(S.plan, S.decisions);
      var bulk = h("button.btn.btn-sm", { type: "button" }, "无冲突项按建议决定");
      bulk.disabled = !S.plan;
      bulk.addEventListener("click", function () {
        S.decisions = bulkResolve(S.plan.items, S.decisions);
        drawPlan();
        drawFooter();
        MC.toast.info("已按建议填好无冲突项", "冲突与需人工的项仍要你自己定");
      });

      var apply = h("button.btn.btn-sm.btn-primary", { type: "button" }, "生成升级草稿");
      apply.disabled = !state.allowed;
      apply.title = state.reason || "按当前决议建一条基线草稿";
      apply.addEventListener("click", function () {
        MC.withBusy(apply, function () {
          return submit().then(
            function (res) {
              MC.toast.success(
                "已生成升级草稿",
                "基线 " + ((res.item && res.item.id) || "") + " 仍是草稿，需要走变更单批准才发布"
              );
              S.plan = null;
              S.decisions = {};
              util.replace(
                planHost,
                MC.stateBlock({
                  kind: "empty",
                  icon: "check",
                  title: "草稿已生成",
                  desc: "接下来在「配置变更」分段把它提交并批准；批准即发布。",
                })
              );
              drawFooter();
              return res;
            },
            function (err) {
              var info = MC.configApi.failure(err) || {};
              if (info.code === "PLAN_STALE") {
                MC.toast.error("计划已过期", info.message || "重新计算一次");
              } else {
                MC.toast.error("生成失败", info.message || "");
              }
              throw err;
            }
          );
        });
      });

      var hint = h("span.small.muted", state.reason || "决议齐了就能生成草稿");

      util.replace(
        footerHost,
        h("div.row", { style: "gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px" }, bulk, apply, hint)
      );
    }

    function submit() {
      var plan = S.plan;
      var payload = { decisions: S.decisions, reason: "跨版本升级" };
      if (S.mode === "common") {
        payload.release_id = plan.target.release_id;
        payload.variant_id = plan.target.variant_id;
        return MC.configApi.createCommonUpgrade(payload);
      }
      if (S.mode === "model") {
        payload.release_id = plan.target.release_id;
        payload.expected = {
          model_revision: (plan.previous || {}).model_revision,
          parent_revision: (plan.previous || {}).upstream_revision,
        };
        return MC.configApi.createModelUpgrade(pid, payload);
      }
      payload.expected = {
        model_revision: (plan.previous || {}).model_revision,
        parent_revision: (plan.previous || {}).upstream_revision,
      };
      return MC.configApi.createRebase(pid, payload);
    }

    util.replace(
      host,
      h(
        "div.panel",
        h("div.panel-header", h("h2", "版本升级"), h("span.muted.small", "候选值由服务端算，前端只摆位置")),
        h("div", { style: "padding:14px 16px" }, headHost, planHost, footerHost)
      )
    );

    drawHead();
    drawPlan();
    drawFooter();
    MC.configApi.releases().then(
      function (body) {
        S.releases = (body.items || []).slice().reverse();
        if (S.releases.length) S.releaseId = S.releases[0].id;
        drawHead();
      },
      function () {
        S.releases = [];
        drawHead();
      }
    );
  };
})(window);
