/* 前端纯逻辑测试。新增前端算法时在此追加用例。 */
(function () {
  "use strict";
  var MC = window.MC;
  var results = [];
  function test(name, fn) {
    try {
      fn();
      results.push({ name: name, ok: true });
    } catch (e) {
      results.push({ name: name, ok: false, err: e && e.message ? e.message : String(e) });
    }
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

  test("M2-09：历史、对比与修改记录已在生产放行，未实现的接口不得提前打开", function () {
    var prod = MC.capsFor(false);
    ["favorites", "revision", "remarks", "search", "history", "compare", "records"].forEach(function (k) {
      ok(prod[k], k + " 已接真实后端");
    });
    ["schedule", "review", "dl", "settings", "import", "delete", "tools", "temp"].forEach(function (k) {
      ok(!prod[k], k + " 尚未实现，不能提前放行");
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

  test("外壳：速览与文件页恒可用，其余由 MC.caps 决定", function () {
    var capOfKind = MC.App.prototype.capOfKind;
    MC.caps = MC.capsFor(false);
    ok(capOfKind("overview") && capOfKind("f") && capOfKind("search"), "速览、文件与搜索已接入真实后端");
    ok(!capOfKind("settings") && !capOfKind("t"), "未接入的 kind 不可用");
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
})();
