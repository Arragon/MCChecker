/*
 * 导入配置文件：本地上传 / 从链接下载。
 * 部署者可“保存到服务器”（同名自动归档旧版本）；所有人可“临时查看”（仅本人可见，按 TTL 过期）。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  MC.openImport = function (app, preset) {
    preset = preset || {};
    var canSave = app.can("deployer");
    var state = { source: preset.source || "local", files: [], persist: canSave };
    var limit = app.session.limits.upload_max_bytes;

    var fileInput = h("input", { type: "file", multiple: true, accept: ".xml,.json,.ini,.cfg,.conf,.txt,application/xml,application/json,text/xml,text/plain", class: "sr-only" });
    var drop = h("div.dropzone", { tabindex: "0", role: "button", "aria-label": "选择或拖入文件" }, icon("upload"), h("div", h("strong", "点击选择"), " 或将 XML / JSON / INI / CFG / CONF / TXT 文件拖到此处"), h("div.xsmall", "可一次选择多个文件。单个文件不超过 " + util.formatBytes(limit)));
    var chosen = h("div.file-chip-list");
    var nameInput = h("input.input.input-mono", { type: "text", placeholder: "例如 config.xml", autocomplete: "off" });
    var urlInput = h("input.input.input-mono", { type: "url", placeholder: "http://10.20.1.15/cfg/config.xml", autocomplete: "off" });
    var nameField = MC.field({ label: "文件名", required: true, control: nameInput, help: "决定服务器上的文件名；扩展名用于识别格式。选择多个文件时，各自使用原文件名。" });
    var nameWrap = h("div", nameField);
    var urlField = MC.field({ label: "下载链接", required: true, control: urlInput, help: "仅支持 http / https，服务器端下载，超时 30 秒" });
    var existsNote = h("div.banner.banner-warning.hidden", icon("alert"), h("div.banner-body"));
    var submit = h("button.btn.btn-primary", { type: "button" }, "导入并打开");

    function renderChosen() {
      util.clear(chosen);
      state.files.forEach(function (f) {
        chosen.appendChild(
          h(
            "div.file-chip",
            util.fmtTag(f.name),
            h("span.grow.truncate", f.name),
            h("span.xsmall.muted", util.formatBytes(f.size)),
            h("button.btn.btn-ghost.btn-icon.btn-sm", {
              type: "button",
              "aria-label": "移除 " + f.name,
              onclick: function () {
                state.files = state.files.filter(function (x) { return x !== f; });
                if (state.files.length !== 1) delete nameInput.dataset.touched;
                renderChosen();
              },
            }, icon("x"))
          )
        );
      });
      var single = state.files.length === 1;
      nameWrap.classList.toggle("hidden", state.files.length > 1);
      if (single && !nameInput.dataset.touched) nameInput.value = state.files[0].name;
      if (!state.files.length && !nameInput.dataset.touched) nameInput.value = "";
      submit.textContent = state.source === "local" && state.files.length > 1 ? "导入 " + state.files.length + " 个文件" : "导入并打开";
      drop.classList.toggle("is-compact", state.files.length > 0);
      checkExists();
    }
    function addFiles(list) {
      var next = state.files.slice();
      var tooBig = [];
      var skipped = [];
      Array.prototype.forEach.call(list || [], function (f) {
        if (!f) return;
        if (f.size > limit) { tooBig.push(f.name); return; }
        if (next.some(function (x) { return x.name === f.name; })) { skipped.push(f.name); return; }
        next.push(f);
      });
      if (tooBig.length) MC.toast.error("文件过大", tooBig.join("、") + " 超过 " + util.formatBytes(limit) + " 上限");
      if (skipped.length) MC.toast.warning("已跳过同名文件", skipped.join("、"));
      state.files = next;
      if (next.length !== 1) delete nameInput.dataset.touched;
      renderChosen();
    }
    drop.addEventListener("click", function () { fileInput.click(); });
    drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
    drop.addEventListener("dragover", function (e) { e.preventDefault(); drop.classList.add("is-over"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("is-over"); });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      drop.classList.remove("is-over");
      addFiles(e.dataTransfer.files);
    });
    fileInput.addEventListener("change", function () {
      addFiles(fileInput.files);
      fileInput.value = "";
    });
    function nameFromUrl(raw) {
      var text = String(raw || "").trim().split("#")[0];
      var qpos = text.indexOf("?");
      var path = qpos >= 0 ? text.slice(0, qpos) : text;
      var query = qpos >= 0 ? text.slice(qpos + 1) : "";
      function decode(value) {
        try { return decodeURIComponent(String(value || "").replace(/\+/g, " ")); } catch (e) { return String(value || ""); }
      }
      var leaf = decode(path.split("/").pop() || "").trim();
      var fromQuery = "";
      query.split("&").forEach(function (pair) {
        if (!pair) return;
        var eq = pair.indexOf("=");
        var key = decode(eq >= 0 ? pair.slice(0, eq) : pair);
        if (key !== "filename" && key !== "file" && key !== "name") return;
        fromQuery = decode(eq >= 0 ? pair.slice(eq + 1) : "").split("/").pop().split("\\").pop();
      });
      function extOf(name) {
        var i = String(name || "").lastIndexOf(".");
        return i > 0 ? name.slice(i).toLowerCase() : "";
      }
      var options = [];
      if (leaf) options.push(leaf);
      if (fromQuery) options.push(fromQuery);
      var i;
      for (i = 0; i < options.length; i++) if ([".xml", ".json", ".ini", ".cfg", ".conf", ".txt"].indexOf(extOf(options[i])) >= 0) return options[i];
      for (i = 0; i < options.length; i++) if (extOf(options[i])) return options[i];
      return options[0] || "";
    }
    urlInput.addEventListener("input", function () {
      if (!nameInput.dataset.touched) {
        nameInput.value = nameFromUrl(urlInput.value);
        checkExists();
      }
    });
    nameInput.addEventListener("input", function () { nameInput.dataset.touched = "1"; checkExists(); });

    function plannedNames() {
      if (state.source === "url") return [nameInput.value.trim()].filter(Boolean);
      if (state.files.length === 1) return [nameInput.value.trim() || state.files[0].name];
      return state.files.map(function (f) { return f.name; });
    }
    function existingNames() {
      return plannedNames().filter(function (n) { return app.files.some(function (f) { return f.name === n; }); });
    }
    function checkExists() {
      var names = state.persist ? existingNames() : [];
      existsNote.classList.toggle("hidden", !names.length);
      existsNote.querySelector(".banner-body").textContent = names.length === 1
        ? "服务器上已有 " + names[0] + "，保存后当前版本将归档为历史版本，可随时对比或下载。"
        : "以下文件已存在，保存后当前版本将归档为历史版本：" + names.join("、") + "。";
    }

    var modeGroup = h("div.col", { role: "radiogroup", "aria-label": "导入方式" });
    function renderMode() {
      util.clear(modeGroup);
      [
        { v: true, title: "保存到服务器", desc: "对所有用户可见。同名文件的当前版本将自动归档。", disabled: !canSave, why: "仅管理员可保存" },
        { v: false, title: "临时查看", desc: "仅当前会话可见，" + app.session.limits.temp_ttl_hours + " 小时后自动清除，不写入服务器文件。" },
      ].forEach(function (o) {
        var input = h("input", { type: "radio", name: "imp-mode", checked: state.persist === o.v, disabled: !!o.disabled });
        input.addEventListener("change", function () { state.persist = o.v; checkExists(); });
        modeGroup.appendChild(
          h(
            "label.choice" + (state.persist === o.v ? ".is-checked" : ""),
            { style: o.disabled ? "opacity:.6;cursor:not-allowed" : null },
            input,
            h("div", h("div", { style: "font-weight:500" }, o.title), h("div.c-meta", o.disabled ? o.why : o.desc))
          )
        );
      });
    }
    renderMode();
    modeGroup.addEventListener("change", renderMode);

    var sourcePanel = h("div.col", { style: "gap:12px" });
    var seg = MC.segmented([{ value: "local", label: "本地文件", icon: "upload" }, { value: "url", label: "从链接下载", icon: "link" }], state.source, function (v) {
      state.source = v;
      seg.setValue(v);
      renderSource();
    }, "导入来源");
    function renderSource() {
      util.clear(sourcePanel);
      if (state.source === "local") sourcePanel.append(drop, chosen, fileInput);
      else sourcePanel.append(urlField);
      nameWrap.classList.toggle("hidden", state.source === "local" && state.files.length > 1);
      submit.textContent = state.source === "local" && state.files.length > 1 ? "导入 " + state.files.length + " 个文件" : "导入并打开";
      drop.classList.toggle("is-compact", state.source === "local" && state.files.length > 0);
      checkExists();
    }
    renderSource();

    var body = h("div.col", { style: "gap:14px" }, seg, sourcePanel, nameWrap, h("div.field", h("span.field-label", "导入方式"), modeGroup), existsNote);

    var m = MC.modal({
      title: "导入配置文件",
      subtitle: "支持 XML 与 JSON，本地文件可一次选择多个。导入前将先校验文件能否解析。",
      body: body,
      size: null,
      footer: function (close) {
        submit.addEventListener("click", function () { run(close); });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), submit];
      },
    });

    function invalidName(name) {
      return !name || /[\\/:*?"<>|]/.test(name);
    }
    function finish(close, results) {
      close();
      var persisted = results[0] && results[0].persisted;
      if (persisted) {
        var archived = results.filter(function (r) { return r.archived; }).length;
        var unchanged = results.filter(function (r) { return r.unchanged; }).length;
        var note = archived ? "同名文件的当前版本已归档。" : unchanged ? "未产生新的历史版本。" : null;
        MC.toast.success(results.length === 1 ? (results[0].unchanged ? "内容与服务器版本一致" : "已保存 " + results[0].name) : "已导入 " + results.length + " 个文件", results.length === 1 && results[0].archived ? "旧版本已归档为 " + results[0].archived : note);
        app.syncRevision();
        Promise.resolve(app.refreshFiles && app.refreshFiles()).then(function () { if (results.length === 1) app.openFile(results[0].name); });
      } else {
        MC.toast.info(results.length === 1 ? "已打开临时文件 " + results[0].name : "已打开 " + results.length + " 个临时文件", "仅当前会话可见，不写入服务器文件。");
        results.forEach(function (r) { app.go({ kind: "t", arg: r.token }); });
      }
    }
    function run(close) {
      nameField.setError("");
      urlField.setError("");
      if (state.source === "local" && !state.files.length) return MC.toast.warning("请先选择文件");
      if (state.source === "url" && !/^https?:\/\//i.test(urlInput.value.trim())) return urlField.setError("请输入以 http:// 或 https:// 开头的链接");

      var items = state.source === "url"
        ? [{ name: nameInput.value.trim() }]
        : state.files.map(function (f) { return { file: f, name: state.files.length === 1 ? nameInput.value.trim() : f.name }; });
      var bad = items.filter(function (it) { return invalidName(it.name); });
      if (bad.length) {
        var msg = bad.length === items.length && items.length === 1
          ? (items[0].name ? "文件名不能包含 \\ / : * ? \" < > |" : "请输入文件名")
          : "";
        if (msg) return nameField.setError(msg);
        return MC.toast.warning("文件名无效", bad.map(function (it) { return it.file ? it.file.name : it.name; }).join("、"));
      }
      var seen = {};
      var dup = [];
      items.forEach(function (it) {
        if (seen[it.name]) dup.push(it.name);
        seen[it.name] = true;
      });
      if (dup.length) return MC.toast.warning("文件名重复", dup.join("、"));

      var exists = existingNames();
      MC.withBusy(submit, function () {
        var ask = state.persist && exists.length
          ? MC.confirm({
              title: exists.length === 1 ? "覆盖 " + exists[0] : "覆盖 " + exists.length + " 个文件",
              message: exists.length === 1
                ? "服务器上已有同名文件。保存后当前版本将归档为历史版本；内容完全相同则不生成新的历史版本。"
                : "以下文件已存在：" + exists.join("、") + "。保存后当前版本将归档为历史版本；内容完全相同则不生成新的历史版本。",
              confirmText: "保存并归档",
            })
          : Promise.resolve(true);
        return ask.then(function (ok) {
          if (!ok) return null;
          var done = [];
          var doneFiles = [];
          function uploadAt(i) {
            if (i >= items.length) return Promise.resolve(done);
            var it = items[i];
            var clash = app.files.some(function (f) { return f.name === it.name; }) || done.some(function (d) { return d.name === it.name; });
            var p = state.source === "local"
              ? (state.persist
                ? api.upload(app.p("/files/" + api.seg(it.name)), it.file, it.name, clash ? { overwrite: "1" } : {}).then(function (r) { return { persisted: true, name: it.name, archived: r.archived, unchanged: r.unchanged }; })
                : api.upload(app.p("/temp"), it.file, it.name).then(function (r) { return { persisted: false, token: r.token, name: it.name }; }))
              : api.post(app.p("/import-url"), { url: urlInput.value.trim(), name: it.name, persist: state.persist, overwrite: clash }).then(function (r) {
                  return { persisted: r.persisted, token: r.token, name: it.name, archived: r.archived };
                });
            return p.then(function (row) {
              done.push(row);
              if (it.file) doneFiles.push(it.file);
              return uploadAt(i + 1);
            }, function (err) {
              err.failedName = it.name;
              err.done = done;
              err.doneFiles = doneFiles;
              throw err;
            });
          }
          return uploadAt(0).then(function (results) { finish(close, results); }, function (err) {
            if (err.doneFiles && err.doneFiles.length) {
              state.files = state.files.filter(function (f) { return err.doneFiles.indexOf(f) < 0; });
              renderChosen();
              app.syncRevision();
              if (app.refreshFiles) app.refreshFiles();
            }
            var prefix = err.done && err.done.length ? "已导入 " + err.done.length + " 个，" + err.failedName + " 未能导入。" : "";
            if ((state.source === "url" || state.files.length <= 1) && err.code === "FILE_EXISTS") nameField.setError("已存在同名文件");
            else if (err.detail && err.detail.field === "url") urlField.setError(MC.errorText(err));
            else MC.toast.error(err.code === "PARSE_FAILED" ? "无法解析该文件" : "导入失败", prefix + MC.errorText(err));
          });
        });
      });
    }
    return m;
  };
})(window);
