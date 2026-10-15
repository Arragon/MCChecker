/*
 * [模板专用] DL 八阶多项式计算的 JS 等价实现，用于离线演示。
 * 生产环境由服务端 app/core/dltool.py 计算（规范 §9.8），两者算法与返回结构必须一致。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var KEYS = ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8"];

  function poly(x, c) {
    var y = Number(c.b || 0);
    var p = 1;
    for (var i = 0; i < 8; i++) {
      p *= x;
      var k = Number(c[KEYS[i]] || 0);
      if (k !== 0) y += k * p;
    }
    return y;
  }

  function isConstant(c) {
    return KEYS.every(function (k) { return !Number(c[k] || 0); });
  }

  function bisect(f, a, b) {
    var fa = f(a);
    var fb = f(b);
    if (fa === 0) return a;
    if (fb === 0) return b;
    for (var i = 0; i < 100; i++) {
      var m = (a + b) / 2;
      var fm = f(m);
      if (fm === 0 || (b - a) / 2 < 1e-13 * (1 + Math.abs(m))) return m;
      if (fa * fm < 0) {
        b = m;
        fb = fm;
      } else {
        a = m;
        fa = fm;
      }
    }
    return (a + b) / 2;
  }

  /** |f| 在 [a,b] 内的黄金分割极小值，用于发现不变号的切触重根。 */
  function minAbs(f, a, b) {
    var g = (Math.sqrt(5) - 1) / 2;
    var c = b - g * (b - a);
    var d = a + g * (b - a);
    for (var i = 0; i < 80; i++) {
      if (Math.abs(f(c)) < Math.abs(f(d))) b = d;
      else a = c;
      c = b - g * (b - a);
      d = a + g * (b - a);
    }
    return (a + b) / 2;
  }

  /**
   * 求 f(x)=target 在 [lo,hi] 内的候选实根。
   * 返回 {status:'ok'|'identity'|'none', roots:[{x,residual}]}；结果是“检测到的候选根”，不承诺穷尽。
   */
  function findRoots(target, c, lo, hi, samples) {
    samples = samples || 4000;
    if (isConstant(c)) {
      return Math.abs(Number(c.b || 0) - target) < 1e-12 ? { status: "identity", roots: [] } : { status: "none", roots: [] };
    }
    var f = function (x) { return poly(x, c) - target; };
    var scale = 1 + Math.abs(target);
    var tol = 1e-9 * scale;
    var step = (hi - lo) / samples;
    var roots = [];
    function add(x) {
      for (var i = 0; i < roots.length; i++) if (Math.abs(roots[i] - x) <= 1e-7 * (1 + Math.abs(x))) return;
      roots.push(x);
    }
    var xs = new Array(samples + 1);
    var ys = new Array(samples + 1);
    for (var i = 0; i <= samples; i++) {
      xs[i] = i === samples ? hi : lo + i * step;
      ys[i] = f(xs[i]);
    }
    for (i = 0; i <= samples; i++) {
      if (Math.abs(ys[i]) <= tol) add(xs[i]);
      if (i < samples && ys[i] * ys[i + 1] < 0) add(bisect(f, xs[i], xs[i + 1]));
      if (i > 0 && i < samples) {
        var a = Math.abs(ys[i - 1]);
        var m = Math.abs(ys[i]);
        var b = Math.abs(ys[i + 1]);
        if (m < a && m < b && ys[i - 1] * ys[i + 1] > 0) {
          var x = minAbs(f, xs[i - 1], xs[i + 1]);
          if (Math.abs(f(x)) <= 1e-7 * scale) add(x);
        }
      }
    }
    roots.sort(function (p, q) { return p - q; });
    return {
      status: roots.length ? "ok" : "none",
      roots: roots.map(function (x) { return { x: round(x), residual: Math.abs(f(x)) }; }),
    };
  }

  function round(x) {
    return Math.round(x * 1e10) / 1e10;
  }

  function inRange(v, lo, hi) {
    return (lo === null || lo === undefined || v >= lo) && (hi === null || hi === undefined || v <= hi);
  }

  function scaleOf(item, kind) {
    if (kind !== "pp_energy") return 1;
    return Number(item.trans_coeff) / Number(item.power_conv_coeff);
  }

  function validate(item, kind) {
    var nums = KEYS.concat(["b"]).map(function (k) { return item.coeffs[k]; });
    if (nums.some(function (v) { return !isFinite(Number(v)); })) return "系数必须是有限数值";
    if (kind === "pp_energy") {
      if (!isFinite(item.trans_coeff) || !isFinite(item.power_conv_coeff)) return "传输系数与功率转换系数必须是有限数值";
      if (Number(item.power_conv_coeff) === 0) return "功率转换系数不能为 0";
    }
    if (item.x_min !== null && item.x_max !== null && item.x_min >= item.x_max) return "x 范围下限必须小于上限";
    if (item.y_min !== null && item.y_max !== null && item.y_min >= item.y_max) return "y 范围下限必须小于上限";
    return null;
  }

  function forward(item, kind, xs) {
    var s = scaleOf(item, kind);
    return xs.map(function (x, i) {
      var r = { input_index: i, x: x };
      if (!inRange(x, item.x_min, item.x_max)) {
        r.status = "out_of_x_range";
        return r;
      }
      r.y_base = round(poly(x, item.coeffs));
      r.y = round(r.y_base * s);
      r.status = inRange(r.y, item.y_min, item.y_max) ? "ok" : "out_of_y_range";
      return r;
    });
  }

  function reverse(item, kind, ys) {
    var s = scaleOf(item, kind);
    var lo = item.x_min !== null && item.x_min !== undefined ? item.x_min : -100;
    var hi = item.x_max !== null && item.x_max !== undefined ? item.x_max : 100;
    return ys.map(function (y, i) {
      var r = { input_index: i, y: y, x_values: [] };
      if (!inRange(y, item.y_min, item.y_max)) {
        r.status = "out_of_y_range";
        return r;
      }
      r.y_base = round(y / s);
      var res = findRoots(r.y_base, item.coeffs, lo, hi);
      if (res.status === "identity") {
        r.status = "identity";
        return r;
      }
      r.x_values = res.roots.map(function (q) { return q.x; });
      r.residuals = res.roots.map(function (q) { return q.residual; });
      r.status = r.x_values.length === 0 ? "none" : r.x_values.length === 1 ? "single" : "multiple";
      return r;
    });
  }

  function multiCheck(item, kind) {
    var s = scaleOf(item, kind);
    var lo = item.x_min !== null && item.x_min !== undefined ? item.x_min : -100;
    var hi = item.x_max !== null && item.x_max !== undefined ? item.x_max : 100;
    var ysObs = [];
    for (var i = 0; i <= 800; i++) {
      var v = poly(lo + ((hi - lo) * i) / 800, item.coeffs);
      if (isFinite(v)) ysObs.push(v);
    }
    if (!ysObs.length) return { has_multi: false, cases: [] };
    var ymin = item.y_min !== null && item.y_min !== undefined ? item.y_min / s : Math.min.apply(null, ysObs);
    var ymax = item.y_max !== null && item.y_max !== undefined ? item.y_max / s : Math.max.apply(null, ysObs);
    var cases = [];
    for (i = 0; i < 21 && cases.length < 8; i++) {
      var yb = ymin + ((ymax - ymin) * i) / 20;
      var res = findRoots(yb, item.coeffs, lo, hi, 2000);
      if (res.roots.length > 1) cases.push({ y: round(yb * s), x_values: res.roots.map(function (q) { return q.x; }) });
    }
    return { has_multi: cases.length > 0, cases: cases };
  }

  MC.dlmath = { poly: poly, findRoots: findRoots, forward: forward, reverse: reverse, multiCheck: multiCheck, validate: validate, KEYS: KEYS };
})(window);
