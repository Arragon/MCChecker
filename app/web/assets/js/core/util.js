/*
 * DOM / 格式化 / 通用工具。
 * 安全约定：所有动态内容必须通过 h() 的字符串子节点（textContent）插入，禁止 innerHTML 拼接数据。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});

  function isNode(x) {
    return x && typeof x === "object" && typeof x.nodeType === "number";
  }

  function append(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      for (var i = 0; i < child.length; i++) append(el, child[i]);
      return;
    }
    if (isNode(child)) {
      el.appendChild(child);
      return;
    }
    el.appendChild(document.createTextNode(String(child)));
  }

  /**
   * h("button.btn.btn-primary", {onclick: fn, type: "button"}, "保存")
   * attrs: class / style(object|string) / dataset(object) / on*(function) / 其余按 attribute 设置；
   * 值为 false/null/undefined 的 attribute 不设置，true 设置为空字符串。
   */
  function h(sel, attrs) {
    var parts = String(sel).split(".");
    var tag = parts[0] || "div";
    var el = document.createElement(tag);
    if (parts.length > 1) el.className = parts.slice(1).join(" ");
    var start = 1;
    if (attrs && typeof attrs === "object" && !isNode(attrs) && !Array.isArray(attrs)) {
      start = 2;
      Object.keys(attrs).forEach(function (key) {
        var val = attrs[key];
        if (val === null || val === undefined || val === false) return;
        if (key === "class") {
          el.className = (el.className ? el.className + " " : "") + val;
        } else if (key === "style") {
          if (typeof val === "string") el.style.cssText = val;
          else Object.keys(val).forEach(function (k) { el.style[k] = val[k]; });
        } else if (key === "dataset") {
          Object.keys(val).forEach(function (k) { el.dataset[k] = val[k]; });
        } else if (key.slice(0, 2) === "on" && typeof val === "function") {
          el.addEventListener(key.slice(2), val);
        } else if (key === "value") {
          el.value = val;
        } else if (key === "checked" || key === "disabled" || key === "selected" || key === "readOnly") {
          el[key] = !!val;
        } else {
          el.setAttribute(key, val === true ? "" : String(val));
        }
      });
    }
    for (var i = start; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    return el;
  }

  function replace(el) {
    clear(el);
    for (var i = 1; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }

  /** 关键字高亮：返回 DocumentFragment，内容全部为文本节点。 */
  function highlight(text, kw) {
    var frag = document.createDocumentFragment();
    var s = text === null || text === undefined ? "" : String(text);
    var k = (kw || "").trim().toLowerCase();
    if (!k) {
      frag.appendChild(document.createTextNode(s));
      return frag;
    }
    var lower = s.toLowerCase();
    var pos = 0;
    var idx = lower.indexOf(k);
    while (idx >= 0) {
      if (idx > pos) frag.appendChild(document.createTextNode(s.slice(pos, idx)));
      var mark = document.createElement("mark");
      mark.className = "hl";
      mark.textContent = s.slice(idx, idx + k.length);
      frag.appendChild(mark);
      pos = idx + k.length;
      idx = lower.indexOf(k, pos);
    }
    if (pos < s.length) frag.appendChild(document.createTextNode(s.slice(pos)));
    return frag;
  }

  function debounce(fn, wait) {
    var t = null;
    var wrapped = function () {
      var args = arguments;
      var self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, wait);
    };
    wrapped.cancel = function () { clearTimeout(t); };
    return wrapped;
  }

  function rafThrottle(fn) {
    var scheduled = false;
    return function () {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(function () {
        scheduled = false;
        fn();
      });
    };
  }

  function formatBytes(n) {
    if (n === null || n === undefined || isNaN(n)) return "-";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  /** 服务端时间格式统一为 "YYYY-MM-DD HH:MM:SS"（本地时区）。 */
  function parseTime(s) {
    if (!s) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(s);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  }

  function nowString() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  /** 今天显示 "今天 14:05"，今年显示 "10-15 14:05"，否则完整日期。 */
  function formatTime(s) {
    var d = parseTime(s);
    if (!d) return s || "-";
    var now = new Date();
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return "今天 " + hm;
    var y = new Date(now.getTime() - 86400000);
    if (d.toDateString() === y.toDateString()) return "昨天 " + hm;
    if (d.getFullYear() === now.getFullYear()) return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + hm;
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function formatNumber(x) {
    if (x === null || x === undefined || x === "") return "";
    var n = Number(x);
    if (!isFinite(n)) return String(x);
    if (n !== 0 && (Math.abs(n) >= 1e9 || Math.abs(n) < 1e-6)) return n.toExponential(6).replace(/\.?0+e/, "e");
    return String(Math.round(n * 1e10) / 1e10);
  }

  function fileFormat(name) {
    var m = /\.([a-z0-9]+)$/i.exec(name || "");
    var ext = m ? m[1].toLowerCase() : "";
    if (ext === "xml") return "xml";
    if (ext === "json") return "json";
    return "other";
  }

  function fmtTag(name) {
    var f = fileFormat(name);
    return h("span.fmt-tag.fmt-" + f, { "aria-hidden": "true" }, f === "other" ? "TXT" : f.toUpperCase());
  }

  /** 剪贴板：优先 Clipboard API；HTTP 内网环境回退到 execCommand。返回 Promise<boolean>。 */
  function copyText(text) {
    var s = String(text === null || text === undefined ? "" : text);
    if (navigator.clipboard && global.isSecureContext) {
      return navigator.clipboard.writeText(s).then(function () { return true; }, function () { return fallback(); });
    }
    return Promise.resolve(fallback());
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = s;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      return ok;
    }
  }

  function downloadBlob(content, filename, mime) {
    var blob = content instanceof Blob ? content : new Blob([content], { type: mime || "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = h("a", { href: url, download: filename, rel: "noopener" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function uid(prefix) {
    var rnd = Math.random().toString(36).slice(2, 10);
    return (prefix || "id") + "-" + Date.now().toString(36) + rnd;
  }

  function workspaceScope() {
    try {
      return (MC.workspaces && MC.workspaces.scopeId) ? String(MC.workspaces.scopeId() || "") : "";
    } catch (e) { return ""; }
  }

  function storageKey(key) {
    var scope = workspaceScope();
    if (!scope || key === "mc.workspace" || key === "mc.demo.identity") return key;
    return "mcc-v3-workspace:" + scope + ":" + key;
  }

  function storageGet(key, fallback) {
    try {
      var raw = global.localStorage.getItem(storageKey(key));
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }

  function storageSet(key, value) {
    try {
      global.localStorage.setItem(storageKey(key), JSON.stringify(value));
    } catch (e) {
      /* 配额或隐私模式：静默降级为仅内存状态 */
    }
  }

  function initials(name) {
    var s = String(name || "?").trim();
    if (/^[\u4e00-\u9fa5]/.test(s)) return s.charAt(0);
    return s.slice(0, 2).toUpperCase();
  }

  /**
   * 顶栏要显示的身份名称。未登记时服务端把识别地址（IP）当作 name 返回，
   * 而 §4.3 规定地址只在身份弹层里显示（带复制按钮），所以顶栏不能直接用它。
   */
  function identityName(id) {
    if (!id) return "";
    if (id.registered === false) return "未登记";
    return id.name || "";
  }

  /** 安全外链：仅允许 http/https。 */
  function safeUrl(url) {
    try {
      var u = new URL(url, global.location.href);
      return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  MC.util = {
    h: h,
    clear: clear,
    replace: replace,
    highlight: highlight,
    debounce: debounce,
    rafThrottle: rafThrottle,
    formatBytes: formatBytes,
    formatTime: formatTime,
    formatNumber: formatNumber,
    parseTime: parseTime,
    nowString: nowString,
    fileFormat: fileFormat,
    fmtTag: fmtTag,
    copyText: copyText,
    downloadBlob: downloadBlob,
    uid: uid,
    storageGet: storageGet,
    storageSet: storageSet,
    initials: initials,
    identityName: identityName,
    safeUrl: safeUrl,
  };
})(window);
