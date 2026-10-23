/*
 * 交付产物（M6-14）。
 *
 * 一份产物 = 一次"从已批准基线生成的目标文件"的留痕。这个视图只做四件事：
 * 列出来、生成、下载、看校验结果。三条约束：
 *
 * 1. **产物不可变**：下载走 `/raw`，读的是落盘 blob，重复下载同一串字节；
 *    视图不提供"重新生成覆盖旧产物"——改值要走新的变更单，产出新的基线修订。
 * 2. **同输入同产物**：重复点"生成"拿到的是已有产物（服务端返回 200 + reused），
 *    前端如实提示"已复用"，不说"生成成功"来冒充新产物。
 * 3. **不宣称已核验**：M6-14 的旧 `delivery_status` 仍以 `unverified` 为基线；
 *    M7-09 的人工登记四态另行挂在 `delivery` 上，页面优先显示登记结果，
 *    但「已声明成功」仍不等于「回读核验通过」。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  /* 交付状态词表。服务端目前只会给 unverified（D6 未解除），
   * 另外两个值先摆在这里：未来接入回读核验时不用再改一遍映射。 */
  var STATUS_LABELS = {
    unverified: "待验",
    verified: "已核验",
    failed: "核验失败",
  };

  var STATUS_HINTS = {
    unverified: "尚未在真实系统导入过，只保证「结构与落点值正确」",
    verified: "已在真实系统导入并记录操作者/环境/结果",
    failed: "真实系统导入后核验不通过",
  };

  /* ---- 交付四态与回读核验（M7-09/M7-10）词表：与服务端 config_delivery /
   * config_verify 的标签一一对应，前端不各自造词。 ---- */
  var RESULT_LABELS = {
    pending: "待导入",
    declared_ok: "已声明成功",
    failed: "失败",
    insufficient_evidence: "证据不足",
  };

  var CONCLUSION_LABELS = {
    verified: "核验通过",
    mismatch: "值不一致",
    incomplete: "证据不完整",
    version_mismatch: "版本不一致",
  };

  var ROW_LABELS = {
    matched: "一致",
    missing: "观测里没有",
    mismatched: "不一致",
    unobservable: "无法核验",
  };

  /* ===================== 纯函数（供测试与视图共用） ===================== */

  function resultLabel(result) {
    return RESULT_LABELS[result] || RESULT_LABELS.pending;
  }

  function resultTone(result) {
    if (result === "declared_ok") return "badge-success";
    if (result === "failed") return "badge-danger";
    if (result === "insufficient_evidence") return "badge-warning";
    return "badge-neutral";
  }

  /**
   * 登记表单的本地预检：与服务端硬规矩同序（服务端仍是最终裁决）。
   * 返回问题列表；空列表 = 可以提交。
   */
  function registrationIssues(form) {
    var data = form || {};
    var issues = [];
    var result = data.result || "";
    if (["declared_ok", "failed", "insufficient_evidence"].indexOf(result) < 0) {
      issues.push("先选登记结果（「待导入」是推导出来的，不能登记）");
      return issues;
    }
    if (result === "declared_ok" || result === "failed") {
      if (!String(data.executed_at || "").trim()) issues.push("登记成功/失败必须给出实际执行时间");
    }
    if (result === "declared_ok") {
      var hasEvidence =
        String(data.evidence_note || "").trim() || String(data.evidence_ref || "").trim();
      if (!hasEvidence) issues.push("登记成功必须带证据（说明或引用至少一项）；没有证据请登记为「证据不足」");
    }
    if (result === "failed" && !String(data.failure_reason || "").trim()) {
      issues.push("登记失败必须写失败原因");
    }
    if (result === "insufficient_evidence" && !String(data.missing_evidence || "").trim()) {
      issues.push("登记「证据不足」必须写清缺什么");
    }
    return issues;
  }

  /** 交付摘要 → 提示条：设备层关闭时"所有设备已完成"永远不可声称。 */
  function deliverySummaryNotice(summary) {
    if (!summary || !summary.total) {
      return {
        level: "info",
        text: "还没有人登记过这份产物的导入结果：当前是「待导入」，下载过不等于打过。",
      };
    }
    var counts = summary.counts || {};
    var text =
      "共登记 " + summary.total + " 次：成功 " + (counts.declared_ok || 0) +
      " · 失败 " + (counts.failed || 0) +
      " · 证据不足 " + (counts.insufficient_evidence || 0) +
      "；最近一次：" + resultLabel(summary.result) + "。";
    if (summary.can_claim_all_devices === false) {
      text += "设备层未启用：只能就登记过的对象给结论，不能断言「所有设备已完成」。";
    }
    return { level: (counts.failed || 0) > 0 ? "warn" : "info", text: text };
  }

  function conclusionLabel(conclusion) {
    return CONCLUSION_LABELS[conclusion] || "证据不完整";
  }

  function conclusionTone(conclusion) {
    if (conclusion === "verified") return "badge-success";
    if (conclusion === "mismatch") return "badge-danger";
    return "badge-warning";
  }

  /** 核验报告 → 一行摘要：逐态计数 + 结论 + 范围声明。 */
  function verifySummary(report) {
    if (!report) return "";
    var agg = report.aggregate || {};
    var text =
      "一致 " + (agg.matched || 0) +
      " · 观测里没有 " + (agg.missing || 0) +
      " · 不一致 " + (agg.mismatched || 0) +
      " · 无法核验 " + (agg.unobservable || 0) +
      "；结论：" + conclusionLabel(report.conclusion) + "。";
    if (report.scope_complete === false) {
      text += "观测里有节点没登记成参数，未纳入核验——不能宣传全量一致。";
    }
    return text;
  }

  function verifyRowModel(row) {
    var data = row || {};
    return {
      id: data.parameter_id || "",
      name: data.name || data.parameter_id || "",
      status: data.status || "missing",
      statusLabel: ROW_LABELS[data.status] || ROW_LABELS.missing,
      reason: data.reason || "",
    };
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || STATUS_LABELS.unverified;
  }

  function statusHint(status) {
    return STATUS_HINTS[status] || STATUS_HINTS.unverified;
  }

  function statusTone(status) {
    if (status === "verified") return "badge-success";
    if (status === "failed") return "badge-danger";
    return "badge-warning";
  }

  function shortSha(sha) {
    var text = String(sha || "");
    return text ? text.slice(0, 12) : "—";
  }

  function editCount(item) {
    return ((item && item.edits) || []).length;
  }

  /** 列表行模型：只保留表格要显示的字段。 */
  function rowModel(item) {
    var source = item || {};
    var size = Number(source.byte_size || 0);
    var delivery = source.delivery || null;
    var deliveryResult = delivery && delivery.result ? delivery.result : "";
    var legacyStatus = source.delivery_status || "unverified";
    return {
      id: source.id,
      filename: source.filename || source.id || "（未命名产物）",
      baselineId: source.baseline_id || "",
      revision: source.baseline_revision || "",
      generator: source.generator || "",
      edits: editCount(source),
      same: Number(source.same_count || 0),
      sizeText: size ? util.formatBytes(size) : "—",
      sha: String(source.blob_sha256 || ""),
      shaShort: shortSha(source.blob_sha256),
      status: deliveryResult || legacyStatus,
      statusLabel: deliveryResult ? resultLabel(deliveryResult) : statusLabel(legacyStatus),
      statusTone: deliveryResult ? resultTone(deliveryResult) : statusTone(legacyStatus),
      delivery: delivery,
      deliveryAttempts: delivery ? Number(delivery.attempts || 0) : 0,
      deliveryLabel: deliveryResult ? resultLabel(deliveryResult) : statusLabel(legacyStatus),
      createdAt: source.created_at || "",
      createdBy: source.created_by || "",
      ok: !(source.validation && source.validation.ok === false),
    };
  }

  /** 生成按钮的可用性判断：只有已批准的基线能生成，且要有写权限。 */
  function canGenerate(app, baseline) {
    if (!baseline) return { allowed: false, reason: "先选一条已批准的基线" };
    if (baseline.state !== "approved") return { allowed: false, reason: "只有已批准的基线能生成目标文件" };
    if (app && app.can && !app.can("config_registry_write")) {
      return { allowed: false, reason: "当前身份没有生成产物的权限" };
    }
    return { allowed: true, reason: "" };
  }

  /** 已批准基线 → 下拉选项。标签要能区分"通用层/机型层"与修订号。 */
  function baselineOptions(items) {
    return (items || []).map(function (item) {
      var scope = item.scope === "model" ? "机型" : item.scope === "device" ? "设备" : "通用";
      return {
        id: item.id,
        revision: item.revision || "",
        label:
          scope +
          " · " +
          (item.id || "") +
          (item.revision ? "（" + item.revision + "）" : "") +
          (item.profile_id ? " · " + item.profile_id : ""),
      };
    });
  }

  /** 顶部的交付说明：把 D6 缺口写成一句话，而不是藏在状态徽标里。 */
  function deliveryNotice(model) {
    var rows = model || [];
    if (!rows.length) {
      return { level: "info", text: "还没有交付产物，无从谈核验。" };
    }
    var pending = rows.filter(function (row) {
      return row.status === "unverified" || row.status === "pending";
    }).length;
    var failed = rows.filter(function (row) {
      return row.delivery && row.status === "failed";
    }).length;
    if (failed) {
      return {
        level: "warn",
        text: "有 " + failed + " 份产物登记为导入失败：请查看失败原因并重新登记；失败记录不会被覆盖。",
      };
    }
    if (!pending) {
      var declared = rows.filter(function (row) {
        return row.status === "declared_ok";
      }).length;
      return {
        level: "info",
        text: declared
          ? "全部产物都已声明导入成功；「已声明成功」仍需回读核验报告作为最终结论。"
          : "全部产物都已有导入结果登记。",
      };
    }
    return {
      level: "warn",
      text:
        "有 " +
        pending +
        " 份产物仍是「待验」：本系统只自证「可重新解析、结构与落点值正确」，" +
        "真实系统导入的操作者/环境/结果尚未登记（D6 缺口）。下载成功不等于验过。",
    };
  }

  MC.deliveryUi = {
    STATUS_LABELS: STATUS_LABELS,
    RESULT_LABELS: RESULT_LABELS,
    CONCLUSION_LABELS: CONCLUSION_LABELS,
    ROW_LABELS: ROW_LABELS,
    statusLabel: statusLabel,
    statusHint: statusHint,
    statusTone: statusTone,
    shortSha: shortSha,
    editCount: editCount,
    rowModel: rowModel,
    canGenerate: canGenerate,
    baselineOptions: baselineOptions,
    deliveryNotice: deliveryNotice,
    resultLabel: resultLabel,
    resultTone: resultTone,
    registrationIssues: registrationIssues,
    deliverySummaryNotice: deliverySummaryNotice,
    conclusionLabel: conclusionLabel,
    conclusionTone: conclusionTone,
    verifySummary: verifySummary,
    verifyRowModel: verifyRowModel,
  };

  /* ===================== 视图 ===================== */

  /**
   * 在文件页"配置管理"下面渲染交付分段。
   *
   * `ctx` 只用到 `pid`；基线是**全局已批准基线**，不按文件过滤——
   * 基线挂在"版本 + 机型/设备"上，不挂在逻辑文件上，硬按文件过滤会造出
   * "明明有基线却列不出来"的假空态。
   */
  MC.deliveryUi.render = function (app, host, ctx) {
    var pid = (ctx && ctx.pid) || app.pid;
    var S = { items: [], baselines: [], selected: "", current: null, loading: true };

    var listHost = h("div");
    var detailHost = h("div");
    /* 提示条要等取数完了才画：没加载完就说"全部已核验"是一句假话，
     * 空列表却说"有缺口"也是。加载中保持空白。 */
    var noticeHost = h("div");
    var select = h("select.input", { "aria-label": "选择已批准基线" });
    var genBtn = h("button.btn.btn-sm.btn-primary", { type: "button" }, icon("download"), "生成目标文件");
    var hint = h("span.small.muted");

    function renderNotice(rows) {
      if (!rows) {
        util.replace(noticeHost, null);
        return;
      }
      var notice = deliveryNotice(rows);
      var cls = notice.level === "warn" ? "banner-warning" : "banner-neutral";
      util.replace(
        noticeHost,
        h(
          "div.banner." + cls,
          { style: "margin-bottom:10px" },
          icon(notice.level === "warn" ? "alert" : "info"),
          h("div.banner-body", notice.text)
        )
      );
    }

    function refreshGenerate() {
      var baseline = null;
      S.baselines.forEach(function (item) {
        if (item.id === S.selected) baseline = item;
      });
      var state = canGenerate(app, baseline);
      genBtn.disabled = !state.allowed;
      genBtn.title = state.reason || "从这条基线生成（同输入同产物：已存在的直接复用）";
      util.replace(hint, state.reason ? state.reason : "已选 " + (baseline ? baseline.id + "（" + (baseline.revision || "—") + "）" : "—"));
    }

    function drawList() {
      if (!S.items.length) {
        util.replace(
          listHost,
          MC.stateBlock({
            kind: "empty",
            icon: "download",
            title: "还没有交付产物",
            desc: "先选中一条已批准基线，点「生成目标文件」。生成结果不可变，改值要新建变更单。",
          })
        );
        return;
      }
      var rows = S.items.map(function (item) {
        var row = rowModel(item);
        return h(
          "tr",
          h("td", h("div", row.filename), h("div.small.muted", row.createdBy ? "由 " + row.createdBy + " 生成" : "生成者未知")),
          h("td", h("div", row.baselineId), h("div.small.muted", row.revision ? "修订 " + row.revision : "修订未知")),
          h("td.num", String(row.edits) + (row.same ? " / " + row.same : "")),
          h("td.num", row.sizeText),
          h("td", h("span.badge." + row.statusTone, row.statusLabel), h("div.small.muted", util.formatTime(row.createdAt))),
          h("td", h("code.small", { title: row.sha }, row.shaShort)),
          h(
            "td.cell-actions",
            h("button.btn.btn-sm", { type: "button", onclick: function () { showDetail(item); } }, "详情"),
            h("button.btn.btn-sm", { type: "button", onclick: function () { download(item); } }, icon("download"), "下载")
          )
        );
      });
      util.replace(
        listHost,
        h(
          "div.table-wrap",
          h(
            "table.table",
            h(
              "thead",
              h(
                "tr",
                h("th", "文件"),
                h("th", "基线 / 修订"),
                h("th.num", "改动 / 未变"),
                h("th.num", "大小"),
                h("th", "交付状态"),
                h("th", "SHA-256"),
                h("th.cell-actions", "操作")
              )
            ),
            h("tbody", rows)
          )
        )
      );
    }

    function download(item) {
      var row = rowModel(item);
      MC.api.download(MC.api.p(pid, "/config/artifacts/" + MC.api.seg(row.id) + "/raw"), {}, row.filename).then(
        null,
        function (err) {
          MC.toast.error("下载失败", (MC.configApi.failure(err) || {}).message || "");
        }
      );
    }

    /* -- 交付登记（M7-09）：登记是人做的动作，不由下载触发 -- */
    function drawDeliverySection(row, container) {
      var summaryHost = h("div");
      var formHost = h("div");
      util.replace(
        container,
        h("h3", { style: "margin:16px 0 6px" }, "导入结果登记"),
        h(
          "p.small.muted",
          "「下载了」不等于「打进去了」。成功要带证据、失败要写原因、说不清就登记「证据不足」；" +
            "每次登记都是一条新记录（第几次、上次怎么挂的都留着），不改写旧记录。"
        ),
        summaryHost,
        formHost
      );

      function refreshSummary() {
        MC.configApi.deliverySummary(pid, { artifact_id: row.id }).then(
          function (summary) {
            var notice = deliverySummaryNotice(summary);
            util.replace(
              summaryHost,
              h(
                "div.banner." + (notice.level === "warn" ? "banner-warning" : "banner-neutral"),
                { style: "margin:8px 0" },
                icon(notice.level === "warn" ? "alert" : "info"),
                h("div.banner-body", notice.text)
              )
            );
          },
          function () {
            util.replace(summaryHost, null);
          }
        );
      }
      refreshSummary();

      if (app && app.can && !app.can("config_artifact_generate")) {
        util.replace(formHost, h("p.small.muted", "当前身份没有登记权限（需要生成产物的权限）。"));
        return;
      }

      var resultSelect = h(
        "select.input",
        { "aria-label": "登记结果" },
        h("option", { value: "declared_ok" }, "已声明成功（要带证据）"),
        h("option", { value: "failed" }, "失败（要写原因）"),
        h("option", { value: "insufficient_evidence" }, "证据不足（要写缺什么）")
      );
      var executedInput = h("input.input", { type: "text", placeholder: "实际执行时间（ISO，如 2026-10-22T15:30:00）", "aria-label": "实际执行时间" });
      var evNoteInput = h("input.input", { type: "text", placeholder: "证据说明（如：现场回读截图三张）", "aria-label": "证据说明" });
      var evRefInput = h("input.input", { type: "text", placeholder: "证据引用（照片/日志路径，可选）", "aria-label": "证据引用" });
      var reasonInput = h("input.input", { type: "text", placeholder: "失败原因（登记失败时必填）", "aria-label": "失败原因" });
      var missingInput = h("input.input", { type: "text", placeholder: "缺什么证据（登记证据不足时必填）", "aria-label": "缺失证据" });
      var issuesHost = h("div");
      var submitBtn = h("button.btn.btn-sm.btn-primary", { type: "button", disabled: true }, "登记导入结果");

      function currentForm() {
        return {
          result: resultSelect.value,
          executed_at: executedInput.value,
          evidence_note: evNoteInput.value,
          evidence_ref: evRefInput.value,
          failure_reason: reasonInput.value,
          missing_evidence: missingInput.value,
        };
      }

      function refreshForm() {
        var issues = registrationIssues(currentForm());
        submitBtn.disabled = issues.length > 0;
        util.replace(
          issuesHost,
          issues.length
            ? h("ul.small.muted", { style: "margin:6px 0;padding-left:18px" }, issues.map(function (text) { return h("li", text); }))
            : null
        );
      }
      [resultSelect, executedInput, evNoteInput, evRefInput, reasonInput, missingInput].forEach(function (el) {
        el.addEventListener("input", refreshForm);
        el.addEventListener("change", refreshForm);
      });

      submitBtn.addEventListener("click", function () {
        var form = currentForm();
        if (registrationIssues(form).length) return;
        var payload = {
          artifact_id: row.id,
          result: form.result,
          executed_at: form.executed_at.trim(),
          evidence: { note: form.evidence_note.trim(), ref: form.evidence_ref.trim() },
          failure_reason: form.failure_reason.trim(),
          missing_evidence: form.missing_evidence.trim() ? [form.missing_evidence.trim()] : [],
        };
        MC.withBusy(submitBtn, function () {
          return MC.configApi.registerDelivery(pid, payload).then(
            function (res) {
              MC.toast.success(
                "已登记：" + resultLabel(res.item.result),
                "第 " + res.item.attempt + " 次尝试，旧记录照旧可读"
              );
              refreshSummary();
              return res;
            },
            function (err) {
              MC.toast.error("登记失败", (MC.configApi.failure(err) || {}).message || "");
            }
          );
        });
      });

      util.replace(
        formHost,
        h("div.row", { style: "gap:8px;flex-wrap:wrap;margin:8px 0" }, resultSelect, executedInput),
        h("div.row", { style: "gap:8px;flex-wrap:wrap;margin-bottom:8px" }, evNoteInput, evRefInput),
        h("div.row", { style: "gap:8px;flex-wrap:wrap;margin-bottom:8px" }, reasonInput, missingInput),
        issuesHost,
        h("div.row", { style: "gap:8px;align-items:center" }, submitBtn, h("span.small.muted", "缺必填项时按钮不可用，原因列在上面"))
      );
      refreshForm();
    }

    /* -- 回读核验（M7-10）：每次核验落一份报告，旧结论照旧可读 -- */
    function drawVerifySection(row, container) {
      var listHost = h("div");
      var runBtn = h("button.btn.btn-sm", { type: "button" }, icon("refresh"), "跑一次回读核验");
      var canRun = !(app && app.can) || app.can("config_artifact_generate");
      runBtn.disabled = !canRun;
      runBtn.title = canRun ? "用该机型最新一次观测逐参数比对" : "当前身份没有核验权限";

      function drawReports(items) {
        if (!items.length) {
          util.replace(listHost, h("p.small.muted", "还没有核验报告。核验比对新采集的观测与这份产物的基线值，逐参数给结论。"));
          return;
        }
        util.replace(
          listHost,
          items.map(function (report) {
            var rows = (report.rows || []).map(verifyRowModel);
            return h(
              "div.card",
              { style: "margin:8px 0;padding:10px 12px" },
              h(
                "div.row",
                { style: "gap:8px;align-items:center;flex-wrap:wrap" },
                h("span.badge." + conclusionTone(report.conclusion), conclusionLabel(report.conclusion)),
                h("span.small", util.formatTime(report.created_at)),
                h("span.small.muted", report.created_by ? "由 " + report.created_by : "")
              ),
              h("p.small", { style: "margin:6px 0" }, verifySummary(report)),
              h("p.small.muted", { style: "margin:0 0 6px" }, report.claim || ""),
              rows.length
                ? h(
                    "details",
                    h("summary.small", "逐参数结论（" + rows.length + " 项）"),
                    h(
                      "table.table",
                      { style: "margin-top:6px" },
                      h("thead", h("tr", h("th", "参数"), h("th", "结论"), h("th", "说明"))),
                      h(
                        "tbody",
                        rows.map(function (r) {
                          return h("tr", h("td", r.name), h("td", r.statusLabel), h("td", r.reason || "—"));
                        })
                      )
                    )
                  )
                : null
            );
          })
        );
      }

      function refresh() {
        MC.configApi.verifications(pid, row.id).then(
          function (body) { drawReports(body.items || []); },
          function () { util.replace(listHost, h("p.small.muted", "核验报告读取失败。")); }
        );
      }

      runBtn.addEventListener("click", function () {
        MC.withBusy(runBtn, function () {
          return MC.configApi.runVerification(pid, row.id, {}).then(
            function (res) {
              var item = res.item || {};
              MC.toast.success("核验已落档：" + conclusionLabel(item.conclusion), item.claim || "");
              refresh();
              return res;
            },
            function (err) {
              MC.toast.error("核验失败", (MC.configApi.failure(err) || {}).message || "");
            }
          );
        });
      });

      util.replace(
        container,
        h("h3", { style: "margin:16px 0 6px" }, "回读核验"),
        h(
          "p.small.muted",
          "观测不到的参数不算 0、未登记成参数的节点不进通过判定、采集早于导入不算数——" +
            "满足不了这些，结论只会是「证据不完整」，不会是「核验通过」。"
        ),
        h("div.row", { style: "gap:8px;margin-bottom:6px" }, runBtn),
        listHost
      );
      refresh();
    }

    function showDetail(item) {
      var row = rowModel(item);
      var deliveryHost = h("div");
      var verifyHost = h("div");
      MC.configApi.artifact(pid, row.id).then(
        function (body) {
          var edits = body.edits || [];
          var warnings = ((body.validation || {}).warnings || []).map(function (text) {
            return h("li", text);
          });
          var checks = Object.keys((body.validation || {}).checks || {});
          util.replace(
            detailHost,
            h(
              "div.panel",
              { style: "margin-top:10px" },
              h(
                "div.panel-header",
                h("h3", "产物详情 " + MC.deliveryUi.shortSha(row.sha)),
                h("button.btn.btn-sm", { type: "button", onclick: function () { util.replace(detailHost, null); } }, "收起")
              ),
              h(
                "div",
                { style: "padding:14px 16px" },
                h("p.small.muted", "生成器：" + (row.generator || "—") + " · 模板快照哈希：" + shortSha(body.item && body.item.template_sha256)),
                h(
                  "p.small.muted",
                  "交付状态：" + row.statusLabel + "（" + statusHint(row.status) + "）"
                ),
                edits.length
                  ? h(
                      "table.table",
                      h("thead", h("tr", h("th", "参数"), h("th", "位置"), h("th", "原值"), h("th", "新值"))),
                      h(
                        "tbody",
                        edits.map(function (edit) {
                          return h(
                            "tr",
                            h("td", edit.parameter_id || "—"),
                            h("td", h("code.small", edit.loc || "—")),
                            h("td", edit.from || "（空）"),
                            h("td", edit.to || "（空）")
                          );
                        })
                      )
                    )
                  : h("p.small", "本次生成没有改动任何位置，产物是模板的字节拷贝。"),
                checks.length ? h("p.small.muted", "自检项：" + checks.join("、")) : null,
                warnings.length ? h("div.banner.banner-warning", { style: "margin-top:10px" }, icon("alert"), h("div.banner-body", h("ul", { style: "margin:0;padding-left:18px" }, warnings))) : null,
                deliveryHost,
                verifyHost
              )
            )
          );
          drawDeliverySection(row, deliveryHost);
          drawVerifySection(row, verifyHost);
        },
        function (err) {
          util.replace(detailHost, h("p.small.muted", "详情读取失败：" + ((MC.configApi.failure(err) || {}).message || "")));
        }
      );
    }

    function load() {
      S.loading = true;
      renderNotice(null);
      util.replace(listHost, MC.stateBlock({ kind: "loading", title: "正在读取交付产物…" }));
      var arts = MC.configApi.artifacts(pid).then(
        function (body) { return { ok: true, body: body }; },
        function (err) { return { ok: false, err: MC.configApi.failure(err) }; }
      );
      var bases = MC.configApi.baselines({ state: "approved" }).then(
        function (body) { return { ok: true, body: body }; },
        function (err) { return { ok: false, err: MC.configApi.failure(err) }; }
      );
      Promise.all([arts, bases]).then(function (pair) {
        S.loading = false;
        if (!pair[0].ok) {
          util.replace(listHost, blockFor(pair[0].err));
          return;
        }
        S.items = pair[0].body.items || [];
        S.baselines = pair[1].ok ? pair[1].body.items || [] : [];
        var options = baselineOptions(S.baselines);
        util.replace(select, options.map(function (option) { return h("option", { value: option.id }, option.label); }));
        if (options.length) {
          var exists = options.some(function (option) { return option.id === S.selected; });
          if (!exists) S.selected = options[0].id;
          select.value = S.selected;
        } else {
          S.selected = "";
        }
        refreshGenerate();
        drawList();
        renderNotice(S.items.map(rowModel));
      });
    }

    /** 错误归类 → 现有状态块：不新造一套"接口坏了"的视觉。 */
    function blockFor(info) {
      if (info && info.kind === "disabled") {
        return MC.stateBlock({
          kind: "empty",
          icon: "settings",
          title: "配置管理未开启",
          desc: "服务端特性门禁处于关闭状态。由管理员设置 MCHECKER_CONFIG_MANAGEMENT=on 并重启服务后可用。",
        });
      }
      if (info && info.kind === "forbidden") {
        return MC.stateBlock({
          kind: "locked",
          icon: "lock",
          title: "当前身份无权查看交付产物",
          desc: "读取交付需要配置域的 read 能力；请联系管理员确认 IP 管理员名单。",
        });
      }
      return MC.stateBlock({
        kind: "error",
        icon: "alert",
        title: "读取交付产物失败",
        desc: (info && info.message) || "服务端返回了未预期的响应。",
        actions: [h("button.btn.btn-sm", { type: "button", onclick: load }, "重试")],
      });
    }

    select.addEventListener("change", function () {
      S.selected = select.value;
      refreshGenerate();
    });

    genBtn.addEventListener("click", function () {
      if (!S.selected) return;
      MC.withBusy(genBtn, function () {
        return MC.configApi.generateArtifact(pid, { baseline_id: S.selected }).then(
          function (res) {
            if (res && res.reused) {
              MC.toast.info("已复用同名产物", "同一基线修订 + 同一模板已经生成过，没有新建");
            } else {
              MC.toast.success("已生成目标文件", "产物不可变，改值请新建变更单");
            }
            load();
            if (res && res.item) showDetail(res.item);
            return res;
          },
          function (err) {
            var info = MC.configApi.failure(err) || {};
            MC.toast.error("生成失败", info.message || "");
            throw err;
          }
        );
      });
    });

    util.replace(
      host,
      h(
        "div.panel",
        { style: "margin-top:10px" },
        h(
          "div.panel-header",
          h("h2", "交付（目标文件）"),
          h("span.muted.small", "产物按内容寻址留存，下载不重新生成")
        ),
        h(
          "div",
          { style: "padding:14px 16px" },
          noticeHost,
          h("div.row", { style: "gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px" }, select, genBtn, hint),
          listHost,
          detailHost
        )
      )
    );
    load();
  };
})(window);
