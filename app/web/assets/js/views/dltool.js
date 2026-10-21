/*
 * DL 快捷计算：f(x) = k1·x + k2·x² + … + k8·x⁸ + b，三个计算项（PP 能量 / RP 能量 / RP 脉宽）。
 * - 参数修改先进入“本地草稿”，所有人都可用草稿试算；只有部署者能把草稿保存为共享配置（If-Match 防覆盖）。
 * - 计算由服务端完成（POST .../compute，传入草稿参数），结果按 input_index 与输入一一对应，越界行明确标出而不是静默丢弃。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;
  var KEYS = ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "b"];
  var SUB = { k1: "k₁", k2: "k₂", k3: "k₃", k4: "k₄", k5: "k₅", k6: "k₆", k7: "k₇", k8: "k₈", b: "b" };
  var ITEMS = [
    { id: "pp_energy", label: "PP 能量" },
    { id: "rp_energy", label: "RP 能量" },
    { id: "rp_width", label: "RP 脉宽" },
  ];
  var AXES = {
    pp_energy: { x: "电动波片角度", xUnit: "urad", y: "PP@PF能量", yUnit: "mJ" },
    rp_energy: { x: "RP能量", xUnit: "mJ", y: "TFP2角度(SMTM页面)", yUnit: "" },
    rp_width: { x: "RP脉宽", xUnit: "", delay: "AOM2 delay(SMSM1页面)", width: "AOM2 width(SMSM1页面)", unit: "ns" },
  };

  function withUnit(name, unit) {
    return unit ? name + "（" + unit + "）" : name;
  }

  function axisOf(id, item) {
    if (id !== "rp_width") {
      var spec = AXES[id];
      return { x: spec.x, xUnit: spec.xUnit, y: spec.y, yUnit: spec.yUnit, other: "", otherUnit: "" };
    }
    var mode = item && item.aom_mode === "width" ? "width" : "delay";
    var yName = mode === "width" ? AXES.rp_width.width : AXES.rp_width.delay;
    var other = mode === "width" ? AXES.rp_width.delay : AXES.rp_width.width;
    return { x: AXES.rp_width.x, xUnit: AXES.rp_width.xUnit, y: yName, yUnit: AXES.rp_width.unit, other: other, otherUnit: AXES.rp_width.unit, mode: mode };
  }

  function complementOf(y, item) {
    var sum = finiteNum(item && item.aom_sum);
    var value = finiteNum(y);
    if (sum === null || value === null) return null;
    return sum - value;
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

  var PLOT_KEYS = ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8"];

  function finiteNum(v) {
    if (v === null || v === undefined || v === "") return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function evalCurveY(item, id, x) {
    var c = item.coeffs || {};
    var y = Number(c.b || 0);
    var p = 1;
    for (var i = 0; i < PLOT_KEYS.length; i++) {
      p *= x;
      var k = Number(c[PLOT_KEYS[i]] || 0);
      if (k) y += k * p;
    }
    if (id === "pp_energy") {
      var den = Number(item.power_conv_coeff);
      var num = Number(item.trans_coeff);
      if (!isFinite(den) || den === 0 || !isFinite(num)) return NaN;
      y *= num / den;
    }
    return y;
  }

  function curveReady(item, id) {
    if (id !== "pp_energy") return true;
    var den = Number(item.power_conv_coeff);
    var num = Number(item.trans_coeff);
    return isFinite(den) && den !== 0 && isFinite(num);
  }

  function collectMarks(item, id, mode, text, entry) {
    var parsed = parseNumbers(text).values;
    var rs = entry && entry.res && entry.res.results;
    var same = !!(rs && rs.length === parsed.length);
    if (same) {
      for (var i = 0; i < rs.length; i++) {
        var got = mode === "forward" ? rs[i].x : rs[i].y;
        if (got !== parsed[i]) { same = false; break; }
      }
    }
    var marks = [];
    if (same && mode === "forward") {
      rs.forEach(function (r) {
        var y = r.y;
        if (y === undefined || y === null) y = evalCurveY(item, id, r.x);
        if (!isFinite(r.x) || !isFinite(y)) return;
        marks.push({ x: r.x, y: y, status: r.status || "ok", label: String(r.input_index + 1) });
      });
      return marks;
    }
    if (same && mode === "reverse") {
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
    if (mode === "forward") {
      parsed.forEach(function (x, index) {
        var y = evalCurveY(item, id, x);
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

  function paintPlot(canvas, summaryEl, item, id, marks) {
    var axis = axisOf(id, item);
    var yName = withUnit(axis.y, axis.yUnit);
    var xName = withUnit(axis.x, axis.xUnit);
    function say(text) {
      if (summaryEl) summaryEl.textContent = text;
      canvas.setAttribute("aria-label", text);
    }
    if (!curveReady(item, id)) {
      say("功率转换系数不能为 0，暂时无法绘制曲线。");
      var ctx0 = canvas.getContext("2d");
      if (ctx0) {
        ctx0.setTransform(1, 0, 0, 1, 0, 0);
        ctx0.clearRect(0, 0, canvas.width, canvas.height);
      }
      return;
    }
    var parent = canvas.parentNode;
    var cssW = parent && parent.clientWidth ? parent.clientWidth : 640;
    if (cssW < 280) cssW = 280;
    var cssH = 280;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.width = "100%";
    canvas.style.height = cssH + "px";
    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    var xmin = finiteNum(item.x_min);
    var xmax = finiteNum(item.x_max);
    var yminB = finiteNum(item.y_min);
    var ymaxB = finiteNum(item.y_max);
    var xs = [];
    marks.forEach(function (m) { xs.push(m.x); });
    if (xmin !== null) xs.push(xmin);
    if (xmax !== null) xs.push(xmax);
    var xLo = 0;
    var xHi = 10;
    if (xmin !== null && xmax !== null && xmin < xmax) {
      xLo = xmin;
      xHi = xmax;
    } else if (xs.length) {
      xLo = Math.min.apply(null, xs);
      xHi = Math.max.apply(null, xs);
    }
    marks.forEach(function (m) {
      if (m.x < xLo) xLo = m.x;
      if (m.x > xHi) xHi = m.x;
    });
    if (xLo === xHi) {
      var pad0 = Math.max(Math.abs(xLo) * 0.1, 1);
      xLo -= pad0;
      xHi += pad0;
    } else {
      var pad = (xHi - xLo) * 0.06;
      xLo -= pad;
      xHi += pad;
    }

    var samples = [];
    var steps = 240;
    for (var si = 0; si <= steps; si++) {
      var sx = xLo + (xHi - xLo) * (si / steps);
      samples.push({ x: sx, y: evalCurveY(item, id, sx) });
    }
    var finiteYs = [];
    samples.forEach(function (s) { if (isFinite(s.y)) finiteYs.push(s.y); });
    var anchors = [];
    if (yminB !== null) anchors.push(yminB);
    if (ymaxB !== null) anchors.push(ymaxB);
    marks.forEach(function (m) { if (isFinite(m.y)) anchors.push(m.y); });
    var yPool = anchors.slice();
    if (anchors.length) {
      var aLo = Math.min.apply(null, anchors);
      var aHi = Math.max.apply(null, anchors);
      var aSpan = Math.max(aHi - aLo, Math.abs(aLo) * 0.05, 1);
      finiteYs.forEach(function (y) {
        if (y >= aLo - aSpan * 3 && y <= aHi + aSpan * 3) yPool.push(y);
      });
    } else if (finiteYs.length) {
      var sorted = finiteYs.slice().sort(function (a, b) { return a - b; });
      var i10 = Math.floor(sorted.length * 0.08);
      var i90 = Math.min(sorted.length - 1, Math.max(Math.ceil(sorted.length * 0.92) - 1, 0));
      yPool.push(sorted[i10], sorted[i90]);
    }
    if (yPool.length < 2 && finiteYs.length) yPool.push(Math.min.apply(null, finiteYs), Math.max.apply(null, finiteYs));
    if (!yPool.length) {
      say("当前系数在此范围内数值过大，无法绘制曲线。");
      ctx.fillStyle = cssColor("--c-surface-2", "#f9fafb");
      ctx.fillRect(0, 0, cssW, cssH);
      ctx.fillStyle = cssColor("--c-text-3", "#5d6676");
      ctx.font = "13px sans-serif";
      ctx.fillText("当前系数在此范围内数值过大，无法绘制曲线。", 16, 40);
      return;
    }
    var yLo = Math.min.apply(null, yPool);
    var yHi = Math.max.apply(null, yPool);
    if (yLo === yHi) {
      var ypad0 = Math.max(Math.abs(yLo) * 0.15, 1);
      yLo -= ypad0;
      yHi += ypad0;
    } else {
      var ypad = (yHi - yLo) * 0.12;
      yLo -= ypad;
      yHi += ypad;
    }

    var plotL = 56;
    var plotR = 16;
    var plotT = 16;
    var plotB = 32;
    var plotW = Math.max(cssW - plotL - plotR, 10);
    var plotH = Math.max(cssH - plotT - plotB, 10);
    function X(x) { return plotL + ((x - xLo) / (xHi - xLo)) * plotW; }
    function Y(y) { return plotT + ((yHi - y) / (yHi - yLo)) * plotH; }

    var grid = cssColor("--c-guide", "#e4e7ec");
    var text = cssColor("--c-text-3", "#5d6676");
    var primary = cssColor("--c-primary", "#2563eb");
    var warning = cssColor("--c-warning", "#d97706");
    var danger = cssColor("--c-danger", "#dc2626");
    var surface = cssColor("--c-surface", "#ffffff");

    ctx.fillStyle = cssColor("--c-surface-2", "#f9fafb");
    ctx.fillRect(0, 0, cssW, cssH);
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
      ctx.fillText(util.formatNumber(xt), px, plotT + plotH + 6);
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
      ctx.textAlign = lx > plotL + plotW - 52 ? "right" : "left";
      ctx.textBaseline = "top";
      ctx.fillText(label, lx > plotL + plotW - 52 ? lx - 4 : lx + 4, plotT + 4);
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
    vline(xmin, xName + " 下限");
    vline(xmax, xName + " 上限");
    hline(yminB, yName + " 下限");
    hline(ymaxB, yName + " 上限");

    marks.forEach(function (m) {
      if (m.x < xLo || m.x > xHi || m.y < yLo || m.y > yHi) return;
      var mx = X(m.x);
      var my = Y(m.y);
      var out = m.status === "out_of_x_range" || m.status === "out_of_y_range";
      ctx.beginPath();
      ctx.arc(mx, my, 5, 0, Math.PI * 2);
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
      ctx.fillStyle = text;
      ctx.font = "11px sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(m.label, mx + 6, my - 4);
    });

    var outCount = 0;
    marks.forEach(function (m) {
      if (m.status === "out_of_x_range" || m.status === "out_of_y_range") outCount++;
    });
    var summary = "曲线表示当前系数下 " + xName + " 与 " + yName + " 的关系。竖虚线是 " + xName + " 边界，横虚线是 " + yName + " 边界。";
    if (marks.length) summary += "已标出 " + marks.length + " 个计算点" + (outCount ? "，其中 " + outCount + " 个超出边界（空心橙点）。" : "。");
    else summary += "在下方输入数值后，计算点会标在曲线上。";
    say(summary);
  }

  MC.views.dl = function (app, route) {
    var S = { rev: null, saved: null, draft: null, item: (route.query && route.query.item) || "pp_energy", mode: "forward", inputs: { forward: "", reverse: "" }, results: {}, multi: {} };
    var el = h("div");
    var header = h("div.view-header");
    var bodyEl = h("div.view-body");
    el.append(header, bodyEl);
    var plotCanvas = null;
    var plotSummary = null;
    function drawPlot() {
      if (!plotCanvas || !plotCanvas.isConnected) return;
      var id = S.item;
      var item = S.draft && S.draft[id];
      if (!item) return;
      paintPlot(plotCanvas, plotSummary, item, id, collectMarks(item, id, S.mode, S.inputs[S.mode], S.results[id + ":" + S.mode]));
    }
    function onPlotResize() { drawPlot(); }
    window.addEventListener("resize", onPlotResize);

    function clone(x) { return JSON.parse(JSON.stringify(x)); }
    function itemDirty(id) { return S.saved && JSON.stringify(S.saved[id]) !== JSON.stringify(S.draft[id]); }
    function anyDirty() { return !!S.saved && ITEMS.some(function (it) { return itemDirty(it.id); }); }

    function load() {
      util.replace(bodyEl, MC.stateBlock({ kind: "loading" }));
      api.get(app.p("/dl")).then(function (res) {
        S.rev = res.rev;
        S.saved = res.items;
        S.draft = clone(res.items);
        render();
      }, function (err) { util.replace(bodyEl, MC.errorBlock(err, load)); });
    }

    function renderHeader() {
      var seg = MC.segmented(ITEMS.map(function (it) { return { value: it.id, label: it.label + (S.saved && itemDirty(it.id) ? " •" : "") }; }), S.item, function (v) {
        S.item = v;
        app.go({ kind: "dl", query: { item: v } }, true);
        render();
      }, "计算项");
      util.replace(header, h("div.view-title", h("h1", "DL 快捷计算"), h("div.view-sub", "八阶多项式正向计算与反向求解。修改参数可直接试算，保存后才会影响其他人。")), seg);
    }

    function render() {
      renderHeader();
      app.renderTabbar();
      var id = S.item;
      var item = S.draft[id];
      util.replace(bodyEl, h("div.container", { style: "max-width:1240px" }, dirtyBar(id), h("div.dl-layout", paramsPanel(id, item), calcPanel(id, item))));
      scheduleMultiCheck(id);
      requestAnimationFrame(drawPlot);
    }

    function dirtyBar(id) {
      if (!itemDirty(id)) return null;
      var save = h("button.btn.btn-primary.btn-sm", { type: "button", disabled: !app.can("deployer"), title: app.can("deployer") ? null : "仅部署者可保存为共享配置" }, "保存为配置");
      save.addEventListener("click", function () {
        MC.withBusy(save, function () {
          return api.put(app.p("/dl/" + id), S.draft[id], { "If-Match": S.rev }).then(
            function (res) {
              S.rev = res.rev;
              S.saved[id] = res.item;
              S.draft[id] = clone(res.item);
              MC.toast.success("计算参数已保存");
              app.syncRevision();
              render();
            },
            function (err) {
              if (err.code === "REVISION_CONFLICT") {
                MC.confirm({ title: "参数已被他人修改", message: "其他人在你编辑期间保存了新的计算参数。你可以放弃本地修改并加载最新参数，或保留草稿继续试算。", confirmText: "加载最新参数", cancelText: "保留草稿" }).then(function (ok) { if (ok) load(); });
              } else MC.toastError("保存失败", err);
            }
          );
        });
      });
      return h(
        "div.banner.banner-warning",
        { style: "margin-bottom:16px" },
        icon("edit"),
        h("div.banner-body", "参数已修改，尚未保存。", app.can("deployer") ? "" : "你可以用修改后的参数试算，但只有部署者能保存为共享配置。"),
        h("button.btn.btn-sm", { type: "button", onclick: function () { S.draft[id] = clone(S.saved[id]); S.results = {}; render(); } }, "放弃修改"),
        save
      );
    }

    function numInput(value, onChange, label) {
      var inp = h("input.input.input-sm.input-mono", { type: "text", inputmode: "decimal", value: value === null || value === undefined ? "" : String(value), "aria-label": label, placeholder: "未设置" });
      inp.addEventListener("change", function () {
        var v = numOrNull(inp.value);
        if (Number.isNaN(v)) {
          inp.classList.add("is-invalid");
          MC.toast.warning(label + " 不是有效数值");
          return;
        }
        inp.classList.remove("is-invalid");
        onChange(v);
        render();
      });
      return inp;
    }

    function aomPanel(item) {
      var mode = item.aom_mode === "width" ? "width" : "delay";
      var seg = MC.segmented(
        [{ value: "delay", label: "AOM2 delay 模式" }, { value: "width", label: "AOM2 width 模式" }],
        mode,
        function (v) { item.aom_mode = v; render(); },
        "RP 脉宽结果模式"
      );
      var other = mode === "width" ? AXES.rp_width.delay : AXES.rp_width.width;
      var yName = mode === "width" ? AXES.rp_width.width : AXES.rp_width.delay;
      return h(
        "div",
        h("div.section-title", "AOM2"),
        h("div.col", { style: "gap:8px" },
          seg,
          h("div.field", h("label.field-label", "AOM2 delay + AOM2 width（ns）"), numInput(item.aom_sum, function (v) { item.aom_sum = v; }, "AOM2 delay 与 width 的和")),
          h("div.field-help", "这个和是固定值，可以改。当前 y 是" + yName + "，" + other + " = 和 − y。")
        )
      );
    }

    function paramsPanel(id, item) {
      var coeffs = h(
        "div.coeff-grid",
        KEYS.map(function (k) {
          return h("div.field", h("label.field-label", SUB[k]), numInput(item.coeffs[k], function (v) { item.coeffs[k] = v === null ? 0 : v; }, "系数 " + SUB[k]));
        })
      );
      var terms = KEYS.filter(function (k) { return k !== "b" && Number(item.coeffs[k]); }).map(function (k) { return util.formatNumber(item.coeffs[k]) + "·x" + (k === "k1" ? "" : k.slice(1).replace(/\d/g, function (d) { return "⁰¹²³⁴⁵⁶⁷⁸⁹"[d]; })); });
      var axis = axisOf(id, item);
      var formula = h(
        "div.formula",
        h("div", "f(x) = " + (terms.length ? terms.join(" + ").replace(/\+ -/g, "− ") : "0") + " + " + util.formatNumber(item.coeffs.b)),
        id === "pp_energy" ? h("div", "PP@PF能量 = f(x) × 传输系数 ÷ 功率转换系数") : null,
        h("div", "x：" + withUnit(axis.x, axis.xUnit) + "　　y：" + withUnit(axis.y, axis.yUnit))
      );

      var pp = id === "pp_energy"
        ? h(
            "div.form-grid",
            { style: "grid-template-columns:1fr 1fr" },
            h("div.field", h("label.field-label", "传输系数"), numInput(item.trans_coeff, function (v) { item.trans_coeff = v; }, "传输系数")),
            h("div.field", h("label.field-label", "功率转换系数"), numInput(item.power_conv_coeff, function (v) { item.power_conv_coeff = v; }, "功率转换系数"), h("div.field-help", "不能为 0"))
          )
        : null;

      var ranges = h(
        "div.form-grid",
        { style: "grid-template-columns:repeat(4,1fr)" },
        [["x_min", withUnit(axis.x, axis.xUnit) + " 下限"], ["x_max", withUnit(axis.x, axis.xUnit) + " 上限"], ["y_min", withUnit(axis.y, axis.yUnit) + " 下限"], ["y_max", withUnit(axis.y, axis.yUnit) + " 上限"]].map(function (r) {
          return h("div.field", h("label.field-label", r[1]), numInput(item[r[0]], function (v) { item[r[0]] = v; }, r[1]));
        })
      );

      var b = item.binding || {};
      var bindInfo = b.source_file
        ? h(
            "div.col",
            { style: "gap:6px" },
            h("dl.kv", h("dt", "来源文件"), h("dd", h("button.link-btn", { type: "button", onclick: function () { app.openFile(b.source_file, { node: b.variable_path }); } }, b.source_file)), h("dt", "变量路径"), h("dd.mono.xsmall", b.variable_path), h("dt", "字段映射"), h("dd.mono.xsmall", Object.keys(b.field_map || {}).map(function (k) { return k + "→" + b.field_map[k]; }).join("  ") || "—")),
            h("div.row", h("button.btn.btn-sm", { type: "button", onclick: function (e) { extract(id, b, e.currentTarget); } }, icon("refresh"), "从配置刷新系数"), h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { bindingDialog(id); } }, "修改绑定"))
          )
        : h("div.row", h("span.small.muted.grow", "未绑定。绑定后可从配置文件一键提取系数。"), h("button.btn.btn-sm", { type: "button", onclick: function () { bindingDialog(id); } }, icon("link"), "配置绑定"));

      return h(
        "section.panel",
        h("div.panel-header", h("h2", "函数与参数")),
        h(
          "div.panel-body.col",
          { style: "gap:16px" },
          formula,
          h("div", h("div.section-title", "多项式系数"), coeffs),
          pp ? h("div", h("div.section-title", "PP 能量换算"), pp) : null,
          h("div", h("div.section-title", "有效范围（留空表示不限制）"), ranges, h("div.field-help", { style: "margin-top:6px" }, "反向求解在 " + withUnit(axis.x, axis.xUnit) + " 范围内找根（未设置时为 −100 ~ 100）；超出范围的输入会在结果中标出。")),
          id === "rp_width" ? aomPanel(item) : null,
          h("div", h("div.section-title", "系数绑定"), bindInfo),
          (S.multi[id] = S.multi[id] || h("div"))
        )
      );
    }

    var multiTimer = null;
    function scheduleMultiCheck(id) {
      clearTimeout(multiTimer);
      multiTimer = setTimeout(function () {
        api.post(app.p("/dl/" + id + "/compute"), { mode: "multi_check", params: S.draft[id] }).then(function (res) {
          var host = S.multi[id];
          if (!host) return;
          util.replace(
            host,
            res.has_multi
              ? h(
                  "div.banner.banner-warning",
                  icon("alert"),
                  h("div.banner-body", h("div", h("strong", "存在一对多："), "当前系数在 x 范围内对部分 y 有多个解，反向求解结果可能不唯一。建议收窄 x 范围。"), h("div.xsmall", { style: "margin-top:4px" }, res.cases.slice(0, 3).map(function (c) { return "y=" + util.formatNumber(c.y) + " → x=" + c.x_values.map(util.formatNumber).join(", "); }).join("；")))
                )
              : null
          );
        }, function (err) {
          if (S.multi[id]) util.replace(S.multi[id], h("div.banner.banner-danger", icon("alert"), h("div.banner-body", MC.errorText(err))));
        });
      }, 300);
    }

    function extract(id, binding, btn) {
      MC.withBusy(btn, function () {
        return api.post(app.p("/dl/" + id + "/extract"), { binding: binding }).then(function (res) {
          var item = S.draft[id];
          var rows = res.fields.map(function (f) {
            var old = item.coeffs[f.coeff];
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
            title: "从配置刷新系数",
            subtitle: binding.source_file + " › " + binding.variable_path,
            size: "wide",
            body: h(
              "div.col",
              { style: "gap:12px" },
              hasBad ? h("div.banner.banner-warning", icon("alert"), h("div.banner-body", "部分字段无法提取，这些系数将保持原值，不会被替换为 0。")) : null,
              h("table.table.is-compact", h("thead", h("tr", h("th", "字段"), h("th", "系数"), h("th", "当前"), h("th", "提取值"), h("th", "状态"))), h("tbody", rows))
            ),
            footer: function (close) {
              return [
                h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
                h("button.btn.btn-primary", { type: "button", disabled: !okCount, onclick: function () { Object.keys(res.coeffs).forEach(function (k) { item.coeffs[k] = res.coeffs[k]; }); close(); MC.toast.success("已更新 " + okCount + " 个系数到草稿", "确认无误后点击「保存为配置」"); render(); } }, "应用到草稿"),
              ];
            },
          });
        }, function (err) { MC.toastError("提取失败", err); });
      });
    }

    function bindingDialog(id) {
      var item = S.draft[id];
      var b = item.binding || { source_file: "", variable_path: "", field_map: {} };
      var fileSel = h("select.select", h("option", { value: "" }, "请选择文件"), app.files.map(function (f) { return h("option", { value: f.name, selected: f.name === b.source_file }, f.name); }));
      var pathInput = h("input.input.input-mono", { type: "text", value: b.variable_path || "", placeholder: "例如 scenario_a.xml/CONFIG_ROOT/SCENARIO_BLOCK/input" });
      var mapInput = h("textarea.textarea.input-mono", { rows: 5, placeholder: "m1=k1\nm2=k2\nn=b" }, Object.keys(b.field_map || {}).map(function (k) { return k + "=" + b.field_map[k]; }).join("\n"));
      var mapField = MC.field({ label: "字段 → 系数映射", control: mapInput, help: "每行一条：字段名=系数名，系数名为 k1…k8 或 b" });
      MC.modal({
        title: "系数绑定 · " + item.name,
        size: "wide",
        body: h(
          "div.col",
          { style: "gap:12px" },
          MC.field({ label: "来源配置文件", control: fileSel }),
          MC.field({ label: "变量路径", control: pathInput, help: "可粘贴详情面板的绑定路径。XML 与 JSON 都支持，例如 config.xml/CONFIG_ROOT/input，也可填节点 ID 或定位符。属性写成 min 或 @min 都可以。" }),
          mapField
        ),
        footer: function (close) {
          return [
            h("button.btn.btn-danger-ghost", { type: "button", onclick: function () { item.binding = { source_file: "", variable_path: "", field_map: {} }; close(); render(); } }, "解除绑定"),
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
                item.binding = { source_file: fileSel.value, variable_path: pathInput.value.trim(), field_map: map };
                close();
                render();
                MC.toast.info("绑定已更新到草稿", "点击「从配置刷新系数」预览提取结果");
              },
            }, "应用到草稿"),
          ];
        },
      });
    }

    function calcPanel(id, item) {
      var mode = S.mode;
      var axis = axisOf(id, item);
      var xLabel = withUnit(axis.x, axis.xUnit);
      var yLabel = withUnit(axis.y, axis.yUnit);
      var ta = h("textarea.textarea.input-mono", { rows: 4, placeholder: mode === "forward" ? "输入" + xLabel + "，可用空格、逗号或换行分隔\n例如：10 20 35.5" : "输入目标 " + yLabel + "\n例如：40 60", "aria-label": "输入值" }, S.inputs[mode]);
      ta.addEventListener("input", function () { S.inputs[mode] = ta.value; drawPlot(); });
      var runBtn = h("button.btn.btn-primary", { type: "button" }, icon(mode === "forward" ? "arrow-right" : "search"), mode === "forward" ? "计算" : "求解");
      var resHost = h("div");
      runBtn.addEventListener("click", function () {
        var p = parseNumbers(ta.value);
        if (!p.values.length) return MC.toast.warning("请输入至少一个数值");
        if (p.bad.length) MC.toast.warning("已忽略无法识别的内容", p.bad.slice(0, 5).join("、"));
        MC.withBusy(runBtn, function () {
          return api.post(app.p("/dl/" + id + "/compute"), { mode: mode, values: p.values, params: item }).then(function (res) {
            S.results[id + ":" + mode] = { res: res, choice: {} };
            renderResults();
          }, function (err) { MC.toastError("计算失败", err); });
        });
      });
      ta.addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runBtn.click(); });

      function renderResults() {
        var entry = S.results[id + ":" + mode];
        if (!entry) {
          util.replace(resHost, MC.stateBlock({ kind: "empty", icon: "calc", title: "结果会显示在这里", desc: "Ctrl+Enter 快速计算" }));
          return;
        }
        var rs = entry.res.results;
        var xLabel = withUnit(axis.x, axis.xUnit);
        var yLabel = withUnit(axis.y, axis.yUnit);
        var otherLabel = axis.other ? withUnit(axis.other, axis.otherUnit) : "";
        function otherCell(y) {
          if (!otherLabel) return null;
          var other = complementOf(y, item);
          return h("td.mono", other === null ? "—" : util.formatNumber(other));
        }
        var STATUS = { ok: null, out_of_x_range: xLabel + " 超出范围", out_of_y_range: yLabel + " 超出范围", none: "范围内无实数解", identity: "常数函数：所有 x 都满足", single: null, multiple: null };
        var table;
        if (mode === "forward") {
          table = h("table.table.is-compact.result-table", h("thead", h("tr", h("th", "#"), h("th", xLabel), id === "pp_energy" ? h("th", "f(x)") : null, h("th", yLabel), otherLabel ? h("th", otherLabel) : null, h("th", "状态"))), h("tbody", rs.map(function (r) {
            return h("tr", h("td.num.muted", String(r.input_index + 1)), h("td.mono", util.formatNumber(r.x)), id === "pp_energy" ? h("td.mono.muted", r.y_base === undefined ? "—" : util.formatNumber(r.y_base)) : null, h("td.mono", { style: "color:var(--c-primary-text);font-weight:600" }, r.y === undefined ? "—" : util.formatNumber(r.y)), otherCell(r.y), h("td", STATUS[r.status] ? h("span.badge.badge-warning", STATUS[r.status]) : h("span.badge.badge-success", "正常")));
          })));
        } else {
          table = h("table.table.is-compact.result-table", h("thead", h("tr", h("th", "#"), h("th", "目标 " + yLabel), otherLabel ? h("th", otherLabel) : null, h("th", "解 " + xLabel), h("th", "状态"))), h("tbody", rs.map(function (r) {
            var cell;
            if (r.status === "multiple") {
              var sel = h("select.select.input-sm", { "aria-label": "选择第 " + (r.input_index + 1) + " 行采用的解", style: "width:auto" }, h("option", { value: "" }, "选择采用的解…"), r.x_values.map(function (x) { return h("option", { value: String(x), selected: entry.choice[r.input_index] === x }, util.formatNumber(x)); }));
              sel.addEventListener("change", function () { entry.choice[r.input_index] = sel.value === "" ? undefined : Number(sel.value); drawPlot(); });
              cell = h("td", h("div.row", h("span.mono.multi", r.x_values.map(util.formatNumber).join(", ")), sel));
            } else cell = h("td.mono", r.x_values && r.x_values.length ? util.formatNumber(r.x_values[0]) : "—");
            return h("tr", h("td.num.muted", String(r.input_index + 1)), h("td.mono", util.formatNumber(r.y)), otherCell(r.y), cell, h("td", r.status === "multiple" ? h("span.badge.badge-warning", r.x_values.length + " 个解") : STATUS[r.status] ? h("span.badge.badge-danger", STATUS[r.status]) : h("span.badge.badge-success", "唯一解")));
          })));
        }
        var multi = mode === "reverse" && rs.some(function (r) { return r.status === "multiple"; });
        function csv() {
          if (mode === "forward") {
            var head = [xLabel, yLabel];
            if (otherLabel) head.push(otherLabel);
            head.push("状态");
            return [head.join(",")].concat(rs.map(function (r) {
              var row = [r.x, r.y === undefined ? "" : r.y];
              if (otherLabel) {
                var other = complementOf(r.y, item);
                row.push(other === null ? "" : other);
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
              var other = complementOf(r.y, item);
              row.push(other === null ? "" : other);
            }
            row.push(x, r.status === "multiple" ? (entry.choice[r.input_index] !== undefined ? "已选定" : "多解") : STATUS[r.status] || "唯一解");
            return row.join(",");
          })).join("\n");
        }
        drawPlot();
        util.replace(
          resHost,
          h("div.row", { style: "margin-bottom:8px" }, h("span.small.muted.grow", rs.length + " 条结果" + (mode === "reverse" ? " · 结果为在范围内检测到的候选根" : "")), MC.copyButton(csv, "复制为 CSV"), h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "下载 CSV", "data-tip": "下载 CSV", onclick: function () { util.downloadBlob("\uFEFF" + csv(), item.name + "_" + mode + ".csv", "text/csv;charset=utf-8"); } }, icon("download"))),
          multi ? h("div.banner.banner-warning", { style: "margin-bottom:8px" }, icon("info"), h("div.banner-body", "部分目标值有多个解。请在下拉框中为每行选择采用的解，复制/下载结果时会使用你的选择；系统不会自动替你挑选。")) : null,
          h("div.table-wrap", { style: "max-height:420px;border:1px solid var(--c-border);border-radius:6px" }, table)
        );
      }

      var yName = yLabel;
      plotCanvas = h("canvas.dl-plot", { role: "img", "aria-label": "当前数值关系图" });
      plotSummary = h("p.dl-plot-summary", "正在绘制当前数值关系。");
      var plotCard = h(
        "div.dl-plot-card",
        h("div.section-title", "数值关系"),
        plotCanvas,
        h(
          "div.dl-plot-legend",
          h("span", h("i.lg-curve"), "曲线"),
          h("span", h("i.lg-x"), "x 边界"),
          h("span", h("i.lg-y"), yName + " 边界"),
          h("span", h("i.lg-pt"), "计算点")
        ),
        plotSummary
      );
      var seg = MC.segmented([{ value: "forward", label: "正向 " + xLabel + " → " + yName }, { value: "reverse", label: "反向 " + yName + " → " + xLabel }], mode, function (v) { S.mode = v; render(); }, "计算方向");
      var panel = h("section.panel", h("div.panel-header", h("h2", "计算"), seg), h("div.panel-body.col", { style: "gap:12px" }, plotCard, MC.field({ label: mode === "forward" ? "输入 " + xLabel : "输入目标 " + yName, control: ta }), h("div.row", h("span.xsmall.muted.grow", itemDirty(id) ? "将使用未保存的草稿参数计算" : "使用已保存的参数计算"), runBtn), resHost));
      renderResults();
      return panel;
    }

    load();
    return {
      el: el,
      title: "DL 计算",
      icon: "calc",
      update: function (r) {
        var it = r.query && r.query.item;
        if (it && it !== S.item && S.draft) { S.item = it; render(); }
      },
      isDirty: anyDirty,
      destroy: function () { clearTimeout(multiTimer); window.removeEventListener("resize", onPlotResize); },
    };
  };
})(window);
