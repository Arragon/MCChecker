/*
 * 经验上提 / 历史回滚 / 跨变体迁移 / 出口补发（M7-05、M7-06）。
 *
 * 四件事的共同点：**都只出计划，落地产出的都是新对象**。所以界面上一律是
 * "先看影响 → 再点落地"，没有"点了就改"的快捷路径。
 *
 * 四条界面规矩：
 *
 * 1. **不假装**：设备层（M7-07/08）按计划延期，界面上直接写"未启用"，
 *    不画一个点了会 400 的按钮当摆设。
 * 2. **不吞阻断**：服务端给的 `blockers` 逐条列出（参数名 + 原因），
 *    有阻断就禁用落地按钮，并把原因写在按钮旁边。
 * 3. **不以颜色替代文本**：每个状态都带文字（"可上提""排除：单机标定"），
 *    颜色只是辅助。
 * 4. **历史不许被改写**：回滚按钮的文案是「回滚为新基线」，不是「回退」——
 *    点下去是新建一条已批准基线，旧修订与旧产物照旧可读。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var EXCLUDE_LABELS = {
    not_local: "本地没改过",
    observation_sourced: "来自现场采集",
    device_calibration: "来自单机标定",
    factory_default: "就是出口默认",
    unknown_source: "来源不明",
    already_same: "与目标相同",
  };

  var BLOCKER_LABELS = {
    release_mismatch: "版本不一致",
    evidence_revoked: "证据已撤销",
    nothing_to_promote: "没有可上提的参数",
    unknown_parameter: "目标没有这个参数",
    incomparable: "单位换不过去",
  };

  function valueText(value) {
    if (!value) return "—";
    var text = value.text === undefined || value.text === null ? "" : String(value.text);
    if (!text) return "—";
    return value.unit ? text + " " + value.unit : text;
  }

  function label(code, map) {
    return (map && map[code]) || code || "未知";
  }

  /* -- 上提 ---------------------------------------------------------------- */

  function promoteRow(row) {
    var state = row.eligible
      ? "可上提"
      : "排除：" + (row.excluded_message || label(row.excluded_reason, EXCLUDE_LABELS));
    return {
      id: row.parameter_id,
      name: row.name || row.parameter_id,
      from: valueText(row.from),
      to: valueText(row.to),
      intent: row.intent || "",
      eligible: !!row.eligible,
      stateText: state,
      tone: row.eligible ? "ok" : "muted",
    };
  }

  function promoteSummary(plan) {
    if (!plan) return "";
    var total = (plan.items || []).length;
    var ok = (plan.eligible || []).length;
    var blocked = (plan.blockers || []).length;
    var text = "共 " + total + " 项，可上提 " + ok + " 项";
    if (total > ok) text += "，排除 " + (total - ok) + " 项";
    if (blocked) text += "；有 " + blocked + " 个阻断项，不能建变更单";
    return text;
  }

  function blockerLines(blockers) {
    return (blockers || []).map(function (item) {
      var who = item.parameter_id ? "（" + item.parameter_id + "）" : "";
      return label(item.code, BLOCKER_LABELS) + who + "：" + (item.message || "");
    });
  }

  function deviceNotice(deviceBaselines) {
    var text =
      "设备层未启用（M7-07/08 按计划延期）：单机标定值不能上提到机型层。" +
      "机型级配置完整可用；需要设备级例外时先启用设备目录。";
    if (typeof deviceBaselines === "number") text += "当前设备基线 " + deviceBaselines + " 条。";
    return { level: "warn", text: text };
  }

  /* -- 回滚 ---------------------------------------------------------------- */

  function restoreRow(row) {
    return {
      id: row.parameter_id,
      name: row.name || row.parameter_id,
      from: valueText(row.from),
      to: valueText(row.to),
      intentChanged: (row.from_intent || "") !== (row.to_intent || ""),
      stateText: row.value_changed === false ? "只改意图" : "值变化",
    };
  }

  function restoreSummary(plan) {
    if (!plan) return "";
    if (!plan.changed) return "该修订与当前值完全一致，回滚不会改变任何参数。";
    return "回滚会把 " + plan.changed + " 个参数改回 " + plan.source.revision + " 的值";
  }

  /* -- 迁移 ---------------------------------------------------------------- */

  function migrationNotice(plan) {
    if (!plan) return null;
    var blockers = plan.blockers || [];
    if (blockers.length) {
      return {
        level: "warn",
        text:
          "有 " + blockers.length + " 个参数不能迁移（" +
          blockerLines(blockers).join("；") + "），先补齐目标定义再迁移。",
      };
    }
    if (!(plan.items || []).length) {
      return { level: "warn", text: "这条基线没有可迁移的参数。" };
    }
    return {
      level: "info",
      text: "按 parameter_id 对齐，" + plan.items.length + " 个参数可迁移；创建的是目标变体的草稿，不覆盖源基线。",
    };
  }

  function migrationRow(row) {
    return {
      id: row.parameter_id,
      name: row.name || row.parameter_id,
      from: valueText(row.from),
      to: valueText(row.to),
      stateText: row.converted ? "已换算单位" : "同单位照搬",
    };
  }

  /* -- 补发 ---------------------------------------------------------------- */

  function reissueNotice(plan) {
    if (!plan) return null;
    var text;
    if (!plan.baseline_reference_unchanged) {
      return { level: "warn", text: "补发改变了已发布基线引用的批次，这不该发生，请联系维护者。" };
    }
    if (!plan.current_batch) {
      return { level: "warn", text: "该版本当前没有默认出口批次，无法比较补发影响。" };
    }
    if (plan.template && plan.template.in_current_batch) {
      text = "基线的模板快照仍在当前出口批次里，补发没有替换这份模板。";
    } else {
      text = "基线的模板快照已不在当前批次：补发换了出口文件，但基线引用没被改动，要不要跟进由你决定。";
    }
    if (plan.changed) text += "与补发后的出口默认相比，" + plan.changed + " 个参数不同。";
    return { level: plan.changed ? "warn" : "info", text: text };
  }

  function shortId(id) {
    if (!id) return "—";
    return String(id);
  }

  /* -- 视图 ---------------------------------------------------------------- */

  MC.lifecycleUi = {
    promoteRow: promoteRow,
    promoteSummary: promoteSummary,
    blockerLines: blockerLines,
    deviceNotice: deviceNotice,
    restoreRow: restoreRow,
    restoreSummary: restoreSummary,
    migrationNotice: migrationNotice,
    migrationRow: migrationRow,
    reissueNotice: reissueNotice,
    EXCLUDE_LABELS: EXCLUDE_LABELS,
    BLOCKER_LABELS: BLOCKER_LABELS,

    render: function (app, host, ctx) {
      var pid = (ctx && ctx.pid) || app.pid;
      var S = { baseline: "", baselines: [], variants: [] };

      var baselineSelect = h("select.input", { "aria-label": "选择基线" });
      var variantSelect = h("select.input", { "aria-label": "选择目标模板变体" });
      var revisionSelect = h("select.input", { "aria-label": "选择历史修订" });

      var promoteHost = h("div");
      var restoreHost = h("div");
      var migrateHost = h("div");
      var reissueHost = h("div");

      function card(title, desc, controls, body) {
        return h(
          "section.card",
          { style: "margin-bottom:14px" },
          h("div.card-head", h("h2", title), h("p.small.muted", desc)),
          controls ? h("div.row", { style: "gap:8px;flex-wrap:wrap;margin:8px 0" }, controls) : null,
          body
        );
      }

      function banner(notice) {
        if (!notice) return null;
        var cls = notice.level === "warn" ? "banner-warning" : "banner-neutral";
        return h(
          "div.banner." + cls,
          icon(notice.level === "warn" ? "alert" : "info"),
          h("div.banner-body", notice.text)
        );
      }

      /* -- 上提 -- */
      var promoteBody = h("div", h("p.small.muted", "还没算过。上提不是自动的：点一次，看一次影响。"));
      var promoteBtn = h(
        "button.btn.btn-sm.btn-primary",
        { type: "button" },
        icon("refresh"),
        "计算机型→通用的上提"
      );

      promoteBtn.addEventListener("click", function () {
        MC.withBusy(promoteBtn, function () {
          return MC.configApi.promoteCommonPlan(pid).then(
            function (plan) {
              drawPromote(plan);
              return plan;
            },
            function (err) {
              util.replace(promoteBody, banner({ level: "warn", text: (MC.configApi.failure(err) || {}).message || "计算失败" }));
            }
          );
        });
      });

      function drawPromote(plan) {
        var rows = (plan.items || []).map(promoteRow);
        var createBtn = h(
          "button.btn.btn-sm.btn-primary",
          { type: "button", disabled: !plan.can_apply },
          "生成上提变更单"
        );
        createBtn.addEventListener("click", function () {
          if (!plan.can_apply) return;
          MC.withBusy(createBtn, function () {
            return MC.configApi.createPromoteCommon(pid, {}).then(
              function (res) {
                MC.toast.success("已建上提变更单", "等管理员批准后才动通用基线");
                drawPromote(plan);
                return res;
              },
              function (err) {
                MC.toast.error("建变更单失败", (MC.configApi.failure(err) || {}).message || "");
              }
            );
          });
        });

        var blockers = blockerLines(plan.blockers);
        util.replace(
          promoteHost,
          h("p.small", promoteSummary(plan)),
          rows.length
            ? h(
                "div.table-wrap",
                h(
                  "table.table",
                  h("thead", h("tr", h("th", "参数"), h("th", "通用现值"), h("th", "机型值"), h("th", "状态"))),
                  h(
                    "tbody",
                    rows.map(function (row) {
                      return h(
                        "tr",
                        h("td", h("div", row.name), h("div.small.muted", row.id)),
                        h("td", row.from),
                        h("td", row.to),
                        h("td", h("span.badge." + row.tone, row.stateText))
                      );
                    })
                  )
                )
              )
            : h("p.small.muted", "没有可上提的参数。"),
          blockers.length
            ? h("div.banner.banner-warning", { style: "margin-top:10px" }, icon("alert"), h("div.banner-body", h("ul", { style: "margin:0;padding-left:18px" }, blockers.map(function (line) { return h("li", line); }))))
            : null,
          h("div.row", { style: "gap:8px;margin-top:10px" }, createBtn, h("span.small.muted", plan.can_apply ? "批准后才发布新通用修订；机型基线不会被改写。" : "有阻断项或没有可上提的参数，暂时不能建变更单。"))
        );
      }

      /* -- 回滚 / 迁移 / 补发 -- */
      var restoreBtn = h("button.btn.btn-sm", { type: "button" }, "计算回滚影响");
      var migrateBtn = h("button.btn.btn-sm", { type: "button" }, "计算迁移计划");
      var reissueBtn = h("button.btn.btn-sm", { type: "button" }, "查看补发影响");

      function currentBaseline() {
        for (var i = 0; i < S.baselines.length; i++) {
          if (S.baselines[i].id === S.baseline) return S.baselines[i];
        }
        return null;
      }

      function loadRevisions() {
        if (!S.baseline) {
          util.replace(revisionSelect, h("option", { value: "" }, "（先选基线）"));
          return;
        }
        MC.configApi.baselineDetail(S.baseline).then(
          function (body) {
            var revisions = (body.revisions || []).slice().reverse();
            util.replace(
              revisionSelect,
              revisions.map(function (rev) {
                var head = body.item && body.item.revision === rev;
                return h("option", { value: rev }, rev + (head ? "（当前）" : ""));
              })
            );
            if (revisions.length) revisionSelect.value = revisions[0];
          },
          function () {
            util.replace(revisionSelect, h("option", { value: "" }, "（读取修订失败）"));
          }
        );
      }

      restoreBtn.addEventListener("click", function () {
        var rev = revisionSelect.value;
        if (!S.baseline || !rev) return;
        MC.withBusy(restoreBtn, function () {
          return MC.configApi.restorePlan(S.baseline, { revision: rev }).then(
            function (plan) {
              drawRestore(plan);
              return plan;
            },
            function (err) {
              util.replace(restoreHost, banner({ level: "warn", text: (MC.configApi.failure(err) || {}).message || "计算失败" }));
            }
          );
        });
      });

      function drawRestore(plan) {
        var rows = (plan.items || []).map(restoreRow);
        var applyBtn = h("button.btn.btn-sm.btn-primary", { type: "button" }, "回滚为新基线");
        applyBtn.addEventListener("click", function () {
          MC.withBusy(applyBtn, function () {
            return MC.configApi
              .restoreBaseline(S.baseline, {
                revision: plan.source.revision,
                expected_head: plan.baseline.head_revision,
              })
              .then(
                function (res) {
                  MC.toast.success("已回滚为新基线", "旧修订与旧产物照旧可读（新对象 " + shortId(res.item.id) + "）");
                  loadRevisions();
                  return res;
                },
                function (err) {
                  MC.toast.error("回滚失败", (MC.configApi.failure(err) || {}).message || "");
                }
              );
          });
        });
        util.replace(
          restoreHost,
          h("p.small", restoreSummary(plan)),
          rows.length
            ? h(
                "div.table-wrap",
                h(
                  "table.table",
                  h("thead", h("tr", h("th", "参数"), h("th", "当前"), h("th", "回滚到"), h("th", "差异"))),
                  h(
                    "tbody",
                    rows.map(function (row) {
                      return h(
                        "tr",
                        h("td", row.name),
                        h("td", row.from),
                        h("td", row.to),
                        h("td", row.stateText)
                      );
                    })
                  )
                )
              )
            : h("p.small.muted", "该修订与当前值一致。"),
          h("div.row", { style: "gap:8px;margin-top:10px" }, applyBtn, h("span.small.muted", "回滚新建一条已批准基线，不改写历史修订。"))
        );
      }

      migrateBtn.addEventListener("click", function () {
        var target = variantSelect.value;
        if (!S.baseline || !target) return;
        MC.withBusy(migrateBtn, function () {
          return MC.configApi.migrationPlan(S.baseline, { target_variant_id: target }).then(
            function (plan) {
              drawMigration(plan);
              return plan;
            },
            function (err) {
              util.replace(migrateHost, banner({ level: "warn", text: (MC.configApi.failure(err) || {}).message || "计算失败" }));
            }
          );
        });
      });

      function drawMigration(plan) {
        var rows = (plan.items || []).map(migrationRow);
        var applyBtn = h(
          "button.btn.btn-sm.btn-primary",
          { type: "button", disabled: !plan.can_apply },
          "建目标变体草稿"
        );
        applyBtn.addEventListener("click", function () {
          if (!plan.can_apply) return;
          MC.withBusy(applyBtn, function () {
            return MC.configApi
              .createMigration(S.baseline, { target_variant_id: plan.target.variant_id })
              .then(
                function (res) {
                  MC.toast.success("已建迁移草稿", "源基线未被覆盖（新对象 " + shortId(res.item.id) + "）");
                  return res;
                },
                function (err) {
                  MC.toast.error("迁移失败", (MC.configApi.failure(err) || {}).message || "");
                }
              );
          });
        });
        util.replace(
          migrateHost,
          banner(migrationNotice(plan)),
          rows.length
            ? h(
                "div.table-wrap",
                h(
                  "table.table",
                  h("thead", h("tr", h("th", "参数"), h("th", "源值"), h("th", "目标值"), h("th", "说明"))),
                  h(
                    "tbody",
                    rows.map(function (row) {
                      return h("tr", h("td", row.name), h("td", row.from), h("td", row.to), h("td", row.stateText));
                    })
                  )
                )
              )
            : null,
          h("div.row", { style: "gap:8px;margin-top:10px" }, applyBtn)
        );
      }

      reissueBtn.addEventListener("click", function () {
        if (!S.baseline) return;
        MC.withBusy(reissueBtn, function () {
          return MC.configApi.reissuePlan(S.baseline).then(
            function (plan) {
              drawReissue(plan);
              return plan;
            },
            function (err) {
              util.replace(reissueHost, banner({ level: "warn", text: (MC.configApi.failure(err) || {}).message || "读取失败" }));
            }
          );
        });
      });

      function drawReissue(plan) {
        var rows = (plan.items || []).map(function (row) {
          return { name: row.name || row.parameter_id, from: valueText(row.from), to: valueText(row.to) };
        });
        var applyBtn = h(
          "button.btn.btn-sm",
          { type: "button", disabled: !(plan.current_batch && plan.changed) },
          "按补发刷新草稿"
        );
        applyBtn.addEventListener("click", function () {
          MC.withBusy(applyBtn, function () {
            return MC.configApi.createReissue(S.baseline, {}).then(
              function (res) {
                MC.toast.success("已按补发新建草稿", "原基线仍引用旧批次（新对象 " + shortId(res.item.id) + "）");
                return res;
              },
              function (err) {
                MC.toast.error("刷新失败", (MC.configApi.failure(err) || {}).message || "");
              }
            );
          });
        });
        util.replace(
          reissueHost,
          banner(reissueNotice(plan)),
          rows.length
            ? h(
                "div.table-wrap",
                h(
                  "table.table",
                  h("thead", h("tr", h("th", "参数"), h("th", "基线值"), h("th", "补发后出口默认"))),
                  h("tbody", rows.map(function (row) { return h("tr", h("td", row.name), h("td", row.from), h("td", row.to)); }))
                )
              )
            : null,
          h("div.row", { style: "gap:8px;margin-top:10px" }, applyBtn, h("span.small.muted", "补发不改已发布基线的批次引用；要不要跟进是另一个动作。"))
        );
      }

      baselineSelect.addEventListener("change", function () {
        S.baseline = baselineSelect.value;
        util.replace(restoreHost, null);
        util.replace(migrateHost, null);
        util.replace(reissueHost, null);
        loadRevisions();
      });

      function load() {
        util.replace(promoteHost, MC.stateBlock({ kind: "loading", title: "正在读取…" }));
        var bases = MC.configApi.baselines({}).then(
          function (body) { return { ok: true, body: body }; },
          function (err) { return { ok: false, err: MC.configApi.failure(err) }; }
        );
        var vars = MC.configApi.variants({}).then(
          function (body) { return { ok: true, body: body }; },
          function (err) { return { ok: false, err: MC.configApi.failure(err) }; }
        );
        Promise.all([bases, vars]).then(function (pair) {
          if (!pair[0].ok) {
            util.replace(promoteHost, banner({ level: "warn", text: (pair[0].err && pair[0].err.message) || "读取基线失败" }));
            return;
          }
          S.baselines = pair[0].body.items || [];
          S.variants = pair[1].ok ? pair[1].body.items || [] : [];
          util.replace(
            baselineSelect,
            S.baselines.length
              ? S.baselines.map(function (item) {
                  return h("option", { value: item.id }, (item.scope || "") + " / " + item.id + "（" + (item.revision || "—") + "）");
                })
              : h("option", { value: "" }, "（还没有基线）")
          );
          if (S.baselines.length) {
            S.baseline = S.baseline || S.baselines[0].id;
            baselineSelect.value = S.baseline;
            loadRevisions();
          }
          util.replace(
            variantSelect,
            S.variants.length
              ? S.variants.map(function (item) {
                  return h("option", { value: item.id }, (item.family_id || "") + " / " + item.id);
                })
              : h("option", { value: "" }, "（还没有模板变体）")
          );
          util.replace(promoteHost, h("p.small.muted", "还没算过。上提不是自动的：点一次，看一次影响。"));
        });
      }

      util.replace(
        host,
        h(
          "div.panel",
          h(
            "div.panel-header",
            h("h2", "经验与历史"),
            h("span.muted.small", "四件事都只出计划；落地一律新增对象，不改写历史")
          ),
          h(
            "div",
            { style: "padding:14px 16px" },
            card(
              "经验上提（机型 → 通用）",
              "把机型层验证过的值提到通用层。只上提本地做出的决定：现场采集、单机标定、出口默认都不算经验。",
              [promoteBtn],
              h("div", promoteHost)
            ),
            card("设备 → 机型", "设备层当前未启用。", [], banner(deviceNotice())),
            card(
              "历史回滚",
              "把某个历史修订的内容变成一条新的已批准基线。旧修订与旧产物照旧可读。",
              [baselineSelect, revisionSelect, restoreBtn],
              h("div", restoreHost)
            ),
            card(
              "跨变体迁移",
              "参数按 parameter_id 对齐；目标定义里没有的参数、换算不了的单位一律阻断。",
              [variantSelect, migrateBtn],
              h("div", migrateHost)
            ),
            card(
              "出口补发",
              "同版补发之后：这条基线用的还是哪个模板？补发不会改已发布基线的批次引用。",
              [reissueBtn],
              h("div", reissueHost)
            )
          )
        )
      );

      load();
    },
  };
})(window);
