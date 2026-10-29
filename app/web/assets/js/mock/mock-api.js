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
  /* 任务去重键（`(pid,type,target)`）只留在 mock 内部：`task` 的 JSON 形状必须严格
   * 是 §7.10 的 11 个字段，与 `services/tasks.py::Task.to_payload` 一致（M3-07）。 */
  var taskKeys = {};
  var idempotency = {};
  /* 与后端 `services/review.py::IDEMPOTENCY_WINDOW_SECONDS` 同口径（§6.7 第 1 步）。 */
  var IDEMPOTENCY_WINDOW_SECONDS = 600;
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

  /* 人员与管理员全机型共用一份。各 profile 上的 people/admins 指向同一数组，写的时候整表换上再挂回去。 */
  var sharedPeople = JSON.parse(JSON.stringify(S.people || []));
  var sharedAdmins = JSON.parse(JSON.stringify((S.profiles.default && S.profiles.default.admins) || []));
  function publishPeople(people, admins) {
    if (people) sharedPeople = people;
    if (admins) sharedAdmins = admins;
    Object.keys(S.profiles).forEach(function (pid) {
      if (people) S.profiles[pid].people = sharedPeople;
      if (admins) S.profiles[pid].admins = sharedAdmins;
    });
  }
  function bumpPeopleSettings() {
    Object.keys(rev).forEach(function (pid) { rev[pid].settings++; });
  }
  publishPeople(sharedPeople, sharedAdmins);
  Object.keys(S.profiles).forEach(function (pid) {
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
    var admin = P.admins.some(function (a) { return (a.ip && a.ip === preset.ip) || (!a.ip && a.name === name); });
    var label = admin ? "管理员" : person ? "成员" : "访客";
    return { ip: preset.ip, name: name, registered: !!person, is_deployer: false, is_admin: admin, role_label: label };
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

  /** 与 services/dl.py::_locate_variable 同序：节点 ID、定位符、绑定路径。找不到返回 null。 */
  function locateDlVariable(ix, sourceFile, variablePath) {
    var path = String(variablePath || "").trim();
    if (!path) return null;
    var N = ix.N;
    if (ix.byId[path] && ix.byId[path].length) return ix.byId[path][0];
    if (ix.byLoc[path] !== undefined) return ix.byLoc[path];
    if (path.charAt(0) !== "/" && ix.byLoc["/" + path] !== undefined) return ix.byLoc["/" + path];
    var normalized = path.split(" › ").join("/").split("›").join("/");
    var parts = normalized.split("/").filter(Boolean);
    if (sourceFile && parts[0] === sourceFile) parts = parts.slice(1);
    if (!parts.length) return null;
    for (var i = 1; i < N.p.length; i++) {
      var chain = [];
      var k = i;
      var guard = 0;
      while (k > 0 && k < N.l.length && guard <= N.l.length) {
        chain.unshift(N.l[k]);
        k = N.p[k];
        guard++;
      }
      if (chain.length !== parts.length) continue;
      var same = true;
      for (var j = 0; j < parts.length; j++) if (chain[j] !== parts[j]) same = false;
      if (same) return i;
    }
    return null;
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
    if (A.length > 20000 || B.length > 20000) return { lines: [], total: 0, truncated: true, too_large: true };
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
  /* 契约与 `app/services/tasks.py` + `app/server/api_tasks.py` 对齐（M3-07 建、M3-06 同步，R19）：
   * `task` 的 JSON 形状严格是 §7.10 的 11 个字段，去重键只留在内部 `taskKeys`；
   * 状态由条目结果推出（全成 succeeded / 全败 failed / 部分 partial）；
   * 结束时 `tasks` 必递增，`files` 只在真的写入过内容时递增，`settings` 只随 update_all。 */
  var TASK_FAILED_STATUSES = ["download_failed", "rejected", "too_large", "error"];
  /* 互斥面与后端 `_CONFLICTING_TYPES` 同源（卡 6 复审 F2，卡 7 把规则显示给用户）：
   * 同一机型内三类任务两两互斥；只要有一方是 refresh_records，就按机型整块挡、不看 target
   * （update_all 会对每个有 record_url 的映射项跑 refresh_records）。
   * 「全部更新」正在跑时点「立即拉取/单条更新」**必然** 409 BUSY。 */
  var TASK_CONFLICTS = {
    update_source: ["update_all", "refresh_records"],
    update_all: ["update_source", "refresh_records"],
    refresh_records: ["update_source", "update_all"],
  };

  function busyTaskByKey(key) {
    return Object.keys(tasks).map(function (k) { return tasks[k]; }).filter(function (t) {
      return taskKeys[t.id] === key && (t.state === "queued" || t.state === "running");
    })[0];
  }

  function activeTasksOfTypes(pid, types) {
    return Object.keys(tasks).map(function (k) { return tasks[k]; }).filter(function (t) {
      return t.profile === pid && (t.state === "queued" || t.state === "running") && types.indexOf(t.type) >= 0;
    })[0];
  }

  /* progress 契约（卡 6 移交、卡 7 对齐，R19）：与后端同一出处——
   * 建任务时是 0/0；update_all 在**开始执行**时发布 total（`updates.update_all`
   * 首步 `_publish(task, 0, total, None)`）；单条任务（update_source/refresh_records）
   * 要等下载调用**返回之后**才一起回填 done/total（`_publish_single`），整个下载
   * 期间就是 0/0 ⇒ shell.js 渲染「准备中」。旧 mock 在建任务时就把 total 写进
   * payload，掩盖了这个窗口，本卡改齐。 */
  /* §10.2 末段 / §10.3：`update_all` 对每个**带 record_url** 的映射项在配置更新之后再跑
   * 一次记录拉取，所以「条目数」可以比「目标数」多——后端 `updates.update_all` 就是这个
   * 形状（`progress.total` 仍是映射项数，`result.items` 一个映射项可能出两条）。旧 mock 只
   * 跑配置那半，于是 §9.8.6 空状态里那句「定时更新时会自动拉取」在 mock 里演示不出来。
   * 条目顺序与后端一致：先配置、后记录。
   * （本函数刻意放在 `runTask` 之前：`tests/test_api_tasks.py` 那条 mock 契约用例是按
   * 「从 runTask 的定义起 2600 个字符」的窗口读源码的，把注释堆进 runTask 会把窗口尾的
   * 那句 202 响应挤出窗口——那是断言的容量问题，不是它测错了，所以这里收窄 runTask，
   * 而不是去放宽那条断言。也别在这里逐字写出那两个被 grep 的串，窗口是按它们定位的。） */
  function taskItemsFor(P, pid, type, name) {
    var first = type === "refresh_records" ? doRefreshRecords(P, name) : doUpdate(P, pid, name);
    var items = [first];
    if (type === "update_all") {
      var mapped = P.mapping.filter(function (x) { return x.name === name; })[0];
      if (mapped && mapped.record_url) items.push(doRefreshRecords(P, name));
    }
    return items;
  }

  function runTask(pid, type, name, actor) {
    var P = profile(pid);
    var key = pid + ":" + type + ":" + (name || "*");
    var running = busyTaskByKey(key) || activeTasksOfTypes(pid, TASK_CONFLICTS[type] || []);
    if (running) return fail(409, "BUSY", "同类任务正在执行（由 " + running.actor + " 发起）", { task_id: running.id });
    var targets = type === "update_all" ? P.mapping.map(function (m) { return m.name; }) : [name];
    var t = {
      id: util.uid("task"),
      profile: pid,
      type: type,
      target: name || null,
      state: "running",
      progress: { done: 0, total: 0 },
      result: { items: [] },
      error: null,
      actor: actor,
      created_at: util.nowString(),
      finished_at: null,
    };
    tasks[t.id] = t;
    taskKeys[t.id] = key;
    bump(pid, "tasks");
    var step = 0;
    (function next() {
      setTimeout(function () {
        var tgt = targets[step];
        if (type === "update_all") t.progress.total = targets.length;
        taskItemsFor(P, pid, type, tgt).forEach(function (item) { t.result.items.push(item); });
        step++;
        t.progress.done = step;
        if (type !== "update_all") t.progress.total = targets.length;
        bump(pid, "tasks");
        if (step < targets.length) return next();
        var failed = t.result.items.filter(function (x) {
          return TASK_FAILED_STATUSES.indexOf(x.status) >= 0;
        }).length;
        /* 与后端 `_state_from_items` 同判据：**全部条目**失败才 `failed`。旧写法拿
         * `targets.length` 比，条目数一旦多于目标数（上面那条记录拉取就会）会把
         * 「部分失败」误报成「失败」。 */
        t.state = failed === 0 ? "succeeded" : failed === t.result.items.length ? "failed" : "partial";
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
        // 与后端同口径：unchanged 不通知 files（R5），settings 只随 update_all（§10.1）。
        if (t.result.items.some(function (x) { return x.status === "success"; })) bump(pid, "files");
        if (type === "update_all") bump(pid, "settings");
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

  /* §10.3 / C13 契约（卡 9 / M3-09 与后端对齐，SPEC 权威、mock 跟随）：
   * 拉到的内容与**最新一条**记录逐字节相同 ⇒ `unchanged`，**不新建记录文件**；不同才按
   * §5.3 命名保存。旧 mock 每次点击都无条件 `records.unshift(...)`，于是「再拉相同内容
   * 条数不变」这一半在 mock 里根本测不出来，本卡改齐。
   * mock 没有真的远端可下载：可下载的那份内容就按最新一条记录对待，所以已有记录的源每次
   * 拉取都是 `unchanged`（与「远端没换内容」时的后端一字不差），一条记录都没有时落第一份。
   * 状态词汇与 `services/updates.py::LABELS` 同口径（`success`→已更新、`unchanged`→无变化），
   * 旧文案「已拉取 / 新增 1 条修改记录」是 mock 自己造的。 */
  function doRefreshRecords(P, name) {
    var m = P.mapping.filter(function (x) { return x.name === name; })[0];
    if (!m || !m.record_url) return { name: name, status: "skipped", label: "已跳过", reason: "未配置修改记录链接" };
    var f = P.files[name];
    // mock 把记录挂在文件对象上，没有文件对象时无处存放（后端会照常落 records/<name>/）。
    if (!f) return { name: name, status: "skipped", label: "已跳过", reason: "mock：本地还没有该文件的记录目录" };
    if (f.records[0]) {
      // 「远端内容」与最新一条逐字节相同 ⇒ unchanged，**不新建记录文件**（C13）。
      return { name: name, status: "unchanged", label: "无变化", reason: "内容与最新一条修改记录一致，未新建记录文件", archived: null };
    }
    var s = stamp();
    var filename = "record_" + s.day + "_" + s.sec + recordExt(m.record_url);
    f.records.unshift({ filename: filename, text: "<changes/>", mtime: util.nowString() });
    return { name: name, status: "success", label: "已更新", reason: "已保存新的修改记录 " + filename, archived: null };
  }

  /* §5.3：扩展名取自 record_url 的路径（含点在内 ≤10 个字符且只允许字母数字与点），
   * 取不到则 `.txt`。判据与 `storage.save_record_file` 逐条对齐，别在 mock 里写死 `.xml`。
   * （Python 的 `str.isalnum()` 认中文等非 ASCII 字母，这里只认 A-Za-z0-9：更严，
   * 差别只在「怪扩展名落到 .txt」，演示无影响。） */
  function recordExt(url) {
    var path = String(url || "").split("?")[0].split("#")[0];
    var leaf = path.slice(path.lastIndexOf("/") + 1);
    var dot = leaf.lastIndexOf(".");
    var ext = dot > 0 ? leaf.slice(dot) : "";
    if (!ext || ext.length > 10 || /[^A-Za-z0-9.]/.test(ext)) return ".txt";
    return ext;
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
    return id.is_admin ? null : fail(403, "FORBIDDEN", "仅管理员可执行该操作");
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

  function profileName(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw).trim();
    if (!text || text.length > 100 || /[\u0000-\u001f\u007f]/.test(text)) return null;
    return text;
  }

  route("POST", "/profiles", function (a, req) {
    var id = identity("default");
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = profileName(req.body.name);
    if (!name) return fail(400, "INVALID_INPUT", "请输入机型名称", { field: "name" });
    if (req.body.copy_from && !profile(req.body.copy_from)) return fail(400, "INVALID_INPUT", "要复制的机型不存在", { field: "copy_from" });
    if (S.models.some(function (m) { return m.name === name; })) return fail(409, "FILE_EXISTS", "已存在同名机型", { field: "name" });
    var slug = name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9_-]/g, "") || "model";
    var mid = slug;
    var k = 2;
    while (S.profiles[mid] || mid === "default") mid = slug + "-" + k++;
    var src = req.body.copy_from ? profile(req.body.copy_from) : null;
    var copiedFiles = {};
    if (src) Object.keys(src.files).forEach(function (n) { copiedFiles[n] = { current: { text: fileText(src.files[n].current), mtime: src.files[n].current.mtime }, archives: clone(src.files[n].archives), records: [] }; });
    S.profiles[mid] = src
      ? Object.assign(clone(Object.assign({}, src, { files: {} })), { files: copiedFiles, remarks: [], admins: [], people: [], sourceStatus: {}, schedule: Object.assign({}, src.schedule || {}, { last_run: null }) })
      : { files: {}, mapping: [], sourceStatus: {}, favorites: [], remarks: [], bindings: [], tools: [], schedule: { enabled: false, interval_hours: 24, last_run: null }, admins: [], people: [], dl: null };
    publishPeople(sharedPeople, sharedAdmins);
    rev[mid] = { files: 1, favorites: 1, remarks: 1, settings: 1, tasks: 1 };
    S.models.push({ id: mid, name: name });
    bump("default", "settings");
    return ok({ profile: { id: mid, name: name } }, 201);
  });

  route("PATCH", "/profiles/{pid}", function (a, req) {
    var deny = needDeployer(identity(a.pid));
    if (deny) return deny;
    var m = S.models.filter(function (x) { return x.id === a.pid; })[0];
    if (!m) return fail(404, "NOT_FOUND", "机型不存在");
    var name = profileName(req.body.name);
    if (!name) return fail(400, "INVALID_INPUT", "请输入机型名称", { field: "name" });
    if (S.models.some(function (x) { return x.id !== a.pid && x.name === name; })) return fail(409, "FILE_EXISTS", "已存在同名机型", { field: "name" });
    m.name = name;
    bump("default", "settings");
    return ok({ profile: m });
  });

  route("DELETE", "/profiles/{pid}", function (a) {
    var deny = needDeployer(identity(a.pid));
    if (deny) return deny;
    if (a.pid === "default") return fail(400, "INVALID_INPUT", "默认机型不能删除", { field: "id" });
    if (!S.models.some(function (x) { return x.id === a.pid; })) return fail(404, "NOT_FOUND", "机型不存在");
    S.models = S.models.filter(function (x) { return x.id !== a.pid; });
    delete S.profiles[a.pid];
    bump("default", "settings");
    return ok({ deleted: true, recovery: "mock_delete_profile_" + a.pid });
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
      var unchanged = exists && !archived;
      // 与后端一致：内容没变就不递增 files（决策 16——失败写与「什么都没改」都不 bump）。
      if (!unchanged) bump(a.pid, "files");
      return ok({ file: { name: a.name }, archived: archived, unchanged: unchanged }, exists ? 200 : 201);
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
    // 绑定：只摘掉该文件的变量；只有**因本次删除而变空**的组才删掉（后端同规则）。
    var bindingChanged = false;
    P.bindings = P.bindings.reduce(function (out, b) {
      var all = b.variables || [];
      var kept = all.filter(function (v) { return v.file !== a.name; });
      if (kept.length === all.length) {
        out.push(b);
        return out;
      }
      bindingChanged = true;
      if (kept.length) out.push(Object.assign({}, b, { variables: kept }));
      return out;
    }, []);
    // 与后端一致：只有真正改动了的权威数据才递增对应修订分量（决策 16）。
    bump(a.pid, "files");
    if (removed.favorites) bump(a.pid, "favorites");
    if (removed.remarks) bump(a.pid, "remarks");
    if (bindingChanged) bump(a.pid, "settings");
    return ok({ deleted: true, removed: removed, recovery: recoveryDir(a.pid, a.name) });
  });

  /** §5.6 的恢复区目录名：`<时间>_delete_file_<机型>_<文件>`。 */
  function recoveryDir(pid, name) {
    var stamp = util.nowString().replace(/[-: ]/g, "").replace(/(\d{8})(\d{6})/, "$1_$2");
    return stamp + "_delete_file_" + pid + "_" + name;
  }

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
    // §6.7 第 1 步：命中最近 10 分钟的幂等表就原样返回上次结果，不生成第二份文件。
    var hit = b.request_id && idempotency[b.request_id];
    if (hit && Date.now() / 1000 - hit.at <= IDEMPOTENCY_WINDOW_SECONDS) return ok(hit.result);
    var ix = currentIndex(P, b.file);
    if (!ix) return fail(404, "NOT_FOUND", "源文件不存在");
    if (ix.sha !== b.source_sha) return fail(409, "REVIEW_CONFLICT", "源文件在你审阅期间已被更新，请刷新后重新核对");
    var approved = [];
    var rejected = [];
    var edits = [];
    var pending = P.remarks.filter(function (r) { return r.status === "pending" && r.source_file === b.file; });
    var decisions = b.decisions || [];
    for (var di = 0; di < decisions.length; di++) {
      var d = decisions[di];
      var group = pending.filter(function (r) { return (r.locator || r.node_path || r.node_key) === d.group_key; });
      // 允许部分提交（C07）：请求里的组可能已被别人决定过，只能忽略，不是客户端错误。
      if (!group.length) continue;
      if (d.choice === "reject") {
        group.forEach(function (r) { rejected.push(r); });
        continue;
      }
      var chosen = group.filter(function (r) { return r.id === d.choice; })[0];
      if (!chosen) return fail(400, "INVALID_INPUT", "choice 只能是该组的建议 ID 或 reject", { field: d.group_key, group_key: d.group_key });
      var loc = locateRemark(ix, chosen);
      // §6.7 第 7 步：stale（含定位不到）的节点只能驳回。前端已禁用按钮，这是服务端那一半——
      // 报 400 指出是哪个组，不静默驳回，也不伪装成 409。stale 按组的第一条原值算（§7.8）。
      if (loc.i < 0 || ix.N.v[loc.i] !== group[0].original_value) {
        return fail(400, "INVALID_INPUT", "该节点的当前值已变化或无法唯一定位，只能驳回", { field: d.group_key, group_key: d.group_key, reason: loc.i < 0 ? "unresolvable" : "stale" });
      }
      // 第 4 步：同组各条建议的「提交时原值」可能彼此不同（间隔里文件被动过）→ 409。
      if (ix.N.v[loc.i] !== chosen.original_value) throw { conflict: chosen.node_label };
      edits.push({ loc: ix.N.loc[loc.i], value: chosen.proposed_value, label: chosen.node_label });
      approved.push(chosen);
      group.forEach(function (r) { if (r !== chosen) rejected.push(r); });
    }
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
    if (b.request_id) idempotency[b.request_id] = { at: Date.now() / 1000, result: result };
    // 审阅写回属于建议域：原文件字节不变、只新增一份生成文件，所以只递增 remarks（§7.3）。
    if (approved.length || rejected.length) bump(a.pid, "remarks");
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
    var rec = String(req.body.record_url || "").trim();
    var keyword = String(req.body.keyword || "").trim();
    if (!validName(name)) return fail(400, "INVALID_INPUT", "文件名不能为空且不能包含 \\ / : * ? \" < > |", { field: "name" });
    if (!url) return fail(400, "INVALID_INPUT", "下载链接不能为空", { field: "url" });
    if (!/^https?:\/\//i.test(url)) return fail(400, "INVALID_INPUT", "下载链接需以 http:// 或 https:// 开头", { field: "url" });
    if (rec && !/^https?:\/\//i.test(rec)) return fail(400, "INVALID_INPUT", "修改记录链接需以 http:// 或 https:// 开头", { field: "record_url" });
    // 同名已存在：与后端同用 409 FILE_EXISTS（§7.1 把 INVALID_INPUT 定为 400）。
    if (P.mapping.some(function (m) { return m.name === name; })) return fail(409, "FILE_EXISTS", "已存在同名更新源", { field: "name" });
    var item = { name: name, url: url, record_url: rec };
    if (keyword) item.keyword = keyword;
    P.mapping.push(item);
    bump(a.pid, "settings");
    return ok({ item: P.mapping[P.mapping.length - 1] }, 201);
  });

  route("PATCH", "/p/{pid}/sources/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var m = P.mapping.filter(function (x) { return x.name === a.name; })[0];
    // 与 `services/updates.rename_source` 一致：旧名不在映射 ⇒ 404 + detail.name。
    if (!m) return fail(404, "NOT_FOUND", "更新源不存在", { name: a.name });
    var name = String(req.body.name || "").trim();
    var url = String(req.body.url || "").trim();
    var rec = String(req.body.record_url || "").trim();
    var keyword = req.body.keyword === undefined || req.body.keyword === null ? null : String(req.body.keyword).trim();
    if (!validName(name)) return fail(400, "INVALID_INPUT", "文件名不合法", { field: "name" });
    if (!url) return fail(400, "INVALID_INPUT", "下载链接不能为空", { field: "url" });
    if (!/^https?:\/\//i.test(url)) return fail(400, "INVALID_INPUT", "下载链接需以 http:// 或 https:// 开头", { field: "url" });
    if (rec && !/^https?:\/\//i.test(rec)) return fail(400, "INVALID_INPUT", "修改记录链接需以 http:// 或 https:// 开头", { field: "record_url" });
    if (name !== a.name && P.mapping.some(function (x) { return x.name === name; })) return fail(409, "FILE_EXISTS", "目标文件名已存在", { field: "name" });
    // migrate_files 缺省按 true（与 services/updates.py 与 storage.update_config_mapping 同默认）。
    var migrate = name !== a.name && req.body.migrate_files !== false;
    if (migrate && P.files[a.name]) {
      P.files[name] = P.files[a.name];
      delete P.files[a.name];
      P.favorites.forEach(function (f) { if (f.source_file === a.name) f.source_file = name; });
      P.remarks.forEach(function (r) { if (r.source_file === a.name) r.source_file = name; });
      P.bindings.forEach(function (b) { b.variables.forEach(function (v) { if (v.file === a.name) v.file = name; }); });
      ["pp_energy", "rp_energy", "rp_width", "ppz"].forEach(function (k) {
        var it = P.dl && P.dl.items && P.dl.items[k];
        if (it && it.binding && it.binding.source_file === a.name) it.binding.source_file = name;
      });
    }
    m.name = name;
    m.url = url;
    m.record_url = rec;
    if (keyword !== null) {
      if (keyword) m.keyword = keyword;
      else delete m.keyword;
    }
    // 改名不产生新的文件内容，只改设置：与后端一致，只递增 settings（R5）。
    bump(a.pid, "settings");
    return ok({ item: m });
  });

  route("DELETE", "/p/{pid}/sources/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var before = P.mapping.length;
    P.mapping = P.mapping.filter(function (m) { return m.name !== a.name; });
    var removed = P.mapping.length !== before;
    if (removed) bump(a.pid, "settings"); // 删了 0 条不递增（决策 16）
    return ok({ removed: removed });
  });

  /* 与后端 `api_tasks.create_task` 同一道校验顺序（卡 6 裁定，R19 同步）：
   * 未知 type → 400（field=type）；名字非法 → 400（field=name，_validated_target）；
   * 名字不在映射表 → **404 NOT_FOUND**，不是 202 ——否则会排出一条「什么都没做却
   * 显示已完成」的绿任务。404 附带 detail.name 供前端按业务结果显示。 */
  var TASK_TYPE_LIST = ["update_source", "update_all", "refresh_records"];
  route("POST", "/p/{pid}/tasks", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var type = req.body.type;
    if (TASK_TYPE_LIST.indexOf(type) < 0) return fail(400, "INVALID_INPUT", "任务类型只能是 " + TASK_TYPE_LIST.join("/"), { field: "type", wanted: type });
    var name = null;
    if (type !== "update_all") {
      name = String(req.body.name || "").trim();
      if (!validName(name)) return fail(400, "INVALID_INPUT", "文件名不能为空且不能包含 \\ / : * ? \" < > |", { field: "name", wanted: name });
      if (!P.mapping.some(function (m) { return m.name === name; })) return fail(404, "NOT_FOUND", "该文件没有配置更新源", { name: name });
    }
    return runTask(a.pid, type, name, id.name);
  });

  route("GET", "/p/{pid}/tasks", function (a, req) {
    var items = Object.keys(tasks).map(function (k) { return tasks[k]; }).filter(function (t) {
      return t.profile === a.pid && (req.query.active !== "1" || t.state === "running" || t.state === "queued");
    });
    return ok({ items: clone(items) });
  });

  route("GET", "/tasks/{id}", function (a) {
    var t = tasks[a.id];
    /* 消息与后端 `api_tasks.get_task` 一致：404 = 不存在**或已结束太久**（1 小时/200 条窗口）。 */
    return t ? ok({ task: clone(t) }) : fail(404, "NOT_FOUND", "任务不存在或已结束太久", { id: a.id });
  });

  /* ---------- 定时更新：与后端 `app/services/scheduler.py` 同口径（M3-08 / R19） ----------
   * SPEC 是准，mock 只是把同一条契约在模板模式下演一遍：
   * - `next_run_at` **不持久化**、只在 `enabled` 时给出，否则 `null`（不是缺字段）；
   * - 播种：`last_run.at + interval`；算出来已过期 ⇒ 现在 + 2 分钟（避免启动风暴）；
   *   没有 `last_run` ⇒ 现在 + interval；
   * - `interval_hours` 必须是 1–2160（=24×90 天）的**整数**：小数、字符串、布尔、越界
   *   一律 400，`detail.field = "interval"`（后端 `scheduler.INTERVAL_FIELD` 同值）。 */
  function fmtLocal(d) {
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2) + " " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) + ":00";
  }
  function nextRunAt(s, from) {
    var span = s.interval_hours * 3600 * 1000;
    var base = s.last_run && s.last_run.at ? util.parseTime(s.last_run.at) : null;
    var next = base ? new Date(base.getTime() + span) : new Date(from.getTime() + span);
    if (next.getTime() <= from.getTime()) next = new Date(from.getTime() + 120000);
    return fmtLocal(next);
  }
  function scheduleView(P, from) {
    var s = clone(P.schedule);
    s.next_run_at = s.enabled ? nextRunAt(s, from) : null;
    return s;
  }

  route("GET", "/p/{pid}/schedule", function (a, req, P) {
    return ok(scheduleView(P, new Date()));
  });

  route("PUT", "/p/{pid}/schedule", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var raw = req.body.interval_hours;
    var hrs = typeof raw === "number" ? raw : NaN;
    if (!isFinite(hrs) || hrs !== Math.floor(hrs) || hrs < 1 || hrs > 24 * 90) {
      return fail(400, "INVALID_INPUT", "间隔必须是 1-2160 之间的整数小时", { field: "interval" });
    }
    P.schedule.enabled = !!req.body.enabled;
    P.schedule.interval_hours = hrs;
    bump(a.pid, "settings");
    /* 成功才 bump；403/400 都直接返回，一个字节都不动（与后端「失败写不递增」一致）。
     * 返回的是**整份视图**（含 next_run_at），和 GET 同一个形状——保存后界面按新间隔重计划。 */
    return ok(scheduleView(P, new Date()));
  });

  /* ---------- 人员与权限：与后端 `app/services/people.py` 同口径（M3-13 / INH-1898） ----------
   * SPEC 与后端是准（R19），mock 只是把同一条契约在模板模式下再演一遍：
   * - **IP 必须规范化后才落盘**。后端复用 `services/auth.normalize_ip`（也就是身份解析
   *   用来比对客户端地址的那一只）：IPv4 四段 0–255 且拒绝前导零、IPv6 小写 + 压缩最长零段、
   *   `::ffff:a.b.c.d` 折成点分 IPv4、两端方括号去掉。JS 没有 `ipaddress`，所以这三条规则
   *   在下面 `normalizeIp` 里按同口径实现。不归一的后果不是难看，是**静默失效**：客户端地址
   *   是规范化过的，而 `resolve_person_by_ip` 逐字比较，于是「登记了 IP、也加了管理员，
   *   身份却不是管理员」。
   * - **响应形状取后端先例**（`api_sources.py`）：PUT/POST ⇒ `{item}`（POST 是 201）、
   *   DELETE ⇒ `{removed: bool}`，重名 409 `FILE_EXISTS`。原先这几条路由写的
   *   `{ok:true}` / `{deleted:true}` / 409 `INVALID_INPUT` 是**未定契约**（R46：路由当时还不存在），
   *   本卡按后端对齐。
   * - 非法 IP / 非法姓名一律 400 并带 `detail.field`（`ip` / `name`，与 `views/settings.js`
   *   的字段名同一契约，§7.1）；失败写不 bump（全局约束 / 决策 16）。
   * - 管理员姓名另有一档「删除路径走不通」，**只压创建端**（下面 `BAD_ADMIN_NAME_MESSAGE`
   *   那一整段注释就是它，与 `people._validated_admin_name` 同一裁定）。
   * - 「删了 0 条」不 bump、不改数组。
   */
  var BAD_IP_MESSAGE = "请输入合法的 IPv4/IPv6 地址";
  var BAD_NAME_MESSAGE = "姓名需为 1-100 个字符，且不含控制字符";
  var BAD_ADMIN_NAME_MESSAGE =
    "管理员姓名不能包含 / 、\\ 或任何 %XX 编码写法（这样的人员无法从名单里删除）";
  /* 「加得进来、删不出去」甚至「撤错人」那条死路（M3-13 / INH-1898 三轮裁定，与后端
   * `people._UNMANAGEABLE_PATH_CHARS` / `_UNMANAGEABLE_PATH_TOKENS` / `_PERCENT_ESCAPE_RE` /
   * `_BAD_ADMIN_NAME_MESSAGE` 同源；比对由 `tests/test_api_people.py::TestMockPeopleContract`
   * 逐值执法，它**执行**本文件而不是 grep 本文件）：管理员姓名进的是
   * `DELETE /admins/{name}` 那个路径段，`api.seg()` = `encodeURIComponent`，于是
   * ① 含 `/`、`\\` ⇒ 编成 `%2F` / `%5C`，撞后端 §16.8 第 3 条的全站守卫；
   * ② 含**字面** `%2f` / `%5c` / `%00`（任意大小写）⇒ `%` 先被编成 `%25` 躲过 raw 那一份，
   *    解码后又重新长出这三只 token，而守卫把 raw 与解码后两份一起看，照样撞；
   * ③ 含**任何**字面 `%XX` ⇒ 路径被解码两次，删除端会拿到另一个名字（`测试%20甲` 会撤掉
   *    `测试 甲`）；后端创建端一律 400，分发层再把二次编码路径整段拒掉。
   * mock 里没有路径守卫（模板模式不发真 HTTP），所以这三档只能由创建端自己复现，否则
   * 「模板里加得进来、真后端拒」就是 mock 发明了后端没有的行为（R19）。
   * **只用于 `POST /admins`**：`DELETE /admins/{name}` 与 `ip_mapping` 的 `name` 照旧宽松——
   * 后端服务层也是同一份不对称（v1 直接写文件留下的存量行必须有出路），别在这里「统一」掉它。
   * IP 键那一档（后端 `_validated_ip_key`）在本文件里打不到：`normalizeIp` 拒绝**一切**带 `%`
   * 的写法（know-how 第 2 节裁定的「mock 只比后端严、不补 IPv6 scope 语法」），所以带编码
   * 分隔符的键在这边更早一步就是 400 `field="ip"`，判向相同、理由更粗，不另加死代码。 */
  var UNMANAGEABLE_PATH_CHARS = ["/", "\\"];
  var UNMANAGEABLE_PATH_TOKENS = ["%2f", "%5c", "%00"];
  var PERCENT_ESCAPE_RE = /%[0-9A-Fa-f]{2}/;

  function octets(text) {
    var parts = String(text).split(".");
    var out = [];
    if (parts.length !== 4) return null;
    for (var i = 0; i < 4; i++) {
      // 前导零与 Python 3.9 的 `ipaddress` 一样拒绝（"010.0.0.1" 不是合法 IPv4 文本）。
      if (!/^(0|[1-9]\d{0,2})$/.test(parts[i])) return null;
      var v = Number(parts[i]);
      if (v > 255) return null;
      out.push(v);
    }
    return out;
  }

  function hexGroups(list, allowEmbeddedV4) {
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var g = list[i];
      if (g.indexOf(".") >= 0) {
        // 末尾的点分 IPv4 顶替两组，且只能出现在**最后一组**（`1:2:3:4:5:6:1.2.3.4`）。
        if (!allowEmbeddedV4 || i !== list.length - 1) return null;
        var o = octets(g);
        if (!o) return null;
        out.push((o[0] << 8) | o[1], (o[2] << 8) | o[3]);
        continue;
      }
      if (!/^[0-9a-fA-F]{1,4}$/.test(g)) return null;
      out.push(parseInt(g, 16));
    }
    return out;
  }

  function ipv6Groups(text) {
    if (text.indexOf(":") < 0) return null;
    var compress = text.indexOf("::") >= 0;
    var head, tail;
    if (compress) {
      if (text.indexOf("::", text.indexOf("::") + 1) >= 0) return null; // 只许一个 "::"
      var halves = text.split("::");
      head = halves[0] === "" ? [] : halves[0].split(":");
      tail = !halves[1] || halves[1] === "" ? [] : halves[1].split(":");
      if (head.length + tail.length > 7) return null; // "::" 至少要替一组 0
    } else {
      head = text.split(":");
      tail = [];
    }
    var hg = hexGroups(head, !compress);
    if (!hg) return null;
    var tg = hexGroups(tail, true);
    if (!tg) return null;
    if (compress) {
      var zeros = [];
      for (var i = hg.length + tg.length; i < 8; i++) zeros.push(0);
      return hg.concat(zeros, tg);
    }
    return hg.length + tg.length === 8 ? hg.concat(tg) : null;
  }

  function canonicalIpv6(groups) {
    // IPv4 映射地址折成点分写法（与 `auth.normalize_ip` 的 `ipv4_mapped` 一致）。
    var mapped = groups[5] === 0xffff;
    for (var i = 0; i < 5; i++) if (groups[i] !== 0) mapped = false;
    if (mapped) {
      return [groups[6] >> 8, groups[6] & 255, groups[7] >> 8, groups[7] & 255].join(".");
    }
    // 压缩**最长**的连续零段（长度 ≥2 才压，并列取靠前者——与 `ipaddress` 同规则）。
    var best = { start: -1, len: 0 }, run = { start: -1, len: 0 };
    for (var j = 0; j < 8; j++) {
      if (groups[j] === 0) {
        if (run.len === 0) run.start = j;
        run.len += 1;
        if (run.len > best.len) best = { start: run.start, len: run.len };
      } else {
        run.len = 0;
      }
    }
    var hex = groups.map(function (g) { return g.toString(16); });
    if (best.len < 2) return hex.join(":");
    return hex.slice(0, best.start).join(":") + "::" + hex.slice(best.start + best.len).join(":");
  }

  /** 与后端 `auth.normalize_ip` 同口径：非法即空串（调用方据此判 400）。 */
  function normalizeIp(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw)
      .trim()
      .replace(/^\[+/, "")
      .replace(/\]+$/, "");
    if (!text) return "";
    if (text.indexOf(":") < 0) {
      var o = octets(text);
      return o ? o.join(".") : "";
    }
    var g = ipv6Groups(text);
    return g ? canonicalIpv6(g) : "";
  }

  /** §11.2：姓名 = 去空白后 1–100 字符、无控制字符（两端空白由调用方先 trim）。 */
  function validPersonName(name) {
    return !!name && name.length <= 100 && !/[\x00-\x1f\x7f]/.test(name);
  }

  /** 与后端 `people._unmanageable_path_segment` 同口径：返回第一串让删除路径走不通的子串。 */
  function unmanageablePathSegment(text) {
    var i;
    for (i = 0; i < UNMANAGEABLE_PATH_CHARS.length; i++) {
      if (text.indexOf(UNMANAGEABLE_PATH_CHARS[i]) >= 0) return UNMANAGEABLE_PATH_CHARS[i];
    }
    var lowered = text.toLowerCase();
    for (i = 0; i < UNMANAGEABLE_PATH_TOKENS.length; i++) {
      if (lowered.indexOf(UNMANAGEABLE_PATH_TOKENS[i]) >= 0) return UNMANAGEABLE_PATH_TOKENS[i];
    }
    return null;
  }

  route("GET", "/p/{pid}/ip-mapping", function (a, req, P) {
    return ok({ items: clone(P.people) });
  });

  route("PUT", "/p/{pid}/ip-mapping/{ip}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var oldIp = normalizeIp(a.ip);
    if (!oldIp) return fail(400, "INVALID_INPUT", BAD_IP_MESSAGE, { field: "ip" });
    // `ip` 缺省取路径那一个（只改姓名时不必重复 IP），与后端 `people.put_ip_mapping` 同口径。
    var ip = normalizeIp(req.body.ip === undefined ? a.ip : req.body.ip);
    if (!ip) return fail(400, "INVALID_INPUT", BAD_IP_MESSAGE, { field: "ip" });
    var name = String(req.body.name === null || req.body.name === undefined ? "" : req.body.name).trim();
    if (!validPersonName(name)) return fail(400, "INVALID_INPUT", BAD_NAME_MESSAGE, { field: "name" });
    var rows = P.people.slice();
    // 路径 IP ≠ body IP = 改 IP：**先删旧再写新**（后端把这两步扣在同一次机型锁里）。
    if (ip !== oldIp) rows = rows.filter(function (x) { return normalizeIp(x.ip) !== oldIp; });
    var found = -1;
    for (var i = 0; i < rows.length; i++) {
      if (normalizeIp(rows[i].ip) === ip) { found = i; break; }
    }
    var item = { ip: ip, name: name, updated_at: util.nowString() };
    if (found >= 0) rows[found] = item;
    else rows.push(item);
    publishPeople(rows, null);
    bumpPeopleSettings();
    return ok({ item: clone(item) });
  });

  route("DELETE", "/p/{pid}/ip-mapping/{ip}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var target = normalizeIp(a.ip);
    if (!target) return fail(400, "INVALID_INPUT", BAD_IP_MESSAGE, { field: "ip" });
    var before = P.people.length;
    var keptPeople = P.people.filter(function (x) { return normalizeIp(x.ip) !== target; });
    var removed = keptPeople.length !== before;
    if (removed) {
      publishPeople(keptPeople, null);
      bumpPeopleSettings(); // 删了 0 条不递增（决策 16，与 DELETE /sources 同形）
    }
    return ok({ removed: removed });
  });

  route("GET", "/p/{pid}/admins", function (a, req, P) {
    return ok({ items: clone(P.admins) });
  });

  route("POST", "/p/{pid}/admins", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(req.body.name === null || req.body.name === undefined ? "" : req.body.name).trim();
    if (!validPersonName(name)) return fail(400, "INVALID_INPUT", BAD_NAME_MESSAGE, { field: "name" });
    // 顺序与后端 `_validated_admin_name` 一致：先 §11.2 的通用档（空 / 超长 / 控制字符报
    // `BAD_NAME_MESSAGE`），再「删除路径走不通」这一档报 `BAD_ADMIN_NAME_MESSAGE`。
    if (PERCENT_ESCAPE_RE.test(name)) return fail(400, "INVALID_INPUT", BAD_ADMIN_NAME_MESSAGE, { field: "name" });
    if (unmanageablePathSegment(name)) return fail(400, "INVALID_INPUT", BAD_ADMIN_NAME_MESSAGE, { field: "name" });
    // 重名 409 用 `FILE_EXISTS`：§7.1 把 `INVALID_INPUT` 定成 400，409 的「同名已存在」是这一只
    // （与后端 `people.add_admin` 和上面 `POST /sources` 同码）。
    if (P.admins.some(function (x) { return String(x.name || "").trim() === name; })) {
      return fail(409, "FILE_EXISTS", "该管理员已存在", { name: name, field: "name" });
    }
    var item = { name: name, updated_at: util.nowString() };
    publishPeople(null, P.admins.concat([item]));
    bumpPeopleSettings();
    return ok({ item: clone(item) }, 201);
  });

  route("DELETE", "/p/{pid}/admins/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = String(a.name || "").trim();
    // **故意只用** `validPersonName`（不含上面那一档）：后端 `people.remove_admin` 就是这样
    // 不对称的——限制只压创建端，删除端留给出存量行（v1 直接写文件的名单里可能有含 `/` 或
    // 字面 `%2f` 的人）。mock 里 `dispatch` 已经 `decodeURIComponent` 过路径段，所以这里真的
    // 撤得掉；后端那一侧由 §16.8 的守卫拦着，这是「存量行只能靠服务层/运维出路」的既成事实，
    // 不是本文件要修的东西。别把它「统一」成创建端那一档。
    if (!validPersonName(name)) return fail(400, "INVALID_INPUT", BAD_NAME_MESSAGE, { field: "name" });
    var before = P.admins.length;
    var keptAdmins = P.admins.filter(function (x) { return String(x.name || "").trim() !== name; });
    var removed = keptAdmins.length !== before;
    if (removed) {
      publishPeople(null, keptAdmins);
      bumpPeopleSettings(); // 同上：删了 0 条不 bump
    }
    return ok({ removed: removed });
  });

  /* ---------- 变量绑定：与后端 `app/services/bindings.py` 同口径（M3-14 / INH-1899） ----------
   * - 至少 2 个有效变量（`P.files` 里存在且 path 非空），无效行丢弃；剩不足 2 个 → 400
   *   `detail.field="variables"`。
   * - 组名进 DELETE 的路径段：`/`、`\`、控制字符与任何字面 `%XX` 在创建端拒（`api.seg()`
   *   会把 `%` 编成 `%25`，删除时撞守卫），删除端保持宽松。
   * - 改名撞已有组 → 409 `FILE_EXISTS`；成功返回 `{item}`，DELETE 返回 `{removed: bool}`
   *   （R46：原先 mock 的 `{deleted:true}` 是未定契约）。
   * - 成功才 bump；删 0 条不 bump、不改数组。
   */
  var BAD_BINDING_GROUP_MESSAGE = "绑定组名称需为 1-100 个字符，且不能包含 /、\\ 或任何 %XX 编码写法（这样的组无法删除）";
  var BAD_BINDING_VARIABLES_MESSAGE = "绑定组至少需要 2 个有效变量（文件存在且路径非空）";
  var DUPLICATE_BINDING_GROUP_MESSAGE = "已存在同名绑定组";
  var BINDING_GROUP_MAX = 100;

  function bindingGroupName(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw).trim();
    if (!text || text.length > BINDING_GROUP_MAX) return null;
    if (/[\/\\]/.test(text) || /%[0-9A-Fa-f]{2}/.test(text) || /[\u0000-\u001f\u007f]/.test(text)) return null;
    return text;
  }

  function bindingView(b) {
    return {
      group_name: String(b.group_name || ""),
      variables: (b.variables || []).filter(function (v) { return v && typeof v === "object"; }).map(function (v) {
        return { file: String(v.file || ""), path: String(v.path || "") };
      }),
    };
  }

  route("GET", "/p/{pid}/bindings", function (a, req, P) {
    return ok({ items: P.bindings.map(bindingView) });
  });

  route("PUT", "/p/{pid}/bindings/{group}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = bindingGroupName(req.body.group_name);
    if (!name) return fail(400, "INVALID_INPUT", BAD_BINDING_GROUP_MESSAGE, { field: "group_name" });
    var raw = Array.isArray(req.body.variables) ? req.body.variables : [];
    var vars = raw.filter(function (v) {
      if (!v || typeof v !== "object") return false;
      var file = String(v.file || "").trim();
      var path = String(v.path || "").trim();
      return !!file && !!path && !!P.files[file];
    }).map(function (v) { return { file: String(v.file).trim(), path: String(v.path).trim() }; });
    if (vars.length < 2) return fail(400, "INVALID_INPUT", BAD_BINDING_VARIABLES_MESSAGE, { field: "variables" });
    if (name !== a.group && P.bindings.some(function (b) { return b.group_name === name; })) return fail(409, "FILE_EXISTS", DUPLICATE_BINDING_GROUP_MESSAGE, { field: "group_name" });
    var cur = P.bindings.filter(function (b) { return b.group_name === a.group; })[0];
    if (cur) {
      cur.group_name = name;
      cur.variables = vars;
    } else P.bindings.push({ group_name: name, variables: vars });
    bump(a.pid, "settings");
    return ok({ item: bindingView(cur || P.bindings[P.bindings.length - 1]) });
  });

  route("DELETE", "/p/{pid}/bindings/{group}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var kept = P.bindings.filter(function (b) { return b.group_name !== a.group; });
    var removed = kept.length < P.bindings.length;
    if (removed) {
      P.bindings = kept;
      bump(a.pid, "settings");
    }
    return ok({ removed: removed });
  });

  /* ---------- 外部工具：与后端 `app/services/tools.py` 同口径（M3-15 / INH-1900） ----------
   * - 名称进 DELETE 路径段：`/`、`\`、控制字符与任何字面 `%XX` 在创建端拒；删除端宽松。
   * - 说明可空，去空白后 ≤100、无控制字符。
   * - URL 必须有主机且只允许 http/https。用 `new URL()` 解析，不用字符串前缀匹配
   *   （`java\nscript:` 这类会被前缀网漏掉）；控制字符/空白/反斜杠/userinfo 先拒。
   * - 名称唯一：撞已有条目 → 409 `FILE_EXISTS`；PUT 返回 `{item}`，DELETE 返回
   *   `{removed: bool}`（R46：原 `{ok:true}`/`{deleted:true}` 是未定契约）。
   */
  var BAD_TOOL_NAME_MESSAGE = "工具名称需为 1-100 个字符，且不能包含 /、\\ 或任何 %XX 编码写法（这样的工具无法删除）";
  var BAD_TOOL_DESCRIPTION_MESSAGE = "说明不能超过 100 个字符，且不能包含控制字符";
  var BAD_TOOL_URL_MESSAGE = "访问地址必须是包含主机名的 http 或 https 链接";
  var DUPLICATE_TOOL_MESSAGE = "已存在同名工具";
  var TOOL_TEXT_MAX = 100;

  function toolName(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw).trim();
    if (!text || text.length > TOOL_TEXT_MAX) return null;
    if (/[\/\\]/.test(text) || /%[0-9A-Fa-f]{2}/.test(text) || /[\u0000-\u001f\u007f]/.test(text)) return null;
    return text;
  }

  function toolDescription(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw).trim();
    if (text.length > TOOL_TEXT_MAX || /[\u0000-\u001f\u007f]/.test(text)) return null;
    return text;
  }

  function toolUrl(raw) {
    var text = String(raw === null || raw === undefined ? "" : raw).trim();
    if (!text || /[\u0000-\u0020\u007f\\]/.test(text)) return null;
    var m = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^\/?#]+)/.exec(text);
    if (!m) return null;
    var authority = m[2];
    if (authority.indexOf("%") >= 0 || authority.indexOf("@") >= 0) return null;
    try {
      var parsed = new URL(text);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
      if (!parsed.hostname) return null;
      if (parsed.username || parsed.password) return null;
      return text;
    } catch (e) {
      return null;
    }
  }

  function toolView(t) {
    return {
      name: String(t.name || ""),
      description: String(t.description || ""),
      url: String(t.url || ""),
      added_at: t.added_at || null,
    };
  }

  route("GET", "/p/{pid}/tools", function (a, req, P) {
    return ok({ items: P.tools.map(toolView) });
  });

  route("PUT", "/p/{pid}/tools/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var name = toolName(req.body.name);
    if (!name) return fail(400, "INVALID_INPUT", BAD_TOOL_NAME_MESSAGE, { field: "name" });
    var description = toolDescription(req.body.description);
    if (description === null) return fail(400, "INVALID_INPUT", BAD_TOOL_DESCRIPTION_MESSAGE, { field: "description" });
    var url = toolUrl(req.body.url);
    if (!url) return fail(400, "INVALID_INPUT", BAD_TOOL_URL_MESSAGE, { field: "url" });
    if (name !== a.name && P.tools.some(function (t) { return t.name === name; })) return fail(409, "FILE_EXISTS", DUPLICATE_TOOL_MESSAGE, { field: "name" });
    var cur = P.tools.filter(function (t) { return t.name === a.name; })[0];
    if (cur) {
      cur.name = name;
      cur.description = description;
      cur.url = url;
    } else {
      cur = { name: name, description: description, url: url, added_at: util.nowString() };
      P.tools.push(cur);
    }
    bump(a.pid, "settings");
    return ok({ item: toolView(cur) });
  });

  route("DELETE", "/p/{pid}/tools/{name}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var kept = P.tools.filter(function (t) { return t.name !== a.name; });
    var removed = kept.length < P.tools.length;
    if (removed) {
      P.tools = kept;
      bump(a.pid, "settings");
    }
    return ok({ removed: removed });
  });

  var DL_ITEMS = ["pp_energy", "rp_energy", "rp_width", "ppz"];
  var DL_MODES = ["forward", "reverse", "multi_check"];
  var DL_TRANS_ZERO_MESSAGE = "传输系数不能为 0";

  /* Python `float()` 的十进制语法（允许下划线分隔与首尾空白）。**不要**换回裸
   * `Number(v)`：`Number("") === 0`、`Number(" ") === 0`、`Number([]) === 0`、
   * `Number([5]) === 5`、`Number("0x10") === 16`，这五条后端分别是 invalid/400
   * （C15 / R19）。hex/binary/octal、数组、布尔、null 一律不认；字符串形式的
   * `inf`/`nan` 过不了正则，number 的 `Infinity`/`NaN` 由 isFinite 拒绝，与后端
   * `float()` + `math.isfinite` 同判。 */
  var DL_PY_FLOAT_RE = /^[+-]?(?:(?:\d(?:_?\d)*\.(?:\d(?:_?\d)*)?|\.\d(?:_?\d)*)(?:[eE][+-]?\d(?:_?\d)*)?|\d(?:_?\d)*(?:[eE][+-]?\d(?:_?\d)*)?)$/;

  /** 按 Python float 的语法转成有限 number；不合法返回 null。
   *  请求参数（compute/save）与 extract 提取值**都**走这里。 */
  function dlNumber(v) {
    if (typeof v === "number") return isFinite(v) ? v : null;
    if (typeof v !== "string") return null;
    var text = v.trim();
    if (!text || !DL_PY_FLOAT_RE.test(text)) return null;
    var num = Number(text.replace(/_/g, ""));
    return isFinite(num) ? num : null;
  }

  /** 与 services/dl.py::_validated_params 同口径：{item} 或 {error}。
   *  compute 与 save 都用它，保证 mock 不会放行后端会拒的参数（R19）。 */
  function normalizeDlParams(raw, kind) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { error: "参数必须是对象" };
    if (!raw.coeffs || typeof raw.coeffs !== "object" || Array.isArray(raw.coeffs)) {
      return { error: "系数必须是有限数值" };
    }
    var coeffs = {};
    for (var i = 1; i <= 8; i++) {
      var key = "k" + i;
      var value = dlNumber(raw.coeffs[key]);
      if (value === null) return { error: "系数必须是有限数值" };
      coeffs[key] = value;
    }
    var b = dlNumber(raw.coeffs.b);
    if (b === null) return { error: "系数必须是有限数值" };
    coeffs.b = b;

    var out = { coeffs: coeffs };
    var ranges = ["x_min", "x_max", "y_min", "y_max"];
    for (var j = 0; j < ranges.length; j++) {
      var field = ranges[j];
      var range = raw[field];
      if (range === null || range === undefined || (typeof range === "string" && range.trim() === "")) {
        out[field] = null;
      } else {
        var rangeNum = dlNumber(range);
        if (rangeNum === null) return { error: "范围必须是有限数值", field: field };
        out[field] = rangeNum;
      }
    }
    if (kind === "pp_energy") {
      var trans = dlNumber(raw.trans_coeff);
      var power = dlNumber(raw.power_conv_coeff);
      if (trans === null || power === null) {
        return { error: "传输系数与功率转换系数必须是有限数值" };
      }
      out.trans_coeff = trans;
      out.power_conv_coeff = power;
      if (out.power_conv_coeff === 0) return { error: "功率转换系数不能为 0" };
      if (out.trans_coeff === 0) return { error: DL_TRANS_ZERO_MESSAGE };
    }
    if (out.x_min !== null && out.x_max !== null && out.x_min >= out.x_max) {
      return { error: "x 范围下限必须小于上限" };
    }
    if (out.y_min !== null && out.y_max !== null && out.y_min >= out.y_max) {
      return { error: "y 范围下限必须小于上限" };
    }
    return { item: out };
  }

  function normalizeDlValues(raw) {
    if (raw === undefined || raw === null) return { values: [] };
    if (!Array.isArray(raw)) return { error: "输入值必须是数组" };
    var values = [];
    for (var i = 0; i < raw.length; i++) {
      var num = dlNumber(raw[i]);
      if (num === null) return { error: "输入值必须是有限数值" };
      values.push(num);
    }
    return { values: values };
  }

  function validateDlName(raw) {
    if (!Object.prototype.hasOwnProperty.call(raw, "name")) return null;
    if (typeof raw.name !== "string" || !raw.name.trim()) return "名称必须是非空字符串";
    return null;
  }

  function validateDlBinding(raw) {
    if (raw.binding === undefined || raw.binding === null) return null;
    if (typeof raw.binding !== "object" || Array.isArray(raw.binding)) return "binding 必须是对象";
    var keys = ["source_file", "variable_path"];
    for (var i = 0; i < keys.length; i++) {
      var value = raw.binding[keys[i]];
      if (value !== undefined && value !== null && typeof value !== "string") {
        return "binding." + keys[i] + " 必须是字符串";
      }
    }
    var fieldMap = raw.binding.field_map;
    if (fieldMap !== undefined && fieldMap !== null) {
      if (typeof fieldMap !== "object" || Array.isArray(fieldMap)) return "binding.field_map 必须是对象";
      var names = Object.keys(fieldMap);
      for (var j = 0; j < names.length; j++) {
        if (typeof fieldMap[names[j]] !== "string") return "binding.field_map 必须是字符串映射";
      }
    }
    return null;
  }

  /** 字段集合不变（只改值）：从现有 item 深拷贝，只覆盖已知键，忽略 payload 的未知键。 */
  function mergeDlSet(target, clean, rawBinding) {
    if (!target.coeffs || typeof target.coeffs !== "object") target.coeffs = {};
    Object.keys(clean.coeffs).forEach(function (k) { target.coeffs[k] = clean.coeffs[k]; });
    ["x_min", "x_max", "y_min", "y_max"].forEach(function (k) { target[k] = clean[k]; });
    if (rawBinding && typeof rawBinding === "object" && !Array.isArray(rawBinding)) {
      var binding = target.binding && typeof target.binding === "object" ? clone(target.binding) : {};
      ["source_file", "variable_path"].forEach(function (k) {
        if (Object.prototype.hasOwnProperty.call(rawBinding, k)) {
          binding[k] = rawBinding[k] === null || rawBinding[k] === undefined ? "" : String(rawBinding[k]);
        }
      });
      if (rawBinding.field_map && typeof rawBinding.field_map === "object" && !Array.isArray(rawBinding.field_map)) {
        binding.field_map = clone(rawBinding.field_map);
      }
      target.binding = binding;
    }
    return target;
  }

  /** RP 脉宽：顶层是 AOM2 delay 这一套，`aom_width` 是 AOM2 width 这一套，两套互相独立。 */
  function mergeDlItem(existing, raw, clean, kind, widthClean) {
    var next = clone(existing);
    mergeDlSet(next, clean, raw.binding);
    if (kind === "pp_energy") {
      next.trans_coeff = clean.trans_coeff;
      next.power_conv_coeff = clean.power_conv_coeff;
    }
    if (typeof raw.name === "string" && raw.name.trim()) next.name = raw.name;
    if (kind === "rp_width") {
      if (Object.prototype.hasOwnProperty.call(raw, "aom_sum")) {
        next.aom_sum = raw.aom_sum === null || raw.aom_sum === "" ? null : dlNumber(raw.aom_sum);
      }
      if (widthClean) {
        if (!next.aom_width || typeof next.aom_width !== "object") next.aom_width = emptyDlSet();
        mergeDlSet(next.aom_width, widthClean, raw.aom_width.binding);
      }
    }
    return next;
  }

  function emptyDlSet() {
    var coeffs = {};
    for (var i = 1; i <= 8; i++) coeffs["k" + i] = 0;
    coeffs.b = 0;
    return { coeffs: coeffs, binding: { source_file: "", variable_path: "", field_map: {} }, x_min: null, x_max: null, y_min: null, y_max: null };
  }

  function coeffOnly(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { error: "系数必须是有限数值" };
    var coeffs = {};
    for (var i = 1; i <= 8; i++) {
      var value = dlNumber(raw["k" + i]);
      if (value === null) return { error: "系数必须是有限数值" };
      coeffs["k" + i] = value;
    }
    var b = dlNumber(raw.b);
    if (b === null) return { error: "系数必须是有限数值" };
    coeffs.b = b;
    return { item: { coeffs: coeffs } };
  }

  function coeffSnapshot(coeffs) {
    var out = {};
    for (var i = 1; i <= 8; i++) out["k" + i] = dlNumber(coeffs && coeffs["k" + i]) || 0;
    out.b = dlNumber(coeffs && coeffs.b) || 0;
    return out;
  }

  function sameCoeffMap(a, b) {
    return JSON.stringify(coeffSnapshot(a)) === JSON.stringify(coeffSnapshot(b));
  }

  function sameSnap(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function historyBook(P) {
    if (!P.dlHistory || typeof P.dlHistory !== "object") P.dlHistory = { next: 1, entries: [] };
    if (!Array.isArray(P.dlHistory.entries)) P.dlHistory.entries = [];
    if (!P.dlHistory.next || P.dlHistory.next < 1) P.dlHistory.next = 1;
    return P.dlHistory;
  }

  function dlBindingView(raw) {
    raw = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    var map = raw.field_map && typeof raw.field_map === "object" && !Array.isArray(raw.field_map) ? raw.field_map : {};
    var pairs = {};
    Object.keys(map).forEach(function (k) { if (typeof map[k] === "string") pairs[k] = map[k]; });
    return {
      source_file: typeof raw.source_file === "string" ? raw.source_file : "",
      variable_path: typeof raw.variable_path === "string" ? raw.variable_path : "",
      field_map: pairs,
    };
  }

  function paramSnapshot(item, source) {
    var snap = {
      coeffs: coeffSnapshot(source && source.coeffs),
      x_min: source && source.x_min !== undefined && source.x_min !== "" ? optNum(source.x_min) : null,
      x_max: source && source.x_max !== undefined && source.x_max !== "" ? optNum(source.x_max) : null,
      y_min: source && source.y_min !== undefined && source.y_min !== "" ? optNum(source.y_min) : null,
      y_max: source && source.y_max !== undefined && source.y_max !== "" ? optNum(source.y_max) : null,
      binding: dlBindingView(source && source.binding),
    };
    if (item === "pp_energy") {
      var trans = dlNumber(source && source.trans_coeff);
      var power = dlNumber(source && source.power_conv_coeff);
      snap.trans_coeff = trans === null ? 1 : trans;
      snap.power_conv_coeff = power === null ? 1 : power;
    }
    if (item === "rp_width") {
      snap.aom_sum = source && source.aom_sum !== undefined && source.aom_sum !== "" ? optNum(source.aom_sum) : null;
      snap.aom_width = paramSnapshot("rp_energy", (source && source.aom_width) || {});
    }
    return snap;
  }

  function optNum(v) {
    if (v === null || v === undefined || v === "") return null;
    return dlNumber(v);
  }

  function pushCoeffHistory(P, item, aom, event, note, snapshot, actor, sourceId) {
    var book = historyBook(P);
    var entry = {
      id: "ch_" + ("000000" + book.next).slice(-6),
      item: item,
      aom: aom || "",
      event: event,
      note: note || "",
      coeffs: snapshot.coeffs,
      x_min: snapshot.x_min,
      x_max: snapshot.x_max,
      y_min: snapshot.y_min,
      y_max: snapshot.y_max,
      binding: snapshot.binding,
      actor_ip: actor && actor.ip ? actor.ip : "",
      actor_name: actor && actor.registered ? actor.name : "",
      created_at: util.nowString(),
      source_id: sourceId || "",
    };
    if (item === "pp_energy") {
      entry.trans_coeff = snapshot.trans_coeff;
      entry.power_conv_coeff = snapshot.power_conv_coeff;
    }
    if (item === "rp_width") {
      entry.aom_sum = snapshot.aom_sum;
      entry.aom_width = snapshot.aom_width;
    }
    pruneApplyDupes(P, item);
    book.next += 1;
    book.entries.push(entry);
    var series = book.entries.filter(function (row) { return row.item === item && (row.aom || "") === (aom || ""); });
    if (series.length > 200) {
      var drop = series[0].id;
      book.entries = book.entries.filter(function (row) { return row.id !== drop; });
    }
    return entry;
  }

  function applySnap(target, item, snap) {
    target.coeffs = clone(snap.coeffs);
    ["x_min", "x_max", "y_min", "y_max"].forEach(function (k) { target[k] = snap[k]; });
    target.binding = clone(snap.binding);
    if (item === "pp_energy") {
      target.trans_coeff = snap.trans_coeff;
      target.power_conv_coeff = snap.power_conv_coeff;
    }
    if (item === "rp_width") {
      target.aom_sum = snap.aom_sum === undefined ? null : snap.aom_sum;
      if (!target.aom_width) target.aom_width = emptyDlSet();
      applySnap(target.aom_width, "rp_energy", snap.aom_width || {});
    }
  }

  function historyMode(item, raw) {
    if (item !== "rp_width") return "";
    if (raw === undefined || raw === null || raw === "") return "delay";
    var mode = String(raw).trim();
    return mode === "delay" || mode === "width" ? mode : null;
  }

  function coeffTarget(cfg, item, mode) {
    var host = cfg.items[item];
    if (item === "rp_width" && mode === "width") {
      if (!host.aom_width || typeof host.aom_width !== "object") host.aom_width = emptyDlSet();
      return host.aom_width;
    }
    return host;
  }

  function publicHistory(entry, currentId) {
    var view = {
      id: entry.id,
      item: entry.item,
      aom: entry.aom || "",
      event: entry.event,
      note: entry.note || "",
      coeffs: coeffSnapshot(entry.coeffs),
      x_min: entry.x_min === undefined ? null : entry.x_min,
      x_max: entry.x_max === undefined ? null : entry.x_max,
      y_min: entry.y_min === undefined ? null : entry.y_min,
      y_max: entry.y_max === undefined ? null : entry.y_max,
      binding: dlBindingView(entry.binding),
      actor_ip: entry.actor_ip || "",
      actor_name: entry.actor_name || "",
      created_at: entry.created_at || "",
      source_id: entry.source_id || "",
      current: entry.id === currentId,
    };
    if (entry.item === "pp_energy") {
      view.trans_coeff = entry.trans_coeff;
      view.power_conv_coeff = entry.power_conv_coeff;
    }
    if (entry.item === "rp_width") {
      view.aom_sum = entry.aom_sum === undefined ? null : entry.aom_sum;
      view.aom_width = entry.aom_width || null;
    }
    return view;
  }

  function currentHistoryId(item, entries, liveSnap) {
    var found = null;
    entries.forEach(function (row) {
      if (JSON.stringify(paramSnapshot(item, row)) === JSON.stringify(liveSnap)) found = row.id;
    });
    return found;
  }

  function visibleHistory(item, entries) {
    var kept = [];
    var snaps = [];
    entries.forEach(function (row) {
      var snap = paramSnapshot(item, row);
      var dup = snaps.some(function (prev) { return sameSnap(prev, snap); });
      if (row.event === "apply" && dup) return;
      kept.push(row);
      snaps.push(snap);
    });
    return kept;
  }

  function pruneApplyDupes(P, item) {
    var book = historyBook(P);
    var mine = book.entries.filter(function (row) { return row.item === item; });
    var keep = {};
    visibleHistory(item, mine).forEach(function (row) { keep[row.id] = true; });
    book.entries = book.entries.filter(function (row) { return row.item !== item || keep[row.id]; });
  }

  function dlConfig(P) {
    if (!P.dl) P.dl = clone(S.profiles["default"].dl);
    if (!P.dl.items) P.dl.items = {};
    if (!P.dl.items.ppz) {
      var ppz = emptyDlSet();
      ppz.name = "计算PPz";
      P.dl.items.ppz = ppz;
    }
    var rp = P.dl.items && P.dl.items.rp_width;
    if (rp) {
      if (!Object.prototype.hasOwnProperty.call(rp, "aom_sum")) rp.aom_sum = null;
      if (!rp.aom_width || typeof rp.aom_width !== "object") rp.aom_width = emptyDlSet();
    }
    return P.dl;
  }

  /** 与后端 `services/dl.py::current_rev` 同语义：内容变才变、compute/extract 不影响它。
   *  模板模式没有 SHA-1，用 `MC.mockParse.hash` 的内容哈希代替；值对前端不透明（只回传）。 */
  function dlRev(P) {
    return "d" + parse.hash(JSON.stringify(dlConfig(P).items));
  }

  route("GET", "/p/{pid}/dl", function (a, req, P) {
    var cfg = dlConfig(P);
    return ok({ rev: dlRev(P), items: clone(cfg.items) });
  });

  route("PUT", "/p/{pid}/dl/{item}", function (a, req, P, id) {
    var deny = needDeployer(id);
    if (deny) return deny;
    var cfg = dlConfig(P);
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    // 与后端 `services/dl.py::save_item` 同序：① 头缺失 409；② body 形状与数值校验
    // 400；③ 最后才比 rev 409。否则「rev 过期 + body 非法」时 mock 会报 409，
    // 后端报 400（R19：模板模式不许比后端更早放行或更早拒绝）。
    var header = req.headers["If-Match"] || req.headers["if-match"];
    if (!header) {
      return fail(409, "REVISION_CONFLICT", "缺少 If-Match 头：请先 GET /dl 取得当前 rev", { header: "If-Match" });
    }
    if (!req.body || typeof req.body !== "object" || Array.isArray(req.body)) {
      return fail(400, "INVALID_INPUT", "请求体必须是完整 item 对象");
    }
    var missing = ["x_min", "x_max", "y_min", "y_max"].filter(function (k) {
      return !Object.prototype.hasOwnProperty.call(req.body, k);
    });
    if (missing.length) {
      return fail(
        400,
        "INVALID_INPUT",
        "保存时必须提供完整的范围字段（缺少：" + missing.join("、") + "）；不需要限制时请显式传 null",
        { field: missing[0] }
      );
    }
    var norm = normalizeDlParams(req.body, a.item);
    if (norm.error) return fail(400, "INVALID_INPUT", norm.error);
    var nameErr = validateDlName(req.body);
    if (nameErr) return fail(400, "INVALID_INPUT", nameErr, { field: "name" });
    var bindingErr = validateDlBinding(req.body);
    if (bindingErr) return fail(400, "INVALID_INPUT", bindingErr, { field: "binding" });
    var widthNorm = null;
    if (a.item === "rp_width" && Object.prototype.hasOwnProperty.call(req.body, "aom_width")) {
      var rawWidth = req.body.aom_width;
      if (!rawWidth || typeof rawWidth !== "object" || Array.isArray(rawWidth)) {
        return fail(400, "INVALID_INPUT", "aom_width 必须是对象", { field: "aom_width" });
      }
      var widthMissing = ["x_min", "x_max", "y_min", "y_max"].filter(function (k) {
        return !Object.prototype.hasOwnProperty.call(rawWidth, k);
      });
      if (widthMissing.length) {
        return fail(
          400,
          "INVALID_INPUT",
          "AOM2 width 参数必须提供完整的范围字段（缺少：" + widthMissing.join("、") + "）；不需要限制时请显式传 null",
          { field: "aom_width" }
        );
      }
      widthNorm = normalizeDlParams(rawWidth, a.item);
      if (widthNorm.error) return fail(400, "INVALID_INPUT", widthNorm.error);
      var widthBindingErr = validateDlBinding(rawWidth);
      if (widthBindingErr) return fail(400, "INVALID_INPUT", widthBindingErr, { field: "binding" });
    }
    if (a.item === "rp_width" && Object.prototype.hasOwnProperty.call(req.body, "aom_sum") && req.body.aom_sum !== null && req.body.aom_sum !== "" && dlNumber(req.body.aom_sum) === null) {
      return fail(400, "INVALID_INPUT", "AOM2 delay 与 width 的和必须是有限数值", { field: "aom_sum" });
    }
    if (header !== dlRev(P)) {
      return fail(409, "REVISION_CONFLICT", "计算参数已被他人修改，请刷新后再保存");
    }
    if (!cfg.items[a.item]) return fail(404, "NOT_FOUND", "计算项不存在");
    cfg.editing = false;
    var before = clone(cfg.items[a.item]);
    cfg.items[a.item] = mergeDlItem(cfg.items[a.item], req.body, norm.item, a.item, widthNorm ? widthNorm.item : null);
    var after = cfg.items[a.item];
    if (!sameSnap(paramSnapshot(a.item, before), paramSnapshot(a.item, after))) {
      pushCoeffHistory(P, a.item, "", "config", "", paramSnapshot(a.item, after), id, "");
    }
    bump(a.pid, "settings");
    return ok({ rev: dlRev(P), item: clone(cfg.items[a.item]) });
  });

  route("GET", "/p/{pid}/dl/{item}/coeff-history", function (a, req, P) {
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    var cfg = dlConfig(P);
    var mode = historyMode(a.item, req.query.aom);
    if (mode === null) return fail(400, "INVALID_INPUT", "RP 脉宽模式只能是 delay 或 width", { field: "aom" });
    var target = cfg.items[a.item];
    var live = paramSnapshot(a.item, target);
    var entries = visibleHistory(a.item, historyBook(P).entries.filter(function (row) { return row.item === a.item; }));
    var currentId = currentHistoryId(a.item, entries, live);
    return ok({
      aom: mode,
      current_id: currentId,
      entries: entries.slice().reverse().map(function (row) { return publicHistory(row, currentId); }),
    });
  });

  route("POST", "/p/{pid}/dl/{item}/coeffs", function (a, req, P, id) {
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    var header = req.headers["If-Match"] || req.headers["if-match"];
    if (!header) return fail(409, "REVISION_CONFLICT", "缺少 If-Match 头：请先 GET /dl 取得当前 rev", { header: "If-Match" });
    if (!req.body || typeof req.body !== "object" || Array.isArray(req.body)) return fail(400, "INVALID_INPUT", "请求体必须是对象");
    var mode = historyMode(a.item, req.body.aom);
    if (mode === null) return fail(400, "INVALID_INPUT", "RP 脉宽模式只能是 delay 或 width", { field: "aom" });
    var norm = coeffOnly(req.body.coeffs);
    if (norm.error) return fail(400, "INVALID_INPUT", norm.error, { field: "coeffs" });
    var missing = ["x_min", "x_max", "y_min", "y_max"].filter(function (k) { return !Object.prototype.hasOwnProperty.call(req.body, k); });
    if (missing.length) return fail(400, "INVALID_INPUT", "保存时必须提供完整的范围字段（缺少：" + missing.join("、") + "）", { field: missing[0] });
    var bindingErr = validateDlBinding(req.body);
    if (bindingErr) return fail(400, "INVALID_INPUT", bindingErr, { field: "binding" });
    if (!req.body.binding) return fail(400, "INVALID_INPUT", "binding 必须是对象", { field: "binding" });
    if (a.item === "pp_energy" && (dlNumber(req.body.trans_coeff) === null || dlNumber(req.body.power_conv_coeff) === null)) {
      return fail(400, "INVALID_INPUT", "传输系数与功率转换系数必须是有限数值");
    }
    if (a.item === "pp_energy" && dlNumber(req.body.power_conv_coeff) === 0) return fail(400, "INVALID_INPUT", "功率转换系数不能为 0", { field: "power_conv_coeff" });
    if (a.item === "pp_energy" && dlNumber(req.body.trans_coeff) === 0) return fail(400, "INVALID_INPUT", "传输系数不能为 0", { field: "trans_coeff" });
    if (a.item === "rp_width") {
      if (!Object.prototype.hasOwnProperty.call(req.body, "aom_sum")) return fail(400, "INVALID_INPUT", "保存时必须提供固定和；不需要时请显式传 null", { field: "aom_sum" });
      if (req.body.aom_sum !== null && req.body.aom_sum !== "" && dlNumber(req.body.aom_sum) === null) return fail(400, "INVALID_INPUT", "AOM2 delay 与 width 的和必须是有限数值", { field: "aom_sum" });
      if (!req.body.aom_width || typeof req.body.aom_width !== "object" || Array.isArray(req.body.aom_width)) return fail(400, "INVALID_INPUT", "aom_width 必须是对象", { field: "aom_width" });
      var widthNorm = coeffOnly(req.body.aom_width.coeffs);
      if (widthNorm.error) return fail(400, "INVALID_INPUT", widthNorm.error, { field: "aom_width" });
    }
    var note = req.body.note === undefined || req.body.note === null ? "" : req.body.note;
    if (typeof note !== "string" || note.indexOf("\u0000") >= 0) return fail(400, "INVALID_INPUT", "备注必须是文本", { field: "note" });
    note = note.trim();
    if (note.length > 200) return fail(400, "INVALID_INPUT", "备注不能超过 200 字", { field: "note" });
    if (header !== dlRev(P)) return fail(409, "REVISION_CONFLICT", "计算参数已被他人修改，请刷新后再保存");
    var cfg = dlConfig(P);
    var target = cfg.items[a.item];
    var incoming = paramSnapshot(a.item, {
      coeffs: norm.item.coeffs,
      x_min: req.body.x_min,
      x_max: req.body.x_max,
      y_min: req.body.y_min,
      y_max: req.body.y_max,
      binding: req.body.binding,
      trans_coeff: req.body.trans_coeff,
      power_conv_coeff: req.body.power_conv_coeff,
      aom_sum: req.body.aom_sum,
      aom_width: req.body.aom_width,
    });
    var current = paramSnapshot(a.item, target);
    if (sameSnap(current, incoming)) return ok({ rev: dlRev(P), snapshot: current, coeffs: current.coeffs, unchanged: true, entry: null });
    applySnap(target, a.item, incoming);
    var entry = pushCoeffHistory(P, a.item, "", "save", note, incoming, id, "");
    bump(a.pid, "settings");
    return ok({ rev: dlRev(P), snapshot: incoming, coeffs: incoming.coeffs, unchanged: false, entry: publicHistory(entry, entry.id) });
  });

  route("POST", "/p/{pid}/dl/{item}/coeff-history/{hid}/apply", function (a, req, P, id) {
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    var header = req.headers["If-Match"] || req.headers["if-match"];
    if (!header) return fail(409, "REVISION_CONFLICT", "缺少 If-Match 头：请先 GET /dl 取得当前 rev", { header: "If-Match" });
    if (header !== dlRev(P)) return fail(409, "REVISION_CONFLICT", "计算参数已被他人修改，请刷新后再保存");
    var book = historyBook(P);
    var source = null;
    book.entries.forEach(function (row) { if (row.id === a.hid) source = row; });
    if (!source || source.item !== a.item) return fail(404, "NOT_FOUND", "系数历史不存在", { id: a.hid });
    var cfg = dlConfig(P);
    var target = cfg.items[a.item];
    var incoming = paramSnapshot(a.item, source);
    var current = paramSnapshot(a.item, target);
    var changed = !sameSnap(current, incoming);
    if (changed) {
      applySnap(target, a.item, incoming);
      bump(a.pid, "settings");
    }
    pruneApplyDupes(P, a.item);
    var entries = visibleHistory(a.item, book.entries.filter(function (row) { return row.item === a.item; }));
    var currentId = currentHistoryId(a.item, entries, incoming);
    var row = null;
    book.entries.forEach(function (item) { if (item.id === currentId) row = item; });
    return ok({
      rev: dlRev(P),
      snapshot: incoming,
      coeffs: incoming.coeffs,
      unchanged: !changed,
      entry: row ? publicHistory(row, currentId) : null,
    });
  });

  route("DELETE", "/p/{pid}/dl/{item}/coeff-history/{hid}", function (a, req, P, id) {
    var deny = needAdmin(id);
    if (deny) return deny;
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    var book = historyBook(P);
    var source = null;
    book.entries.forEach(function (row) { if (row.id === a.hid) source = row; });
    if (!source || source.item !== a.item) return fail(404, "NOT_FOUND", "系数历史不存在", { id: a.hid });
    book.entries = book.entries.filter(function (row) { return row.id !== a.hid; });
    var live = paramSnapshot(a.item, dlConfig(P).items[a.item]);
    var entries = visibleHistory(a.item, book.entries.filter(function (row) { return row.item === a.item; }));
    return ok({ deleted: a.hid, current_id: currentHistoryId(a.item, entries, live) });
  });

  route("POST", "/p/{pid}/dl/{item}/extract", function (a, req, P) {
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    // 与后端同序：binding 不是对象 → 400（不是回落 {} 后 404）；source_file 缺 → 404；
    // 节点找不到 → 404；field_map 非对象 → 400。缺 binding 的请求在 mock 里原先会
    // 走下一条 `P.files[undefined]` 的 404，与后端的 400 漂移（R19）。
    var bnd = req.body.binding;
    if (!bnd || typeof bnd !== "object" || Array.isArray(bnd)) {
      return fail(400, "INVALID_INPUT", "binding 必须是对象", { field: "binding" });
    }
    var sourceFile = bnd.source_file === undefined || bnd.source_file === null ? "" : String(bnd.source_file).trim();
    if (!sourceFile) return fail(404, "NOT_FOUND", "绑定的配置文件不存在", { field: "source_file" });
    var variablePath = bnd.variable_path === undefined || bnd.variable_path === null ? "" : String(bnd.variable_path).trim();
    var ix = currentIndex(P, sourceFile);
    if (!ix) return fail(404, "NOT_FOUND", "绑定的配置文件不存在", { field: "source_file" });
    var located = locateDlVariable(ix, sourceFile, variablePath);
    if (located === null) return fail(404, "NOT_FOUND", "在 " + sourceFile + " 中未找到 " + variablePath, { field: "variable_path" });
    var fieldMap = bnd.field_map;
    if (fieldMap === undefined || fieldMap === null) fieldMap = {};
    else if (typeof fieldMap !== "object" || Array.isArray(fieldMap)) {
      return fail(400, "INVALID_INPUT", "field_map 必须是对象", { field: "binding" });
    }
    var N = ix.N;
    var i = located;
    var children = {};
    for (var c = i + 1; c < N.p.length; c++) if (N.p[c] === i) children[N.l[c]] = N.v[c];
    var fields = Object.keys(fieldMap).map(function (fld) {
      var coeff = fieldMap[fld];
      // 无值节点（XML 空元素）与缺失同判 missing；缺失/非数值都不进 coeffs、至少不返回 0（C15）。
      // XML 属性标签带 @；映射里写 min 或 @min 都认。
      var raw = children[fld];
      if ((!(fld in children) || raw === null) && fld.charAt(0) !== "@" && ("@" + fld) in children && children["@" + fld] !== null) {
        raw = children["@" + fld];
      }
      if (raw === undefined || raw === null) return { field: fld, coeff: coeff, status: "missing" };
      var num = dlNumber(raw);
      return num === null
        ? { field: fld, coeff: coeff, status: "invalid", raw: raw }
        : { field: fld, coeff: coeff, status: "ok", value: num };
    });
    var coeffs = {};
    fields.forEach(function (f) { if (f.status === "ok") coeffs[f.coeff] = f.value; });
    return ok({ coeffs: coeffs, fields: fields });
  });

  route("POST", "/p/{pid}/dl/{item}/compute", function (a, req) {
    if (DL_ITEMS.indexOf(a.item) < 0) return fail(404, "NOT_FOUND", "计算项不存在");
    if (DL_MODES.indexOf(req.body.mode) < 0) {
      return fail(400, "INVALID_INPUT", "mode 只允许 forward、reverse、multi_check");
    }
    var norm = normalizeDlParams(req.body.params, a.item);
    if (norm.error) return fail(400, "INVALID_INPUT", norm.error);
    var vals = normalizeDlValues(req.body.values);
    if (vals.error) return fail(400, "INVALID_INPUT", vals.error);
    var params = norm.item;
    var values = vals.values;
    if (req.body.mode === "forward") return ok({ results: dl.forward(params, a.item, values) });
    if (req.body.mode === "reverse") return ok({ results: dl.reverse(params, a.item, values), exhaustive: false });
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
    /** [用例专用] 取某机型的内存数据**引用**。mock 的用例串行跑在同一个 `S` 上，
     * 任何直接改写它的用例都必须自己还原（见 `app/web/tests/tests.js` 的 M3-13 R19 用例），
     * 否则脏数据会留给后面的用例——那是卡 3 那类「桩恢复得太晚」的另一种形态。 */
    profileState: function (pid) { return profile(pid); },
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
