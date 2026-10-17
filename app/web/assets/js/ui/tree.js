/*
 * 文档索引（MC.doc）+ 虚拟滚动树（MC.Tree）。
 *
 * 服务端文档格式（compact document，先序遍历，index 0 为文件根）：
 *   nodes: { p:[父索引], t:[类型], l:[标签], v:[值|null], id:[旧版路径ID], loc:[定位符] }
 * 前端在此基础上计算 depth / size（子树节点数，含自身）/ childCount，
 * 折叠时直接 i += size[i] 跳过整棵子树，渲染成本只与可视行数有关。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var PARAM_SECONDARY = { "@description": 1, "@editPrivilege": 1 };
  var PARAM_RANGE = { "@default": 1, "@min": 1, "@max": 1, "@incMin": 1, "@incMax": 1 };

  /* ===================== Document index ===================== */
  function buildIndex(doc) {
    var N = doc.nodes;
    var n = N.p.length;
    var depth = new Int16Array(n);
    var size = new Int32Array(n);
    var childCount = new Int32Array(n);
    var metaHidden = new Uint8Array(n);
    var isParam = new Uint8Array(n);
    var paramMeta = {};
    var byId = {};
    var byLoc = {};
    var i;

    for (i = 0; i < n; i++) {
      size[i] = 1;
      var pi = N.p[i];
      if (pi >= 0) {
        depth[i] = depth[pi] + 1;
        childCount[pi]++;
      }
      if (byId[N.id[i]] === undefined) byId[N.id[i]] = i;
      byLoc[N.loc[i]] = i;
    }
    // 先序遍历：逆序累加即可得到子树大小
    for (i = n - 1; i > 0; i--) size[N.p[i]] += size[i];

    // 识别 param_ 参数节点：其描述/范围属性折叠进行内展示（与旧版 viewer 一致）
    for (i = 0; i < n; i++) {
      if (N.v[i] === null || childCount[i] === 0 || String(N.l[i]).indexOf("param_") !== 0) continue;
      var meta = null;
      var end = i + size[i];
      for (var c = i + 1; c < end; c += size[c]) {
        var lab = N.l[c];
        if (PARAM_SECONDARY[lab] || PARAM_RANGE[lab]) {
          meta = meta || {};
          meta[lab.slice(1)] = N.v[c];
          metaHidden[c] = 1;
        } else if (N.t[c] === "attribute") {
          meta = meta || {};
          meta[lab.slice(1)] = N.v[c];
        }
      }
      if (meta && (meta.description !== undefined || meta.default !== undefined || meta.min !== undefined || meta.max !== undefined || meta.editPrivilege !== undefined)) {
        isParam[i] = 1;
        paramMeta[i] = meta;
      } else {
        for (c = i + 1; c < end; c += size[c]) metaHidden[c] = 0;
      }
    }

    var lowerL = null;
    var lowerV = null;

    var idx = {
      doc: doc,
      n: n,
      p: N.p,
      t: N.t,
      l: N.l,
      v: N.v,
      id: N.id,
      loc: N.loc,
      depth: depth,
      size: size,
      childCount: childCount,
      metaHidden: metaHidden,
      isParam: isParam,
      paramMeta: paramMeta,
      byId: byId,
      byLoc: byLoc,
      lower: function () {
        if (!lowerL) {
          lowerL = new Array(n);
          lowerV = new Array(n);
          for (var k = 0; k < n; k++) {
            lowerL[k] = String(N.l[k]).toLowerCase();
            lowerV[k] = N.v[k] === null ? "" : String(N.v[k]).toLowerCase();
          }
        }
        return { l: lowerL, v: lowerV };
      },
      /** 面向用户的路径：CONFIG_ROOT › SCENARIO_BLOCK › input › param_02 */
      displayPath: function (k, sep) {
        var parts = [];
        while (k > 0) {
          parts.unshift(N.l[k]);
          k = N.p[k];
        }
        return parts.join(sep || " › ");
      },
      ancestors: function (k) {
        var out = [];
        k = N.p[k];
        while (k > 0) {
          out.push(k);
          k = N.p[k];
        }
        return out;
      },
      attrsOf: function (k) {
        var out = [];
        var end = k + size[k];
        for (var c = k + 1; c < end; c += size[c]) if (N.t[c] === "attribute") out.push(c);
        return out;
      },
      valueCount: function () {
        var cnt = 0;
        for (var k = 0; k < n; k++) if (N.v[k] !== null) cnt++;
        return cnt;
      },
    };
    return idx;
  }

  MC.doc = { buildIndex: buildIndex };

  /* ===================== Virtual tree ===================== */
  var OVERSCAN = 12;

  function valueClass(t, v) {
    if (t === "int" || t === "float") return "t-num";
    if (t === "bool") return "t-bool";
    if (t === "NoneType") return "t-null";
    if (t === "element" || t === "attribute" || t === "text") {
      if (v === "true" || v === "false") return "t-bool";
      if (/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(v)) return "t-num";
    }
    return "";
  }

  function kindClass(t, label) {
    if (t === "attribute") return "k-attr";
    if (t === "text") return "k-text";
    if (/^\[\d+\]$/.test(label)) return "k-index";
    return "";
  }

  /**
   * new MC.Tree(host, idx, {
   *   showAttrs: true,
   *   decor(i) -> {star: 'on'|'covered'|null, remarks: n},
   *   onSelect(i), onStar(i), onActivate(i),
   *   starReason, onStarBlocked(i)  // 收藏接口未接入时：星标渲染但禁用，按 S 给出原因
   * })
   */
  function Tree(host, idx, opts) {
    this.idx = idx;
    this.opts = opts || {};
    this.rowH = 28;
    this.showAttrs = this.opts.showAttrs !== false;
    this.expanded = new Uint8Array(idx.n);
    this.selected = -1;
    this.filter = null; // {kw, show:Uint8Array, matches:[], cur}
    this.rows = [];

    this.el = h("div.vtree", { tabindex: "0", role: "tree", "aria-label": opts.label || "配置结构" });
    this.spacer = h("div.vtree-spacer");
    this.win = h("div.vtree-window", { role: "presentation" });
    this.spacer.appendChild(this.win);
    this.el.appendChild(this.spacer);
    host.appendChild(this.el);

    var self = this;
    this._onScroll = util.rafThrottle(function () { self.paint(); });
    this.el.addEventListener("scroll", this._onScroll);
    this.el.addEventListener("click", function (e) { self.onClick(e); });
    this.el.addEventListener("dblclick", function (e) {
      var row = e.target.closest(".tr");
      if (row) self.toggle(+row.dataset.i);
    });
    this.el.addEventListener("keydown", function (e) { self.onKey(e); });
    if (global.ResizeObserver) {
      this._ro = new ResizeObserver(function () { self.paint(); });
      this._ro.observe(this.el);
    }

    // 默认展开策略：小文件全部展开（与旧版一致）；大文件只展开前两层，避免首屏卡顿
    if (idx.n <= 3000) this.expandAll(true);
    else this.expandLevel(2, true);
    this.layout();
  }

  Tree.prototype.isVisibleNode = function (i) {
    if (this.idx.metaHidden[i]) return false;
    if (!this.showAttrs && this.idx.t[i] === "attribute") return false;
    return true;
  };

  /** 计算可见行：折叠节点跳过整棵子树；过滤时只保留命中节点、其祖先与其子树。 */
  Tree.prototype.layout = function () {
    var idx = this.idx;
    var rows = [];
    var show = this.filter ? this.filter.show : null;
    var i = 1;
    while (i < idx.n) {
      if (!this.isVisibleNode(i) || (show && !show[i])) {
        i += idx.size[i];
        continue;
      }
      rows.push(i);
      if (idx.size[i] > 1 && !this.expanded[i]) i += idx.size[i];
      else i++;
    }
    this.rows = rows;
    this._vcc = {};
    this.spacer.style.height = rows.length * this.rowH + "px";
    this.paint();
  };

  Tree.prototype.visibleChildCount = function (i) {
    if (this._vcc && this._vcc[i] !== undefined) return this._vcc[i];
    var idx = this.idx;
    var cnt = 0;
    var end = i + idx.size[i];
    for (var c = i + 1; c < end; c += idx.size[c]) {
      if (!this.isVisibleNode(c)) continue;
      if (this.filter && !this.filter.show[c]) continue;
      cnt++;
    }
    if (this._vcc) this._vcc[i] = cnt;
    return cnt;
  };

  Tree.prototype.paint = function () {
    var rows = this.rows;
    var top = this.el.scrollTop;
    var height = this.el.clientHeight || 600;
    var start = Math.max(0, Math.floor(top / this.rowH) - OVERSCAN);
    var end = Math.min(rows.length, Math.ceil((top + height) / this.rowH) + OVERSCAN);
    var idx = this.idx;
    var frag = document.createDocumentFragment();
    var decor = this.opts.decor;
    var matchSet = this.filter ? this.filter.matchSet : null;
    var curMatch = this.filter && this.filter.matches.length ? this.filter.matches[this.filter.cur] : -1;

    for (var r = start; r < end; r++) {
      var i = rows[r];
      var hasKids = this.visibleChildCount(i) > 0;
      var open = hasKids && !!this.expanded[i];
      var value = idx.v[i];
      var t = idx.t[i];
      var d = decor ? decor(i) : {};
      var cls = "tr " + kindClass(t, idx.l[i]) + (hasKids ? " is-branch" : "") + (i === this.selected ? " is-selected" : "");
      if (matchSet && matchSet[i]) cls += " is-match";
      if (i === curMatch) cls += " is-current-match";

      var row = h("div", {
        class: cls,
        role: "treeitem",
        "aria-level": String(idx.depth[i]),
        "aria-expanded": hasKids ? String(open) : null,
        "aria-selected": String(i === this.selected),
        dataset: { i: String(i) },
      });
      row.appendChild(h("span.tr-indent", { style: { width: (idx.depth[i] - 1) * 18 + "px" } }));
      row.appendChild(
        h(
          "button.tr-toggle" + (hasKids ? (open ? ".is-open" : "") : ".is-leaf"),
          { type: "button", tabindex: "-1", "aria-label": open ? "折叠" : "展开", dataset: { act: "toggle" } },
          icon("chevron-right")
        )
      );
      if (this.opts.onStar || this.opts.starReason) {
        var starState = d.star || "";
        var starBlocked = !this.opts.onStar;
        row.appendChild(
          h(
            "button.tr-star" + (starState === "on" ? ".is-on" : starState === "covered" ? ".is-covered" : ""),
            {
              type: "button",
              tabindex: "-1",
              disabled: starBlocked,
              "aria-label": starState === "on" ? "取消收藏" : starState === "covered" ? "已被父级收藏包含" : "收藏",
              title: starBlocked ? this.opts.starReason : starState === "covered" ? "已被父级收藏包含" : null,
              dataset: { act: "star" },
            },
            icon("star")
          )
        );
      }
      row.appendChild(h("span.tr-label", idx.l[i]));
      if (value !== null) {
        row.appendChild(h("span.tr-eq", "="));
        row.appendChild(h("span.tr-value " + valueClass(t, value), { title: value.length > 48 ? value : null }, value));
      }
      if (idx.isParam[i]) {
        var pm = idx.paramMeta[i];
        var metaText = [pm.description, pm.editPrivilege].filter(function (x) { return x; }).join(" · ");
        if (metaText) row.appendChild(h("span.tr-meta", metaText));
      }
      var badges = [];
      if (idx.isParam[i] && (idx.paramMeta[i].min !== undefined || idx.paramMeta[i].max !== undefined)) {
        badges.push(h("span.badge", { title: "参数范围见右侧详情" }, rangeText(idx.paramMeta[i])));
      }
      if (d.remarks) badges.push(h("span.badge.badge-warning", icon("message"), String(d.remarks)));
      if (badges.length) row.appendChild(h("span.tr-badges", badges));
      if (hasKids && !open) row.appendChild(h("span.tr-count", "(" + this.visibleChildCount(i) + ")"));
      frag.appendChild(row);
    }
    this.win.style.transform = "translateY(" + start * this.rowH + "px)";
    util.clear(this.win);
    this.win.appendChild(frag);
  };

  function rangeText(pm) {
    var lo = pm.min !== undefined ? (pm.incMin === "false" ? "(" : "[") + pm.min : "(-∞";
    var hi = pm.max !== undefined ? pm.max + (pm.incMax === "false" ? ")" : "]") : "+∞)";
    return lo + ", " + hi;
  }
  Tree.rangeText = rangeText;

  Tree.prototype.onClick = function (e) {
    var row = e.target.closest(".tr");
    if (!row) return;
    var i = +row.dataset.i;
    var act = e.target.closest("[data-act]");
    if (act && act.dataset.act === "toggle") {
      this.toggle(i);
      return;
    }
    if (act && act.dataset.act === "star") {
      this.select(i);
      if (this.opts.onStar) this.opts.onStar(i);
      return;
    }
    this.select(i);
    this.el.focus({ preventScroll: true });
  };

  Tree.prototype.onKey = function (e) {
    var pos = this.rows.indexOf(this.selected);
    var idx = this.idx;
    var i = this.selected;
    var page = Math.max(1, Math.floor(this.el.clientHeight / this.rowH) - 1);
    switch (e.key) {
      case "ArrowDown":
        this.select(this.rows[Math.min(this.rows.length - 1, pos + 1)]);
        break;
      case "ArrowUp":
        this.select(this.rows[Math.max(0, pos - 1)]);
        break;
      case "PageDown":
        this.select(this.rows[Math.min(this.rows.length - 1, pos + page)]);
        break;
      case "PageUp":
        this.select(this.rows[Math.max(0, pos - page)]);
        break;
      case "Home":
        this.select(this.rows[0]);
        break;
      case "End":
        this.select(this.rows[this.rows.length - 1]);
        break;
      case "ArrowRight":
        if (i < 0) return;
        if (this.visibleChildCount(i) > 0 && !this.expanded[i]) this.toggle(i);
        else if (this.visibleChildCount(i) > 0) this.select(this.rows[pos + 1]);
        break;
      case "ArrowLeft":
        if (i < 0) return;
        if (this.expanded[i] && this.visibleChildCount(i) > 0) this.toggle(i);
        else if (idx.p[i] > 0) this.select(idx.p[i]);
        break;
      case "Enter":
        if (i >= 0 && this.opts.onActivate) this.opts.onActivate(i);
        break;
      case "s":
      case "S":
        if (i < 0 || e.ctrlKey || e.metaKey) break;
        if (this.opts.onStar) this.opts.onStar(i);
        else if (this.opts.onStarBlocked) this.opts.onStarBlocked(i);
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  Tree.prototype.toggle = function (i) {
    this.expanded[i] = this.expanded[i] ? 0 : 1;
    this.layout();
  };

  Tree.prototype.select = function (i) {
    if (i === undefined || i < 0) return;
    this.selected = i;
    this.scrollIntoView(i);
    this.paint();
    if (this.opts.onSelect) this.opts.onSelect(i);
  };

  Tree.prototype.scrollIntoView = function (i) {
    var pos = this.rows.indexOf(i);
    if (pos < 0) return;
    var y = pos * this.rowH;
    var top = this.el.scrollTop;
    var hgt = this.el.clientHeight;
    if (y < top) this.el.scrollTop = y;
    else if (y + this.rowH > top + hgt) this.el.scrollTop = y - hgt + this.rowH * 3;
  };

  /** 展开祖先并选中节点（搜索结果跳转、收藏定位）。 */
  Tree.prototype.reveal = function (i) {
    var self = this;
    if (this.filter && !this.filter.show[i]) this.setFilter("");
    this.idx.ancestors(i).forEach(function (a) { self.expanded[a] = 1; });
    this.layout();
    this.select(i);
    var pos = this.rows.indexOf(i);
    if (pos >= 0) this.el.scrollTop = Math.max(0, pos * this.rowH - this.el.clientHeight / 3);
    this.paint();
  };

  Tree.prototype.expandAll = function (silent) {
    this.expanded.fill(1);
    if (!silent) this.layout();
  };

  Tree.prototype.collapseAll = function () {
    this.expanded.fill(0);
    this.layout();
  };

  Tree.prototype.expandLevel = function (level, silent) {
    var idx = this.idx;
    for (var i = 0; i < idx.n; i++) this.expanded[i] = idx.depth[i] < level ? 1 : 0;
    if (!silent) this.layout();
  };

  Tree.prototype.setShowAttrs = function (on) {
    this.showAttrs = on;
    this.layout();
  };

  /**
   * 过滤：命中 label/value/备注 的节点 + 其全部祖先 + 其整棵子树（与旧版搜索语义一致）。
   * 返回命中节点数（不含仅作为上下文显示的祖先/子孙）。
   */
  Tree.prototype.setFilter = function (kw, noteOf) {
    var k = (kw || "").trim().toLowerCase();
    if (!k) {
      this.filter = null;
      this.layout();
      return 0;
    }
    var idx = this.idx;
    var low = idx.lower();
    var n = idx.n;
    var match = new Uint8Array(n);
    var show = new Uint8Array(n);
    var matches = [];
    for (var i = 1; i < n; i++) {
      if (idx.metaHidden[i]) continue;
      var note = noteOf ? noteOf(i) : "";
      if (low.l[i].indexOf(k) >= 0 || low.v[i].indexOf(k) >= 0 || (note && note.toLowerCase().indexOf(k) >= 0)) {
        match[i] = 1;
        matches.push(i);
      }
    }
    var under = new Uint8Array(n);
    for (i = 1; i < n; i++) {
      under[i] = match[i] || under[idx.p[i]] ? 1 : 0;
      if (under[i]) show[i] = 1;
    }
    for (var m = 0; m < matches.length; m++) {
      var a = idx.p[matches[m]];
      while (a > 0 && !show[a]) {
        show[a] = 1;
        this.expanded[a] = 1;
        a = idx.p[a];
      }
      a = idx.p[matches[m]];
      while (a > 0) {
        this.expanded[a] = 1;
        a = idx.p[a];
      }
    }
    this.filter = { kw: k, show: show, matchSet: match, matches: matches, cur: 0 };
    this.layout();
    if (matches.length) this.reveal(matches[0]);
    return matches.length;
  };

  Tree.prototype.gotoMatch = function (dir) {
    var f = this.filter;
    if (!f || !f.matches.length) return null;
    f.cur = (f.cur + dir + f.matches.length) % f.matches.length;
    this.reveal(f.matches[f.cur]);
    return { cur: f.cur + 1, total: f.matches.length };
  };

  Tree.prototype.refresh = function () {
    this.paint();
  };

  Tree.prototype.destroy = function () {
    this.el.removeEventListener("scroll", this._onScroll);
    if (this._ro) this._ro.disconnect();
    if (this.el.parentNode) this.el.parentNode.removeChild(this.el);
  };

  MC.Tree = Tree;
})(window);
