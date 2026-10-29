/*
 * 通用 UI 组件：Toast / Modal / Confirm / Popover 菜单 / 状态块 / 表单控件。
 * 所有组件只接收纯文本或 DOM 节点，不接受 HTML 字符串。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var h = MC.util.h;
  var icon = MC.icon;

  /* ===================== Toast ===================== */
  var stack = null;
  var TOAST_ICON = { success: "check-circle", error: "x-circle", warning: "alert", info: "info" };

  function toast(type, title, desc, opts) {
    opts = opts || {};
    if (!stack) {
      stack = h("div.toast-stack", { role: "status", "aria-live": "polite" });
      document.body.appendChild(stack);
    }
    var actionBtn = opts.action
      ? h("button.btn.btn-sm", { type: "button", onclick: function () { close(); opts.action.onClick(); } }, opts.action.label)
      : null;
    var closeBtn = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "关闭提示", onclick: close }, icon("x"));
    var el = h(
      "div.toast.toast-" + type,
      icon(TOAST_ICON[type] || "info"),
      h("div.toast-body", h("div.toast-title", title), desc ? h("div.toast-desc", desc) : null),
      actionBtn,
      closeBtn
    );
    stack.appendChild(el);
    var timer = setTimeout(close, opts.duration || (type === "error" ? 8000 : 3500));
    function close() {
      clearTimeout(timer);
      if (el.parentNode) el.parentNode.removeChild(el);
    }
    return close;
  }

  MC.toast = {
    success: function (t, d, o) { return toast("success", t, d, o); },
    error: function (t, d, o) { return toast("error", t, d, o); },
    warning: function (t, d, o) { return toast("warning", t, d, o); },
    info: function (t, d, o) { return toast("info", t, d, o); },
  };

  /** 把 ApiError 转换为用户可读文案（不暴露堆栈/路径）。 */
  var CODE_TEXT = {
    FORBIDDEN: "没有执行该操作的权限",
    NOT_FOUND: "目标不存在或已被删除",
    INVALID_INPUT: "输入内容不符合要求",
    SOURCE_CHANGED: "源文件已被更新，请刷新后重试",
    REVIEW_CONFLICT: "源文件已变化，审阅结果未提交",
    REVIEW_UNSAFE: "生成结果未通过一致性校验，已阻止生成",
    REVISION_CONFLICT: "数据已被他人修改，请刷新后再保存",
    FILE_EXISTS: "已存在同名文件",
    TOO_LARGE: "文件超过大小限制",
    BUSY: "该机型已有互斥任务在运行（单条更新/全量更新/拉取修改记录三者互斥）",
    DOWNLOAD_FAILED: "从链接下载失败",
    PARSE_FAILED: "文件无法解析为 XML/JSON",
    FAVORITE_COVERED: "该节点已被父级收藏包含，请先取消父级",
    STORAGE_FAILURE: "服务器写入失败，数据未保存",
    CORRUPT_DATA: "服务器数据文件损坏，已进入只读保护",
    NETWORK: "无法连接服务器",
  };

  function errorText(err) {
    if (!err) return "未知错误";
    if (err.message && err.code && CODE_TEXT[err.code] && err.message === CODE_TEXT[err.code]) return err.message;
    return err.message || CODE_TEXT[err.code] || "操作失败";
  }

  MC.errorText = errorText;
  MC.toastError = function (title, err, opts) {
    // M9-05：工作区切换后旧请求的拒绝不应再弹错误。
    if (err && err.code === "STALE_CONTEXT") return;
    return toast("error", title, errorText(err), opts);
  };

  /* ===================== Modal ===================== */
  var FOCUSABLE = 'button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  /**
   * MC.modal({title, subtitle, body: Node, footer: Node[]|fn(close)=>Node[], size: 'wide'|'xwide', onClose, dismissible})
   * 返回 {close, el}
   */
  function modal(opts) {
    var prevFocus = document.activeElement;
    var titleId = MC.util.uid("dlg");
    var dialog = h("div.modal" + (opts.size ? ".is-" + opts.size : ""), { role: "dialog", "aria-modal": "true", "aria-labelledby": titleId });
    var backdrop = h("div.modal-backdrop", dialog);
    var closed = false;

    function close(result) {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", onKey, true);
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      if (opts.onClose) opts.onClose(result);
    }

    function onKey(e) {
      if (e.key === "Escape" && opts.dismissible !== false) {
        e.stopPropagation();
        close();
      } else if (e.key === "Tab") {
        var items = dialog.querySelectorAll(FOCUSABLE);
        if (!items.length) return;
        var first = items[0];
        var last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    dialog.appendChild(
      h(
        "div.modal-header",
        h("div.grow", h("h2", { id: titleId }, opts.title), opts.subtitle ? h("div.modal-sub", opts.subtitle) : null),
        opts.dismissible === false ? null : h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "关闭", onclick: function () { close(); } }, icon("x"))
      )
    );
    dialog.appendChild(h("div.modal-body", opts.body));
    var footer = typeof opts.footer === "function" ? opts.footer(close) : opts.footer;
    if (footer) dialog.appendChild(h("div.modal-footer", footer));

    backdrop.addEventListener("mousedown", function (e) {
      if (e.target === backdrop && opts.dismissible !== false) close();
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(backdrop);
    setTimeout(function () {
      var target = dialog.querySelector("[autofocus]") || dialog.querySelector(".modal-body " + FOCUSABLE) || dialog.querySelector(FOCUSABLE);
      if (target) target.focus();
    }, 0);
    return { close: close, el: dialog };
  }

  MC.modal = modal;

  /**
   * 确认框：危险操作必须说明影响范围与是否可恢复。
   * MC.confirm({title, message, detail: Node, confirmText, danger, requireText}) → Promise<boolean>
   */
  MC.confirm = function (opts) {
    return new Promise(function (resolve) {
      var typed = null;
      var okBtn = h(
        "button.btn" + (opts.danger ? ".btn-danger" : ".btn-primary"),
        { type: "button", disabled: !!opts.requireText },
        opts.confirmText || "确定"
      );
      var body = h(
        "div.col",
        { style: "gap:12px" },
        opts.message ? h("p", opts.message) : null,
        opts.detail || null,
        opts.requireText
          ? h(
              "div.field",
              h("label.field-label", "请输入 ", h("code", opts.requireText), " 以确认"),
              (typed = h("input.input", {
                type: "text",
                autocomplete: "off",
                oninput: function () { okBtn.disabled = typed.value.trim() !== opts.requireText; },
              }))
            )
          : null
      );
      var m = modal({
        title: opts.title,
        body: body,
        onClose: function (res) { resolve(res === true); },
        footer: function (close) {
          okBtn.addEventListener("click", function () { close(true); });
          return [h("button.btn", { type: "button", onclick: function () { close(false); } }, opts.cancelText || "取消"), okBtn];
        },
      });
      return m;
    });
  };

  /* ===================== Popover / Menu ===================== */
  var openPop = null;

  function closePopover() {
    if (openPop) {
      var p = openPop;
      openPop = null;
      p.destroy();
    }
  }

  /**
   * MC.popover(anchor, contentNode, {align: 'start'|'end', onClose})
   * 定位在 anchor 下方；点击外部 / Esc 关闭；菜单项支持 ↑↓ 键。
   */
  function popover(anchor, content, opts) {
    opts = opts || {};
    closePopover();
    var el = h("div.popover", { role: opts.role || "dialog" }, content);
    document.body.appendChild(el);
    var r = anchor.getBoundingClientRect();
    var w = el.offsetWidth;
    var left = opts.align === "end" ? r.right - w : r.left;
    left = Math.max(8, Math.min(left, global.innerWidth - w - 8));
    var top = r.bottom + 6;
    if (top + el.offsetHeight > global.innerHeight - 8) top = Math.max(8, r.top - el.offsetHeight - 6);
    el.style.left = left + "px";
    el.style.top = top + "px";
    anchor.setAttribute("aria-expanded", "true");

    function onDoc(e) {
      if (!el.contains(e.target) && !anchor.contains(e.target)) closePopover();
    }
    function onKey(e) {
      if (e.key === "Escape") {
        closePopover();
        anchor.focus();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        var items = Array.prototype.slice.call(el.querySelectorAll(".menu-item:not([disabled])"));
        if (!items.length) return;
        e.preventDefault();
        var i = items.indexOf(document.activeElement);
        i = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
        items[i].focus();
      }
    }
    setTimeout(function () {
      document.addEventListener("mousedown", onDoc, true);
      document.addEventListener("keydown", onKey, true);
      var first = el.querySelector(".menu-item, input, button");
      if (first && opts.autofocus !== false) first.focus();
    }, 0);
    var api = {
      el: el,
      destroy: function () {
        document.removeEventListener("mousedown", onDoc, true);
        document.removeEventListener("keydown", onKey, true);
        anchor.setAttribute("aria-expanded", "false");
        if (el.parentNode) el.parentNode.removeChild(el);
        if (opts.onClose) opts.onClose();
      },
    };
    openPop = api;
    return api;
  }

  /** items: [{label, icon, onClick, danger, checked, meta, disabled} | 'sep' | {header}] */
  function menu(anchor, items, opts) {
    var nodes = items.map(function (it) {
      if (it === "sep") return h("div.menu-sep", { role: "separator" });
      if (it.header) return h("div.menu-label", it.header);
      return h(
        "button.menu-item" + (it.danger ? ".is-danger" : ""),
        {
          type: "button",
          role: "menuitem",
          disabled: !!it.disabled,
          title: it.title || null,
          onclick: function () {
            closePopover();
            if (it.onClick) it.onClick();
          },
        },
        it.checked !== undefined ? h("span.menu-check", it.checked ? icon("check") : null) : it.icon ? icon(it.icon) : null,
        h("span.grow.truncate", it.label),
        it.meta ? h("span.menu-meta", it.meta) : null
      );
    });
    return popover(anchor, h("div", { role: "menu" }, nodes), Object.assign({ role: "menu" }, opts || {}));
  }

  MC.popover = popover;
  MC.menu = menu;
  MC.closePopover = closePopover;

  /* ===================== State blocks ===================== */
  /**
   * MC.state({kind:'empty'|'loading'|'error'|'locked', icon, title, desc, actions:[Node]})
   */
  MC.stateBlock = function (o) {
    if (o.kind === "loading") {
      return h("div.state", { role: "status" }, h("div.spinner"), h("div.state-desc", o.title || "正在加载…"));
    }
    var cls = o.kind === "error" ? ".is-error" : o.kind === "locked" ? ".is-locked" : "";
    var ic = o.icon || (o.kind === "error" ? "alert" : o.kind === "locked" ? "lock" : "info");
    return h(
      "div.state" + cls,
      { role: o.kind === "error" ? "alert" : null },
      h("div.state-icon", icon(ic)),
      o.title ? h("div.state-title", o.title) : null,
      o.desc ? h("div.state-desc", o.desc) : null,
      o.actions && o.actions.length ? h("div.state-actions", o.actions) : null
    );
  };

  MC.errorBlock = function (err, retry) {
    return MC.stateBlock({
      kind: "error",
      title: "加载失败",
      desc: errorText(err),
      actions: retry ? [h("button.btn", { type: "button", onclick: retry }, icon("refresh"), "重试")] : null,
    });
  };

  /* ===================== Form helpers ===================== */
  /** MC.field({label, required, help, error, control}) */
  MC.field = function (o) {
    var id = o.control.id || MC.util.uid("f");
    o.control.id = id;
    var err = h("div.field-error", { role: "alert" }, o.error || "");
    if (!o.error) err.classList.add("hidden");
    var wrap = h(
      "div.field" + (o.span ? ".span-all" : ""),
      h("label.field-label", { for: id }, o.label, o.required ? h("span.req", { "aria-hidden": "true" }, "*") : null),
      o.control,
      o.help ? h("div.field-help", o.help) : null,
      err
    );
    wrap.setError = function (msg) {
      err.textContent = msg || "";
      err.classList.toggle("hidden", !msg);
      o.control.classList.toggle("is-invalid", !!msg);
      if (msg) o.control.setAttribute("aria-invalid", "true");
      else o.control.removeAttribute("aria-invalid");
    };
    return wrap;
  };

  MC.switchEl = function (label, checked, onChange, disabled) {
    var input = h("input", { type: "checkbox", role: "switch", checked: checked, disabled: disabled });
    input.addEventListener("change", function () { if (onChange) onChange(input.checked); });
    var el = h("label.switch", input, h("span.switch-track"), h("span", label));
    el.input = input;
    return el;
  };

  /** 分段切换。items: [{value, label, icon, count}] */
  MC.segmented = function (items, value, onChange, ariaLabel) {
    var wrap = h("div.segmented", { role: "tablist", "aria-label": ariaLabel || null });
    function render(v) {
      MC.util.clear(wrap);
      items.forEach(function (it) {
        wrap.appendChild(
          h(
            "button",
            {
              type: "button",
              role: "tab",
              disabled: !!it.disabled,
              "aria-disabled": it.disabled ? "true" : null,
              "aria-selected": String(it.value === v),
              title: it.title || null,
              onclick: function () {
                if (it.disabled) return;
                if (it.value !== v) onChange(it.value);
              },
            },
            it.icon ? icon(it.icon) : null,
            it.label,
            it.count !== undefined && it.count !== null ? h("span.seg-count", String(it.count)) : null
          )
        );
      });
    }
    render(value);
    wrap.setValue = render;
    return wrap;
  };

  /** 带 loading 的异步按钮执行器：防重复提交。 */
  MC.withBusy = function (btn, promiseFactory) {
    if (btn.classList.contains("is-loading")) return Promise.resolve();
    btn.classList.add("is-loading");
    btn.disabled = true;
    function done() {
      btn.classList.remove("is-loading");
      btn.disabled = false;
    }
    return Promise.resolve()
      .then(promiseFactory)
      .then(
        function (v) { done(); return v; },
        function (e) { done(); throw e; }
      );
  };

  MC.copyButton = function (getText, label) {
    var btn = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": label || "复制", "data-tip": label || "复制" }, icon("copy"));
    btn.addEventListener("click", function () {
      MC.util.copyText(typeof getText === "function" ? getText() : getText).then(function (ok) {
        if (ok) MC.toast.success("已复制到剪贴板");
        else MC.toast.warning("复制失败", "浏览器限制了剪贴板，请手动选中文本复制");
      });
    });
    return btn;
  };

  MC.diffStats = function (diff) {
    if (!diff) return h("span.diff-stats", h("span.none", "未计算"));
    if (!diff.has_changes) return h("span.diff-stats", h("span.none", "无变更"));
    var s = diff.summary || {};
    return h(
      "span.diff-stats",
      { "aria-label": "新增 " + (s.added || 0) + "，删除 " + (s.removed || 0) + "，修改 " + (s.modified || 0) },
      h("span.add", "+" + (s.added || 0)),
      h("span.del", "−" + (s.removed || 0)),
      h("span.mod", "~" + (s.modified || 0)),
      s.bound ? h("span.bind", "⛓" + s.bound) : null,
      diff.structural === false ? h("span.none", "仅文本") : null
    );
  };
})(window);
