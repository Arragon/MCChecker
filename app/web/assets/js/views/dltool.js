/*
 * DL 快捷计算：f(x) = k1·x + k2·x² + … + k8·x⁸ + b，三个计算项（PP 能量 / RP 能量 / RP 脉宽）。
 * - 参数修改先进入“本地草稿”，所有人都可用草稿试算；只有部署者能把草稿保存为共享配置（If-Match 防覆盖）。
 * - 计算由服务端完成（POST .../compute，传入草稿参数），结果按 input_index 与输入一一对应，越界行明确标出而不是静默丢弃。
 * - 输入数值后自动计算（防抖）；改参数同样自动重算，结果始终对应当前参数。
 * - RP 脉宽有两套独立的参数（AOM2 delay / AOM2 width）。顶层字段是 delay 这一套，item.aom_width 是 width 这一套；
 *   另一项 = 固定和（item.aom_sum）− y。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  var KEYS = ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "b"];
  var PLOT_KEYS = KEYS.slice(0, 8);
  var SUB = { k1: "k₁", k2: "k₂", k3: "k₃", k4: "k₄", k5: "k₅", k6: "k₆", k7: "k₇", k8: "k₈", b: "b" };
  var POW = { k1: "x", k2: "x²", k3: "x³", k4: "x⁴", k5: "x⁵", k6: "x⁶", k7: "x⁷", k8: "x⁸", b: "常数" };
  var SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹";
  var ITEMS = [
    { id: "pp_energy", label: "PP 能量" },
    { id: "rp_energy", label: "RP 能量" },
    { id: "rp_width", label: "RP 脉宽" },
  ];
  var AXES = {
    pp_energy: { x: "电动波片角度", xUnit: "urad", y: "PP@PF能量", yUnit: "mJ" },
    rp_energy: { x: "RP能量", xUnit: "mJ", y: "TFP2角度(SMTM页面)", yUnit: "urad" },
    rp_width: {
      x: "RP脉宽",
      xUnit: "ns",
      delay: { name: "AOM2 delay(SMSM1页面)", unit: "ns", mode: "AOM2 delay 模式" },
      width: { name: "AOM2 width(SMSM1页面)", unit: "ns", mode: "AOM2 width 模式" },
    },
  };
  var RANGE_KEYS = ["x_min", "x_max", "y_min", "y_max"];

  function withUnit(name, unit) {
    return unit ? name + "（" + unit + "）" : name;
  }

  /** 「AOM2 delay(SMSM1页面)」拆成主名 + 页面备注，两段各自不折行，避免在「页」「面」之间断开。 */
  function splitName(name) {
    var m = /^(.*?)\s*(\([^)]*页面\))\s*$/.exec(String(name));
    return m && m[1] ? { main: m[1], note: m[2] } : { main: String(name), note: "" };
  }

  function nameNode(name, unit) {
    var parts = splitName(name);
    return h("span.dl-name", h("span.dl-name-main", parts.main), parts.note ? h("small.dl-note", parts.note) : null, unit ? h("small.dl-unit-tag", unit) : null);
  }

  function axisOf(id, aom) {
    if (id !== "rp_width") {
      var a = AXES[id];
      return { x: a.x, xUnit: a.xUnit, y: a.y, yUnit: a.yUnit, other: "", otherUnit: "" };
    }
    var cur = aom === "width" ? AXES.rp_width.width : AXES.rp_width.delay;
    var oth = aom === "width" ? AXES.rp_width.delay : AXES.rp_width.width;
    return { x: AXES.rp_width.x, xUnit: AXES.rp_width.xUnit, y: cur.name, yUnit: cur.unit, other: oth.name, otherUnit: oth.unit };
  }

  function parseNumbers(text) {
    var tokens = String(text || "").split(/[\s,;，；]+/).filter(Boolean);
    var values = [];
    var bad = [];
    tokens.forEach(function (t) {
      var v = Number(t);
      if (t !== "" && isFinite(v)) values.push(v);
      else bad.push(t);
    });
    return { values: values, bad: bad };
  }

  function numOrNull(s) {
    var t = String(s === undefined || s === null ? "" : s).trim();
    if (t === "") return null;
    var v = Number(t);
    return isFinite(v) ? v : NaN;
  }

  function finiteNum(v) {
    if (v === null || v === undefined || v === "") return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function clone(x) { return JSON.parse(JSON.stringify(x)); }

  function emptySet() {
    var coeffs = {};
    KEYS.forEach(function (k) { coeffs[k] = 0; });
    return { coeffs: coeffs, binding: { source_file: "", variable_path: "", field_map: {} }, x_min: null, x_max: null, y_min: null, y_max: null };
  }

  /** 多项式文本。limit 给了就只保留前 limit 项并以 … 收尾（用于模式卡片的一行摘要）。 */
  function polyText(set, limit) {
    var c = (set && set.coeffs) || {};
    var parts = [];
    PLOT_KEYS.forEach(function (k, i) {
      var v = Number(c[k]);
      if (!v) return;
      var pow = i === 0 ? "x" : "x" + String(i + 1).replace(/\d/g, function (d) { return SUP[d]; });
      parts.push({ neg: v < 0, body: util.formatNumber(Math.abs(v)) + "·" + pow });
    });
    var b = Number(c.b) || 0;
    if (b || !parts.length) parts.push({ neg: b < 0, body: util.formatNumber(Math.abs(b)) });
    var cut = limit && parts.length > limit;
    if (cut) parts = parts.slice(0, limit);
    var out = parts.map(function (p, i) {
      if (i === 0) return (p.neg ? "−" : "") + p.body;
      return (p.neg ? "− " : "+ ") + p.body;
    }).join(" ");
    return out + (cut ? " …" : "");
  }

  function evalCurveY(set, id, x) {
    var c = set.coeffs || {};
    var y = Number(c.b || 0);
    var p = 1;
    for (var i = 0; i < PLOT_KEYS.length; i++) {
      p *= x;
      var k = Number(c[PLOT_KEYS[i]] || 0);
      if (k) y += k * p;
    }
    if (id === "pp_energy") {
      var den = Number(set.power_conv_coeff);
      var num = Number(set.trans_coeff);
      if (!isFinite(den) || den === 0 || !isFinite(num)) return NaN;
      y *= num / den;
    }
    return y;
  }

  /**
   * 在已填写的 x 上下限内取样。若同时填了 y 上下限，只保留落在 y 范围内的点。
   * 返回这些点上 x、y 各自的最小和最大。x 上下限不齐时 bounded 为 false。
   */
  function curveExtrema(set, id) {
    if (!curveReady(set, id)) return { ready: false, bounded: false, empty: true };
    var xmin = finiteNum(set.x_min);
    var xmax = finiteNum(set.x_max);
    if (xmin === null || xmax === null || !(xmin < xmax)) return { ready: true, bounded: false, empty: true };
    var ymin = finiteNum(set.y_min);
    var ymax = finiteNum(set.y_max);
    var steps = 480;
    var found = null;
    for (var i = 0; i <= steps; i++) {
      var x = xmin + ((xmax - xmin) * i) / steps;
      var y = evalCurveY(set, id, x);
      if (!isFinite(y)) continue;
      if (ymin !== null && y < ymin) continue;
      if (ymax !== null && y > ymax) continue;
      if (!found) found = { xMin: x, xMax: x, yMin: y, yMax: y };
      else {
        if (x < found.xMin) found.xMin = x;
        if (x > found.xMax) found.xMax = x;
        if (y < found.yMin) found.yMin = y;
        if (y > found.yMax) found.yMax = y;
      }
    }
    if (!found) return { ready: true, bounded: true, empty: true };
    found.ready = true;
    found.bounded = true;
    found.empty = false;
    return found;
  }

  function curveReady(set, id) {
    if (id !== "pp_energy") return true;
    var den = Number(set.power_conv_coeff);
    var num = Number(set.trans_coeff);
    return isFinite(den) && den !== 0 && isFinite(num);
  }

  /** 图上的计算点：优先用服务端结果（带状态）；结果还没回来时，正向用本地曲线预览。 */
  function collectMarks(set, id, dir, text, entry) {
    var parsed = parseNumbers(text).values;
    var rs = entry && entry.res && entry.res.results;
    var same = !!(rs && rs.length === parsed.length);
    if (same) {
      for (var i = 0; i < rs.length; i++) {
        var got = dir === "forward" ? rs[i].x : rs[i].y;
        if (got !== parsed[i]) { same = false; break; }
      }
    }
    var marks = [];
    if (same && dir === "forward") {
      rs.forEach(function (r) {
        var y = r.y;
        if (y === undefined || y === null) y = evalCurveY(set, id, r.x);
        if (!isFinite(r.x) || !isFinite(y)) return;
        marks.push({ x: r.x, y: y, status: r.status || "ok", label: String(r.input_index + 1) });
      });
      return marks;
    }
    if (same && dir === "reverse") {
      rs.forEach(function (r) {
        if (!isFinite(r.y)) return;
        var xs = r.x_values || [];
        var chosen = entry.choice && entry.choice[r.input_index];
        if (r.status === "multiple" && chosen !== undefined) xs = [chosen];
        xs.forEach(function (x) {
          if (!isFinite(x)) return;
          marks.push({ x: x, y: r.y, status: r.status || "single", label: String(r.input_index + 1) });
        });
      });
      return marks;
    }
    if (dir === "forward") {
      parsed.forEach(function (x, index) {
        var y = evalCurveY(set, id, x);
        if (!isFinite(y)) return;
        marks.push({ x: x, y: y, status: "preview", label: String(index + 1) });
      });
    }
    return marks;
  }

  function cssColor(name, fallback) {
    var v = "";
    try { v = getComputedStyle(document.documentElement).getPropertyValue(name) || ""; } catch (e) { v = ""; }
    return String(v).replace(/^\s+|\s+$/g, "") || fallback;
  }

  function niceStep(span, count) {
    if (!(span > 0) || !isFinite(span)) return 1;
    var raw = span / Math.max(count, 1);
    var pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    if (!isFinite(pow) || pow === 0) return raw;
    var err = raw / pow;
    var step = 10;
    if (err < 1.5) step = 1;
    else if (err < 3.5) step = 2;
    else if (err < 7.5) step = 5;
    return step * pow;
  }

  /** y 对 x 的导数。PP 能量乘上与曲线相同的换算系数，零点位置不变，极大小会随符号翻转。 */
  function evalDeriv(set, id, x) {
    var c = set.coeffs || {};
    var d = 0;
    var p = 1;
    for (var n = 1; n <= PLOT_KEYS.length; n++) {
      var k = Number(c[PLOT_KEYS[n - 1]] || 0);
      if (k) d += n * k * p;
      p *= x;
    }
    if (id === "pp_energy") {
      var den = Number(set.power_conv_coeff);
      var num = Number(set.trans_coeff);
      if (!isFinite(den) || den === 0 || !isFinite(num)) return NaN;
      d *= num / den;
    }
    return d;
  }

  function bisectDeriv(set, id, a, b) {
    var da = evalDeriv(set, id, a);
    for (var i = 0; i < 60; i++) {
      var m = (a + b) / 2;
      if (Math.abs(b - a) <= 1e-10 * (1 + Math.abs(m))) return m;
      var dm = evalDeriv(set, id, m);
      if (!isFinite(dm)) return m;
      if (da === 0 || da * dm <= 0) b = m;
      else { a = m; da = dm; }
    }
    return (a + b) / 2;
  }

  /**
   * 开区间 (x0, x1) 内的极值点（导数变号）。纯拐点（导数碰到 0 但不变号）不标。
   * 返回按 x 排序的 {x, y, kind:'min'|'max'}。
   */
  function curveTurns(set, id, x0, x1) {
    if (!curveReady(set, id) || !(x1 > x0) || !isFinite(x0) || !isFinite(x1)) return [];
    var steps = 480;
    var prevX = x0;
    var prevD = evalDeriv(set, id, x0);
    var span = x1 - x0;
    var found = [];
    function push(root) {
      var edge = span * 1e-6;
      if (!(root > x0 + edge && root < x1 - edge)) return;
      var eps = Math.max(span * 1e-4, 1e-8);
      var yL = evalCurveY(set, id, root - eps);
      var yM = evalCurveY(set, id, root);
      var yR = evalCurveY(set, id, root + eps);
      if (!isFinite(yL) || !isFinite(yM) || !isFinite(yR)) return;
      var kind = null;
      if (yM <= yL && yM <= yR && (yM < yL || yM < yR)) kind = "min";
      else if (yM >= yL && yM >= yR && (yM > yL || yM > yR)) kind = "max";
      if (!kind) return;
      for (var j = 0; j < found.length; j++) {
        if (Math.abs(found[j].x - root) <= 1e-5 * (1 + span)) return;
      }
      found.push({ x: root, y: yM, kind: kind });
    }
    for (var i = 1; i <= steps; i++) {
      var x = i === steps ? x1 : x0 + span * (i / steps);
      var d = evalDeriv(set, id, x);
      if (isFinite(prevD) && prevD === 0) push(prevX);
      else if (isFinite(prevD) && isFinite(d) && prevD * d < 0) push(bisectDeriv(set, id, prevX, x));
      prevX = x;
      prevD = d;
    }
    found.sort(function (a, b) { return a.x - b.x; });
    return found.slice(0, 8);
  }

  function finitePush(arr, v) {
    if (v !== null && v !== undefined && isFinite(v)) arr.push(v);
  }

  /** 单点或空跨度撑开一点，避免上下界叠在同一条线上。 */
  function widenPair(lo, hi, rel, abs) {
    if (hi > lo) return { lo: lo, hi: hi };
    var p = Math.max(Math.abs(lo) * rel, abs);
    return { lo: lo - p, hi: (hi > lo ? hi : lo) + p };
  }

  /**
   * 取景：画面必须包住已填的 x/y 上下界和全部计算点，并在四周留白。
   * 曲线本身可以画出界外被裁掉，不能反过来把边界和计算点挤出画面。
   * 没有边界也没有计算点时，才按曲线本身取 y。
   * turns 只保留落在这个画面里的极值点。
   */
  function plotFrame(set, id, marks) {
    marks = marks || [];
    var xs = [];
    finitePush(xs, finiteNum(set.x_min));
    finitePush(xs, finiteNum(set.x_max));
    marks.forEach(function (m) { finitePush(xs, m.x); });
    var xPair = xs.length ? widenPair(Math.min.apply(null, xs), Math.max.apply(null, xs), 0.1, 1) : { lo: 0, hi: 10 };
    var ys = [];
    finitePush(ys, finiteNum(set.y_min));
    finitePush(ys, finiteNum(set.y_max));
    marks.forEach(function (m) { finitePush(ys, m.y); });
    var turns = curveTurns(set, id, xPair.lo, xPair.hi);
    if (ys.length && turns.length) {
      /* 极值点离边界/计算点不远时一并入画，避免抛物线顶点被裁掉；离得过远则不拉轴，免得把边界和计算点挤成一条线。 */
      var yAnchorLo = Math.min.apply(null, ys);
      var yAnchorHi = Math.max.apply(null, ys);
      var yRoom = Math.max(yAnchorHi - yAnchorLo, Math.max(Math.abs(yAnchorLo), Math.abs(yAnchorHi), 1)) * 2;
      turns.forEach(function (t) {
        if (isFinite(t.y) && t.y >= yAnchorLo - yRoom && t.y <= yAnchorHi + yRoom) ys.push(t.y);
      });
    }
    var yPair;
    if (ys.length) {
      yPair = widenPair(Math.min.apply(null, ys), Math.max.apply(null, ys), 0.15, 1);
    } else if (curveReady(set, id)) {
      var samples = [];
      var steps = 240;
      for (var i = 0; i <= steps; i++) {
        var y = evalCurveY(set, id, xPair.lo + (xPair.hi - xPair.lo) * (i / steps));
        if (isFinite(y)) samples.push(y);
      }
      turns.forEach(function (t) { if (isFinite(t.y)) samples.push(t.y); });
      if (!samples.length) return null;
      yPair = widenPair(Math.min.apply(null, samples), Math.max.apply(null, samples), 0.15, 1);
    } else return null;
    if (!isFinite(yPair.lo) || !isFinite(yPair.hi)) return null;
    var xPad = (xPair.hi - xPair.lo) * 0.1;
    var yPad = (yPair.hi - yPair.lo) * 0.14;
    var xLo = xPair.lo - xPad;
    var xHi = xPair.hi + xPad;
    var yLo = yPair.lo - yPad;
    var yHi = yPair.hi + yPad;
    return {
      xLo: xLo,
      xHi: xHi,
      yLo: yLo,
      yHi: yHi,
      turns: turns.filter(function (t) { return t.x >= xLo && t.x <= xHi && t.y >= yLo && t.y <= yHi; }),
    };
  }

  /** 画曲线、x/y 边界线、计算点和极值。返回一句话摘要供图下方显示。 */
  function paintPlot(canvas, set, id, marks, axis, focus) {
    var xName = withUnit(axis.x, axis.xUnit);
    var yName = withUnit(axis.y, axis.yUnit);
    var parent = canvas.parentNode;
    var cssW = parent && parent.clientWidth ? parent.clientWidth : 560;
    if (cssW < 240) cssW = 240;
    var cssH = 240;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.width = "100%";
    canvas.style.height = cssH + "px";
    var ctx = canvas.getContext("2d");
    if (!ctx) return "";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    var surface2 = cssColor("--c-surface-2", "#f9fafb");
    var text = cssColor("--c-text-3", "#5d6676");
    ctx.fillStyle = surface2;
    ctx.fillRect(0, 0, cssW, cssH);
    function note(msg) {
      ctx.fillStyle = text;
      ctx.font = "13px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(msg, cssW / 2, cssH / 2);
      return msg;
    }
    if (!curveReady(set, id)) return note("功率转换系数不能为 0，暂时无法绘制曲线");

    var xmin = finiteNum(set.x_min);
    var xmax = finiteNum(set.x_max);
    var yminB = finiteNum(set.y_min);
    var ymaxB = finiteNum(set.y_max);
    var frame = plotFrame(set, id, marks);
    if (!frame) return note("当前系数在此范围内数值过大，无法绘制曲线");

    var plotL = 56;
    var plotR = 18;
    var plotT = 28;
    var plotB = 40;
    var plotW = Math.max(cssW - plotL - plotR, 10);
    var plotH = Math.max(cssH - plotT - plotB, 10);
    /* 再留出标记和坐标文字的像素边，避免贴着绘图区边缘被裁掉。 */
    var gutter = 16;
    var gx = (frame.xHi - frame.xLo) * (gutter / Math.max(plotW - gutter * 2, 1));
    var gy = (frame.yHi - frame.yLo) * (gutter / Math.max(plotH - gutter * 2, 1));
    var xLo = frame.xLo - gx;
    var xHi = frame.xHi + gx;
    var yLo = frame.yLo - gy;
    var yHi = frame.yHi + gy;
    function X(x) { return plotL + ((x - xLo) / (xHi - xLo)) * plotW; }
    function Y(y) { return plotT + ((yHi - y) / (yHi - yLo)) * plotH; }

    var samples = [];
    var steps = 240;
    for (var si = 0; si <= steps; si++) {
      var sx = xLo + (xHi - xLo) * (si / steps);
      samples.push({ x: sx, y: evalCurveY(set, id, sx) });
    }

    var grid = cssColor("--c-guide", "#e4e7ec");
    var primary = cssColor("--c-primary", "#2563eb");
    var warning = cssColor("--c-warning", "#d97706");
    var danger = cssColor("--c-danger", "#dc2626");
    var turnColor = cssColor("--c-success", "#16a34a");
    var surface = cssColor("--c-surface", "#ffffff");

    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    ctx.font = "11px sans-serif";
    ctx.fillStyle = text;
    var xStep = niceStep(xHi - xLo, 5);
    var xStart = Math.ceil(xLo / xStep) * xStep;
    var drawn = 0;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (var xt = xStart; xt <= xHi + xStep * 1e-6 && drawn < 8; xt += xStep) {
      drawn++;
      var px = X(xt);
      ctx.beginPath();
      ctx.moveTo(px, plotT);
      ctx.lineTo(px, plotT + plotH);
      ctx.stroke();
      ctx.fillText(util.formatNumber(xt), px, plotT + plotH + 5);
    }
    var yStep = niceStep(yHi - yLo, 4);
    var yStart = Math.ceil(yLo / yStep) * yStep;
    drawn = 0;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (var yt = yStart; yt <= yHi + yStep * 1e-6 && drawn < 8; yt += yStep) {
      drawn++;
      var py = Y(yt);
      ctx.beginPath();
      ctx.moveTo(plotL, py);
      ctx.lineTo(plotL + plotW, py);
      ctx.stroke();
      ctx.fillText(util.formatNumber(yt), plotL - 6, py);
    }

    ctx.fillStyle = text;
    ctx.font = "11px sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText("y  " + yName, plotL, 6);
    ctx.textAlign = "right";
    ctx.textBaseline = "bottom";
    ctx.fillText("x  " + xName, plotL + plotW, cssH - 4);

    ctx.save();
    ctx.beginPath();
    ctx.rect(plotL, plotT, plotW, plotH);
    ctx.clip();
    ctx.beginPath();
    var pen = false;
    samples.forEach(function (s) {
      if (!isFinite(s.y)) { pen = false; return; }
      var cx = X(s.x);
      var cy = Y(s.y);
      if (!pen) { ctx.moveTo(cx, cy); pen = true; }
      else ctx.lineTo(cx, cy);
    });
    ctx.strokeStyle = primary;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();

    function vline(x, label) {
      if (x === null || x < xLo || x > xHi) return;
      var lx = X(x);
      ctx.save();
      ctx.strokeStyle = warning;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(lx, plotT);
      ctx.lineTo(lx, plotT + plotH);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = warning;
      ctx.font = "11px sans-serif";
      var right = lx > plotL + plotW - 60;
      ctx.textAlign = right ? "right" : "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(label, right ? lx - 4 : lx + 4, plotT + plotH - 4);
    }
    function hline(y, label) {
      if (y === null || y < yLo || y > yHi) return;
      var ly = Y(y);
      ctx.save();
      ctx.strokeStyle = danger;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(plotL, ly);
      ctx.lineTo(plotL + plotW, ly);
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = danger;
      ctx.font = "11px sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = ly < plotT + 18 ? "top" : "bottom";
      ctx.fillText(label, plotL + 6, ly < plotT + 18 ? ly + 2 : ly - 2);
    }
    vline(xmin, "x 下限");
    vline(xmax, "x 上限");
    hline(yminB, "y 下限");
    hline(ymaxB, "y 上限");

    frame.turns.forEach(function (t) {
      if (t.x < xLo || t.x > xHi || t.y < yLo || t.y > yHi) return;
      var tx = X(t.x);
      var ty = Y(t.y);
      ctx.beginPath();
      ctx.moveTo(tx, ty - 3.5);
      ctx.lineTo(tx + 3.5, ty);
      ctx.lineTo(tx, ty + 3.5);
      ctx.lineTo(tx - 3.5, ty);
      ctx.closePath();
      ctx.fillStyle = surface;
      ctx.fill();
      ctx.lineWidth = 1.25;
      ctx.strokeStyle = turnColor;
      ctx.stroke();
      var label = util.formatNumber(t.x) + ", " + util.formatNumber(t.y);
      ctx.font = "10px sans-serif";
      ctx.fillStyle = turnColor;
      ctx.textAlign = "left";
      ctx.textBaseline = t.kind === "min" ? "top" : "bottom";
      var lx = tx + 6;
      var ly = t.kind === "min" ? ty + 5 : ty - 5;
      var tw = ctx.measureText(label).width;
      if (lx + tw > plotL + plotW - 2) lx = Math.max(plotL + 2, tx - 6 - tw);
      if (t.kind === "min" && ly + 12 > plotT + plotH - 2) {
        ctx.textBaseline = "bottom";
        ly = ty - 5;
      } else if (t.kind !== "min" && ly - 12 < plotT + 2) {
        ctx.textBaseline = "top";
        ly = ty + 5;
      }
      ctx.strokeStyle = surface;
      ctx.lineWidth = 3;
      ctx.lineJoin = "round";
      ctx.strokeText(label, lx, ly);
      ctx.fillText(label, lx, ly);
    });

    marks.forEach(function (m) {
      if (m.x < xLo || m.x > xHi || m.y < yLo || m.y > yHi) return;
      var mx = X(m.x);
      var my = Y(m.y);
      var out = m.status === "out_of_x_range" || m.status === "out_of_y_range";
      var on = focus !== null && focus !== undefined && String(focus) === m.label;
      if (on) {
        ctx.beginPath();
        ctx.arc(mx, my, 10, 0, Math.PI * 2);
        ctx.fillStyle = out ? "rgba(217,119,6,0.18)" : "rgba(37,99,235,0.16)";
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(mx, my, on ? 6 : 5, 0, Math.PI * 2);
      ctx.fillStyle = surface;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = out ? warning : primary;
      ctx.stroke();
      if (!out) {
        ctx.beginPath();
        ctx.arc(mx, my, 2.2, 0, Math.PI * 2);
        ctx.fillStyle = primary;
        ctx.fill();
      }
      ctx.fillStyle = cssColor("--c-text", "#101828");
      ctx.font = (on ? "600 " : "") + "11px sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(m.label, mx + 7, my - 4);
    });

    var outCount = 0;
    marks.forEach(function (m) {
      if (m.status === "out_of_x_range" || m.status === "out_of_y_range") outCount++;
    });
    var summary = !marks.length ? "输入数值后，计算点将标注在曲线上。" : ("已标注 " + marks.length + " 个计算点" + (outCount ? "，其中 " + outCount + " 个超出边界（空心橙点）" : ""));
    if (frame.turns.length) summary += "；已标注 " + frame.turns.length + " 个极值点坐标";
    return summary;
  }

  MC.dlCurveExtrema = curveExtrema;
  MC.dlPlotFrame = plotFrame;

  MC.views.dl = function (app, route) {
    var q = route.query || {};
    var S = {
      rev: null,
      saved: null,
      draft: null,
      item: q.item || "pp_energy",
      aom: q.aom === "width" ? "width" : "delay",
      dir: "forward",
      inputs: {},
      results: {},
      focus: null,
      headerKey: null,
      ui: null,
    };
    var el = h("div");
    var header = h("div.view-header.dl-header");
    var bodyEl = h("div.view-body");
    el.append(header, bodyEl);

    var solveTimer = null;
    var solveSeq = 0;
    var multiTimer = null;
    var multiSeq = 0;
    var resizeObs = null;

    /* ---------------- 参数集合：RP 脉宽按模式取不同的一套 ---------------- */
    function setOf(src, id, aom) {
      var it = src[id];
      if (id === "rp_width" && aom === "width") return it.aom_width;
      return it;
    }
    function activeSet(id) { return setOf(S.draft, id, S.aom); }
    function paramsOf(id) {
      var set = activeSet(id);
      var p = { coeffs: set.coeffs, x_min: set.x_min, x_max: set.x_max, y_min: set.y_min, y_max: set.y_max };
      if (id === "pp_energy") {
        p.trans_coeff = set.trans_coeff;
        p.power_conv_coeff = set.power_conv_coeff;
      }
      return p;
    }
    function sigOf(set) { return JSON.stringify([set.coeffs, set.x_min, set.x_max, set.y_min, set.y_max, set.binding]); }
    function setDirty(id, aom) { return !!S.saved && sigOf(setOf(S.saved, id, aom)) !== sigOf(setOf(S.draft, id, aom)); }
    function itemDirty(id) { return !!S.saved && JSON.stringify(S.saved[id]) !== JSON.stringify(S.draft[id]); }
    function anyDirty() { return !!S.saved && ITEMS.some(function (it) { return itemDirty(it.id); }); }
    function keyOf(id) { return id + (id === "rp_width" ? ":" + S.aom : "") + ":" + S.dir; }

    function load() {
      util.replace(bodyEl, MC.stateBlock({ kind: "loading" }));
      api.get(app.p("/dl")).then(function (res) {
        S.rev = res.rev;
        if (res.items.rp_width && !res.items.rp_width.aom_width) res.items.rp_width.aom_width = emptySet();
        S.saved = res.items;
        S.draft = clone(res.items);
        render();
      }, function (err) { util.replace(bodyEl, MC.errorBlock(err, load)); });
    }

    /* ---------------- 页头：计算项切换 + 保存 ---------------- */
    function headerKey() {
      return [S.item, anyDirty(), ITEMS.map(function (it) { return itemDirty(it.id) ? 1 : 0; }).join(""), app.can("deployer")].join("|");
    }

    function renderHeader() {
      var key = headerKey();
      if (S.headerKey === key && header.firstChild) return;
      S.headerKey = key;
      var seg = MC.segmented(
        ITEMS.map(function (it) { return { value: it.id, label: it.label + (S.saved && itemDirty(it.id) ? " •" : "") }; }),
        S.item,
        function (v) {
          S.item = v;
          S.focus = null;
          app.go({ kind: "dl", query: itemQuery() }, true);
          render();
        },
        "计算项"
      );
      var id = S.item;
      var save = null;
      if (S.saved && itemDirty(id)) {
        var saveBtn = h("button.btn.btn-primary.btn-sm", { type: "button", disabled: !app.can("deployer"), title: app.can("deployer") ? null : "仅管理员可保存为共享配置" }, "保存为配置");
        saveBtn.addEventListener("click", function () { doSave(saveBtn); });
        save = h(
          "div.dl-save",
          h("span.badge.badge-warning", "有未保存的修改"),
          app.can("deployer") ? null : h("span.xsmall.muted", "仅管理员可保存"),
          h("button.btn.btn-sm", { type: "button", onclick: function () { S.draft[id] = clone(S.saved[id]); S.results = {}; render(); } }, "放弃修改"),
          saveBtn
        );
      }
      util.replace(
        header,
        h("div.view-title", h("h1", "DL 快捷计算"), h("div.view-sub", "以八阶多项式进行正向求值与反向求解。参数修改仅作用于当前会话，保存后对所有用户生效。")),
        seg,
        save
      );
    }

    function itemQuery() {
      var qy = { item: S.item };
      if (S.item === "rp_width") qy.aom = S.aom;
      return qy;
    }

    function doSave(btn) {
      var id = S.item;
      MC.withBusy(btn, function () {
        return api.put(app.p("/dl/" + id), S.draft[id], { "If-Match": S.rev }).then(
          function (res) {
            S.rev = res.rev;
            if (id === "rp_width" && !res.item.aom_width) res.item.aom_width = emptySet();
            S.saved[id] = res.item;
            S.draft[id] = clone(res.item);
            MC.toast.success("计算参数已保存");
            app.syncRevision();
            render();
          },
          function (err) {
            if (err.code === "REVISION_CONFLICT") {
              MC.confirm({ title: "计算参数已被更新", message: "保存期间已有其他用户写入新的计算参数。可放弃本地修改并加载最新参数，或保留当前草稿继续计算。", confirmText: "加载最新参数", cancelText: "保留草稿" }).then(function (ok) { if (ok) load(); });
            } else MC.toastError("保存失败", err);
          }
        );
      });
    }

    /* ---------------- 整页渲染 ---------------- */
    function render() {
      S.headerKey = null;
      renderHeader();
      app.renderTabbar();
      var id = S.item;
      var ui = (S.ui = {});
      var wrap = h("div.dl-wrap");
      if (id === "rp_width") wrap.append(modeCard());
      wrap.append(h("div.dl-grid", solveCard(id), paramsCard(id)));
      util.replace(bodyEl, wrap);
      updateParseInfo(parseNumbers(S.inputs[keyOf(id)]));
      renderResults();
      watchChart();
      requestAnimationFrame(drawPlot);
      scheduleMultiCheck();
      scheduleSolve(60);
      return ui;
    }

    /** 参数输入过程中的轻量刷新：不重建输入框，所以焦点和光标不会丢。 */
    function afterParamChange() {
      renderHeader();
      app.renderTabbar();
      updateFormula();
      renderTiles();
      renderResults();
      scheduleMultiCheck();
      scheduleSolve(220);
      drawPlot();
    }

    function numField(value, onChange, label, o) {
      o = o || {};
      var inp = h("input.input.input-mono", {
        type: "text",
        inputmode: "decimal",
        value: value === null || value === undefined ? "" : String(value),
        "aria-label": label,
        placeholder: o.placeholder || "未设置",
        autocomplete: "off",
        spellcheck: "false",
      });
      inp.addEventListener("input", function () {
        var v = numOrNull(inp.value);
        if (Number.isNaN(v)) {
          inp.classList.add("is-invalid");
          return;
        }
        inp.classList.remove("is-invalid");
        onChange(v);
        afterParamChange();
      });
      inp.addEventListener("change", function () {
        var v = numOrNull(inp.value);
        if (Number.isNaN(v)) {
          MC.toast.warning(label + " 不是有效数值", "该输入未写入草稿");
          return;
        }
        if (o.coeff && v === null) inp.value = "0";
      });
      return h("div.dl-num" + (o.unit ? ".has-unit" : ""), inp, o.unit ? h("span.dl-unit", o.unit) : null);
    }

    /* ---------------- RP 脉宽：模式卡片 ---------------- */
    function tileFor(mode) {
      var spec = AXES.rp_width[mode];
      var set = setOf(S.draft, "rp_width", mode);
      var on = S.aom === mode;
      return h(
        "button.dl-tile" + (on ? ".is-active" : ""),
        {
          type: "button",
          role: "tab",
          "aria-selected": String(on),
          onclick: function () {
            if (S.aom === mode) return;
            S.aom = mode;
            S.focus = null;
            app.go({ kind: "dl", query: itemQuery() }, true);
            render();
          },
        },
        h("span.dl-tile-top", h("span.dl-tile-mode", spec.mode), setDirty("rp_width", mode) ? h("span.dl-dot", { title: "这一套参数有未保存的修改" }) : null),
        h("span.dl-tile-name", nameNode(spec.name, spec.unit)),
        h("span.dl-tile-fn", "y = " + polyText(set, 3))
      );
    }

    function renderTiles() {
      if (!S.ui || !S.ui.tiles) return;
      util.replace(S.ui.tiles, tileFor("delay"), tileFor("width"));
    }

    function modeCard() {
      var item = S.draft.rp_width;
      S.ui.tiles = h("div.dl-tiles", { role: "tablist", "aria-label": "RP 脉宽输出量" });
      renderTiles();
      var sum = h(
        "div.dl-sum",
        h("div.dl-sum-title", "固定和", h("span.xsmall.muted", "AOM2 delay + AOM2 width")),
        numField(item.aom_sum, function (v) { item.aom_sum = v; }, "AOM2 delay 与 width 的和", { unit: "ns", placeholder: "未设置" })
      );
      return h(
        "section.panel.dl-modes",
        h("div.dl-modes-head", h("h2", "输出量"), h("span.xsmall.muted", "两种模式各自维护系数、有效范围与系数绑定。切换模式后，参数、曲线与结果同步切换。")),
        h("div.dl-modes-body", S.ui.tiles, sum)
      );
    }

    /* ---------------- 求解卡片：输入 / 曲线 / 结果 ---------------- */
    function flowPill(axis) {
      var fwd = S.dir === "forward";
      var xNode = h("span.dl-flow-node", h("span.dl-var", "x"), nameNode(axis.x, axis.xUnit));
      var yNode = h("span.dl-flow-node", h("span.dl-var.is-y", "y"), nameNode(axis.y, axis.yUnit));
      return h("div.dl-flow", fwd ? [xNode, icon("arrow-right"), yNode] : [yNode, icon("arrow-right"), xNode]);
    }

    function solveCard(id) {
      var axis = axisOf(id, S.aom);
      var key = keyOf(id);
      var fwd = S.dir === "forward";
      var dirSeg = MC.segmented(
        [{ value: "forward", label: "正向 x → y" }, { value: "reverse", label: "反向 y → x" }],
        S.dir,
        function (v) { S.dir = v; S.focus = null; render(); },
        "计算方向"
      );

      var inLabel = fwd ? withUnit(axis.x, axis.xUnit) : withUnit(axis.y, axis.yUnit);
      var ta = h("textarea.textarea.input-mono.dl-input", {
        id: util.uid("dlin"),
        rows: 6,
        spellcheck: "false",
        "aria-label": "输入" + inLabel,
        placeholder: (fwd ? "输入 x 值" : "输入目标 y 值") + "\n例如：10 20 35.5",
      }, S.inputs[key] || "");
      var parseEl = h("div.dl-parse");
      var runBtn = h("button.btn.btn-primary", { type: "button" }, icon(fwd ? "arrow-right" : "search"), fwd ? "计算" : "求解");
      var clearBtn = h("button.btn.btn-ghost", { type: "button" }, "清空");
      ta.addEventListener("input", function () {
        S.inputs[key] = ta.value;
        S.focus = null;
        updateParseInfo(parseNumbers(ta.value));
        scheduleSolve(350);
        drawPlot();
      });
      ta.addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runSolve(true); } });
      runBtn.addEventListener("click", function () { runSolve(true); });
      clearBtn.addEventListener("click", function () {
        S.inputs[key] = "";
        ta.value = "";
        delete S.results[key];
        updateParseInfo(parseNumbers(""));
        renderResults();
        drawPlot();
        ta.focus();
      });
      S.ui.ta = ta;
      S.ui.parseEl = parseEl;

      var canvas = h("canvas.dl-plot", { role: "img", "aria-label": "当前数值关系图" });
      var caption = h("div.dl-plot-caption", "正在绘制…");
      var extrema = h("div.dl-extrema");
      S.ui.canvas = canvas;
      S.ui.caption = caption;
      S.ui.extrema = extrema;
      S.ui.chartWrap = h("div.dl-plot-wrap", canvas);

      var resHost = h("div.dl-res");
      S.ui.resHost = resHost;

      return h(
        "section.panel.dl-solve",
        h("div.panel-header", h("h2", "求解"), dirSeg),
        h(
          "div.panel-body.dl-solve-body",
          flowPill(axis),
          h(
            "div.dl-solve-top",
            h(
              "div.dl-input-pane",
              h("div.dl-pane-title", h("label", { for: ta.id }, "输入 " + (fwd ? "x" : "y")), h("span.dl-pane-sub", nameNode(fwd ? axis.x : axis.y, fwd ? axis.xUnit : axis.yUnit))),
              ta,
              parseEl,
              h("div.dl-actions", runBtn, clearBtn)
            ),
            h(
              "div.dl-chart",
              h(
                "div.dl-pane-title",
                h("span", "数值关系"),
                h("div.dl-legend", h("span", h("i.lg-curve"), "曲线"), h("span", h("i.lg-x"), "x 边界"), h("span", h("i.lg-y"), "y 边界"), h("span", h("i.lg-pt"), "计算点"), h("span", h("i.lg-turn"), "极值"))
              ),
              S.ui.chartWrap,
              caption,
              extrema
            )
          ),
          resHost
        )
      );
    }

    function updateParseInfo(p) {
      var elp = S.ui && S.ui.parseEl;
      if (!elp) return;
      elp.className = "dl-parse";
      if (!p.values.length && !p.bad.length) {
        elp.textContent = "支持空格、逗号、分号或换行分隔多个数值";
        return;
      }
      var msg = "已识别 " + p.values.length + " 个数值";
      if (p.bad.length) {
        msg += "，忽略无法识别的内容：" + p.bad.slice(0, 4).join("、") + (p.bad.length > 4 ? " 等" : "");
        elp.classList.add("is-warn");
      }
      elp.textContent = msg;
    }

    function scheduleSolve(delay) {
      clearTimeout(solveTimer);
      solveTimer = setTimeout(function () { runSolve(false); }, delay === undefined ? 350 : delay);
    }

    function runSolve(manual) {
      clearTimeout(solveTimer);
      if (!S.draft || !S.ui) return Promise.resolve();
      var id = S.item;
      var dir = S.dir;
      var key = keyOf(id);
      var text = S.inputs[key] || "";
      var p = parseNumbers(text);
      updateParseInfo(p);
      if (!p.values.length) {
        delete S.results[key];
        renderResults();
        drawPlot();
        if (manual) MC.toast.warning("请输入至少一个数值");
        return Promise.resolve();
      }
      var seq = ++solveSeq;
      return api.post(app.p("/dl/" + id + "/compute"), { mode: dir, values: p.values, params: paramsOf(id) }).then(
        function (res) {
          if (seq !== solveSeq) return;
          var prev = S.results[key];
          S.results[key] = { res: res, text: text, choice: prev && prev.text === text && prev.choice ? prev.choice : {} };
          renderResults();
          drawPlot();
        },
        function (err) {
          if (seq !== solveSeq) return;
          S.results[key] = { error: MC.errorText(err), text: text };
          renderResults();
          drawPlot();
        }
      );
    }

    function badge(cls, text) { return h("span.badge." + cls, text); }

    function renderResults() {
      var host = S.ui && S.ui.resHost;
      if (!host) return;
      var id = S.item;
      var dir = S.dir;
      var entry = S.results[keyOf(id)];
      var item = S.draft[id];
      var axis = axisOf(id, S.aom);
      var xLabel = withUnit(axis.x, axis.xUnit);
      var yLabel = withUnit(axis.y, axis.yUnit);
      var otherLabel = axis.other ? withUnit(axis.other, axis.otherUnit) : "";

      if (!entry) {
        util.replace(host, h("div.dl-res-head", h("h3", "结果")));
        return;
      }
      if (entry.error) {
        util.replace(host, h("div.banner.banner-danger", icon("alert"), h("div.banner-body", entry.error)));
        return;
      }

      var rs = entry.res.results;
      function otherCell(y) {
        if (!otherLabel) return null;
        var sum = finiteNum(item.aom_sum);
        var yy = finiteNum(y);
        return h("td.mono", sum === null || yy === null ? "—" : util.formatNumber(sum - yy));
      }
      var STATUS = { ok: null, out_of_x_range: "x 超出范围", out_of_y_range: "y 超出范围", none: "范围内无实数解", identity: "常数函数：所有 x 都满足", single: null, multiple: null };
      function th(name, unit) {
        var parts = splitName(name);
        var sub = [parts.note, unit].filter(Boolean).join(" · ");
        return h("th", { title: withUnit(name, unit) }, h("div.dl-th", h("span", parts.main), sub ? h("small", sub) : null));
      }
      function rowHover(tr, index) {
        tr.addEventListener("mouseenter", function () { S.focus = String(index + 1); drawPlot(); });
        tr.addEventListener("mouseleave", function () { S.focus = null; drawPlot(); });
        return tr;
      }

      var ok = 0;
      var bad = 0;
      var multiRows = 0;
      rs.forEach(function (r) {
        if (r.status === "ok" || r.status === "single" || r.status === "identity") ok++;
        else if (r.status === "multiple") multiRows++;
        else bad++;
      });

      var table;
      if (dir === "forward") {
        table = h(
          "table.table.is-compact.dl-table",
          h("thead", h("tr", h("th.num", "#"), th(axis.x, axis.xUnit), id === "pp_energy" ? th("f(x)") : null, th(axis.y, axis.yUnit), otherLabel ? th(axis.other, axis.otherUnit) : null, h("th", "状态"))),
          h("tbody", rs.map(function (r) {
            return rowHover(
              h(
                "tr",
                h("td.num.muted", String(r.input_index + 1)),
                h("td.mono", util.formatNumber(r.x)),
                id === "pp_energy" ? h("td.mono.muted", r.y_base === undefined ? "—" : util.formatNumber(r.y_base)) : null,
                h("td.mono.dl-out", r.y === undefined ? "—" : util.formatNumber(r.y)),
                otherCell(r.y),
                h("td", STATUS[r.status] ? badge("badge-warning", STATUS[r.status]) : badge("badge-success", "正常"))
              ),
              r.input_index
            );
          }))
        );
      } else {
        table = h(
          "table.table.is-compact.dl-table",
          h("thead", h("tr", h("th.num", "#"), th(axis.y, axis.yUnit), otherLabel ? th(axis.other, axis.otherUnit) : null, th("解 " + axis.x, axis.xUnit), h("th", "状态"))),
          h("tbody", rs.map(function (r) {
            var cell;
            if (r.status === "multiple") {
              var sel = h(
                "select.select.input-sm.dl-pick",
                { "aria-label": "选择第 " + (r.input_index + 1) + " 行采用的解" },
                h("option", { value: "" }, "选择采用的解…"),
                r.x_values.map(function (x) { return h("option", { value: String(x), selected: entry.choice[r.input_index] === x }, util.formatNumber(x)); })
              );
              sel.addEventListener("change", function () { entry.choice[r.input_index] = sel.value === "" ? undefined : Number(sel.value); drawPlot(); });
              cell = h("td", h("div.dl-multi", h("span.mono.multi", r.x_values.map(util.formatNumber).join(", ")), sel));
            } else cell = h("td.mono.dl-out", r.x_values && r.x_values.length ? util.formatNumber(r.x_values[0]) : "—");
            return rowHover(
              h(
                "tr",
                h("td.num.muted", String(r.input_index + 1)),
                h("td.mono", util.formatNumber(r.y)),
                otherCell(r.y),
                cell,
                h("td", r.status === "multiple" ? badge("badge-warning", r.x_values.length + " 个解") : STATUS[r.status] ? badge("badge-danger", STATUS[r.status]) : badge("badge-success", "唯一解"))
              ),
              r.input_index
            );
          }))
        );
      }

      function csv() {
        if (dir === "forward") {
          var head = [xLabel, yLabel];
          if (otherLabel) head.push(otherLabel);
          head.push("状态");
          return [head.join(",")].concat(rs.map(function (r) {
            var row = [r.x, r.y === undefined ? "" : r.y];
            if (otherLabel) {
              var sum = finiteNum(item.aom_sum);
              row.push(sum === null || finiteNum(r.y) === null ? "" : sum - r.y);
            }
            row.push(STATUS[r.status] || "正常");
            return row.join(",");
          })).join("\n");
        }
        var revHead = ["目标" + yLabel];
        if (otherLabel) revHead.push(otherLabel);
        revHead.push("解" + xLabel, "状态");
        return [revHead.join(",")].concat(rs.map(function (r) {
          var x = r.status === "multiple" ? (entry.choice[r.input_index] !== undefined ? entry.choice[r.input_index] : r.x_values.join(" | ")) : (r.x_values[0] !== undefined ? r.x_values[0] : "");
          var row = [r.y];
          if (otherLabel) {
            var sum2 = finiteNum(item.aom_sum);
            row.push(sum2 === null || finiteNum(r.y) === null ? "" : sum2 - r.y);
          }
          row.push(x, r.status === "multiple" ? (entry.choice[r.input_index] !== undefined ? "已选定" : "多解") : STATUS[r.status] || "唯一解");
          return row.join(",");
        })).join("\n");
      }

      var fileLabel = item.name + (id === "rp_width" ? "_" + S.aom : "") + "_" + dir + ".csv";
      util.replace(
        host,
        h(
          "div.dl-res-head",
          h("h3", "结果"),
          h("span.badge.badge-count", String(rs.length)),
          ok ? badge("badge-success", ok + " 正常") : null,
          multiRows ? badge("badge-warning", multiRows + " 多解") : null,
          bad ? badge("badge-warning", bad + " 需留意") : null,
          h("span.spacer"),
          dir === "reverse" ? h("span.xsmall.muted.dl-res-note", "候选根为当前范围内的检测结果，不保证穷尽。") : null,
          MC.copyButton(csv, "复制为 CSV"),
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "下载 CSV", "data-tip": "下载 CSV", onclick: function () { util.downloadBlob("\uFEFF" + csv(), fileLabel, "text/csv;charset=utf-8"); } }, icon("download"))
        ),
        multiRows ? h("div.banner.banner-warning.dl-res-banner", icon("info"), h("div.banner-body", "部分目标值存在多个解。请逐行指定采用的解。复制与下载将使用该指定，系统不自动选取。")) : null,
        h("div.table-wrap.dl-res-table", table)
      );
    }

    function extremaNodes(set, id, axis) {
      var got = curveExtrema(set, id);
      if (!got.bounded) return h("div.dl-extrema-note", "填写 x 的上下限后，将给出该范围内曲线上 x 与 y 的最大值和最小值。");
      if (got.empty) return h("div.dl-extrema-note", "当前曲线在给定的 x、y 范围内没有采样点。");
      function cell(label, value, unit) {
        return h("div.dl-ext", h("span.dl-ext-k", label), h("span.mono", util.formatNumber(value)), unit ? h("span.dl-ext-u", unit) : null);
      }
      return [
        cell("x 最小", got.xMin, axis.xUnit),
        cell("x 最大", got.xMax, axis.xUnit),
        cell("y 最小", got.yMin, axis.yUnit),
        cell("y 最大", got.yMax, axis.yUnit),
      ];
    }

    /* ---------------- 图 ---------------- */
    function drawPlot() {
      var ui = S.ui;
      if (!ui || !ui.canvas || !ui.canvas.isConnected || !S.draft) return;
      var id = S.item;
      var set = activeSet(id);
      var axis = axisOf(id, S.aom);
      var marks = collectMarks(set, id, S.dir, S.inputs[keyOf(id)], S.results[keyOf(id)]);
      var summary = paintPlot(ui.canvas, set, id, marks, axis, S.focus);
      if (ui.caption) ui.caption.textContent = summary;
      if (ui.extrema) util.replace(ui.extrema, extremaNodes(set, id, axis));
      ui.canvas.setAttribute("aria-label", "数值关系图。" + summary);
    }

    function watchChart() {
      if (resizeObs) { resizeObs.disconnect(); resizeObs = null; }
      if (typeof ResizeObserver !== "undefined" && S.ui && S.ui.chartWrap) {
        var last = 0;
        resizeObs = new ResizeObserver(function () {
          var w = S.ui && S.ui.chartWrap ? S.ui.chartWrap.clientWidth : 0;
          if (w && Math.abs(w - last) > 1) { last = w; drawPlot(); }
        });
        resizeObs.observe(S.ui.chartWrap);
      }
    }

    /* ---------------- 参数卡片 ---------------- */
    function formulaNodes(id, set, axis) {
      var lines = [h("div", "f(x) = " + polyText(set))];
      if (id === "pp_energy") lines.push(h("div", axis.y + " = f(x) × 传输系数 ÷ 功率转换系数"));
      return lines;
    }

    function updateFormula() {
      if (!S.ui || !S.ui.formula) return;
      var id = S.item;
      util.replace(S.ui.formula, formulaNodes(id, activeSet(id), axisOf(id, S.aom)));
    }

    function coeffGroup(set) {
      return h(
        "div.dl-group",
        h("div.section-title", "多项式系数"),
        h(
          "div.dl-coeffs",
          KEYS.map(function (k) {
            return h(
              "div.dl-coeff",
              h("label.dl-coeff-k", SUB[k], h("small", POW[k])),
              numField(set.coeffs[k], function (v) { set.coeffs[k] = v === null ? 0 : v; }, "系数 " + SUB[k], { coeff: true, placeholder: "0" })
            );
          })
        )
      );
    }

    function rangeRow(set, axis, which) {
      var isX = which === "x";
      var name = isX ? axis.x : axis.y;
      var unit = isX ? axis.xUnit : axis.yUnit;
      var lo = isX ? "x_min" : "y_min";
      var hi = isX ? "x_max" : "y_max";
      return h(
        "div.dl-range",
        h("div.dl-range-name", h("span.dl-var" + (isX ? "" : ".is-y"), which), nameNode(name, unit)),
        h(
          "div.dl-range-pair",
          numField(set[lo], function (v) { set[lo] = v; }, name + " 下限", { unit: unit, placeholder: "不限" }),
          h("span.dl-range-sep", "~"),
          numField(set[hi], function (v) { set[hi] = v; }, name + " 上限", { unit: unit, placeholder: "不限" })
        )
      );
    }

    function rangeGroup(set, axis) {
      return h(
        "div.dl-group",
        h("div.section-title", "有效范围"),
        rangeRow(set, axis, "x"),
        rangeRow(set, axis, "y")
      );
    }

    function ppGroup(set) {
      return h(
        "div.dl-group",
        h("div.section-title", "PP 能量换算"),
        h(
          "div.dl-pair",
          h("div.field", h("label.field-label", "传输系数"), numField(set.trans_coeff, function (v) { set.trans_coeff = v; }, "传输系数", { placeholder: "1" })),
          h("div.field", h("label.field-label", "功率转换系数"), numField(set.power_conv_coeff, function (v) { set.power_conv_coeff = v; }, "功率转换系数", { placeholder: "1" }))
        ),
        h("div.field-help", "功率转换系数、传输系数都不能为 0。")
      );
    }

    function bindingGroup(id, set) {
      var b = set.binding || {};
      var body;
      if (b.source_file) {
        var map = Object.keys(b.field_map || {});
        body = h(
          "div.col",
          { style: "gap:8px" },
          h("div.dl-bind-row", h("span.dl-bind-k", "文件"), h("button.link-btn.dl-bind-v", { type: "button", title: b.source_file, onclick: function () { app.openFile(b.source_file, { node: b.variable_path }); } }, b.source_file)),
          h("div.dl-bind-row", h("span.dl-bind-k", "路径"), h("span.mono.dl-bind-v", { title: b.variable_path }, b.variable_path || "—")),
          h("div.dl-bind-row", h("span.dl-bind-k", "映射"), map.length ? h("span.dl-chips", map.map(function (k) { return h("span.dl-chip", k + " → " + (SUB[b.field_map[k]] || b.field_map[k])); })) : h("span.dl-bind-v", "—")),
          h("div.row", h("button.btn.btn-sm", { type: "button", onclick: function (e) { extract(id, set, e.currentTarget); } }, icon("refresh"), "从配置刷新系数"), h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { bindingDialog(id); } }, "修改绑定"))
        );
      } else {
        body = h("div.col", { style: "gap:8px" }, h("div.field-help", "尚未绑定。绑定后可从配置文件提取系数。"), h("div.row", h("button.btn.btn-sm", { type: "button", onclick: function () { bindingDialog(id); } }, icon("link"), "配置绑定")));
      }
      return h("div.dl-group", h("div.section-title", "系数绑定"), body);
    }

    function paramsCard(id) {
      var set = activeSet(id);
      var axis = axisOf(id, S.aom);
      S.ui.formula = h("div.dl-formula", formulaNodes(id, set, axis));
      S.ui.multi = h("div.dl-multi-box");
      var modeBadge = id === "rp_width" ? h("span.badge.badge-primary", AXES.rp_width[S.aom].mode) : null;
      return h(
        "section.panel.dl-params-card",
        h("div.panel-header", h("h2", "函数与参数"), modeBadge),
        h(
          "div.panel-body.dl-params-body",
          S.ui.formula,
          S.ui.multi,
          h("div.dl-params", coeffGroup(set), rangeGroup(set, axis), id === "pp_energy" ? ppGroup(set) : null, bindingGroup(id, set))
        )
      );
    }

    function scheduleMultiCheck() {
      clearTimeout(multiTimer);
      multiTimer = setTimeout(function () {
        if (!S.ui || !S.draft) return;
        var id = S.item;
        var seq = ++multiSeq;
        api.post(app.p("/dl/" + id + "/compute"), { mode: "multi_check", params: paramsOf(id) }).then(function (res) {
          var host = S.ui && S.ui.multi;
          if (!host || seq !== multiSeq) return;
          util.replace(
            host,
            res.has_multi
              ? h(
                  "div.banner.banner-warning",
                  icon("alert"),
                  h(
                    "div.banner-body",
                    h("div", h("strong", "存在一对多："), "当前系数在 x 范围内对部分 y 存在多个解，反向结果可能不唯一。建议收窄 x 范围。"),
                    h("div.xsmall", { style: "margin-top:4px" }, res.cases.slice(0, 3).map(function (c) { return "y=" + util.formatNumber(c.y) + " → x=" + c.x_values.map(util.formatNumber).join(", "); }).join("；"))
                  )
                )
              : null
          );
        }, function (err) {
          var host = S.ui && S.ui.multi;
          if (host && seq === multiSeq) util.replace(host, h("div.banner.banner-danger", icon("alert"), h("div.banner-body", MC.errorText(err))));
        });
      }, 300);
    }

    /* ---------------- 绑定：提取预览 / 编辑 ---------------- */
    function setTitle(id) {
      var item = S.draft[id];
      return item.name + (id === "rp_width" ? " · " + AXES.rp_width[S.aom].mode : "");
    }

    function extract(id, set, btn) {
      var binding = set.binding;
      MC.withBusy(btn, function () {
        return api.post(app.p("/dl/" + id + "/extract"), { binding: binding }).then(function (res) {
          var rows = res.fields.map(function (f) {
            var old = set.coeffs[f.coeff];
            return h(
              "tr",
              h("td.mono", f.field),
              h("td.mono", SUB[f.coeff] || f.coeff),
              h("td.mono", util.formatNumber(old)),
              h("td.mono", f.status === "ok" ? util.formatNumber(f.value) : "—"),
              h("td", f.status === "ok" ? (f.value === old ? h("span.muted.xsmall", "未变化") : h("span.badge.badge-primary", "将更新")) : h("span.badge.badge-danger", f.status === "missing" ? "字段缺失" : "非数值"))
            );
          });
          var okCount = res.fields.filter(function (f) { return f.status === "ok"; }).length;
          var hasBad = okCount < res.fields.length;
          MC.modal({
            title: "从配置刷新系数 · " + setTitle(id),
            subtitle: binding.source_file + " › " + binding.variable_path,
            size: "wide",
            body: h(
              "div.col",
              { style: "gap:12px" },
              hasBad ? h("div.banner.banner-warning", icon("alert"), h("div.banner-body", "部分字段无法提取，这些系数将保持原值，不会被替换为 0。")) : null,
              h("div.table-wrap", h("table.table.is-compact", h("thead", h("tr", h("th", "字段"), h("th", "系数"), h("th", "当前"), h("th", "提取值"), h("th", "状态"))), h("tbody", rows)))
            ),
            footer: function (close) {
              return [
                h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
                h("button.btn.btn-primary", {
                  type: "button",
                  disabled: !okCount,
                  onclick: function () {
                    Object.keys(res.coeffs).forEach(function (k) { set.coeffs[k] = res.coeffs[k]; });
                    close();
                    MC.toast.success("已更新 " + okCount + " 个系数到草稿", "确认无误后点击「保存为配置」");
                    render();
                  },
                }, "应用到草稿"),
              ];
            },
          });
        }, function (err) { MC.toastError("提取失败", err); });
      });
    }

    function bindingDialog(id) {
      var set = activeSet(id);
      var b = set.binding || { source_file: "", variable_path: "", field_map: {} };
      var fileSel = h("select.select", h("option", { value: "" }, "请选择文件"), app.files.map(function (f) { return h("option", { value: f.name, selected: f.name === b.source_file }, f.name); }));
      var pathInput = h("input.input.input-mono", { type: "text", value: b.variable_path || "", placeholder: "例如 scenario_a.xml/CONFIG_ROOT/SCENARIO_BLOCK/input", "aria-label": "变量路径" });
      var searchInput = h("input.input.input-sm", { type: "search", placeholder: "搜索名称、路径或值", "aria-label": "搜索变量" });
      var listHost = h("div.dl-var-list");
      var listStatus = h("div.dl-var-status");
      var mapInput = h("textarea.textarea.input-mono", { rows: 4, placeholder: "m1=k1\nm2=k2\nn=b" }, Object.keys(b.field_map || {}).map(function (k) { return k + "=" + b.field_map[k]; }).join("\n"));
      var mapField = MC.field({ label: "字段 → 系数映射", control: mapInput, help: "每行一条：字段名=系数名，系数名为 k1…k8 或 b" });
      var nodes = [];
      var loadToken = 0;

      function renderList() {
        var name = fileSel.value;
        var selected = pathInput.value.trim();
        if (!name) {
          listStatus.textContent = "";
          util.replace(listHost, h("div.dl-var-empty", "选择文件后可以浏览变量"));
          return;
        }
        if (listStatus.dataset.state === "loading") return;
        if (listStatus.dataset.state === "error") return;
        var q = searchInput.value.trim().toLowerCase();
        var matched = [];
        for (var i = 0; i < nodes.length; i++) {
          if (!q || nodes[i].hay.indexOf(q) >= 0) matched.push(nodes[i]);
        }
        var shown = matched.slice(0, 80);
        listStatus.textContent = matched.length
          ? "显示 " + shown.length + (matched.length > shown.length ? " / " + matched.length + "，缩小搜索以查看其余" : " / " + matched.length)
          : "没有匹配的变量";
        util.replace(listHost, shown.length ? shown.map(function (n) {
          return h(
            "button.dl-var-row" + (selected === n.path ? ".is-active" : ""),
            { type: "button", onclick: function () { pathInput.value = n.path; renderList(); } },
            h(
              "span.dl-var-main",
              h("span.dl-var-leaf", n.rel.split("/").pop()),
              h("span.dl-var-rel", n.rel)
            ),
            n.value !== null && n.value !== undefined ? h("span.dl-var-val", { title: String(n.value) }, n.value) : null
          );
        }) : h("div.dl-var-empty", q ? "没有匹配的变量" : "这份文件没有可绑定的节点"));
      }

      function loadFile() {
        var name = fileSel.value;
        var token = ++loadToken;
        nodes = [];
        if (!name) {
          listStatus.dataset.state = "";
          renderList();
          return;
        }
        listStatus.dataset.state = "loading";
        listStatus.textContent = "正在读取…";
        util.replace(listHost, h("div.dl-var-empty", "正在读取…"));
        api.get(app.p("/files/" + api.seg(name) + "/document")).then(function (doc) {
          if (token !== loadToken) return;
          var N = doc.nodes || {};
          var parents = N.p || [];
          var labels = N.l || [];
          var values = N.v || [];
          var built = [];
          for (var i = 1; i < parents.length; i++) {
            var chain = [];
            var k = i;
            var guard = 0;
            while (k > 0 && guard <= parents.length) {
              chain.unshift(String(labels[k]));
              k = parents[k];
              guard++;
            }
            var rel = chain.join("/");
            var value = values[i];
            built.push({
              path: name + "/" + rel,
              rel: rel,
              value: value,
              hay: (rel + "\n" + (value === null || value === undefined ? "" : value)).toLowerCase(),
            });
          }
          nodes = built;
          listStatus.dataset.state = "ready";
          renderList();
        }, function (err) {
          if (token !== loadToken) return;
          listStatus.dataset.state = "error";
          listStatus.textContent = "";
          util.replace(listHost, h("div.dl-var-empty", MC.errorText(err)));
        });
      }

      fileSel.addEventListener("change", loadFile);
      searchInput.addEventListener("input", renderList);
      pathInput.addEventListener("input", function () {
        if (listStatus.dataset.state !== "loading") renderList();
      });
      loadFile();

      MC.modal({
        title: "系数绑定 · " + setTitle(id),
        size: "xwide",
        body: h(
          "div.dl-bind-grid",
          h(
            "div.col",
            { style: "gap:12px" },
            MC.field({ label: "来源配置文件", control: fileSel }),
            MC.field({ label: "变量路径", control: pathInput, help: "可在右侧选择变量，或填写、粘贴路径。" }),
            mapField
          ),
          h(
            "div.dl-var-pane",
            h("div.dl-var-head", h("span.section-title", { style: "margin:0" }, "变量"), searchInput),
            listHost,
            listStatus
          )
        ),
        footer: function (close) {
          return [
            h("button.btn.btn-danger-ghost", { type: "button", onclick: function () { set.binding = { source_file: "", variable_path: "", field_map: {} }; close(); render(); } }, "解除绑定"),
            h("span.spacer"),
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              onclick: function () {
                var map = {};
                var err = null;
                mapInput.value.split("\n").map(function (l) { return l.trim(); }).filter(Boolean).forEach(function (l) {
                  var p = l.split("=");
                  var fld = (p[0] || "").trim();
                  var ck = (p[1] || "").trim();
                  if (!fld || KEYS.indexOf(ck) < 0) err = "无法识别：“" + l + "”";
                  else map[fld] = ck;
                });
                if (err) return mapField.setError(err);
                set.binding = { source_file: fileSel.value, variable_path: pathInput.value.trim(), field_map: map };
                close();
                render();
                MC.toast.info("绑定已更新到草稿", "点击「从配置刷新系数」预览提取结果");
              },
            }, "应用到草稿"),
          ];
        },
      });
    }

    load();
    return {
      el: el,
      title: "DL 计算",
      icon: "calc",
      update: function (r) {
        var qy = r.query || {};
        var changed = false;
        if (qy.item && qy.item !== S.item && S.draft) { S.item = qy.item; changed = true; }
        if ((qy.aom === "delay" || qy.aom === "width") && qy.aom !== S.aom && S.draft) { S.aom = qy.aom; changed = true; }
        if (changed) render();
      },
      isDirty: anyDirty,
      destroy: function () {
        clearTimeout(multiTimer);
        clearTimeout(solveTimer);
        if (resizeObs) resizeObs.disconnect();
      },
    };
  };
})(window);
