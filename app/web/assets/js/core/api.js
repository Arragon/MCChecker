/*
 * API 客户端 + 事件总线 + 路由。
 * 后端契约见 docs/MCChecker_REDESIGN_SPEC.md §7；模板模式下由 mock/mock-api.js 实现同一契约。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;

  /* ===================== Event bus ===================== */
  var listeners = {};
  MC.bus = {
    on: function (evt, fn) {
      (listeners[evt] = listeners[evt] || []).push(fn);
      return function () {
        listeners[evt] = (listeners[evt] || []).filter(function (f) { return f !== fn; });
      };
    },
    emit: function (evt, payload) {
      (listeners[evt] || []).slice().forEach(function (fn) {
        try { fn(payload); } catch (e) { console.error("[bus]", evt, e); }
      });
    },
  };

  /* ===================== API ===================== */
  var BASE = "/api/v1";

  function ApiError(status, code, message, detail) {
    this.name = "ApiError";
    this.status = status;
    this.code = code || "UNKNOWN";
    this.message = message || "请求失败";
    this.detail = detail || null;
  }
  ApiError.prototype = Object.create(Error.prototype);

  var NETWORK_MESSAGE = "无法连接服务器，请检查网络或稍后重试";

  function buildQuery(query) {
    if (!query) return "";
    var parts = [];
    Object.keys(query).forEach(function (k) {
      var v = query[k];
      if (v === undefined || v === null || v === "") return;
      parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  function seg(s) {
    return encodeURIComponent(String(s));
  }

  function profilePath(pid, rest) {
    return "/p/" + seg(pid) + rest;
  }

  /**
   * request(method, path, {query, body, raw, headers})
   * - body: JSON 对象；raw: Blob/File/ArrayBuffer（上传原始字节，配合 X-Filename 头）
   * 返回解析后的 JSON；非 2xx 抛出 ApiError。
   */
  function request(method, path, opts) {
    opts = opts || {};
    var headers = Object.assign({ Accept: "application/json", "X-Requested-With": "MCChecker" }, opts.headers || {});
    if (MC.config && MC.config.mock) {
      return MC.mock
        .handle({ method: method, path: path, query: opts.query || {}, body: opts.body, raw: opts.raw, headers: headers })
        .then(function (res) {
          if (res.status >= 200 && res.status < 300) return res.body;
          var err = (res.body && res.body.error) || {};
          throw new ApiError(res.status, err.code, err.message, err.detail);
        });
    }
    var init = { method: method, headers: headers, credentials: "same-origin" };
    if (opts.raw !== undefined) {
      init.body = opts.raw;
      headers["Content-Type"] = "application/octet-stream";
    } else if (opts.body !== undefined) {
      init.body = JSON.stringify(opts.body);
      headers["Content-Type"] = "application/json; charset=utf-8";
    }
    return fetch(BASE + path + buildQuery(opts.query), init).then(
      function (resp) {
        var ct = resp.headers.get("Content-Type") || "";
        var parse = ct.indexOf("application/json") >= 0 ? resp.json() : resp.text().then(function () { return null; });
        return parse.then(function (data) {
          if (resp.ok) return data;
          var err = (data && data.error) || {};
          throw new ApiError(resp.status, err.code, err.message || "请求失败（HTTP " + resp.status + "）", err.detail);
        });
      },
      function () {
        throw new ApiError(0, "NETWORK", NETWORK_MESSAGE);
      }
    );
  }

  /** 取回二进制，不触发浏览器保存。mock 与生产走同一返回形状 {blob, filename}。 */
  function fetchBlob(path, query) {
    if (MC.config && MC.config.mock) {
      return MC.mock.download({ path: path, query: query || {} });
    }
    return fetch(BASE + path + buildQuery(query), { credentials: "same-origin" }).then(
      function (resp) {
        if (!resp.ok) {
          return resp.json().then(
            function (data) {
              var err = (data && data.error) || {};
              throw new ApiError(resp.status, err.code, err.message || "下载失败");
            },
            function () { throw new ApiError(resp.status, "DOWNLOAD_FAILED", "下载失败（HTTP " + resp.status + "）"); }
          );
        }
        var cd = resp.headers.get("Content-Disposition") || "";
        var m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
        var name = m ? decodeURIComponent(m[1]) : "";
        return resp.blob().then(function (blob) { return { blob: blob, filename: name }; });
      },
      function () { throw new ApiError(0, "NETWORK", NETWORK_MESSAGE); }
    );
  }

  /** 下载：先取回 Blob 再保存，失败时可给出明确原因与重试入口。 */
  function download(path, query, filename) {
    return fetchBlob(path, query).then(function (res) {
      util.downloadBlob(res.blob, res.filename || filename);
      return true;
    });
  }

  MC.ApiError = ApiError;
  MC.api = {
    request: request,
    download: download,
    fetchBlob: fetchBlob,
    seg: seg,
    p: profilePath,
    get: function (path, query) { return request("GET", path, { query: query }); },
    post: function (path, body, query) { return request("POST", path, { body: body || {}, query: query }); },
    put: function (path, body, headers) { return request("PUT", path, { body: body, headers: headers }); },
    patch: function (path, body) { return request("PATCH", path, { body: body }); },
    del: function (path, query) { return request("DELETE", path, { query: query }); },
    upload: function (path, file, filename, query) {
      return request(path.indexOf("/temp") >= 0 ? "POST" : "PUT", path, {
        raw: file,
        query: query,
        headers: { "X-Filename": encodeURIComponent(filename || file.name || "upload") },
      });
    },
  };

  /* ===================== 能力开关 ===================== */
  /*
   * 后端接口按里程碑逐卡接入（docs/MCChecker_DELIVERY_PLAN.md 第 6 节），而 index.html 与
   * template.html 共享同一批视图代码，两者唯一差别是 MC.config.mock。因此"哪些接口已经可用"
   * 集中在这里：生产模式下未接入的入口一律 disabled 并说明接入卡号，且不发请求（避免 404 噪声）。
   * 每张前端卡在自己的改动里把对应键翻成 true；M4-08 全部为 true 后本机制删除。
   */
  var CAP_KEYS = [
    "favorites", "remarks", "schedule", "search", "review", "dl", "settings", "import",
    "delete", "history", "compare", "records", "tools", "revision", "temp",
  ];

  /* 已经接入真实后端的接口。后端卡在自己的 PR 里实现接口，紧随的前端卡在这里加一项，
   * 视图代码本身不用改。必须始终是 CAP_KEYS 的子集（前端测试页有断言会红）。 */
  var WIRED = ["favorites", "revision"];

  MC.capsFor = function (mock) {
    var caps = {};
    CAP_KEYS.forEach(function (k) {
      caps[k] = !!mock || WIRED.indexOf(k) >= 0;
    });
    return caps;
  };

  MC.caps = MC.capsFor(!!(MC.config && MC.config.mock));
  MC.capKeys = CAP_KEYS.slice();
  MC.wiredCaps = WIRED.slice();

  /* 每项能力未接入时展示给用户的去向（卡号见 docs/MCChecker_DELIVERY_PLAN.md 第 6 节）。 */
  MC.unwired = {
    favorites: "收藏将在 M2-01 / M2-02 接入",
    remarks: "修改建议将在 M2-03 / M2-04 接入",
    schedule: "定时更新状态将在 M3-08 接入",
    search: "全局搜索将在 M2-05 / M2-06 接入",
    review: "审阅页将在 M3-02 / M3-03 接入",
    dl: "DL 计算将在 M3-10 ~ M3-12 接入",
    settings: "设置页将在 M3-06 / M3-13 ~ M3-16 接入",
    import: "导入本地文件或链接将在 M2-10 / M2-11 接入",
    delete: "删除文件（移入恢复区）将在 M2-12 接入",
    history: "历史版本将在 M2-07 / M2-09 接入",
    compare: "结构化对比将在 M2-08 / M2-09 接入",
    records: "修改记录将在 M3-09 接入",
    tools: "外部工具将在 M3-15 接入",
    revision: "多用户更新提示将在 M2-01 接入",
    temp: "临时文件将在 M2-10 / M2-11 接入",
  };

  /* ===================== Router (hash) ===================== */
  /*
   * #/p/{pid}/overview
   * #/p/{pid}/search?q=
   * #/p/{pid}/f/{name}?view=tree|history|compare|records&version=&node=
   * #/p/{pid}/t/{token}?node=
   * #/p/{pid}/review
   * #/p/{pid}/dl?item=
   * #/p/{pid}/settings/{section}
   */
  var KINDS = { overview: 1, search: 1, f: 1, t: 1, review: 1, dl: 1, settings: 1 };

  function parseHash(hash) {
    var raw = (hash || "").replace(/^#/, "");
    var qIdx = raw.indexOf("?");
    var pathPart = qIdx >= 0 ? raw.slice(0, qIdx) : raw;
    var queryPart = qIdx >= 0 ? raw.slice(qIdx + 1) : "";
    var segs = pathPart.split("/").filter(Boolean).map(function (s) {
      try { return decodeURIComponent(s); } catch (e) { return s; }
    });
    var query = {};
    queryPart.split("&").filter(Boolean).forEach(function (kv) {
      var i = kv.indexOf("=");
      var k = i >= 0 ? kv.slice(0, i) : kv;
      var v = i >= 0 ? kv.slice(i + 1) : "";
      try { query[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, " ")); } catch (e) { query[k] = v; }
    });
    if (segs[0] !== "p" || !segs[1]) return null;
    var kind = segs[2] || "overview";
    if (!KINDS[kind]) kind = "overview";
    return { pid: segs[1], kind: kind, arg: segs.slice(3).join("/") || "", query: query };
  }

  function formatRoute(r) {
    var s = "#/p/" + seg(r.pid) + "/" + r.kind;
    if (r.arg) s += "/" + seg(r.arg);
    var q = r.query ? buildQuery(r.query) : "";
    return s + q;
  }

  MC.router = {
    parse: parseHash,
    format: formatRoute,
    current: function () { return parseHash(global.location.hash); },
    go: function (r, replace) {
      var next = formatRoute(r);
      if (next === global.location.hash) {
        MC.bus.emit("route", parseHash(next));
        return;
      }
      if (replace) global.history.replaceState(null, "", next);
      else global.location.hash = next;
      if (replace) MC.bus.emit("route", parseHash(next));
    },
    start: function () {
      global.addEventListener("hashchange", function () {
        MC.bus.emit("route", parseHash(global.location.hash));
      });
    },
  };
})(window);
