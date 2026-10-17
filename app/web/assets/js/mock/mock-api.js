/*
 * [模板专用] 内存版 API，实现 docs/MCChecker_REDESIGN_SPEC.md §7 的全部接口契约。
 * 前端代码只依赖 MC.api。模板入口 template.html 加载本目录；生产入口 index.html 不加载 mock，由服务端 /config.js 提供 {mock:false}。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var parse = MC.mockParse;
  var dl = MC.dlmath;

  var S = MC.mockFixtures.build();
  var temps = {};
  var tasks = {};
  var idempotency = {};
  var rev = {};
  var docCache = {};

  var IDENTITIES = {
    "deployer-admin": { ip: "127.0.0.1", deployer: true, label: "部署者 + 管理员（本机）" },
    deployer: { ip: "::1", deployer: true, label: "部署者（本机，未登记）" },
    admin: { ip: "10.12.3.22", deployer: false, label: "管理员（王强）" },
    member: { ip: "10.12.3.21", deployer: false, label: "已登记人员（李娜）" },
    guest: { ip: "10.12.3.45", deployer: false, label: "访客（未登记）" },
  };
  var identityKey = util.storageGet("mc.demo.identity", "deployer-admin");
  if (!IDENTITIES[identityKey]) identityKey = "deployer-admin";

  Object.keys(S.profiles).forEach(function (pid) {
    var P = S.profiles[pid];
    P.people = P.people || JSON.parse(JSON.stringify(S.people));
    rev[pid] = { files: 1, favorites: 1, remarks: 1, settings: 1, tasks: 1 };
  });

  /* ---------------- helpers ---------------- */
  function ok(body, status) {
    return { status: status || 200, body: body };
  }
  function fail(status, code, message, detail) {
    return { status: status, body: { error: { code: code, message: message, detail: detail || null } } };
  }
  function bump(pid, part) {
    rev[pid][part]++;
  }
  function clone(x) {
    return JSON.parse(JSON.stringify(x));
  }
  function bytes(text) {
    return new Blob([text || ""]).size;
  }
  function profile(pid) {
    return S.profiles[pid] || null;
  }
  function stamp(d) {
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    d = d || new Date();
    return { day: d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()), sec: p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) };
  }
  function addSuffix(name, suffix) {
    var i = name.lastIndexOf(".");
    return i > 0 ? name.slice(0, i) + "_" + suffix + name.slice(i) : name + "_" + suffix;
  }

  function identity(pid) {
    var preset = IDENTITIES[identityKey];
    var P = profile(pid) || profile("default");
    var person = P.people.filter(function (x) { return x.ip === preset.ip; })[0];
    var name = person ? person.name : preset.ip;
    var admin = P.admins.some(function (a) { return a.name === name; });
    var label = preset.deployer && admin ? "部署者 · 管理员" : preset.deployer ? "部署者" : admin ? "管理员" : person ? "成员" : "访客";
    return { ip: preset.ip, name: name, registered: !!person, is_deployer: preset.deployer, is_admin: admin, role_label: label };
  }

  function fileText(entry) {
    if (entry.text === null && entry.generate) entry.text = entry.generate();
    return entry.text;
  }

  function versionEntry(P, name, version) {
    var f = P.files[name];
    if (!f) return null;
    version = version || "current";
    if (version === "current") return { entry: f.current, label: "当前版本", version: "current", display: name };
    var m = /^(archive|record):(.+)$/.exec(version);
    if (!m) return null;
    var list = m[1] === "archive" ? f.archives : f.records;
    var e = list.filter(function (x) { return x.filename === m[2]; })[0];
    if (!e) return null;
    return { entry: e, label: (m[1] === "archive" ? "历史版本 " : "修改记录 ") + e.filename, version: version, display: e.filename };
  }

  function compactDoc(text, displayName) {
    var key = parse.hash(text) + "|" + displayName;
    if (!docCache[key]) docCache[key] = parse.parse(text, displayName);
    return docCache[key];
  }

  function documentPayload(pid, name, ve, kind) {
    var text = fileText(ve.entry);
    var parsed = compactDoc(text, ve.display);
    return {
      file: {
        profile: pid,
        name: name,
        kind: kind || (ve.version === "current" ? "current" : ve.version.split(":")[0]),
        version: ve.version,
        version_label: ve.label,
        sha256: parse.hash(text),
        format: parsed.format,
        size: bytes(text),
        updated_at: ve.entry.mtime,
        parser_version: 2,
        node_count: parsed.nodes.p.length - 1,
        read_only: ve.version !== "current",
        expires_at: ve.entry.expires_at || null,
      },
      nodes: parsed.nodes,
    };
  }

  function currentIndex(P, name) {
    var f = P.files[name];
    if (!f) return null;
    var text = fileText(f.current);
    var parsed = compactDoc(text, name);
    var N = parsed.nodes;
    var byId = {};
    var byLoc = {};
    for (var i = 0; i < N.p.length; i++) {
      (byId[N.id[i]] = byId[N.id[i]] || []).push(i);
      byLoc[N.loc[i]] = i;
    }
    return { N: N, byId: byId, byLoc: byLoc, sha: parse.hash(text), format: parsed.format, text: text };
  }

  function valuePath(N, i) {
    var parts = [];
    while (i > 0) {
      parts.unshift(N.l[i]);
      i = N.p[i];
    }
    return parts.join("/");
  }

  function displayPath(N, i) {
    var parts = [];
    while (i > 0) {
      parts.unshift(N.l[i]);
      i = N.p[i];
    }
    return parts.join(" › ");
  }

  /* ---------------- diff ---------------- */
  function valueMap(text, name) {
    var N = compactDoc(text, name).nodes;
    var map = {};
    for (var i = 1; i < N.p.length; i++) {
      if (N.v[i] === null) continue;
      map[N.loc[i]] = { v: N.v[i], path: displayPath(N, i), vpath: valuePath(N, i) };
    }
    return map;
  }

  function boundGroups(P) {
    var out = {};
    P.bindings.forEach(function (b) {
      (b.variables || []).forEach(function (v) {
        var norm = String(v.path || "").split("/").slice(1).join("/");
        out[norm] = b.group_name;
      });
    });
    return out;
  }

  function structural(P, a, an, b, bn) {
    var ma, mb;
    try {
      ma = valueMap(a, an);
      mb = valueMap(b, bn);
    } catch (e) {
      return { type: "unsupported", summary: { added: 0, removed: 0, modified: 0, bound: 0 }, items: [] };
    }
    var bound = boundGroups(P);
    var keys = {};
    Object.keys(ma).forEach(function (k) { keys[k] = 1; });
    Object.keys(mb).forEach(function (k) { keys[k] = 1; });
    var items = [];
    var sum = { added: 0, removed: 0, modified: 0, bound: 0 };
    Object.keys(keys).sort().forEach(function (k) {
      var o = ma[k];
      var n = mb[k];
      var op = !o ? "added" : !n ? "removed" : o.v !== n.v ? "modified" : null;
      if (!op) return;
      var ref = n || o;
      var group = bound[ref.vpath] || null;
      sum[op]++;
      if (group) sum.bound++;
      items.push({ op: op, loc: k, path: ref.path, old: o ? o.v : null, new: n ? n.v : null, bound_group: group });
    });
    return { type: "structural", summary: sum, items: items };
  }

  function unified(a, b, an, bn, limit) {
    var A = a.split("\n");
    var B = b.split("\n");
    if (A.length * B.length > 4e6) return { lines: [], total: 0, truncated: true, too_large: true };
    var n = A.length;
    var m = B.length;
    var dp = [];
    for (var i = 0; i <= n; i++) dp.push(new Int32Array(m + 1));
    for (i = n - 1; i >= 0; i--) for (var j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    var ops = [];
    i = 0;
    j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && A[i] === B[j]) { ops.push([" ", A[i], i, j]); i++; j++; }
      else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { ops.push(["+", B[j], i, j]); j++; }
      else { ops.push(["-", A[i], i, j]); i++; }
    }
    var lines = ["--- " + an, "+++ " + bn];
    var k = 0;
    while (k < ops.length) {
      if (ops[k][0] === " ") { k++; continue; }
      var s = Math.max(0, k - 3);
      var e = k;
      while (e < ops.length) {
        if (ops[e][0] !== " ") { e++; continue; }
        var run = e;
        while (run < ops.length && ops[run][0] === " ") run++;
        if (run - e > 6 || run === ops.length) { e = Math.min(ops.length, e + 3); break; }
        e = run;
      }
      var aStart = ops[s][2] + 1;
      var bStart = ops[s][3] + 1;
      var aLen = 0;
      var bLen = 0;
      for (var q = s; q < e; q++) {
        if (ops[q][0] !== "+") aLen++;
        if (ops[q][0] !== "-") bLen++;
      }
      lines.push("@@ -" + aStart + "," + aLen + " +" + bStart + "," + bLen + " @@");
      for (q = s; q < e; q++) lines.push(ops[q][0] + ops[q][1]);
      k = e;
    }
    if (lines.length === 2) lines = [];
    var total = lines.length;
    limit = limit || 2000;
    return { lines: lines.slice(0, limit), total: total, truncated: total > limit };
  }

  function diffSummary(P, a, an, b, bn) {
    var st = structural(P, a, an, b, bn);
    if (st.type !== "structural") return null;
    return { has_changes: a !== b, summary: st.summary, structural: true };
  }

  /* ---------------- favorites ---------------- */
  function snapshotChildren(N, i) {
    var out = [];
    for (var c = i + 1; c < N.p.length && c <= i + subtreeEnd(N, i); c++) {
      if (N.p[c] !== i) continue;
      out.push({ label: N.l[c], value: N.v[c], path: N.id[c], children: snapshotChildren(N, c) });
    }
    return out;
  }
  function subtreeEnd(N, i) {
    var j = i + 1;
    while (j < N.p.length) {
      var a = N.p[j];
      var inside = false;
      while (a >= 0) {
        if (a === i) { inside = true; break; }
        a = N.p[a];
      }
      if (!inside) break;
      j++;
    }
    return j - i - 1;
  }
  function coveredPaths(fav) {
    var out = {};
    (function walk(list) {
      (list || []).forEach(function (c) {
        out[c.path] = 1;
        walk(c.children);
      });
    })(fav.children);
    return out;
  }
  /* §7.7：实时值按旧 ID 匹配，同名兄弟共享旧 ID 时取先序**最后一个**
   * （与 v1 favorites_live 的“后写覆盖”一致）。 */
  function resolveLive(ix, item) {
    var ids = ix.byId[item.path];
    var live = clone(item);
    live.missing = !ids;
    live.saved_value = item.value;
    if (ids) live.value = ix.N.v[ids[ids.length - 1]];
    (function walk(list) {
      (list || []).forEach(function (c) {
        var cid = ix.byId[c.path];
        c.missing = !cid;
        if (cid) c.value = ix.N.v[cid[cid.length - 1]];
        walk(c.children);
      });
    })(live.children);
    return live;
  }

  /* ---------------- review ---------------- */
  function locateRemark(ix, r) {
    if (r.locator && ix.byLoc[r.locator] !== undefined) return { i: ix.byLoc[r.locator], legacy: false };
    var ids = ix.byId[r.node_path];
    if (ids && ids.length === 1) return { i: ids[0], legacy: !r.locator };
    return { i: -1, legacy: !r.locator, ambiguous: !!(ids && ids.length > 1) };
  }

  function applyEdits(text, format, edits) {
    if (format === "json") {
      var data = JSON.parse(text);
      edits.forEach(function (e) {
        var segs = e.loc.split("/").slice(1).map(function (s) { return s.replace(/~1/g, "/").replace(/~0/g, "~"); });
        var obj = data;
        for (var i = 0; i < segs.length - 1; i++) obj = obj[segs[i]];
        var last = segs[segs.length - 1];
        var old = obj[last];
        if (typeof old === "number") {
          var num = Number(e.value);
          if (!isFinite(num)) throw new Error("类型不匹配：" + e.label + " 需要数值");
          obj[last] = num;
        } else if (typeof old === "boolean") {
          if (e.value !== "true" && e.value !== "false") throw new Error("类型不匹配：" + e.label + " 需要 true/false");
          obj[last] = e.value === "true";
        } else obj[last] = e.value;
      });
      return JSON.stringify(data, null, 2);
    }
    var dom = new DOMParser().parseFromString(text, "application/xml");
    edits.forEach(function (e) {
      var steps = e.loc.split("/").slice(1);
      var el = null;
      var attr = null;
      steps.forEach(function (st, k) {
        if (st.charAt(0) === "@") { attr = st.slice(1); return; }
        var m = /^(.+)\[(\d+)\]$/.exec(st);
        var parent = k === 0 ? dom : el;
        var count = 0;
        var found = null;
        for (var c = parent.firstChild; c; c = c.nextSibling) {
          if (c.nodeType === 1 && c.nodeName === m[1] && ++count === +m[2]) { found = c; break; }
        }
        el = found;
      });
      if (!el) throw new Error("定位失败：" + e.loc);
      if (attr) el.setAttribute(attr, e.value);
      else el.textContent = e.value;
    });
    var out = new XMLSerializer().serializeToString(dom);
    return /^<\?xml/.test(text) && !/^<\?xml/.test(out) ? '<?xml version="1.0" encoding="UTF-8"?>\n' + out : out;
  }

  /* ---------------- tasks ---------------- */
  function runTask(pid, type, name, actor) {
    var P = profile(pid);
    var key = pid + ":" + type + ":" + (name || "*");
    var running = Object.keys(tasks).map(function (k) { return tasks[k]; }).filter(function (t) {
      return t.key === key && (t.state === "queued" || t.state === "running");
    })[0];
    if (running) return fail(409, "BUSY", "该任务正在执行（由 " + running.actor + " 发起）", { task_id: running.id });
    var targets = type === "update_all" ? P.mapping.map(function (m) { return m.name; }) : [name];
    var t = {
      id: util.uid("task"),
      key: key,
      profile: pid,
      type: type,
      target: name || null,
      state: "running",
      progress: { done: 0, total: targets.length },
      result: { items: [] },
      error: null,
      actor: actor,
      created_at: util.nowString(),
      finished_at: null,
    };
    tasks[t.id] = t;
    bump(pid, "tasks");
    var step = 0;
    (function next() {
      setTimeout(function () {
        var tgt = targets[step];
        var res = type === "refresh_records" ? doRefreshRecords(P, tgt) : doUpdate(P, pid, tgt);
        t.result.items.push(res);
        step++;
        t.progress.done = step;
        bump(pid, "tasks");
        if (step < targets.length) return next();
        var failed = t.result.items.filter(function (x) { return x.status === "download_failed" || x.status === "error" || x.status === "rejected"; }).length;
        t.state = failed === 0 ? "succeeded" : failed === targets.length ? "failed" : "partial";
        t.finished_at = util.nowString();
        if (type === "update_all") {
          P.schedule.last_run = {
            at: t.finished_at,
            success: t.result.items.filter(function (x) { return x.status === "success"; }).length,
            unchanged: t.result.items.filter(function (x) { return x.status === "unchanged"; }).length,
            failed: failed,
            skipped: 0,
          };
        }
        bump(pid, "tasks");
        bump(pid, "files");
        bump(pid, "settings");
      }, 900);
    })();
    return ok({ task: t }, 202);
  }

  function doUpdate(P, pid, name) {
    var at = util.nowString();
    var res;
    if (name === "device_profile.json") res = { name: name, status: "download_failed", label: "下载失败", reason: "连接超时（30 秒）" };
    else if (name === "scenario_b.xml") res = { name: name, status: "unchanged", label: "无变化", reason: "内容与当前版本一致，未归档" };
    else {
      res = { name: name, status: "unchanged", label: "无变化", reason: "内容与当前版本一致，未归档" };
      if (P.pendingRemote && P.pendingRemote[name]) {
        saveVersion(P, name, P.pendingRemote[name]);
        delete P.pendingRemote[name];
        res = { name: name, status: "success", label: "已更新", reason: "已归档旧版本" };
      }
    }
    res.at = at;
    P.sourceStatus[name] = res;
    return res;
  }

  function doRefreshRecords(P, name) {
    var m = P.mapping.filter(function (x) { return x.name === name; })[0];
    if (!m || !m.record_url) return { name: name, status: "skipped", label: "已跳过", reason: "未配置修改记录链接" };
    var f = P.files[name];
    var last = f.records[0];
    var s = stamp();
    f.records.unshift({ filename: "record_" + s.day + "_" + s.sec + ".xml", text: last ? last.text : "<changes/>", mtime: util.nowString() });
    return { name: name, status: "success", label: "已拉取", reason: "新增 1 条修改记录" };
  }

  function saveVersion(P, name, text) {
    var f = P.files[name];
    if (!f) {
      P.files[name] = { current: { text: text, mtime: util.nowString() }, archives: [], records: [] };
      return null;
    }
    if (fileText(f.current) === text) return null;
    var s = stamp();
    var an = addSuffix(name, s.day);
    if (f.archives.some(function (a) { return a.filename === an; })) an = addSuffix(name, s.day + "_" + s.sec);
    var k = 2;
    var base = an;
    while (f.archives.some(function (a) { return a.filename === an; })) an = base.replace(/(\.[^.]+)?$/, "_" + k++ + "$1");
    f.archives.unshift({ filename: an, text: f.current.text, mtime: util.nowString() });
    f.current = { text: text, mtime: util.nowString() };
    return an;
  }

  /* ---------------- routes ---------------- */
  var routes = [];
  function route(method, pattern, handler) {
    var keys = [];
    var re = new RegExp("^" + pattern.replace(/\{(\w+)\}/g, function (_, k) { keys.push(k); return "([^/]+)"; }) + "$");
    routes.push({ method: method, re: re, keys: keys, handler: handler });
  }

  function needDeployer(id) {
    return id.is_deployer ? null : fail(403, "FORBIDDEN", "仅部署者（服务器本机）可执行该操作");
  }
  function needAdmin(id) {
    return id.is_admin ? null : fail(403, "FORBIDDEN", "仅管理员可执行该操作");
  }

  route("GET", "/session", function (a, req) {
    var pid = profile(req.query.profile) ? req.query.profile : "default";
    return ok({
      identity: identity(pid),
      profile: S.models.filter(function (m) { return m.id === pid; })[0],
      profiles: clone(S.models),
      server: { version: "2.0.0-template", time: util.nowString(), scheduler: true, mock: true },
      limits: { upload_max_bytes: 50 * 1024 * 1024, tab_limit: 15, temp_ttl_hours: 24 },
    });
  });

  route("POST", "/profiles", function (a, req) {
    var id = identity("default");
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(req.body.name || "").trim();
    if (!name) return fail(400, "INVALID_INPUT", "请输入机型名称", { field: "name" });
    if (S.models.some(function (m) { return m.name === name; })) return fail(409, "INVALID_INPUT", "已存在同名机型", { field: "name" });
    var slug = name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9_-]/g, "") || "model";
    var mid = slug;
    var k = 2;
    while (S.profiles[mid] || mid === "default") mid = slug + "-" + k++;
    var src = req.body.copy_from ? profile(req.body.copy_from) : null;
    var copiedFiles = {};
    if (src) Object.keys(src.files).forEach(function (n) { copiedFiles[n] = { current: { text: fileText(src.files[n].current), mtime: src.files[n].current.mtime }, archives: clone(src.files[n].archives), records: [] }; });
    S.profiles[mid] = src
      ? Object.assign(clone(Object.assign({}, src, { files: {} })), { files: copiedFiles, remarks: [], admins: [], people: [], sourceStatus: {} })
      : { files: {}, mapping: [], sourceStatus: {}, favorites: [], remarks: [], bindings: [], tools: [], schedule: { enabled: false, interval_hours: 24, last_run: null }, admins: [], people: [], dl: null };
    rev[mid] = { files: 1, favorites: 1, remarks: 1, settings: 1, tasks: 1 };
    S.models.push({ id: mid, name: name });
    return ok({ profile: { id: mid, name: name } }, 201);
  });

  route("PATCH", "/profiles/{pid}", function (a, req) {
    var deny = needDeployer(identity(a.pid));
    if (deny) return deny;
    var m = S.models.filter(function (x) { return x.id === a.pid; })[0];
    if (!m) return fail(404, "NOT_FOUND", "机型不存在");
    var name = String(req.body.name || "").trim();
    if (!name) return fail(400, "INVALID_INPUT", "请输入机型名称", { field: "name" });
    m.name = name;
    return ok({ profile: m });
  });

  route("DELETE", "/profiles/{pid}", function (a) {
    var deny = needDeployer(identity(a.pid));
    if (deny) return deny;
    if (a.pid === "default") return fail(400, "INVALID_INPUT", "默认机型不能删除");
    S.models = S.models.filter(function (x) { return x.id !== a.pid; });
    delete S.profiles[a.pid];
    return ok({ deleted: true });
  });

  route("GET", "/p/{pid}/revision", function (a) {
    var r = rev[a.pid];
    return ok({ rev: [r.files, r.favorites, r.remarks, r.settings, r.tasks].join("."), parts: clone(r) });
  });

  route("GET", "/p/{pid}/files", function (a, req, P) {
    var items = Object.keys(P.files).sort().map(function (name) {
      var f = P.files[name];
      return {
        name: name,
        format: util.fileFormat(name),
        size: bytes(fileText(f.current)),
        updated_at: f.current.mtime,
        mapped: P.mapping.some(function (m) { return m.name === name; }),
        archive_count: f.archives.length,
        record_count: f.records.length,
        pending_remarks: P.remarks.filter(function (r) { return r.source_file === name && r.status === "pending"; }).length,
      };
    });
    return ok({ items: items });
  });

  route("GET", "/p/{pid}/files/{name}/document", function (a, req, P) {
    var ve = versionEntry(P, a.name, req.query.version);
    if (!ve) return fail(404, "NOT_FOUND", "文件或版本不存在");
    try {
      return ok(documentPayload(a.pid, a.name, ve));
    } catch (e) {
      return fail(422, "PARSE_FAILED", "该版本无法解析为 XML/JSON，可下载原文查看", { raw_available: true });
    }
  });

  route("GET", "/p/{pid}/temp/{token}/document", function (a) {
    var t = temps[a.token];
    if (!t || t.profile !== a.pid) return fail(404, "NOT_FOUND", "临时文件不存在或已过期");
    var payload = documentPayload(a.pid, t.name, { entry: t, label: "临时文件（仅自己可见）", version: "temp", display: t.name }, "temp");
    payload.file.token = a.token;
    return ok(payload);
  });

  route("PUT", "/p/{pid}/files/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    return req.rawText.then(function (text) {
      try { parse.parse(text, a.name); } catch (e) { return fail(422, "PARSE_FAILED", "文件无法解析：" + e.message); }
      var exists = !!P.files[a.name];
      if (exists && req.query.overwrite !== "1") return fail(409, "FILE_EXISTS", "已存在同名文件 " + a.name, { name: a.name });
      var archived = saveVersion(P, a.name, text);
      bump(a.pid, "files");
      return ok({ file: { name: a.name }, archived: archived, unchanged: exists && !archived }, exists ? 200 : 201);
    });
  });

  route("DELETE", "/p/{pid}/files/{name}", function (a, req, P, id) {
    var deny = needAdmin(id);
    if (deny) return deny;
    var f = P.files[a.name];
    if (!f) return fail(404, "NOT_FOUND", "文件不存在");
    var removed = {
      archives: f.archives.length,
      records: f.records.length,
      remarks: P.remarks.filter(function (r) { return r.source_file === a.name; }).length,
      favorites: P.favorites.filter(function (x) { return x.source_file === a.name; }).length,
    };
    delete P.files[a.name];
    P.mapping = P.mapping.filter(function (m) { return m.name !== a.name; });
    P.remarks = P.remarks.filter(function (r) { return r.source_file !== a.name; });
    P.favorites = P.favorites.filter(function (x) { return x.source_file !== a.name; });
    ["files", "favorites", "remarks", "settings"].forEach(function (k) { bump(a.pid, k); });
    return ok({ deleted: true, removed: removed, recovery: "已移入 data/recovery/，可由部署者恢复" });
  });

  route("POST", "/p/{pid}/temp", function (a, req, P) {
    var name = decodeURIComponent(req.headers["X-Filename"] || "upload");
    return req.rawText.then(function (text) {
      try { parse.parse(text, name); } catch (e) { return fail(422, "PARSE_FAILED", "文件无法解析：" + e.message); }
      var token = util.uid("tmp");
      var exp = new Date(Date.now() + 24 * 3600 * 1000);
      temps[token] = { profile: a.pid, name: name, text: text, mtime: util.nowString(), expires_at: exp.toISOString().slice(0, 16).replace("T", " ") };
      return ok({ token: token, name: name, format: util.fileFormat(name), size: bytes(text), expires_at: temps[token].expires_at }, 201);
    });
  });

  route("POST", "/p/{pid}/import-url", function (a, req, P, id) {
    var url = String(req.body.url || "").trim();
    var name = String(req.body.name || "").trim() || url.split("/").pop() || "downloaded.xml";
    if (!/^https?:\/\//i.test(url)) return fail(400, "INVALID_INPUT", "仅支持 http/https 链接", { field: "url" });
    if (/fail|timeout/.test(url)) return fail(502, "DOWNLOAD_FAILED", "下载失败：连接超时（30 秒），已重试 3 次");
    var text = S.profiles["default"].files["scenario_b.xml"].current.text;
    if (req.body.persist) {
      var deny = needDeployer(id);
      if (deny) return deny;
      if (P.files[name] && !req.body.overwrite) return fail(409, "FILE_EXISTS", "已存在同名文件 " + name, { name: name });
      var archived = saveVersion(P, name, text);
      bump(a.pid, "files");
      return ok({ persisted: true, file: { name: name }, archived: archived });
    }
    var token = util.uid("tmp");
    temps[token] = { profile: a.pid, name: name, text: text, mtime: util.nowString(), expires_at: "24 小时后" };
    return ok({ persisted: false, token: token, name: name });
  });

  route("GET", "/p/{pid}/files/{name}/archives", function (a, req, P) {
    var f = P.files[a.name];
    if (!f) return fail(404, "NOT_FOUND", "文件不存在");
    var cur = fileText(f.current);
    var items = f.archives.map(function (ar, k) {
      var newer = k === 0 ? { text: cur, name: a.name } : { text: f.archives[k - 1].text, name: f.archives[k - 1].filename };
      return { filename: ar.filename, date: ar.mtime, size: bytes(ar.text), compared_to: newer.name, diff: diffSummary(P, ar.text, ar.filename, newer.text, newer.name) };
    });
    return ok({ current: { name: a.name, updated_at: f.current.mtime, size: bytes(cur), sha256: parse.hash(cur) }, items: items });
  });

  route("GET", "/p/{pid}/files/{name}/records", function (a, req, P) {
    var f = P.files[a.name];
    if (!f) return fail(404, "NOT_FOUND", "文件不存在");
    var m = P.mapping.filter(function (x) { return x.name === a.name; })[0];
    var items = f.records.map(function (r, k) {
      var prev = f.records[k + 1];
      return { filename: r.filename, date: r.mtime, size: bytes(r.text), diff_prev: prev ? diffSummary(P, prev.text, prev.filename, r.text, r.filename) : null };
    });
    return ok({ record_url: m ? m.record_url : "", items: items });
  });

  function resolveSide(P, side) {
    if (side.temp) {
      var t = temps[side.temp];
      return t ? { text: t.text, name: t.name, label: "上传文件 " + t.name } : null;
    }
    var ve = versionEntry(P, side.file, side.version);
    return ve ? { text: fileText(ve.entry), name: ve.display, label: ve.label } : null;
  }

  route("POST", "/p/{pid}/compare", function (a, req, P) {
    var base = resolveSide(P, req.body.base || {});
    var target = resolveSide(P, req.body.target || {});
    if (!base || !target) return fail(404, "NOT_FOUND", "对比的文件版本不存在或已过期");
    var st = structural(P, base.text, base.name, target.text, target.name);
    return ok({
      has_changes: base.text !== target.text,
      base: { label: base.label, name: base.name },
      target: { label: target.label, name: target.name },
      structural: st,
      unified: unified(base.text, target.text, base.name, target.name, req.body.text_limit),
    });
  });

  route("GET", "/p/{pid}/search", function (a, req, P) {
    var q = String(req.query.q || "").trim().toLowerCase();
    if (!q) return fail(400, "INVALID_INPUT", "请输入搜索关键词", { field: "q" });
    var limit = Math.max(1, Math.min(+req.query.limit || 200, 1000));
    var offset = Math.max(0, +req.query.offset || 0);
    if (req.query.file && !P.files[req.query.file]) return fail(404, "NOT_FOUND", "文件不存在");
    var notes = {};
    P.favorites.forEach(function (f) { if (f.note) notes[f.source_file + "|" + f.path] = f.note; });
    var hits = [];
    var fileHit = {};
    Object.keys(P.files).sort().forEach(function (name) {
      if (req.query.file && req.query.file !== name) return;
      var ix = currentIndex(P, name);
      var N = ix.N;
      for (var i = 1; i < N.p.length; i++) {
        var note = notes[name + "|" + N.id[i]] || "";
        var field = String(N.l[i]).toLowerCase().indexOf(q) >= 0 ? "label" : N.v[i] !== null && String(N.v[i]).toLowerCase().indexOf(q) >= 0 ? "value" : note && note.toLowerCase().indexOf(q) >= 0 ? "note" : null;
        if (!field) continue;
        fileHit[name] = (fileHit[name] || 0) + 1;
        hits.push({ file: name, id: N.id[i], loc: N.loc[i], path: displayPath(N, i), label: N.l[i], value: N.v[i], type: N.t[i], note: note, match: field });
      }
    });
    return ok({ q: req.query.q, total: hits.length, files: fileHit, items: hits.slice(offset, offset + limit), truncated: hits.length > offset + limit });
  });

  route("GET", "/p/{pid}/favorites", function (a, req, P) {
    var groups = {};
    P.favorites.forEach(function (f) { (groups[f.source_file] = groups[f.source_file] || []).push(f); });
    var out = Object.keys(groups).map(function (file) {
      var exists = !!P.files[file];
      var ix = exists ? currentIndex(P, file) : null;
      return {
        file: file,
        exists: exists,
        updated_at: exists ? P.files[file].current.mtime : null,
        items: groups[file].map(function (it) {
          if (!ix) return Object.assign(clone(it), { missing: true, saved_value: it.value });
          return resolveLive(ix, it);
        }),
      };
    });
    out.sort(function (x, y) { return (y.exists ? 1 : 0) - (x.exists ? 1 : 0) || x.file.localeCompare(y.file); });
    return ok({ groups: out });
  });

  route("POST", "/p/{pid}/favorites", function (a, req, P) {
    var ix = currentIndex(P, req.body.file);
    if (!ix) return fail(404, "NOT_FOUND", "文件不存在");
    var ids = ix.byId[req.body.id];
    if (!ids) return fail(404, "NOT_FOUND", "节点不存在");
    var dup = P.favorites.filter(function (f) { return f.source_file === req.body.file && f.path === req.body.id; })[0];
    if (dup) return ok({ item: dup });
    var parent = P.favorites.filter(function (f) { return f.source_file === req.body.file && coveredPaths(f)[req.body.id]; })[0];
    if (parent) return fail(409, "FAVORITE_COVERED", "该节点已被父级收藏「" + parent.label + "」包含", { parent: parent.path });
    var i = ids[ids.length - 1];
    var N = ix.N;
    var children = N.v[i] === null ? snapshotChildren(N, i) : [];
    if (N.v[i] === null && !children.length) return fail(400, "INVALID_INPUT", "该节点下没有可收藏的变量");
    var item = { path: N.id[i], label: N.l[i], value: N.v[i], source_file: req.body.file, note: "", children: children, added_at: util.nowString() };
    P.favorites.push(item);
    bump(a.pid, "favorites");
    return ok({ item: item }, 201);
  });

  route("PATCH", "/p/{pid}/favorites", function (a, req, P) {
    var f = P.favorites.filter(function (x) { return x.source_file === req.body.file && x.path === req.body.path; })[0];
    if (!f) return fail(404, "NOT_FOUND", "收藏不存在");
    f.note = String(req.body.note || "");
    bump(a.pid, "favorites");
    return ok({ item: f });
  });

  route("DELETE", "/p/{pid}/favorites", function (a, req, P) {
    var before = P.favorites.length;
    P.favorites = P.favorites.filter(function (x) {
      if (x.source_file !== req.query.file) return true;
      return req.query.path ? x.path !== req.query.path : false;
    });
    bump(a.pid, "favorites");
    return ok({ removed: before - P.favorites.length });
  });

  route("GET", "/p/{pid}/remarks", function (a, req, P) {
    var items = P.remarks.filter(function (r) {
      return (!req.query.file || r.source_file === req.query.file) && (!req.query.status || r.status === req.query.status);
    });
    return ok({ items: clone(items) });
  });

  /* §6.7 第 4 步：JSON 节点按原类型校验建议值，XML 不做类型检查。与 services/review.py 保持一致。 */
  function requiredType(fmt, t, proposed) {
    if (fmt !== "json") return "";
    if (t === "int") return /^-?\d+$/.test(proposed) ? "" : "整数";
    if (t === "float") return proposed !== "" && isFinite(Number(proposed)) ? "" : "数字";
    if (t === "bool") return proposed === "true" || proposed === "false" ? "" : "true 或 false";
    if (t === "NoneType") return proposed === "null" ? "" : "null";
    return "";
  }

  route("POST", "/p/{pid}/remarks", function (a, req, P, id) {
    var b = req.body;
    var proposed = String(b.proposed_value || "").trim();
    if (!proposed) return fail(400, "INVALID_INPUT", "请输入建议值", { field: "proposed_value" });
    var ix = currentIndex(P, b.file);
    if (!ix) return fail(404, "NOT_FOUND", "文件不存在");
    if (b.sha256 && b.sha256 !== ix.sha) return fail(409, "SOURCE_CHANGED", "文件已更新，请刷新后基于最新版本提交建议");
    var i = ix.byLoc[b.loc];
    if (i === undefined || ix.N.v[i] === null) return fail(400, "INVALID_INPUT", "只能对有值的节点提交建议", { field: "loc" });
    if (proposed === ix.N.v[i]) return fail(400, "INVALID_INPUT", "建议值与当前值相同", { field: "proposed_value" });
    var need = requiredType(ix.format, ix.N.t[i], proposed);
    if (need) return fail(400, "INVALID_INPUT", "该节点需要" + need + "类型的值", { field: "proposed_value" });
    var r = {
      id: util.uid("rm"),
      source_file: b.file,
      node_key: "",
      node_path: ix.N.id[i],
      node_label: ix.N.l[i],
      original_value: ix.N.v[i],
      proposed_value: proposed,
      node_type: ix.N.t[i],
      tree_type: ix.format,
      actor_ip: id.ip,
      actor_name: id.registered ? id.name : "",
      actor_display: id.name,
      status: "pending",
      reviewed_by: "",
      reviewer_ip: "",
      reviewed_at: "",
      generated_file: "",
      created_at: util.nowString(),
      locator: b.loc,
      source_sha256: ix.sha,
      schema_version: 2,
    };
    P.remarks.push(r);
    bump(a.pid, "remarks");
    return ok({ item: r }, 201);
  });

  route("GET", "/p/{pid}/review", function (a, req, P, id) {
    var deny = needAdmin(id);
    if (deny) return deny;
    var byFile = {};
    P.remarks.filter(function (r) { return r.status === "pending"; }).forEach(function (r) {
      var f = (byFile[r.source_file] = byFile[r.source_file] || { file: r.source_file, nodes: {} });
      var key = r.locator || r.node_path || r.node_key;
      var g = (f.nodes[key] = f.nodes[key] || { group_key: key, loc: r.locator || null, id: r.node_path, label: r.node_label, node_type: r.node_type, original_value: r.original_value, remarks: [] });
      g.remarks.push({ id: r.id, actor_display: r.actor_display, actor_ip: r.actor_ip, proposed_value: r.proposed_value, created_at: r.created_at });
    });
    var files = Object.keys(byFile).sort().map(function (name) {
      var ix = currentIndex(P, name);
      var nodes = Object.keys(byFile[name].nodes).map(function (k) {
        var g = byFile[name].nodes[k];
        var loc = ix ? locateRemark(ix, { locator: g.loc, node_path: g.id }) : { i: -1, legacy: true };
        g.legacy = loc.legacy;
        g.resolvable = loc.i >= 0;
        g.current_value = loc.i >= 0 ? ix.N.v[loc.i] : null;
        g.path = loc.i >= 0 ? displayPath(ix.N, loc.i) : g.id;
        g.loc = loc.i >= 0 ? ix.N.loc[loc.i] : g.loc;
        g.stale = g.resolvable && g.current_value !== g.original_value;
        return g;
      });
      return { file: name, exists: !!ix, current_sha: ix ? ix.sha : null, nodes: nodes };
    });
    return ok({ files: files });
  });

  route("POST", "/p/{pid}/review/submit", function (a, req, P, id) {
    var deny = needAdmin(id);
    if (deny) return deny;
    var b = req.body;
    if (b.request_id && idempotency[b.request_id]) return ok(idempotency[b.request_id]);
    var ix = currentIndex(P, b.file);
    if (!ix) return fail(404, "NOT_FOUND", "源文件不存在");
    if (ix.sha !== b.source_sha) return fail(409, "REVIEW_CONFLICT", "源文件在你审阅期间已被更新，请刷新后重新核对");
    var approved = [];
    var rejected = [];
    var edits = [];
    var pending = P.remarks.filter(function (r) { return r.status === "pending" && r.source_file === b.file; });
    (b.decisions || []).forEach(function (d) {
      var group = pending.filter(function (r) { return (r.locator || r.node_path || r.node_key) === d.group_key; });
      if (!group.length) return;
      if (d.choice === "reject") {
        group.forEach(function (r) { rejected.push(r); });
        return;
      }
      var chosen = group.filter(function (r) { return r.id === d.choice; })[0];
      if (!chosen) return;
      var loc = locateRemark(ix, chosen);
      if (loc.i < 0 || ix.N.v[loc.i] !== chosen.original_value) throw { conflict: chosen.node_label };
      edits.push({ loc: ix.N.loc[loc.i], value: chosen.proposed_value, label: chosen.node_label });
      approved.push(chosen);
      group.forEach(function (r) { if (r !== chosen) rejected.push(r); });
    });
    var generated = null;
    if (edits.length) {
      var out;
      try { out = applyEdits(ix.text, ix.format, edits); } catch (e) { return fail(422, "REVIEW_UNSAFE", e.message || "生成结果校验失败"); }
      var s = stamp();
      generated = addSuffix(b.file, s.day + "_" + s.sec);
      P.files[generated] = { current: { text: out, mtime: util.nowString() }, archives: [], records: [] };
    }
    var now = util.nowString();
    approved.forEach(function (r) { Object.assign(r, { status: "approved", reviewed_by: id.name, reviewer_ip: id.ip, reviewed_at: now, generated_file: generated }); });
    rejected.forEach(function (r) { Object.assign(r, { status: "rejected", reviewed_by: id.name, reviewer_ip: id.ip, reviewed_at: now, generated_file: "" }); });
    var result = { generated_file: generated, approved: approved.length, rejected: rejected.length };
    if (b.request_id) idempotency[b.request_id] = result;
    bump(a.pid, "remarks");
    bump(a.pid, "files");
    return ok(result);
  });

  route("GET", "/p/{pid}/sources", function (a, req, P) {
    return ok({
      items: P.mapping.map(function (m) {
        var f = P.files[m.name];
        return Object.assign(clone(m), { local_updated_at: f ? f.current.mtime : null, last_result: P.sourceStatus[m.name] || null });
      }),
    });
  });

  function validName(name) {
    return /^[^\\/:*?"<>|\u0000-\u001f]+$/.test(name) && name !== "." && name !== ".." && name.length <= 180;
  }

  route("POST", "/p/{pid}/sources", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(req.body.name || "").trim();
    var url = String(req.body.url || "").trim();
    if (!validName(name)) return fail(400, "INVALID_INPUT", "文件名不能为空且不能包含 \\ / : * ? \" < > |", { field: "name" });
    if (!/^https?:\/\//i.test(url)) return fail(400, "INVALID_INPUT", "下载链接需以 http:// 或 https:// 开头", { field: "url" });
    if (P.mapping.some(function (m) { return m.name === name; })) return fail(409, "INVALID_INPUT", "已存在同名更新源", { field: "name" });
    P.mapping.push({ name: name, url: url, record_url: String(req.body.record_url || "").trim() });
    bump(a.pid, "settings");
    return ok({ item: P.mapping[P.mapping.length - 1] }, 201);
  });

  route("PATCH", "/p/{pid}/sources/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var m = P.mapping.filter(function (x) { return x.name === a.name; })[0];
    if (!m) return fail(404, "NOT_FOUND", "更新源不存在");
    var name = String(req.body.name || "").trim();
    var url = String(req.body.url || "").trim();
    var rec = String(req.body.record_url || "").trim();
    if (!validName(name)) return fail(400, "INVALID_INPUT", "文件名不合法", { field: "name" });
    if (!/^https?:\/\//i.test(url)) return fail(400, "INVALID_INPUT", "下载链接需以 http:// 或 https:// 开头", { field: "url" });
    if (rec && !/^https?:\/\//i.test(rec)) return fail(400, "INVALID_INPUT", "修改记录链接需以 http:// 或 https:// 开头", { field: "record_url" });
    if (name !== a.name && P.mapping.some(function (x) { return x.name === name; })) return fail(409, "INVALID_INPUT", "目标文件名已存在", { field: "name" });
    if (name !== a.name && req.body.migrate_files && P.files[a.name]) {
      P.files[name] = P.files[a.name];
      delete P.files[a.name];
      P.favorites.forEach(function (f) { if (f.source_file === a.name) f.source_file = name; });
      P.remarks.forEach(function (r) { if (r.source_file === a.name) r.source_file = name; });
      bump(a.pid, "files");
    }
    m.name = name;
    m.url = url;
    m.record_url = rec;
    bump(a.pid, "settings");
    return ok({ item: m });
  });

  route("DELETE", "/p/{pid}/sources/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    P.mapping = P.mapping.filter(function (m) { return m.name !== a.name; });
    bump(a.pid, "settings");
    return ok({ deleted: true });
  });

  route("POST", "/p/{pid}/tasks", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var type = req.body.type;
    if (["update_source", "update_all", "refresh_records"].indexOf(type) < 0) return fail(400, "INVALID_INPUT", "未知任务类型");
    if (type !== "update_all" && !P.mapping.some(function (m) { return m.name === req.body.name; })) return fail(404, "NOT_FOUND", "该文件没有配置更新源");
    return runTask(a.pid, type, req.body.name, id.name);
  });

  route("GET", "/p/{pid}/tasks", function (a, req) {
    var items = Object.keys(tasks).map(function (k) { return tasks[k]; }).filter(function (t) {
      return t.profile === a.pid && (req.query.active !== "1" || t.state === "running" || t.state === "queued");
    });
    return ok({ items: clone(items) });
  });

  route("GET", "/tasks/{id}", function (a) {
    var t = tasks[a.id];
    return t ? ok({ task: clone(t) }) : fail(404, "NOT_FOUND", "任务不存在");
  });

  route("GET", "/p/{pid}/schedule", function (a, req, P) {
    var s = clone(P.schedule);
    if (s.enabled) {
      var base = s.last_run ? util.parseTime(s.last_run.at) : new Date();
      var next = new Date(base.getTime() + s.interval_hours * 3600 * 1000);
      s.next_run_at = next.getFullYear() + "-" + ("0" + (next.getMonth() + 1)).slice(-2) + "-" + ("0" + next.getDate()).slice(-2) + " " + ("0" + next.getHours()).slice(-2) + ":" + ("0" + next.getMinutes()).slice(-2) + ":00";
    }
    return ok(s);
  });

  route("PUT", "/p/{pid}/schedule", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var hrs = Number(req.body.interval_hours);
    if (!isFinite(hrs) || hrs < 1 || hrs > 24 * 90) return fail(400, "INVALID_INPUT", "间隔需在 1 小时到 90 天之间", { field: "interval" });
    P.schedule.enabled = !!req.body.enabled;
    P.schedule.interval_hours = hrs;
    bump(a.pid, "settings");
    return ok(clone(P.schedule));
  });

  route("GET", "/p/{pid}/ip-mapping", function (a, req, P) {
    return ok({ items: clone(P.people) });
  });

  route("PUT", "/p/{pid}/ip-mapping/{ip}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var ip = String(req.body.ip || a.ip).trim();
    var name = String(req.body.name || "").trim();
    if (!/^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-f:]+$/i.test(ip)) return fail(400, "INVALID_INPUT", "IP 地址格式不正确", { field: "ip" });
    if (!name) return fail(400, "INVALID_INPUT", "请输入人员姓名", { field: "name" });
    if (ip !== a.ip) P.people = P.people.filter(function (x) { return x.ip !== a.ip; });
    var cur = P.people.filter(function (x) { return x.ip === ip; })[0];
    if (cur) Object.assign(cur, { name: name, updated_at: util.nowString() });
    else P.people.push({ ip: ip, name: name, updated_at: util.nowString() });
    bump(a.pid, "settings");
    return ok({ ok: true });
  });

  route("DELETE", "/p/{pid}/ip-mapping/{ip}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    P.people = P.people.filter(function (x) { return x.ip !== a.ip; });
    bump(a.pid, "settings");
    return ok({ deleted: true });
  });

  route("GET", "/p/{pid}/admins", function (a, req, P) {
    return ok({ items: clone(P.admins) });
  });

  route("POST", "/p/{pid}/admins", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(req.body.name || "").trim();
    if (!name) return fail(400, "INVALID_INPUT", "请输入管理员姓名", { field: "name" });
    if (P.admins.some(function (x) { return x.name === name; })) return fail(409, "INVALID_INPUT", "该管理员已存在", { field: "name" });
    P.admins.push({ name: name, updated_at: util.nowString() });
    bump(a.pid, "settings");
    return ok({ ok: true }, 201);
  });

  route("DELETE", "/p/{pid}/admins/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    P.admins = P.admins.filter(function (x) { return x.name !== a.name; });
    bump(a.pid, "settings");
    return ok({ deleted: true });
  });

  route("GET", "/p/{pid}/bindings", function (a, req, P) {
    return ok({ items: clone(P.bindings) });
  });

  route("PUT", "/p/{pid}/bindings/{group}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(req.body.group_name || "").trim();
    var vars = (req.body.variables || []).filter(function (v) { return v.file && String(v.path || "").trim(); });
    if (!name) return fail(400, "INVALID_INPUT", "请输入绑定组名称", { field: "group_name" });
    if (vars.length < 2) return fail(400, "INVALID_INPUT", "绑定组至少需要 2 个变量", { field: "variables" });
    if (name !== a.group && P.bindings.some(function (b) { return b.group_name === name; })) return fail(409, "INVALID_INPUT", "已存在同名绑定组", { field: "group_name" });
    var cur = P.bindings.filter(function (b) { return b.group_name === a.group; })[0];
    if (cur) {
      cur.group_name = name;
      cur.variables = vars;
    } else P.bindings.push({ group_name: name, variables: vars });
    bump(a.pid, "settings");
    return ok({ ok: true });
  });

  route("DELETE", "/p/{pid}/bindings/{group}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    P.bindings = P.bindings.filter(function (b) { return b.group_name !== a.group; });
    bump(a.pid, "settings");
    return ok({ deleted: true });
  });

  route("GET", "/p/{pid}/tools", function (a, req, P) {
    return ok({ items: clone(P.tools) });
  });

  route("PUT", "/p/{pid}/tools/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(req.body.name || "").trim();
    var url = String(req.body.url || "").trim();
    if (!name) return fail(400, "INVALID_INPUT", "请输入工具名称", { field: "name" });
    if (!/^https?:\/\//i.test(url)) return fail(400, "INVALID_INPUT", "访问地址需以 http:// 或 https:// 开头", { field: "url" });
    if (name !== a.name && P.tools.some(function (t) { return t.name === name; })) return fail(409, "INVALID_INPUT", "已存在同名工具", { field: "name" });
    var cur = P.tools.filter(function (t) { return t.name === a.name; })[0];
    if (cur) Object.assign(cur, { name: name, description: String(req.body.description || ""), url: url });
    else P.tools.push({ name: name, description: String(req.body.description || ""), url: url, added_at: util.nowString() });
    bump(a.pid, "settings");
    return ok({ ok: true });
  });

  route("DELETE", "/p/{pid}/tools/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    P.tools = P.tools.filter(function (t) { return t.name !== a.name; });
    bump(a.pid, "settings");
    return ok({ deleted: true });
  });

  function dlConfig(P) {
    if (!P.dl) P.dl = clone(S.profiles["default"].dl);
    return P.dl;
  }

  route("GET", "/p/{pid}/dl", function (a, req, P) {
    var cfg = dlConfig(P);
    return ok({ rev: "r" + rev[a.pid].settings, items: clone(cfg.items) });
  });

  route("PUT", "/p/{pid}/dl/{item}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    if (req.headers["If-Match"] && req.headers["If-Match"] !== "r" + rev[a.pid].settings) {
      return fail(409, "REVISION_CONFLICT", "计算参数已被他人修改，请刷新后再保存");
    }
    var cfg = dlConfig(P);
    if (!cfg.items[a.item]) return fail(404, "NOT_FOUND", "计算项不存在");
    var err = dl.validate(req.body, a.item);
    if (err) return fail(400, "INVALID_INPUT", err);
    cfg.items[a.item] = Object.assign(cfg.items[a.item], clone(req.body));
    bump(a.pid, "settings");
    return ok({ rev: "r" + rev[a.pid].settings, item: clone(cfg.items[a.item]) });
  });

  route("POST", "/p/{pid}/dl/{item}/extract", function (a, req, P) {
    var bnd = req.body.binding || {};
    var ix = currentIndex(P, bnd.source_file);
    if (!ix) return fail(404, "NOT_FOUND", "绑定的配置文件不存在");
    var ids = ix.byId[bnd.variable_path];
    if (!ids) return fail(404, "NOT_FOUND", "在 " + bnd.source_file + " 中未找到 " + bnd.variable_path, { field: "variable_path" });
    var N = ix.N;
    var i = ids[0];
    var children = {};
    for (var c = i + 1; c < N.p.length; c++) if (N.p[c] === i) children[N.l[c]] = N.v[c];
    var fields = Object.keys(bnd.field_map || {}).map(function (fld) {
      var coeff = bnd.field_map[fld];
      if (!(fld in children)) return { field: fld, coeff: coeff, status: "missing" };
      var num = Number(children[fld]);
      return isFinite(num) ? { field: fld, coeff: coeff, status: "ok", value: num } : { field: fld, coeff: coeff, status: "invalid", raw: children[fld] };
    });
    var coeffs = {};
    fields.forEach(function (f) { if (f.status === "ok") coeffs[f.coeff] = f.value; });
    return ok({ coeffs: coeffs, fields: fields });
  });

  route("POST", "/p/{pid}/dl/{item}/compute", function (a, req) {
    var params = req.body.params;
    var err = dl.validate(params, a.item);
    if (err) return fail(400, "INVALID_INPUT", err);
    var vals = req.body.values || [];
    if (vals.some(function (v) { return !isFinite(v); })) return fail(400, "INVALID_INPUT", "输入包含非数值");
    if (req.body.mode === "forward") return ok({ mode: "forward", results: dl.forward(params, a.item, vals) });
    if (req.body.mode === "reverse") return ok({ mode: "reverse", results: dl.reverse(params, a.item, vals), exhaustive: false });
    return ok(dl.multiCheck(params, a.item));
  });

  /* ---------------- dispatcher ---------------- */
  function handle(req) {
    var path = req.path;
    var latency = /document$/.test(path) ? 260 : 140;
    return new Promise(function (resolve) {
      setTimeout(function () {
        var res;
        try {
          res = dispatch(req, path);
        } catch (e) {
          if (e && e.conflict) res = fail(409, "REVIEW_CONFLICT", "节点「" + e.conflict + "」的当前值已变化，请刷新后重新审阅");
          else {
            console.error(e);
            res = fail(500, "STORAGE_FAILURE", "服务器内部错误");
          }
        }
        Promise.resolve(res).then(resolve);
      }, latency + Math.random() * 120);
    });
  }

  function dispatch(req, path) {
    for (var r = 0; r < routes.length; r++) {
      var rt = routes[r];
      if (rt.method !== req.method) continue;
      var m = rt.re.exec(path);
      if (!m) continue;
      var args = {};
      rt.keys.forEach(function (k, i) { args[k] = decodeURIComponent(m[i + 1]); });
      var P = null;
      if (args.pid !== undefined && path.indexOf("/p/") === 0) {
        P = profile(args.pid);
        if (!P) return fail(404, "NOT_FOUND", "机型不存在：" + args.pid);
      }
      req.body = req.body || {};
      req.rawText = req.raw ? (req.raw.text ? req.raw.text() : Promise.resolve(String(req.raw))) : Promise.resolve("");
      return rt.handler(args, req, P, identity(args.pid && P ? args.pid : "default"));
    }
    return fail(404, "NOT_FOUND", "接口不存在：" + req.method + " " + path);
  }

  function download(req) {
    return new Promise(function (resolve, reject) {
      setTimeout(function () {
        var m = /^\/p\/([^/]+)\/files\/([^/]+)\/raw$/.exec(req.path);
        var t = /^\/p\/([^/]+)\/temp\/([^/]+)\/raw$/.exec(req.path);
        if (t && temps[decodeURIComponent(t[2])]) {
          var tmp = temps[decodeURIComponent(t[2])];
          return resolve({ blob: new Blob([tmp.text]), filename: tmp.name });
        }
        var P = m && profile(decodeURIComponent(m[1]));
        var ve = P && versionEntry(P, decodeURIComponent(m[2]), req.query.version);
        if (!ve) return reject(new MC.ApiError(404, "NOT_FOUND", "文件或版本不存在，无法下载"));
        resolve({ blob: new Blob([fileText(ve.entry)], { type: "application/octet-stream" }), filename: ve.display });
      }, 200);
    });
  }

  MC.mock = {
    handle: handle,
    download: download,
    identities: IDENTITIES,
    identityKey: function () { return identityKey; },
    setIdentity: function (key) {
      identityKey = key;
      util.storageSet("mc.demo.identity", key);
    },
    /** 模拟其他用户/定时任务更新了 scenario_a.xml，用于演示多用户“数据已更新”提示。 */
    simulateExternalChange: function (pid) {
      var P = profile(pid);
      var f = P && P.files["scenario_a.xml"];
      if (!f) return false;
      var text = fileText(f.current).replace(/>(\d\.\d\de\+02)<\/param_13>/, function (all, v) {
        return ">" + (Number(v) + 10).toExponential(2).replace("e+", "e+0") + "</param_13>";
      });
      saveVersion(P, "scenario_a.xml", text);
      P.sourceStatus["scenario_a.xml"] = { status: "success", label: "已更新", at: util.nowString(), reason: "定时任务（模拟）" };
      bump(pid, "files");
      return true;
    },
  };
})(window);
