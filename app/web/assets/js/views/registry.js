/*
 * 参数登记矩阵（M5-09）+ 文件页配置管理分段（M5-10）。
 *
 * 数据来源：`/api/v1/config/*`（M6-04 起是真实接口）。接口不可用时服务端回 404，
 * 这里**明确显示"接口不可用"**，不回退 mock——mock 只在模板模式（MC.config.mock）下由
 * api.js 分流，生产页永远走真实后端。
 *
 * 写入（M6-04）：新建参数 / 追加定义。表单必填保存理由，定义追加带
 * `expected_definition_id` 做 CAS；409 时保留草稿并给出可读原因，绝不静默覆盖。
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
      (item.search_keys || []).forEach(function (key) { haystack.push(key); });
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

  /* ---- 定义表单：定位文本 ↔ 契约里的 {path, attribute}（M6-04） ---- */

  var FIELD_LABELS = {
    xml_name: "XML 名称",
    xml_loc: "XML 定位",
    json_key: "JSON 键",
    type: "类型",
    unit: "单位",
    min: "下界",
    max: "上界",
  };

  var EMPTY_DRAFT = {
    xml_name: "",
    xml_loc: "",
    json_key: "",
    type: "decimal",
    unit: "",
    min: "",
    max: "",
    remarks: "",
    reason: "",
  };

  /** 把契约里的 loc 还原成人能读、也能再解析的文本：`Axis[2]/Velocity@unit`。 */
  function locToText(loc) {
    if (!loc || !loc.path || !loc.path.length) return "";
    var text = loc.path
      .map(function (step) {
        var occurrence = Number(step.occurrence || 1);
        return occurrence > 1 ? step.name + "[" + occurrence + "]" : step.name;
      })
      .join("/");
    return loc.attribute ? text + "@" + loc.attribute : text;
  }

  /** 反向解析：`Axis[2]/Velocity` 或 `Axis/Velocity@unit`；空串返回 null。 */
  function textToLoc(text) {
    var raw = String(text == null ? "" : text).trim();
    if (!raw) return null;
    var attribute = null;
    var at = raw.lastIndexOf("@");
    if (at > 0) {
      attribute = raw.slice(at + 1).trim() || null;
      raw = raw.slice(0, at);
    }
    var steps = [];
    raw.split("/").forEach(function (chunk) {
      var part = chunk.trim();
      if (!part) return;
      var match = /^(.*?)\[(\d+)\]$/.exec(part);
      if (match) {
        steps.push({ name: match[1], occurrence: parseInt(match[2], 10) });
      } else {
        steps.push({ name: part, occurrence: 1 });
      }
    });
    if (!steps.length) return null;
    return { path: steps, attribute: attribute };
  }

  /** 当前定义 → 表单草稿（编辑起点）。 */
  function draftFromDefinition(definition) {
    if (!definition) return Object.assign({}, EMPTY_DRAFT);
    var constraints = definition.constraints || {};
    return {
      xml_name: (definition.xml && definition.xml.name) || "",
      xml_loc: locToText(definition.xml && definition.xml.loc),
      json_key: (definition.json && definition.json.key) || "",
      type: definition.type || "decimal",
      unit: definition.unit || "",
      min: constraints.min == null ? "" : String(constraints.min),
      max: constraints.max == null ? "" : String(constraints.max),
      remarks: definition.remarks || "",
      reason: "",
    };
  }

  /**
   * 草稿 → 请求体。**保存理由必填**（服务端审计里不出现空理由的写操作），
   * 校验失败时只回 `{errors}`，不构造半成品请求。
   */
  function buildDefinitionPayload(draft) {
    var source = draft || {};
    var errors = [];
    var xmlName = String(source.xml_name || "").trim();
    if (!xmlName) errors.push("XML 名称不能为空");
    var loc = textToLoc(source.xml_loc);
    if (!loc) errors.push("XML 定位不能为空（例如 Axis[2]/Velocity 或 PulseWidth）");
    var reason = String(source.reason || "").trim();
    if (!reason) errors.push("请填写保存理由");
    var type = String(source.type || "decimal").trim();
    var min = String(source.min == null ? "" : source.min).trim();
    var max = String(source.max == null ? "" : source.max).trim();
    if ((min || max) && type !== "decimal" && type !== "int") {
      errors.push("只有数值类型才能填下界/上界");
    }
    if (errors.length) return { errors: errors };
    var payload = {
      xml_name: xmlName,
      xml_loc: loc,
      json_key: String(source.json_key || "").trim() || null,
      type: type,
      unit: String(source.unit || "").trim(),
      remarks: String(source.remarks || "").trim(),
      reason: reason,
    };
    if (min || max) payload.constraints = { min: min || null, max: max || null };
    if (source.expected_definition_id !== undefined) {
      payload.expected_definition_id = source.expected_definition_id || "";
    }
    return { payload: payload };
  }

  /** 修改前后摘要：只列真正变化的字段，空值显示为「（空）」。 */
  function diffSummary(before, after) {
    var show = function (value) {
      var text = value == null ? "" : String(value);
      return text === "" ? "（空）" : text;
    };
    var changes = [];
    Object.keys(FIELD_LABELS).forEach(function (field) {
      var from = show(before && before[field]);
      var to = show(after && after[field]);
      if (from !== to) {
        changes.push({ field: field, label: FIELD_LABELS[field], before: from, after: to });
      }
    });
    return changes;
  }

  /* ===================== 现场对账三栏模型（M6-10） ===================== */

  /* 差异状态的展示顺序：**要人决定的排前面，一致与双方皆无排后面**。
   * 后端返回的顺序按 parameter_id 排，直接照搬会让"没变化"淹没"要处理"。 */
  var DIFF_ORDER = ["changed", "missing_observed", "missing_baseline", "unknown", "same", "missing_both"];

  var DIFF_LABELS = {
    same: "一致",
    changed: "已变更",
    missing_observed: "现场未采到",
    missing_baseline: "基线未覆盖",
    missing_both: "双方皆无",
    unknown: "无法判定",
  };

  /* 观测条目的五种状态（与 config_ingest 常量一一对应）。`missing`/`invalid`
   * 都不是"值"，界面上必须说清是哪种，否则用户会以为参数被删了。 */
  var ENTRY_LABELS = {
    mapped: "已映射",
    unmapped: "未映射",
    ambiguous: "有歧义",
    missing: "缺失",
    invalid: "值不合法",
  };

  var SCOPE_LABELS = { common: "通用层", model: "机型层", device: "设备层" };

  /** 类型化值 → 可读文本。`null` 类型显示 `null`，不当成空串（两者语义不同）。 */
  function valueText(value) {
    if (!value) return "—";
    var type = String(value.type || "");
    var text = value.text == null ? "" : String(value.text);
    if (type === "null") return "null";
    if (type === "string" || type === "text") return text === "" ? "（空串）" : text;
    return value.unit ? text + " " + value.unit : text;
  }

  /** 把一行对账结果摊平成展示模型；`observed` 缺失时如实说"没采到"，不补值。 */
  function reconcileRow(row) {
    var source = row || {};
    var observed = source.observed || null;
    var baseline = source.baseline || null;
    var proposed = source.proposed || {};
    var status = String(source.diff || "unknown");
    var observedLabel = observed ? ENTRY_LABELS[observed.status] || observed.status : "未采集";
    return {
      parameter_id: source.parameter_id,
      name: source.name || source.parameter_id,
      category: source.category || "",
      unit: source.unit || "",
      known: !!source.known,
      diff: status,
      label: DIFF_LABELS[status] || status,
      needsDecision: status === "changed" || status === "missing_baseline" || status === "unknown",
      observedText: valueText(observed && observed.value),
      observedLabel: observedLabel,
      observedReason: (observed && observed.reason) || "",
      observedAt: (observed && observed.captured_at) || "",
      baselineText: valueText(baseline && baseline.value),
      baselineIntent: (baseline && baseline.intent) || "",
      proposedText: valueText(proposed.value),
      proposedSource: proposed.source || "none",
    };
  }

  /** 按差异状态分组；空组不出现，顺序按 DIFF_ORDER 而不是对象键序。 */
  function reconcileGroups(rows) {
    var buckets = {};
    (rows || []).forEach(function (row) {
      var key = String(row.diff || "unknown");
      (buckets[key] = buckets[key] || []).push(row);
    });
    var groups = [];
    DIFF_ORDER.forEach(function (status) {
      var items = buckets[status];
      if (!items || !items.length) return;
      groups.push({ status: status, label: DIFF_LABELS[status] || status, rows: items });
      delete buckets[status];
    });
    // 后端将来加了新状态也不会被悄悄吞掉
    Object.keys(buckets).forEach(function (status) {
      groups.push({ status: status, label: status, rows: buckets[status] });
    });
    return groups;
  }

  /** 计数摘要：只列非零项，按展示顺序拼成一句话。 */
  function reconcileSummary(counts) {
    var source = counts || {};
    var parts = DIFF_ORDER.filter(function (status) {
      return Number(source[status] || 0) > 0;
    }).map(function (status) {
      return DIFF_LABELS[status] + " " + source[status];
    });
    return parts.length ? parts.join(" · ") : "暂无可对账的参数";
  }

  /**
   * 顶部提示条：只报服务端给出的事实，不推断。
   * `level` 取 `error|warn|info`——过期提醒必须是 warn 而不是悄悄换数据。
   */
  function reconcileNotices(payload) {
    var data = payload || {};
    var list = [];
    var observation = data.observation || null;
    var baseline = data.baseline || null;
    if (data.stale_observation) {
      list.push({
        level: "warn",
        key: "stale",
        text:
          "该文件已有更新的现场采集（" +
          (data.newer_observation_id || "新观测") +
          "）。本表仍按你在看的这条观测计算，不会静默替换；点「按最新重算」再走一次。",
      });
    }
    if (!data.has_observation) {
      list.push({
        level: "info",
        key: "no-observation",
        text: "该逻辑文件还没有现场采集记录，现场列全空，拟值只能来自基线。",
      });
    } else if (observation && observation.status === "parse_failed") {
      list.push({
        level: "error",
        key: "parse-failed",
        text: "最近一次采集解析失败，参数值都拿不到；请先在观测详情里看失败原因（原文件已存档，不会丢）。",
      });
    }
    if (!data.has_baseline) {
      list.push({
        level: "warn",
        key: "no-baseline",
        text: "没有已批准的基线可作为基准，基线列全空。拟值只是「按现场值建议」，先建基线再谈变更。",
      });
    } else if (baseline && baseline.scope && baseline.scope !== "model") {
      list.push({
        level: "info",
        key: "scope-" + baseline.scope,
        text:
          "本次基准是「" +
          (SCOPE_LABELS[baseline.scope] || baseline.scope) +
          "」基线（" +
          (baseline.id || "") +
          "）" +
          (baseline.scope === "common"
            ? "，改动会影响所有继承它的机型。"
            : "，只影响绑定到该设备的机型。"),
      });
    }
    var unresolved = (observation && observation.unresolved_keys) || [];
    if (unresolved.length) {
      list.push({
        level: "warn",
        key: "unresolved",
        text:
          unresolved.length +
          " 个键在参数主档里找不到对应参数（" +
          unresolved.slice(0, 4).join("、") +
          (unresolved.length > 4 ? " 等" : "") +
          "），已列为未映射，不会进拟值。",
      });
    }
    return list;
  }

  /**
   * 把本地草稿叠到服务端结果上。草稿只存"用户改到一半的拟值文本"，
   * **不是权威**：刷新一次服务端结果说了算，草稿只负责别把输入弄丢。
   */
  function applyDraft(payload, draft) {
    var data = payload || {};
    var saved = (draft && draft.edits) || {};
    var adopt = (draft && draft.adopt) || {};
    var rows = (data.rows || []).map(function (row) {
      var model = reconcileRow(row);
      var edited = saved[model.parameter_id];
      model.editText = edited === undefined ? model.proposedText : String(edited);
      model.adopted =
        adopt[model.parameter_id] === undefined ? model.needsDecision : !!adopt[model.parameter_id];
      model.changed = model.editText !== model.proposedText;
      return model;
    });
    return { rows: rows, pending: pendingChanges(rows) };
  }

  /**
   * 待提交行：勾了采纳、有值、**且与基线不同**。
   *
   * "与基线相同"的行勾了也不算待提交——把没变化的行塞进变更单，等于让审批人为
   * 零改动签字。真正的提交在 M6-11（带理由与批准）。
   */
  function pendingChanges(rows) {
    return (rows || [])
      .filter(function (row) {
        if (!row.adopted) return false;
        if (!row.editText || row.editText === "—") return false;
        return row.editText !== row.baselineText;
      })
      .map(function (row) {
        return {
          parameter_id: row.parameter_id,
          before: row.baselineText,
          after: row.editText,
          diff: row.diff,
        };
      });
  }

  MC.configUi = {
    filterParameters: filterParameters,
    versionColumns: versionColumns,
    cellText: cellText,
    buildRows: buildRows,
    locToText: locToText,
    textToLoc: textToLoc,
    draftFromDefinition: draftFromDefinition,
    buildDefinitionPayload: buildDefinitionPayload,
    diffSummary: diffSummary,
    valueText: valueText,
    reconcileRow: reconcileRow,
    reconcileGroups: reconcileGroups,
    reconcileSummary: reconcileSummary,
    reconcileNotices: reconcileNotices,
    applyDraft: applyDraft,
    pendingChanges: pendingChanges,
    DIFF_ORDER: DIFF_ORDER,
    DIFF_LABELS: DIFF_LABELS,
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
        title: "参数登记接口不可用",
        desc: "服务端没有对应的接口（可能是版本不匹配）。这里不放行 mock 数据。",
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

  /* ===================== 参数登记视图（M5-09 / M6-04） ===================== */

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
      families: [],
      editor: null, // {kind:"create"|"definition", pmid, draft, before, errors}
    };

    var canWrite = function () { return S.writable && app.can("config_registry_write"); };

    var search = h("input.input", {
      type: "search",
      placeholder: "按名称、旧名、XML 名称或 JSON 键检索",
      value: S.q,
      "aria-label": "检索参数",
    });
    var statusLine = h("div.small.muted");
    var toolbar = h("div.row", { style: "gap:8px;align-items:center" });
    var body = h("div");
    var editorHost = h("div");
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
          toolbar,
          S.file
            ? h(
                "div.banner.banner-neutral",
                { style: "margin-top:10px" },
                icon("file"),
                h(
                  "div.banner-body",
                  "已按文件 " + S.file + " 进入检索。参数登记按配置类别展示该类别下的全部参数；" +
                    "该文件的现场对账（原值 / 基线 / 拟值三栏）在文件页的「配置管理」分段。"
                )
              )
            : null,
          body,
          editorHost,
          draftNote
        )
      )
    );

    function draftKey(pmid) { return pmid ? "param:" + pmid : "registry:new"; }

    function updateDraftNote() {
      var api = MC.configApi;
      var draft = api.getDraft(app.pid, draftKey(S.editor && S.editor.pmid));
      if (S.editor && S.editor.draft) {
        util.replace(
          draftNote,
          h("span", "有未提交的草稿（", h("strong", S.editor.draft.xml_name || S.editor.draft.name || "未命名"), "）。切换页面前会提示。")
        );
        return;
      }
      util.replace(draftNote, draft ? h("span", "有未提交的草稿（", h("strong", draft.name || draft.xml_name || "未命名"), "）。") : "");
    }

    /* ---- 表单渲染 ---- */

    function field(label, control, help) {
      return h(
        "div.field",
        h("label.field-label", label),
        control,
        help ? h("div.field-help", help) : null
      );
    }

    function textInput(value, onInput, placeholder, aria) {
      var input = h("input.input", { type: "text", value: value || "", placeholder: placeholder || "", "aria-label": aria || placeholder || "" });
      input.addEventListener("input", function () { onInput(input.value); });
      return input;
    }

    function errorList(errors) {
      if (!errors || !errors.length) return null;
      return h(
        "div.field-error",
        h("strong", "还不能提交："),
        h("ul", { style: "margin:6px 0 0 18px" }, errors.map(function (message) { return h("li", message); }))
      );
    }

    function openCreate() {
      if (!S.families.length) {
        MC.toast.error("还没有配置类别：请先由管理员创建类别（配置类别接口已可用）");
        return;
      }
      S.editor = {
        kind: "create",
        pmid: null,
        draft: { family_id: S.families[0].id, name: "", category: "", notes: "" },
        before: null,
        errors: [],
      };
      renderEditor();
    }

    function openDefinition(parameter) {
      MC.configApi.parameter(parameter.id).then(
        function (detail) {
          var current = detail.current;
          S.editor = {
            kind: "definition",
            pmid: parameter.id,
            before: MC.configUi.draftFromDefinition(current),
            draft: Object.assign(MC.configUi.draftFromDefinition(current), { expected_definition_id: current ? current.id : "" }),
            errors: [],
            current: current,
            parameter: detail.item,
          };
          var saved = MC.configApi.getDraft(app.pid, draftKey(parameter.id));
          if (saved) S.editor.draft = Object.assign(S.editor.draft, saved);
          renderEditor();
        },
        function (info) {
          MC.toast.error(info.message);
        }
      );
    }

    function closeEditor() {
      S.editor = null;
      util.replace(editorHost, "");
      updateDraftNote();
    }

    function renderEditor() {
      if (!S.editor) { util.replace(editorHost, ""); return; }
      var editor = S.editor;
      var draft = editor.draft;
      var title = editor.kind === "create" ? "新建参数主档" : "追加定义修订 · " + ((editor.parameter && editor.parameter.name) || editor.pmid);

      var controls;
      if (editor.kind === "create") {
        var familySelect = h(
          "select.input",
          { "aria-label": "所属配置类别" },
          S.families.map(function (family) {
            return h("option", { value: family.id, selected: family.id === draft.family_id ? "selected" : null }, family.name + "（" + family.code + "）");
          })
        );
        familySelect.addEventListener("change", function () { draft.family_id = familySelect.value; });
        controls = h(
          "div",
          { style: "display:grid;gap:10px" },
          field("所属配置类别", familySelect),
          field("参数名称", textInput(draft.name, function (v) { draft.name = v; }, "例如 脉冲宽度", "参数名称")),
          field("类别", textInput(draft.category, function (v) { draft.category = v; }, "例如 时序", "类别")),
          field("备注", textInput(draft.notes, function (v) { draft.notes = v; }, "可选", "备注"))
        );
      } else {
        var diff = MC.configUi.diffSummary(editor.before, draft);
        controls = h(
          "div",
          { style: "display:grid;gap:10px" },
          field("XML 名称", textInput(draft.xml_name, function (v) { draft.xml_name = v; renderDiff(); }, "例如 PulseWidth", "XML 名称")),
          field("XML 定位", textInput(draft.xml_loc, function (v) { draft.xml_loc = v; renderDiff(); }, "Axis[2]/Velocity 或 PulseWidth@unit", "XML 定位"), "同名节点用 [n] 标序号；属性用 @ 后缀。"),
          field("JSON 键", textInput(draft.json_key, function (v) { draft.json_key = v; renderDiff(); }, "例如 PULSE_WIDTH", "JSON 键")),
          field("类型", textInput(draft.type, function (v) { draft.type = v; renderDiff(); }, "decimal / int / bool / text / enum", "类型")),
          field("单位", textInput(draft.unit, function (v) { draft.unit = v; renderDiff(); }, "例如 s、mm/s", "单位")),
          h(
            "div.row",
            { style: "gap:10px" },
            field("下界", textInput(draft.min, function (v) { draft.min = v; renderDiff(); }, "可选", "下界")),
            field("上界", textInput(draft.max, function (v) { draft.max = v; renderDiff(); }, "可选", "上界"))
          ),
          field("保存理由（必填）", textInput(draft.reason, function (v) { draft.reason = v; }, "写清为什么改，审计会记录这句话", "保存理由")),
          field("备注", textInput(draft.remarks, function (v) { draft.remarks = v; }, "可选", "备注")),
          diffHost
        );
      }

      function renderDiff() {
        if (editor.kind !== "definition") return;
        var changes = MC.configUi.diffSummary(editor.before, editor.draft);
        util.replace(
          diffHost,
          changes.length
            ? h(
                "div.panel",
                { style: "padding:10px 12px" },
                h("div.small.muted", "修改前后摘要"),
                h(
                  "ul",
                  { style: "margin:6px 0 0 18px" },
                  changes.map(function (change) {
                    return h("li.small", change.label + "：", h("code", change.before), " → ", h("strong", change.after));
                  })
                )
              )
            : h("div.small.muted", "与当前定义一致（提交后会生成一条内容相同的新修订）")
        );
      }

      var diffHost = h("div");
      if (editor.kind === "definition") renderDiff();

      util.replace(
        editorHost,
        h(
          "div.panel",
          { style: "margin-top:14px" },
          h("div.panel-header", h("h2", title), h("span.muted.small", editor.kind === "definition" && editor.current ? "当前 r" + editor.current.revision_no : "")),
          h(
            "div",
            { style: "padding:14px 16px;display:grid;gap:12px" },
            controls,
            errorList(editor.errors),
            h(
              "div.row",
              { style: "gap:8px" },
              h("button.btn.btn-primary.btn-sm", { type: "button", onclick: submit }, "提交"),
              h("button.btn.btn-sm", { type: "button", onclick: closeEditor }, "取消")
            )
          )
        )
      );
    }

    /* ---- 提交 ---- */

    function submit() {
      var editor = S.editor;
      if (!editor) return;
      if (editor.kind === "create") {
        var name = String(editor.draft.name || "").trim();
        if (!name) { editor.errors = ["参数名称不能为空"]; renderEditor(); return; }
        MC.configApi.createParameter({
          family_id: editor.draft.family_id,
          name: name,
          category: editor.draft.category,
          notes: editor.draft.notes,
        }).then(
          function (body) {
            MC.configApi.clearDraft(app.pid, draftKey(null));
            S.editor = null;
            MC.toast.success("已创建参数 " + (body.item && body.item.name ? body.item.name : ""));
            load();
          },
          function (info) {
            editor.errors = [MC.configApi.conflictMessage(info) || info.message];
            MC.configApi.setDraft(app.pid, draftKey(null), editor.draft);
            renderEditor();
          }
        );
        return;
      }

      var built = MC.configUi.buildDefinitionPayload(editor.draft);
      if (built.errors) { editor.errors = built.errors; renderEditor(); return; }
      MC.configApi.addDefinition(editor.pmid, built.payload).then(
        function (body) {
          MC.configApi.clearDraft(app.pid, draftKey(editor.pmid));
          S.editor = null;
          MC.toast.success("已追加定义 r" + (body.item && body.item.revision_no ? body.item.revision_no : ""));
          load();
        },
        function (info) {
          // 409 一律保留草稿：用户不该为别人的并发修改丢自己的输入。
          editor.errors = [MC.configApi.conflictMessage(info) || info.message];
          MC.configApi.setDraft(app.pid, draftKey(editor.pmid), editor.draft);
          renderEditor();
          updateDraftNote();
        }
      );
    }

    /* ---- 列表 ---- */

    function renderToolbar() {
      util.replace(
        toolbar,
        canWrite()
          ? [
              h("button.btn.btn-primary.btn-sm", { type: "button", onclick: openCreate }, icon("plus"), "新建参数"),
              h("span.small.muted", "写入前需填写保存理由；并发冲突会保留草稿。"),
            ]
          : h("span.small.muted", "只读：当前身份没有 config_registry_write 能力。")
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
            desc: canWrite() ? "点上方「新建参数」登记第一条参数。" : "参数主档由管理员登记。",
          })
        );
        return;
      }
      if (!items.length) {
        util.replace(body, MC.stateBlock({ kind: "empty", icon: "search", title: "没有匹配的参数", desc: "换个关键词试试，检索覆盖历史名称、XML 名称与 JSON 键。" }));
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
                  h("th", "状态"),
                  h("th", "操作")
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
                    h("td", row.parameter.status === "retired" ? h("span.badge", "已退役") : h("span.badge.badge-ok", "启用")),
                    h(
                      "td",
                      canWrite()
                        ? h(
                            "button.btn.btn-sm",
                            { type: "button", onclick: function () { openDefinition(row.parameter); } },
                            row.parameter.current_definition_id ? "追加定义" : "设置定义"
                          )
                        : "—"
                    )
                  );
                })
              )
            )
          ),
          h("p.xsmall.muted", { style: "margin-top:10px" }, "定义修订只追加、不覆盖：每次提交都会生成新修订，历史基线仍指向它当初那一版。")
        )
      );
      updateDraftNote();
    }

    function load() {
      S.loading = true;
      S.info = null;
      render();
      var api = MC.configApi;
      return Promise.all([api.status(), api.parameters({ q: "" }), api.families()]).then(
        function (results) {
          S.mode = results[0].mode;
          S.writable = !!results[0].writable;
          S.items = (results[1] && results[1].items) || [];
          S.families = (results[2] && results[2].items) || [];
          S.loading = false;
          util.replace(
            statusLine,
            h(
              "span",
              "档位 " + (S.mode || "off") + " · " + S.items.length + " 个参数" + (S.writable ? " · 可写" : " · 只读")
            )
          );
          renderToolbar();
          render();
        },
        function (info) {
          S.loading = false;
          S.info = info;
          util.replace(statusLine, "");
          renderToolbar();
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

  /** 状态徽标：要人决定的用 warning，一致用 success，无法判定用 danger，其余中性。 */
  function diffBadge(status) {
    var cls =
      status === "changed" ? ".badge-warning" : status === "same" ? ".badge-success" : status === "unknown" ? ".badge-danger" : "";
    return h("span.badge" + cls, DIFF_LABELS[status] || status);
  }

  /**
   * 画三栏表（M6-10）。差异由服务端给，这里只做分组、筛选与草稿叠加。
   *
   * 拟值列可改：改了只进本地草稿（`MC.configApi.drafts`），
   * **不改服务端任何对象**——真正的提交要等 M6-11 的变更单。
   */
  function drawReconcile(host, payload, draftOf, saveDraft) {
    var data = payload || {};
    var model = applyDraft(data, draftOf());
    var filter = (draftOf().filter || "all");

    var infoLine = h(
      "p.small.muted",
      "逻辑文件：" + ((data.logical_file && (data.logical_file.display_name || data.logical_file.id)) || "—") +
        "（" + ((data.logical_file && data.logical_file.role) || "未标角色") + "）" +
        " · 现场：" + (data.observation ? data.observation.id + (data.observation.captured_at ? "（" + util.formatTime(data.observation.captured_at) + "）" : "") : "未采集") +
        " · 基线：" + (data.baseline ? data.baseline.id + "（" + (SCOPE_LABELS[data.baseline.scope] || data.baseline.scope || "—") + "）" : "无")
    );

    var notices = reconcileNotices(data).map(noticeBlock);

    var chips = h("div.row", { style: "gap:6px;flex-wrap:wrap;margin-bottom:10px" });
    var tableHost = h("div");
    var pendingHost = h("div.small.muted", { style: "margin-top:8px" });

    function setFilter(next) {
      var draft = draftOf();
      draft.filter = next;
      saveDraft(draft);
      filter = next;
      drawChips();
      drawTable();
    }

    function drawChips() {
      var options = [{ status: "all", label: "全部 " + model.rows.length }].concat(
        reconcileGroups(model.rows).map(function (group) {
          return { status: group.status, label: group.label + " " + group.rows.length };
        })
      );
      util.replace(
        chips,
        options.map(function (option) {
          var active = filter === option.status;
          return h(
            "button.btn.btn-sm" + (active ? ".btn-primary" : ""),
            {
              type: "button",
              "aria-pressed": active ? "true" : "false",
              onclick: function () { setFilter(option.status); },
            },
            option.label
          );
        })
      );
    }

    function updatePending() {
      var pending = pendingChanges(model.rows);
      var edited = model.rows.filter(function (row) { return row.changed; }).length;
      util.replace(
        pendingHost,
        h(
          "span",
          "拟值 = 服务端建议，不是决定。当前待提交 " +
            pending.length +
            " 行（勾选且与基线不同）" +
            (edited ? "，手工改过 " + edited + " 行" : "") +
            "；变更单（含理由与批准）在 M6-11 接入后统一提交，这里不会写入任何对象。"
        )
      );
    }

    function drawTable() {
      var rows = model.rows.filter(function (row) { return filter === "all" || row.diff === filter; });
      if (!rows.length) {
        util.replace(
          tableHost,
          MC.stateBlock({
            kind: "empty",
            icon: "settings",
            title: "这一组没有参数",
            desc: "换个状态筛选，或确认这个逻辑文件是否已登记参数。",
          })
        );
        return;
      }
      util.replace(
        tableHost,
        h(
          "table.table",
          h(
            "thead",
            h(
              "tr",
              h("th", "参数"),
              h("th", "现场原值"),
              h("th", "批准基线"),
              h("th", "拟值（可改）"),
              h("th", "状态")
            )
          ),
          h(
            "tbody",
            rows.map(function (row) { return drawRow(row, draftOf, saveDraft, updatePending); })
          )
        )
      );
    }

    drawChips();
    drawTable();
    updatePending();
    util.replace(host, h("div", infoLine, notices, chips, tableHost, pendingHost));
  }

  /** 单行：现场 / 基线两栏只读，拟值栏可改（只进草稿）。 */
  function drawRow(row, draftOf, saveDraft, updatePending) {
    var observedCell = h(
      "td",
      h("div", row.observedText),
      h(
        "div.small.muted",
        row.observedLabel + (row.observedReason ? "：" + row.observedReason : "")
      )
    );

    var input = h("input.input", {
      type: "text",
      value: row.editText === "—" ? "" : row.editText,
      placeholder: "（不采纳）",
      "aria-label": "拟值：" + row.name,
      disabled: !row.known,
      title: row.known ? "改这里只是本地草稿，不会写入服务端" : "该参数未在参数主档登记，改不了",
    });
    var mark = h("span.small.muted", row.changed ? "· 已改" : "");
    input.addEventListener("input", function () {
      var draft = draftOf();
      draft.edits = draft.edits || {};
      draft.edits[row.parameter_id] = input.value;
      saveDraft(draft);
      row.editText = input.value;
      row.changed = input.value !== row.proposedText;
      util.replace(mark, row.changed ? "· 已改" : "");
      updatePending();
    });

    var adopt = h("input", { type: "checkbox", checked: row.adopted, disabled: !row.known, "aria-label": "采纳：" + row.name });
    adopt.addEventListener("change", function () {
      var draft = draftOf();
      draft.adopt = draft.adopt || {};
      draft.adopt[row.parameter_id] = adopt.checked;
      saveDraft(draft);
      row.adopted = adopt.checked;
      updatePending();
    });

    var nameCell = h(
      "td",
      h("div", row.name),
      h("div.small.muted", [row.category, row.parameter_id].filter(Boolean).join(" · ") + (row.known ? "" : " · 未登记"))
    );

    return h(
      "tr",
      nameCell,
      observedCell,
      h(
        "td",
        h("div", row.baselineText),
        h("div.small.muted", row.baselineIntent ? "意图：" + row.baselineIntent : "")
      ),
      h("td", h("div.row", { style: "gap:6px;align-items:center" }, adopt, input, mark)),
      h("td", diffBadge(row.diff))
    );
  }

  /* ===================== 文件页配置管理分段（M5-10 / M6-10） ===================== */

  /** 提示条：level → 现有视觉组件（.banner 的四种皮肤）。 */
  function noticeBlock(notice) {
    var cls = notice.level === "error" ? "banner-danger" : notice.level === "warn" ? "banner-warning" : "banner-neutral";
    var glyph = notice.level === "error" ? "alert" : notice.level === "warn" ? "alert" : "info";
    return h("div.banner." + cls, { style: "margin-bottom:10px" }, icon(glyph), h("div.banner-body", notice.text));
  }

  /**
   * 在文件页"配置管理"分段渲染现场对账三栏（M6-10）。
   *
   * 服务端算差异、前端只摆位置：不猜、不补、不在本地重算相等性。
   * 草稿（每行的拟值文本、勾选、筛选）存在 `MC.configApi.drafts` 里，
   * 切标签回来还在；**权威数据永远来自服务端**，刷新即覆盖草稿的基线。
   */
  MC.configUi.renderFileSection = function (app, ctx, host) {
    var pid = (ctx && ctx.pid) || app.pid;
    var name = (ctx && ctx.name) || "";
    var revision = (ctx && ctx.version) || "current";
    var key = "file:" + name;

    util.replace(host, MC.stateBlock({ kind: "loading", title: "正在读取配置关联…" }));

    function draftOf() {
      var saved = MC.configApi.getDraft(pid, key);
      return saved || { note: "", edits: {}, adopt: {}, filter: "all" };
    }
    function saveDraft(next) {
      MC.configApi.setDraft(pid, key, next);
    }

    function load(query) {
      var statusP = MC.configApi.profileStatus(pid);
      var reconcileP = MC.configApi.reconcile(pid, query || { filename: name }).then(
        function (body) { return { ok: true, body: body }; },
        function (err) { return { ok: false, err: err }; }
      );
      return Promise.all([statusP, reconcileP]).then(function (pair) {
        draw(pair[0], pair[1]);
      });
    }

    function draw(status, result) {
      var note = h("textarea.input", {
        rows: "3",
        placeholder: "记录这次现场核对的口径（例如：与 1.1.0 出口 XML 对照，脉冲宽度按秒计）",
        "aria-label": "关联说明草稿",
      });
      var draft = draftOf();
      note.value = draft.note || "";
      note.addEventListener("input", function () {
        var next = draftOf();
        next.note = note.value;
        next.filename = name;
        next.version = revision;
        saveDraft(next);
      });

      var footer = h("div.row", { style: "margin-top:10px;gap:8px;flex-wrap:wrap" });
      var box = h(
        "div.panel",
        h(
          "div.panel-header",
          h("h2", "配置管理"),
          h("span.muted.small", (status.baselines || 0) + " 条基线 / " + (status.observations || 0) + " 条观测")
        ),
        h("div", { style: "padding:14px 16px" }, h("div", { id: "cfg-reconcile" })),
        h(
          "div",
          { style: "padding:0 16px 16px" },
          h("p.small.muted", "核对说明只存在本机内存里，不会写入基线；切到别的分段再回来还在。"),
          note,
          footer
        )
      );

      var inner = box.querySelector("#cfg-reconcile");

      function redrawFooter() {
        util.replace(
          footer,
          h(
            "button.btn.btn-sm",
            {
              type: "button",
              onclick: function () {
                MC.configApi.clearDraft(pid, key);
                note.value = "";
                MC.toast.success("已清除本地草稿");
                load();
              },
            },
            "清除草稿"
          ),
          h(
            "button.btn.btn-sm",
            {
              type: "button",
              onclick: function () {
                util.replace(inner, MC.stateBlock({ kind: "loading", title: "正在按最新采集重算…" }));
                load({ filename: name });
              },
            },
            "按最新重算"
          ),
          h(
            "a.btn.btn-sm",
            { href: MC.router.format({ pid: pid, kind: "registry", query: { file: name } }) },
            "在参数登记中查看"
          )
        );
      }

      util.replace(host, box);
      redrawFooter();

      if (!result.ok) {
        var err = result.err || {};
        if (err.kind === "disabled") {
          util.replace(inner, stateFor({ kind: "disabled" }, app));
        } else if (err.kind === "forbidden") {
          util.replace(inner, stateFor({ kind: "forbidden" }, app));
        } else if (err.kind === "notfound") {
          // 没登记成逻辑文件不是错误，是"还没配"——给出下一步而不是红色报错
          util.replace(
            inner,
            MC.stateBlock({
              kind: "empty",
              icon: "settings",
              title: "该文件还没有登记为逻辑文件",
              desc:
                "配置管理按「逻辑文件 → 配置类别 → 参数主档」定位参数。" +
                "先在配置类别里把这个文件登记成逻辑文件（含 role），这里才会出现三栏对账。",
              actions: [
                h(
                  "a.btn.btn-sm",
                  { href: MC.router.format({ pid: pid, kind: "registry", query: { file: name } }) },
                  "去参数登记"
                ),
              ],
            })
          );
        } else {
          util.replace(inner, stateFor(err, app));
        }
        return;
      }

      drawReconcile(inner, result.body, draftOf, saveDraft);
    }

    load();
  };
})(window);
