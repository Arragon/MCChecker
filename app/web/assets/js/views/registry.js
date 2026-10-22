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
          S.file ? h("div.banner.banner-info", icon("file"), h("div.banner-body", "已按文件筛选：" + S.file + "（关联关系随 M6-10 上线，当前显示全部参数）")) : null,
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
                "文件：" + name + " · 版本：" + revision + "。此文件的现场对账（原值/基线/拟值三栏）随 M6-10 上线；在此之前只能记录核对说明，不会写入基线。"
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
