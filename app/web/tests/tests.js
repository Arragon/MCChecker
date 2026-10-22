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
  var EXPECTED_CASES = 56;  // 49 + 配置管理 7 条（M5-09 / M5-11）
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
    var orig = { get: MC.api.get, post: MC.api.post, put: MC.api.put, patch: MC.api.patch, del: MC.api.del, request: MC.api.request, download: MC.api.download, fetchBlob: MC.api.fetchBlob, upload: MC.api.upload };
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
