/* Value editing keeps numeric text exact and inheritance explicit. */
(function (global) {
  "use strict";
  var MC = global.MC;
  function valueKey(row) {
    row = row || {};
    return JSON.stringify([row.membership || "not_introduced", row.enabled !== false,
      row.value_state || "unset", row.value_state === "present" ? String(row.value) : null]);
  }
  function key(row, branch) {
    return valueKey(row) + (branch ? "/" + ((row || {}).intent || "inherit") : "");
  }
  function error(parameter, row) {
    if (row.membership === "removed" || row.enabled === false || row.value_state !== "present") return "";
    var type = String(parameter.type || "").toLowerCase(), text = String(row.value == null ? "" : row.value);
    if ((type === "bool" || type === "boolean") && text !== "true" && text !== "false") return "请选择 true 或 false";
    if ((type === "int" || type === "integer") && !/^[+-]?\d+$/.test(text)) return "请输入整数";
    if (["number", "decimal", "float"].indexOf(type) >= 0 && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return "请输入有效数值";
    return "";
  }
  function change(original, input, branch, explicitMode) {
    var result = Object.assign({}, input);
    if (result.value_state !== "present") delete result.value;
    if (branch && !explicitMode) {
      result.intent = original.intent === "pin" && result.value_state === "present" ? "pin"
        : valueKey(original) === valueKey(result) ? original.intent || "inherit" : "override";
    }
    return key(original, branch) === key(result, branch) ? null : result;
  }
  function rebase(original, edits, latest, branch, sourceChanged) {
    var result = {}, conflicts = [];
    Object.keys(edits).forEach(function (id) {
      if (key(edits[id], branch) === key(latest[id], branch)) return;
      if (!sourceChanged && key(original[id], branch) === key(latest[id], branch)) result[id] = edits[id];
      else conflicts.push({ parameter_id: id, before: original[id], mine: edits[id], latest: latest[id] });
    });
    return { edits: result, conflicts: conflicts };
  }
  async function persist(options) {
    var ids = Object.keys(options.edits), draft = options.draft, values = Object.assign({}, draft.values || {});
    for (var start = 0; start < ids.length; start += 100) {
      if (!options.current()) throw { code: "CONTEXT_CHANGED", message: "已离开当前配置，后续保存已停止" };
      var batch = ids.slice(start, start + 100), expected = draft.revision, next;
      var target = Object.assign({}, values);
      batch.forEach(function (id) { target[id] = options.edits[id]; });
      try {
        next = await options.write(batch.map(function (id) { return Object.assign({ parameter_id: id }, options.edits[id]); }), expected);
      } catch (err) {
        if (err.status && err.status < 500) throw err;
        // A lost response is not a failed write. Read before deciding whether to continue.
        next = await options.read();
        var all = new Set(Object.keys(target).concat(Object.keys(next.values || {})));
        var same = next.revision === expected + 1 && next.draft_id === draft.draft_id && !next.frozen && next.status !== "published";
        all.forEach(function (id) { if (key(target[id], options.branch) !== key((next.values || {})[id], options.branch)) same = false; });
        if (!same) throw err;
      }
      values = Object.assign({}, values, next.values || {});
      draft = Object.assign({}, draft, next, { values: values });
      options.saved(batch, draft);
    }
    return draft;
  }
  MC.configEditor = { key: key, valueKey: valueKey, error: error, change: change, rebase: rebase, persist: persist };
})(window);
