/* 前端纯逻辑测试。新增前端算法时在此追加用例。 */
(function () {
  "use strict";
  var MC = window.MC;
  /* F2（卡 7 复审）+ 第三轮 C1：全局错误兜底与「这个兜底在什么条件下才成立」。
   * 此前这里没有任何 error/unhandledrejection 监听——「用例返回已 resolve 的 Promise
   * 之后才抛出的断言/异常」会被静默吞掉，页面照样报 ALL PASSED，正是本卡要消灭的
   * 「静默绿」再往下一层。两类失败都记成**合成失败 slot** 并立刻重画汇总——report
   * 幂等可重入，队尾那次与迟到错误谁最后跑谁定标题。
   *
   * 【钉死的验收命令 = HTTP，不是 file://】起一个临时静态服务器服务 app/web/
   *  —— `./.venv39/bin/python -m http.server 0 --bind 127.0.0.1 --directory <app/web 或其 /tmp 副本>`
   *  （随机端口；绝不拿仓库 data/ 当服务根；用完关掉），同一个预算跑：
   *    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
   *      --disable-gpu --no-sandbox --virtual-time-budget=8000 --dump-dom \
   *      "http://127.0.0.1:<port>/tests/index.html"   → 期望 <title>ALL PASSED (40)</title>
   *  【HTTP 下两种抖动，重跑而不是改代码】`--virtual-time-budget` 与真实网络延迟竞速，
   *  预算到点时 Chrome 会取消在途脚本，于是：① dump 早于 report() ⇒ 标题停在初始值
   *  （既非绿也非红）；② 某个外部 .js 没跑完 ⇒ 一批用例同时报 `X is not a function`
   *  （看着像回归，其实交付是好的：逐字节核过服务端 80/80 与磁盘一致）。**只认
   *  `ALL PASSED (n)` / `n FAILED / m` 两种标题**；①②一律重跑。实测抖动率按服务器
   *  形态排：HTTP/1.1 keep-alive 的多线程服务器 22 次 0 抖；stdlib `python -m http.server`
   *  （HTTP/1.0，每请求一条连接）24 次抖 5 次；单线程 socketserver 9 次抖 2 次
   *  ——**验收优先用 keep-alive 那种**。profile 共用与否不是变量，抬预算也不保证。
   *  完整数据与判据见 docs/know-how.md 第 4 节。
   *
   * 【为什么 file:// 不算验收】实测边界（Chrome 155，同一份页面两种来源各跑一遍，
   * 结论如实记录）：变量是**产生拒绝的那个脚本的来源**，与「串行队列在不在进行中」
   * 无关——
   *   ① **外部脚本**（本文件的全部用例、所有视图模块都是外部脚本）里产生的
   *      unhandledrejection，file:// 下**从不派发**：队列进行中产生的不派发；改到
   *      report 跑完、队列早已 settle 之后再产生，照样不派发；预算 8000→30000 也一样。
   *   ② **inline** 页面脚本产生的 rejection 在 file:// 下**会**派发 ⇒ 盲区精确到
   *      「外部脚本来源」，不是「这个页面里的所有拒绝」。
   *   ③ error 通道不受此限，file:// 下照样派发，只是外部脚本的 message/filename 被
   *      浏览器掩成 "Script error."（只有 inline 的带 filename:line，去 DevTools 找肇事行）。
   *   ④ HTTP 下两个监听都完整命中、含真实 filename:line 与消息。
   * 后果：「用例不返回那条 Promise 链、断言写在链里」这种形状在 file:// 下确定性地绿——
   * 用例注册了也启动了（用例数钉死满足）、没有同步抛（error 通道静默），能抓它的只有
   * rejection 通道，而那条通道在 file:// 下对①是瞎的。所以三层兜底（这两个监听 +
   * EXPECTED_CASES 钉死 + 队尾宏任务 flush）只有在 HTTP 命令下才是闭合的。 */
  /* F2（卡 7 复审）：钉死用例总数——某条用例「注册了却没跑到」或干脆没注册时，
   * 呈现的是更小的绿色数字而不是红。新加用例必须同步改这里（对不上当场判失败）。
   * 第三轮 nit：这条声明必须排在下面注册的两个监听**之前**——监听在脚本求值期就可能调
   * report()，那时若常量还没赋值，红会写成「预期 undefined，实际注册 0」（仍然红，
   * 但不说人话）。 */
  /* M3-09 合并轮实测：本行取的是**页面自己报出的注册数**，不是两边各自写的数字。
   * 合并前 29719a1 写 43（本卡 +1）、0dd72b1 写 42（M4-02 +2 却没同步改这里），
   * git 默认合并对这一行不报冲突（只有一侧动过它），于是 43 直接胜出、42 侧的
   * 两条用例照样注册 ⇒ 语义冲突只能靠实测拆穿。HTTP keep-alive 形态下三轮都是
   * 「预期 43，实际注册 45」，45 条全部真跑到且绿，唯一红的就是这条闸。
   * INH-1915：52 这个值在新增 fileview 门控用例后由页面重读确认（`ALL PASSED (52)`）；
   * M3-14 新增 bindings 生产用例后按页面自报数重读为 53；M3-15 新增 tools 生产用例后
   * 按页面自报数重读为 54。改了 `app/web/**` 或并过主线之后必须再重读一次，不能沿用本值。 */
  var EXPECTED_CASES = 150;  // + 连接分区、逐连接草稿与刷新后的 CAS
  var traps = [];
  function recordTrap(name, detail) {
    traps.push({ name: name, ok: false, err: detail });
    report();
  }
  window.addEventListener("error", function (e) {
    var where = e && e.filename ? e.filename.split("/").pop() + ":" + e.lineno : "file:// 下来源被掩为 Script error.，看浏览器控制台";
    var msg = (e && e.error && e.error.message) || (e && e.message) || "未知";
    recordTrap("全局脚本错误（用例流之外，位于 " + where + "）：" + msg, msg);
  });
  window.addEventListener("unhandledrejection", function (e) {
    var reason = e && e.reason;
    var msg = (reason && (reason.message || reason.code)) || String(reason);
    recordTrap("未处理的 Promise 拒绝（用例流之外）：" + msg, msg);
  });
  /* M3-06（修卡 3 遗留）：记下真 API。异步用例把 MC.api.* 换成 stub 后，
   * 后续用例开始时**必须**已经换回真身——下面这条「隔离证明」按注册序排在
   * 唯一一条异步 stub 用例（文件详情）之后，用它证明恢复是真的排到了序上。
   * F9：stubs 现在覆盖全部 9 个请求面（含 request/download/fetchBlob/upload），
   * 隔离证明跟着逐个核对。 */
  var REAL_API = { get: MC.api.get, post: MC.api.post, put: MC.api.put, patch: MC.api.patch, del: MC.api.del, request: MC.api.request, download: MC.api.download, fetchBlob: MC.api.fetchBlob, upload: MC.api.upload };
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

  test("工作区：mc.* 键按 mcc-v3-workspace:<wid>: 隔离，mc.workspace 保持全局", function () {
    var key = "mc.tabs.ws-test";
    MC.workspaces.setActive("ws-test");
    MC.util.storageSet(key, [{ k: 1 }]);
    var raw = localStorage.getItem("mcc-v3-workspace:ws-test:" + key);
    ok(raw, "scoped key 应存在");
    eq(MC.util.storageGet(key, null)[0].k, 1);
    MC.util.storageSet("mc.workspace", "ws-test");
    eq(localStorage.getItem("mc.workspace"), JSON.stringify("ws-test"));
    MC.workspaces.setActive("default");
    eq(MC.util.storageGet(key, null), null, "默认工作区不应读到 ws-test 的键");
    localStorage.removeItem("mcc-v3-workspace:ws-test:" + key);
    localStorage.removeItem("mc.workspace");
  });

  test("工作区：v3 session 返回 workspace/workspaces，configspace 旧链解析到 configv3", function () {
    var route = MC.router.parse("#/p/default/configspace");
    eq(route.kind, "configv3", "configspace 深链进入 V3 配置管理");
    return MC.api.getV3("/api/v3/session", { workspace: "default" }).then(function (s) {
      eq(s.workspace.id, "default");
      ok(Array.isArray(s.workspaces) && s.workspaces.length >= 1, "workspaces 必须非空");
      ok(s.identity && s.identity.capabilities, "identity/capabilities 必须下发");
    });
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

  /* M4-02（§16.8 第 1、2 条的前端那一半）。
   * 后端只保证「送出去的是数据」（tests/test_api_security.py），**「以文本显示、
   * 不执行」只能在真实 DOM 上判**，所以这条在这里。执法点是 util.js 的 append()
   * 走 createTextNode；把 append 换成 innerHTML 赋值，本条立刻红（nodeType 变 1、
   * img 出现、onerror 触发 alert）。
   * 必须在 HTTP 形态下跑（见文件顶部钉死的命令）：file:// 里外部脚本的
   * unhandledrejection 根本不派发，这类失败会报绿。 */
  test("注入串以文本节点渲染且不执行（§16.8 第 1 条）", function () {
    var PAYLOAD = "<img src=x onerror=alert(1)>";
    var alerted = 0;
    var realAlert = window.alert;
    window.alert = function () { alerted++; };
    var host = document.createElement("div");
    try {
      // 1) 字符串子节点（所有视图渲染标签/值/备注/工具名的唯一通道）
      var cell = MC.util.h("td", PAYLOAD);
      eq(cell.childNodes.length, 1, "只应有一个子节点");
      eq(cell.firstChild.nodeType, 3, "子节点必须是文本节点（TEXT_NODE）");
      eq(cell.firstChild.textContent, PAYLOAD, "文本必须逐字等于注入串");
      eq(cell.querySelector("img"), null, "绝不能生成 img 元素");
      host.appendChild(cell);

      // 2) 属性值（侧栏工具链接的 title 就是这条路径，shell.js:481）
      var link = MC.util.h("a", { href: "#", title: PAYLOAD, "data-name": PAYLOAD });
      eq(link.getAttribute("title"), PAYLOAD, "属性值必须原样");
      eq(link.querySelector("img"), null, "属性里注入不出元素");
      eq(link.childNodes.length, 0);
      host.appendChild(link);

      // 3) 搜索高亮（另一条会拼 DOM 的路径）
      var frag = MC.util.highlight(PAYLOAD, "img");
      var box = MC.util.h("span", {}, frag);
      eq(box.querySelector("img"), null, "高亮片段里不能出现 img");
      eq(box.textContent, PAYLOAD, "高亮后的可见文本仍是原串");
      host.appendChild(box);

      // 4) replace() 复用同一条 append 通道
      var again = MC.util.replace(MC.util.h("div"), PAYLOAD);
      eq(again.firstChild.nodeType, 3);
      host.appendChild(again);

      document.body.appendChild(host);
      eq(document.body.querySelectorAll("img").length, 0, "全页都不该因注入串多出 img");
      eq(alerted, 0, "alert 被调用过 ⇒ 内联事件真的执行了（XSS 成立）");
    } finally {
      window.alert = realAlert;
      host.remove();
    }
  });

  test("safeUrl：非 http(s) 协议一律拒绝（§16.8 第 2 条渲染侧）", function () {
    [
      "javascript:alert(1)",
      "JaVaScRiPt:alert(1)",
      " javascript:alert(1)",
      "java\tscript:alert(1)",
      "javascript:alert(1)//",
      "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
      "ftp://example.com/x",
    ].forEach(function (bad) {
      eq(MC.util.safeUrl(bad), null, "必须拒绝：" + JSON.stringify(bad));
    });
    ok(MC.util.safeUrl("http://10.0.0.9/a.xml"), "http 放行");
    ok(MC.util.safeUrl("https://example.com/b.xml"), "https 放行");
    // 相对地址会被解析成同源 http(s)：这是**允许**的（侧栏写 `/tools` 这类站内链接），
    // 如实记下这条边界，别让它被误读成「safeUrl 会拦住一切非绝对地址」。
    eq(String(MC.util.safeUrl("/inside/path")).indexOf(location.protocol), 0, "站内相对地址解析为同源");
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

  function tick() {
    return new Promise(function (resolve) {
      setTimeout(resolve, 0);
    });
  }
  function settingsApp(canDeployer) {
    /* `paints` 是 UC-34 的观察点：视图在身份变化后必须重画顶栏与侧栏，而且**不整页刷新**
     * （`go` 计数为 0、挂载的 DOM 节点还在原地）。真实的 `App` 把这两个 render 挂在
     * prototype 上（views/shell.js），这里用计数器替身，断言的是「被调用了几次」。 */
    var paints = { header: 0, nav: 0, go: 0 };
    return {
      paints: paints,
      pid: "default",
      identity: { ip: "10.12.3.21", name: canDeployer ? "部署者甲" : "李娜", registered: true, is_deployer: !!canDeployer, is_admin: !!canDeployer, role_label: canDeployer ? "部署者" : "成员" },
      can: function (kind) { return kind === "deployer" && !!canDeployer; },
      session: { profiles: [{ id: "default", name: "默认机型" }] },
      files: [],
      activeTasks: [],
      taskSlot: MC.util.h("div"),
      p: function (rest) { return "/p/default" + rest; },
      go: function () { paints.go++; },
      renderHeader: function () { paints.header++; },
      renderNav: function () { paints.nav++; },
      openFile: function () {},
      switchProfile: function () {},
      reloadSession: function () { return Promise.resolve(); },
      syncRevision: function () { return Promise.resolve(); },
      refreshFiles: function () { return Promise.resolve(); },
      refreshTools: function () { return Promise.resolve(); },
      renderTasks: MC.App.prototype.renderTasks,
      pollTasks: function () { return Promise.resolve(); },
      showTask: function (id) { return MC.App.prototype.showTask.call(this, id); },
      startTask: function (body) { return MC.App.prototype.startTask.call(this, body); },
    };
  }
  function stubApi(handlers) {
    /* F9（卡 7 复审）：桩覆盖面 = MC.api 的全部请求面，不止五个动词包装。
     * 「未接入分段一个请求都不发」的断言宽度取决于这里拦了多少门——
     * request/download/fetchBlob/upload 过去无人拦，结论只对已拦的成立。
     * （两个可选方案里选了「加宽桩」而不是「静态断言视图只用五个动词」，
     * 因为前者对未来的视图改动仍然有效。） */
    var orig = { get: MC.api.get, post: MC.api.post, put: MC.api.put, patch: MC.api.patch, del: MC.api.del, request: MC.api.request, download: MC.api.download, fetchBlob: MC.api.fetchBlob, upload: MC.api.upload, getV3: MC.api.getV3, requestV3: MC.api.requestV3 };
    var calls = [];
    function mk(method) {
      return function (path, arg) {
        calls.push(method + " " + path + (arg !== undefined ? " " + JSON.stringify(arg) : ""));
        var h = handlers[method];
        var r = h ? h(path, arg) : Promise.resolve({ items: [] });
        return r;
      };
    }
    MC.api.get = mk("GET");
    MC.api.post = mk("POST");
    MC.api.put = mk("PUT");
    MC.api.patch = mk("PATCH");
    MC.api.del = mk("DELETE");
    MC.api.request = function (method, path, opts) {
      calls.push(method + " " + path + (opts && opts.body !== undefined ? " " + JSON.stringify(opts.body) : ""));
      return handlers.request ? handlers.request(method, path, opts) : Promise.resolve({ items: [] });
    };
    MC.api.download = function (path) { calls.push("DOWNLOAD " + path); return Promise.resolve({ blob: null, filename: "" }); };
    MC.api.fetchBlob = function (path) { calls.push("FETCHBLOB " + path); return Promise.resolve({ blob: null, filename: "" }); };
    MC.api.upload = function (path) { calls.push("UPLOAD " + path); return Promise.resolve({}); };
    MC.api.getV3 = function (path, query) {
      calls.push("GETV3 " + path + (query !== undefined ? " " + JSON.stringify(query) : ""));
      return handlers.getV3 ? handlers.getV3(path, query) : Promise.resolve({ items: [] });
    };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push("V3 " + method + " " + path + (opts && opts.body !== undefined ? " " + JSON.stringify(opts.body) : ""));
      return handlers.requestV3 ? handlers.requestV3(method, path, opts) : Promise.resolve({});
    };
    return { calls: calls, restore: function () { Object.keys(orig).forEach(function (k) { MC.api[k] = orig[k]; }); } };
  }
  function cleanOverlays() {
    MC.closePopover();
    document.querySelectorAll(".modal-backdrop").forEach(function (b) { b.remove(); });
    /* 只清空 toast 内容、不删 .toast-stack 容器：components.js 复用同一个容器变量，
     * 删掉容器会让后续用例的 toast 落到游离节点上。 */
    document.querySelectorAll(".toast").forEach(function (t) { t.remove(); });
  }
  var SOURCE_LIST = {
    items: [
      { name: "a.xml", url: "http://up/a.xml", record_url: "", local_updated_at: "2026-10-18 10:00:00", last_result: { status: "success", label: "已更新", reason: "已归档旧版本 a_20261017.xml", at: "2026-10-18 10:00:00", archived: "a_20261017.xml" } },
      { name: "b.xml", url: "http://up/b.xml", record_url: "http://up/b_rec.xml", local_updated_at: null, last_result: { status: "rejected", label: "已拒绝", reason: "根节点由 R 变为 html，可能下载到了错误页面，已拒绝替换", at: "2026-10-17 09:00:00", archived: null } },
      { name: "c.xml", url: "http://up/c.xml", record_url: "", local_updated_at: "2026-10-18 08:00:00", last_result: { status: "download_failed", label: "下载失败", reason: "连接超时（30 秒），已重试 3 次", at: "2026-10-18 08:00:00", archived: null } },
      { name: "d.xml", url: "http://up/d.xml", record_url: "", local_updated_at: "2026-10-16 08:00:00", last_result: { status: "unchanged", label: "无变化", reason: "内容与当前版本一致，未归档", at: "2026-10-16 08:00:00", archived: null } },
    ],
  };
  function sourceHandlers(patchImpl) {
    return {
      GET: function (path) {
        if (/\/sources$/.test(path)) return Promise.resolve(JSON.parse(JSON.stringify(SOURCE_LIST)));
        return Promise.resolve({ items: [] });
      },
      POST: function () { return Promise.resolve({ task: { id: "tk1", type: "update_source", target: "a.xml", state: "running", progress: { done: 0, total: 0 }, result: { items: [] }, actor: "部署者甲" } }); },
      PATCH: patchImpl,
    };
  }
  /* 统一的生产模式挂载器：mock 位、视图实例、API 桩、caps 覆盖、浮层 DOM 无论用例
   * 成败都在 settle 阶段恢复——执行序已串行，这就是「后续用例拿到真 API」的机制保证。 */
  function productionRun() {
    var wasMock = MC.config.mock;
    var stage = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0;width:900px;height:600px" });
    document.body.append(stage);
    MC.config.mock = false;
    var insts = [];
    var restoreFns = [];
    var activeRestore = null;
    var r = {
      stage: stage,
      mount: function (app, seg) {
        var inst = MC.views.settings(app, { kind: "settings", arg: seg, query: {} });
        stage.append(inst.el);
        insts.push(inst);
        return inst;
      },
      /* 登记「非设置页」的视图实例（F1 用例挂 fileview 用）：stage/destroy 语义与 mount 一致。 */
      mountView: function (inst) {
        stage.append(inst.el);
        insts.push(inst);
        return inst;
      },
      useApi: function (handlers) {
        /* 同一时刻只允许一层桩：先摘掉上一层，新桩才会捕获到「真 API」作为恢复目标，
         * 恢复顺序也就天然正确。 */
        if (activeRestore) activeRestore();
        var s = stubApi(handlers);
        activeRestore = s.restore;
        restoreFns.push(s.restore);
        return s;
      },
    };
    function finish() {
      MC.config.mock = wasMock;
      insts.forEach(function (i) { i.destroy(); });
      stage.remove();
      cleanOverlays();
      restoreFns.forEach(function (f) { f(); });
    }
    r.run = function (body) {
      var p;
      try {
        p = Promise.resolve(body(r));
      } catch (e) {
        p = Promise.reject(e);
      }
      return p
        .catch(function (e) {
          finish();
          throw e;
        })
        .then(finish);
    };
    return r;
  }

  test("M3-16：profiles 分段在生产模式读 /session，列出机型；成员的新增/改名/删除全部禁用", function () {
    var t = productionRun();
    return t.run(function (r) {
      var models = [
        { id: "default", name: "默认机型" },
        { id: "p1", name: "产线一" },
      ];
      var spy = r.useApi({
        GET: function (path) {
          if (/\/session$/.test(path)) return Promise.resolve({ profile: models[0], profiles: models, server: {} });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mount(settingsApp(true), "profiles");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        eq(spy.calls, ['GET /session {"profile":"default"}'], "进入已接入的 profiles 只发这一个请求");
        ok(main.textContent.indexOf("默认机型") >= 0 && main.textContent.indexOf("产线一") >= 0, "两个机型都要列出");
        var add = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("新增机型") >= 0; })[0];
        ok(add && !add.disabled, "部署者可以新增机型");
        var rename = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent === "重命名"; })[0];
        ok(rename && !rename.disabled, "部署者可以重命名");
        var inst2 = r.mount(settingsApp(false), "profiles");
        return tick().then(function () {
          var main2 = inst2.el.querySelector(".settings-main");
          var add2 = [].slice.call(main2.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("新增机型") >= 0; })[0];
          ok(add2 && add2.disabled, "非部署者的新增按钮必须禁用");
          ok(/管理员/.test(add2.getAttribute("title") || ""), "禁用要说明原因，实际：" + (add2 ? add2.getAttribute("title") : "<没有按钮>"));
          var rename2 = [].slice.call(main2.querySelectorAll("button")).filter(function (b) { return b.textContent === "重命名"; })[0];
          ok(rename2 && rename2.disabled, "非部署者的重命名按钮必须禁用");
          var del2 = [].slice.call(main2.querySelectorAll("button")).filter(function (b) { return b.textContent === "删除"; })[0];
          ok(del2 && del2.disabled, "非部署者的删除按钮必须禁用");
        });
      });
    });
  });

  test("M10-01：非 legacy 工作区的机型列表与改名走 v3 工作区端点", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("w1");
    var t = productionRun();
    return t.run(function (r) {
      var models = [
        { id: "default", name: "默认机型" },
        { id: "p1", name: "产线一" },
      ];
      var spy = r.useApi({
        getV3: function (path) {
          eq(path, "/api/v3/workspaces/w1/profiles", "列表要按当前工作区取机型");
          return Promise.resolve({ items: models });
        },
        requestV3: function (method, path, opts) {
          return Promise.resolve({ profile: { id: "p1", name: opts.body.name } });
        },
      });
      var inst = r.mount(settingsApp(true), "profiles");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        ok(main.textContent.indexOf("产线一") >= 0, "v3 返回的机型要列出");
        eq(spy.calls, ["GETV3 /api/v3/workspaces/w1/profiles"], "非 legacy 工作区不得再读 v1 /session");
        var rename = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent === "重命名"; })[1];
        rename.click();
        return tick().then(function () {
          modalField("机型名称").control.value = "产线一改";
          var save = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.indexOf("保存") >= 0; })[0];
          save.click();
          return tick().then(function () {
            eq(spy.calls[1], 'V3 PATCH /api/v3/workspaces/w1/profiles/p1 {"name":"产线一改"}', "改名必须走 v3 工作区端点");
            var switched = null;
            var fake = {
              pid: "p1",
              wid: "w1",
              session: { profile: { name: "产线一改" } },
              instances: {},
              confirmContext: function () { return Promise.resolve(true); },
              saveTabs: function () {},
              saveActiveRoute: function () {},
              destroyInstance: function () {},
              go: function () {},
              pollTasks: function () {},
              startPolling: function () {},
              loadSession: function (pid, wid) {
                switched = [pid, wid];
                this.pid = pid;
                this.session = { profile: { name: "产线一改" } };
                return Promise.resolve();
              },
            };
            return MC.App.prototype.switchProfile.call(fake, "p2").then(function () {
              eq(switched.join(","), "p2,w1", "切换机型必须留在原工作区");
            });
          });
        });
      });
    }).then(function () {
      MC.workspaces.setActive(prev);
    }, function (e) {
      MC.workspaces.setActive(prev);
      throw e;
    });
  });

  test("M10-02：切换工作区后晚到的文件/工具响应不得覆盖当前 W", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("w1");
    var origGet = MC.api.get;
    var origEmit = MC.bus.emit;
    var pending = [];
    var emitted = [];
    MC.api.get = function (path) {
      return new Promise(function (resolve) { pending.push({ path: path, resolve: resolve }); });
    };
    MC.bus.emit = function (name) { emitted.push(name); };
    var renders = 0;
    var fake = {
      pid: "default",
      files: [],
      tools: [],
      p: function (rest) { return "/p/default" + rest; },
      renderFileList: function () { renders++; },
      renderNav: function () { renders++; },
    };
    var pFiles = MC.App.prototype.refreshFiles.call(fake);
    var pTools = MC.App.prototype.refreshTools.call(fake);
    eq(pending.map(function (x) { return x.path; }), ["/p/default/files", "/p/default/tools"], "两个请求都要先发出");
    MC.workspaces.bumpEpoch();
    pending[0].resolve({ items: [{ name: "stale.xml" }] });
    pending[1].resolve({ items: [{ name: "stale-tool" }] });
    function restore() {
      MC.api.get = origGet;
      MC.bus.emit = origEmit;
      MC.workspaces.setActive(prev);
    }
    return Promise.all([pFiles, pTools]).then(function () {
      eq(fake.files.length, 0, "旧 W 的文件列表不得落到新 W");
      eq(fake.tools.length, 0, "旧 W 的工具列表不得落到新 W");
      eq(renders, 0, "过期响应不得触发重画");
      eq(emitted.length, 0, "过期响应不得发 changed/files 事件");
      restore();
    }, function (e) {
      restore();
      throw e;
    });
  });

  test("M11-02：文本格式文件页显示原文并标明不支持结构树", function () {
    var t = productionRun();
    var origGet = MC.api.get;
    var origFetch = MC.api.fetchBlob;
    return t.run(function (r) {
      MC.api.get = function () {
        return Promise.reject(new MC.ApiError(422, "PARSE_FAILED", "该格式不支持结构树", { unsupported: true }));
      };
      MC.api.fetchBlob = function () {
        return Promise.resolve({ blob: { text: function () { return Promise.resolve("key=value\n# 注释\n"); } }, filename: "a.conf" });
      };
      var app = settingsApp(true);
      app.files = [{ name: "a.conf", format: "text", size: 20, updated_at: "2026-10-29 10:00:00", mapped: false, archive_count: 0, record_count: 0, pending_remarks: 0 }];
      var inst = r.mountView(MC.views.f(app, { kind: "f", arg: "a.conf", query: {} }));
      function waitRaw(n) {
        return tick().then(function () {
          if (inst.el.textContent.indexOf("key=value") >= 0) return true;
          if (!n) return false;
          return waitRaw(n - 1);
        });
      }
      return waitRaw(20).then(function () {
        var text = inst.el.textContent;
        ok(text.indexOf("不支持结构树") >= 0, "要清楚标 unsupported：" + text.slice(0, 200));
        ok(text.indexOf("key=value") >= 0, "原文要显示出来");
        ok(inst.el.querySelector("pre.raw-text"), "原文面板");
        var seg = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent === "原文"; });
        ok(seg.length === 1, "视图标签改为「原文」");
      });
    }).then(function () {
      MC.api.get = origGet;
      MC.api.fetchBlob = origFetch;
    }, function (e) {
      MC.api.get = origGet;
      MC.api.fetchBlob = origFetch;
      throw e;
    });
  });

  test("M12-07：配置管理原生视图按 W/P 读取固定配置，非管理员显示只读横幅与五个 tab", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var spy = r.useApi({
        getV3: function (path) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 0 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [], edges: [] });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) return Promise.resolve({ revision: 0, items: [], total: 0 });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: null, baselines: [] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mountView(MC.views.configv3(settingsApp(false), { query: { config: "cfg-1" } }));
      function wait(n) {
        return tick().then(function () {
          if (inst.el.textContent.indexOf("一号配置") >= 0) return true;
          if (!n) return false;
          return wait(n - 1);
        });
      }
      return wait(20).then(function () {
        var text = inst.el.textContent;
        ok(inst.el.querySelector('[aria-label="配置管理导航"]'), "保留配置管理导航");
        ok(text.indexOf("一号配置") >= 0 && text.indexOf("MC-001") >= 0, "配置列表与标识");
        ok(inst.el.querySelector(".banner-warning") && text.indexOf("只读模式，编辑需要管理员权限") >= 0, "非管理员只读横幅保留明确的编辑权限说明");
        ["版本演进", "参数工作台", "参数总览", "模板导出", "配置历史"].forEach(function (label) {
          ok(text.indexOf(label) >= 0, "缺少 tab：" + label);
        });
        ok(spy.calls.filter(function (c) { return c.indexOf("GETV3 " + base) === 0; }).length >= 2, "必须走 v3 W/P 配置端点");
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M13-05：提取 modal 50/100 分页、筛选与逐行决议保留", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var release = {
        release_id: "rel-1", version: "690", order: 1, predecessor_id: null,
        created_at: "2026-10-29 10:00:00", definition_revision: 1,
        default_revision_id: null, default_frozen_at: null,
        d0: { values: {}, frozen: false, revision: 0 },
      };
      function planRows() {
        var rows = [];
        var i;
        for (i = 0; i < 80; i++) rows.push({ row_id: "n" + i, display_path: "/G/p" + i, leaf_name: "Param" + i, directory: ["G"], status: "new", suggested_action: "add", suggested_type: "number", value_kind: "number", candidates: [], type_options: [] });
        for (i = 0; i < 25; i++) rows.push({ row_id: "e" + i, display_path: "/L/e" + i, leaf_name: "Existing" + i, directory: ["L"], status: "existing", suggested_action: "link", suggested_type: "string", existing_parameter_id: "p-e" + i, existing_parameter: { parameter_id: "p-e" + i, name: "Existing" + i, directory: ["L"], type: "string" }, candidates: [], type_options: [] });
        for (i = 0; i < 10; i++) rows.push({ row_id: "c" + i, display_path: "/C/d" + i, leaf_name: "Dup" + i, directory: ["C"], status: i < 3 ? "same_name_other_path" : i < 6 ? "ambiguous" : i < 8 ? "type_conflict" : "null_uncertain", suggested_action: "pending", suggested_type: i < 8 ? "number" : "", value_kind: i < 8 ? "number" : "null", existing_parameter_id: null, candidates: i < 3 ? [{ parameter_id: "p-c" + i, name: "Dup" + i, directory: ["Old"], type: "string" }] : i < 6 ? [{ parameter_id: "p-x" + i, name: "Dup" + i, directory: ["X"], type: "string" }, { parameter_id: "p-y" + i, name: "Dup" + i, directory: ["Y"], type: "string" }] : [], type_options: i >= 8 ? ["string", "number", "boolean", "decimal"] : [] });
        for (i = 0; i < 5; i++) rows.push({ row_id: "u" + i, display_path: "/U/@v" + i, leaf_name: "Attr" + i, directory: ["U"], status: "unsupported", suggested_action: "skip", suggested_type: "", value_kind: "string", candidates: [], type_options: [] });
        return rows;
      }
      function queryOf(path) {
        var q = {};
        String(path.split("?")[1] || "").split("&").forEach(function (part) {
          var kv = part.split("=");
          if (kv[0]) q[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || "");
        });
        return q;
      }
      var allRows = planRows();
      var decisions = {};
      var counts = { total: allRows.length, new: 80, existing: 25, same_name_other_path: 3, ambiguous: 3, type_conflict: 2, null_uncertain: 2, unsupported: 5, conflict: 10 };
      function defaultDecision(row) {
        if (row.status === "new") return { action: "add", type: row.suggested_type || "", parameter_id: "" };
        if (row.status === "existing") return { action: "link", type: row.suggested_type || "", parameter_id: row.existing_parameter_id || "" };
        if (row.status === "unsupported") return { action: "skip", type: "", parameter_id: "" };
        return { action: "pending", type: row.suggested_type || "", parameter_id: row.existing_parameter_id || "" };
      }
      function effective(row) { return decisions[row.row_id] || defaultDecision(row); }
      function pendingCount() {
        return allRows.filter(function (row) {
          var d = effective(row);
          var conflict = ["same_name_other_path", "ambiguous", "type_conflict", "null_uncertain"].indexOf(row.status) >= 0;
          return conflict && d.action === "pending";
        }).length;
      }
      function planSummary() {
        return { plan_id: "ep-1", config_id: "cfg-1", status: "open", stale: false,
          source: { name: "scenario.json", version_id: "v-1", file_id: "f-1" }, revision: 3,
          counts: counts, pending: pendingCount(), created_at: "2026-10-29 10:00:00" };
      }
      r.useApi({
        getV3: function (path, extra) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 1 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [release] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [], edges: [] });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) return Promise.resolve({ revision: 1, items: [], total: 0 });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: null, baselines: [] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          if (path === "/api/v3/workspaces/default/profiles/default/file-selector") {
            return Promise.resolve({ items: [{ name: "scenario.json", file_id: "f-1", version_id: "v-1", format: "json" }], total: 1 });
          }
          if (path.indexOf(base + "/cfg-1/extraction-plans/ep-1/rows") === 0) {
            var q = queryOf(path);
            var filter = q.filter || "all";
            var filtered = allRows.filter(function (row) {
              if (filter === "new") return row.status === "new";
              if (filter === "existing") return row.status === "existing";
              if (filter === "conflict") return ["same_name_other_path", "ambiguous", "type_conflict", "null_uncertain"].indexOf(row.status) >= 0;
              return true;
            });
            var limit = +q.limit || 50;
            var offset = +q.offset || 0;
            var page = Object.assign({}, planSummary(), {
              filter: filter, q: q.q || "", total: filtered.length, offset: offset, limit: limit,
              filter_digest: "d-" + filter + "-" + filtered.length,
              items: filtered.slice(offset, offset + limit).map(function (row) {
                return Object.assign({}, row, { decision: effective(row) });
              }),
            });
            return Promise.resolve(page);
          }
          return Promise.resolve({ items: [] });
        },
        requestV3: function (method, path, opts) {
          if (method === "POST" && path === base + "/cfg-1/extraction-plans") {
            return Promise.resolve({ plan: planSummary() });
          }
          if (method === "PATCH" && path.indexOf("/extraction-plans/ep-1/decisions") >= 0) {
            var body = opts.body || {};
            var updated = 0;
            (body.row_ids || []).forEach(function (id) {
              decisions[id] = { action: body.decision.action, type: body.decision.type || "", parameter_id: body.decision.parameter_id || "" };
              updated++;
            });
            return Promise.resolve(Object.assign({}, planSummary(), { updated: updated }));
          }
          if (method === "POST" && path.indexOf("/extraction-plans/ep-1/recompute") >= 0) {
            return Promise.resolve({ plan: planSummary(), preserved: Object.keys(decisions).length });
          }
          if (method === "POST" && path.indexOf("/extraction-plans/ep-1/commit") >= 0) {
            return Promise.resolve({ result: { created: 80, unchanged: 40, skipped: 0, bound: 0, replayed: false } });
          }
          return Promise.resolve({});
        },
      });
      var testApp = settingsApp(true);
      testApp.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(testApp, { query: { config: "cfg-1", tab: "workbench" } }));
      function waitFor(fn, n) {
        return tick().then(function () {
          if (fn()) return true;
          if (!n) return false;
          return waitFor(fn, n - 1);
        });
      }
      function modalText() {
        var m = document.querySelector(".modal");
        return m ? m.textContent : "";
      }
      function modalRows() {
        return document.querySelectorAll(".modal tbody tr").length;
      }
      function clickText(text) {
        var btn = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.trim() === text; })[0];
        if (btn) btn.click();
        return !!btn;
      }
      function clickView(text) {
        var btn = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent.trim() === text; })[0];
        if (btn) btn.click();
        return !!btn;
      }
      return waitFor(function () { return inst.el.textContent.indexOf("从文件提取参数") >= 0; }, 20).then(function () {
        clickView("从文件提取参数");
        return waitFor(function () { return modalText().indexOf("预览参数") >= 0; }, 20);
      }).then(function () {
        clickText("预览参数");
        return waitFor(function () { return modalText().indexOf("计划 120 行") >= 0; }, 20);
      }).then(function () {
        var mEl = document.querySelector(".modal"); ok(modalRows() === 50, "默认 50 行/页，实际 " + modalRows());
        ok(modalText().indexOf("待决议 10 行") >= 0, "初始冲突计数");
        ok(modalText().indexOf("补齐已有参数缺失绑定") >= 0, "补绑显式开关");
        ok(modalText().indexOf("加入当前软件版本（未设值）") >= 0, "可选成员开关");
        var apply = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.indexOf("提交新增") >= 0; })[0];
        ok(apply && apply.disabled, "有冲突时应用按钮必须禁用");
        clickText("下一页");
        return waitFor(function () { return modalText().indexOf("第 2 / 3 页") >= 0; }, 10);
      }).then(function () {
        ok(modalRows() === 50, "第二页 50 行");
        var size = document.querySelector('.modal select[aria-label="每页行数"]');
        ok(size, "每页行数选择器");
        size.value = "100";
        size.dispatchEvent(new Event("change", { bubbles: true }));
        return waitFor(function () { return modalText().indexOf("第 1 / 2 页") >= 0; }, 10);
      }).then(function () {
        ok(modalRows() === 100, "100 行/页");
        clickText("冲突");
        return waitFor(function () { return modalText().indexOf("第 1 / 1 页") >= 0; }, 10);
      }).then(function () {
        ok(modalRows() === 10, "冲突筛选 10 行");
        var first = [].slice.call(document.querySelectorAll('.modal select[aria-label="逐行决议"]')).filter(function (s) { return s.value === "pending"; })[0];
        ok(first, "冲突行默认待决议");
        first.value = "skip";
        first.dispatchEvent(new Event("change", { bubbles: true }));
        return waitFor(function () { return modalText().indexOf("待决议 9 行") >= 0; }, 20);
      }).then(function () {
        ok(modalText().indexOf("待决议 9 行") >= 0, "单行决议后计数减一");
        clickText("全部");
        return waitFor(function () { return modalText().indexOf("计划 120 行") >= 0; }, 10);
      }).then(function () {
        clickText("冲突");
        return waitFor(function () { return modalRows() === 10; }, 10);
      }).then(function () {
        ok(modalText().indexOf("待决议 9 行") >= 0, "切换筛选后决议必须保留");
        function resolveNext(n) {
          if (!n) return Promise.resolve(true);
          var sel = [].slice.call(document.querySelectorAll('.modal select[aria-label="逐行决议"]')).filter(function (s) { return s.value === "pending"; })[0];
          if (!sel) return Promise.resolve(false);
          sel.value = "skip";
          sel.dispatchEvent(new Event("change", { bubbles: true }));
          var target = n - 1 === 0 ? "冲突已全部决议" : "待决议 " + (n - 1) + " 行";
          return waitFor(function () { return modalText().indexOf(target) >= 0; }, 20).then(function (okDone) {
            return okDone ? resolveNext(n - 1) : false;
          });
        }
        return resolveNext(9);
      }).then(function () {
        ok(modalText().indexOf("冲突已全部决议") >= 0, "全部决议后计数：" + modalText());
        var apply = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.indexOf("提交新增") >= 0; })[0];
        ok(apply && !apply.disabled, "决议齐备后可提交");
        apply.click();
        return waitFor(function () { return !document.querySelector(".modal"); }, 20);
      }).then(function () {
        ok(!document.querySelector(".modal"), "提交成功后 modal 关闭");
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M14-02：合并 modal 三栏 O/L/N、逐行决议与生成新草稿", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var release = { release_id: "rel-1", version: "690", order: 1, predecessor_id: null, created_at: "2026-10-29 10:00:00", definition_revision: 1, default_revision_id: "d0-rel-1", default_frozen_at: "2026-10-29 10:00:00", d0: { values: {}, frozen: true, revision: 0 } };
      var row = { parameter_id: "p-1", name: "P1", action: "conflict", base: { value: { text: "1", type: "decimal" } }, ours: { value: { text: "9", type: "decimal" } }, theirs: { value: { text: "3", type: "decimal" } }, decision: "pending", allowed_decisions: ["adopt_target", "keep_local", "custom"] };
      var decision = "pending";
      r.useApi({
        getV3: function (path, extra) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 1 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [release] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [{ release_id: "rel-1", version: "690", order: 1, predecessor_id: null, d0_frozen: true }], edges: [] });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) return Promise.resolve({ revision: 1, items: [], total: 0 });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: "bl-1", baselines: [{ baseline_id: "bl-1", label: "B1" }] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          if (path.indexOf("/merge-plans/mp-1/rows") >= 0) {
            return Promise.resolve({ plan_id: "mp-1", config_id: "cfg-1", status: "open", stale: false, mode: "main_cross_version", total: 1, pending: decision === "pending" ? 1 : 0, filter: "conflict", items: [Object.assign({}, row, { decision: decision })] });
          }
          return Promise.resolve({ items: [] });
        },
        requestV3: function (method, path, opts) {
          if (method === "POST" && /\/merge-plans$/.test(path)) {
            return Promise.resolve({ plan: { plan_id: "mp-1", config_id: "cfg-1", status: "open", stale: false, mode: "main_cross_version", pending: 1 } });
          }
          if (method === "PATCH" && path.indexOf("/merge-plans/mp-1/decisions") >= 0) {
            decision = opts.body.decision;
            return Promise.resolve({ plan_id: "mp-1", pending: decision === "pending" ? 1 : 0 });
          }
          if (method === "POST" && path.indexOf("/merge-plans/mp-1/commit") >= 0) {
            return Promise.resolve({ draft: { draft_id: "bd-1", kind: "baseline" } });
          }
          return Promise.resolve({});
        },
      });
      var testApp = settingsApp(true);
      testApp.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(testApp, { query: { config: "cfg-1", tab: "workbench" } }));
      function waitFor(fn, n) {
        return tick().then(function () { if (fn()) return true; if (!n) return false; return waitFor(fn, n - 1); });
      }
      function modalText() { var m = document.querySelector(".modal"); return m ? m.textContent : ""; }
      function clickModal(text) {
        var btn = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.trim() === text; })[0];
        if (btn) btn.click();
        return !!btn;
      }
      return waitFor(function () { return inst.el.textContent.indexOf("合并配置") >= 0; }, 20).then(function () {
        var btn = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent.trim() === "合并配置"; })[0];
        ok(btn, "合并入口");
        btn.click();
        return waitFor(function () { return modalText().indexOf("预览合并") >= 0; }, 20);
      }).then(function () {
        ok(document.querySelector('.modal select[aria-label="共同基准 O"]') && document.querySelector('.modal select[aria-label="当前配置 L"]') && document.querySelector('.modal select[aria-label="目标配置 N"]'), "三方输入选择真实修订，无需手填修订编号");
        clickModal("预览合并");
        return waitFor(function () { return modalText().indexOf("待决议 1 行") >= 0; }, 20);
      }).then(function () {
        ok(modalText().indexOf("O 基准") >= 0 && modalText().indexOf("L 本地") >= 0 && modalText().indexOf("N 目标") >= 0, "三栏");
        var sel = document.querySelector('.modal select[aria-label="合并决议"]');
        ok(sel, "逐行决议");
        sel.value = "keep_local";
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        return waitFor(function () { return modalText().indexOf("冲突已全部决议") >= 0; }, 20);
      }).then(function () {
        var commit = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.indexOf("生成新草稿") >= 0; })[0];
        ok(commit && !commit.disabled, "决议齐备后可提交");
        commit.click();
        return waitFor(function () { return !document.querySelector(".modal"); }, 20);
      }).then(function () {
        ok(!document.querySelector(".modal"), "提交后关闭");
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M15-05：模板导出与配置历史 tab 渲染", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var release = { release_id: "rel-1", version: "690", order: 1, predecessor_id: null, created_at: "2026-10-29 10:00:00", definition_revision: 1, default_revision_id: "d0-rel-1", default_frozen_at: "2026-10-29 10:00:00", d0: { values: {}, frozen: true, revision: 0 } };
      r.useApi({
        getV3: function (path, extra) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 1 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [release] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [], edges: [] });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) return Promise.resolve({ revision: 1, items: [], total: 0 });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: "bl-1", baselines: [{ baseline_id: "bl-1", label: "B1", release_id: "rel-1" }] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          if (path === "/api/v3/workspaces/default/profiles/default/file-selector") return Promise.resolve({ items: [{ name: "tpl.json", version_id: "v-1", file_id: "f-1", format: "json" }] });
          if (path === base + "/cfg-1/events") return Promise.resolve({ items: [{ event_id: "ev-1", type: "merge_commit", principal: "验收管理员", ip: "127.0.0.1", at: "2026-10-29 10:00:00", request_id: "r1", source: {} }] });
          return Promise.resolve({ items: [] });
        },
      });
      var testApp = settingsApp(true);
      testApp.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(testApp, { query: { config: "cfg-1" } }));
      function waitFor(fn, n) { return tick().then(function () { if (fn()) return true; if (!n) return false; return waitFor(fn, n - 1); }); }
      function tab(text) {
        var b = [].slice.call(inst.el.querySelectorAll("button")).filter(function (x) { return x.textContent.trim() === text; })[0];
        ok(b, "缺少 tab " + text); b.click();
      }
      return waitFor(function () { return inst.el.textContent.indexOf("配置历史") >= 0; }, 20).then(function () {
        tab("模板导出");
        return waitFor(function () { return inst.el.textContent.indexOf("预览导出") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("配置修订") >= 0 && inst.el.querySelector('[aria-label="模板文件"]'), "导出控件与可访问名称");
        ok(inst.el.textContent.indexOf("tpl.json") >= 0, "模板文件选择");
        tab("配置历史");
        return waitFor(function () { return inst.el.textContent.indexOf("merge_commit") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("验收管理员") >= 0, "事件操作人");
        ok(inst.el.textContent.indexOf("从该修订恢复") < 0, "无 revision_id 的事件不应显示恢复");
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M15-09：配置历史失败显示错误块并可重试", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var release = { release_id: "rel-1", version: "690", order: 1, predecessor_id: null, created_at: "2026-10-29 10:00:00", definition_revision: 1, default_revision_id: null, default_frozen_at: null, d0: { values: {}, frozen: false, revision: 0 } };
      var fail = true;
      r.useApi({
        getV3: function (path, extra) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 1 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [release] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [], edges: [] });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) return Promise.resolve({ revision: 0, items: [], total: 0 });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: null, baselines: [] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          if (path.indexOf("/cfg-1/events") >= 0) {
            return fail ? Promise.reject({ code: "STORAGE_FAILURE", message: "模拟失败", status: 500 }) : Promise.resolve({ items: [] });
          }
          return Promise.resolve({ items: [] });
        },
      });
      var testApp = settingsApp(true);
      testApp.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(testApp, { query: { config: "cfg-1" } }));
      function waitFor(fn, n) { return tick().then(function () { if (fn()) return true; if (!n) return false; return waitFor(fn, n - 1); }); }
      return waitFor(function () { return inst.el.textContent.indexOf("配置历史") >= 0; }, 20).then(function () {
        var tab = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent.trim() === "配置历史"; })[0];
        ok(tab, "配置历史 tab"); tab.click();
        return waitFor(function () { return inst.el.textContent.indexOf("读取配置事件失败") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("读取配置事件失败") >= 0, "失败要显示错误块");
        var retry = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent.trim() === "重试"; })[0];
        ok(retry, "错误块要有重试");
        fail = false; retry.click();
        return waitFor(function () { return inst.el.textContent.indexOf("没有配置事件") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("没有配置事件") >= 0, "重试后恢复空态");
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M13-PERF：50k 模式列表/目录/总览只取当前页", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var release = { release_id: "rel-1", version: "690", order: 1, predecessor_id: null, created_at: "2026-10-29 10:00:00", definition_revision: 1, default_revision_id: null, default_frozen_at: null, d0: { values: {}, frozen: false, revision: 0 } };
      function q(path) {
        var out = {}, tail = String(path.split("?")[1] || "");
        tail.split("&").forEach(function (part) { var kv = part.split("="); if (kv[0]) out[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || ""); });
        return out;
      }
      function rows(prefix, offset, limit) {
        var items = [];
        for (var i = 0; i < limit; i++) items.push({ parameter_id: "p-" + (offset + i), name: prefix + (offset + i), directory: ["G0"], type: "number", unit: "", attrs: {}, source: {} });
        return items;
      }
      r.useApi({
        getV3: function (path, extra) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 1 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [release] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [], edges: [] });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: null, baselines: [] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          if (path === base + "/cfg-1/drafts/d0-rel-1") return Promise.resolve({ draft_id: "d0-rel-1", revision: 0, values: {} });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) {
            var query = q(path); var offset = +query.offset || 0; var limit = +query.limit || 100;
            return Promise.resolve({ revision: 1, total: 5000, offset: offset, limit: limit, items: rows("P", offset, limit) });
          }
          if (path.indexOf(base + "/cfg-1/parameter-tree") === 0) {
            var pq = q(path);
            if (!pq.parent) return Promise.resolve({ revision: 1, parent: "", directories: [{ name: "G0", path: "G0", count: 4900 }], parameter_total: 0, offset: 0, limit: 100, items: [] });
            return Promise.resolve({ revision: 1, parent: pq.parent, directories: [], parameter_total: 4900, offset: +pq.offset || 0, limit: 100, items: rows("DirParam", 0, 100) });
          }
          if (path.indexOf("/overview") >= 0) {
            var oq = Object.assign(q(path), extra || {}); var oo = +oq.offset || 0; var ol = +oq.limit || 100;
            return Promise.resolve({ config_id: "cfg-1", release_id: "rel-1", release_version: "690", definition_revision: 1, has_previous: false, columns: [{ kind: "default", column_id: "d0-rel-1", label: "D0", revision_id: "d0-rel-1" }], total: 5000, offset: oo, limit: ol, rows: rows("Ov", oo, ol).map(function (x) { return { parameter_id: x.parameter_id, name: x.name, directory: ["G"], type: "number", unit: "", attrs: {}, source: {}, cells: { "d0-rel-1": { status: "ok", value: "1", revision_id: "d0-rel-1" } } }; }) });
          }
          return Promise.resolve({ items: [] });
        },
      });
      var testApp = settingsApp(true);
      testApp.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(testApp, { query: { config: "cfg-1" } }));
      function waitFor(fn, n) { return tick().then(function () { if (fn()) return true; if (!n) return false; return waitFor(fn, n - 1); }); }
      function tab(text) { var b = [].slice.call(inst.el.querySelectorAll("button")).filter(function (x) { return x.textContent.trim() === text; })[0]; ok(b, "缺少 " + text); b.click(); }
      return waitFor(function () { return inst.el.textContent.indexOf("版本演进") >= 0; }, 20).then(function () {
        tab("参数工作台");
        return waitFor(function () { return inst.el.textContent.indexOf("共 5000 行") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("P0") >= 0 && inst.el.textContent.indexOf("P99") >= 0, "首页 100 行");
        ok(inst.el.textContent.indexOf("P100") < 0, "未加载第二页");
        var next = [].slice.call(inst.el.querySelectorAll("button")).filter(function (x) { return x.textContent.trim() === "下一页"; })[0];
        next.click();
        return waitFor(function () { return inst.el.textContent.indexOf("P100") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("P0") < 0, "翻页不保留旧页行");
        tab("参数树");
        return waitFor(function () { return !!inst.el.querySelector(".cv-param-folders") && !inst.el.querySelector(".vtree") && inst.el.textContent.indexOf("G0 · 4900") >= 0; }, 20);
      }).then(function () {
        var dir = [].slice.call(inst.el.querySelectorAll("button")).filter(function (x) { return x.textContent.trim() === "G0 · 4900"; })[0];
        dir.click();
        return waitFor(function () { return inst.el.textContent.indexOf("DirParam0") >= 0; }, 20);
      }).then(function () {
        tab("参数总览");
        return waitFor(function () { return inst.el.textContent.indexOf("共 5000 行") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.textContent.indexOf("Ov0") >= 0, "总览首页");
        var next = [].slice.call(inst.el.querySelectorAll("button")).filter(function (x) { return x.textContent.trim() === "下一页"; })[0];
        next.click();
        return waitFor(function () { return inst.el.textContent.indexOf("Ov100") >= 0; }, 20);
      }).then(function () {
        var first = inst.el.querySelector("tbody tr td.cell-strong");
        ok(first && first.textContent.trim().indexOf("Ov100") === 0, "总览翻页只换当前页：" + (first && first.textContent));
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M13-01：参数工作台列表/参数树双模式共享同一 draft 与选中参数", function () {
    var prev = MC.workspaces.scopeId();
    MC.workspaces.setActive("default");
    var t = productionRun();
    return t.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs";
      var release = {
        release_id: "rel-1", version: "690", order: 1, predecessor_id: null,
        created_at: "2026-10-29 10:00:00", definition_revision: 1,
        default_revision_id: null, default_frozen_at: null,
        d0: { values: {}, frozen: false, revision: 0 },
      };
      var param = {
        parameter_id: "p-1", name: "Pulse", description: "说明", directory: ["G", "Sub"],
        type: "number", unit: "ms", attrs: { precision: 2 }, source: { locator: "/R[1]/p[1]" },
      };
      r.useApi({
        getV3: function (path, extra) {
          if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-1", code: "MC-001", name: "一号配置", release_count: 1 }] });
          if (path === base + "/cfg-1") return Promise.resolve({ config: { config_id: "cfg-1", code: "MC-001", name: "一号配置", releases: [release] } });
          if (path === base + "/cfg-1/graph") return Promise.resolve({ nodes: [{ release_id: "rel-1", version: "690", order: 1, predecessor_id: null, d0_frozen: false }], edges: [] });
          if (path.indexOf(base + "/cfg-1/parameters") === 0) return Promise.resolve({ revision: 1, items: [param], total: 1 });
          if (path === base + "/cfg-1/branches/main") return Promise.resolve({ head_id: null, baselines: [] });
          if (path === base + "/cfg-1/machines") return Promise.resolve({ items: [] });
          if (path === base + "/cfg-1/drafts/d0-rel-1") return Promise.resolve({ draft_id: "d0-rel-1", revision: 0, values: { "p-1": { membership: "included", enabled: true, value_state: "present", value: "5" } } });
          if (path.indexOf("/overview") >= 0) return Promise.resolve({ columns: [], rows: [], has_previous: false });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mountView(MC.views.configv3(settingsApp(false), { query: { config: "cfg-1" } }));
      function waitFor(fn, n) {
        return tick().then(function () {
          if (fn()) return true;
          if (!n) return false;
          return waitFor(fn, n - 1);
        });
      }
      return waitFor(function () { return inst.el.textContent.indexOf("版本演进") >= 0; }, 20).then(function () {
        var tab = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent === "参数工作台"; })[0];
        ok(tab, "参数工作台 tab");
        tab.click();
        return waitFor(function () { return inst.el.textContent.indexOf("参数树") >= 0; }, 10);
      }).then(function () {
        var treeBtn = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent === "参数树"; })[0];
        ok(treeBtn, "参数树模式按钮");
        treeBtn.click();
        return waitFor(function () { return inst.el.querySelector(".vtree") && inst.el.textContent.indexOf("Pulse") >= 0; }, 20);
      }).then(function () {
        ok(inst.el.querySelector(".vtree"), "参数树要渲染");
        ok(inst.el.querySelector(".vtree").clientHeight > 0, "参数树必须有可点击的可见高度");
        var leaf = Array.prototype.filter.call(inst.el.querySelectorAll(".vtree .tr"), function (row) { return row.textContent.indexOf("Pulse") >= 0; })[0];
        ok(leaf, "参数叶可见"); leaf.click();
        eq(inst.el.querySelectorAll(".cv-param-data tbody tr").length, 1, "叶子选择联动右侧表格");
        ok(inst.el.querySelector(".cv-folder-trail").textContent === "Pulse", "显示选中参数上下文");
        ok(inst.el.textContent.indexOf("Pulse") >= 0, "树要有参数叶");
        ok(inst.el.textContent.indexOf("G / Sub") >= 0 || inst.el.textContent.indexOf("G") >= 0, "树/详情保留完整层级");
      });
    }).then(function () { MC.workspaces.setActive(prev); }, function (e) { MC.workspaces.setActive(prev); throw e; });
  });

  test("M3-14：bindings 分段在生产模式打 /bindings，行内显示紫色组名与绑定路径，成员只读", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi({
        GET: function (path) {
          if (/\/bindings$/.test(path)) return Promise.resolve({
            items: [
              {
                group_name: "超时组",
                variables: [
                  { file: "a.xml", path: "a.xml/R/params/timeout" },
                  { file: "b.xml", path: "b.xml/R/params/timeout" },
                ],
              },
            ],
          });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mount(settingsApp(true), "bindings");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        eq(spy.calls, ["GET /p/default/bindings"], "进入已接入的 bindings 只发这一个请求");
        var badge = main.querySelector(".badge.badge-bind");
        ok(badge && badge.textContent === "超时组", "组名要用紫色绑定徽标显示：" + (badge ? badge.textContent : "<没有徽标>"));
        ok(main.textContent.indexOf("a.xml/R/params/timeout") >= 0, "变量 1 的绑定路径要显示");
        ok(main.textContent.indexOf("b.xml/R/params/timeout") >= 0, "变量 2 的绑定路径要显示");
        var edit = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent === "编辑"; })[0];
        ok(edit && !edit.disabled, "部署者可以编辑绑定组");
        var inst2 = r.mount(settingsApp(false), "bindings");
        return tick().then(function () {
          var main2 = inst2.el.querySelector(".settings-main");
          var edit2 = [].slice.call(main2.querySelectorAll("button")).filter(function (b) { return b.textContent === "编辑"; })[0];
          ok(edit2 && edit2.disabled, "非部署者的编辑按钮必须禁用");
          ok(/管理员/.test(edit2.getAttribute("title") || ""), "禁用要说明原因，实际：" + (edit2 ? edit2.getAttribute("title") : "<没有按钮>"));
        });
      });
    });
  });

  test("M3-15：tools 分段在生产模式打 /tools，行内显示名称/说明/地址，成员只读", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi({
        GET: function (path) {
          if (/\/tools$/.test(path)) return Promise.resolve({
            items: [
              {
                name: "构建工具",
                description: "生成固件的脚本",
                url: "https://example.com/build?x=1",
                added_at: "2026-10-20 10:00:00",
              },
            ],
          });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mount(settingsApp(true), "tools");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        eq(spy.calls, ["GET /p/default/tools"], "进入已接入的 tools 只发这一个请求");
        ok(main.textContent.indexOf("构建工具") >= 0, "工具名称要显示");
        ok(main.textContent.indexOf("生成固件的脚本") >= 0, "说明要显示");
        ok(main.textContent.indexOf("https://example.com/build?x=1") >= 0, "访问地址要显示");
        var edit = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent === "编辑"; })[0];
        ok(edit && !edit.disabled, "部署者可以编辑工具");
        var inst2 = r.mount(settingsApp(false), "tools");
        return tick().then(function () {
          var main2 = inst2.el.querySelector(".settings-main");
          var edit2 = [].slice.call(main2.querySelectorAll("button")).filter(function (b) { return b.textContent === "编辑"; })[0];
          ok(edit2 && edit2.disabled, "非部署者的编辑按钮必须禁用");
          ok(/管理员/.test(edit2.getAttribute("title") || ""), "禁用要说明原因，实际：" + (edit2 ? edit2.getAttribute("title") : "<没有按钮>"));
        });
      });
    });
  });

  test("外部工具页：导航按钮对应的页面列出链接，部署者可编辑", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi({
        GET: function (path) {
          if (/\/tools$/.test(path)) return Promise.resolve({
            items: [
              { name: "构建工具", description: "生成固件的脚本", url: "https://example.com/build?x=1", added_at: "2026-10-20 10:00:00" },
              { name: "监控", description: "", url: "http://10.0.0.8/", added_at: "2026-10-20 10:00:00" },
            ],
          });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mountView(MC.views.tools(settingsApp(true), { kind: "tools", query: {} }));
      return tick().then(function () {
        eq(spy.calls[0], "GET /p/default/tools", "打开外部工具页读取工具列表");
        var links = [].slice.call(inst.el.querySelectorAll("a.tool-open"));
        eq(links.length, 2, "每个工具一张可打开的卡片");
        eq(links[0].getAttribute("target"), "_blank");
        eq(links[0].getAttribute("rel"), "noopener noreferrer");
        ok(links[0].href.indexOf("https://example.com/build") === 0, "地址用安全链接打开");
        ok(inst.el.textContent.indexOf("构建工具") >= 0);
        ok(inst.el.textContent.indexOf("https://example.com/build?x=1") >= 0, "卡片上显示完整地址");
        ok(inst.el.textContent.indexOf("生成固件的脚本") >= 0, "卡片上显示完整备注");
        var edit = inst.el.querySelector("button[aria-label='编辑 构建工具']");
        ok(edit, "部署者可以编辑");
        var guest = r.mountView(MC.views.tools(settingsApp(false), { kind: "tools", query: {} }));
        return tick().then(function () {
          ok(!guest.el.querySelector("button[aria-label='编辑 构建工具']"), "非部署者不显示编辑");
          ok(!guest.el.querySelector(".btn-primary"), "非部署者没有添加按钮");
        });
      });
    });
  });

  test("M3-06：sources 分段在生产模式打 /sources 与 POST /tasks，状态列显示条目状态与原因", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi(sourceHandlers());
      var app = settingsApp(true);
      var inst = r.mount(app, "sources");
      var main;
      return tick()
        .then(function () {
          main = inst.el.querySelector(".settings-main");
          eq(spy.calls, ["GET /p/default/sources"], "进入已接入的 sources 只发这一个请求");
          ["已更新", "已拒绝", "下载失败", "无变化"].forEach(function (label) {
            ok(main.textContent.indexOf(label) >= 0, "状态列要显示条目状态「" + label + "」（来自 last_result，不是任务 state）");
          });
          ok(main.textContent.indexOf("根节点由 R 变为 html") >= 0, "rejected 行要显示原因");
          ok(main.textContent.indexOf("连接超时（30 秒），已重试 3 次") >= 0, "download_failed 行要显示原因");
          ok(main.textContent.indexOf("尚未下载") >= 0, "没有本地文件要如实显示");
          ok(main.textContent.indexOf("修改记录：http://up/b_rec.xml") >= 0, "record_url 要显示");
          var upds = [].slice.call(main.querySelectorAll("tbody button")).filter(function (b) { return b.textContent.indexOf("更新") >= 0; });
          eq(upds.length, 4, "每行一个更新按钮");
          upds.forEach(function (b) { ok(!b.disabled, "分段已接入且 caps.tasks 为真时更新按钮可用"); });
          upds[0].click();
          return tick();
        })
        .then(function () {
          ok(spy.calls.indexOf('POST /p/default/tasks {"type":"update_source","name":"a.xml"}') >= 0, "更新按钮只走 POST /tasks，不做第二套同步按钮：" + spy.calls.join(" | "));
          ok(app.taskSlot.textContent.indexOf("准备中") >= 0, "入队瞬间 total 还没发布 ⇒ 顶栏显示「准备中」而不是 0/0");
          var all = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("全部立即更新") >= 0; })[0];
          ok(all && !all.disabled, "有条目且 tasks 已接入时「全部立即更新」可用");
          all.click();
          return tick();
        })
        .then(function () {
          ok(spy.calls.indexOf('POST /p/default/tasks {"type":"update_all"}') >= 0, "全部更新同样只走 POST /tasks");
          eq(spy.calls.filter(function (c) { return c.indexOf("POST") === 0; }).length, 2, "两次点击各发一个任务请求，没有重试或重复");
        });
    });
  });

  /* 读 `.settings-main` 里 `dl.kv` 的某一行（dt 文本 → dd 文本）。视图把字段名读错时
   * 整页 textContent 仍然「像是对的」，逐行取值才抓得住「显示了另一个键」。 */
  function kvValue(rootEl, label) {
    var dts = [].slice.call(rootEl.querySelectorAll("dt"));
    var hit = dts.filter(function (dt) { return dt.textContent === label; })[0];
    if (!hit) return "<没有这一行>";
    var dd = hit.nextElementSibling;
    return dd ? dd.textContent : "<没有值>";
  }

  test("M3-08：schedule 分段在生产模式读写 /schedule——2 天存成 48、回显自动选单位、400 落在间隔字段（UC-33）", function () {
    var t = productionRun();
    return t.run(function (r) {
      var store = { enabled: true, interval_hours: 6, last_run: { at: "2026-10-18 10:00:00", success: 2, unchanged: 1, failed: 0, skipped: 0 }, next_run_at: "2026-10-18 16:00:00" };
      var spy = r.useApi({
        GET: function (path) {
          if (/\/schedule$/.test(path)) return Promise.resolve(JSON.parse(JSON.stringify(store)));
          return Promise.resolve({ items: [] });
        },
        /* 与后端 `services/scheduler.validate_interval` 同一条校验：1–2160 的整数，否则 400
         * 且 `detail.field = "interval"`（R19：mock 只是参考，这条是从 SPEC 抄来的）。 */
        PUT: function (path, body) {
          var n = body && body.interval_hours;
          if (typeof n !== "number" || n !== Math.floor(n) || n < 1 || n > 2160) {
            return Promise.reject(new MC.ApiError(400, "INVALID_INPUT", "间隔必须是 1-2160 之间的整数小时", { field: "interval" }));
          }
          store = { enabled: !!body.enabled, interval_hours: n, last_run: store.last_run, next_run_at: "2026-10-20 10:00:00" };
          return Promise.resolve(JSON.parse(JSON.stringify(store)));
        },
      });
      var inst = r.mount(settingsApp(true), "schedule");
      var main, amount, unit, save;
      return tick()
        .then(function () {
          main = inst.el.querySelector(".settings-main");
          eq(spy.calls, ["GET /p/default/schedule"], "D2 门控开了 ⇒ 进分段只发这一个请求（不再画只读横幅）");
          amount = main.querySelector("input[type=number]");
          unit = main.querySelector("select");
          eq([amount.value, unit.value], ["6", "h"], "6 小时：单位自动选「小时」");
          var deploySave = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("保存") >= 0; })[0];
          ok(!amount.disabled && !!deploySave && !deploySave.disabled, "部署者身份下控件与保存都可用（未接入时才禁用）");
          ok(main.textContent.indexOf("已启用") >= 0, "状态取自真实 enabled");
          eq(kvValue(main, "下次执行"), MC.util.formatTime(store.next_run_at), "「下次执行」显示调度器算出的 next_run_at（读错键就退化成空白或「—」）");
          ok(kvValue(main, "下次执行") !== "—", "next_run_at 有值时不许画「—」");
          ok(main.textContent.indexOf("2 个更新") >= 0 && main.textContent.indexOf("尚未执行") < 0, "「上次执行」显示 last_run 汇总");
          /* UC-33：选「2 天」保存 ⇒ 落库必须是 48 小时 */
          unit.value = "d";
          amount.value = "2";
          save = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("保存") >= 0; })[0];
          save.click();
          return tick();
        })
        .then(function () {
          ok(spy.calls.indexOf('PUT /p/default/schedule {"enabled":true,"interval_hours":48}') >= 0, "「天」必须换算成 24 的整数倍再 PUT：" + spy.calls.join(" | "));
          /* 保存成功后视图重新 GET（renderSection(true)），回读 48 要自动按「2 天」显示。 */
          var amount2 = main.querySelector("input[type=number]");
          var unit2 = main.querySelector("select");
          eq([amount2.value, unit2.value], ["2", "d"], "回读 48 小时：单位自动选「天」、数值显示 2（UC-33 的「页面显示 2 天」）");
          /* 服务端 400 的落点：前端整数检查放行 3000，越界由后端拒——错误必须长在「执行间隔」字段上，
           * 不是弹一个全局故障 toast（前后端字段名 interval 是契约，见 api_schedule 用例）。 */
          unit2.value = "h";
          amount2.value = "3000";
          var save2 = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("保存") >= 0; })[0];
          save2.click();
          return tick();
        })
        .then(function () {
          /* `MC.field` 把错误写在 `.field-error`（role=alert）上、把 is-invalid 加在 control
           * （这里是装 input+select 的那只 `div.row`）上——两处都要核，只看 textContent 会把
           * 「隐藏的错误节点」也读成通过。 */
          var intervalField = [].slice.call(main.querySelectorAll(".field")).filter(function (f) {
            var l = f.querySelector(".field-label");
            return l && l.textContent.indexOf("执行间隔") >= 0;
          })[0];
          ok(intervalField, "要有「执行间隔」字段");
          var msg = intervalField.querySelector(".field-error");
          ok(!msg.classList.contains("hidden"), "400 必须把错误显出来（不是 hidden）");
          ok(msg.textContent.indexOf("整数") >= 0, "错误文案要显示后端那句话，实际：" + msg.textContent);
          ok(intervalField.querySelector(".row").classList.contains("is-invalid"), "间隔控件要标红");
          var toasts = [].slice.call(document.querySelectorAll(".toast"));
          ok(!toasts.some(function (x) { return x.className.indexOf("toast-error") >= 0; }), "字段级 400 不许退化成全局「服务器故障」toast：" + toasts.map(function (x) { return x.textContent; }).join(" | "));
          eq(spy.calls.filter(function (c) { return c.indexOf("PUT") === 0; }).length, 2, "两次保存各一个 PUT，不重试");
        });
    });
  });

  test("M3-08：非部署者看定时分段是只读的——控件禁用、title 给原因，但仍然读得到状态", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi({
        GET: function (path) {
          if (/\/schedule$/.test(path)) return Promise.resolve({ enabled: false, interval_hours: 24, last_run: null, next_run_at: null });
          return Promise.resolve({ items: [] });
        },
      });
      var inst = r.mount(settingsApp(false), "schedule");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        eq(spy.calls, ["GET /p/default/schedule"], "GET /schedule 是全部角色可读（§7.9），门控不许挡只读用户");
        var controls = [].slice.call(main.querySelectorAll("button, input, select"));
        ok(controls.length >= 4, "分段要有控件占位，实际 " + controls.length);
        controls.forEach(function (c) {
          ok(c.disabled, "非部署者 ⇒ 控件一律 disabled");
          /* 开关的 title 长在外层 label 上（`MC.switchEl` 把 title 给 wrapper），
           * 所以按「自己或被 titled 的祖先」取，别要求裸 input 自己带 title。 */
          var why = c.getAttribute("title") || (c.closest("[title]") || {}).title || "";
          ok(why.indexOf("管理员") >= 0, "禁用要点名原因（是权限，不是没接入），实际：" + JSON.stringify(why));
        });
      });
    });
  });

  /* ---------- M3-13：人员与权限（IP 对应表 + 管理员名单） ----------
   * 数据故意摆成三种形状：当前访问者自己（要标「你」）、已登记且是管理员的（角色列要显示
   * 「管理员」）、只在管理员名单里但没有 IP 行的（要显示「未登记 IP，暂不生效」）。 */
  var IP_ROWS = [
    { ip: "10.12.3.21", name: "部署者甲", updated_at: "2026-10-18 09:00:00" },
    { ip: "10.12.3.22", name: "王强", updated_at: "2026-10-17 15:00:00" },
  ];
  var ADMIN_ROWS = [
    { ip: "10.12.3.22", name: "王强", updated_at: "2026-10-17 15:00:00" },
    { ip: "10.9.9.9", name: "外网管理员", updated_at: "2026-10-16 10:00:00" },
  ];
  function peopleHandlers(extra) {
    var h = {
      GET: function (path) {
        if (/\/ip-mapping$/.test(path)) return Promise.resolve({ items: JSON.parse(JSON.stringify(IP_ROWS)) });
        if (/\/admins$/.test(path)) return Promise.resolve({ items: JSON.parse(JSON.stringify(ADMIN_ROWS)) });
        return Promise.resolve({ items: [] });
      },
    };
    Object.keys(extra || {}).forEach(function (k) { h[k] = extra[k]; });
    return h;
  }
  /** 找到弹层里某个 label 对应的输入框，并返回「点保存」的触发器。 */
  function modalField(labelText) {
    var fields = [].slice.call(document.querySelectorAll(".modal .field")).filter(function (f) {
      var l = f.querySelector(".field-label");
      return l && l.textContent.indexOf(labelText) >= 0;
    })[0];
    if (!fields) return null;
    var control = fields.querySelector("input, select, textarea");
    return {
      field: fields,
      control: control,
      error: fields.querySelector(".field-error"),
      button: function () {
        var btns = [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) {
          return b.textContent.indexOf("保存") >= 0 || b.textContent.indexOf("添加") >= 0;
        });
        return btns[0];
      },
    };
  }

  test("M3-13：people 分段在生产模式读 /ip-mapping 与 /admins，表出「你」、角色与未登记 IP，并带出 §11.1 的可信内网提示", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi(peopleHandlers());
      var inst = r.mount(settingsApp(true), "people");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        eq(spy.calls, ["GET /p/default/ip-mapping", "GET /p/default/admins"], "门控开了 ⇒ 进 people 只发这两个请求，实际：" + spy.calls.join(" | "));
        ok(main.textContent.indexOf("人员与权限将在 M3-13 接入") < 0, "已接入的分段不许再挂未接入文案");
        /* §9.8.10 的三列语义：当前访问者标「你」、有 IP 行且不在名单的是「成员」、
         * 只在管理员名单里没 IP 行的显示「未登记 IP，暂不生效」。 */
        eq([].slice.call(main.querySelectorAll("tbody tr")).length, 4, "IP 表 2 行 + 管理员表 2 行");
        ok(main.textContent.indexOf("你") >= 0, "当前访问者的 IP 行要标「你」");
        ok(main.textContent.indexOf("10.9.9.9") >= 0, "管理员表要列出被授权的 IP");
        /* §11.1：这句提示必须在界面上，不许只写在文档里（本卡把它从「未接入横幅」后面救出来）。 */
        ok(main.textContent.indexOf("可信内网") >= 0, "§11.1 的「仅适用于可信内网」必须出现在分段文案里");
        ok(main.textContent.indexOf("强认证") >= 0, "要说清 IP 识别不是强认证");
        ok(main.textContent.indexOf("反向代理") >= 0, "经代理部署会全员同一个地址，这句也得看得见");
      });
    });
  });

  test("M3-13 / UC-34：登记当前 IP 并加为管理员后重新 GET /session、就地重画顶栏与侧栏（不整页刷新）", function () {
    var t = productionRun();
    return t.run(function (r) {
      var app = settingsApp(true); /* 部署者才有登记入口（非部署者的写控件是禁用的） */
      var sessionCalls = [];
      var spy = r.useApi(peopleHandlers({
        PUT: function (path, body) { return Promise.resolve({ item: { ip: body.ip, name: body.name, updated_at: "2026-10-18 10:00:00" } }); },
        POST: function (path, body) { return Promise.resolve({ item: { name: body.name, updated_at: "2026-10-18 10:00:00" } }); },
        GET: function (path, query) {
          if (path === "/session") {
            sessionCalls.push(JSON.stringify(query || {}));
            return Promise.resolve({
              identity: { ip: "10.12.3.21", name: "部署者甲", registered: true, is_deployer: true, is_admin: true, role_label: "部署者 · 管理员" },
              profile: { id: "default", name: "默认机型" },
              profiles: [{ id: "default", name: "默认机型" }],
              server: { version: "v2", time: "2026-10-18 10:00:00", scheduler: false },
              limits: { upload_max_bytes: 1, tab_limit: 15, temp_ttl_hours: 6 },
            });
          }
          if (/\/ip-mapping$/.test(path)) return Promise.resolve({ items: JSON.parse(JSON.stringify(IP_ROWS)) });
          if (/\/admins$/.test(path)) return Promise.resolve({ items: JSON.parse(JSON.stringify(ADMIN_ROWS)) });
          return Promise.resolve({ items: [] });
        },
      }));
      var inst = r.mount(app, "people");
      var main;
      return tick()
        .then(function () {
          main = inst.el.querySelector(".settings-main");
          var addP = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("登记人员") >= 0; })[0];
          ok(addP, "要有「登记人员」入口");
          addP.click();
          var ipField = modalField("IP 地址");
          ok(ipField && ipField.control, "弹层里要有 IP 地址字段");
          ipField.control.value = "10.12.3.21";
          var nameField = modalField("姓名");
          nameField.control.value = "部署者甲";
          ipField.button().click();
          return tick().then(tick);
        })
        .then(function () {
          ok(spy.calls.indexOf('PUT /p/default/ip-mapping/10.12.3.21 {"ip":"10.12.3.21","name":"部署者甲"}') >= 0, "登记走 PUT，路径与 body 同一个 IP：" + spy.calls.join(" | "));
          /* UC-34 的关键一步：保存成功后必须重新 GET /session 才能刷新身份。
           * 少了这一步，界面上的角色徽标与侧栏「审阅修改」都会停在旧身份上。 */
          eq(sessionCalls.length, 1, "PUT 之后重新拉一次 /session，实际：" + sessionCalls.length);
          eq(sessionCalls[0], '{"profile":"default"}', "/session 要带当前机型");
          eq([app.paints.header, app.paints.nav], [1, 1], "顶栏与侧栏各重画一次");
          eq(app.paints.go, 0, "不整页刷新：一次导航/重载都不许发生");
          ok(document.body.contains(inst.el), "视图实例还在文档里（没有被整页重载替掉）");
          /* 刷新身份之后分段自己重读了一次（`renderSection(true)`），两张表都要在。 */
          eq(spy.calls.filter(function (c) { return c.indexOf("GET /p/default/ip-mapping") === 0; }).length, 2, "保存后局部刷新列表：初始一次 + 保存后一次");
          app.paints.header = 0;
          app.paints.nav = 0;
          var addA = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("添加管理员") >= 0; })[0];
          addA.click();
          var ipF = modalField("IP 地址");
          ipF.control.value = "10.12.3.22";
          modalField("助记名").control.value = "王强";
          ipF.button().click();
          return tick().then(tick);
        })
        .then(function () {
          ok(spy.calls.indexOf('POST /p/default/admins {"ip":"10.12.3.22","name":"王强"}') >= 0, "加管理员走 POST /admins：" + spy.calls.join(" | "));
          eq([app.paints.header, app.paints.nav], [1, 1], "第二次保存同样只重画，不整页刷新");
          eq(app.session.identity.role_label, "部署者 · 管理员", "顶栏身份取自重新拉到的 /session");
        });
    });
  });

  test("M3-13：非法 IP 的 400 落在 IP 输入框、重名的 409 落在姓名上，都不退化成全局错误 toast", function () {
    var t = productionRun();
    return t.run(function (r) {
      /* 后端契约（`services/people.py`）：IP 非法 ⇒ 400 INVALID_INPUT + detail.field="ip"；
       * 管理员重名 ⇒ 409 FILE_EXISTS + detail.field="name"。`detail.field` 就是前后端的字段契约。 */
      var spy = r.useApi(peopleHandlers({
        PUT: function () { return Promise.reject(new MC.ApiError(400, "INVALID_INPUT", "请输入合法的 IPv4/IPv6 地址", { field: "ip" })); },
        POST: function () { return Promise.reject(new MC.ApiError(409, "FILE_EXISTS", "该管理员已存在", { field: "ip", ip: "10.12.3.22" })); },
      }));
      var inst = r.mount(settingsApp(true), "people");
      var main, ipField, nameField;
      return tick()
        .then(function () {
          main = inst.el.querySelector(".settings-main");
          [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("登记人员") >= 0; })[0].click();
          ipField = modalField("IP 地址");
          ipField.control.value = "999.1.1.1";
          modalField("姓名").control.value = "张三";
          ipField.button().click();
          return tick().then(tick);
        })
        .then(function () {
          ok(!ipField.error.classList.contains("hidden"), "400 必须把错误显示在 IP 字段下方");
          ok(ipField.error.textContent.indexOf("IPv4") >= 0, "要显示后端那句话，实际：" + ipField.error.textContent);
          ok(ipField.control.classList.contains("is-invalid"), "IP 输入框要标红");
          ok(ipField.control.getAttribute("aria-invalid") === "true", "读屏要听得到无效（§9.9）");
          [].slice.call(document.querySelectorAll(".modal button")).filter(function (b) { return b.textContent.indexOf("取消") >= 0; })[0].click();
          [].slice.call(main.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("添加管理员") >= 0; })[0].click();
          nameField = modalField("IP 地址");
          nameField.control.value = "10.12.3.22";
          nameField.button().click();
          return tick().then(tick);
        })
        .then(function () {
          ok(!nameField.error.classList.contains("hidden"), "409 必须显示在 IP 下方而不是弹全局 toast");
          ok(nameField.error.textContent.indexOf("已存在") >= 0, nameField.error.textContent);
          var toasts = [].slice.call(document.querySelectorAll(".toast"));
          ok(!toasts.some(function (x) { return x.className.indexOf("toast-error") >= 0; }), "字段级错误不许退化成「服务器故障」toast：" + toasts.map(function (x) { return x.textContent; }).join(" | "));
          eq(spy.calls.filter(function (c) { return c.indexOf("PUT") === 0; }).length, 1, "400 不自动重试");
          eq(spy.calls.filter(function (c) { return c.indexOf("POST") === 0; }).length, 1, "409 不自动重试");
        });
    });
  });

  test("M3-13：非部署者读得到两张表，但写控件全部禁用且 title 说是权限而不是「尚未接入」", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi(peopleHandlers());
      var inst = r.mount(settingsApp(false), "people");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        eq(spy.calls, ["GET /p/default/ip-mapping", "GET /p/default/admins"], "§7.9 读=全部角色，门控不许挡只读用户");
        var controls = [].slice.call(main.querySelectorAll("button, input, select"));
        ok(controls.length >= 4, "两张表都要有操作按钮，实际 " + controls.length);
        controls.forEach(function (c) {
          ok(c.disabled, "非部署者 ⇒ 写控件一律 disabled");
          var why = c.getAttribute("title") || (c.closest("[title]") || {}).title || "";
          ok(why.indexOf("管理员") >= 0, "禁用要点名原因（是权限，不是没接入），实际：" + JSON.stringify(why));
          ok(why.indexOf("M3-") < 0, "分段已接入，不许再挂「将在 M3-xx 接入」的去向文案：" + why);
        });
        ok(main.textContent.indexOf("可信内网") >= 0, "只读身份同样要看得见 §11.1 的提示");
      });
    });
  });

  test("M3-13 / R19：mock 的人员接口与后端同契约——IP 规范化落盘、`{item}`/`{removed}`、409 FILE_EXISTS、非法值 400", function () {
    /* 模板模式的绿不算后端接对的证据（R19），但 mock 必须与后端同口径。这条直接打
     * MC.mock.handle，钉本卡改掉的四处漂移（原先是 `{ok:true}` / `{deleted:true}` /
     * 409 `INVALID_INPUT` / 一个连 `abc` 都放过的 IP 正则）：
     * ① 非法 IP（`999.1.1.1`、`abc`、空串）与空姓名 ⇒ 400 + `detail.field`；
     * ② 合法但写法五花八门 ⇒ 存成 `ipaddress` 的规范形式（`::ffff:10.1.2.3` 折成 IPv4、
     *    大写折成小写）——不归一就是「登记了也不生效」的静默失败；
     * ③ 成功写 ⇒ PUT `{item}`、POST 201 `{item}`、DELETE `{removed: bool}`（后端先例）；
     * ④ 重名 ⇒ 409 `FILE_EXISTS`；「删了 0 条」不 bump settings（决策 16）。
     *
     * **必须一条一条串起来等**，不许 `Promise.all` 并发下发：`handle()` 每次的延迟是
     * `140 + Math.random()*120` 毫秒（`mock-api.js`），并发时**谁先落盘不确定**，
     * 于是「第二次 POST 该 409」会随机变成「第一次就 409」、「DELETE 了 0 条之后读到的
     * settings」会读到别人 bump 之后的值（本卡实测两轮各红一处，就是这个形状）。
     * 串行之后的顺序才是用例声明的顺序，也与后端「同一机型一把锁串行写」一致。 */
    var P = MC.mock.profileState("default");
    var keep = {
      people: JSON.parse(JSON.stringify(P.people)),
      admins: JSON.parse(JSON.stringify(P.admins)),
      identity: MC.mock.identityKey(),
    };
    var base = null;
    function call(method, path, body) {
      return MC.mock.handle({ method: method, path: path, query: {}, body: body, headers: {} });
    }
    /* 写接口要有部署者身份：当前身份可能被前面的用例切走过（shell 的身份切换器写
     * `mc.demo.identity`），这里当场钉成 deployer-admin，结束时还原。 */
    MC.mock.setIdentity("deployer-admin");
    return call("PUT", "/p/default/ip-mapping/999.1.1.1", { ip: "999.1.1.1", name: "张三" })
      .then(function (res) {
        eq(res.status, 400, "契约 ①：`999.1.1.1` 必须 400（旧正则每段只限 1-3 位、不限值域，所以它放过 999）");
        eq(res.body.error.code, "INVALID_INPUT");
        eq(res.body.error.detail.field, "ip");
        return call("PUT", "/p/default/ip-mapping/1.2.3.4", { ip: "abc", name: "张三" });
      })
      .then(function (res) {
        eq(res.status, 400, "契约 ①：`abc` 必须 400（旧正则的 IPv6 那一支把纯字母当合法十六进制段放过）");
        eq(res.body.error.detail.field, "ip");
        return call("PUT", "/p/default/ip-mapping/1.2.3.4", { ip: "", name: "张三" });
      })
      .then(function (res) {
        eq(res.status, 400, "契约 ①：空 IP 400");
        eq(res.body.error.detail.field, "ip");
        return call("PUT", "/p/default/ip-mapping/1.2.3.4", { ip: "1.2.3.4", name: "   " });
      })
      .then(function (res) {
        eq(res.status, 400, "§11.2：去空白后为空的姓名 400");
        eq(res.body.error.detail.field, "name");
        eq(P.people.length, keep.people.length, "四条全被拒 ⇒ 一张表都不该多出行来（失败写不落盘）");
        eq(P.admins.length, keep.admins.length, "同上，管理员名单也不许动");
        return call("PUT", "/p/default/ip-mapping/::ffff:10.1.2.3", { ip: "::ffff:10.1.2.3", name: "映射甲" });
      })
      .then(function (res) {
        eq(res.status, 200, "契约 ②：`::ffff:a.b.c.d` 合法");
        eq(res.body.item.ip, "10.1.2.3", "契约 ②：落盘折成点分 IPv4（与 `auth.normalize_ip` 同口径，而 `resolve_person_by_ip` 是逐字比较）");
        eq(P.people.filter(function (x) { return x.ip === "10.1.2.3"; }).length, 1, "规范化后按新写法存一行");
        return call("PUT", "/p/default/ip-mapping/FE80::1", { ip: "FE80::1", name: "大写乙" });
      })
      .then(function (res) {
        eq(res.status, 200, "契约 ②：大写 IPv6 合法");
        eq(res.body.item.ip, "fe80::1", "契约 ②：大写落成小写规范形式");
        return call("POST", "/p/default/admins", { name: "新管理员" });
      })
      .then(function (res) {
        eq(res.status, 201, "契约 ③：POST /admins 是 201（§7.1「201=创建」）");
        eq(Object.keys(res.body).sort(), ["item"], "契约 ③：响应是 `{item}`，不是 `{ok:true}`");
        return call("GET", "/p/default/revision");
      })
      .then(function (res) {
        /* 基准取在**唯一一次成功的写之后**：再往后的每一步都只该被「真删掉一条」推动。
         * 基准若取在 POST 之前，201 自己那一次 bump 会算进账里，「删了 0 条不 bump」
         * 就永远核不出来（本卡第一版就踩在这里，实测随机红）。 */
        base = res.body.parts.settings;
        return call("POST", "/p/default/admins", { name: "新管理员" });
      })
      .then(function (res) {
        eq(res.status, 409, "契约 ④：重名 409");
        eq(res.body.error.code, "FILE_EXISTS", "契约 ④：409 的错误码是 `FILE_EXISTS`，不是 `INVALID_INPUT`（§7.1 把后者定成 400）");
        eq(res.body.error.detail.field, "name");
        return call("DELETE", "/p/default/ip-mapping/10.9.9.9");
      })
      .then(function (res) {
        eq(res.body, { removed: false }, "契约 ③/④：DELETE ⇒ `{removed: bool}`（后端 `DELETE /sources` 的先例，不是 `{deleted:true}`）");
        return call("GET", "/p/default/revision");
      })
      .then(function (res) {
        eq(res.body.parts.settings, base, "契约 ④：删了 0 条不 bump settings（决策 16）");
        return call("DELETE", "/p/default/admins/新管理员");
      })
      .then(function (res) {
        eq(res.body, { removed: true }, "真删掉一条 ⇒ removed:true");
        return call("GET", "/p/default/revision");
      })
      .then(function (res) {
        eq(res.body.parts.settings, base + 1, "真删掉一条才 bump 一次");
      })
      .finally(function () {
        /* 本用例改写的是**共享的** default 机型状态与全局身份（mock 是模板模式的活数据）。
         * 还原只放在异步链的 `.finally` 里；外层再套同步 `try/finally` 是**错的**：那会在
         * Promise 刚返回的那一刻就还回去，等于没还原（串行只保证「settle 之后」，不保证内容）。 */
        P.people = keep.people;
        P.admins = keep.admins;
        MC.mock.setIdentity(keep.identity);
      });
  });

  test("M3-13：mock 与后端同口径拒掉「加得进来、删不出去」的管理员姓名；删除侧 mock 比后端 HTTP 宽是已知差异", function () {
    /* 后端把姓名这一档挡在创建端（`people._validated_admin_name`），mock 必须跟着动：
     * 模板模式里加得进来、真后端 400 拒掉的名字，就是本项目禁的「mock 发明了后端没有的行为」
     * （R19 / know-how 第 2 节那条未定契约）。
     *   ① 创建端——裸 `/`、`\\`、字面 `%2f`/`%5c`/`%00`，以及**任何** `%XX`；
     *      两侧同判向、同文案由 `TestMockPeopleContract` **执行** mock 真身逐值核住。
     *   ② 删除端的已知差异：后端 **HTTP 边界**并不宽松（`/` 与 `%XX` 名字都会撞 400，
     *      只有服务层/运维能删存量行）；mock 只解一次路径，所以它的 DELETE 比后端 HTTP 宽。
     *      这是模板预览的已知差异，不是「后端 HTTP 也宽松」的证据；本用例只核 mock 的判决。
     * 与上面那条 R19 用例同一个理由：**一条一条串行 await**，不许 `Promise.all`——
     * mock 每次延迟 140+random(120) 毫秒且并发落盘顺序不定，会把断言刷成随机红。 */
    var P = MC.mock.profileState("default");
    var keep = {
      people: JSON.parse(JSON.stringify(P.people)),
      admins: JSON.parse(JSON.stringify(P.admins)),
      identity: MC.mock.identityKey(),
    };
    function call(method, path, body) {
      return MC.mock.handle({ method: method, path: path, query: {}, body: body, headers: {} });
    }
    /* 「含 `%XX`（X 为十六进制）」的名字故意**不**出现在这里的放行名单：后端 HTTP 今天
     * 也删不出它们（二次解码，见
     * `test_a_literal_percent_escape_is_rejected_at_creation_and_a_legacy_row_is_service_only`），
     * mock 只解一次，所以在 mock 里「删得出去」不能当成后端删得出去的证据。 */
    var rejected = ["王/强", "王%2F强"];
    MC.mock.setIdentity("deployer-admin");
    P.admins = [
      { ip: "127.0.0.1", name: "张伟", updated_at: "2026-10-18 09:00:00" },
      { name: "李/强", updated_at: "2026-10-18 09:00:00" },
    ];
    function expectRejected(name) {
      return call("POST", "/p/default/admins", { name: name }).then(function (res) {
        eq(res.status, 400, "不可管理的管理员姓名必须 400，实际 " + res.status + "：" + name);
        eq(res.body.error.code, "INVALID_INPUT", name);
        eq(res.body.error.detail.field, "name", "必须带字段（守卫那只 400 不带，所以只在创建端做得到）：" + name);
      });
    }
    /* 只打 5 次 `handle`：mock 每次延迟 140+random(120) 毫秒，而 `--virtual-time-budget=8000`
     * 是整页的硬截止（know-how 第 4 节 F2.4）。本卡实测这条加到 13 次调用时，页面在 8000 下
     * **确定地**停在「运行中…」（既不是绿也不是红，也不是那两种抖动——重跑多少次都一样），
     * 6 次调用时也只能在 7000 与 8000 之间勉强收线；原来的 50 条 suite 在 6000 下还是绿的。
     * ⇒ 覆盖面交给 `tests/test_api_people.py::TestMockPeopleContract`（它**执行** mock 真身、
     * 逐值核全部名单，不花页面的预算），这里只留「不对称」那两半各一条最小判据。 */
    return expectRejected(rejected[0])
      .then(function () { return expectRejected(rejected[1]); })
      .then(function () {
        eq(P.admins.length, 2, "两条被拒的姓名一条都不该进名单（失败写不落盘）");
        return call("PUT", "/p/default/ip-mapping/10.4.4.4", { ip: "10.4.4.4", name: "王/强" });
      })
      .then(function (res) {
        eq(res.status, 200, "IP 行的姓名不吃管理员那一档（它不进路径段）：" + JSON.stringify(res.body));
        eq(res.body.item.name, "王/强", "同一串 `王/强` 当姓名必须登记得进来");
        eq(P.people.filter(function (x) { return x.ip === "10.4.4.4"; }).length, 1,
          "那一行按 IP 落进了表（清理靠 `.finally` 还原，不多打一次请求——预算账见上面注释）");
        return call("POST", "/p/default/admins", { name: "100%达标" });
      })
      .then(function (res) {
        eq(res.status, 201, "`%` 后面不是那三只 token 的名字不许被连坐拒掉：" + JSON.stringify(res.body));
        return call("DELETE", "/p/default/admins/" + encodeURIComponent("李/强"));
      })
      .then(function (res) {
        eq(res.body, { removed: true }, "删除端不许跟着创建端收紧——v1 存量行全靠这一条出路");
        eq(P.admins.map(function (x) { return x.name; }), ["张伟", "100%达标"],
          "撤掉的是存量行那一条；放行侧那一条还在，被拒的两条从来没进过名单");
      })
      .finally(function () {
        P.people = keep.people;
        P.admins = keep.admins;
        MC.mock.setIdentity(keep.identity);
      });
  });

  test("M3-06：409 BUSY 不是服务器故障——解释互斥、不重试、给跳到 detail.task_id 的入口；404 按业务错误显示", function () {
    var t = productionRun();
    return t.run(function (r) {
      var runningAll = { id: "t9", profile: "default", type: "update_all", target: null, state: "running", progress: { done: 1, total: 3 }, result: { items: [] }, error: null, actor: "李娜", created_at: "2026-10-18 10:00:00", finished_at: null };
      var postErr = new MC.ApiError(409, "BUSY", "同类任务正在执行（由 李娜 发起）", { task_id: "t9" });
      var spy = r.useApi({
        GET: function (path) {
          if (/\/sources$/.test(path)) return Promise.resolve(JSON.parse(JSON.stringify(SOURCE_LIST)));
          if (path === "/tasks/t9") return Promise.resolve({ task: JSON.parse(JSON.stringify(runningAll)) });
          return Promise.resolve({ items: [] });
        },
        POST: function () { return Promise.reject(postErr); },
      });
      var app = settingsApp(true);
      var inst = r.mount(app, "sources");
      var main;
      var rowUpd = function (i) {
        return [].slice.call(main.querySelectorAll("tbody button")).filter(function (b) { return b.textContent.indexOf("更新") >= 0; })[i];
      };
      return tick()
        .then(function () {
          main = inst.el.querySelector(".settings-main");
          rowUpd(0).click();
          return tick();
        })
        .then(function () {
          var toasts = [].slice.call(document.querySelectorAll(".toast"));
          var warn = toasts.filter(function (x) { return x.className.indexOf("toast-warning") >= 0; })[0];
          ok(warn, "409 必须是解释性警示而不是错误 toast（BUSY 是预期结果，不是服务器故障）");
          ok(!toasts.some(function (x) { return x.className.indexOf("toast-error") >= 0; }), "不得按「无法开始更新/服务器故障」处理");
          ok(warn.textContent.indexOf("已有任务在运行") >= 0, "文案要说清互斥：" + warn.textContent);
          ok(warn.textContent.indexOf("李娜") >= 0, "要带上后端 detail 里的发起者");
          eq(spy.calls.filter(function (c) { return c.indexOf("POST") === 0; }).length, 1, "409 不自动重试");
          var action = warn.querySelector("button.btn");
          ok(action && action.textContent.indexOf("查看该任务") >= 0, "要给出跳转 detail.task_id 的入口");
          action.click();
          return tick();
        })
        .then(function () {
          ok(spy.calls.indexOf("GET /tasks/t9") >= 0, "入口按 detail.task_id 拉那条任务（GET /tasks/{id}）");
          var modal = [].slice.call(document.querySelectorAll(".modal-backdrop .modal")).pop();
          ok(modal, "弹出那条任务的详情");
          ok(modal.textContent.indexOf("全量更新") >= 0 && modal.textContent.indexOf("进行中") >= 0 && modal.textContent.indexOf("1/3") >= 0, "弹窗显示任务类型/状态/进度：" + modal.textContent);
          /* 换 404（卡 6 裁定：名字不在映射表 ⇒ 404 是业务结果，不是服务器故障）：
           * 显示后端消息、刷新本列表、不重试。 */
          document.querySelectorAll(".modal-backdrop").forEach(function (b) { b.remove(); });
          document.querySelectorAll(".toast").forEach(function (x) { x.remove(); });
          postErr = new MC.ApiError(404, "NOT_FOUND", "该文件没有配置更新源", { name: "a.xml" });
          rowUpd(1).click();
          return tick();
        })
        .then(function () {
          var toasts = [].slice.call(document.querySelectorAll(".toast"));
          ok(!toasts.some(function (x) { return x.className.indexOf("toast-error") >= 0; }), "404 不走「服务器故障」式错误 toast");
          var warn = toasts.filter(function (x) { return x.className.indexOf("toast-warning") >= 0 && x.textContent.indexOf("该文件没有配置更新源") >= 0; })[0];
          ok(warn, "404 显示后端业务消息：" + toasts.map(function (x) { return x.className + "|" + x.textContent; }).join(" ; "));
          var gets = spy.calls.filter(function (c) { return c === "GET /p/default/sources"; }).length;
          ok(gets >= 2, "404 说明列表已过期 ⇒ 刷新本列表（GET /sources 第二次），实际 " + gets + " 次");
          eq(spy.calls.filter(function (c) { return c.indexOf("POST") === 0; }).length, 2, "两次点击各一次 POST：409 与 404 都不自动重试");
        });
    });
  });

  test("M3-06 / F1：文件页「立即拉取」409 BUSY 与设置页同一共享口径——warning 点名在跑任务并跳 detail.task_id", function () {
    /* 卡 7 复审 F1：「全部更新」在跑时点「立即拉取」必然 409（三类两两互斥）。
     * 过去 fileview 直接 MC.toastError("无法开始拉取")——红色故障 toast、不解释互斥、
     * 不给那条任务的入口。现在走共享的 MC.taskStartError（shell.js），本用例镜像
     * 上面设置页那条 409 断言，钉住「warning / 点名在跑任务 / 跳转落 detail.task_id」。 */
    var t = productionRun();
    return t.run(function (r) {
      var runningAll = { id: "t9", profile: "default", type: "update_all", target: null, state: "running", progress: { done: 1, total: 3 }, result: { items: [] }, error: null, actor: "李娜", created_at: "2026-10-18 10:00:00", finished_at: null };
      var postErr = new MC.ApiError(409, "BUSY", "同类任务正在执行（由 李娜 发起）", { task_id: "t9" });
      var spy = r.useApi({
        GET: function (path) {
          if (/\/records$/.test(path)) return Promise.resolve({ record_url: "http://up/cfg_rec.xml", items: [] });
          if (path === "/tasks/t9") return Promise.resolve({ task: JSON.parse(JSON.stringify(runningAll)) });
          return Promise.resolve({ items: [] });
        },
        POST: function () { return Promise.reject(postErr); },
      });
      var app = settingsApp(true);
      app.files = [{ name: "cfg.xml", record_count: 2, archive_count: 1 }];
      var inst = r.mountView(MC.views.f(app, { kind: "f", arg: "cfg.xml", query: { view: "records" } }));
      var fetchBtn;
      return tick()
        .then(function () {
          var btns = [].slice.call(inst.el.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("立即拉取") >= 0; });
          eq(btns.length, 1, "修改记录视图要有「立即拉取」按钮");
          fetchBtn = btns[0];
          ok(!fetchBtn.disabled, "部署者 + 已配置 record_url + tasks 已接入 ⇒ 按钮可用");
          fetchBtn.click();
          return tick();
        })
        .then(function () {
          var toasts = [].slice.call(document.querySelectorAll(".toast"));
          var warn = toasts.filter(function (x) { return x.className.indexOf("toast-warning") >= 0; })[0];
          ok(warn, "409 在文件页同样是解释性 warning，不是红色故障 toast");
          ok(!toasts.some(function (x) { return x.className.indexOf("toast-error") >= 0; }), "不得按「无法开始拉取/服务器故障」处理（F1 修的正是这个）");
          ok(warn.textContent.indexOf("已有任务在运行") >= 0, "文案与设置页同源（MC.taskStartError）：" + warn.textContent);
          ok(warn.textContent.indexOf("李娜") >= 0, "要点名那条在跑任务的发起者");
          eq(spy.calls.filter(function (c) { return c.indexOf("POST") === 0; }).length, 1, "409 不自动重试");
          var action = warn.querySelector("button.btn");
          ok(action && action.textContent.indexOf("查看该任务") >= 0, "要给出跳转 detail.task_id 的入口");
          action.click();
          return tick();
        })
        .then(function () {
          ok(spy.calls.indexOf("GET /tasks/t9") >= 0, "入口按 detail.task_id 拉那条任务");
          var modal = [].slice.call(document.querySelectorAll(".modal-backdrop .modal")).pop();
          ok(modal, "弹出那条任务的详情");
          ok(modal.textContent.indexOf("全量更新") >= 0 && modal.textContent.indexOf("进行中") >= 0, "弹窗用共享词汇说出在跑的是全量更新：" + modal.textContent.slice(0, 160));
        });
    });
  });

  test("M3-06：改名对话框带 migrate_files 勾选；字段级错误保留输入；保存成功只刷新列表", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi(
        sourceHandlers(function () {
          return Promise.reject(new MC.ApiError(409, "FILE_EXISTS", "已存在同名更新源", { field: "name", name: "改名.xml" }));
        })
      );
      var app = settingsApp(true);
      var inst = r.mount(app, "sources");
      var modal, nameField, migrate;
      return tick()
        .then(function () {
          var main = inst.el.querySelector(".settings-main");
          main.querySelector('tbody button[aria-label="更多操作"]').click();
          var edit = [].slice.call(document.querySelectorAll(".popover .menu-item")).filter(function (m) { return m.textContent.indexOf("编辑") >= 0; })[0];
          ok(edit, "行菜单里有「编辑」");
          edit.click();
          modal = [].slice.call(document.querySelectorAll(".modal-backdrop .modal")).pop();
          nameField = modal.querySelectorAll(".field input.input-mono")[0];
          migrate = modal.querySelector('input[type="checkbox"]');
          ok(migrate, "改名对话框必须有「是否迁移本地文件」勾选");
          ok(migrate.checked, "缺省勾选迁移（与后端 migrate_files 缺省 True 同口径）");
          ok(modal.textContent.indexOf("迁移") >= 0, "勾选旁要说明迁移范围（本地文件、历史、收藏、建议）");
          migrate.checked = false;
          nameField.value = "改名.xml";
          nameField.dispatchEvent(new Event("input"));
          var save = [].slice.call(modal.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("保存") >= 0; })[0];
          save.click();
          return tick();
        })
        .then(function () {
          var patchCalls = spy.calls.filter(function (c) { return c.indexOf("PATCH") === 0; });
          eq(patchCalls.length, 1, "一次点击一次 PATCH");
          ok(patchCalls[0].indexOf("PATCH /p/default/sources/a.xml ") === 0, "PATCH 打到旧名字（改名前的键）：" + patchCalls[0]);
          ok(patchCalls[0].indexOf('"migrate_files":false') >= 0, "取消勾选要如实提交 migrate_files=false：" + patchCalls[0]);
          var fieldWrap = nameField.closest ? nameField.closest(".field") : nameField.parentNode;
          var errEl = fieldWrap.querySelector(".field-error");
          ok(errEl && !errEl.classList.contains("hidden") && errEl.textContent.length > 0, "detail.field=name 的错误显示在「文件名」输入框下方");
          eq(nameField.value, "改名.xml", "字段错误必须保留用户输入");
          ok(nameField.classList.contains("is-invalid"), "输入框标红");
          /* 接口恢复正常后再保存一次：成功 ⇒ 对话框关闭、只重新拉本列表（不整页 reload）。 */
          migrate.checked = false;
          var spy2 = r.useApi(
            sourceHandlers(function () {
              return Promise.resolve({ item: { name: "改名.xml", url: "http://up/a.xml", record_url: "" } });
            })
          );
          var save2 = [].slice.call(modal.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("保存") >= 0; })[0];
          save2.click();
          return tick().then(function () {
            ok(!document.body.contains(modal), "保存成功后对话框关闭");
            var gets = spy2.calls.filter(function (c) { return c === "GET /p/default/sources"; }).length;
            ok(gets >= 1, "保存成功后重新拉本列表刷新（而不是整页 reload）");
            var patch2 = spy2.calls.filter(function (c) { return c.indexOf("PATCH") === 0; });
            eq(patch2.length, 1, "第二次点击只发一次 PATCH");
          });
        });
    });
  });

  test("M3-06：非部署者进 sources 可查看，但页级只读横幅说明原因、写控件全部禁用", function () {
    var t = productionRun();
    return t.run(function (r) {
      var spy = r.useApi(sourceHandlers());
      var inst = r.mount(settingsApp(false), "sources");
      return tick().then(function () {
        var main = inst.el.querySelector(".settings-main");
        ok(spy.calls.indexOf("GET /p/default/sources") >= 0, "读取仍允许（读接口全部角色可用）");
        ok(main.textContent.indexOf("只读") >= 0 && main.textContent.indexOf("管理员") >= 0, "页级横幅要说清身份与原因：" + main.textContent.slice(0, 160));
        var writeBtns = [].slice.call(main.querySelectorAll("button")).filter(function (b) { return /新增|更新/.test(b.textContent); });
        ok(writeBtns.length >= 3, "覆盖面：新增、每行更新、全部立即更新都要在");
        writeBtns.forEach(function (b) {
          ok(b.disabled, "非部署者的写按钮必须禁用");
          ok((b.getAttribute("title") || "").indexOf("管理员") >= 0, "禁用要说明原因");
        });
      });
    });
  });

  test("M3-06：顶栏任务进度在 total 未发布时渲染「准备中」，不显示 0/0", function () {
    /* 后端契约（卡 6 移交）：update_source 的 progress.total 在下载结束后才回填，
     * 下载期间就是 0/0 ⇒ UI 必须渲染成「准备中」。 */
    function slotFor(tasks) {
      var slot = MC.util.h("div");
      MC.App.prototype.renderTasks.call({ taskSlot: slot, activeTasks: tasks });
      return slot;
    }
    var early = slotFor([{ id: "t1", type: "update_source", target: "x.xml", state: "running", progress: { done: 0, total: 0 }, result: { items: [] }, actor: "张三" }]);
    ok(early.textContent.indexOf("准备中") >= 0, "total=0 ⇒ 「准备中」，实际：" + early.textContent);
    ok(early.textContent.indexOf("0/0") < 0, "不能再出现 0/0");
    var later = slotFor([{ id: "t2", type: "update_all", target: null, state: "running", progress: { done: 2, total: 5 }, result: { items: [] }, actor: "张三" }]);
    ok(later.textContent.indexOf("2/5") >= 0 && later.textContent.indexOf("准备中") < 0, "total 已发布 ⇒ 正常 n/m");
    var withQueued = slotFor([
      { id: "t2", type: "update_all", target: null, state: "running", progress: { done: 2, total: 5 }, result: { items: [] }, actor: "张三" },
      { id: "t3", type: "update_all", target: null, state: "queued", progress: { done: 0, total: 0 }, result: { items: [] }, actor: "李四" },
    ]);
    ok(withQueued.textContent.indexOf("等 2 项") >= 0, "queued 也算活跃任务，顶栏必须计入：" + withQueued.textContent);
  });

  test("M3-06：任务浮层里 total 未发布时**整条进度条都不画**（注释与 markup 不得各说各话）", function () {
    /* 第三轮 nit 的回归位：F7 的注释一直声称「不在 total 未发布时画 0% 空条」，可 markup
     * 原先**无条件**画 `.progress`——灰色槽照样出现，只是里面是空的。现在按 hasProgress
     * 条件渲染，这条用例点开真浮层钉住两侧：未发布⇒0 条，已发布⇒1 条且宽度按 done/total。
     * 上面那条「准备中」用例只查顶栏按钮文字，看不见浮层，所以两者都要在。 */
    var slot = MC.util.h("div");
    document.body.appendChild(slot);
    function open(tasks) {
      MC.util.clear(slot);
      MC.App.prototype.renderTasks.call({ taskSlot: slot, activeTasks: tasks });
      slot.querySelector("button.task-indicator").click();
      var bars = document.querySelectorAll(".popover .progress");
      var width = bars.length ? bars[0].firstElementChild.style.width : null;
      cleanOverlays();
      return { bars: bars.length, width: width };
    }
    var early = open([{ id: "t1", type: "update_source", target: "x.xml", state: "running", progress: { done: 0, total: 0 }, result: { items: [] }, actor: "张三" }]);
    eq(early.bars, 0, "total 未发布 ⇒ 一条都不画（旧 markup 会留一条 0% 灰槽，正是注释与 markup 矛盾的地方）");
    var later = open([{ id: "t2", type: "update_all", target: null, state: "running", progress: { done: 2, total: 5 }, result: { items: [] }, actor: "张三" }]);
    eq([later.bars, later.width], [1, "40%"], "total 已发布 ⇒ 照旧画一条，宽度 2/5 = 40%");
    slot.remove();
  });

  test("M3-06 / R19：mock 与后端 /tasks 契约同步——单条任务 0/0 起步、三类两两互斥 409+task_id、未知名 404", function () {
    /* 模板模式的绿不算后端接对的证据（R19），但 mock 必须与后端同口径——这条用例
     * 直接打 MC.mock.handle，钉住卡 6 移交的三处契约：
     * ① 单条任务入队不预发 total（下载期间 0/0 ⇒ UI「准备中」）；
     * ② 互斥面 = 后端 _CONFLICTING_TYPES：三类两两互斥、撞车 409 BUSY + detail.task_id；
     * ③ 名字不在映射表 ⇒ 404 NOT_FOUND（不是那条「什么都没做却已完成」的绿任务）。 */
    function call(method, path, body) {
      return MC.mock.handle({ method: method, path: path, query: {}, body: body, headers: {} });
    }
    var first = null;
    return call("POST", "/p/default/tasks", { type: "update_source", name: "scenario_a.xml" })
      .then(function (r) {
        eq(r.status, 202, "单条更新走 POST /tasks");
        first = r.body.task;
        ok(!("key" in first), "payload 不得泄漏内部去重键 key");
        eq([first.progress.done, first.progress.total], [0, 0], "契约 ①：入队时不预发 total，下载期间就是 0/0");
        eq(first.result.items.length, 0, "入队响应里没有结果条目");
        /* 互斥断言与在跑任务**并发**下发：mock 的模拟下载约 900ms 后完成，串行等待会
         * 撞上完成时刻变成随机红。并发 ⇒ 每条都在同一个 dispatch 窗口（≤260ms）里被
         * BUSY 挡住，与后端行为一致且不受延迟抖动影响。 */
        return Promise.all([
          call("POST", "/p/default/tasks", { type: "update_all" }),
          call("POST", "/p/default/tasks", { type: "update_source", name: "scenario_a.xml" }),
          call("POST", "/p/default/tasks", { type: "refresh_records", name: "scenario_a.xml" }),
          call("POST", "/p/default/tasks", { type: "bogus" }),
          call("POST", "/p/default/tasks", { type: "update_source", name: "bad/name" }),
          call("POST", "/p/default/tasks", { type: "update_source", name: "没配置.xml" }),
          call("GET", "/tasks/nope-not-here"),
        ]);
      })
      .then(function (rs) {
        var all = rs[0], same = rs[1], rec = rs[2], bogus = rs[3], badname = rs[4], ghost = rs[5], gone = rs[6];
        eq(all.status, 409, "契约 ②：update_source 在跑 ⇒ update_all 必 409（与后端加宽后的互斥面一致）");
        eq(all.body.error.code, "BUSY", "409 是 BUSY 业务结果，不是服务器故障");
        ok(all.body.error.detail && all.body.error.detail.task_id === first.id, "detail.task_id 指回正在跑的那条，前端据此给跳转入口");
        eq(same.status, 409, "同 (机型,类型,目标) 重复提交也 409，不排队不重试");
        eq(rec.status, 409, "契约 ②：三类两两互斥 ⇒ refresh_records 撞进行中的 update_source（卡 6 复审 F2）");
        eq(bogus.status, 400, "未知 type ⇒ 400 且落 field=type（校验在入队之前）");
        eq(bogus.body.error.detail.field, "type");
        eq(badname.status, 400, "非法文件名 ⇒ 400 且落 field=name（后端 _validated_target 同款）");
        eq(badname.body.error.detail.field, "name");
        eq(ghost.status, 404, "契约 ③：名字不在映射表 ⇒ 404，不给「什么都没做却显示已完成」的绿任务");
        eq(ghost.body.error.code, "NOT_FOUND");
        eq(ghost.body.error.detail.name, "没配置.xml", "detail.name 供前端按业务结果显示");
        eq(gone.status, 404, "未知任务 id 404，消息与后端同口径（不存在或已结束太久）");
        ok(gone.body.error.message.indexOf("已结束太久") >= 0, "消息口径：" + gone.body.error.message);
      });
  });

  test("删除确认：说明影响数量与恢复方式，确认后直接删除，不要求输入文件名", function () {
    var calls = [];
    var origDel = MC.api.del;
    MC.api.del = function (path) { calls.push(path); return Promise.resolve({ deleted: true, recovery: "x" }); };
    var app = {
      p: function (rest) { return "/api/v1/p/default" + rest; },
      closeTab: function () {},
      refreshFiles: function () {},
      syncRevision: function () {},
    };
    MC.App.prototype.deleteFile.call(app, { name: "config.xml", archive_count: 3, record_count: 1 });
    var backdrops = document.body.querySelectorAll(".modal-backdrop");
    var backdrop = backdrops[backdrops.length - 1];
    var dialog = backdrop.querySelector(".modal");
    var text = dialog.textContent;
    ok(/3 个历史版本/.test(text) && /1 条修改记录/.test(text), "确认框要说明影响数量：" + text);
    ok(/恢复区/.test(text), "要说明删除后去哪里恢复：" + text);
    ok(!dialog.querySelector("input"), "删除配置文件不要求输入文件名");
    var okBtn = dialog.querySelector(".btn-danger");
    ok(okBtn && !okBtn.disabled, "确认按钮可直接点击");
    ok(/config\.xml/.test(text), "标题里带上被删的文件名");
    ok(calls.length === 0, "打开确认框时不发请求");
    okBtn.click();
    return Promise.resolve().then(function () {
      eq(calls.length, 1, "点确认后才删除");
      ok(calls[0].indexOf("config.xml") >= 0, "请求打到该文件：" + calls[0]);
    }).finally(function () {
      MC.api.del = origDel;
      document.body.querySelectorAll(".modal-backdrop").forEach(function (n) { n.remove(); });
    });
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
    ok(/仅管理员/.test(vc[0].text), "禁用时要说明原因：" + vc[0].text);
    ok(vc[1].checked === true, "访客默认落在「临时查看」");

    var deployer = MC.openImport(stub(true), { source: "local" });
    var dc = choices(deployer);
    ok(dc[0].disabled === false && dc[0].checked === true, "部署者默认可保存");
    ok(/24 小时/.test(dc[1].text), "临时项要说明过期时间");
    var input = deployer.el.querySelector('input[type="file"]');
    ok(input && input.multiple, "本地导入可一次选择多个文件");
    var dt = new DataTransfer();
    dt.items.add(new File(["<R/>"], "a.xml"));
    dt.items.add(new File(["{}"], "b.json"));
    input.files = dt.files;
    input.dispatchEvent(new Event("change"));
    var chips = deployer.el.querySelectorAll(".file-chip");
    eq(chips.length, 2, "两个文件都列出来");
    ok(/导入 2 个文件/.test(deployer.el.textContent), "多个文件时按钮改为批量导入");
    var nameField = [].slice.call(deployer.el.querySelectorAll(".field")).filter(function (f) { return f.querySelector("label") && f.querySelector("label").textContent.indexOf("文件名") === 0; })[0];
    ok(nameField && nameField.parentNode.classList.contains("hidden"), "多个文件不再填写同一个文件名");
    deployer.close();
  });

  test("本地导入：多个文件按各自文件名依次上传，已存在的先确认覆盖", function () {
    var calls = [];
    var orig = MC.api.upload;
    MC.api.upload = function (path, file, filename, query) {
      calls.push({ path: path, filename: filename, query: query || {} });
      return Promise.resolve({ archived: filename === "a.xml" ? "a_old.xml" : null, unchanged: false });
    };
    var opened = [];
    var app = {
      can: function () { return true; },
      session: { limits: { upload_max_bytes: 52428800, temp_ttl_hours: 24 } },
      files: [{ name: "a.xml" }],
      p: function (rest) { return "/api/v1/p/default" + rest; },
      syncRevision: function () {},
      refreshFiles: function () { return Promise.resolve(); },
      openFile: function (name) { opened.push(name); },
      go: function () {},
    };
    var m = MC.openImport(app, { source: "local" });
    var input = m.el.querySelector('input[type="file"]');
    var dt = new DataTransfer();
    dt.items.add(new File(["<R/>"], "a.xml"));
    dt.items.add(new File(["{}"], "b.json"));
    input.files = dt.files;
    input.dispatchEvent(new Event("change"));
    var btn = [].slice.call(m.el.querySelectorAll("button")).filter(function (b) { return b.textContent.indexOf("导入 2 个文件") >= 0; })[0];
    btn.click();
    return Promise.resolve().then(function () {
      var backs = document.body.querySelectorAll(".modal-backdrop");
      var confirm = backs[backs.length - 1];
      ok(/a\.xml/.test(confirm.textContent) && /覆盖/.test(confirm.textContent), "已存在的文件先确认覆盖");
      confirm.querySelector(".btn-primary").click();
      return new Promise(function (resolve) { setTimeout(resolve, 0); });
    }).then(function () {
      eq(calls.length, 2, "两个文件都要上传，实际 " + calls.length);
      eq(calls[0].filename, "a.xml");
      eq(calls[0].query.overwrite, "1");
      eq(calls[1].filename, "b.json");
      ok(!calls[1].query.overwrite, "新文件不带覆盖参数");
      eq(opened.length, 0, "一次导入多个文件时不逐个打开");
    }).finally(function () {
      MC.api.upload = orig;
      document.body.querySelectorAll(".modal-backdrop").forEach(function (n) { n.remove(); });
    });
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
    /* F2.3（卡 7 复审）：stubApi 换的是全部 9 个请求面，只查 5 个就漏掉另外 4 个
     * 可能泄漏的门（put/patch 早已在桩里、过去没人核对；request/download/fetchBlob/upload 是 F9 新拦的）。 */
    ok(MC.api.get === REAL_API.get, "MC.api.get 不是真身：异步用例的 stub 泄漏到了后续用例（卡 3 的桩污染）");
    ok(MC.api.post === REAL_API.post, "MC.api.post 不是真身：桩泄漏");
    ok(MC.api.put === REAL_API.put, "MC.api.put 不是真身：桩泄漏");
    ok(MC.api.patch === REAL_API.patch, "MC.api.patch 不是真身：桩泄漏");
    ok(MC.api.del === REAL_API.del, "MC.api.del 不是真身：桩泄漏");
    ok(MC.api.request === REAL_API.request, "MC.api.request 不是真身：桩泄漏");
    ok(MC.api.download === REAL_API.download, "MC.api.download 不是真身：桩泄漏");
    ok(MC.api.fetchBlob === REAL_API.fetchBlob, "MC.api.fetchBlob 不是真身：桩泄漏");
    ok(MC.api.upload === REAL_API.upload, "MC.api.upload 不是真身：桩泄漏");
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

  test("节点详情：XML 元素列出全部属性，param 节点也不把属性藏起来", function () {
    var app = {
      pid: "default",
      files: [],
      identity: { registered: true, name: "张三", role_label: "管理员", is_admin: true },
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
    var nodes = MC.mockParse.parse(XML, "x.xml").nodes;
    var doc = {
      format: "xml",
      file: { name: "x.xml", version: "current", updated_at: "2026-10-18T00:00:00Z", size: 64, node_count: nodes.p.length, sha256: "0123456789abcdef" },
      nodes: nodes,
    };
    var origGet = MC.api.get;
    MC.api.get = function (path) {
      if (/\/favorites$/.test(path)) return Promise.resolve({ groups: [] });
      if (/\/remarks$/.test(path)) return Promise.resolve({ items: [] });
      if (/\/archives$/.test(path) || /\/records$/.test(path)) return Promise.resolve({ items: [] });
      return Promise.resolve(doc);
    };
    var stage = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0;width:800px;height:500px;display:flex" });
    document.body.append(stage);
    var inst = MC.views.f(app, { kind: "f", arg: "x.xml", query: { node: "/R[1]" } });
    stage.append(inst.el);
    function attrSection() {
      return [...inst.el.querySelectorAll(".insp-section")].filter(function (s) {
        var title = s.querySelector(".section-title");
        return title && title.textContent.indexOf("属性") === 0;
      })[0];
    }
    return new Promise(function (resolve) { setTimeout(resolve, 0); }).then(function () {
      try {
        var sec = attrSection();
        ok(sec, "选中带属性的元素时，详情要有「属性」");
        ok(sec.textContent.indexOf("a") >= 0 && sec.textContent.indexOf("1") >= 0, "要列出属性名和值，实际：" + sec.textContent);
        var titles = [...inst.el.querySelectorAll(".insp-section .section-title")].map(function (n) { return n.textContent; });
        var sugAt = titles.findIndex(function (t) { return t.indexOf("修改建议") === 0; });
        var attrAt = titles.findIndex(function (t) { return t.indexOf("属性") === 0; });
        ok(sugAt >= 0 && attrAt > sugAt, "修改建议要在属性上面，实际顺序：" + titles.join(" / "));
        inst.update({ kind: "f", arg: "x.xml", query: { node: "/R[1]/param_1[1]" } });
        var paramSec = attrSection();
        ok(paramSec, "param_ 节点不能因为有参数信息就省掉属性");
        ok(paramSec.textContent.indexOf("min") >= 0 && paramSec.textContent.indexOf("0") >= 0, "min 要出现在属性里");
        ok(paramSec.textContent.indexOf("type") >= 0 && paramSec.textContent.indexOf("int") >= 0, "未折叠进范围的 type 也要出现在属性里");
      } finally {
        inst.destroy();
        stage.remove();
        MC.api.get = origGet;
      }
    });
  });

  test("DL：在 x、y 范围内给出曲线上 x 和 y 的最大最小值", function () {
    var zero = { k1: 0, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 0 };
    var line = { k1: 1, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 0 };
    var open = MC.dlCurveExtrema({ coeffs: line, x_min: null, x_max: 10, y_min: null, y_max: null }, "rp_energy");
    ok(open.bounded === false, "x 上下限不齐时不给数值");
    var full = MC.dlCurveExtrema({ coeffs: line, x_min: 0, x_max: 10, y_min: null, y_max: null }, "rp_energy");
    eq(full.xMin, 0);
    eq(full.xMax, 10);
    eq(full.yMin, 0);
    eq(full.yMax, 10);
    var clipped = MC.dlCurveExtrema({ coeffs: line, x_min: 0, x_max: 10, y_min: 2, y_max: 8 }, "rp_energy");
    eq(clipped.xMin, 2);
    eq(clipped.xMax, 8);
    eq(clipped.yMin, 2);
    eq(clipped.yMax, 8);
    var quad = MC.dlCurveExtrema({ coeffs: { k1: 0, k2: 1, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 0 }, x_min: -2, x_max: 2, y_min: null, y_max: null }, "rp_energy");
    eq(quad.yMin, 0);
    eq(quad.yMax, 4);
    eq(quad.xMin, -2);
    eq(quad.xMax, 2);
    var blocked = MC.dlCurveExtrema({ coeffs: zero, x_min: 0, x_max: 1, y_min: null, y_max: null, trans_coeff: 1, power_conv_coeff: 0 }, "pp_energy");
    ok(blocked.ready === false, "功率转换系数为 0 时不算范围");
  });

  test("DL：缩放包住边界与计算点，并标出极值点", function () {
    function coeffs(extra) {
      var c = { k1: 0, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 0 };
      Object.keys(extra || {}).forEach(function (k) { c[k] = extra[k]; });
      return c;
    }
    function near(a, b, tol) { ok(Math.abs(a - b) <= (tol || 1e-3), a + " 与 " + b + " 相差过大"); }
    var quad = { coeffs: coeffs({ k1: -6, k2: 1, b: 9 }), x_min: -10, x_max: 10, y_min: null, y_max: null };
    var curve = MC.dlPlotFrame(quad, "rp_energy", []);
    ok(curve.xLo < -10 && curve.xHi > 10, "x 上下界要留在画面内");
    ok(curve.yLo < 0 && curve.yHi > 169, "没有计算点时按曲线本身取 y");
    eq(curve.turns.length, 1);
    near(curve.turns[0].x, 3);
    near(curve.turns[0].y, 0);
    eq(curve.turns[0].kind, "min");

    var steep = { coeffs: coeffs({ k8: 1 }), x_min: 0, x_max: 2, y_min: 0, y_max: 10 };
    var fit = MC.dlPlotFrame(steep, "rp_energy", [{ x: 5, y: 3, status: "out_of_x_range", label: "1" }]);
    ok(fit.xLo < 0 && fit.xHi > 5, "x 上下界和越界计算点都要入画");
    ok(fit.yLo < 0 && fit.yHi > 10 && fit.yLo < 3 && fit.yHi > 3, "y 上下界和计算点都要入画");
    ok(fit.yHi < 30, "陡峭曲线不能把边界和计算点挤出画面，实际 yHi=" + fit.yHi);

    var bare = { coeffs: coeffs({ k8: 1 }), x_min: 0, x_max: 2, y_min: null, y_max: null };
    var onlyPoint = MC.dlPlotFrame(bare, "rp_energy", [{ x: 1, y: 2, label: "1" }]);
    ok(onlyPoint.yLo < 2 && onlyPoint.yHi > 2 && onlyPoint.yHi < 20, "只有计算点时 y 围着点，不跟着 x^8 走");

    var cubic = { coeffs: coeffs({ k3: 1 }), x_min: -2, x_max: 2, y_min: null, y_max: null };
    eq(MC.dlPlotFrame(cubic, "rp_energy", []).turns.length, 0, "导数不变号的拐点不是极值");

    var waved = { coeffs: coeffs({ k1: -1, k3: 1 / 3 }), x_min: -2, x_max: 2, y_min: null, y_max: null };
    var waves = MC.dlPlotFrame(waved, "rp_energy", []).turns;
    eq(waves.length, 2);
    eq(waves[0].kind, "max");
    eq(waves[1].kind, "min");
    near(waves[0].x, -1);
    near(waves[1].x, 1);
    near(waves[0].y, 2 / 3);
    near(waves[1].y, -2 / 3);

    var flipped = { coeffs: coeffs({ k2: 1 }), x_min: -2, x_max: 2, y_min: null, y_max: null, trans_coeff: -1, power_conv_coeff: 1 };
    var top = MC.dlPlotFrame(flipped, "pp_energy", []).turns;
    eq(top.length, 1);
    eq(top[0].kind, "max");
    near(top[0].x, 0);
    near(top[0].y, 0);

    var nearVertex = MC.dlPlotFrame(
      { coeffs: coeffs({ k2: 1 }), x_min: -4, x_max: 4, y_min: null, y_max: 20 },
      "rp_energy",
      [{ x: 3, y: 9, label: "1" }]
    );
    ok(nearVertex.yLo < 0 && nearVertex.yHi > 20, "顶点离计算点和 y 上限不远时要一起入画");
    eq(nearVertex.turns.length, 1);
    near(nearVertex.turns[0].x, 0);
    near(nearVertex.turns[0].y, 0);

    var farPeak = MC.dlPlotFrame({ coeffs: coeffs({ k2: -1, b: 1000 }), x_min: -2, x_max: 2, y_min: 0, y_max: 1 }, "rp_energy", []);
    eq(farPeak.turns.length, 0, "离 y 上下界很远的极值不拉大画面");
    ok(farPeak.yLo < 0 && farPeak.yHi > 1 && farPeak.yHi < 8, "y 上下界仍占住画面");
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

  /* 卡 9 / M3-09（INH-1894）追加：mock 的修改记录契约必须与后端同口径。
   * 旧 mock 每次「立即拉取」都无条件 `records.unshift(...)` 并回 `success/已拉取`，
   * 于是 §10.3 与 C13 那条「内容与最新一条相同就不新建记录文件」在 mock 里根本演示不出来，
   * 而 UC-30 的「再拉相同内容条数不变」正是这张卡的主验收。用例打在 `MC.mock.handle` 上，
   * 断的是**可观察结果**（列表条数与文件名、任务条目状态、files 修订号），不是源码文字。 */
  test("mock §10.3 / C13：相同内容的记录拉取不新建文件、不递增 files", function () {
    function call(method, path, body) {
      return MC.mock.handle({ method: method, path: path, query: {}, body: body, headers: {} });
    }
    /* mock 的模拟步骤是 setTimeout(900) 逐条推进：这里必须**轮询到任务结束**再断言，
     * 但轮询本身有上限，超时是失败而不是跳过（浮空链一律 return，见文件头那条 C1）。 */
    function waitFinished(id, tries) {
      return call("GET", "/tasks/" + id).then(function (r) {
        var t = r.body && r.body.task;
        if (t && (t.state === "succeeded" || t.state === "partial" || t.state === "failed")) return t;
        if (tries <= 0) throw new Error("mock 任务没有在轮询上限内结束：" + id);
        return new Promise(function (resolve) {
          setTimeout(resolve, 40);
        }).then(function () {
          return waitFinished(id, tries - 1);
        });
      });
    }
    function names(r) {
      return r.body.items.map(function (x) { return x.filename; });
    }
    function later(fn) {
      return new Promise(function (resolve) {
        setTimeout(resolve, 40);
      }).then(fn);
    }
    /* 前面的契约用例「并发下发」后Leave了一条在跑的 mock 任务（`setTimeout(900)` 逐条推进）。
     * 按 §10.1 / 卡 6 复审 F2，`refresh_records` 与 `update_source`/`update_all` 在**同一机型内
     * 两两互斥**，直接 POST 会撞 409——那是契约正确，不是本用例要测的东西，所以先等队列空。 */
    function waitIdle(tries) {
      return call("GET", "/p/default/tasks").then(function (r) {
        var busy = (r.body.items || []).filter(function (t) {
          return t.state === "queued" || t.state === "running";
        });
        if (!busy.length) return null;
        if (tries <= 0) {
          throw new Error("default 机型里始终有在跑的更新类任务，无法开始拉取：" + busy.map(function (t) { return t.type; }).join(","));
        }
        return later(function () {
          return waitIdle(tries - 1);
        });
      });
    }
    var name = "scenario_a.xml"; // 默认映射里唯一带 record_url 的项，种子里已有两条记录
    var beforeFiles = null;
    var beforeNames = null;
    return waitIdle(120)
      .then(function () {
        return call("GET", "/p/default/revision");
      })
      .then(function (r) {
        beforeFiles = r.body.parts.files;
        return call("GET", "/p/default/files/" + encodeURIComponent(name) + "/records");
      })
      .then(function (r) {
        beforeNames = names(r);
        eq(beforeNames.length, 2, "种子里有两条修改记录");
        return call("POST", "/p/default/tasks", { type: "refresh_records", name: name });
      })
      .then(function (r) {
        eq(r.status, 202, "「立即拉取」只走 POST /tasks（页面不再自己定时拉）");
        return waitFinished(r.body.task.id, 80);
      })
      .then(function (task) {
        var item = task.result.items[0];
        eq(item.status, "unchanged", "mock 的「远端内容」就是最新那一条 ⇒ C13 判重必须生效：" + item.status);
        eq(item.label, "无变化", "文案与 `updates.LABELS` 同口径，不再是 mock 自造的「已拉取」");
        return call("GET", "/p/default/files/" + encodeURIComponent(name) + "/records");
      })
      .then(function (r) {
        eq(names(r), beforeNames, "相同内容不得新增记录文件：条数与名字都不许变");
        return call("GET", "/p/default/revision");
      })
      .then(function (r) {
        eq(r.body.parts.files, beforeFiles, "unchanged 拉取不递增 files（R5：不叫醒别人）");
      });
  });

  /* EXPECTED_CASES（钉死用例总数）已提到文件顶部两个监听之前（第三轮 nit）。 */
  function report() {
    var out = document.getElementById("out");
    var failed = 0;
    /* 幂等可重入：全局兜底可能在队尾 report 之后才触发，重画前先清空。 */
    while (out.firstChild) out.removeChild(out.firstChild);
    results.concat(traps).forEach(function (r) {
      var li = document.createElement("li");
      li.className = r.ok ? "pass" : "fail";
      li.textContent = (r.ok ? "✓ " : "✗ ") + r.name + (r.ok ? "" : " — " + r.err);
      if (!r.ok) failed++;
      out.appendChild(li);
    });
    var total = results.length + traps.length;
    if (results.length !== EXPECTED_CASES) {
      var drift = document.createElement("li");
      drift.className = "fail";
      drift.textContent = "✗ 用例注册数不符：预期 " + EXPECTED_CASES + "，实际注册 " + results.length + "（有用例丢失或没注册上）";
      out.appendChild(drift);
      failed++;
      total++;
    }
    var sum = document.getElementById("summary");
    sum.textContent = failed ? failed + " FAILED / " + total : "ALL PASSED (" + total + ")";
    sum.className = failed ? "fail" : "pass";
    document.title = sum.textContent;
  }

  /* ===================== 配置管理（M5-09 / M5-11） ===================== */

  test("config-api：错误码归类覆盖门禁/无权限/冲突/离线", function () {
    var f = MC.configApi.failure;
    eq(f({ code: "CONFIG_DISABLED", status: 503 }).kind, "disabled");
    eq(f({ code: "FORBIDDEN", status: 403 }).kind, "forbidden");
    eq(f({ code: "POLICY_BLOCKED", status: 403 }).kind, "forbidden");
    eq(f({ code: "REVISION_CONFLICT", status: 409 }).kind, "conflict");
    eq(f({ code: "PLAN_STALE", status: 409 }).kind, "conflict");
    eq(f({ code: "IDEMPOTENCY_MISMATCH", status: 409 }).kind, "conflict");
    eq(f({ code: "VALUE_OUT_OF_RANGE", status: 422 }).kind, "invalid");
    eq(f({ code: "CORRUPT_DATA", status: 503 }).kind, "corrupt");
    eq(f({ status: 0, message: "网络断了" }).kind, "network");
    eq(f({ code: "SOMETHING_NEW", status: 418 }).kind, "server", "未知码不得当成冲突");
  });

  test("config-api：失败信息带 field，便于把错误落到输入框", function () {
    var info = MC.configApi.failure({ code: "VALUE_OUT_OF_RANGE", status: 422, message: "太高", detail: { field: "pulse" } });
    eq(info.field, "pulse");
    eq(info.message, "太高");
    ok(MC.configApi.isRetryable({ status: 0 }), "网络错误可重试");
    ok(!MC.configApi.isRetryable({ code: "FORBIDDEN", status: 403 }), "403 不该提示重试");
  });

  test("config-api：草稿按机型隔离，can() 之外不影响服务端状态", function () {
    MC.configApi.clearDraft("p1", "file:a.xml");
    MC.configApi.clearDraft("p2", "file:a.xml");
    ok(!MC.configApi.hasDraft("p1"), "初始无草稿");
    MC.configApi.setDraft("p1", "file:a.xml", { note: "现场核对" });
    ok(MC.configApi.hasDraft("p1"), "写入后应有草稿");
    ok(!MC.configApi.hasDraft("p2"), "不得跨机型串草稿");
    eq(MC.configApi.getDraft("p1", "file:a.xml").note, "现场核对");
    eq(MC.configApi.getDraft("p1", "file:b.xml"), undefined);
    MC.configApi.clearDraft("p1", "file:a.xml");
    ok(!MC.configApi.hasDraft("p1"), "清除后应无草稿");
  });

  test("config-api：确认不了就明说，不当成成功", function () {
    eq(MC.configApi.conflictMessage({ kind: "disabled" }), null);
    ok(MC.configApi.conflictMessage({ kind: "conflict", code: "PLAN_STALE" }).indexOf("已保留") > 0);
  });

  test("参数登记：四种检索维度都能命中", function () {
    var items = [
      { id: "pm_000008", name: "脉冲宽度", category: "时序", aliases: [{ name: "Pulse" }], definitions: [{ release_id: "sr_2", xml_name: "PulseWidth", json_key: "PULSE_WIDTH", unit: "s" }] },
      { id: "pm_000030", name: "轴1速度", category: "运动", definitions: [{ release_id: "sr_2", xml_name: "Velocity", json_key: "AXIS1_VELOCITY", unit: "mm/s" }] },
    ];
    eq(MC.configUi.filterParameters(items, "").length, 2, "空关键词返回全部");
    eq(MC.configUi.filterParameters(items, "脉冲").map(function (x) { return x.id; }), ["pm_000008"]);
    eq(MC.configUi.filterParameters(items, "pm_000030").map(function (x) { return x.id; }), ["pm_000030"]);
    eq(MC.configUi.filterParameters(items, "PulseWidth").map(function (x) { return x.id; }), ["pm_000008"]);
    eq(MC.configUi.filterParameters(items, "axis1_velocity").map(function (x) { return x.id; }), ["pm_000030"], "JSON 键不区分大小写");
    eq(MC.configUi.filterParameters(items, "Pulse").map(function (x) { return x.id; }), ["pm_000008"], "历史名也算");
    eq(MC.configUi.filterParameters(items, "不存在").length, 0);
  });

  test("参数登记：版本列去重排序、单元格含单位与修订号", function () {
    var items = [
      { id: "a", definitions: [{ release_id: "sr_2", release_label: "1.1.0", xml_name: "PulseWidth", unit: "s", revision_no: 2 }] },
      { id: "b", definitions: [{ release_id: "sr_1", release_label: "1.0.0", xml_name: "Pulse", unit: "ms" }, { release_id: "sr_2", release_label: "1.1.0", xml_name: "PulseWidth" }] },
    ];
    var columns = MC.configUi.versionColumns(items);
    eq(columns.map(function (c) { return c.label; }), ["1.0.0", "1.1.0"]);
    eq(MC.configUi.cellText({ xml_name: "PulseWidth", json_key: "PULSE_WIDTH", unit: "s", revision_no: 2 }), "PulseWidth / PULSE_WIDTH (s, r2)");
    eq(MC.configUi.cellText(null), "—");
    var rows = MC.configUi.buildRows(items, columns);
    eq(rows[1].cells.sr_1, "Pulse (ms)");
    eq(rows[0].cells.sr_1, undefined, "缺定义时留空，由视图显示破折号");
  });

  test("参数登记：缺列不伪造数据", function () {
    var rows = MC.configUi.buildRows([{ id: "a", definitions: [] }], []);
    eq(rows.length, 1);
    eq(rows[0].cells, {});
  });

  /* ---- M6-04：定义表单 ↔ 契约的互转与必填校验 ---- */

  test("定义表单：定位文本与 {path, attribute} 双向可逆", function () {
    eq(MC.configUi.textToLoc(""), null, "空文本不是定位");
    eq(MC.configUi.textToLoc("PulseWidth"), { path: [{ name: "PulseWidth", occurrence: 1 }], attribute: null });
    eq(
      MC.configUi.textToLoc("Axis[2]/Velocity"),
      { path: [{ name: "Axis", occurrence: 2 }, { name: "Velocity", occurrence: 1 }], attribute: null },
      "同名节点用 [n] 标序号"
    );
    eq(
      MC.configUi.textToLoc("PulseWidth@unit"),
      { path: [{ name: "PulseWidth", occurrence: 1 }], attribute: "unit" },
      "@ 后缀是属性名"
    );
    eq(MC.configUi.locToText({ path: [{ name: "Axis", occurrence: 2 }, { name: "Velocity", occurrence: 1 }], attribute: null }), "Axis[2]/Velocity");
    eq(MC.configUi.locToText({ path: [{ name: "PulseWidth", occurrence: 1 }], attribute: "unit" }), "PulseWidth@unit");
    eq(MC.configUi.locToText(null), "");
  });

  test("定义表单：草稿转请求体，缺名称/定位/理由一律拦下", function () {
    var empty = MC.configUi.buildDefinitionPayload({});
    ok(empty.errors.length >= 3, "三个必填项都要报出来");
    ok(empty.payload === undefined, "校验失败不构造请求体");

    var noReason = MC.configUi.buildDefinitionPayload({ xml_name: "PulseWidth", xml_loc: "PulseWidth" });
    eq(noReason.errors.length, 1);
    ok(noReason.errors[0].indexOf("理由") >= 0, "保存理由必填");

    var okPayload = MC.configUi.buildDefinitionPayload({
      xml_name: " PulseWidth ",
      xml_loc: "PulseWidth",
      json_key: "PULSE_WIDTH",
      type: "decimal",
      unit: "s",
      min: "0",
      max: "",
      remarks: "单位改为秒",
      reason: "1.1.0 起改为秒",
    });
    eq(okPayload.errors, undefined);
    eq(okPayload.payload.xml_name, "PulseWidth", "名称去首尾空格");
    eq(okPayload.payload.constraints, { min: "0", max: null });
    eq(okPayload.payload.reason, "1.1.0 起改为秒");

    var nonNumeric = MC.configUi.buildDefinitionPayload({ xml_name: "X", xml_loc: "X", reason: "r", type: "text", min: "1" });
    ok(nonNumeric.errors[0].indexOf("数值") > 0, "非数值类型不许填上下界");
  });

  test("定义表单：修改前后摘要只列真正变化的字段", function () {
    var before = MC.configUi.draftFromDefinition({
      xml: { name: "Pulse", loc: { path: [{ name: "Pulse", occurrence: 1 }], attribute: null } },
      json: { key: "PULSE" },
      type: "decimal",
      unit: "ms",
      constraints: { min: "0", max: "10" },
    });
    eq(before.xml_loc, "Pulse");
    eq(before.min, "0");
    var changes = MC.configUi.diffSummary(before, Object.assign({}, before, { xml_name: "PulseWidth", unit: "s" }));
    eq(changes.map(function (c) { return c.field; }), ["xml_name", "unit"]);
    eq(changes[1].before, "ms");
    eq(changes[1].after, "s");
    eq(MC.configUi.diffSummary(before, before), [], "没改就没有摘要");
    eq(MC.configUi.diffSummary({ max: "" }, { max: "" }), []);
    eq(MC.configUi.diffSummary({}, { unit: "s" })[0].before, "（空）", "空值显示成占位而不是空白");
  });

  test("定义表单：带 CAS 期望值时一并进请求体", function () {
    var built = MC.configUi.buildDefinitionPayload({
      xml_name: "PulseWidth",
      xml_loc: "PulseWidth",
      reason: "并发保护",
      expected_definition_id: "dr_000009",
    });
    eq(built.payload.expected_definition_id, "dr_000009");
    var blank = MC.configUi.buildDefinitionPayload({
      xml_name: "PulseWidth",
      xml_loc: "PulseWidth",
      reason: "首次定义",
      expected_definition_id: "",
    });
    eq(blank.payload.expected_definition_id, "", "空串表示「我看到的时候还没有定义」");
    var untouched = MC.configUi.buildDefinitionPayload({ xml_name: "X", xml_loc: "X", reason: "r" });
    ok(!("expected_definition_id" in untouched.payload), "不传就不带这个键，避免误判");
  });

  /* ---- M6-10：现场对账三栏（原值 / 基线 / 拟值） ---- */

  test("现场对账：类型化值按类型渲染，null 不当空串", function () {
    var v = MC.configUi.valueText;
    eq(v(null), "—", "没有值就是没有，不编一个空串");
    eq(v({ type: "null", text: "null", unit: null }), "null", "JSON null 与「没有这个参数」不是一回事");
    eq(v({ type: "decimal", text: "1.5", unit: "ms" }), "1.5 ms");
    eq(v({ type: "decimal", text: "1.5", unit: null }), "1.5");
    eq(v({ type: "string", text: "", unit: null }), "（空串）", "空串与 null 要能分开看");
    eq(v({ type: "bool", text: "false", unit: null }), "false");
    eq(v({ type: "string", text: "快一点", unit: null }), "快一点");
  });

  test("现场对账：按差异分组，需要决定的排前面", function () {
    var rows = [
      { parameter_id: "pm_2", name: "脉冲宽度", diff: "same" },
      { parameter_id: "pm_1", name: "最大速度", diff: "changed" },
      { parameter_id: "pm_3", name: "未映射项", diff: "unknown" },
      { parameter_id: "pm_4", name: "基线独有的", diff: "missing_observed" },
    ].map(MC.configUi.reconcileRow);
    var groups = MC.configUi.reconcileGroups(rows);
    eq(groups.map(function (g) { return g.status; }), ["changed", "missing_observed", "unknown", "same"]);
    eq(groups[0].label, "已变更");
    eq(groups[0].rows[0].parameter_id, "pm_1");
    ok(MC.configUi.reconcileRow({ diff: "changed" }).needsDecision, "已变更要人决定");
    ok(MC.configUi.reconcileRow({ diff: "missing_baseline" }).needsDecision, "基线没覆盖也要人决定");
    ok(!MC.configUi.reconcileRow({ diff: "same" }).needsDecision, "一致不用决定");
    eq(MC.configUi.reconcileGroups([]), [], "没有行就没有组");
    // 后端将来加了新状态：不能被分组悄悄吞掉
    var extra = MC.configUi.reconcileGroups([MC.configUi.reconcileRow({ diff: "renamed" })]);
    eq(extra.length, 1);
    eq(extra[0].label, "renamed", "未知状态原样露出，不伪装成 known");
  });

  test("现场对账：计数摘要只列非零项", function () {
    var sum = MC.configUi.reconcileSummary({ changed: 2, same: 3, unknown: 0, missing_observed: 1 });
    eq(sum, "已变更 2 · 现场未采到 1 · 一致 3", "顺序按展示优先级，不按对象键序");
    eq(MC.configUi.reconcileSummary({}), "暂无可对账的参数");
    eq(MC.configUi.reconcileSummary({ same: 0 }), "暂无可对账的参数", "全零等于没有");
  });

  test("现场对账：过期观测要提醒而不是静默替换", function () {
    var stale = MC.configUi.reconcileNotices({
      stale_observation: true,
      newer_observation_id: "ob_000009",
      has_observation: true,
      has_baseline: true,
      observation: { id: "ob_000001", status: "parsed", unresolved_keys: [] },
      baseline: { id: "bl_000001", scope: "model", state: "approved" },
    });
    eq(stale.length, 1);
    eq(stale[0].level, "warn", "换数据这种事不能是 info");
    ok(stale[0].text.indexOf("ob_000009") > 0, "要把新观测的 ID 说出来");
    ok(stale[0].text.indexOf("不会静默替换") > 0);

    var none = MC.configUi.reconcileNotices({ has_observation: false, has_baseline: false });
    eq(none.map(function (n) { return n.key; }), ["no-observation", "no-baseline"]);
    eq(none[1].level, "warn", "没基线时拟值只是建议，必须提示");

    var failed = MC.configUi.reconcileNotices({
      has_observation: true,
      has_baseline: true,
      observation: { id: "ob_1", status: "parse_failed", unresolved_keys: [] },
      baseline: { id: "bl_1", scope: "model", state: "approved" },
    });
    eq(failed[0].key, "parse-failed");
    eq(failed[0].level, "error");

    var common = MC.configUi.reconcileNotices({
      has_observation: true,
      has_baseline: true,
      observation: { id: "ob_1", status: "parsed", unresolved_keys: ["A", "B", "C", "D", "E"] },
      baseline: { id: "bl_1", scope: "common", state: "approved" },
    });
    eq(common.map(function (n) { return n.key; }), ["scope-common", "unresolved"]);
    ok(common[0].text.indexOf("所有继承它的机型") > 0, "选通用层前要说清影响范围");
    ok(common[1].text.indexOf("5 个键") >= 0 && common[1].text.indexOf("等") > 0, "多于 4 个要省略");
  });

  test("现场对账：拟值只是草稿，待提交行必须与基线不同", function () {
    var payload = {
      rows: [
        {
          parameter_id: "pm_1",
          name: "最大速度",
          diff: "changed",
          observed: { status: "mapped", value: { type: "decimal", text: "1.2", unit: "m/s" } },
          baseline: { value: { type: "decimal", text: "1", unit: "m/s" }, intent: "override" },
          proposed: { value: { type: "decimal", text: "1.2", unit: "m/s" }, source: "observed" },
        },
        {
          parameter_id: "pm_2",
          name: "脉冲宽度",
          diff: "same",
          observed: { status: "mapped", value: { type: "decimal", text: "1", unit: "ms" } },
          baseline: { value: { type: "decimal", text: "1", unit: "ms" } },
          proposed: { value: { type: "decimal", text: "1", unit: "ms" }, source: "baseline" },
        },
      ],
    };
    var fresh = MC.configUi.applyDraft(payload, null);
    eq(fresh.rows[0].editText, "1.2 m/s", "默认带出服务端建议");
    eq(fresh.rows[0].adopted, true, "要决定的默认勾上");
    eq(fresh.rows[0].baselineIntent, "override");
    eq(fresh.pending.length, 1, "一致的行即使勾上也不算待提交");
    eq(fresh.pending[0].parameter_id, "pm_1");
    eq(fresh.pending[0].before, "1 m/s");
    eq(fresh.pending[0].after, "1.2 m/s");

    var edited = MC.configUi.applyDraft(payload, { edits: { pm_1: "1.5 m/s" }, adopt: { pm_2: false } });
    eq(edited.rows[0].editText, "1.5 m/s", "草稿覆盖服务端建议");
    eq(edited.rows[0].changed, true, "改过要标出来");
    eq(edited.pending[0].after, "1.5 m/s");
    eq(
      MC.configUi.applyDraft(payload, { adopt: { pm_1: false } }).pending.length,
      0,
      "取消勾选就不进待提交"
    );
  });

  test("现场对账：文件页三栏真实渲染，切走再回来草稿还在", function () {
    var payload = {
      profile_id: "default",
      logical_file: { id: "lf_000001", display_name: "轴参数", role: "observation", family_id: "f1" },
      observation: { id: "ob_000001", captured_at: "2026-10-20T10:00:00Z", status: "parsed", unresolved_keys: [] },
      baseline: { id: "bl_000001", scope: "model", state: "approved" },
      rows: [
        {
          parameter_id: "pm_1",
          name: "最大速度",
          category: "运动",
          known: true,
          diff: "changed",
          observed: { status: "mapped", value: { type: "decimal", text: "1.2", unit: "m/s" } },
          baseline: { value: { type: "decimal", text: "1", unit: "m/s" }, intent: "inherit" },
          proposed: { value: { type: "decimal", text: "1.2", unit: "m/s" }, source: "observed" },
        },
        {
          parameter_id: "pm_2",
          name: "脉冲宽度",
          category: "信号",
          known: true,
          diff: "same",
          observed: { status: "mapped", value: { type: "decimal", text: "1", unit: "ms" } },
          baseline: { value: { type: "decimal", text: "1", unit: "ms" }, intent: "pin" },
          proposed: { value: { type: "decimal", text: "1", unit: "ms" }, source: "baseline" },
        },
      ],
      counts: { changed: 1, same: 1 },
      stale_observation: true,
      newer_observation_id: "ob_000009",
      has_baseline: true,
      has_observation: true,
    };
    var app = { pid: "default", can: function () { return true; } };
    var origStatus = MC.configApi.profileStatus;
    var origReconcile = MC.configApi.reconcile;
    MC.configApi.profileStatus = function () { return Promise.resolve({ baselines: 1, observations: 2 }); };
    MC.configApi.reconcile = function () { return Promise.resolve(payload); };
    MC.configApi.clearDraft("default", "file:cfg.json");
    var stage = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0" });
    document.body.append(stage);
    function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
    function restore() {
      MC.configApi.profileStatus = origStatus;
      MC.configApi.reconcile = origReconcile;
      MC.configApi.clearDraft("default", "file:cfg.json");
      stage.remove();
    }
    MC.configUi.renderFileSection(app, { pid: "default", name: "cfg.json", version: "current" }, stage);
    return tick()
      .then(function () {
        eq(
          [].map.call(stage.querySelectorAll("thead th"), function (t) { return t.textContent; }),
          ["参数", "现场原值", "批准基线", "拟值（可改）", "状态"],
          "三栏必须并列摆出来"
        );
        eq(stage.querySelectorAll("tbody tr").length, 2);
        ok(stage.textContent.indexOf("不会静默替换") > 0, "过期观测要显式提醒");
        eq(stage.querySelector(".badge.badge-warning").textContent, "已变更");

        var input = stage.querySelector('input[aria-label="拟值：最大速度"]');
        ok(input, "拟值栏要可编辑");
        input.value = "1.5 m/s";
        input.dispatchEvent(new Event("input"));
        eq(MC.configApi.getDraft("default", "file:cfg.json").edits.pm_1, "1.5 m/s", "编辑落到草稿");
        ok(stage.textContent.indexOf("待提交 1 行") > 0, "改完立刻反映待提交行数");

        /* 切标签 = 视图被销毁后重新渲染：草稿必须还在 */
        MC.configUi.renderFileSection(app, { pid: "default", name: "cfg.json", version: "current" }, stage);
        return tick();
      })
      .then(function () {
        eq(
          stage.querySelector('input[aria-label="拟值：最大速度"]').value,
          "1.5 m/s",
          "切走再回来，没提交的输入还在"
        );
        ok(stage.textContent.indexOf("待提交 1 行") > 0, "重渲染后仍按草稿算待提交");
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });

  /* ---- M6-11 / M6-12：配置变更单 ---- */

  test("配置变更：状态标签与分组顺序（要人动手的排前面）", function () {
    var ui = MC.changesUi;
    eq(ui.stateLabel("submitted"), "待批准");
    eq(ui.stateLabel("approved"), "已发布");
    eq(ui.stateLabel("nonsense"), "nonsense", "未知状态原样露出，不伪装成已知");
    eq(ui.stateTone("approved"), "badge-success");
    eq(ui.stateTone("rejected"), "badge-danger");
    eq(ui.stateTone("draft"), "");

    var groups = ui.groupByState([
      { id: "c3", state: "approved" },
      { id: "c1", state: "draft" },
      { id: "c2", state: "submitted" },
    ]);
    eq(groups.map(function (g) { return g.state; }), ["draft", "submitted", "approved"]);
    eq(ui.groupByState([]), []);
    var extra = ui.groupByState([{ id: "c9", state: "archived" }]);
    eq(extra[0].state, "archived", "后端加了新状态也不会被吞");
  });

  test("配置变更：改动行只算真正写入且变了的", function () {
    var ui = MC.changesUi;
    var v = function (text, unit) { return { type: "decimal", text: text, unit: unit || null }; };
    eq(
      ui.changedCount({
        changes: [
          { writes_value: true, from: v("650", "ms"), to: v("700", "ms") },
          { writes_value: false, from: v("650", "ms"), to: v("650", "ms") },
          {
            writes_value: true,
            from: v("650", "ms"),
            to: v("650", "ms"),
            from_intent: "override",
            to_intent: "pin",
          },
          { writes_value: true, from: v("1", "mm/s"), to: v("1", "mm/s"), from_intent: "override", to_intent: "override" },
        ],
      }),
      2,
      "值变了算一条；只改意图（走 pin）也算；原样写回不算"
    );
    eq(ui.changedCount({}), 0);
    eq(ui.changedCount(null), 0);
  });

  test("配置变更：逐项行模型把决议翻成人话", function () {
    var ui = MC.changesUi;
    var row = ui.rowModel({
      parameter_id: "pm_1",
      name: "脉冲宽度",
      category: "时序",
      known: true,
      decision: "pin",
      writes_value: true,
      from: { type: "decimal", text: "650", unit: "ms" },
      to: { type: "decimal", text: "700", unit: "ms" },
      from_intent: "override",
      to_intent: "pin",
      reason: "产线要求",
    });
    eq(row.decisionLabel, "钉住");
    eq(row.fromText, "650 ms");
    eq(row.toText, "700 ms");
    eq(row.changed, true);
    eq(row.reason, "产线要求");
    eq(ui.rowModel({ decision: "clear_pin" }).decisionLabel, "取消钉住");
    eq(ui.rowModel({ decision: "weird" }).decisionLabel, "weird");
  });

  test("配置变更：批准阻碍逐条说清，全清才允许批", function () {
    var ui = MC.changesUi;
    var submitted = {
      item: { state: "submitted" },
      changed_rows: 2,
      staleness: { stale: false, reasons: [] },
    };
    eq(ui.approveBlockers(submitted), []);
    eq(ui.canApprove(submitted), true);

    var draft = ui.approveBlockers({ item: { state: "draft" }, changed_rows: 1, staleness: { stale: false } });
    eq(draft.length, 1);
    ok(draft[0].text.indexOf("待批准") > 0, "要告诉用户只有待批准能批");

    var stale = ui.approveBlockers({
      item: { state: "submitted" },
      changed_rows: 1,
      staleness: { stale: true, reasons: ["baseline_revision_changed", "definition_set_changed"] },
    });
    eq(stale.length, 2);
    ok(stale[0].text.indexOf("改过") > 0, "基线又改过要用人话说");
    eq(stale[0].level, "warn");

    eq(ui.canApprove({ item: { state: "submitted" }, changed_rows: 0, staleness: { stale: false } }), false);
  });

  test("配置变更：列表与详情按状态渲染，批准按钮受能力键控制", function () {
    var item = {
      id: "cs_000001",
      title: "改脉冲宽度",
      reason: "产线要求",
      state: "submitted",
      baseline_id: "bl_000001",
      baseline_revision: "r1",
      changes: [{ writes_value: true, from: { type: "decimal", text: "650", unit: "ms" }, to: { type: "decimal", text: "700", unit: "ms" } }],
      submitted_by: "管理员乙",
      submitted_at: "2026-10-20T10:00:00Z",
      revision: "r2",
    };
    var detail = {
      item: item,
      rows: [
        {
          parameter_id: "pm_1",
          name: "脉冲宽度",
          category: "时序",
          decision: "use_new",
          writes_value: true,
          from: { type: "decimal", text: "650", unit: "ms" },
          to: { type: "decimal", text: "700", unit: "ms" },
          reason: "产线要求",
        },
      ],
      changed_rows: 1,
      staleness: { stale: false, reasons: [] },
      allowed: ["approved", "rejected", "withdrawn"],
    };
    var app = {
      pid: "p1",
      can: function (key) { return key === "config_change_approve" || key === "config_change_submit"; },
    };
    var origList = MC.configApi.changeSets;
    var origDetail = MC.configApi.changeSet;
    MC.configApi.changeSets = function () { return Promise.resolve({ items: [item] }); };
    MC.configApi.changeSet = function () { return Promise.resolve(detail); };
    var host = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0" });
    document.body.append(host);
    function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
    MC.changesUi.render(app, host);
    return tick()
      .then(function () {
        ok(host.textContent.indexOf("待批准") > 0, "列表里要有状态徽标");
        eq(host.querySelectorAll("tbody tr").length, 1);
        var view = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent === "查看";
        })[0];
        ok(view, "每行都要能点开");
        view.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        ok(host.textContent.indexOf("批准并发布") > 0, "管理员应看到批准按钮");
        var approve = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent === "批准并发布";
        })[0];
        ok(approve, "批准按钮必须渲染出来");
        eq(approve.disabled, false, "没有阻碍时按钮可用");
        ok(host.textContent.indexOf("同一个事务") > 0, "要说清批准即发布");
        MC.configApi.changeSets = origList;
        MC.configApi.changeSet = origDetail;
        host.remove();
      })
      .catch(function (err) {
        MC.configApi.changeSets = origList;
        MC.configApi.changeSet = origDetail;
        host.remove();
        throw err;
      });
  });

  /* ---- M6-14：交付产物 ---- */

  test("交付产物：状态词表不把待验说成已验证", function () {
    var ui = MC.deliveryUi;
    eq(ui.statusLabel("unverified"), "待验");
    eq(ui.statusLabel("verified"), "已核验");
    eq(ui.statusLabel("failed"), "核验失败");
    eq(ui.statusLabel("archived"), "待验", "后端加了新状态也按最保守的待验处理");
    eq(ui.statusLabel(undefined), "待验", "缺字段不能变成已核验");
    eq(ui.statusTone("verified"), "badge-success");
    eq(ui.statusTone("failed"), "badge-danger");
    eq(ui.statusTone("unverified"), "badge-warning");
    ok(ui.statusHint("unverified").indexOf("结构") > 0, "待验要解释清楚它只保证了什么");
  });

  test("交付产物：行模型把字节、修订、改动数翻成人话", function () {
    var ui = MC.deliveryUi;
    var row = ui.rowModel({
      id: "ar_000001",
      filename: "axis.xml",
      baseline_id: "bl_000001",
      baseline_revision: "r3",
      byte_size: 2048,
      blob_sha256: "0123456789abcdef0123456789abcdef",
      same_count: 5,
      edits: [{ parameter_id: "pm_1" }, { parameter_id: "pm_2" }],
      delivery_status: "unverified",
      created_at: "2026-08-01 10:00:00",
      created_by: "管理员乙",
      validation: { ok: true },
    });
    eq(row.edits, 2);
    eq(row.same, 5);
    eq(row.revision, "r3");
    eq(row.sizeText, "2.0 KB");
    eq(row.shaShort, "0123456789ab");
    eq(row.statusLabel, "待验");
    eq(row.ok, true);
    var bad = ui.rowModel({ id: "ar_2", validation: { ok: false } });
    eq(bad.ok, false, "自检不通过要能看出来");
    eq(bad.sizeText, "—", "没给字节数就明说没有，不编一个 0 B");
    eq(ui.rowModel({}).statusLabel, "待验", "缺省对象也按待验");
  });

  test("交付产物：生成前置条件（没选/没批准/没权限都不许点）", function () {
    var ui = MC.deliveryUi;
    var admin = { can: function () { return true; } };
    var member = { can: function () { return false; } };
    eq(ui.canGenerate(admin, null).allowed, false);
    eq(ui.canGenerate(admin, null).reason.indexOf("先选") >= 0, true);
    eq(ui.canGenerate(admin, { id: "bl_1", state: "draft" }).allowed, false);
    eq(ui.canGenerate(admin, { id: "bl_1", state: "approved" }).allowed, true);
    eq(ui.canGenerate(member, { id: "bl_1", state: "approved" }).allowed, false);
    eq(ui.canGenerate(member, { id: "bl_1", state: "approved" }).reason.indexOf("权限") >= 0, true);
  });

  test("交付产物：待验提示必须说清 D6 缺口，全验过才改口", function () {
    var ui = MC.deliveryUi;
    var warn = ui.deliveryNotice([{ status: "unverified" }, { status: "unverified" }]);
    eq(warn.level, "warn");
    ok(warn.text.indexOf("2 份") > 0, "要说清有几份待验");
    ok(warn.text.indexOf("D6") > 0, "缺口的名字要露出来，不然没人追");
    eq(ui.deliveryNotice([]).level, "info", "空列表不报缺口");
    eq(ui.deliveryNotice([{ status: "verified" }]).level, "info");
  });

  test("交付产物：基线下拉区分层级，标签带修订号", function () {
    var ui = MC.deliveryUi;
    var options = ui.baselineOptions([
      { id: "bl_1", scope: "common", revision: "r2" },
      { id: "bl_2", scope: "model", revision: "r1", profile_id: "p1" },
    ]);
    eq(options.length, 2);
    eq(options[0].label.indexOf("通用") === 0, true);
    eq(options[0].label.indexOf("r2") > 0, true);
    eq(options[1].label.indexOf("机型") === 0, true);
    eq(options[1].label.indexOf("p1") > 0, true);
    eq(ui.baselineOptions(null), []);
  });

  test("交付产物：文件页真实渲染，复用不冒充新建、待验说清", function () {
    var host = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0" });
    document.body.append(host);
    var origArtifacts = MC.configApi.artifacts;
    var origArtifact = MC.configApi.artifact;
    var origBaselines = MC.configApi.baselines;
    var origGen = MC.configApi.generateArtifact;
    var calls = [];
    MC.configApi.artifacts = function () {
      return Promise.resolve({
        items: [
          {
            id: "ar_000001",
            filename: "axis.xml",
            baseline_id: "bl_000001",
            baseline_revision: "r2",
            byte_size: 1024,
            blob_sha256: "0123456789abcdef0123456789abcdef",
            same_count: 3,
            edits: [{ parameter_id: "pm_1", loc: "/R[1]/P[1]", from: "650", to: "700" }],
            delivery_status: "unverified",
            created_at: "2026-08-01 10:00:00",
            created_by: "管理员乙",
            generator: "config_export/v1",
            template_sha256: "abcdef0123456789",
            validation: { ok: true, checks: { structure_unchanged: true }, warnings: ["不宣称逐字节保真"] },
          },
        ],
        total: 1,
      });
    };
    MC.configApi.baselines = function () {
      return Promise.resolve({ items: [{ id: "bl_000001", scope: "common", revision: "r2", state: "approved" }] });
    };
    MC.configApi.artifact = function () {
      return Promise.resolve({
        item: { template_sha256: "abcdef0123456789" },
        edits: [{ parameter_id: "pm_1", loc: "/R[1]/P[1]", from: "650", to: "700" }],
        validation: { ok: true, checks: { structure_unchanged: true }, warnings: ["不宣称逐字节保真"] },
        delivery_status: "unverified",
      });
    };
    MC.configApi.generateArtifact = function (pid, payload) {
      calls.push(payload);
      return Promise.resolve({ item: { id: "ar_000001" }, reused: true });
    };
    var app = { pid: "p1", can: function () { return true; } };
    function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }

    function restore() {
      MC.configApi.artifacts = origArtifacts;
      MC.configApi.artifact = origArtifact;
      MC.configApi.baselines = origBaselines;
      MC.configApi.generateArtifact = origGen;
      host.remove();
    }

    MC.deliveryUi.render(app, host, { pid: "p1" });
    return tick()
      .then(function () {
        ok(host.textContent.indexOf("交付（目标文件）") >= 0, "分段标题要在");
        eq(host.querySelectorAll("tbody tr").length, 1);
        ok(host.textContent.indexOf("待验") > 0, "交付状态是待验");
        ok(host.textContent.indexOf("D6") > 0, "缺口要在页面上说清");
        var gen = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent.indexOf("生成目标文件") >= 0;
        })[0];
        ok(gen, "要能生成");
        eq(gen.disabled, false, "已批准基线 + 有权限 → 可点");
        gen.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        eq(calls.length, 1, "点一次只发一次请求");
        eq(calls[0].baseline_id, "bl_000001");
      })
      .then(function () {
        var detail = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent === "详情";
        })[0];
        ok(detail, "每行都要能看详情");
        detail.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        ok(host.textContent.indexOf("逐字节保真") > 0, "告警要显示出来，不能只存着");
        ok(host.textContent.indexOf("/R[1]/P[1]") > 0, "落点要能对照");
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });

  /* ---- M7-04：跨版本升级合并 ---- */

  test("版本升级：状态标签与分类可多重，不以颜色替代文本", function () {
    var ui = MC.upgradeUi;
    eq(ui.ACTION_LABELS.conflict, "冲突");
    eq(ui.ACTION_LABELS.from_new, "跟随新版");
    var model = ui.rowModel({
      parameter_id: "pm_1",
      name: "脉冲宽度",
      action: "conflict",
      reasons: ["pin_needs_confirm"],
      base: { value: { type: "decimal", text: "650", unit: "ms" } },
      ours: { value: { type: "decimal", text: "700", unit: "ms" } },
      theirs: { value: { type: "decimal", text: "0.7", unit: "s" } },
    });
    eq(model.actionLabel, "冲突");
    eq(model.tagLabels.indexOf("冲突") >= 0, true);
    eq(model.tagLabels.indexOf("固定值") >= 0, true, "pin 冲突要额外标出来");
    eq(model.tagLabels.indexOf("单位") >= 0, true, "单位不同要能被筛出来");
    eq(model.oursText, "700 ms");
    eq(model.needsDecision, true);
    // 分类是标签，不是颜色：删掉所有样式也读得懂
    eq(ui.classify({ action: "drop" }).indexOf("removed") >= 0, true);
    eq(ui.classify({ action: "from_new", theirs: { value: { type: "int", text: "1" } } }).indexOf("new") >= 0, true);
  });

  test("版本升级：过滤维度计数与命中", function () {
    var ui = MC.upgradeUi;
    var items = [
      { parameter_id: "a", action: "conflict" },
      { parameter_id: "b", action: "from_new", theirs: { value: { type: "int", text: "1" } } },
      { parameter_id: "c", action: "keep", base: { value: {} }, ours: { value: {} }, theirs: { value: {} } },
      { parameter_id: "d", action: "manual", blockers: [{ code: "unknown_parameter" }] },
    ];
    var counts = ui.countByFilter(items);
    eq(counts.all, 4);
    eq(counts.conflict, 1);
    eq(counts.manual, 1);
    eq(counts.new, 1);
    eq(counts.same, 1);
    eq(items.filter(function (i) { return ui.matchFilter(i, "conflict"); }).length, 1);
    eq(items.filter(function (i) { return ui.matchFilter(i, "all"); }).length, 4);
    eq(items.filter(function (i) { return ui.matchFilter(i, "pinned"); }).length, 0);
  });

  test("版本升级：批量只填无冲突项，冲突与需人工留着", function () {
    var ui = MC.upgradeUi;
    var items = [
      { parameter_id: "a", action: "from_new", decision: "use_new" },
      { parameter_id: "b", action: "keep", decision: null },
      { parameter_id: "c", action: "conflict", decision: null },
      { parameter_id: "d", action: "manual", decision: "manual" },
    ];
    var filled = ui.bulkResolve(items, {});
    eq(filled.a, "use_new");
    eq(filled.c, undefined, "冲突不许被批量决定");
    eq(filled.d, undefined, "需人工的项不许被批量决定");
    var pending = ui.pendingOf({ items: items }, filled);
    eq(pending, ["c", "d"]);
  });

  test("版本升级：无共同基准时不许一键合并", function () {
    var ui = MC.upgradeUi;
    var blocked = ui.canApply(
      { three_way: false, items: [], blockers: [] },
      {}
    );
    eq(blocked.allowed, false);
    eq(blocked.reason.indexOf("没有共同基准") >= 0, true);

    var ready = ui.canApply(
      {
        three_way: true,
        items: [{ parameter_id: "a", action: "conflict", decision: null }],
        blockers: [],
      },
      {}
    );
    eq(ready.allowed, false);
    eq(ready.reason.indexOf("1 项没有决议") >= 0, true);

    var okPlan = ui.canApply(
      { three_way: true, items: [{ parameter_id: "a", action: "from_new", decision: "use_new" }], blockers: [] },
      {}
    );
    eq(okPlan.allowed, true, okPlan.reason);
    eq(ui.canApply(null, {}).allowed, false, "没有计划就别提落地");
  });

  test("版本升级：阻断项可以显式 drop 化解", function () {
    var ui = MC.upgradeUi;
    var plan = {
      three_way: true,
      items: [{ parameter_id: "a", action: "manual", decision: "manual" }],
      blockers: [{ parameter_id: "a", code: "unknown_parameter" }],
    };
    eq(ui.canApply(plan, {}).allowed, false);
    eq(ui.canApply(plan, { a: "drop" }).allowed, true, "显式 drop 之后不再阻断");
  });

  test("版本升级：摘要只说非零项", function () {
    var ui = MC.upgradeUi;
    eq(ui.summaryText(null), "尚未计算");
    eq(
      ui.summaryText({
        items: [
          { action: "from_new" },
          { action: "from_new" },
          { action: "conflict" },
        ],
      }),
      "跟随新版 2 · 冲突 1"
    );
    eq(ui.summaryText({ items: [{ action: "keep" }] }), "保持 1");
    eq(ui.summaryText({ items: [] }), "没有需要处理的参数");
  });

  test("版本升级：真实渲染三栏 + 决议下拉 + 落地按钮受控", function () {
    var host = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0" });
    document.body.append(host);
    var orig = {
      releases: MC.configApi.releases,
      common: MC.configApi.upgradeCommonPlan,
      create: MC.configApi.createCommonUpgrade,
    };
    var created = [];
    MC.configApi.releases = function () {
      return Promise.resolve({ items: [{ id: "sr_000002", version_label: "2.0.0" }] });
    };
    MC.configApi.upgradeCommonPlan = function (query) {
      return Promise.resolve({
        mode: "common_release_upgrade",
        three_way: true,
        publishable: false,
        target: { release_id: query.release_id, variant_id: "tv_000002" },
        previous: { release_id: "sr_000001", baseline_revision: "r2" },
        warnings: [],
        items: [
          {
            parameter_id: "pm_1",
            name: "脉冲宽度",
            action: "conflict",
            decision: null,
            allowed: ["use_new", "inherit_old", "manual", "pin", "clear_pin"],
            reasons: ["both_sides_changed"],
            blockers: [],
            base: { value: { type: "decimal", text: "650", unit: "ms" } },
            ours: { value: { type: "decimal", text: "900", unit: "ms" } },
            theirs: { value: { type: "decimal", text: "700", unit: "ms" } },
            candidate: { type: "decimal", text: "900", unit: "ms" },
          },
          {
            parameter_id: "pm_2",
            name: "最大速度",
            action: "from_new",
            decision: "use_new",
            allowed: [],
            reasons: ["ours_unchanged"],
            blockers: [],
            base: { value: { type: "decimal", text: "100", unit: "mm/s" } },
            ours: { value: { type: "decimal", text: "100", unit: "mm/s" } },
            theirs: { value: { type: "decimal", text: "120", unit: "mm/s" } },
            candidate: { type: "decimal", text: "120", unit: "mm/s" },
          },
        ],
        blockers: [],
      });
    };
    MC.configApi.createCommonUpgrade = function (payload) {
      created.push(payload);
      return Promise.resolve({ item: { id: "bl_000099" }, applied: { explicit: ["pm_1"] } });
    };
    var app = { pid: "p1", can: function () { return true; } };
    function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
    function restore() {
      MC.configApi.releases = orig.releases;
      MC.configApi.upgradeCommonPlan = orig.common;
      MC.configApi.createCommonUpgrade = orig.create;
      host.remove();
    }

    MC.upgradeUi.render(app, host, { pid: "p1" });
    return tick()
      .then(function () {
        ok(host.textContent.indexOf("版本升级") >= 0, "分段标题要在");
        // 刚进来只有空态：必须先点「计算合并」才有计划
        ok(host.textContent.indexOf("先选目标版本再计算") >= 0, "空态要说下一步");
        var run = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent.indexOf("计算合并") >= 0;
        })[0];
        ok(run, "要有计算按钮");
        run.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        ok(host.textContent.indexOf("650 ms") >= 0 && host.textContent.indexOf("900 ms") >= 0, "三栏要把三方值摆开");
        eq(host.querySelectorAll("tbody tr").length, 2);
        var apply = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent === "生成升级草稿";
        })[0];
        ok(apply, "要有落地按钮");
        eq(apply.disabled, true, "冲突没决议时不许落地");
        ok(host.textContent.indexOf("1 项没有决议") >= 0, "理由要说人话");
        return null;
      })
      .then(function () {
        // 逐行做决议：第一行保留本地，第二行已经按建议填好
        var select = host.querySelectorAll("tbody tr")[0].querySelector("select");
        select.value = "inherit_old";
        select.dispatchEvent(new Event("change"));
        return tick();
      })
      .then(function () {
        var apply = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent === "生成升级草稿";
        })[0];
        eq(apply.disabled, false, "决议齐了就可以落地");
        apply.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        eq(created.length, 1);
        eq(created[0].release_id, "sr_000002");
        eq(created[0].decisions.pm_1, "inherit_old");
        eq(created[0].decisions.pm_2, "use_new", "无冲突项按建议带上");
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });

  /* ---- 经验与历史（M7-05 / M7-06）---- */

  test("经验上提：可上提与排除都要说清理由", function () {
    var ui = MC.lifecycleUi;
    var yes = ui.promoteRow({
      parameter_id: "pm_1",
      name: "脉冲宽度",
      eligible: true,
      from: { type: "decimal", text: "650", unit: "ms" },
      to: { type: "decimal", text: "900", unit: "ms" },
      intent: "override",
    });
    ok(yes.eligible, "可上提标记要在");
    eq(yes.from, "650 ms");
    eq(yes.to, "900 ms");
    eq(yes.stateText, "可上提");

    var no = ui.promoteRow({
      parameter_id: "pm_2",
      name: "最大速度",
      eligible: false,
      excluded_reason: "device_calibration",
      excluded_message: "值来自单台设备的标定，不能进机型/通用层",
      from: null,
      to: { type: "decimal", text: "110", unit: "mm/s" },
    });
    ok(no.stateText.indexOf("排除") === 0, "排除要带前缀");
    ok(no.stateText.indexOf("单台设备") >= 0, "理由要照服务端的原文说");
    eq(no.from, "—", "没有现值也要有占位，不能空白");
  });

  test("经验上提：摘要与阻断都不许含糊", function () {
    var ui = MC.lifecycleUi;
    var text = ui.promoteSummary({
      items: [{ eligible: true }, { eligible: false }, { eligible: false }],
      eligible: ["pm_1"],
      blockers: [{ code: "evidence_revoked", message: "来源对象已经不存在" }],
    });
    ok(text.indexOf("共 3 项") >= 0 && text.indexOf("可上提 1 项") >= 0, "要有总数与可上提数");
    ok(text.indexOf("排除 2 项") >= 0, "排除数要写出来");
    ok(text.indexOf("1 个阻断项") >= 0, "阻断要明确说不能建单");

    var lines = ui.blockerLines([
      { code: "release_mismatch", message: "不在同一版本", parameter_id: "pm_9" },
    ]);
    eq(lines.length, 1);
    ok(lines[0].indexOf("版本不一致") >= 0, "代号要翻成人话");
    ok(lines[0].indexOf("pm_9") >= 0, "要指出哪个参数");
    eq(ui.promoteSummary(null), "", "没有计划就不要编摘要");
  });

  test("经验上提：设备层如实写明未启用", function () {
    var notice = MC.lifecycleUi.deviceNotice(3);
    eq(notice.level, "warn");
    ok(notice.text.indexOf("未启用") >= 0, "要直接说未启用");
    ok(notice.text.indexOf("M7-07/08") >= 0, "要指向延期的卡号");
    ok(notice.text.indexOf("3 条") >= 0, "有设备基线时要报数");
    ok(MC.lifecycleUi.deviceNotice().text.indexOf("设备基线") < 0, "没给数字就不要瞎写");
  });

  test("历史回滚：行模型与摘要说的是'回滚到哪一版'", function () {
    var ui = MC.lifecycleUi;
    var row = ui.restoreRow({
      parameter_id: "pm_1",
      name: "脉冲宽度",
      from: { type: "decimal", text: "1000", unit: "ms" },
      to: { type: "decimal", text: "900", unit: "ms" },
      from_intent: "override",
      to_intent: "override",
      value_changed: true,
    });
    eq(row.from, "1000 ms");
    eq(row.to, "900 ms");
    eq(row.stateText, "值变化");

    var text = ui.restoreSummary({
      changed: 2,
      source: { revision: "r2" },
      baseline: { head_revision: "r3" },
    });
    ok(text.indexOf("2 个参数") >= 0 && text.indexOf("r2") >= 0, "要说清改几个、回到哪一版");
    ok(
      ui.restoreSummary({ changed: 0, source: { revision: "r2" } }).indexOf("不会改变") >= 0,
      "零差异要说'不会改变'"
    );
  });

  test("迁移与补发：提示条不粉饰", function () {
    var ui = MC.lifecycleUi;
    var blocked = ui.migrationNotice({
      items: [],
      blockers: [{ code: "incomparable", parameter_id: "pm_2", message: "单位换不过去" }],
    });
    eq(blocked.level, "warn");
    ok(blocked.text.indexOf("不能迁移") >= 0, "有阻断就要说不能迁");
    ok(blocked.text.indexOf("pm_2") >= 0, "要点名参数");

    var okPlan = ui.migrationNotice({ items: [{ parameter_id: "pm_1" }], blockers: [] });
    eq(okPlan.level, "info");
    ok(okPlan.text.indexOf("不覆盖源基线") >= 0, "要说清新建而不是覆盖");

    var stale = ui.reissueNotice({
      baseline_reference_unchanged: true,
      current_batch: { batch_id: "rb_1" },
      template: { in_current_batch: false },
      changed: 2,
    });
    eq(stale.level, "warn");
    ok(stale.text.indexOf("不在当前批次") >= 0, "模板被换掉要说出来");
    ok(stale.text.indexOf("2 个参数不同") >= 0, "差异数要报");

    var clean = ui.reissueNotice({
      baseline_reference_unchanged: true,
      current_batch: { batch_id: "rb_1" },
      template: { in_current_batch: true },
      changed: 0,
    });
    eq(clean.level, "info");
    ok(clean.text.indexOf("仍在当前出口批次") >= 0, "没变化就照实说没变化");

    var broken = ui.reissueNotice({ baseline_reference_unchanged: false });
    eq(broken.level, "warn");
    ok(broken.text.indexOf("不该发生") >= 0, "引用被改要当异常报");
  });

  test("经验与历史：四张卡都在，上提按钮出计划后受控", function () {
    var host = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0" });
    document.body.append(host);
    var orig = {
      baselines: MC.configApi.baselines,
      variants: MC.configApi.variants,
      detail: MC.configApi.baselineDetail,
      plan: MC.configApi.promoteCommonPlan,
    };
    MC.configApi.baselines = function () {
      return Promise.resolve({ items: [{ id: "bl_000001", scope: "model", revision: "r2" }] });
    };
    MC.configApi.variants = function () {
      return Promise.resolve({ items: [{ id: "tv_000002", family_id: "cf_000001" }] });
    };
    MC.configApi.baselineDetail = function () {
      return Promise.resolve({ item: { revision: "r2" }, revisions: ["r1", "r2"] });
    };
    MC.configApi.promoteCommonPlan = function () {
      return Promise.resolve({
        mode: "promote_to_common",
        can_apply: false,
        publishable: false,
        items: [
          {
            parameter_id: "pm_1",
            name: "脉冲宽度",
            eligible: true,
            from: { type: "decimal", text: "650", unit: "ms" },
            to: { type: "decimal", text: "900", unit: "ms" },
          },
          {
            parameter_id: "pm_2",
            name: "最大速度",
            eligible: false,
            excluded_reason: "observation_sourced",
            excluded_message: "值来自一次现场采集，属于单次观测而不是经验",
          },
        ],
        eligible: ["pm_1"],
        blockers: [{ code: "evidence_revoked", message: "来源对象已经不存在" }],
      });
    };
    var app = { pid: "p1", can: function () { return true; } };
    function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
    function restore() {
      MC.configApi.baselines = orig.baselines;
      MC.configApi.variants = orig.variants;
      MC.configApi.baselineDetail = orig.detail;
      MC.configApi.promoteCommonPlan = orig.plan;
      host.remove();
    }

    MC.lifecycleUi.render(app, host, { pid: "p1" });
    return tick()
      .then(function () {
        ok(host.textContent.indexOf("经验上提") >= 0, "上提卡要在");
        ok(host.textContent.indexOf("设备 → 机型") >= 0, "设备卡要在");
        ok(host.textContent.indexOf("未启用") >= 0, "设备层要写明未启用");
        ok(host.textContent.indexOf("历史回滚") >= 0, "回滚卡要在");
        ok(host.textContent.indexOf("跨变体迁移") >= 0, "迁移卡要在");
        ok(host.textContent.indexOf("出口补发") >= 0, "补发卡要在");
        var run = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent.indexOf("计算机型") >= 0;
        })[0];
        ok(run, "要有上提计划按钮");
        run.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        eq(host.querySelectorAll("table")[0].querySelectorAll("tbody tr").length, 2, "两行都要列出来");
        ok(host.textContent.indexOf("650 ms") >= 0 && host.textContent.indexOf("900 ms") >= 0, "要把现值与机型值摆开");
        ok(host.textContent.indexOf("证据已撤销") >= 0, "阻断要翻成人话");
        var create = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent === "生成上提变更单";
        })[0];
        ok(create, "要有建单按钮");
        eq(create.disabled, true, "有阻断时不许建单");
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  /* ---- 交付登记与回读核验（M7-09 / M7-10）---- */

  test("交付登记：成功要证据、失败要原因、待导入不能登记", function () {
    var ui = MC.deliveryUi;
    eq(ui.resultLabel("pending"), "待导入");
    eq(ui.resultLabel("something-new"), "待导入", "未知状态按最保守的待导入");
    ok(ui.registrationIssues({ result: "pending" })[0].indexOf("待导入") >= 0);
    ok(ui.registrationIssues({ result: "declared_ok" }).join(" ").indexOf("执行时间") >= 0);
    ok(
      ui.registrationIssues({ result: "declared_ok", executed_at: "2026-10-22T10:00:00Z" })
        .join(" ").indexOf("证据") >= 0,
      "成功没有证据必须拦下"
    );
    ok(ui.registrationIssues({ result: "failed" }).join(" ").indexOf("原因") >= 0);
    ok(ui.registrationIssues({ result: "insufficient_evidence" }).join(" ").indexOf("缺什么") >= 0);
    eq(
      ui.registrationIssues({
        result: "declared_ok",
        executed_at: "2026-10-22T10:00:00Z",
        evidence_note: "回读一致",
      }),
      [],
      "证据和实际执行时间都有才放行"
    );
  });

  test("交付状态：M7-09 登记四态优先于旧待验标记", function () {
    var ui = MC.deliveryUi;
    var row = ui.rowModel({
      delivery: { result: "declared_ok", attempts: 2, label: "已声明成功" },
      delivery_status: "unverified",
    });
    eq(row.statusLabel, "已声明成功");
    eq(row.deliveryAttempts, 2);
    eq(ui.deliveryNotice([row]).level, "info");
    var failed = ui.rowModel({ delivery: { result: "failed", attempts: 1 } });
    eq(failed.statusLabel, "失败");
    eq(ui.deliveryNotice([failed]).level, "warn");
    var pending = ui.rowModel({ delivery: { result: "pending", attempts: 0 } });
    eq(pending.statusLabel, "待导入");
  });

  test("交付摘要：设备层关闭时不许说所有设备完成", function () {
    var ui = MC.deliveryUi;
    ok(ui.deliverySummaryNotice(null).text.indexOf("待导入") >= 0, "没登记就明说是待导入");
    var notice = ui.deliverySummaryNotice({
      total: 2,
      counts: { declared_ok: 1, failed: 1, insufficient_evidence: 0 },
      result: "declared_ok",
      can_claim_all_devices: false,
    });
    eq(notice.level, "warn", "有失败记录要提示");
    ok(notice.text.indexOf("设备层未启用") >= 0, "要说明设备层没开");
    ok(notice.text.indexOf("所有设备") >= 0, "要明确不能声称所有设备完成");
  });

  test("回读核验：逐参数状态与范围声明不宣传全量一致", function () {
    var ui = MC.deliveryUi;
    var text = ui.verifySummary({
      aggregate: { matched: 2, missing: 1, mismatched: 0, unobservable: 0 },
      conclusion: "incomplete",
      scope_complete: true,
    });
    ok(text.indexOf("观测里没有 1") >= 0, "缺项要单列，不能当 0");
    ok(text.indexOf("证据不完整") >= 0);
    var limited = ui.verifySummary({
      aggregate: { matched: 2, missing: 0, mismatched: 0, unobservable: 1 },
      conclusion: "incomplete",
      scope_complete: false,
    });
    ok(limited.indexOf("不能宣传全量一致") >= 0, "有未登记节点时必须收窄结论");
    eq(ui.verifyRowModel({ status: "something-new" }).statusLabel, "观测里没有", "未知行的状态按最保守处理");
    eq(ui.conclusionTone("verified"), "badge-success");
    eq(ui.conclusionTone("version_mismatch"), "badge-warning");
  });


  /* ---- 审计与谱系（M7-11）---- */

  test("审计：筛选只保留非空项，标题有回退", function () {
    var ui = MC.auditUi;
    eq(ui.buildQuery({ action: " baseline.approve ", category: "", actor: "管理员丁" }).action, "baseline.approve");
    eq(ui.buildQuery({ action: "x" }).actor, undefined);
    eq(ui.buildQuery({}).limit, 30);
    eq(ui.eventTitle({ action: "artifact.generate" }), "artifact.generate");
    eq(ui.eventTitle({ reason: "初始基线" }), "初始基线");
    ok(ui.eventMeta({ actor_label: "管理员丁", at: "2026-10-22T10:00:00Z" }).indexOf("管理员丁") >= 0);
    var rows = ui.revisionRows({ revisions: [{ revision: "r2", created_at: "t", created_by: "甲", state: "approved" }] });
    eq(rows[0].revision, "r2");
    eq(ui.provenanceGroups({ provenance: { related: { baseline: [{ kind_label: "基线", id: "bl_1" }] } } })[0].kind, "baseline");
  });

  test("审计：列表与来源抽屉走真实 API 形状", function () {
    var host = MC.util.h("div", { style: "position:absolute;left:-9999px;top:0" });
    document.body.append(host);
    var origAudit = MC.configApi.audit;
    var origHistory = MC.configApi.history;
    var calls = [];
    MC.configApi.audit = function (pid, query) {
      calls.push({ pid: pid, query: query });
      return Promise.resolve({
        items: [
          {
            seq: 3,
            at: "2026-10-22T10:00:00Z",
            action: "baseline.approve",
            reason: "初始基线",
            actor_label: "管理员丁",
            objects_detail: [
              { kind: "baseline", kind_label: "基线", id: "bl_000001", revision: "r2", label: "通用 bl_000001" },
            ],
          },
        ],
        next_cursor: null,
      });
    };
    MC.configApi.history = function (objectId, revision) {
      return Promise.resolve({
        object: { id: objectId, state: "approved" },
        object_kind: "baseline",
        object_kind_label: "基线",
        revisions: [{ revision: "r2", created_at: "t", created_by: "管理员丁", state: "approved" }],
        provenance: { related: { artifact: [{ id: "ar_1", kind_label: "交付产物", label: "axis.xml" }] } },
        events: [],
      });
    };
    var app = { pid: "p1" };
    function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
    function restore() {
      MC.configApi.audit = origAudit;
      MC.configApi.history = origHistory;
      host.remove();
    }
    MC.auditUi.render(app, host, { pid: "p1" });
    return tick()
      .then(function () {
        eq(calls[0].pid, "p1");
        eq(calls[0].query.limit, 30);
        eq(host.querySelectorAll("tbody tr").length, 1);
        ok(host.textContent.indexOf("初始基线") >= 0);
        var target = [].filter.call(host.querySelectorAll("button"), function (b) {
          return b.textContent.indexOf("基线") >= 0;
        })[0];
        ok(target, "对象要能点开谱系");
        target.dispatchEvent(new Event("click"));
        return tick();
      })
      .then(function () {
        ok(host.textContent.indexOf("修订链") >= 0);
        ok(host.textContent.indexOf("axis.xml") >= 0);
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  test("M16-31：更新管理壳、三个内容页与旧设置入口收口", function () {
    var app = settingsApp(true);
    var original = MC.api.requestV3;
    MC.api.requestV3 = function () { return Promise.resolve({ items: [], total: 0, next_cursor: null }); };
    var inst = MC.views.updates(app, { kind: "updates", arg: "sources", query: {} });
    eq(inst.title, "更新管理");
    eq(inst.el.querySelectorAll("[data-sync-tab]").length, 3);
    ok(inst.el.textContent.indexOf("更新源") >= 0, "默认展示更新源");
    var runs = inst.el.querySelector('[data-sync-tab="runs"]');
    ok(runs, "运行记录 tab 存在");
    runs.click();
    ok(inst.el.textContent.indexOf("运行记录") >= 0, "切换后展示运行记录");
    ok(app.paints.go >= 1, "切换 tab 走 app.go，不整页刷新");
    inst.destroy();
    inst.el.remove();
    MC.api.requestV3 = original;
  });

  test("SYNC-32：更新源列表、编辑 409 保留草稿与只读门控", function () {
    var calls = [];
    var listRevisionChanged = false;
    var original = MC.api.requestV3;
    var originalWid = MC.workspaces && MC.workspaces.scopeId;
    var source = {
      source_id: "src-test-1", revision: 3, name: "A.xml", kind: "http_file",
      display_path: "/synthetic/A.xml", resource_ref: "https://example.invalid/A.xml",
      record_source: "https://example.invalid/records", keyword: "alpha",
      enabled: true, schedule_mode: "custom", interval_hours: 6,
      updated_at: "2026-11-02T00:00:00Z",
    };
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-test"; };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET") {
        if (opts && opts.query && opts.query.status === "all") {
          return Promise.resolve(opts.query.cursor === "0"
            ? { items: [{ source_id: "other" }], total: 2, next_cursor: "1", query_revision: 5 }
            : { items: [Object.assign({}, source, { revision: 4, name: "远端名称" })], total: 2, next_cursor: null, query_revision: listRevisionChanged ? 6 : 5 });
        }
        return Promise.resolve({ items: [source], total: 1, next_cursor: null });
      }
      if (method === "PATCH") {
        return Promise.reject({ status: 409, code: "REVISION_CONFLICT", message: "changed" });
      }
      return Promise.resolve({ run: { run_id: "run-test-1" }, task: { id: "t1" } });
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalWid) MC.workspaces.scopeId = originalWid;
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "tasks_run" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "sources", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        ok(host.textContent.indexOf("A.xml") >= 0, "列表显示真实来源");
        ok(host.textContent.indexOf("/synthetic/A.xml") >= 0, "显示路径");
        ok(host.textContent.indexOf("6 小时") >= 0, "显示自定义频率");
        var edit = host.querySelector("[data-sync-edit]");
        ok(edit, "可写身份有编辑入口");
        edit.click();
        var modal = document.querySelector(".modal");
        ok(modal, "打开编辑抽屉");
        var inputs = modal.querySelectorAll("input.input");
        var nameInput = inputs[0];
        nameInput.value = "B.xml";
        var recordInput = [].filter.call(inputs, function (input) { return input.placeholder && input.placeholder.indexOf("修改记录") >= 0; })[0];
        ok(recordInput && recordInput.value.indexOf("records") >= 0, "旧 record_url 不丢");
        var keywordInput = inputs[4];
        ok(keywordInput && keywordInput.value === "alpha", "keyword 不丢");
        var save = modal.querySelector("[data-sync-save]");
        save.click();
        return tick().then(function () {
          ok(modal.textContent.indexOf("保留") >= 0, "409 保留草稿并提示");
          ok(document.querySelector(".modal"), "409 不关闭编辑框");
          ok(modal.querySelector("[data-sync-reload]"), "409 提供重新载入最新版本入口");
          modal.querySelector("[data-sync-reload]").click();
          return tick().then(function () {
            var compare = lastModal();
            ok(compare !== modal && compare.textContent.indexOf("远端名称") >= 0 && compare.textContent.indexOf("B.xml") >= 0, "对比服务端和本地草稿");
            ok(calls.some(function (c) { return c.opts.query && c.opts.query.cursor === "1"; }), "跨页按稳定 ID 找到来源");
            modalButton(compare, "取消").click();
            ok(modal.querySelectorAll("input.input")[0].value === "B.xml", "取消对比保留草稿");
            listRevisionChanged = true;
            modal.querySelector("[data-sync-reload]").click();
            return tick().then(function () {
              ok(modal.textContent.indexOf("读取时发生变化") >= 0, "分页期间版本变化必须停止恢复");
              eq(modal.querySelectorAll("input.input")[0].value, "B.xml", "分页变化时草稿不丢失");
              cleanOverlays();
            });
          });
        });
      })
      .then(function () {
        var run = host.querySelector("[data-sync-run]");
        ok(run, "可写身份有单项更新");
        run.click();
        return tick();
      })
      .then(function () {
        ok(calls.some(function (c) { return c.method === "PATCH" && c.opts.headers && c.opts.headers["If-Match"] === "3"; }), "保存带 If-Match revision");
        ok(calls.some(function (c) { return c.method === "POST" && c.path.indexOf("/run") >= 0; }), "单项更新投递 run");
        var readOnly = settingsApp(false);
        readOnly.can = function () { return false; };
        MC.api.requestV3 = function () { return Promise.resolve({ items: [source], total: 1, next_cursor: null }); };
        var ro = MC.views.updates(readOnly, { kind: "updates", arg: "sources", query: {} });
        return tick().then(function () {
          ok(!ro.el.querySelector("[data-sync-edit]"), "只读身份无编辑入口");
          ok(ro.el.textContent.indexOf("只读") >= 0, "只读身份显示只读");
          ro.destroy();
          ro.el.remove();
          inst.destroy();
          inst.el.remove();
          restore();
        });
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  function lastModal() {
    var all = document.querySelectorAll(".modal");
    return all[all.length - 1] || null;
  }
  function modalButton(root, text) {
    if (!root) return null;
    return [].slice.call(root.querySelectorAll("button")).filter(function (b) {
      return (b.textContent || "").trim() === text;
    })[0] || null;
  }

  test("R6：来源删除确认和批量处理不跨工作区", function () {
    var original = MC.api.requestV3, originalConfirm = MC.confirm, originalScope = MC.workspaces.scopeId, originalEpoch = MC.workspaces.epoch;
    var wid = "w-a", epoch = 1, resolveConfirm, resolvePatch, writes = [];
    MC.workspaces.scopeId = function () { return wid; };
    MC.workspaces.epoch = function () { return epoch; };
    MC.confirm = function () { return new Promise(function (resolve) { resolveConfirm = resolve; }); };
    MC.api.requestV3 = function (method, path) {
      if (method === "GET" && /\/sync\/sources$/.test(path)) return Promise.resolve({ items: ["a", "b"].map(function (id) { return { source_id: id, revision: 1, name: id, kind: "http_file", enabled: true }; }) });
      if (method === "PATCH" || method === "DELETE") { writes.push({ method: method, path: path }); return new Promise(function (resolve) { resolvePatch = resolve; }); }
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function (key) { return key === "settings_write"; };
    var inst = MC.views.updates(app, { arg: "sources" });
    function cleanup() { inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; MC.confirm = originalConfirm; MC.workspaces.scopeId = originalScope; MC.workspaces.epoch = originalEpoch; }
    return tick().then(function () {
      inst.el.querySelector('[data-sync-delete="a"]').click();
      wid = "w-b"; epoch++; resolveConfirm(true);
      return tick();
    }).then(function () {
      eq(writes.length, 0, "切换后确认删除不发请求");
      wid = "w-a"; epoch++; inst.update({ arg: "sources" });
      return tick();
    }).then(function () {
      var boxes = inst.el.querySelectorAll("[data-sync-select]");
      boxes[0].checked = true; boxes[0].dispatchEvent(new Event("change"));
      boxes = inst.el.querySelectorAll("[data-sync-select]");
      boxes[1].checked = true; boxes[1].dispatchEvent(new Event("change"));
      MC.confirm = function () { return Promise.resolve(true); };
      inst.el.querySelector("[data-sync-batch-disable]").click();
      return tick();
    }).then(function () {
      eq(writes.length, 1, "批量开始只发送首项");
      wid = "w-b"; epoch++; resolvePatch({ enabled: false, revision: 2 });
      return tick().then(tick);
    }).then(function () {
      eq(writes.length, 1, "切换后不发送后续项");
      ok(writes[0].path.indexOf("/w-a/") >= 0, "已发请求属于原工作区");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("SYNC-32B：新建来源含类型/连接/目标绑定，删除与批量动作保持筛选", function () {
    var calls = [];
    var original = MC.api.requestV3;
    var originalScope = MC.workspaces && MC.workspaces.scopeId;
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-32b"; };
    var rows = [
      { source_id: "s1", revision: 1, name: "A.xml", kind: "http_file", enabled: true, schedule_mode: "inherit", target: { kind: "file_archive", file_id: "file-1" } },
      { source_id: "s2", revision: 2, name: "B.xml", kind: "http_file", enabled: true, schedule_mode: "inherit", target: { kind: "file_archive", file_id: "file-2" } },
    ];
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && /\/sync\/sources$/.test(path)) return Promise.resolve({ items: rows.slice(), total: 2, next_cursor: null });
      if (method === "GET" && /\/connections$/.test(path)) return Promise.resolve({ items: [{ connection_id: "c1", name: "SDA 一", enabled: true }] });
      if (method === "GET" && /file-selector/.test(path)) return Promise.resolve({ items: [{ file_id: "file-1", name: "A.xml", current_version: "3" }] });
      if (method === "POST" && /\/sync\/source-tests$/.test(path)) {
        return Promise.resolve({ status: "succeeded", message: "内容已读取并通过格式校验；未保存或更新业务文件。", items: [] });
      }
      if (method === "POST" && /\/sync\/sources$/.test(path)) {
        return Promise.resolve({ source_id: "s3", revision: 1, name: "新来源", kind: "http_file", enabled: true });
      }
      if (method === "DELETE") return Promise.resolve({});
      if (method === "PATCH") return Promise.resolve({ source_id: path.split("/").pop(), revision: 9, enabled: opts.body.enabled });
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalScope) MC.workspaces.scopeId = originalScope;
      cleanOverlays();
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "tasks_run" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "sources", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        ok(host.querySelector("[data-sync-new]"), "可写身份有新建入口");
        ok(host.textContent.indexOf("最近结果") >= 0, "展示运行核验和候选结果");
        ok(host.textContent.indexOf("核验状态未提供") >= 0, "接口没有核验数据时不声称没有核验记录");
        eq(host.querySelectorAll("[data-sync-select]").length, 2, "每行都有勾选框");
        ok(host.querySelector("[data-sync-batch]"), "有批量工具条");
        host.querySelector('[data-sync-test="s1"]').click();
        return tick().then(tick);
      })
      .then(function () {
        var preview = calls.filter(function (c) { return c.method === "POST" && /\/sync\/source-tests$/.test(c.path); })[0];
        ok(preview, "来源测试调用真实内容校验接口");
        eq(preview.opts.body.name, "A.xml");
        ok(!preview.opts.body.source_id, "只发送来源输入字段");
        ok(/格式校验/.test(host.textContent), "显示服务端真实测试回执");
        var checks = host.querySelectorAll("[data-sync-select]");
        checks[0].checked = true;
        checks[0].dispatchEvent(new Event("change"));
        checks[1].checked = true;
        checks[1].dispatchEvent(new Event("change"));
        return tick();
      })
      .then(function () {
        var disable = host.querySelector("[data-sync-batch-disable]");
        ok(disable && !disable.disabled, "选中后批量停用可用");
        disable.click();
        return tick().then(function () {
          var confirm = lastModal();
          ok(confirm && confirm.textContent.indexOf("批量停用") >= 0, "批量为危险操作需确认");
          modalButton(confirm, "批量停用").click();
          return tick().then(tick);
        });
      })
      .then(function () {
        var patches = calls.filter(function (c) { return c.method === "PATCH"; });
        eq(patches.length, 2, "批量停用逐项 CAS");
        ok(patches.every(function (c) { return c.opts.body.enabled === false; }), "逐项写入 enabled=false");
        ok(patches.some(function (c) { return c.opts.headers["If-Match"] === "1"; }), "按各自 revision 做 If-Match");
        host.querySelector('[data-sync-delete="s1"]').click();
        return tick().then(function () {
          var confirm = lastModal();
          ok(confirm && confirm.textContent.indexOf("历史版本、候选和运行记录都会保留") >= 0, "删除说明数据保留范围");
          modalButton(confirm, "删除来源").click();
          return tick().then(tick);
        });
      })
      .then(function () {
        var del = calls.filter(function (c) { return c.method === "DELETE"; })[0];
        eq(del.path, "/api/v3/workspaces/w-32b/sync/sources/s1", "删除当前行来源");
        eq(del.opts.headers["If-Match"], "9", "删除用批量后的最新 revision 做 CAS");
        ok(!host.querySelector('[data-sync-source="s1"]'), "删除后局部移除该行");
        host.querySelector("[data-sync-new]").click();
        var modal = document.querySelector(".modal");
        ok(modal, "打开新建抽屉");
        eq(modal.querySelector("[data-sync-kind]").options.length, 4, "类型只列真实 kind");
        var name = modal.querySelectorAll("input.input")[0];
        name.value = "新来源";
        name.dispatchEvent(new Event("input"));
        var ref = modal.querySelectorAll("input.input")[1];
        ref.value = "https://example.invalid/new.xml";
        ref.dispatchEvent(new Event("input"));
        return tick().then(function () {
          var fileSelect = modal.querySelector("[data-sync-target-file]");
          ok(fileSelect, "目标绑定提供 FileResource 选择");
          fileSelect.value = "file-1";
          fileSelect.dispatchEvent(new Event("change"));
          modal.querySelector("[data-sync-save]").click();
          return tick().then(tick);
        });
      })
      .then(function () {
        var created = calls.filter(function (c) { return c.method === "POST" && /\/sync\/sources$/.test(c.path); })[0];
        ok(created, "新建走 POST /sync/sources");
        eq(created.opts.body.profile_id, "default", "新建带机型");
        eq(created.opts.body.target.kind, "file_archive");
        eq(created.opts.body.target.file_id, "file-1", "目标绑定写入 file_id");
        inst.destroy();
        inst.el.remove();
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });

  test("SYNC-32C：未保存草稿提示、失败重试与停用来源禁止更新", function () {
    var calls = [];
    var original = MC.api.requestV3;
    var originalScope = MC.workspaces && MC.workspaces.scopeId;
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-32c"; };
    var rows = [
      { source_id: "s1", revision: 4, name: "A.xml", kind: "http_file", enabled: true, schedule_mode: "inherit" },
      { source_id: "s2", revision: 1, name: "B.xml", kind: "http_file", enabled: false, schedule_mode: "inherit" },
    ];
    var failList = false;
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && /\/sync\/sources$/.test(path)) {
        if (failList) return Promise.reject({ status: 503, code: "STORAGE_FAILURE", message: "服务器写入失败" });
        return Promise.resolve({ items: rows.slice(), total: 2, next_cursor: null });
      }
      if (method === "GET" && /file-selector/.test(path)) return Promise.resolve({ items: [] });
      if (method === "GET" && /\/connections$/.test(path)) return Promise.resolve({ items: [] });
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalScope) MC.workspaces.scopeId = originalScope;
      cleanOverlays();
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "tasks_run" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "sources", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        var disabledRun = host.querySelector('[data-sync-run="s2"]');
        ok(disabledRun && disabledRun.disabled, "停用来源不能直接更新");
        host.querySelector('[data-sync-edit="s1"]').click();
        var modal = document.querySelector(".modal");
        ok(modal, "打开编辑抽屉");
        var name = modal.querySelectorAll("input.input")[0];
        name.value = "A2.xml";
        name.dispatchEvent(new Event("input"));
        ok(inst.isDirty(), "改动后视图报告脏状态");
        modalButton(modal, "取消").click();
        return tick().then(function () {
          var confirm = lastModal();
          ok(confirm && confirm.textContent.indexOf("放弃") >= 0, "关闭前提示未保存草稿");
          modalButton(confirm, "保留").click();
          return tick().then(function () {
            ok(document.querySelector(".modal") === modal, "取消放弃后抽屉仍打开");
            modalButton(modal, "取消").click();
            return tick().then(function () {
              modalButton(lastModal(), "放弃修改").click();
              return tick();
            });
          });
        });
      })
      .then(function () {
        ok(!document.querySelector(".modal"), "确认放弃后抽屉关闭");
        ok(!inst.isDirty(), "关闭后脏状态复位");
        failList = true;
        host.querySelector("[data-sync-refresh]").click();
        return tick().then(tick);
      })
      .then(function () {
        var retry = host.querySelector("[data-sync-retry]");
        ok(retry, "列表失败给出显式重试");
        ok(host.querySelector("[data-sync-new]"), "失败时工具栏保留");
        ok(!host.querySelector(".sync-empty"), "读取失败不能显示空数据提示");
        ok(host.querySelector("[data-sync-refresh]"), "读取失败仍可使用筛选工具栏");
        failList = false;
        retry.click();
        return tick().then(tick);
      })
      .then(function () {
        ok(host.textContent.indexOf("A.xml") >= 0, "重试后列表恢复");
        ok(!host.querySelector("[data-sync-error]"), "成功后清除错误区");
        inst.destroy();
        inst.el.remove();
        restore();
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  test("SYNC-37：SDA 目录选择回填资源 ID/路径，服务端不一致拒绝保存", function () {
    var calls = [];
    var original = MC.api.requestV3;
    var originalScope = MC.workspaces && MC.workspaces.scopeId;
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-catalog"; };
    var source = {
      source_id: "src-cat-1", revision: 2, name: "表 A", kind: "sda_table",
      connection_id: "conn-1", resource_ref: "table:old", display_path: "/tables/old",
      record_source: "", keyword: "", enabled: true, schedule_mode: "inherit",
      updated_at: "2026-11-02T00:00:00Z",
    };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && path.indexOf("/catalog") >= 0) {
        return Promise.resolve({
          items: [{ name: "表 A", kind: "table", resource_ref: "table:1", display_path: "/tables/a" }],
        });
      }
      if (method === "GET") return Promise.resolve({ items: [source], total: 1, next_cursor: null });
      if (method === "PATCH") {
        return Promise.reject({
          status: 400,
          code: "INVALID_INPUT",
          message: "SDA catalog 资源 ID 与展示路径不一致或不在当前目录",
        });
      }
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalScope) MC.workspaces.scopeId = originalScope;
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "sources", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        host.querySelector("[data-sync-edit]").click();
        var modal = document.querySelector(".modal");
        ok(modal, "打开编辑抽屉");
        var picker = modal.querySelector("[data-sync-catalog]");
        ok(picker, "SDA 来源有目录选择入口");
        picker.click();
        return tick().then(function () {
          ok(calls.some(function (c) {
            return c.method === "GET" && c.path.indexOf("/workspaces/w-catalog/connections/conn-1/catalog") >= 0;
          }), "按来源连接读取受控目录");
          var select = modal.querySelector("[data-sync-catalog-select]");
          ok(select && select.style.display !== "none", "目录加载后可选择");
          eq(select.options.length, 2);
          select.value = "0";
          select.dispatchEvent(new Event("change"));
          var inputs = modal.querySelectorAll("input.input");
          eq(inputs[1].value, "table:1", "回填资源 ID");
          eq(inputs[2].value, "/tables/a", "回填展示路径");
          modal.querySelector("[data-sync-save]").click();
          return tick().then(function () {
            var patch = calls.filter(function (c) { return c.method === "PATCH"; })[0];
            eq(patch.opts.body.resource_ref, "table:1");
            eq(patch.opts.body.display_path, "/tables/a");
            eq(patch.opts.headers["If-Match"], "2");
            ok(modal.textContent.indexOf("不一致") >= 0, "服务端不一致拒绝保存并提示");
            ok(document.querySelector(".modal"), "拒绝时不关闭抽屉");
            cleanOverlays();
            inst.destroy();
            inst.el.remove();
            restore();
          });
        });
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  test("R6：计划保存回包不能覆盖切换后的工作区", function () {
    var original = MC.api.requestV3, originalScope = MC.workspaces.scopeId, originalEpoch = MC.workspaces.epoch;
    var wid = "w-a", epoch = 1, resolveSave;
    MC.workspaces.scopeId = function () { return wid; };
    MC.workspaces.epoch = function () { return epoch; };
    MC.api.requestV3 = function (method, path) {
      if (method === "GET" && /\/sync\/policy$/.test(path)) return Promise.resolve({ revision: 1, enabled: false, default_interval_hours: path.indexOf("/w-a/") >= 0 ? 6 : 18, profile_overrides: {} });
      if (method === "PUT" && /\/sync\/policy$/.test(path)) return new Promise(function (resolve) { resolveSave = resolve; });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function (name) { return name === "settings_write"; };
    var inst = MC.views.updates(app, { arg: "plan" });
    function cleanup() { inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; MC.workspaces.scopeId = originalScope; MC.workspaces.epoch = originalEpoch; }
    return tick().then(function () {
      var input = inst.el.querySelector("[data-plan-interval]"); input.value = "9"; input.dispatchEvent(new Event("input"));
      inst.el.querySelector("[data-plan-save]").click();
      wid = "w-b"; epoch++; inst.update({ arg: "plan" });
      return tick();
    }).then(function () {
      resolveSave({ revision: 2, enabled: false, default_interval_hours: 9, profile_overrides: {} });
      return tick();
    }).then(function () {
      eq(inst.el.querySelector("[data-plan-interval]").value, "18", "旧工作区保存回包不能覆盖新计划");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：工作区切换隔离计划读取与预览的晚到响应", function () {
    var original = MC.api.requestV3, originalScope = MC.workspaces.scopeId, originalEpoch = MC.workspaces.epoch;
    var wid = "w-a", epoch = 1, reads = 0, resolvePolicy, resolvePreview;
    MC.workspaces.scopeId = function () { return wid; };
    MC.workspaces.epoch = function () { return epoch; };
    MC.api.requestV3 = function (method, path) {
      if (method === "GET" && /\/sync\/policy$/.test(path)) {
        if (path.indexOf("/w-a/") >= 0 && ++reads > 1) return new Promise(function (resolve) { resolvePolicy = resolve; });
        return Promise.resolve({ revision: 1, enabled: false, default_interval_hours: path.indexOf("/w-a/") >= 0 ? 6 : 18, profile_overrides: {} });
      }
      if (method === "POST" && /\/sync\/previews$/.test(path)) return new Promise(function (resolve) { resolvePreview = resolve; });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function (name) { return name === "settings_write"; };
    var inst = MC.views.updates(app, { arg: "plan" });
    function cleanup() { inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; MC.workspaces.scopeId = originalScope; MC.workspaces.epoch = originalEpoch; }
    return tick().then(function () {
      inst.el.querySelector("[data-plan-preview]").click();
      inst.el.querySelector("[data-plan-refresh]").click();
      wid = "w-b"; epoch++; inst.update({ arg: "plan" });
      return tick();
    }).then(function () {
      eq(inst.el.querySelector("[data-plan-interval]").value, "18", "新工作区计划加载");
      resolvePolicy({ revision: 9, enabled: false, default_interval_hours: 99, profile_overrides: {} });
      resolvePreview({ preview_id: "old-preview", total: 1, read_set_sha256: "old-workspace", items: [] });
      return tick();
    }).then(function () {
      eq(inst.el.querySelector("[data-plan-interval]").value, "18", "旧计划回包不得覆盖");
      ok(!inst.el.querySelector('[data-plan-read-set="old-workspace"]'), "旧预览不得进入新工作区");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("SYNC-33：计划 CAS 409 保留草稿、workspace_all 预览与暂停后手动全量更新", function () {
    var calls = [];
    var previewBodies = [];
    var runBodies = [];
    var original = MC.api.requestV3;
    var originalConfirm = MC.confirm;
    var originalScope = MC.workspaces.scopeId;
    var originalCurrent = MC.workspaces.current;
    var policy = {
      revision: 4, enabled: false, default_interval_hours: 6, anchor: null,
      profile_overrides: { second: { enabled: true, interval_hours: 8 } },
    };
    var sourceRows = [
      { source_id: "src-33-1", profile_id: "default", name: "A.xml", enabled: true, schedule_mode: "manual" },
      { source_id: "src-33-2", profile_id: "second", name: "B.xml", enabled: true, schedule_mode: "inherit" },
    ];
    MC.workspaces.scopeId = function () { return "w-plan"; };
    MC.workspaces.current = function () {
      return {
        id: "w-plan",
        profiles: [{ id: "default", name: "默认机型" }, { id: "second", name: "第二机型" }],
      };
    };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && /\/sync\/policy$/.test(path)) {
        return Promise.resolve(JSON.parse(JSON.stringify(policy)));
      }
      if (method === "GET" && /\/sync\/sources$/.test(path)) {
        return Promise.resolve({ items: sourceRows.slice(), total: 2, next_cursor: null });
      }
      if (method === "PUT" && /\/sync\/policy$/.test(path)) {
        return Promise.reject({ status: 409, code: "REVISION_CONFLICT", message: "changed" });
      }
      if (method === "POST" && /\/sync\/previews$/.test(path)) {
        previewBodies.push(opts.body);
        return Promise.resolve({
          preview_id: "prev-33", total: 2, excluded: [],
          read_set_sha256: "abcdef0123456789",
          items: sourceRows.slice(),
        });
      }
      if (method === "POST" && /\/sync\/runs$/.test(path)) {
        runBodies.push(opts.body);
        return Promise.resolve({
          preview: { preview_id: "prev-33", total: 2, excluded: [], read_set_sha256: "abcdef0123456789" },
          runs: [
            { profile_id: "default", run: { run_id: "run-33-a", state: "queued", trigger: "manual" }, task: { id: "task-33-a", type: "sync_run", state: "queued", result: { items: [] } } },
            { profile_id: "second", run: { run_id: "run-33-b", state: "queued", trigger: "manual" }, task: { id: "task-33-b", type: "sync_run", state: "queued", result: { items: [] } } },
          ],
        });
      }
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      MC.confirm = originalConfirm;
      MC.workspaces.scopeId = originalScope;
      MC.workspaces.current = originalCurrent;
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "tasks_run"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "plan", query: {} });
    document.body.appendChild(inst.el);
    return tick()
      .then(function () {
        eq(inst.el.querySelector("[data-plan-enabled]").checked, false, "加载全局暂停状态");
        eq(inst.el.querySelector("[data-plan-interval]").value, "6", "加载默认间隔");
        eq(inst.el.querySelector("[data-plan-interval]").getAttribute("aria-label"), "默认间隔（小时）", "默认间隔有可访问名称");
        eq(inst.el.querySelector('[data-plan-override-enabled="second"]').getAttribute("aria-label"), "第二机型 自动状态覆盖", "机型覆盖有可访问名称");
        var invalidInterval = inst.el.querySelector("[data-plan-interval]");
        invalidInterval.value = "0";
        invalidInterval.dispatchEvent(new Event("input"));
        inst.el.querySelector("[data-plan-save]").click();
        invalidInterval = inst.el.querySelector("[data-plan-interval]");
        eq(invalidInterval.getAttribute("aria-invalid"), "true", "无效间隔标记错误");
        ok(document.getElementById(invalidInterval.getAttribute("aria-describedby")).textContent.indexOf("1–2160") >= 0, "错误说明与间隔关联");
        eq(document.activeElement, invalidInterval, "验证失败焦点返回无效输入");
        invalidInterval.value = "6";
        invalidInterval.dispatchEvent(new Event("input"));
        ok(!invalidInterval.hasAttribute("aria-invalid"), "修改后清除旧错误关联");
        var invalidOverride = inst.el.querySelector('[data-plan-override-interval="second"]');
        invalidOverride.value = "0";
        invalidOverride.dispatchEvent(new Event("input"));
        inst.el.querySelector("[data-plan-save]").click();
        invalidOverride = inst.el.querySelector('[data-plan-override-interval="second"]');
        eq(invalidOverride.getAttribute("aria-invalid"), "true", "机型覆盖错误定位到对应输入");
        eq(document.activeElement, invalidOverride, "机型覆盖错误焦点可恢复");
        ok(!inst.el.querySelector("[data-plan-interval]").hasAttribute("aria-invalid"), "机型错误不会标记全局间隔");
        invalidOverride.value = "8";
        invalidOverride.dispatchEvent(new Event("input"));
        eq(inst.el.querySelector('[data-plan-override-enabled="second"]').value, "true", "加载机型覆盖 enabled");
        eq(inst.el.querySelector('[data-plan-override-interval="second"]').value, "8", "加载机型覆盖 interval");
        ok(!inst.isDirty(), "加载后计划无未保存修改");
        var interval = inst.el.querySelector("[data-plan-interval]");
        interval.value = "9";
        interval.dispatchEvent(new Event("input"));
        ok(inst.isDirty(), "输入计划后即标记未保存，切换工作区须确认");
        var reads = calls.filter(function (c) { return c.method === "GET" && /\/sync\/policy$/.test(c.path); }).length;
        MC.confirm = function () { return Promise.resolve(false); };
        inst.el.querySelector("[data-plan-refresh]").click();
        return tick().then(function () {
          eq(calls.filter(function (c) { return c.method === "GET" && /\/sync\/policy$/.test(c.path); }).length, reads, "取消刷新不重读计划");
          eq(inst.el.querySelector("[data-plan-interval]").value, "9", "取消刷新保留草稿");
          MC.confirm = originalConfirm;
          inst.el.querySelector("[data-plan-save]").click();
          return tick();
        });
      })
      .then(function () {
        ok(inst.el.textContent.indexOf("保留") >= 0, "409 提示草稿保留");
        eq(inst.el.querySelector("[data-plan-interval]").value, "9", "409 后草稿不被旧 revision 覆盖");
        var failures = inst.el.querySelector("[data-plan-failures]");
        failures.checked = true;
        failures.dispatchEvent(new Event("change"));
        inst.el.querySelector("[data-plan-preview]").click();
        return tick();
      })
      .then(function () {
        eq(previewBodies.length, 1, "只发一次预览");
        eq(previewBodies[0].scope, "workspace_all", "失败筛选不缩小预览范围");
        ok(
          inst.el.querySelector("[data-plan-read-set]").textContent.indexOf("abcdef") >= 0,
          "显示 read_set 指纹"
        );
        inst.el.querySelector("[data-plan-run]").click();
        return tick();
      })
      .then(function () {
        eq(runBodies.length, 1, "提交一次全量任务");
        eq(runBodies[0].scope, "workspace_all", "暂停自动仍按 workspace_all 手动运行");
        eq(runBodies[0].preview_id, "prev-33", "沿用当前预览的冻结 read_set");
        ok(
          calls.some(function (c) {
            return c.method === "POST" && c.path.indexOf("/workspaces/w-plan/sync/runs") >= 0;
          }),
          "请求路径携带原始工作区"
        );
        ok(inst.el.textContent.indexOf("w-plan") >= 0, "UI 保留任务归属工作区");
        ok(inst.el.textContent.indexOf("run-33-a") >= 0, "显示默认机型 run");
        ok(inst.el.textContent.indexOf("run-33-b") >= 0, "显示第二机型 run");

        var readOnly = settingsApp(false);
        readOnly.can = function () { return false; };
        var ro = MC.views.updates(readOnly, { kind: "updates", arg: "plan", query: {} });
        return tick().then(function () {
          ok(!ro.el.querySelector("[data-plan-save]"), "只读身份无保存入口");
          ok(!ro.el.querySelector("[data-plan-run]"), "只读身份无手动任务入口");
          ok(ro.el.querySelector("[data-plan-readonly]"), "只读身份有说明");
          eq(ro.el.querySelector("[data-plan-interval]").disabled, true, "只读身份输入禁用");
          ro.destroy();
          ro.el.remove();
          inst.destroy();
          inst.el.remove();
          restore();
        });
      })
      .catch(function (err) {
        inst.destroy();
        inst.el.remove();
        restore();
        throw err;
      });
  });


  test("SYNC-34：连接/凭据状态、只读门控、失败不泄密与保活独立", function () {
    var calls = [];
    var original = MC.api.requestV3;
    var originalScope = MC.workspaces.scopeId;
    var originalCurrent = MC.workspaces.current;
    var scope = "w-conn";
    var connection = {
      connection_id: "conn-34", workspace_id: "w-conn", name: "SDA 主连接",
      origin: "https://sda.example.invalid", protocol: "sda_v1",
      policy_revision: 2, enabled: true,
      credential: {
        connection_id: "conn-34", state: "locked", auth_epoch: 2,
        active_generation: null, revision: 3, last_error: "bad_credentials",
        candidate: {
          candidate_id: "cred-00000034", generation: 1, auth_epoch: 2,
          state: "failed", reason_code: "bad_credentials",
        },
      },
    };
    var heartbeat = {
      connection_id: "conn-34", enabled: false, interval_minutes: 10,
      last_heartbeat_at: null, last_renewing_activity: null, next_due_at: null,
      heartbeat_validated: false, validated_at: null, evidence: {},
      last_result: null, reason_code: null, revision: 0,
    };
    var runOp = {
      op_id: "op-00000034", workspace_id: "w-conn", connection_id: "conn-34",
      kind: "heartbeat_run", state: "blocked", reason_code: "disabled",
      result: { heartbeat: heartbeat },
    };
    MC.workspaces.scopeId = function () { return scope; };
    MC.workspaces.current = function () {
      return { id: scope, profiles: [{ id: "default", name: "默认机型" }] };
    };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && /\/connections$/.test(path)) {
        return Promise.resolve({ items: [JSON.parse(JSON.stringify(connection))] });
      }
      if (method === "GET" && /\/heartbeat$/.test(path)) {
        return Promise.resolve(JSON.parse(JSON.stringify(heartbeat)));
      }
      if (method === "PUT" && /\/heartbeat$/.test(path)) {
        return Promise.resolve(Object.assign({}, heartbeat, opts.body, { revision: 1 }));
      }
      if (method === "POST" && /\/heartbeat\/run$/.test(path)) {
        return Promise.resolve({ operation: JSON.parse(JSON.stringify(runOp)) });
      }
      if (method === "POST" && /\/credentials\/candidates$/.test(path)) {
        return Promise.reject({ status: 400, code: "INVALID_INPUT", message: "password=super-secret-pw" });
      }
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      MC.workspaces.scopeId = originalScope;
      MC.workspaces.current = originalCurrent;
    }
    var app = settingsApp(false);
    app.can = function (name) { return name === "integration_credentials_manage"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "connections", query: {} });
    document.body.appendChild(inst.el);
    var pendingModal = null;
    return tick()
      .then(function () {
        ok(inst.el.textContent.indexOf("SDA 主连接") >= 0, "列表显示连接名称");
        ok(inst.el.textContent.indexOf("https://sda.example.invalid") >= 0, "显示 origin");
        eq(inst.el.querySelector('[data-cred-state="conn-34"]').textContent, "已锁定");
        eq(inst.el.querySelector('[data-cred-epoch="conn-34"]').textContent, "2");
        ok(inst.el.querySelector('[data-cred-error="conn-34"]').textContent.indexOf("bad_credentials") >= 0, "显示最近错误");
        ok(inst.el.querySelector('[data-hb-status="conn-34"]').textContent.indexOf("已停用") >= 0, "保活状态来自服务端");
        ok(inst.el.querySelector('[data-auto-relogin="conn-34"]'), "自动重登只读展示");
        ok(inst.el.querySelector('[data-cred-manual="conn-34"]'), "锁定后提示人工处理");
        ok(inst.el.querySelector('[data-hb-enabled="conn-34"]'), "可写身份有保活开关");
        inst.el.querySelector('[data-conn-cred="conn-34"]').click();
        pendingModal = document.querySelector("[data-cred-password]").closest(".modal");
        ok(pendingModal, "打开更换凭据表单");
        var user = pendingModal.querySelector("[data-cred-username]");
        var pwd = pendingModal.querySelector("[data-cred-password]");
        ok(!pendingModal.querySelector("[data-cred-token]"), "管理员无需运维令牌");
        pendingModal.querySelector("[data-cred-submit]").click();
        eq(document.activeElement, user, "缺少账号时聚焦账号");
        ok(user.getAttribute("aria-invalid") === "true" && document.getElementById(user.getAttribute("aria-describedby")).textContent.indexOf("账号") >= 0, "账号错误关联说明");
        user.value = "operator-account";
        user.dispatchEvent(new Event("input"));
        pendingModal.querySelector("[data-cred-submit]").click();
        eq(document.activeElement, pwd, "缺少密码时聚焦密码");
        ok(pwd.getAttribute("aria-invalid") === "true", "密码错误关联说明");
        pwd.value = "super-secret-pw";
        pwd.dispatchEvent(new Event("input"));
        pendingModal.querySelector("[data-cred-submit]").click();
        return tick().then(function () {
          eq(pwd.value, "", "失败后密码框清空");
          ok(pendingModal.textContent.indexOf("super-secret-pw") < 0, "失败不回显秘密");
          ok(pendingModal.textContent.indexOf("INVALID_INPUT") >= 0, "显示安全错误码");
          ok(document.querySelector(".modal"), "失败不关闭表单");
          pendingModal.querySelector(".modal-footer .btn").click();

        });
      })
      .then(function () {
        var autoBefore = inst.el.querySelector('[data-auto-relogin="conn-34"]').textContent;
        var enabled = inst.el.querySelector('[data-hb-enabled="conn-34"]');
        var interval = inst.el.querySelector('[data-hb-interval="conn-34"]');
        ok(!inst.el.querySelector("[data-hb-token]"), "保活无需运维令牌");
        interval.value = "0";
        inst.el.querySelector('[data-hb-save="conn-34"]').click();
        eq(document.activeElement, interval, "保活间隔错误定位输入");
        ok(interval.getAttribute("aria-invalid") === "true", "保活间隔错误关联说明");
        enabled.checked = true;
        interval.value = "15";
        interval.dispatchEvent(new Event("input"));
        inst.el.querySelector('[data-hb-save="conn-34"]').click();
        return tick().then(function () {
          var put = calls.filter(function (c) { return c.method === "PUT" && /\/heartbeat$/.test(c.path); })[0];
          ok(put, "保存保活发 PUT");
          eq(put.opts.body, { interval_minutes: 15, enabled: true, auto_relogin: true });
          eq(put.opts.headers["If-Match"], "0", "保活保存带 revision CAS");
          ok(!put.opts.headers["X-Operator-Token"], "请求不带运维令牌");
          eq(inst.el.querySelector('[data-auto-relogin="conn-34"]').textContent, autoBefore,
            "切换保活不改变自动重登展示");
          ok(inst.el.querySelector('[data-hb-status="conn-34"]').textContent.indexOf("已启用") >= 0,
            "保活开关反映服务端返回");
          eq(calls.filter(function (c) { return c.path.indexOf("/credentials/") >= 0; }).length, 1,
            "保存保活不触碰凭据接口（只有前面的候选失败一次）");
          inst.el.querySelector('[data-hb-run="conn-34"]').click();
          return tick();
        });
      })
      .then(function () {
        var run = calls.filter(function (c) { return c.method === "POST" && /\/heartbeat\/run$/.test(c.path); })[0];
        ok(run, "立即保活走 POST /heartbeat/run");
        ok(inst.el.querySelector('[data-sync-operation="conn-34"]'), "显示安全操作状态");
        ok(inst.el.textContent.indexOf("保活未启用") >= 0, "blocked 原因来自服务端，不伪造成功");
        scope = "w-conn-2";
        inst.update({ kind: "updates", arg: "connections", query: {} });
        return tick().then(function () {
          ok(calls.some(function (c) {
            return c.method === "GET" && c.path.indexOf("/workspaces/w-conn-2/connections") >= 0;
          }), "切换工作区后按新 W 重新加载");
          ok(inst.el.isConnected, "切换只重画视图，整页未刷新");
        });
      })
      .then(function () {
        var readOnly = settingsApp(false);
        readOnly.can = function () { return false; };
        var ro = MC.views.updates(readOnly, { kind: "updates", arg: "connections", query: {} });
        document.body.appendChild(ro.el);
        return tick().then(function () {
          ok(!ro.el.querySelector("[data-conn-edit]"), "只读身份无编辑入口");
          ok(!ro.el.querySelector("[data-conn-create]"), "只读身份无新建入口");
          ok(!ro.el.querySelector("[data-conn-cred]"), "只读身份无凭据操作");
          ok(!ro.el.querySelector("[data-hb-enabled]"), "只读身份无保活开关");
          ok(!ro.el.querySelector("[data-hb-save]"), "只读身份无保活保存");
          ok(ro.el.querySelector('[data-cred-state="conn-34"]'), "只读身份仍看凭据状态");
          ok(ro.el.querySelector('[data-hb-status="conn-34"]'), "只读身份仍看保活状态");
          ok(ro.el.textContent.indexOf("只读身份") >= 0, "只读身份有说明");
          ro.destroy();
          ro.el.remove();
          inst.destroy();
          inst.el.remove();
          restore();
        });
      })
      .catch(function (err) {
        try {
          if (pendingModal) {
            var cancel = pendingModal.querySelector(".modal-footer .btn");
            if (cancel) cancel.click();
          }
        } catch (e) { /* 已在关闭流程中 */ }
        inst.destroy();
        inst.el.remove();
        restore();
        throw err;
      });
  });


  test("R6：诊断下载包含后续事件页", function () {
    var original = MC.api.requestV3;
    var originalCreate = URL.createObjectURL;
    var originalRevoke = URL.revokeObjectURL;
    var originalClick = HTMLAnchorElement.prototype.click;
    var downloaded;
    var run = { run_id: "run-export", trigger: "manual", state: "succeeded", counts: {} };
    URL.createObjectURL = function (blob) { downloaded = blob; return "blob:test"; };
    URL.revokeObjectURL = function () {};
    HTMLAnchorElement.prototype.click = function () {};
    MC.api.requestV3 = function (method, path, opts) {
      if (method === "GET" && /\/events$/.test(path)) {
        var after = opts && opts.query ? opts.query.after_seq || 0 : 0;
        return Promise.resolve({ items: after === 0
          ? Array.from({ length: 200 }, function (_, i) { return { seq: i + 1 }; })
          : [{ seq: 201, message: "末尾诊断事件" }] });
      }
      if (method === "GET" && /\/runs\/run-export$/.test(path)) return Promise.resolve({ run: run, items: [] });
      if (method === "GET" && /\/sync\/runs$/.test(path)) return Promise.resolve({ items: [{ run: run }], total: 1 });
      return Promise.resolve({ items: [] });
    };
    var inst = MC.views.updates(settingsApp(true), { arg: "runs" });
    function cleanup() { cleanOverlays(); inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; HTMLAnchorElement.prototype.click = originalClick; }
    return tick().then(function () { inst.el.querySelector("[data-sync-run]").click(); return tick(); })
      .then(function () { modalButton(lastModal(), "下载诊断").click(); return tick().then(tick); })
      .then(function () { ok(downloaded, "实际生成诊断 Blob"); return downloaded.text(); })
      .then(function (raw) {
        var payload = JSON.parse(raw);
        eq(payload.events.length, 201, "下载包括后续页");
        eq(payload.events[200].message, "末尾诊断事件", "最后事件保留");
        cleanup();
      }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：运行详情事件分页失败后保留已有内容并可重试", function () {
    var original = MC.api.requestV3;
    var calls = [];
    var fail = true;
    var run = { run_id: "run-pages", trigger: "manual", state: "succeeded", counts: {} };
    MC.api.requestV3 = function (method, path, opts) {
      if (method === "GET" && /\/events$/.test(path)) {
        var after = opts && opts.query ? opts.query.after_seq || 0 : 0;
        calls.push(after);
        if (after === 0) return Promise.resolve({ items: Array.from({ length: 200 }, function (_, i) { return { seq: i + 1, message: "事件 " + (i + 1) }; }) });
        if (fail) return Promise.reject({ code: "NETWORK", message: "下一页失败" });
        return Promise.resolve({ items: [{ seq: 201, message: "末尾事件 201" }] });
      }
      if (method === "GET" && /\/runs\/run-pages$/.test(path)) return Promise.resolve({ run: run, items: [] });
      if (method === "GET" && /\/sync\/runs$/.test(path)) return Promise.resolve({ items: [{ run: run }], total: 1 });
      return Promise.resolve({ items: [] });
    };
    var inst = MC.views.updates(settingsApp(true), { arg: "runs" });
    function cleanup() { cleanOverlays(); inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; }
    return tick().then(function () {
      inst.el.querySelector("[data-sync-run]").click();
      return tick();
    }).then(function () {
      var modal = lastModal();
      ok(modal.textContent.indexOf("事件 200") >= 0, "首批事件可见");
      modal.querySelector("[data-sync-events-more]").click();
      return tick();
    }).then(function () {
      var modal = lastModal();
      ok(modal.textContent.indexOf("下一页失败") >= 0, "分页错误持续显示");
      ok(modal.textContent.indexOf("事件 200") >= 0, "失败后已加载事件保留");
      fail = false;
      modal.querySelector("[data-sync-events-more]").click();
      return tick();
    }).then(function () {
      var modal = lastModal();
      ok(modal.textContent.indexOf("末尾事件 201") >= 0, "重试后追加后续事件");
      ok(calls.filter(function (x) { return x === 200; }).length === 2, "两次请求都从正确序号继续");
      ok(!modal.querySelector("[data-sync-events-more]"), "最后一批不足 200 条时移除加载入口");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("SYNC-35：运行记录列表、items/events 与失败项重试", function () {
    var calls = [];
    var failDownload = false;
    var original = MC.api.requestV3;
    var originalWid = MC.workspaces && MC.workspaces.scopeId;
    var run = {
      run_id: "run-test-1", trigger: "manual", state: "partial",
      counts: { total: 2, succeeded: 1, failed: 1, blocked: 0 },
      created_at: "2026-11-02T00:00:00Z", finished_at: "2026-11-02T00:01:00Z",
      parent_run_id: null,
    };
    var items = [
      { item_id: "item-1", source_id: "src-1", name: "A.xml", state: "succeeded", stage: "finished", reason_code: "validated", committed: true },
      { item_id: "item-2", source_id: "src-2", name: "B.xml", state: "blocked", stage: "finished", reason_code: "collector_offline", committed: false },
    ];
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-runs"; };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && path.indexOf("/events") >= 0) {
        if (failDownload) return Promise.reject({ code: "NETWORK", message: "日志暂时不可用" });
        return Promise.resolve({ items: [
          { seq: 1, item_id: "item-1", source_id: "src-1", at: "2026-11-02T00:00:10Z", status: "succeeded", message: "A 已提交" },
          { seq: 2, item_id: "item-2", source_id: "src-2", at: "2026-11-02T00:00:11Z", status: "blocked", message: "B 已阻断" },
        ] });
      }
      if (method === "GET" && path.indexOf("/runs/") >= 0) {
        return Promise.resolve({ run: run, items: items });
      }
      if (method === "GET") {
        return Promise.resolve({ items: [{ run: run, items: items }], total: 1, next_cursor: null });
      }
      return Promise.resolve({ parent_run_id: run.run_id, runs: [] });
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalWid) MC.workspaces.scopeId = originalWid;
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "tasks_run" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "runs", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        ok(host.textContent.indexOf("run-test-1") >= 0, "列表显示运行");
        var row = host.querySelector("[data-sync-run]");
        ok(row, "运行行可点开");
        row.click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        ok(modal, "打开运行详情");
        ok(modal.textContent.indexOf("A.xml") >= 0 && modal.textContent.indexOf("B.xml") >= 0, "items 可见");
        ok(modal.textContent.indexOf("collector_offline") >= 0, "失败原因可见");
        ok(modal.textContent.indexOf("A 已提交") >= 0, "事件日志可见");
        modal.querySelector('[data-sync-run-item="item-2"]').click();
        ok(modal.textContent.indexOf("B 已阻断") >= 0, "选中来源显示对应日志");
        ok(modal.textContent.indexOf("A 已提交") < 0, "选中来源不混入其他来源日志");
        modal.querySelector('[data-sync-run-item="all"]').click();
        ok(modal.textContent.indexOf("A 已提交") >= 0, "可返回全部日志");
        failDownload = true;
        modalButton(modal, "下载诊断").click();
        return tick().then(function () {
          ok(modal.querySelector("[data-sync-download-error]").textContent.indexOf("日志暂时不可用") >= 0, "下载失败在详情中持续显示");
          ok(modalButton(modal, "下载诊断"), "失败后保留重试入口");
          failDownload = false;
          var retry = modal.querySelector("[data-sync-retry]");
          ok(retry, "失败项有重试入口");
          retry.click();
          return tick().then(function () {
            ok(calls.some(function (c) { return c.method === "POST" && c.path.indexOf("/retry") >= 0; }), "重试走 POST /retry");
            [].filter.call(modal.querySelectorAll(".modal-footer .btn"), function (b) { return b.textContent === "关闭"; })[0].click();
          });
        });
      })
      .then(function () {
        var ro = settingsApp(false);
        ro.can = function () { return false; };
        MC.api.requestV3 = function (method, path) {
          if (method === "GET" && path.indexOf("/events") >= 0) return Promise.resolve({ items: [] });
          if (method === "GET" && path.indexOf("/runs/") >= 0) return Promise.resolve({ run: run, items: items });
          return Promise.resolve({ items: [{ run: run, items: items }], total: 1, next_cursor: null });
        };
        var view = MC.views.updates(ro, { kind: "updates", arg: "runs", query: {} });
        return tick().then(function () {
          view.el.querySelector("[data-sync-run]").click();
          return tick().then(function () {
            var modal = document.querySelector(".modal");
            ok(!modal.querySelector("[data-sync-retry]"), "只读身份无重试");
            [].filter.call(modal.querySelectorAll(".modal-footer .btn"), function (b) { return b.textContent === "关闭"; })[0].click();
            view.destroy();
            view.el.remove();
            inst.destroy();
            inst.el.remove();
            restore();
          });
        });
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });

  test("R6：候选目标切换使旧预览和晚到响应失效", function () {
    var original = MC.api.requestV3;
    var resolvePreview;
    var resolveApply;
    var requests = 0;
    MC.api.requestV3 = function (method, path) {
      if (method === "GET" && /\/sync\/candidates$/.test(path)) return Promise.resolve({ items: [{ candidate_id: "r6-race", source_id: "s", profile_id: "default", state: "pending", file_version_id: "v" }] });
      if (method === "GET" && /\/releases$/.test(path)) return Promise.resolve({ items: [{ release_id: "r1" }, { release_id: "r2" }] });
      if (method === "GET" && /\/configs$/.test(path)) return Promise.resolve({ items: [{ config_id: "c1", name: "配置" }] });
      if (method === "POST" && /\/apply-preview$/.test(path)) {
        requests++;
        if (requests === 1) return new Promise(function (resolve) { resolvePreview = resolve; });
        return Promise.resolve({ preview_id: "p2", blockers: [], items: [{ parameter_id: "fresh", name: "新目标", action: "conflict", allowed: ["use_new", "inherit_old"] }] });
      }
      if (method === "POST" && /\/apply$/.test(path)) return new Promise(function (resolve) { resolveApply = resolve; });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write"; };
    var inst = MC.views.updates(app, { arg: "candidates" });
    function cleanup() { cleanOverlays(); inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; }
    return tick().then(function () { inst.el.querySelector("[data-sync-candidate]").click(); return tick().then(tick); })
      .then(function () {
        var modal = lastModal(), release = modal.querySelector('[aria-label="软件版本"]');
        modal.querySelector("[data-sync-preview]").click();
        release.value = "r2"; release.dispatchEvent(new Event("change"));
        resolvePreview({ preview_id: "p1", blockers: [], items: [{ parameter_id: "stale", name: "旧目标", action: "from_new" }] });
        return tick();
      }).then(function () {
        var modal = lastModal();
        ok(!modal.querySelector("[data-sync-diff]"), "旧目标晚到预览不显示");
        modal.querySelector("[data-sync-preview]").click();
        return tick();
      }).then(function () {
        var modal = lastModal(), release = modal.querySelector('[aria-label="软件版本"]');
        ok(modal.querySelector('[data-sync-diff="fresh"]'), "新目标预览显示");
        release.value = "r1"; release.dispatchEvent(new Event("change"));
        ok(!modal.querySelector("[data-sync-apply]"), "切换目标后写入入口失效");
        modal.querySelector("[data-sync-preview]").click();
        return tick();
      }).then(function () {
        var modal = lastModal(), decision = modal.querySelector("[data-sync-decision]");
        decision.value = "use_new"; decision.dispatchEvent(new Event("change"));
        modal.querySelector("[data-sync-apply]").click();
        decision.value = "inherit_old"; decision.dispatchEvent(new Event("change"));
        ok(modal.querySelector("[data-sync-apply]").disabled && modal.querySelector("[data-sync-preview]").disabled, "写入中不能再次预览或提交");
        resolveApply({ draft_revision: 1 });
        return tick();
      }).then(function () {
        cleanup();
      }).catch(function (err) { cleanup(); throw err; });
  });

  test("SYNC-36：候选差异预览、冲突决议与写入草稿", function () {
    var calls = [];
    var original = MC.api.requestV3;
    var originalWid = MC.workspaces && MC.workspaces.scopeId;
    var candidate = {
      candidate_id: "cand-36", source_id: "src-36", profile_id: "default",
      state: "pending", content_hash: "a".repeat(64), created_at: "2026-11-02T00:00:00Z",
      file_version_id: "v-36",
    };
    var preview = {
      preview_id: "prev-36", candidate_id: "cand-36", draft_revision: 3,
      d0_frozen: false, blockers: [], allowed_decisions: ["use_new", "inherit_old", "manual"],
      items: [
        { parameter_id: "p1", name: "Pulse", action: "from_new", ours: { value: { type: "int", text: "1" } }, theirs: { value: { type: "int", text: "2" } } },
        { parameter_id: "p2", name: "Enabled", action: "conflict", allowed: ["use_new", "inherit_old", "manual"], base: { value: { type: "null", text: "null" } }, ours: { value: { type: "bool", text: "false" } }, theirs: { value: { type: "string", text: "" } } },
        { parameter_id: "p3", name: "Removed", action: "drop", ours: { value: { type: "int", text: "0" } }, theirs: null },
      ],
    };
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-cand"; };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && path.indexOf("/sync/candidates") >= 0) {
        return Promise.resolve({ items: [candidate], total: 1, next_cursor: null });
      }
      if (method === "GET" && path.indexOf("/configs") >= 0 && path.indexOf("/releases") >= 0) {
        return Promise.resolve({ items: [{ release_id: "rel-36", version: "690" }] });
      }
      if (method === "GET" && path.indexOf("/configs") >= 0) {
        return Promise.resolve({ items: [{ config_id: "cfg-36", code: "MC-1", name: "配置" }] });
      }
      if (method === "POST" && path.indexOf("/apply-preview") >= 0) {
        return Promise.resolve(preview);
      }
      return Promise.resolve({ changed: ["p1", "p2"], draft_revision: 4 });
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalWid) MC.workspaces.scopeId = originalWid;
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "candidates", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        var row = host.querySelector("[data-sync-candidate]");
        ok(row, "候选列表可点开");
        row.click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        ok(modal, "打开候选抽屉");
        ok(modal.textContent.indexOf("无文件版本") < 0, "候选有关联文件版本");
        var previewBtn = modal.querySelector("[data-sync-preview]");
        ok(previewBtn, "有生成预览按钮");
        previewBtn.click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        ok(modal.textContent.indexOf("Pulse") >= 0 && modal.textContent.indexOf("Enabled") >= 0, "显示参数差异");
        var firstCells = modal.querySelector('[data-sync-diff="p1"]').querySelectorAll("td");
        var secondCells = modal.querySelector('[data-sync-diff="p2"]').querySelectorAll("td");
        eq(firstCells[2].textContent, "未设", "缺失基线不冒充 null");
        eq(secondCells[2].textContent, "null", "显式 null 独立显示");
        eq(secondCells[3].textContent, "false", "布尔 false 保留");
        eq(secondCells[4].textContent, '""', "空字符串独立显示");
        ok(modal.querySelector('[data-sync-diff="p3"]').textContent.indexOf("移除") >= 0, "移除动作不能冒充采用新值");
        var decision = modal.querySelector("[data-sync-decision]");
        ok(decision, "冲突参数有决议下拉");
        decision.value = "use_new";
        decision.dispatchEvent(new Event("change"));
        var apply = modal.querySelector("[data-sync-apply]");
        ok(apply && !apply.disabled, "可写身份可应用");
        apply.click();
        return tick().then(function () {
          var call = calls.filter(function (c) { return c.method === "POST" && c.path.indexOf("/apply") >= 0 && c.path.indexOf("preview") < 0; })[0];
          ok(call, "应用走 POST /apply");
          ok(call.opts.body.preview_id === "prev-36", "携带预览 id");
          ok(call.opts.body.decisions.p2 === "use_new", "决议随请求发送");
          ok(document.body.textContent.indexOf("已写入") >= 0, "成功只提示写入草稿");
          modal.querySelector(".modal-footer .btn").click();
        });
      })
      .then(function () {
        var ro = settingsApp(false);
        ro.can = function () { return false; };
        var view = MC.views.updates(ro, { kind: "updates", arg: "candidates", query: {} });
        return tick().then(function () {
          view.el.querySelector("[data-sync-candidate]").click();
          return tick().then(function () {
            var modal = document.querySelector(".modal");
            var previewBtn = modal.querySelector("[data-sync-preview]");
            previewBtn.click();
            return tick().then(function () {
              var apply = modal.querySelector("[data-sync-apply]");
              ok(apply && apply.disabled, "只读身份应用禁用");
              modal.querySelector(".modal-footer .btn").click();
              view.destroy();
              view.el.remove();
              inst.destroy();
              inst.el.remove();
              restore();
            });
          });
        });
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  test("SYNC-36B：未决冲突禁止写入、批量决议一键套用与 409 重新预览", function () {
    var calls = [];
    var original = MC.api.requestV3;
    var originalWid = MC.workspaces && MC.workspaces.scopeId;
    var candidate = {
      candidate_id: "cand-36b", source_id: "src-36b", profile_id: "default",
      state: "pending", content_hash: "b".repeat(64), created_at: "2026-11-02T00:00:00Z",
      file_version_id: "v-36b",
    };
    var allowOld = true;
    function makePreview() {
      return {
        preview_id: "prev-36b", candidate_id: "cand-36b", draft_revision: 5,
        d0_frozen: false, blockers: [], can_apply: false,
        summary: { conflict: 2 },
        items: [
          { parameter_id: "p1", name: "Pulse", action: "conflict", allowed: allowOld ? ["use_new", "inherit_old"] : ["use_new", "pin"], base: { value: { text: "0" } }, ours: { value: { text: "1" } }, theirs: { value: { text: "2" } } },
          { parameter_id: "p2", name: "Enabled", action: "conflict", allowed: ["use_new", "pin"], base: { value: { text: "0" } }, ours: { value: { text: "0" } }, theirs: { value: { text: "1" } } },
        ],
      };
    }
    var applyAttempt = 0;
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-36b"; };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && path.indexOf("/sync/candidates") >= 0) {
        return Promise.resolve({ items: [candidate], total: 1, next_cursor: null });
      }
      if (method === "GET" && path.indexOf("/releases") >= 0) return Promise.resolve({ items: [{ release_id: "rel-36b", version: "1" }] });
      if (method === "GET" && path.indexOf("/configs") >= 0) return Promise.resolve({ items: [{ config_id: "cfg-36b", code: "C", name: "配置" }] });
      if (method === "POST" && path.indexOf("/apply-preview") >= 0) return Promise.resolve(makePreview());
      if (method === "POST" && path.indexOf("/apply") >= 0) {
        applyAttempt += 1;
        return Promise.reject({ status: 409, code: "REVISION_CONFLICT", message: "draft changed" });
      }
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalWid) MC.workspaces.scopeId = originalWid;
      cleanOverlays();
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "tasks_run" || name === "deployer" || name === "admin"; };
    var inst = MC.views.updates(app, { kind: "updates", arg: "candidates", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        host.querySelector("[data-sync-candidate]").click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        modal.querySelector("[data-sync-preview]").click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        var rows = modal.querySelectorAll("[data-sync-diff]");
        eq(rows.length, 2, "两行冲突都渲染");
        ok(rows[0].textContent.indexOf("0") >= 0, "展示基线列");
        var apply = modal.querySelector("[data-sync-apply]");
        ok(apply.disabled, "有未决冲突时禁止写入");
        ok(modal.textContent.indexOf("待决议") >= 0, "提示待决议数量");
        modal.querySelector("[data-sync-all-new]").click();
        ok(!apply.disabled, "一键全部采用新值后可写入");
        modal.querySelector("[data-sync-clear-decisions]").click();
        ok(apply.disabled, "清空决议后再次禁止写入");
        modal.querySelector("[data-sync-all-old]").click();
        var selects = modal.querySelectorAll("[data-sync-decision]");
        eq(selects[0].value, "inherit_old", "批量保留当前写入下拉");
        eq(selects[1].value, "", "不允许保留的冲突保持未决");
        ok(apply.disabled, "不允许的批量决议不能解除写入禁用");
        selects[1].value = "use_new";
        selects[1].dispatchEvent(new Event("change"));
        ok(!apply.disabled, "逐项补全有效决议后才允许写入");
        apply.click();
        return tick().then(tick);
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        ok(modal.textContent.indexOf("409") >= 0, "409 提示草稿变化");
        var rePreview = modal.querySelector("[data-sync-repreview]");
        ok(rePreview, "409 提供重新预览入口");
        var before = calls.filter(function (c) { return c.method === "POST" && c.path.indexOf("/apply-preview") >= 0; }).length;
        allowOld = false;
        rePreview.click();
        return tick().then(function () {
          var modal2 = document.querySelector(".modal");
          var after = calls.filter(function (c) { return c.method === "POST" && c.path.indexOf("/apply-preview") >= 0; }).length;
          ok(after > before, "重新预览重新请求 apply-preview");
          var selects = modal2.querySelectorAll("[data-sync-decision]");
          eq(selects[0].value, "", "重新预览后移除不再允许的旧决议");
          eq(selects[1].value, "use_new", "重新预览保留仍然有效的决议");
          ok(modal2.querySelector("[data-sync-apply]").disabled, "旧决议失效时禁止写入");
          eq(applyAttempt, 1, "只提交过一次写入");
          inst.destroy();
          inst.el.remove();
          restore();
        });
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });

  test("R6：批量候选切换工作区后不向新工作区继续写入", function () {
    var original = MC.api.requestV3, originalScope = MC.workspaces.scopeId, originalEpoch = MC.workspaces.epoch;
    var wid = "w-a", epoch = 1, resolvePreview, writes = [];
    MC.workspaces.scopeId = function () { return wid; };
    MC.workspaces.epoch = function () { return epoch; };
    MC.api.requestV3 = function (method, path) {
      if (method === "GET" && /\/sync\/candidates$/.test(path)) return Promise.resolve({ items: ["a", "b"].map(function (id) { return { candidate_id: id, source_id: id, profile_id: "default", state: "pending", file_version_id: "v" }; }) });
      if (method === "GET" && /\/releases$/.test(path)) return Promise.resolve({ items: [{ release_id: "rel" }] });
      if (method === "GET" && /\/configs$/.test(path)) return Promise.resolve({ items: [{ config_id: "cfg" }] });
      if (method === "POST") { writes.push(path); return new Promise(function (resolve) { resolvePreview = resolve; }); }
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function (key) { return key === "settings_write"; };
    var inst = MC.views.updates(app, { arg: "candidates" });
    function cleanup() { cleanOverlays(); inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; MC.workspaces.scopeId = originalScope; MC.workspaces.epoch = originalEpoch; }
    return tick().then(function () {
      var boxes = inst.el.querySelectorAll("[data-sync-candidate-select]");
      boxes[0].checked = true; boxes[0].dispatchEvent(new Event("change"));
      boxes = inst.el.querySelectorAll("[data-sync-candidate-select]");
      boxes[1].checked = true; boxes[1].dispatchEvent(new Event("change"));
      inst.el.querySelector("[data-sync-candidate-batch-apply]").click();
      return tick().then(tick);
    }).then(function () {
      var modal = lastModal(), config = modal.querySelector("[data-sync-batch-config]");
      config.value = "cfg"; config.dispatchEvent(new Event("change"));
      modal.querySelector("[data-sync-batch-release]").value = "rel";
      modal.querySelector("[data-sync-batch-apply]").click();
      return tick();
    }).then(function () {
      eq(writes.length, 1, "只启动第一个预览");
      wid = "w-b"; epoch++;
      resolvePreview({ preview_id: "preview-a", can_apply: true, items: [] });
      return tick().then(tick);
    }).then(function () {
      eq(writes.length, 1, "切换后不继续预览或写入");
      ok(writes[0].indexOf("/w-a/") >= 0, "已发请求归属原工作区");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("SYNC-36C：候选筛选/多选批量写入与跳转配置管理", function () {
    var calls = [];
    var goArgs = [];
    var original = MC.api.requestV3;
    var originalWid = MC.workspaces && MC.workspaces.scopeId;
    var rows = [
      { candidate_id: "cand-a", source_id: "s-a", profile_id: "default", state: "pending", content_hash: "a".repeat(64), created_at: "2026-11-02T00:00:00Z", file_version_id: "v-a" },
      { candidate_id: "cand-b", source_id: "s-b", profile_id: "default", state: "pending", content_hash: "b".repeat(64), created_at: "2026-11-01T00:00:00Z", file_version_id: "v-b" },
    ];
    if (MC.workspaces) MC.workspaces.scopeId = function () { return "w-36c"; };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (method === "GET" && path.indexOf("/sync/candidates") >= 0) {
        return Promise.resolve({ items: rows.slice(), total: 2, next_cursor: null });
      }
      if (method === "GET" && path.indexOf("/releases") >= 0) return Promise.resolve({ items: [{ release_id: "rel-c", version: "9" }] });
      if (method === "GET" && path.indexOf("/configs") >= 0) return Promise.resolve({ items: [{ config_id: "cfg-c", code: "C", name: "配置" }] });
      if (method === "POST" && path.indexOf("/apply-preview") >= 0) {
        var cid = path.split("/")[path.split("/").length - 2];
        return Promise.resolve({ preview_id: "prev-" + cid, candidate_id: cid, draft_revision: 1, d0_frozen: false, blockers: [], can_apply: true, needs_decision: [], items: [] });
      }
      if (method === "POST" && path.indexOf("/apply") >= 0) return Promise.resolve({ draft_revision: 2, changed: [] });
      return Promise.resolve({});
    };
    function restore() {
      MC.api.requestV3 = original;
      if (MC.workspaces && originalWid) MC.workspaces.scopeId = originalWid;
      cleanOverlays();
    }
    var app = settingsApp(true);
    app.can = function (name) { return name === "settings_write" || name === "tasks_run" || name === "deployer" || name === "admin"; };
    app.go = function (route) { goArgs.push(route); };
    var inst = MC.views.updates(app, { kind: "updates", arg: "candidates", query: {} });
    var host = inst.el;
    return tick()
      .then(function () {
        ok(host.querySelector("[data-sync-candidate-state]"), "有状态筛选");
        ok(host.querySelector("[data-sync-candidate-profile]"), "有机型筛选");
        eq(host.querySelectorAll("article[data-sync-candidate]").length, 2, "候选以独立卡片呈现");
        ok(host.querySelector('[data-sync-candidate="cand-a"] button'), "候选详情可通过键盘按钮打开");
        var checks = host.querySelectorAll("[data-sync-candidate-select]");
        eq(checks.length, 2, "每行有勾选框");
        checks[0].checked = true;
        checks[0].dispatchEvent(new Event("change"));
        checks[1].checked = true;
        checks[1].dispatchEvent(new Event("change"));
        return tick();
      })
      .then(function () {
        var batch = host.querySelector("[data-sync-candidate-batch-apply]");
        ok(batch && !batch.disabled, "选中后可批量写入");
        batch.click();
        return tick().then(tick);
      })
      .then(function () {
        var modal = lastModal();
        ok(modal && modal.textContent.indexOf("批量写入草稿") >= 0, "批量写入抽屉");
        var config = modal.querySelector("[data-sync-batch-config]");
        config.value = "cfg-c";
        config.dispatchEvent(new Event("change"));
        var release = modal.querySelector("[data-sync-batch-release]");
        release.value = "rel-c";
        release.dispatchEvent(new Event("change"));
        modal.querySelector("[data-sync-batch-apply]").click();
        return tick().then(tick).then(tick);
      })
      .then(function () {
        var previews = calls.filter(function (c) { return c.method === "POST" && c.path.indexOf("/apply-preview") >= 0; });
        var applies = calls.filter(function (c) { return c.method === "POST" && c.path.indexOf("/apply") >= 0 && c.path.indexOf("preview") < 0; });
        eq(previews.length, 2, "批量逐候选预览");
        eq(applies.length, 2, "批量逐候选写入");
        ok(applies[0].opts.body.request_id.indexOf("batch-") === 0, "批量使用幂等 request_id");
        host.querySelector('[data-sync-candidate="cand-a"]').click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        modal.querySelector("[data-sync-preview]").click();
        return tick();
      })
      .then(function () {
        var modal = document.querySelector(".modal");
        modal.querySelector("[data-sync-apply]").click();
        return tick().then(function () {
          var goto = document.querySelector("[data-sync-goto-v3]");
          ok(goto, "写入成功提供去配置管理入口");
          goto.click();
          eq(goArgs.length, 1, "跳转走 app.go，不整页刷新");
          eq(goArgs[0].kind, "configv3");
          eq(goArgs[0].query.config, "cfg-c");
          eq(goArgs[0].query.release, "rel-c");
          inst.destroy();
          inst.el.remove();
          restore();
        });
      })
      .catch(function (err) {
        restore();
        throw err;
      });
  });


  test("SYNC-36：空候选列表只请求一次，不自我刷新循环", function () {
    var count = 0;
    var original = MC.api.requestV3;
    MC.api.requestV3 = function (method, path) {
      if (method === "GET" && path.indexOf("/sync/candidates") >= 0) count++;
      return Promise.resolve({ items: [], total: 0, next_cursor: null });
    };
    var app = settingsApp(true);
    var inst = MC.views.updates(app, { kind: "updates", arg: "candidates", query: {} });
    return tick()
      .then(function () {
        return new Promise(function (resolve) { setTimeout(resolve, 50); });
      })
      .then(function () {
        eq(count, 1, "空候选只发一次 GET，不得在 renderBody 里递归重载");
        ok(inst.el.textContent.indexOf("没有待处理的更新候选") >= 0, "显示空态");
        inst.destroy();
        inst.el.remove();
        MC.api.requestV3 = original;
      })
      .catch(function (err) {
        MC.api.requestV3 = original;
        throw err;
      });
  });


  test("R6：更新源机型范围使用服务端筛选并清除旧选择", function () {
    var original = MC.api.requestV3;
    var calls = [];
    MC.api.requestV3 = function (method, path, opts) {
      if (method === "GET" && /\/sync\/sources$/.test(path)) {
        calls.push(opts.query || {});
        return Promise.resolve({ items: [{ source_id: "scope-src", name: "来源", kind: "http_file", profile_id: "model-a", enabled: true }], total: 1 });
      }
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true);
    app.pid = "model-a";
    var inst = MC.views.updates(app, { arg: "sources", pid: "model-a" });
    function cleanup() { inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; }
    return tick().then(function () {
      eq(calls[0].profile_id, "model-a", "默认按当前机型向服务端查询");
      var checkbox = inst.el.querySelector("[data-sync-select]");
      checkbox.checked = true;
      checkbox.dispatchEvent(new Event("change"));
      var scope = inst.el.querySelector("[data-sync-scope]");
      ok(scope, "存在机型范围筛选");
      scope.value = "workspace";
      scope.dispatchEvent(new Event("change"));
      return tick();
    }).then(function () {
      eq(calls[calls.length - 1].profile_id, undefined, "全部机型不向服务端传机型过滤");
      ok(!inst.el.querySelector("[data-sync-select]").checked, "切换范围清除原选择");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：来源筛选切换后旧响应不能覆盖当前结果", function () {
    var original = MC.api.requestV3;
    var resolveOld;
    var count = 0;
    MC.api.requestV3 = function (method, path, opts) {
      if (method === "GET" && /\/sync\/sources$/.test(path) && !(opts.query && opts.query.status === "all")) {
        count += 1;
        if (count === 2) return new Promise(function (resolve) { resolveOld = resolve; });
        return Promise.resolve({ items: [{ source_id: "current", name: "当前机型来源", kind: "http_file", enabled: true }], total: 1 });
      }
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true);
    app.pid = "model-a";
    var inst = MC.views.updates(app, { arg: "sources" });
    function cleanup() { inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; }
    return tick().then(function () {
      var scope = inst.el.querySelector("[data-sync-scope]");
      scope.value = "workspace"; scope.dispatchEvent(new Event("change"));
      scope.value = "profile"; scope.dispatchEvent(new Event("change"));
      return tick();
    }).then(function () {
      resolveOld({ items: [{ source_id: "old", name: "旧范围来源", kind: "http_file", enabled: true }], total: 1 });
      return tick();
    }).then(function () {
      ok(inst.el.textContent.indexOf("当前机型来源") >= 0, "当前范围结果仍可见");
      ok(inst.el.textContent.indexOf("旧范围来源") < 0, "晚到的旧范围结果被丢弃");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：候选跨工作区切换忽略旧请求", function () {
    var original = MC.api.requestV3;
    var originalScope = MC.workspaces.scopeId;
    var wid = "w-a", resolveOld;
    MC.workspaces.scopeId = function () { return wid; };
    MC.api.requestV3 = function (method, path) {
      if (/\/sync\/candidates$/.test(path)) {
        if (path.indexOf("/w-a/") >= 0) return new Promise(function (resolve) { resolveOld = resolve; });
        return Promise.resolve({ items: [{ candidate_id: "new-workspace", state: "pending", profile_id: "default" }], total: 1 });
      }
      return Promise.resolve({ items: [] });
    };
    var inst = MC.views.updates(settingsApp(true), { arg: "candidates" });
    function cleanup() { inst.destroy(); inst.el.remove(); MC.api.requestV3 = original; MC.workspaces.scopeId = originalScope; }
    wid = "w-b";
    inst.update({ arg: "candidates" });
    return tick().then(function () {
      resolveOld({ items: [{ candidate_id: "old-workspace", state: "pending", profile_id: "default" }], total: 1 });
      return tick();
    }).then(function () {
      ok(inst.el.querySelector('[data-sync-candidate="new-workspace"]'), "新工作区数据可见");
      ok(!inst.el.querySelector('[data-sync-candidate="old-workspace"]'), "旧工作区结果不得覆盖");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：运行记录与候选快速切换筛选忽略晚到响应", function () {
    var original = MC.api.requestV3;
    var oldRuns, oldCandidates;
    MC.api.requestV3 = function (method, path, opts) {
      var state = opts && opts.query && opts.query.state;
      if (/\/sync\/runs$/.test(path)) {
        if (state === "failed") return new Promise(function (resolve) { oldRuns = resolve; });
        return Promise.resolve({ items: state === "succeeded" ? [{ run: { run_id: "new-run", state: "succeeded", counts: {} } }] : [], total: 1 });
      }
      if (/\/sync\/candidates$/.test(path)) {
        if (state === "pending") return new Promise(function (resolve) { oldCandidates = resolve; });
        return Promise.resolve({ items: state === "applied" ? [{ candidate_id: "new-candidate", state: "applied", profile_id: "default" }] : [], total: 1 });
      }
      return Promise.resolve({ items: [] });
    };
    var runs = MC.views.updates(settingsApp(true), { arg: "runs" });
    var candidates = MC.views.updates(settingsApp(true), { arg: "candidates" });
    function cleanup() { runs.destroy(); candidates.destroy(); runs.el.remove(); candidates.el.remove(); MC.api.requestV3 = original; }
    function choose(root, selector, value) { var s = root.querySelector(selector); ok(s, "加载时保留筛选"); s.value = value; s.dispatchEvent(new Event("change")); }
    return tick().then(function () {
      choose(runs.el, '[aria-label="运行结果"]', "failed");
      choose(runs.el, '[aria-label="运行结果"]', "succeeded");
      choose(candidates.el, "[data-sync-candidate-state]", "pending");
      choose(candidates.el, "[data-sync-candidate-state]", "applied");
      return tick();
    }).then(function () {
      oldRuns({ items: [{ run: { run_id: "old-run", state: "failed", counts: {} } }], total: 1 });
      oldCandidates({ items: [{ candidate_id: "old-candidate", state: "pending", profile_id: "default" }], total: 1 });
      return tick();
    }).then(function () {
      ok(runs.el.querySelector('[data-sync-run="new-run"]') && !runs.el.querySelector('[data-sync-run="old-run"]'), "运行记录保留最新筛选结果");
      ok(candidates.el.querySelector('[data-sync-candidate="new-candidate"]') && !candidates.el.querySelector('[data-sync-candidate="old-candidate"]'), "候选保留最新筛选结果");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：运行记录与候选读取失败时保留筛选和重试，不显示空数据", function () {
    var original = MC.api.requestV3;
    MC.api.requestV3 = function (method, path) {
      if (path.indexOf("/sync/runs") >= 0 || path.indexOf("/sync/candidates") >= 0) {
        return Promise.reject({ code: "NETWORK", message: "读取失败" });
      }
      return Promise.resolve({ items: [] });
    };
    var runs = MC.views.updates(settingsApp(true), { arg: "runs" });
    var candidates = MC.views.updates(settingsApp(true), { arg: "candidates" });
    function cleanup() { runs.destroy(); candidates.destroy(); runs.el.remove(); candidates.el.remove(); MC.api.requestV3 = original; }
    return tick().then(function () {
      ok(runs.el.textContent.indexOf("读取失败") >= 0, "运行记录错误可见");
      ok(runs.el.querySelector("select") && !runs.el.querySelector(".sync-empty"), "运行记录保留筛选并隐藏空态");
      ok(candidates.el.textContent.indexOf("读取失败") >= 0, "候选错误可见");
      ok(candidates.el.querySelector("[data-sync-candidate-refresh]") && !candidates.el.querySelector(".sync-empty"), "候选保留重试并隐藏空态");
      cleanup();
    }).catch(function (err) { cleanup(); throw err; });
  });

  test("R6：更新管理标签键盘导航与面板关联", function () {
    var original = MC.api.requestV3;
    MC.api.requestV3 = function () { return Promise.resolve({ items: [], total: 0 }); };
    var inst = MC.views.updates(settingsApp(true), { arg: "sources" });
    document.body.appendChild(inst.el);
    try {
      var first = inst.el.querySelector('[data-sync-tab="sources"]');
      first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
      var runs = inst.el.querySelector('[data-sync-tab="runs"]');
      eq(runs.getAttribute("aria-selected"), "true");
      eq(runs.getAttribute("tabindex"), "0");
      eq(inst.el.querySelector('[role="tabpanel"]').getAttribute("aria-labelledby"), runs.id);
      runs.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
      eq(inst.el.querySelector('[data-sync-tab="candidates"]').getAttribute("aria-selected"), "true");
    } finally {
      inst.destroy(); inst.el.remove(); MC.api.requestV3 = original;
    }
  });

  test("R6：弹窗焦点循环忽略隐藏控件", function () {
    var input = MC.util.h("input", { "aria-label": "可见输入" });
    var hidden = MC.util.h("button", { hidden: true }, "隐藏操作");
    var modal = MC.modal({ title: "焦点测试", dismissible: false, body: MC.util.h("div", input, hidden) });
    try {
      input.focus();
      var event = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
      document.dispatchEvent(event);
      ok(event.defaultPrevented, "唯一可见焦点项的 Tab 保持在弹窗内");
      eq(document.activeElement, input, "焦点没有移动到隐藏控件");
    } finally { modal.close(); }
  });

  test("R6：二级对话框独占 Escape 与 Tab，关闭后保留设置面板", function () {
    var outer = MC.modal({ title: "连接设置", body: MC.util.h("button", "更换凭据") });
    var inner = MC.modal({ title: "更换凭据", body: MC.util.h("input", { "aria-label": "账号" }) });
    try {
      var input = inner.el.querySelector("input");
      input.focus();
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
      ok(inner.el.contains(document.activeElement), "焦点留在最上层");
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      ok(!inner.el.isConnected, "关闭凭据弹层");
      ok(outer.el.isConnected, "保留连接设置");
    } finally { inner.close(); outer.close(); }
  });

  test("R6：配置管理共享导航保持真实路由与当前页语义", function () {
    var calls = [];
    var nav = MC.configWorkspaceNav({ go: function (route) { calls.push(route); } }, "updates");
    eq(nav.querySelectorAll("button").length, 4);
    eq(nav.querySelector('[aria-current="page"]').textContent, "更新管理");
    nav.querySelectorAll("button")[1].click(); nav.querySelectorAll("button")[3].click();
    eq(calls, [{ kind: "configv3", arg: "files" }, { kind: "configv3", arg: "records" }]);
  });

  test("R6：来源目标筛选发给服务端，批量计划一次提交各自修订", function () {
    var original = MC.api.requestV3, calls = [], inst;
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (/\/sync\/sources$/.test(path)) return Promise.resolve({ items: [{ source_id: "s1", name: "A.xml", revision: 4, enabled: true }], total: 1,
        filters: { configurations: [{ id: "cfg1", label: "配置一" }], releases: [{ id: "rel1", label: "691" }] } });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "sources" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; }
    return tick().then(function () {
      var select = inst.el.querySelector('select[aria-label="全部固定配置"]');
      select.value = "cfg1"; select.dispatchEvent(new Event("change")); return tick();
    }).then(function () {
      var list = calls.filter(function (call) { return /\/sync\/sources$/.test(call.path); });
      eq(list[list.length - 1].opts.query.configuration_id, "cfg1");
      var check = inst.el.querySelector("[data-sync-select]"); check.checked = true; check.dispatchEvent(new Event("change"));
      inst.el.querySelector("[data-sync-batch-plan]").click();
      var modal = lastModal();
      modal.querySelector("[data-sync-batch-plan-save]").click(); return tick();
    }).then(function () {
      var save = calls.filter(function (call) { return /source-policy-batches$/.test(call.path); });
      eq(save.length, 1); eq(save[0].opts.body, { updates: [{ source_id: "s1", expected_revision: 4, schedule_mode: "inherit", interval_hours: null }] });
      done();
    }).catch(function (err) { done(); throw err; });
  });

  test("R6：编辑中测试发送未保存输入且不保存来源", function () {
    var original = MC.api.requestV3, calls = [], inst;
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ method: method, path: path, opts: opts || {} });
      if (/\/sync\/sources$/.test(path)) return Promise.resolve({ items: [], total: 0 });
      if (/file-selector$/.test(path)) return Promise.resolve({ items: [{ file_id: "f1", name: "A.xml" }] });
      if (/source-tests$/.test(path)) return Promise.resolve({ status: "failed", message: "服务器不可达，输入已保留" });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "sources" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; }
    return tick().then(function () { inst.el.querySelector("[data-sync-new]").click(); return tick(); }).then(function () {
      var modal = lastModal(), inputs = modal.querySelectorAll("input.input");
      eq(getComputedStyle(modal.querySelector("[data-sync-connection]").parentElement).display, "none", "HTTP 地址不显示 SDA 连接");
      ok(modal.querySelector(".sync-source-advanced"), "记录和匹配规则保留在高级设置");
      inputs[0].value = "Current.xml"; inputs[0].dispatchEvent(new Event("input"));
      inputs[1].value = "https://example.invalid/unsaved.xml"; inputs[1].dispatchEvent(new Event("input"));
      var target = modal.querySelector("[data-sync-target-file]"); target.value = "f1"; target.dispatchEvent(new Event("change"));
      modal.querySelector("[data-sync-test-input]").click(); return tick();
    }).then(function () {
      var request = calls.filter(function (call) { return /source-tests$/.test(call.path); })[0];
      eq(request.opts.body.resource_ref, "https://example.invalid/unsaved.xml");
      ok(lastModal().textContent.indexOf("服务器不可达") >= 0);
      eq(lastModal().querySelectorAll("input.input")[0].value, "Current.xml");
      eq(calls.filter(function (call) { return call.method === "PATCH" || call.method === "POST" && /\/sync\/sources$/.test(call.path); }).length, 0);
      var kind = lastModal().querySelector("[data-sync-kind]"); kind.value = "sda_file"; kind.dispatchEvent(new Event("change"));
      ok(lastModal().querySelectorAll("input.input")[1].readOnly, "SDA 稳定标识由目录选择填入，不能手工猜测");
      ok(getComputedStyle(lastModal().querySelector("[data-sync-connection]").parentElement).display !== "none", "SDA 显示连接选择");
      done();
    }).catch(function (err) { done(); throw err; });
  });

  test("R6：运行取消失败可重试，成功只宣称请求已提交", function () {
    var original = MC.api.requestV3, inst, writes = 0;
    var run = { run_id: "run-one", state: "queued", counts: { total: 1 }, trigger: "manual" };
    MC.api.requestV3 = function (method, path) {
      if (/\/cancel$/.test(path)) { writes++; return writes === 1 ? Promise.reject({ message: "临时失败" }) : Promise.resolve({ run: run }); }
      if (/\/runs$/.test(path)) return Promise.resolve({ items: [{ run: run }], total: 1 });
      if (/\/runs\/run-one$/.test(path)) return Promise.resolve({ run: run, items: [] });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "runs" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; }
    return tick().then(function () { inst.el.querySelector("[data-sync-run]").click(); return tick(); }).then(function () {
      lastModal().querySelector("[data-sync-run-cancel]").click(); modalButton(lastModal(), "取消运行").click(); return tick();
    }).then(function () {
      ok(lastModal().textContent.indexOf("临时失败") >= 0); ok(!lastModal().querySelector("[data-sync-run-cancel]").disabled);
      lastModal().querySelector("[data-sync-run-cancel]").click(); modalButton(lastModal(), "取消运行").click(); return tick();
    }).then(function () { eq(writes, 2); ok(document.body.textContent.indexOf("取消请求已提交") >= 0); done(); }).catch(function (err) { done(); throw err; });
  });

  test("R6：未知原来源的对象形状可渲染，决议前禁止写入", function () {
    var original = MC.api.requestV3, inst;
    MC.api.requestV3 = function (method, path) {
      if (/\/candidates$/.test(path)) return Promise.resolve({ items: [{ candidate_id: "c1", source_id: "s1", profile_id: "default", state: "pending", file_version_id: "v1" }], total: 1 });
      if (/\/configs$/.test(path)) return Promise.resolve({ items: [{ config_id: "cfg1", name: "配置一" }] });
      if (/\/releases$/.test(path)) return Promise.resolve({ items: [{ release_id: "rel1", version: "691" }] });
      if (/apply-preview$/.test(path)) return Promise.resolve({ preview_id: "prv1", base: {}, base_known: false, needs_decision: ["p1"], can_apply: false,
        items: [{ parameter_id: "p1", name: "参数一", action: "conflict", ours: { value: { type: "int", text: "1" } }, theirs: { value: { type: "int", text: "2" } }, allowed: ["use_new", "inherit_old", "manual"] }], blockers: [] });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "candidates" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; }
    return tick().then(function () { inst.el.querySelector('[data-sync-candidate="c1"] button').click(); return tick().then(tick); }).then(function () {
      lastModal().querySelector("[data-sync-preview]").click(); return tick();
    }).then(function () {
      var modal = lastModal(); ok(modal.querySelector("[data-sync-diff]")); ok(modal.textContent.indexOf("原来源未知") >= 0);
      ok(modal.querySelector("[data-sync-apply]").disabled);
      var select = modal.querySelector("[data-sync-decision]"); select.value = "use_new"; select.dispatchEvent(new Event("change"));
      ok(!modal.querySelector("[data-sync-apply]").disabled); done();
    }).catch(function (err) { done(); throw err; });
  });

  test("R6：配置文件使用唯一文件 API 分页，配置记录使用既有事件 API", function () {
    var original = MC.api.getV3, calls = [], inst;
    MC.api.getV3 = function (path, query) {
      calls.push({ path: path, query: query || {} });
      if (/file-selector$/.test(path)) return Promise.resolve({ items: [{ name: "A.xml", file_id: "f1", version_id: "v1", format: "xml" }], total: 51 });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.configv3(app, { arg: "files" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); MC.api.getV3 = original; cleanOverlays(); }
    return tick().then(function () {
      ok(inst.el.textContent.indexOf("A.xml") >= 0);
      modalButton(inst.el, "下一页").click(); return tick();
    }).then(function () {
      var pages = calls.filter(function (call) { return /file-selector$/.test(call.path); }); eq(pages[pages.length - 1].query.offset, 50);
      inst.update({ arg: "records" }); return tick();
    }).then(function () { ok(calls.some(function (call) { return /\/profiles\/default\/events$/.test(call.path); })); done(); }).catch(function (err) { done(); throw err; });
  });

  test("R6：运行搜索在服务端分页前生效，失败过滤和复制错误可恢复", function () {
    var original = MC.api.requestV3, originalCopy = MC.util.copyText, calls = [], copied = "", copyOk = false, inst;
    var run = { run_id: "run-filter", state: "partial", trigger: "manual", counts: { total: 2, failed: 1, succeeded: 1 } };
    MC.util.copyText = function (value) { copied = value; return Promise.resolve(copyOk); };
    MC.api.requestV3 = function (method, path, opts) {
      calls.push({ path: path, opts: opts || {} });
      if (/\/runs$/.test(path)) return Promise.resolve({ items: [{ run: run }], total: 1 });
      if (/\/runs\/run-filter$/.test(path)) return Promise.resolve({ run: run, items: [{ item_id: "ok", source_id: "s-ok", state: "succeeded", name: "ok.xml" }, { item_id: "bad", source_id: "s-bad", state: "failed", name: "target.xml" }] });
      if (/\/events$/.test(path)) return Promise.resolve({ items: [{ seq: 1, item_id: "ok", message: "successful-log" }, { seq: 2, item_id: "bad", message: "failed-log" }] });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "runs" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; MC.util.copyText = originalCopy; }
    return tick().then(function () {
      var query = inst.el.querySelector('input[aria-label="搜索运行或来源文件"]'); query.value = "target.xml"; query.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" })); return tick();
    }).then(function () {
      var check = inst.el.querySelector(".sync-filters label input"); check.checked = true; check.dispatchEvent(new Event("change")); return tick();
    }).then(function () {
      var lists = calls.filter(function (call) { return /\/runs$/.test(call.path); }); eq(lists[lists.length - 1].opts.query.q, "target.xml"); eq(lists[lists.length - 1].opts.query.failed_only, true);
      inst.el.querySelector("[data-sync-run]").click(); return tick();
    }).then(function () {
      var modal = lastModal(), only = modal.querySelector("[data-sync-run-failures]"); only.checked = true; only.dispatchEvent(new Event("change"));
      eq(getComputedStyle(modal.querySelector('[data-sync-run-item="ok"]')).display, "none");
      ok(modal.querySelector(".sync-log-body").textContent.indexOf("successful-log") < 0);
      modal.querySelector("[data-sync-copy-diagnostics]").click(); return tick().then(tick);
    }).then(function () {
      ok(lastModal().querySelector("[data-sync-download-error]").textContent.indexOf("剪贴板不可用") >= 0);
      copyOk = true; lastModal().querySelector("[data-sync-copy-diagnostics]").click(); return tick().then(tick);
    }).then(function () { eq(JSON.parse(copied).run.run_id, "run-filter"); eq(JSON.parse(copied).events.length, 2); ok(lastModal().querySelector("[data-sync-download-error]").hidden); done(); }).catch(function (err) { done(); throw err; });
  });

  test("R6：运行详情刷新状态与操作，保留失败筛选", function () {
    var original = MC.api.requestV3, inst, phase = "succeeded";
    var queued = { run_id: "run-live-state", state: "queued", trigger: "manual", counts: { total: 1 } };
    MC.api.requestV3 = function (method, path) {
      if (/\/runs$/.test(path)) return Promise.resolve({ items: [{ run: queued }], total: 1 });
      if (/\/runs\/run-live-state$/.test(path)) return Promise.resolve({ run: { run_id: queued.run_id, state: phase, trigger: "manual", counts: { total: 1, succeeded: phase === "succeeded" ? 1 : 0, failed: phase === "failed" ? 1 : 0 } }, items: [{ item_id: "one", source_id: "source-one", name: "One.xml", state: phase }] });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "runs" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; }
    return tick().then(function () { var trigger = inst.el.querySelector("[data-sync-run] button"); trigger.focus(); trigger.click(); return tick(); }).then(function () {
      var modal = lastModal();
      ok(modal.querySelector(".modal-sub").textContent.indexOf("已完成") >= 0, "最新 manifest 覆盖打开时的排队状态");
      eq(getComputedStyle(modal.querySelector("[data-sync-run-cancel]")).display, "none");
      eq(getComputedStyle(modal.querySelector("[data-sync-retry]")).display, "none");
      var filter = modal.querySelector("[data-sync-run-failures]"); filter.checked = true; filter.dispatchEvent(new Event("change"));
      phase = "failed";
      modal.querySelector("[data-sync-detail-refresh]").click(); return tick();
    }).then(function () {
      var modal = lastModal();
      ok(modal.querySelector(".modal-sub").textContent.indexOf("失败 1") >= 0);
      ok(getComputedStyle(modal.querySelector("[data-sync-retry]")).display !== "none");
      ok(modal.querySelector("[data-sync-run-failures]").checked);
      ok(!modal.querySelector("[data-sync-detail-refresh]").disabled);
      modalButton(modal, "关闭").click(); return tick().then(tick);
    }).then(function () {
      eq(document.activeElement, inst.el.querySelector("[data-sync-run] button"), "刷新列表后焦点回到运行入口");
      done();
    }).catch(function (err) { done(); throw err; });
  });

  test("配置列表：点击重试不把事件当 ID，空列表和失效配置不是加载失败", function () {
    var previous = MC.workspaces.scopeId(); MC.workspaces.setActive("default");
    var runner = productionRun();
    return runner.run(function (r) {
      var base = "/api/v3/workspaces/default/profiles/default/configs", failed = true, items = [], detailCalls = [];
      r.useApi({ getV3: function (path) {
        if (path === base) return failed ? Promise.reject({ code: "NETWORK_ERROR", message: "连接失败" }) : Promise.resolve({ items: items });
        detailCalls.push(path);
        if (path === base + "/cfg-present") return Promise.resolve({ config: { config_id: "cfg-present", name: "可用配置", releases: [] } });
        return Promise.resolve({ items: [], nodes: [], edges: [], baselines: [] });
      } });
      var inst = r.mountView(MC.views.configv3(settingsApp(true)));
      return tick().then(function () {
        ok(inst.el.textContent.indexOf("加载失败") >= 0, "真实请求失败仍有错误反馈");
        failed = false;
        Array.prototype.filter.call(inst.el.querySelectorAll("button"), function (b) { return b.textContent.indexOf("重试") >= 0; })[0].click(); return tick();
      }).then(function () {
        eq(detailCalls.length, 0, "空列表重试不请求事件对象对应的配置");
        ok(inst.el.textContent.indexOf("创建第一组固定配置") >= 0);
        ok(inst.el.textContent.indexOf("加载失败") < 0);
        items = [{ config_id: "cfg-present", name: "可用配置", code: "P", release_count: 0 }];
        inst = r.mountView(MC.views.configv3(settingsApp(true), { query: { config: "cfg-deleted" } })); return tick();
      }).then(function () {
        ok(inst.el.textContent.indexOf("该配置已不存在") >= 0);
        ok(inst.el.textContent.indexOf("加载失败") < 0);
        eq(detailCalls.length, 0, "不反复请求已从权威列表移除的 ID");
        Array.prototype.filter.call(inst.el.querySelectorAll("button"), function (b) { return b.textContent === "查看配置列表"; })[0].click(); return tick();
      }).then(function () { ok(inst.el.textContent.indexOf("可用配置") >= 0); eq(detailCalls.length, 0, "目录页不自动打开详情"); });
    }).then(function () { MC.workspaces.setActive(previous); }, function (err) { MC.workspaces.setActive(previous); throw err; });
  });

  test("连接重排：切换与刷新保留各自草稿，保存不跳过原修订冲突", function () {
    var original = MC.api.requestV3, inst, revision = 1, submitted, acceptSave = false;
    MC.api.requestV3 = function (method, path, opts) {
      if (method === "PUT" && /\/heartbeat$/.test(path)) { submitted = opts; return acceptSave ? Promise.resolve(Object.assign({ revision: 10 }, opts.body)) : Promise.reject({ code: "REVISION_CONFLICT", message: "设置已被其他管理员修改" }); }
      if (/\/connections$/.test(path)) return Promise.resolve({ items: ["a", "b"].map(function (id) { return { connection_id: id, name: "连接 " + id, origin: "https://example.invalid", enabled: true, credential: { state: "valid", auth_epoch: 2 } }; }) });
      if (/\/heartbeat$/.test(path)) return Promise.resolve({ revision: revision, enabled: true, interval_minutes: 10, auto_relogin: true });
      return Promise.resolve({ items: [] });
    };
    var app = settingsApp(true); app.can = function () { return true; };
    inst = MC.views.updates(app, { arg: "connections" }); document.body.appendChild(inst.el);
    function done() { inst.destroy(); inst.el.remove(); cleanOverlays(); MC.api.requestV3 = original; }
    return tick().then(function () {
      ok(!inst.el.querySelector(".sync-strip"), "状态不再单占一行");
      ok(inst.el.querySelector(".sync-head [data-sync-connections]"), "连接入口应在标题行");
      ok(!lastModal().querySelector("table"), "连接信息不用窄抽屉表格");
      var input = inst.el.querySelector('[data-hb-interval="a"]'); input.value = "17"; input.dispatchEvent(new Event("input"));
      var picker = inst.el.querySelector('select[aria-label="选择连接"]'); picker.value = "b"; picker.dispatchEvent(new Event("change"));
      eq(inst.el.querySelector('[data-hb-interval="b"]').value, "10");
      picker = inst.el.querySelector('select[aria-label="选择连接"]'); picker.value = "a"; picker.dispatchEvent(new Event("change"));
      eq(inst.el.querySelector('[data-hb-interval="a"]').value, "17");
      revision = 9; inst.el.querySelector("[data-connections-refresh]").click(); return tick();
    }).then(function () {
      eq(inst.el.querySelector('[data-hb-interval="a"]').value, "17");
      ok(lastModal().querySelector('.modal-footer [data-hb-save="a"]'), "保存设置固定在底部");
      inst.el.querySelector('[data-hb-save="a"]').click(); return tick();
    }).then(function () {
      eq(submitted.headers["If-Match"], "1");
      ok(Array.prototype.some.call(lastModal().querySelectorAll(".form-error"), function (node) { return node.textContent && node.style.display !== "none"; }), "409 错误应显示");
      eq(inst.el.querySelector('[data-hb-interval="a"]').value, "17");
      inst.el.querySelector("[data-sync-sheet-close]").click();
      inst.el.querySelector("[data-sync-connections]").click();
      eq(inst.el.querySelector('[data-hb-interval="a"]').value, "17");
      ok(inst.isDirty(), "连接草稿应标记未保存");
      ok(lastModal().textContent.indexOf("当前服务器：保活开启 · 10 分钟") >= 0);
      ok(lastModal().textContent.indexOf("您的设置：保活开启 · 17 分钟") >= 0);
      inst.el.querySelector('[data-hb-confirm-revision="a"]').click();
      acceptSave = true;
      inst.el.querySelector('[data-hb-save="a"]').click(); return tick();
    }).then(function () {
      eq(submitted.headers["If-Match"], "9", "明确核对后才采用最新修订");
      eq(submitted.body.interval_minutes, 17);
      ok(!inst.isDirty(), "成功保存后清除对应连接草稿"); done();
    }).catch(function (err) { done(); throw err; });
  });

  function configPrototypeFixture(r, frozen) {
    var base = "/api/v3/workspaces/default/profiles/default/configs", requests = [], reads = [], previews = 0;
    var param = { parameter_id: "p-one", name: "速度", directory: ["运动"], type: "number", unit: "mm/s" };
    var values = { "p-one": { membership: "included", enabled: true, value_state: "present", value: "0" } };
    var release = { release_id: "rel-one", version: "690", order: 1, definition_revision: 1, default_revision_id: frozen ? "d0-rel-one" : null, d0: { frozen: !!frozen, revision: 7, values: values } };
    r.useApi({ getV3: function (path) {
      reads.push(path);
      if (path === base) return Promise.resolve({ items: [{ config_id: "cfg-one", name: "运行配置", code: "RUN", release_count: 1, parameter_count: 1, machine_count: 1, latest_release: { version: "690", frozen: frozen } }] });
      if (path === base + "/cfg-one") return Promise.resolve({ config: { config_id: "cfg-one", name: "运行配置", code: "RUN", releases: [release] } });
      if (path.indexOf("/parameters") >= 0) return Promise.resolve({ items: [param], total: 1 });
      if (path.indexOf("/parameter-tree") >= 0) return Promise.resolve({ directories: [{ name: "运动", path: "运动", count: 1, has_children: false }], items: [], parameter_total: 0 });
      if (path.indexOf("/branches/main") >= 0) return Promise.resolve({ baselines: [{ baseline_id: "bl-one", label: "B1", release_id: "rel-one", source_revision_id: "d0-rel-one" }] });
      if (path.indexOf("/machines") >= 0) return Promise.resolve({ items: [{ machine_id: "machine-one", machine_no: "MC-001", release_id: "rel-one", head_id: "mr-one", revisions: [{ revision_id: "mr-one", label: "r1", release_id: "rel-one", base_revision_id: "bl-one" }] }] });
      if (path.indexOf("/drafts/d0-") >= 0) return Promise.resolve({ revision: 7, values: values });
      if (path.indexOf("/revisions/") >= 0) return Promise.resolve({ snapshot: { values: { "p-one": { membership: "included", enabled: true, value_state: "present", value: "9" } } } });
      if (path.indexOf("/bindings") >= 0) return Promise.resolve({ binding_set: { bindings: { one: { parameter_id: "p-one", locator: "json:/Speed" } } } });
      if (path.indexOf("/file-selector") >= 0) return Promise.resolve({ items: [{ version_id: "file-version", name: "template.json" }] });
      if (path.indexOf("/overview") >= 0) return Promise.resolve({ columns: [], rows: [], total: 0 });
      return Promise.resolve({ items: [] });
    }, requestV3: function (method, path, options) {
      requests.push({ method: method, path: path, options: options });
      if (method === "PATCH") return Promise.reject({ code: "REVISION_CONFLICT", message: "并发更新", status: 409 });
      if (path.indexOf("/commit") >= 0) return Promise.resolve({ artifact: { artifact_id: "artifact-one", filename: "result.json" } });
      if (path.indexOf("/export-plans") >= 0) { previews++; return Promise.resolve({ plan: { plan_id: "plan-" + previews, preview_token: "token-" + previews, applied: 1, mapping_total: 1, mapping: [{ parameter_id: "p-one", name: "速度", locator: "json:/Speed", value: "0" }], preview: '{"Speed":0}' } }); }
      return Promise.resolve({});
    } });
    return { reads: reads, requests: requests };
  }

  test("参数目录：无子目录不显示展开按钮，目录名称仍可选择", function () {
    var t = productionRun();
    return t.run(function (r) {
      configPrototypeFixture(r, false);
      var app = settingsApp(true); app.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(app, { query: { config: "cfg-one", tab: "workbench" } }));
      function wait(n) { return tick().then(function () { if (!inst.el.querySelector('[aria-label="选择目录 运动"]') && n) return wait(n - 1); }); }
      return wait(20).then(function () {
        ok(inst.el.querySelector('[aria-label="选择目录 运动"]'), "叶目录保留选择按钮");
        ok(!inst.el.querySelector('[aria-label="展开 运动"], [aria-label="折叠 运动"]'), "无子目录不得伪装成可展开节点");
      });
    });
  });

  test("配置原型：目录先于详情，泳道选择真实修订，返回不自动重开", function () {
    var runner = productionRun();
    return runner.run(function (r) {
      var fixture = configPrototypeFixture(r, true), app = settingsApp(true); app.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(app));
      function button(text) { return Array.prototype.filter.call(inst.el.querySelectorAll("button"), function (b) { return b.textContent.trim() === text; })[0]; }
      return tick().then(function () {
        eq(fixture.reads.length, 1, "目录不为每行提前请求全部详情");
        ok(inst.el.querySelector(".cv-catalog-table")); ok(!inst.el.querySelector(".cv-graph-canvas"));
        button("打开配置").click(); return tick().then(tick);
      }).then(function () {
        eq(inst.el.querySelectorAll("[data-config-revision]").length, 3, "D0、B1、机台 r1 泳道节点");
        inst.el.querySelector('[data-config-revision="mr-one"]').click(); return tick().then(tick);
      }).then(function () {
        ok(fixture.reads.some(function (path) { return /\/revisions\/mr-one$/.test(path); }), "读取已选不可变机台快照");
        button("参数工作台").click();
        ok(inst.el.querySelector(".cv-param-data").textContent.indexOf("9") >= 0);
        ok(!inst.el.querySelector('.cv-param-data input[aria-label="速度的配置值"]'), "历史机台值只读");
        inst.el.querySelector(".cv-breadcrumb button").click();
        ok(inst.el.querySelector(".cv-catalog-table")); ok(!inst.el.querySelector(".cv-graph-canvas"));
      });
    });
  });

  test("配置原型：视图切换与 409 保留逐 ID 草稿，取消离开仍可编辑", function () {
    var runner = productionRun();
    return runner.run(function (r) {
      var fixture = configPrototypeFixture(r, false), app = settingsApp(true); app.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(app, { query: { config: "cfg-one", tab: "workbench" } }));
      function button(text) { return Array.prototype.filter.call(inst.el.querySelectorAll("button"), function (b) { return b.textContent.trim() === text; })[0]; }
      return tick().then(tick).then(function () {
        var input = inst.el.querySelector('[aria-label="速度的配置值"]'); eq(input.value, "0"); input.value = "17"; input.dispatchEvent(new Event("input"));
        button("参数树").click(); return tick().then(tick);
      }).then(function () {
        eq(inst.el.querySelector('[aria-label="速度的配置值"]').value, "17", "切视图保留输入");
        button("保存值").click(); return tick().then(tick);
      }).then(function () {
        eq(fixture.requests[0].options.body.expected_revision, 7);
        eq(fixture.requests[0].options.body.items.length, 1);
        eq(fixture.requests[0].options.body.items[0].value, "17");
        eq(inst.el.querySelector('[aria-label="速度的配置值"]').value, "17"); ok(inst.isDirty(), "409 保留修改及离开保护");
        inst.update({ arg: "configs", query: {} });
        modalButton(lastModal(), "继续编辑").click();
        ok(inst.el.querySelector('[aria-label="速度的配置值"]'));
      });
    });
  });

  test("配置原型：更改模板映射立即失效预览，再预览后才使用新令牌生成文件", function () {
    var runner = productionRun();
    return runner.run(function (r) {
      var fixture = configPrototypeFixture(r, true), app = settingsApp(true); app.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(app, { query: { config: "cfg-one", tab: "export" } }));
      function button(text) { return Array.prototype.filter.call(inst.el.querySelectorAll("button"), function (b) { return b.textContent.trim() === text; })[0]; }
      return tick().then(tick).then(function () { button("预览导出").click(); return tick().then(tick); }).then(function () {
        var commit = inst.el.querySelector("[data-export-commit]"); ok(!commit.disabled);
        var mapping = inst.el.querySelector('[aria-label="速度的模板落点"]'); mapping.value = "json:/Other"; mapping.dispatchEvent(new Event("input"));
        ok(commit.disabled, "修改后不能提交旧预览"); commit.click(); eq(fixture.requests.length, 1);
        button("预览导出").click(); return tick().then(tick);
      }).then(function () {
        eq(fixture.requests[1].options.body.mappings["p-one"], "json:/Other");
        inst.el.querySelector("[data-export-commit]").click(); return tick().then(tick);
      }).then(function () {
        eq(fixture.requests[2].options.body.preview_token, "token-2");
        ok(inst.el.querySelector("[data-export-download]"));
      });
    });
  });

  test("配置原型：未保存表单 Esc 确认可取消，主标签方向键切换并返回焦点", function () {
    var runner = productionRun();
    return runner.run(function (r) {
      var fixture = configPrototypeFixture(r, false), app = settingsApp(true); app.can = function () { return true; };
      var inst = r.mountView(MC.views.configv3(app));
      function button(text) { return Array.prototype.filter.call(inst.el.querySelectorAll("button"), function (b) { return b.textContent.trim() === text; })[0]; }
      return tick().then(function () {
        button("新建固定配置").click();
        var form = lastModal(), input = form.querySelector("input"); input.value = "NEW_CONFIG"; input.dispatchEvent(new Event("input", { bubbles: true }));
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        modalButton(lastModal(), "继续编辑").click();
        ok(document.body.contains(form)); eq(input.value, "NEW_CONFIG");
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        modalButton(lastModal(), "放弃修改").click();
        return tick().then(function () { ok(!document.body.contains(form)); eq(fixture.requests.length, 0); button("打开配置").click(); return tick().then(tick); });
      }).then(function () {
        var tab = inst.el.querySelector('.cv-tabs [aria-selected="true"]'); tab.focus(); tab.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
        eq(inst.el.querySelector('.cv-tabs [aria-selected="true"]').textContent.trim(), "参数工作台");
        eq(document.activeElement.textContent.trim(), "参数工作台");
        var panel = inst.el.querySelector('[role="tabpanel"]');
        eq(panel.getAttribute("aria-labelledby"), document.activeElement.id);
      });
    });
  });

  /* 队尾先让出一个**宏任务**边界再汇总：V8 的 unhandledrejection 派发排在微任务链
   * 清空之后——串行队列本身是一条长微任务链，不 flush 的话最后几条用例的迟到拒绝
   * 会排在 report 之后（headless --dump-dom 拿到的还是旧标题）。兜底 handler 自己
   * 也会重画 report，这里是把「汇总之前该到的错误一定先到」变成顺序保证。 */
  queue
    .then(function () {
      return new Promise(function (resolve) {
        setTimeout(resolve, 50);
      });
    })
    .then(report);
})();
