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
    var state = { source: preset.source || "local", file: null, persist: canSave };

    var fileInput = h("input", { type: "file", accept: ".xml,.json,application/xml,application/json,text/xml", class: "sr-only" });
    var drop = h("div.dropzone", { tabindex: "0", role: "button", "aria-label": "选择或拖入文件" }, icon("upload"), h("div", h("strong", "点击选择"), " 或将 XML / JSON 文件拖到此处"), h("div.xsmall", "单个文件不超过 " + util.formatBytes(app.session.limits.upload_max_bytes)));
    var chosen = h("div");
    var nameInput = h("input.input.input-mono", { type: "text", placeholder: "例如 config.xml", autocomplete: "off" });
    var urlInput = h("input.input.input-mono", { type: "url", placeholder: "http://10.20.1.15/cfg/config.xml", autocomplete: "off" });
    var nameField = MC.field({ label: "文件名", required: true, control: nameInput, help: "决定服务器上的文件名；扩展名用于识别格式" });
    var urlField = MC.field({ label: "下载链接", required: true, control: urlInput, help: "仅支持 http / https，服务器端下载，超时 30 秒" });
    var existsNote = h("div.banner.banner-warning.hidden", icon("alert"), h("div.banner-body"));

    function pick(f) {
      if (!f) return;
      if (f.size > app.session.limits.upload_max_bytes) {
        MC.toast.error("文件过大", "超过 " + util.formatBytes(app.session.limits.upload_max_bytes) + " 上限");
        return;
      }
      state.file = f;
      if (!nameInput.value) nameInput.value = f.name;
      util.replace(chosen, h("div.file-chip", util.fmtTag(f.name), h("span.grow.truncate", f.name), h("span.xsmall.muted", util.formatBytes(f.size)), h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "移除", onclick: function () { state.file = null; util.clear(chosen); drop.classList.remove("hidden"); } }, icon("x"))));
      drop.classList.add("hidden");
      checkExists();
    }
    drop.addEventListener("click", function () { fileInput.click(); });
    drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
    drop.addEventListener("dragover", function (e) { e.preventDefault(); drop.classList.add("is-over"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("is-over"); });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      drop.classList.remove("is-over");
      pick(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", function () { pick(fileInput.files[0]); });
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
      for (i = 0; i < options.length; i++) if (extOf(options[i]) === ".xml" || extOf(options[i]) === ".json") return options[i];
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

    function checkExists() {
      var n = nameInput.value.trim();
      var exists = app.files.some(function (f) { return f.name === n; });
      existsNote.classList.toggle("hidden", !(exists && state.persist));
      existsNote.querySelector(".banner-body").textContent = "服务器上已有 " + n + "，保存后当前版本会自动归档为历史版本，可随时对比或下载。";
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
    }
    renderSource();

    var body = h("div.col", { style: "gap:14px" }, seg, sourcePanel, nameField, h("div.field", h("span.field-label", "导入方式"), modeGroup), existsNote);

    var submit = h("button.btn.btn-primary", { type: "button" }, "导入并打开");
    var m = MC.modal({
      title: "导入配置文件",
      subtitle: "支持 XML 与 JSON。导入前将先校验文件能否解析。",
      body: body,
      size: null,
      footer: function (close) {
        submit.addEventListener("click", function () { run(close); });
        return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), submit];
      },
    });

    function run(close) {
      nameField.setError("");
      urlField.setError("");
      var name = nameInput.value.trim();
      if (!name) return nameField.setError("请输入文件名");
      if (/[\\/:*?"<>|]/.test(name)) return nameField.setError("文件名不能包含 \\ / : * ? \" < > |");
      if (state.source === "local" && !state.file) return MC.toast.warning("请先选择文件");
      if (state.source === "url" && !/^https?:\/\//i.test(urlInput.value.trim())) return urlField.setError("请输入以 http:// 或 https:// 开头的链接");
      var exists = app.files.some(function (f) { return f.name === name; });

      MC.withBusy(submit, function () {
        // 同名覆盖前先确认（卡面要求）：确认框与临时文件「保存到服务器」同一套说法。
        var ask = state.persist && exists
          ? MC.confirm({ title: "覆盖 " + name, message: "服务器上已有同名文件。保存后当前版本会归档为历史版本；内容完全相同则不生成新的历史版本。", confirmText: "保存并归档" })
          : Promise.resolve(true);
        return ask.then(function (ok) {
          if (!ok) return null;
          var p;
          if (state.source === "local") {
            p = state.persist
              ? api.upload(app.p("/files/" + api.seg(name)), state.file, name, exists ? { overwrite: "1" } : {}).then(function (r) { return { persisted: true, name: name, archived: r.archived, unchanged: r.unchanged }; })
              : api.upload(app.p("/temp"), state.file, name).then(function (r) { return { persisted: false, token: r.token, name: name }; });
          } else {
            p = api.post(app.p("/import-url"), { url: urlInput.value.trim(), name: name, persist: state.persist, overwrite: exists }).then(function (r) {
              return { persisted: r.persisted, token: r.token, name: name, archived: r.archived };
            });
          }
          return p.then(
            function (r) {
              close();
              if (r.persisted) {
                MC.toast.success(r.unchanged ? "内容与服务器版本一致" : "已保存 " + r.name, r.archived ? "旧版本已归档为 " + r.archived : r.unchanged ? "未产生新的历史版本" : null);
                app.syncRevision();
                app.refreshFiles().then(function () { app.openFile(r.name); });
              } else {
                MC.toast.info("已打开临时文件 " + r.name, "仅你可见，不影响服务器文件");
                app.go({ kind: "t", arg: r.token });
              }
            },
            function (err) {
              if (err.code === "FILE_EXISTS") nameField.setError("已存在同名文件");
              else if (err.detail && err.detail.field === "url") urlField.setError(MC.errorText(err));
              else MC.toastError(err.code === "PARSE_FAILED" ? "无法解析该文件" : "导入失败", err);
            }
          );
        });
      });
    }
    return m;
  };
})(window);
