/* 前端纯逻辑测试。新增前端算法时在此追加用例。 */
(function () {
  "use strict";
  var MC = window.MC;
  /* M3-06（修卡 3 遗留）：记下真 API。异步用例把 MC.api.* 换成 stub 后，
   * 后续用例开始时**必须**已经换回真身——下面这条「隔离证明」按注册序排在
   * 唯一一条异步 stub 用例（文件详情）之后，用它证明恢复是真的排到了序上。 */
  var REAL_API = { get: MC.api.get, post: MC.api.post, put: MC.api.put, del: MC.api.del };
  var results = [];
  /*
   * 用例可以是同步的，也可以返回一个 Promise（视图渲染要走 api.get 的 Promise 链，
   * 断言只能在 DOM 真正画出来之后做）。
   *
   * M3-06 起用例**串行执行**：每条要等上一条（含其异步部分）settle 之后才开始。
   * 这是机制修复，不是碰时机——卡 3 的旧机制里同步批次先全部跑完、异步用例的
   * `finally` 恢复 `MC.api.*` 恢复得太晚，整个同步批次期间 API 都是假的（今天恰好
   * 没人依赖真 API 所以无害，卡 7 起有依赖了）。串行之后，stub 用例结束时恢复，
   * 后续用例必然看到真 API；顺序断言见「测试隔离」用例。
   * 异步用例在其 slot 里先占位为失败、settle 成功后才改写，汇总永远是队列的最后一步。
   */
  var queue = Promise.resolve();
  function test(name, fn) {
    queue = queue.then(function () {
      var slot = { name: name, ok: true, err: null };
      results.push(slot);
      try {
        var r = fn();
        if (r && typeof r.then === "function") {
          slot.ok = false;
          return r.then(
            function () {
              slot.ok = true;
              slot.err = null;
            },
            function (e) {
              slot.err = e && e.message ? e.message : String(e);
            }
          );
        }
      } catch (e) {
        slot.ok = false;
        slot.err = e && e.message ? e.message : String(e);
      }
      return undefined;
    });
  }
  function eq(a, b, msg) {
    var sa = JSON.stringify(a);
    var sb = JSON.stringify(b);
    if (sa !== sb) throw new Error((msg || "不相等") + "：期望 " + sb + "，实际 " + sa);
  }
  function ok(v, msg) { if (!v) throw new Error(msg || "断言失败"); }

  MC.icons.inject();

  var XML = '<?xml version="1.0"?><R a="1"><item>x</item><item>y<sub>z</sub></item><param_1 description="d" min="0" max="9" type="int">5</param_1></R>';
  var JSONTXT = '{"a":{"b/c":1,"t":true,"n":null},"arr":[1,{"k":"v"}]}';

  test("XML 解析：根、属性、同名兄弟 occurrence 定位符", function () {
    var N = MC.mockParse.parse(XML, "x.xml").nodes;
    eq(N.l[0], "x.xml");
    eq(N.id[1], "root/R");
    eq(N.loc[1], "/R[1]");
    var items = N.loc.filter(function (l) { return /item\[\d\]$/.test(l); });
    eq(items, ["/R[1]/item[1]", "/R[1]/item[2]"]);
    ok(N.id.indexOf("root/R@a") > 0, "属性 ID");
    ok(N.loc.indexOf("/R[1]/item[2]/#text") > 0, "混合内容 #text");
  });

  test("JSON 解析：JSON Pointer 转义、类型名与旧版 ID 一致", function () {
    var N = MC.mockParse.parse(JSONTXT, "c.json").nodes;
    var i = N.loc.indexOf("/a/b~1c");
    ok(i > 0, "pointer 转义 /");
    eq(N.id[i], "root.a.b/c");
    eq(N.t[i], "int");
    eq(N.v[N.loc.indexOf("/a/t")], "true");
    eq(N.t[N.loc.indexOf("/a/n")], "NoneType");
    eq(N.id[N.loc.indexOf("/arr/1/k")], "root.arr[1].k");
  });

  test("索引：depth / size / childCount / param 元数据折叠", function () {
    var doc = { nodes: MC.mockParse.parse(XML, "x.xml").nodes };
    var idx = MC.doc.buildIndex(doc);
    eq(idx.size[0], idx.n);
    eq(idx.depth[1], 1);
    var p = idx.byLoc["/R[1]/param_1[1]"];
    ok(idx.isParam[p], "识别 param_ 节点");
    eq(idx.paramMeta[p].min, "0");
    ok(idx.metaHidden[idx.byLoc["/R[1]/param_1[1]/@description"]], "description 折叠");
    ok(!idx.metaHidden[idx.byLoc["/R[1]/param_1[1]/@type"]], "type 保留为子行");
    eq(idx.displayPath(p), "R › param_1");
  });

  test("虚拟树：折叠跳过子树、过滤保留祖先与子树、命中计数准确", function () {
    var big = ['<R>'];
    for (var g = 0; g < 50; g++) {
      big.push("<g" + g + ">");
      for (var k = 0; k < 40; k++) big.push("<v" + k + ">" + (g * 100 + k) + "</v" + k + ">");
      big.push("</g" + g + ">");
    }
    big.push("</R>");
    var idx = MC.doc.buildIndex({ nodes: MC.mockParse.parse(big.join(""), "b.xml").nodes });
    var host = document.getElementById("host");
    var tree = new MC.Tree(host, idx, {});
    tree.collapseAll();
    eq(tree.rows.length, 1, "全部折叠只剩根元素");
    tree.expandLevel(2);
    eq(tree.rows.length, 51);
    var n = tree.setFilter("g7");
    eq(n, 1, "只有 g7 命中");
    ok(tree.rows.length >= 2, "命中节点与其子树可见");
    n = tree.setFilter("4321");
    eq(n, 1);
    eq(idx.l[tree.filter.matches[0]], "v21");
    tree.setFilter("");
    tree.destroy();
  });

  test("路由：编码中文文件名与查询参数往返一致", function () {
    var r = { pid: "dl-200", kind: "f", arg: "配置 A.xml", query: { view: "compare", base: "archive:配置 A_20261008.xml", target: "current" } };
    var back = MC.router.parse(MC.router.format(r));
    eq(back.pid, r.pid);
    eq(back.arg, r.arg);
    eq(back.query, r.query);
    eq(MC.router.parse("#/x"), null);
  });

  test("DL：正向计算保留 input_index，越界行标注而非丢弃", function () {
    var item = { coeffs: { k1: 2, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 1 }, x_min: 0, x_max: 10, y_min: null, y_max: 15 };
    var r = MC.dlmath.forward(item, "rp_width", [-1, 3, 9]);
    eq(r.map(function (x) { return x.input_index; }), [0, 1, 2]);
    eq(r.map(function (x) { return x.status; }), ["out_of_x_range", "ok", "out_of_y_range"]);
    eq(r[1].y, 7);
  });

  test("DL：反向求解发现切触重根与多解", function () {
    var sq = { coeffs: { k1: -6, k2: 1, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 9 }, x_min: -10, x_max: 10, y_min: null, y_max: null };
    var r = MC.dlmath.reverse(sq, "rp_width", [0, 4]);
    eq(r[0].status, "single", "(x-3)^2=0 只有切触根");
    ok(Math.abs(r[0].x_values[0] - 3) < 1e-6, "根为 3");
    eq(r[1].status, "multiple");
    eq(r[1].x_values.map(Math.round), [1, 5]);
  });

  test("DL：常数函数返回 identity / none，零除被拒绝", function () {
    var c = { coeffs: { k1: 0, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 2 }, x_min: null, x_max: null, y_min: null, y_max: null };
    eq(MC.dlmath.reverse(c, "rp_width", [2])[0].status, "identity");
    eq(MC.dlmath.reverse(c, "rp_width", [3])[0].status, "none");
    var pp = Object.assign({}, c, { trans_coeff: 1, power_conv_coeff: 0 });
    ok(MC.dlmath.validate(pp, "pp_energy"), "功率转换系数为 0 必须报错");
  });

  test("工具函数：时间格式、字节、安全链接", function () {
    eq(MC.util.formatBytes(2048), "2.0 KB");
    eq(MC.util.safeUrl("javascript:alert(1)"), null);
    ok(MC.util.safeUrl("http://10.0.0.1/x"), "允许 http");
    eq(MC.util.fileFormat("A.JSON"), "json");
  });

  test("标签淘汰：打开第 16 个文件时关掉最久未用的干净标签，有草稿的留下", function () {
    var tabs = [{ key: "overview", pinned: true, opened: 0, lastUsed: 0 }];
    for (var i = 0; i < 16; i++) {
      tabs.push({ key: "f:" + i, pinned: false, opened: i + 1, lastUsed: i + 1, dirty: i === 0 });
    }
    var plan = MC.tabs.enforce(tabs, "f:15", 15);
    eq(plan.closed, ["f:1"]);
    ok(plan.warning === false, "还有可关闭的干净标签时不应警告");
    eq(plan.remaining, 16);
  });

  test("标签淘汰：其余标签都有未保存修改时不关闭并标记警告", function () {
    var tabs = [{ key: "overview", pinned: true }];
    for (var i = 0; i < 16; i++) tabs.push({ key: "f:" + i, dirty: true, lastUsed: i, opened: i });
    var plan = MC.tabs.enforce(tabs, "f:15", 15);
    eq(plan.closed, []);
    ok(plan.warning, "应警告");
    eq(plan.remaining, 17);
  });

  test("标签持久化：临时文件不写入 mc.tabs，速览固定页也不写入", function () {
    ok(!MC.tabs.persistable({ pinned: true, route: { kind: "overview" } }));
    ok(!MC.tabs.persistable({ route: { kind: "t" } }));
    ok(MC.tabs.persistable({ route: { kind: "f" } }));
    ok(MC.tabs.persistable({ route: { kind: "search" } }));
  });

  test("安全：h() 只插入文本节点", function () {
    var el = MC.util.h("div", '<img src=x onerror="window.__xss=1">');
    eq(el.children.length, 0);
    ok(el.textContent.indexOf("<img") === 0);
    ok(!window.__xss);
  });

  test("身份：未登记时顶栏显示「未登记」而不是识别地址（§4.3 / C18）", function () {
    eq(MC.util.identityName({ registered: false, name: "127.0.0.1" }), "未登记");
    eq(MC.util.identityName({ registered: true, name: "张三" }), "张三");
    eq(MC.util.identityName(null), "");
    ok(MC.util.initials(MC.util.identityName({ registered: false, name: "10.0.0.9" })) !== "10", "头像首字不取自 IP");
  });

  test("能力开关：模板全开，生产只放行已接入项，且每项能力都有点名卡号的说明", function () {
    var keys = MC.capKeys.slice().sort();
    var on = MC.capsFor(true);
    var off = MC.capsFor(false);
    ok(keys.length >= 10, "键集非空");
    eq(Object.keys(on).sort(), keys, "capsFor 覆盖全部能力键");
    ok(keys.every(function (k) { return on[k] === true; }), "模板模式下全部可用");
    ok(keys.every(function (k) { return off[k] === (MC.wiredCaps.indexOf(k) >= 0); }), "生产模式只放行已接入的接口");
    ok(MC.wiredCaps.every(function (k) { return MC.capKeys.indexOf(k) >= 0; }), "WIRED 里不能有拼错或未登记的能力键");
    eq(Object.keys(MC.unwired).sort(), keys, "每项能力都要有未接入说明");
    ok(keys.every(function (k) { return /M\d-\d\d/.test(MC.unwired[k]); }), "说明必须点名接入卡号");
  });

  test("M3-03：审阅页已在生产放行，未实现的接口不得提前打开", function () {
    var prod = MC.capsFor(false);
    ["favorites", "revision", "remarks", "search", "history", "compare", "records", "import", "temp", "delete", "review"].forEach(function (k) {
      ok(prod[k], k + " 已接真实后端");
    });
    ["schedule", "dl", "settings", "tools"].forEach(function (k) {
      ok(!prod[k], k + " 尚未实现，不能提前放行");
    });
    /* 放行只改 WIRED：未接入文案必须留着，M4-08 删本机制前 view 仍靠它给禁用提示。 */
    ok(/M\d-\d\d/.test(MC.unwired.review), "review 的未接入说明要保留并点名卡号：" + MC.unwired.review);
  });

  test("M3-07 / D4：任务接口独立放行，tools 键不再代表任务可用性", function () {
    var prod = MC.capsFor(false);
    ok(MC.capKeys.indexOf("tasks") >= 0, "tasks 已登记在 CAP_KEYS");
    ok(MC.wiredCaps.indexOf("tasks") >= 0, "tasks 已接入真实后端（M3-07）");
    ok(prod.tasks, "生产模式下 tasks 为真：「立即拉取」与两个更新按钮可用");
    ok(!prod.tools, "tools 仍只代表外部工具，M3-15 才放行");
    eq(MC.capKeys.length, 16, "能力键共 16 项");
    ok(/M\d-\d\d/.test(MC.unwired.tasks), "tasks 的未接入说明要点名卡号：" + MC.unwired.tasks);
  });

  test("M3-07 / D4：tasks 未接入时 pollTasks 与 startTask 一个请求都不发", function () {
    var calls = [];
    var origGet = MC.api.get;
    var origPost = MC.api.post;
    var origCap = MC.caps.tasks;
    var poll = MC.App.prototype.pollTasks;
    var start = MC.App.prototype.startTask;
    var stubApp = { p: function (s) { return "/p/p1" + s; }, activeTasks: [], taskSlot: null, renderTasks: function () {}, pollTasks: poll };
    try {
      MC.api.get = function (path, query) { calls.push("GET " + path); return Promise.resolve({ items: [] }); };
      MC.api.post = function (path, body) { calls.push("POST " + path); return Promise.resolve({ task: {} }); };
      MC.caps.tasks = false;
      poll.call(stubApp);
      /* startTask 走 reject 而不是「假装任务已开始」；catch 挂上以免留下未处理的拒绝。 */
      start.call(stubApp, { type: "update_all" }).then(null, function () {});
      eq(calls, [], "未接入时不得打 /tasks（决策 14：禁用 + 说明 + 不发请求）");
    } finally {
      /* 同步恢复：异步 finally 会让其余用例在整个同步批次里都用假 API（卡 3 的已知坑）。 */
      MC.api.get = origGet;
      MC.api.post = origPost;
      MC.caps.tasks = origCap;
    }
  });

  test("删除确认：给出影响数量与恢复方式，并且必须敲对文件名才能确认（§9.8.11 / UC-11）", function () {
    var calls = [];
    var origDel = MC.api.del;
    MC.api.del = function (path) { calls.push(path); return Promise.resolve({ deleted: true, recovery: "x" }); };
    var app = {
      p: function (rest) { return "/api/v1/p/default" + rest; },
      closeTab: function () {},
      refreshFiles: function () {},
      syncRevision: function () {},
    };
    try {
      MC.App.prototype.deleteFile.call(app, { name: "config.xml", archive_count: 3, record_count: 1 });
      var backdrops = document.body.querySelectorAll(".modal-backdrop");
      var backdrop = backdrops[backdrops.length - 1];
      var dialog = backdrop.querySelector(".modal");
      var text = dialog.textContent;
      ok(/3 个历史版本/.test(text) && /1 条修改记录/.test(text), "确认框要说明影响数量：" + text);
      ok(/恢复区/.test(text), "要说明删除后去哪里恢复：" + text);
      var input = dialog.querySelector("input");
      var okBtn = dialog.querySelector(".btn-danger");
      ok(input && okBtn, "危险操作要有输入确认与红色按钮");
      ok(/config\.xml/.test(text), "标题里带上被删的文件名");
      ok(okBtn.disabled, "没敲文件名之前不能确认");
      input.value = "configxm";
      input.dispatchEvent(new Event("input"));
      ok(okBtn.disabled, "打错文件名仍然禁用");
      input.value = "config.xml";
      input.dispatchEvent(new Event("input"));
      ok(!okBtn.disabled, "敲对文件名后确认按钮解锁");
      ok(calls.length === 0, "光打字不发请求，点了才发");
    } finally {
      MC.api.del = origDel;
      var left = document.body.querySelectorAll(".modal-backdrop");
      if (left.length) left[left.length - 1].remove();
    }
  });

  test("差异摘要统计：null 显示未计算，有值时给出 +/−/~ 与文字", function () {
    var none = MC.diffStats(null);
    ok(none.textContent.indexOf("未计算") >= 0, "diff 为 null 时不能崩，也不能谎报无变更");
    ok(MC.diffStats({ has_changes: false }).textContent.indexOf("无变更") >= 0);
    var stats = MC.diffStats({ has_changes: true, summary: { added: 2, removed: 1, modified: 3, bound: 4 } });
    eq(stats.getAttribute("aria-label"), "新增 2，删除 1，修改 3", "不只靠颜色，读屏要有文字");
    ok(stats.textContent.indexOf("+2") >= 0 && stats.textContent.indexOf("−1") >= 0 && stats.textContent.indexOf("~3") >= 0);
  });

  test("导入对话框：访客没有保存选项，部署者默认保存到服务器（§9.8.11 / UC-27、UC-28）", function () {
    function stub(canDeployer) {
      return {
        can: function (kind) { return kind === "deployer" && canDeployer; },
        session: { limits: { upload_max_bytes: 52428800, temp_ttl_hours: 24 } },
        files: [],
        p: function (rest) { return "/api/v1/p/default" + rest; },
      };
    }
    function choices(m) {
      return [...m.el.querySelectorAll("label.choice")].map(function (l) {
        return { text: l.textContent, checked: l.querySelector("input").checked, disabled: l.querySelector("input").disabled };
      });
    }

    var visitor = MC.openImport(stub(false), { source: "local" });
    var vc = choices(visitor);
    visitor.close();
    eq(vc.length, 2, "两种导入方式");
    ok(vc[0].text.indexOf("保存到服务器") >= 0 && vc[0].disabled === true, "访客的保存项必须禁用");
    ok(/仅部署者|服务器本机/.test(vc[0].text), "禁用时要说明原因：" + vc[0].text);
    ok(vc[1].checked === true, "访客默认落在「临时查看」");

    var deployer = MC.openImport(stub(true), { source: "local" });
    var dc = choices(deployer);
    deployer.close();
    ok(dc[0].disabled === false && dc[0].checked === true, "部署者默认可保存");
    ok(/24 小时/.test(dc[1].text), "临时项要说明过期时间");
  });

  test("文件详情：null 值节点的提交建议输入框与按钮禁用并说明只读（§6.7 D1 / UC-13）", function () {
    var app = {
      pid: "default",
      files: [],
      identity: { registered: true, name: "张三", role: "deployer", role_label: "部署者" },
      can: function () { return true; },
      p: function (rest) { return "/api/v1/p/default" + rest; },
      go: function () {},
      setTabTitle: function () {},
      renderTabbar: function () {},
      downloadFile: function () {},
      syncRevision: function () {},
      refreshFiles: function () {},
      openFile: function () {},
      startTask: function () {},
    };
    var nodes = MC.mockParse.parse(JSONTXT, "cfg.json").nodes;
    var doc = {
      format: "json",
      file: { name: "cfg.json", version: "current", updated_at: "2026-10-18T00:00:00Z", size: 64, node_count: nodes.p.length, sha256: "0123456789abcdef" },
      nodes: nodes,
    };
    var origGet = MC.api.get;
    MC.api.get = function (path) {
      if (/\/favorites$/.test(path)) return Promise.resolve({ groups: [] });
      if (/\/remarks$/.test(path)) return Promise.resolve({ items: [] });
      return Promise.resolve(doc);
    };
    /* 单独一块离屏容器，避免与 #host 上的虚拟树用例互相干扰。 */
    var stage = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0;width:600px;height:400px;display:flex" });
    document.body.append(stage);
    var inst = MC.views.f(app, { kind: "f", arg: "cfg.json", query: { node: "/a/n" } });
    stage.append(inst.el);

    function suggestSection() {
      var hit = [...inst.el.querySelectorAll(".insp-section")].filter(function (s) {
        var t = s.querySelector(".section-title");
        return t && /^修改建议/.test(t.textContent);
      })[0];
      ok(hit, "详情面板里必须渲染出「修改建议」区块（说明选中与渲染链路没断）");
      return hit;
    }
    function submitButton(sec) {
      var b = [...sec.querySelectorAll("button.btn")].filter(function (x) { return x.textContent.indexOf("提交建议") >= 0; })[0];
      ok(b, "「修改建议」区块里要有提交按钮");
      return b;
    }

    /* 文档要等 api.get 的 Promise 链跑完才落到 DOM，这里让出一个宏任务再查。 */
    return new Promise(function (resolve) {
      setTimeout(resolve, 0);
    }).then(function () {
      try {
        var head = inst.el.querySelector(".inspector-head h3");
        ok(head && head.textContent === "n", "路由 ?node= 应选中 null 节点，实际选中的是 " + (head ? head.textContent : "（无）"));
        eq(inst.el.querySelector(".inspector-head .badge").textContent, "NoneType");

        var sec = suggestSection();
        var input = sec.querySelector("input.input-mono");
        ok(input, "null 节点也要走到「输入框 + 提交按钮」这一分支（否则本用例没有验证到门控）");
        ok(input.disabled, "null 值的建议输入框必须禁用");
        ok(/null 值为只读/.test(input.title), "禁用要说明原因：" + input.title);
        var btn = submitButton(sec);
        ok(btn.disabled, "null 值的提交按钮必须禁用");
        ok(/null 值为只读/.test(btn.title), "按钮禁用也要说明原因：" + btn.title);

        /* 反例：同一文档里普通值节点的提交建议仍然可用，证明上面不是「整块都禁用」造成的假通过。 */
        inst.update({ kind: "f", arg: "cfg.json", query: { node: "/a/b~1c" } });
        var head2 = inst.el.querySelector(".inspector-head h3");
        ok(head2 && head2.textContent === "b/c", "切换到 int 节点，实际选中的是 " + (head2 ? head2.textContent : "（无）"));
        var sec2 = suggestSection();
        var input2 = sec2.querySelector("input.input-mono");
        ok(input2 && !input2.disabled, "普通值节点的输入框可用");
        ok(!input2.title, "普通值节点不该带只读提示");
        ok(!submitButton(sec2).disabled, "普通值节点的提交按钮可用");
      } finally {
        inst.destroy();
        stage.remove();
        MC.api.get = origGet;
      }
    });
  });

  /* 隔离证明（M3-06）：上一条是**异步**用例，它把 MC.api.get 换成 stub、只在自己 settle
   * 时恢复。旧机制下所有同步用例先于它跑完，这条按注册序紧随其后的用例就会看到假 API ⇒ 红。
   * 修好执行序（用例串行 settle）后它必须绿：证明恢复排在序上，不是碰运气。 */
  test("测试隔离：异步桩用例 settle 后，后续用例必须拿到真 MC.api", function () {
    ok(MC.api.get === REAL_API.get, "MC.api.get 不是真身：异步用例的 stub 泄漏到了后续用例（卡 3 的桩污染）");
    ok(MC.api.post === REAL_API.post, "MC.api.post 不是真身：桩泄漏");
    ok(MC.api.del === REAL_API.del, "MC.api.del 不是真身：桩泄漏");
  });


  test("外壳：速览与文件页恒可用，其余由 MC.caps 决定", function () {
    var capOfKind = MC.App.prototype.capOfKind;
    MC.caps = MC.capsFor(false);
    ok(capOfKind("overview") && capOfKind("f") && capOfKind("search") && capOfKind("t") && capOfKind("review"), "速览、文件、搜索、临时标签与审阅页已接入真实后端");
    ok(!capOfKind("settings"), "未接入的 kind 不可用");
    MC.caps = MC.capsFor(true);
    ok(capOfKind("settings") && capOfKind("t"), "接入后可用");
  });

  test("搜索高亮：用文本节点拆分关键词，关键字不进入 HTML（§9.8.8 / UC-25）", function () {
    var frag = MC.util.highlight('Port <img src=x onerror="window.__hl=1">', "port");
    var text = "";
    var marks = 0;
    frag.childNodes.forEach(function (n) {
      if (n.nodeName === "MARK") marks++;
      text += n.nodeType === 3 ? n.nodeValue : n.textContent || "";
    });
    window.__hl = 0;
    eq(marks, 1, "命中片段被包进 <mark>");
    eq(text, 'Port <img src=x onerror="window.__hl=1">', "原文按文本完整保留");
    eq(frag.querySelectorAll("img").length, 0, "不把关键词拼进 HTML");
    ok(!window.__hl, "载荷不执行");
    eq(MC.util.highlight("PARAM_01", "param_0").childNodes.length >= 2, true, "大小写不敏感");
    eq(MC.util.highlight("ab", "cd").querySelectorAll("mark").length, 0, "不命中时没有 mark");
  });

  test("组件：MC.menu 的未接入项真的 disabled 并带原因", function () {
    var anchor = document.createElement("button");
    document.body.appendChild(anchor);
    var pop = MC.menu(anchor, [{ label: "删除文件…", disabled: true, title: MC.unwired.delete }]);
    var item = pop.el.querySelector(".menu-item");
    ok(item.disabled, "未接入项必须真的 disabled");
    ok((item.getAttribute("title") || "").indexOf("M2-12") >= 0, "title 说明接入卡号");
    MC.closePopover();
    anchor.remove();
  });

  test("分段控件：未接入的分段真的不可点并说明原因", function () {
    var clicked = [];
    var seg = MC.segmented(
      [{ value: "tree", label: "结构" }, { value: "history", label: "历史版本", disabled: true, title: MC.unwired.history }],
      "tree",
      function (v) { clicked.push(v); },
      "文件视图"
    );
    var btns = seg.querySelectorAll("button");
    eq(btns.length, 2);
    ok(btns[1].disabled, "未接入分段必须真的 disabled");
    eq(btns[1].getAttribute("aria-disabled"), "true");
    ok((btns[1].getAttribute("title") || "").indexOf("M2-07") >= 0, "title 说明接入卡号");
    btns[1].click();
    btns[0].click();
    eq(clicked, [], "禁用分段与重复点当前分段都不触发切换");
  });

  test("虚拟树：收藏未接入时星标渲染但禁用，键盘 S 给出原因", function () {
    var idx = MC.doc.buildIndex({ nodes: MC.mockParse.parse('<R><a>1</a></R>', "s.xml").nodes });
    var blocked = 0;
    var tree = new MC.Tree(document.getElementById("host"), idx, { starReason: MC.unwired.favorites, onStarBlocked: function () { blocked++; } });
    var star = tree.el.querySelector(".tr-star");
    ok(star, "星标仍占位，行结构不因未接入而变");
    ok(star.disabled, "未接入时星标禁用");
    eq(star.getAttribute("title"), MC.unwired.favorites);
    tree.select(1);
    tree.el.dispatchEvent(new KeyboardEvent("keydown", { key: "S", bubbles: true }));
    eq(blocked, 1, "按 S 给出未接入说明而不是静默失败");
    tree.destroy();
  });

  test("显示属性开关：关闭后属性行不渲染，但 compact 数组未被裁剪", function () {
    var idx = MC.doc.buildIndex({ nodes: MC.mockParse.parse(XML, "x.xml").nodes });
    var tree = new MC.Tree(document.getElementById("host"), idx, { showAttrs: true });
    function attrRows(t) {
      return t.rows.filter(function (i) { return idx.t[i] === "attribute" && !idx.metaHidden[i]; }).length;
    }
    var n0 = idx.n;
    ok(attrRows(tree) > 0, "默认显示属性行");
    tree.setShowAttrs(false);
    eq(attrRows(tree), 0, "关闭后不渲染属性行");
    eq(idx.n, n0, "文档节点数量不变");
    ok(idx.l.indexOf("@a") >= 0, "属性仍在服务端下发的文档里");
    tree.destroy();
  });

  test("虚拟树：5 万节点全部展开时 DOM 行数不随总节点数增长", function () {
    var parts = ["<R>"];
    for (var g = 0; g < 250; g++) {
      parts.push("<g" + g + ">");
      for (var k = 0; k < 200; k++) parts.push("<v>" + (g * 1000 + k) + "</v>");
      parts.push("</g" + g + ">");
    }
    parts.push("</R>");
    var idx = MC.doc.buildIndex({ nodes: MC.mockParse.parse(parts.join(""), "big.xml").nodes });
    ok(idx.n - 1 >= 50000, "样本至少 5 万节点，实际 " + (idx.n - 1));
    var tree = new MC.Tree(document.getElementById("host"), idx, {});
    ok(tree.rows.length < 1000, "默认只展开前两层，逻辑行数 " + tree.rows.length);
    tree.expandAll();
    ok(tree.rows.length > 50000, "全部展开后逻辑行数 " + tree.rows.length);
    var domRows = tree.el.querySelectorAll(".tr").length;
    ok(domRows > 0 && domRows <= 80, "可视区 DOM 行数与总节点数无关，实测 " + domRows);
    tree.destroy();
  });

  function report() {
    var out = document.getElementById("out");
    var failed = 0;
    results.forEach(function (r) {
      var li = document.createElement("li");
      li.className = r.ok ? "pass" : "fail";
      li.textContent = (r.ok ? "✓ " : "✗ ") + r.name + (r.ok ? "" : " — " + r.err);
      if (!r.ok) failed++;
      out.appendChild(li);
    });
    var sum = document.getElementById("summary");
    sum.textContent = failed ? failed + " FAILED / " + results.length : "ALL PASSED (" + results.length + ")";
    sum.className = failed ? "fail" : "pass";
    document.title = sum.textContent;
  }

  queue.then(report);
})();
