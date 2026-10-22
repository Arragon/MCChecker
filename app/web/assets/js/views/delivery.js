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
 * 3. **不宣称已核验**：真实系统导入的状态恒为 `unverified`（D6 缺口），
 *    页面上必须把"待验"说清楚，别让人以为下载过就等于验过了。
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

  /* ===================== 纯函数（供测试与视图共用） ===================== */

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
      status: source.delivery_status || "unverified",
      statusLabel: statusLabel(source.delivery_status),
      statusTone: statusTone(source.delivery_status),
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
      return row.status === "unverified";
    }).length;
    if (!pending) {
      return { level: "info", text: "全部产物已完成真实系统导入核验。" };
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
    statusLabel: statusLabel,
    statusHint: statusHint,
    statusTone: statusTone,
    shortSha: shortSha,
    editCount: editCount,
    rowModel: rowModel,
    canGenerate: canGenerate,
    baselineOptions: baselineOptions,
    deliveryNotice: deliveryNotice,
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

    function showDetail(item) {
      var row = rowModel(item);
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
                warnings.length ? h("div.banner.banner-warning", { style: "margin-top:10px" }, icon("alert"), h("div.banner-body", h("ul", { style: "margin:0;padding-left:18px" }, warnings))) : null
              )
            )
          );
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
