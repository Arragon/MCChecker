/*
 * 文件工作区（路由 f / t）：结构 | 历史版本 | 对比 | 修改记录。
 * 一个文件只占一个标签页；子视图与版本通过路由 query 表达，可直接分享链接：
 *   #/p/default/f/scenario_a.xml?view=tree&version=archive:scenario_a_20261008.xml&node=/CONFIG_ROOT[1]/...
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;
  var UNWIRED = MC.unwired;

  function sideToBody(fileName, v) {
    if (!v) return null;
    if (v.indexOf("temp:") === 0) return { temp: v.slice(5) };
    return { file: fileName, version: v };
  }

  function versionLabel(v) {
    if (!v || v === "current") return "当前版本";
    if (v.indexOf("archive:") === 0) return "历史版本 " + v.slice(8);
    if (v.indexOf("record:") === 0) return "修改记录 " + v.slice(7);
    if (v.indexOf("temp:") === 0) return "上传的文件";
    return v;
  }

  /* D1（§6.7 提交建议第 1/4 步）：null 节点的当前值恒为 "null"，提交任何建议都会被
   * 判为「与当前值相同」，把别的节点改成 null 也过不了类型检查 —— null 是只读值。 */
  var NULL_VALUE_HINT = "null 值为只读，不支持提交修改建议";

  function factory(app, route) {
    var isTemp = route.kind === "t";
    var S = {
      name: isTemp ? null : route.arg,
      token: isTemp ? route.arg : null,
      view: "tree",
      version: "current",
      doc: null,
      idx: null,
      tree: null,
      selected: -1,
      favs: [],
      remarks: [],
      archives: null,
      records: null,
      stale: false,
      showAttrs: util.storageGet("mc.showAttrs", true),
      inspector: util.storageGet("mc.inspector", true),
      compare: { base: null, target: null, result: null, filter: "all", q: "", boundOnly: false },
      pendingNode: null,
    };

    var el = h("div.fv");
    var header = h("div.fv-header");
    var body = h("div.fv-body");
    el.append(header, body);

    var inst = {
      el: el,
      title: isTemp ? "临时文件" : S.name,
      icon: isTemp ? "file-temp" : "file",
      update: function (r) { applyRoute(r); },
      destroy: function () {
        unsub.forEach(function (f) { f(); });
        if (S.tree) S.tree.destroy();
      },
      isDirty: function () { return !!(S.draftInput && S.draftInput.value.trim()); },
    };

    var unsub = [
      MC.bus.on("changed:files", function () { checkStale(); }),
      MC.bus.on("changed:remarks", function () { if (!isTemp) loadRemarks().then(function () { if (S.tree) S.tree.refresh(); renderInspector(); }); }),
      MC.bus.on("changed:favorites", function () { if (!isTemp) loadFavs().then(function () { if (S.tree) S.tree.refresh(); renderInspector(); }); }),
      MC.bus.on("task:done", function (t) { if (t.type === "refresh_records" && t.target === S.name && S.view === "records") loadRecords(); }),
    ];

    function nav(q) {
      var query = Object.assign({}, q);
      Object.keys(query).forEach(function (k) { if (query[k] === null || query[k] === undefined || query[k] === "" || (k === "version" && query[k] === "current") || (k === "view" && query[k] === "tree")) delete query[k]; });
      app.go({ kind: isTemp ? "t" : "f", arg: isTemp ? S.token : S.name, query: query });
    }

    function applyRoute(r) {
      var q = r.query || {};
      var view = q.view || "tree";
      if (view !== "tree" && !MC.caps[view]) view = "tree";
      var version = isTemp ? "temp" : q.version || "current";
      var needDoc = !S.doc || S.doc.file.version !== version;
      S.view = view;
      S.version = version;
      if (view === "compare") {
        if (q.base) S.compare.base = q.base;
        if (q.target) S.compare.target = q.target;
      }
      S.pendingNode = q.node || null;
      renderHeader();
      if (view === "tree") {
        if (needDoc) loadDoc();
        else {
          renderTreeView();
          revealPending();
        }
      } else if (view === "history") renderHistory();
      else if (view === "compare") renderCompare();
      else if (view === "records") renderRecords();
    }

    /* ---------------- data ---------------- */
    function docPath() {
      return isTemp ? app.p("/temp/" + api.seg(S.token) + "/document") : app.p("/files/" + api.seg(S.name) + "/document");
    }

    function loadDoc() {
      util.replace(body, h("div", { style: "padding:20px 24px" }, [1, 2, 3, 4, 5, 6, 7, 8].map(function (k) {
        return h("div.skeleton.skeleton-line", { style: { width: 20 + ((k * 37) % 60) + "%", marginLeft: (k % 4) * 18 + "px" } });
      })));
      var q = S.version !== "current" && !isTemp ? { version: S.version } : {};
      var t0 = performance.now();
      return Promise.all([api.get(docPath(), q), isTemp ? null : loadFavs(), isTemp ? null : loadRemarks()]).then(
        function (res) {
          S.doc = res[0];
          if (isTemp) {
            S.name = S.doc.file.name;
            inst.title = S.name + "（临时 · 仅自己可见）";
            app.setTabTitle(inst, inst.title);
          }
          S.idx = MC.doc.buildIndex(S.doc);
          S.loadMs = Math.round(performance.now() - t0);
          S.stale = false;
          S.selected = -1;
          renderHeader();
          renderTreeView();
          revealPending();
        },
        function (err) {
          util.replace(
            body,
            err.code === "PARSE_FAILED"
              ? MC.stateBlock({ kind: "error", title: "无法解析该版本", desc: MC.errorText(err), actions: [h("button.btn", { type: "button", onclick: function () { app.downloadFile(S.name, S.version); } }, icon("download"), "下载原文")] })
              : MC.errorBlock(err, loadDoc)
          );
        }
      );
    }

    function loadFavs() {
      if (!MC.caps.favorites) { S.favs = []; return Promise.resolve(); }
      return api.get(app.p("/favorites")).then(function (res) {
        var g = res.groups.filter(function (x) { return x.file === S.name; })[0];
        S.favs = g ? g.items : [];
      }, function () { S.favs = []; });
    }

    function loadRemarks() {
      if (!MC.caps.remarks) { S.remarks = []; return Promise.resolve(); }
      return api.get(app.p("/remarks"), { file: S.name, status: "pending" }).then(function (res) { S.remarks = res.items; }, function () { S.remarks = []; });
    }

    function checkStale() {
      if (isTemp || !MC.caps.history || !S.doc || S.version !== "current") return;
      var f = app.files.filter(function (x) { return x.name === S.name; })[0];
      api.get(app.p("/files/" + api.seg(S.name) + "/archives")).then(function (res) {
        if (res.current.sha256 !== S.doc.file.sha256 && !S.stale) {
          S.stale = true;
          renderHeader();
        }
        S.archives = res;
      }, function () { if (!f) { S.stale = true; renderHeader(); } });
    }

    /* ---------------- header ---------------- */
    function renderHeader() {
      var f = app.files.filter(function (x) { return x.name === S.name; })[0];
      var d = S.doc && S.doc.file;
      var meta = h("div.fv-meta");
      if (d) {
        meta.append(
          h("span.meta-item", icon("clock"), (S.version === "current" ? "更新于 " : "保存于 ") + util.formatTime(d.updated_at)),
          h("span.meta-item", util.formatBytes(d.size)),
          h("span.meta-item", d.node_count.toLocaleString() + " 个节点"),
          h("span.meta-item.mono", { title: "内容指纹（用于判断版本是否变化）" }, "#" + String(d.sha256).slice(0, 8))
        );
        if (d.expires_at) meta.append(h("span.meta-item", icon("info"), "临时文件，" + d.expires_at + " 过期"));
      }
      var title = h(
        "div.fv-title-row",
        util.fmtTag(S.name || ".xml"),
        h("h1", S.name || "加载中…"),
        isTemp ? h("span.badge.badge-warning", "临时 · 仅自己可见") : null,
        S.version !== "current" && !isTemp ? h("span.badge.badge-primary", versionLabel(S.version)) : null,
        S.version !== "current" && !isTemp ? h("button.link-btn.small", { type: "button", onclick: function () { nav({ view: "tree" }); } }, "回到当前版本") : null,
        h("span.spacer"),
        h("button.btn", { type: "button", onclick: function () { isTemp ? downloadTemp() : app.downloadFile(S.name, S.version); } }, icon("download"), S.version === "current" || isTemp ? "下载" : "下载此版本"),
        moreButton()
      );
      var segOff = function (cap) { return { disabled: !MC.caps[cap], title: MC.caps[cap] ? null : UNWIRED[cap] }; };
      var views = isTemp
        ? [{ value: "tree", label: "结构", icon: "list" }, Object.assign({ value: "compare", label: "对比", icon: "compare" }, segOff("compare"))]
        : [
            { value: "tree", label: "结构", icon: "list" },
            Object.assign({ value: "history", label: "历史版本", icon: "history", count: f ? f.archive_count : null }, segOff("history")),
            Object.assign({ value: "compare", label: "对比", icon: "compare" }, segOff("compare")),
            Object.assign({ value: "records", label: "修改记录", icon: "records", count: f ? f.record_count : null }, segOff("records")),
          ];
      var seg = MC.segmented(views, S.view, function (v) {
        var q = { view: v };
        if (v === "tree" && S.version !== "current") q.version = S.version;
        nav(q);
      }, "文件视图");
      var staleBanner = S.stale
        ? h("div.banner.banner-warning", icon("refresh"), h("div.banner-body", "此文件已被他人或定时任务更新，当前显示的是旧内容。"), h("button.btn.btn-sm", { type: "button", onclick: function () { S.doc = null; loadDoc(); app.syncRevision(); } }, "重新加载"))
        : null;
      util.replace(header, title, meta, h("div.row", seg), staleBanner);
    }

    function moreButton() {
      var b = h("button.btn.btn-ghost.btn-icon", { type: "button", "aria-label": "更多操作", "aria-haspopup": "menu" }, icon("more"));
      b.addEventListener("click", function () {
        var f = app.files.filter(function (x) { return x.name === S.name; })[0];
        var items = [
          { label: "复制文件名", icon: "copy", onClick: function () { util.copyText(S.name).then(function () { MC.toast.success("已复制文件名"); }); } },
          { label: "复制本页链接", icon: "link", onClick: function () { util.copyText(location.href).then(function () { MC.toast.success("已复制链接", "同事打开后会定位到同一文件与版本"); }); } },
        ];
        if (isTemp) {
          items.push("sep", { label: "保存到服务器…", icon: "upload", disabled: !app.can("deployer"), title: app.can("deployer") ? null : "仅部署者可保存", onClick: saveTempToServer });
        } else if (f) {
          items.push("sep", { label: "删除文件…", icon: "trash", danger: true, disabled: !app.can("admin") || !MC.caps.delete, title: !MC.caps.delete ? UNWIRED.delete : app.can("admin") ? null : "仅管理员可删除文件", onClick: function () { app.deleteFile(f); } });
        }
        MC.menu(b, items, { align: "end" });
      });
      return b;
    }

    function downloadTemp() {
      api.download(app.p("/temp/" + api.seg(S.token) + "/raw"), {}, S.name).then(null, function (err) {
        MC.toastError("下载失败", err, { action: { label: "重试", onClick: downloadTemp } });
      });
    }

    function saveTempToServer() {
      if (!app.can("deployer")) {
        MC.toast.warning("仅部署者可保存", "请在服务器本机访问后再把临时文件保存到服务器");
        return;
      }
      var exists = app.files.some(function (f) { return f.name === S.name; });
      var ask = exists
        ? MC.confirm({ title: "覆盖 " + S.name, message: "服务器上已有同名文件。保存后当前版本会归档为历史版本；内容完全相同则不生成新的历史版本。", confirmText: "保存并归档" })
        : MC.confirm({ title: "保存到服务器", message: "将把这份临时文件保存为 " + S.name + "，本机型的所有人都可以打开。", confirmText: "保存" });
      ask.then(function (ok) {
        if (!ok) return;
        api.fetchBlob(app.p("/temp/" + api.seg(S.token) + "/raw")).then(function (res) {
          return api.upload(app.p("/files/" + api.seg(S.name)), res.blob, S.name, exists ? { overwrite: "1" } : {});
        }).then(function (r) {
          MC.toast.success(r.unchanged ? "内容与服务器版本一致" : "已保存 " + S.name, r.archived ? "旧版本已归档为 " + r.archived : r.unchanged ? "未产生新的历史版本" : null);
          app.syncRevision();
          return app.refreshFiles().then(function () { app.openFile(S.name); });
        }, function (err) {
          if (err.code === "FILE_EXISTS") MC.toast.warning("已存在同名文件", "请确认后再保存");
          else MC.toastError("保存失败", err);
        });
      });
    }

    /* ---------------- tree ---------------- */
    function favState() {
      var direct = {};
      var covered = {};
      S.favs.forEach(function (f) {
        direct[f.path] = f;
        (function walk(list) { (list || []).forEach(function (c) { covered[c.path] = f; walk(c.children); }); })(f.children);
      });
      return { direct: direct, covered: covered };
    }

    function remarkCount(i) {
      var loc = S.idx.loc[i];
      var id = S.idx.id[i];
      var n = 0;
      S.remarks.forEach(function (r) { if (r.locator ? r.locator === loc : r.node_path === id) n++; });
      return n;
    }

    function noteOf(i) {
      var f = S.favs.filter(function (x) { return x.path === S.idx.id[i]; })[0];
      return f ? f.note : "";
    }

    function renderTreeView() {
      if (!S.idx) return loadDoc();
      if (S.tree) {
        S.tree.destroy();
        S.tree = null;
      }
      var readOnly = isTemp || S.version !== "current";
      var filterInput = h("input.input.input-sm", { type: "search", placeholder: "在本文件中筛选（名称 / 值 / 备注）", "aria-label": "在本文件中筛选" });
      var matchInfo = h("span.match-nav");
      var paneHost = h("div.tree-pane");
      var status = h("div.tree-status");

      var runFilter = util.debounce(function () {
        var n = S.tree.setFilter(filterInput.value, isTemp ? null : noteOf);
        renderMatch(n ? { cur: 1, total: n } : null, filterInput.value.trim());
        updateStatus();
      }, 160);
      filterInput.addEventListener("input", runFilter);
      filterInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          renderMatch(S.tree.gotoMatch(e.shiftKey ? -1 : 1), filterInput.value.trim());
        } else if (e.key === "Escape" && filterInput.value) {
          e.stopPropagation();
          filterInput.value = "";
          runFilter();
        }
      });

      function renderMatch(info, kw) {
        util.clear(matchInfo);
        if (!kw) return;
        if (!info) {
          matchInfo.append(h("span", { style: "color:var(--c-warning-text)" }, "无匹配"));
          return;
        }
        matchInfo.append(
          h("span.num", info.cur + " / " + info.total),
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "上一个匹配", onclick: function () { renderMatch(S.tree.gotoMatch(-1), kw); } }, icon("chevron-up")),
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "下一个匹配", onclick: function () { renderMatch(S.tree.gotoMatch(1), kw); } }, icon("chevron-down"))
        );
      }

      var expandBtn = h("button.btn.btn-sm", { type: "button", "aria-haspopup": "menu" }, icon("expand"), "展开");
      expandBtn.addEventListener("click", function () {
        MC.menu(expandBtn, [
          { label: "展开到第 1 层", onClick: function () { S.tree.expandLevel(2); updateStatus(); } },
          { label: "展开到第 2 层", onClick: function () { S.tree.expandLevel(3); updateStatus(); } },
          { label: "展开到第 3 层", onClick: function () { S.tree.expandLevel(4); updateStatus(); } },
          { label: "全部展开", meta: S.idx.n > 20000 ? "节点较多" : null, onClick: function () { S.tree.expandAll(); updateStatus(); } },
        ]);
      });
      var collapseBtn = h("button.btn.btn-sm", { type: "button", onclick: function () { S.tree.collapseAll(); updateStatus(); } }, icon("collapse"), "全部折叠");
      var attrSwitch = MC.switchEl("显示属性", S.showAttrs, function (on) {
        S.showAttrs = on;
        util.storageSet("mc.showAttrs", on);
        S.tree.setShowAttrs(on);
        updateStatus();
      });
      attrSwitch.classList.add("small");
      var inspBtn = h("button.btn.btn-ghost.btn-icon.btn-sm" + (S.inspector ? ".is-active" : ""), { type: "button", "aria-label": "显示/隐藏详情面板", "aria-pressed": String(S.inspector), "data-tip": "详情面板", "data-tip-pos": "left" }, icon("inspector"));
      inspBtn.addEventListener("click", function () {
        S.inspector = !S.inspector;
        util.storageSet("mc.inspector", S.inspector);
        split.classList.toggle("is-inspector-hidden", !S.inspector);
        inspBtn.classList.toggle("is-active", S.inspector);
        inspBtn.setAttribute("aria-pressed", String(S.inspector));
      });

      var toolbar = h(
        "div.tree-toolbar",
        h("div.input-wrap.tree-filter", icon("search"), filterInput),
        matchInfo,
        h("span.header-sep"),
        expandBtn,
        collapseBtn,
        attrSwitch,
        h("span.spacer"),
        readOnly ? h("span.badge", icon("lock"), isTemp ? "临时文件：只读" : "历史内容：只读") : null,
        inspBtn
      );
      paneHost.append(toolbar);
      S.inspectorEl = h("aside.inspector", { "aria-label": "节点详情" });
      var split = h("div.fv-split" + (S.inspector ? "" : ".is-inspector-hidden"), paneHost, S.inspectorEl);
      util.replace(body, split);

      var fs = favState();
      S.tree = new MC.Tree(paneHost, S.idx, {
        showAttrs: S.showAttrs,
        label: S.name + " 的结构",
        decor: function (i) {
          var id = S.idx.id[i];
          return { star: isTemp ? null : fs.direct[id] ? "on" : fs.covered[id] ? "covered" : null, remarks: isTemp ? 0 : remarkCount(i) };
        },
        onSelect: function (i) {
          S.selected = i;
          renderInspector();
        },
        onActivate: function (i) {
          if (!S.inspector) inspBtn.click();
          S.selected = i;
          renderInspector();
        },
        onStar: isTemp || !MC.caps.favorites
          ? null
          : function (i) { toggleFav(i).then(function () { fs = favState(); S.tree.refresh(); renderInspector(); }); },
        starReason: isTemp || MC.caps.favorites ? null : UNWIRED.favorites,
        onStarBlocked: isTemp ? null : function () { MC.toast.info("收藏暂不可用", UNWIRED.favorites); },
      });
      S.refreshFavDecor = function () { fs = favState(); S.tree.refresh(); };
      paneHost.append(status);

      function updateStatus() {
        util.replace(
          status,
          h("span", S.idx.n - 1 + " 个节点"),
          h("span", "显示 " + S.tree.rows.length + " 行"),
          S.loadMs !== undefined ? h("span", "加载 " + S.loadMs + " ms") : null,
          h("span.spacer"),
          h("span", h("kbd", "↑"), h("kbd", "↓"), " 浏览 ", h("kbd", "←"), h("kbd", "→"), " 折叠/展开 ", isTemp ? null : [h("kbd", "S"), MC.caps.favorites ? " 收藏 " : " 收藏（未接入） "], h("kbd", "Enter"), " 详情")
        );
      }
      updateStatus();
      renderInspector();
    }

    function revealPending() {
      if (!S.pendingNode || !S.tree) return;
      var i = S.idx.byLoc[S.pendingNode];
      if (i === undefined) i = S.idx.byId[S.pendingNode];
      if (i !== undefined) {
        S.tree.reveal(i);
        S.tree.el.focus({ preventScroll: true });
      } else MC.toast.warning("未找到目标节点", "该节点在此版本中不存在");
      S.pendingNode = null;
    }

    function toggleFav(i) {
      var id = S.idx.id[i];
      var fs = favState();
      if (fs.direct[id]) {
        var removed = fs.direct[id];
        return api.del(app.p("/favorites"), { file: S.name, path: id }).then(function () {
          S.favs = S.favs.filter(function (f) { return f.path !== id; });
          MC.toast.info("已取消收藏 " + S.idx.l[i], null, {
            action: {
              label: "撤销",
              onClick: function () {
                api.post(app.p("/favorites"), { file: S.name, id: id }).then(function (res) {
                  var done = function () {
                    if (!S.favs.some(function (f) { return f.path === id; })) S.favs.push(res.item);
                    if (S.refreshFavDecor) S.refreshFavDecor();
                    renderInspector();
                    app.syncRevision();
                  };
                  if (removed.note) api.patch(app.p("/favorites"), { file: S.name, path: id, note: removed.note }).then(done, done);
                  else done();
                }, function (err) { MC.toastError("撤销失败", err); });
              },
            },
          });
          app.syncRevision();
        }, function (err) { MC.toastError("取消收藏失败", err); });
      }
      if (fs.covered[id]) {
        MC.toast.warning("已被父级收藏包含", "请先取消父级「" + fs.covered[id].label + "」的收藏");
        return Promise.resolve();
      }
      return api.post(app.p("/favorites"), { file: S.name, id: id }).then(function (res) {
        S.favs.push(res.item);
        var n = (res.item.children || []).length;
        MC.toast.success("已收藏 " + S.idx.l[i], n ? "包含 " + n + " 个子项，可在「速览」中查看" : "可在「速览」中查看实时值");
        app.syncRevision();
      }, function (err) {
        if (err.code === "FAVORITE_COVERED") MC.toast.warning("已被父级收藏包含", err.message || "请先取消父级收藏");
        else MC.toastError("收藏失败", err);
      });
    }

    /* ---------------- inspector ---------------- */
    function section(title, content, extra) {
      return h("section.insp-section", h("div.section-title", h("span.grow", title), extra || null), content);
    }

    function renderInspector() {
      var box = S.inspectorEl;
      if (!box) return;
      var i = S.selected;
      if (i < 0 || !S.idx) {
        util.replace(box, h("div.inspector-head", h("h3", "节点详情")), h("div.inspector-body", MC.stateBlock({ kind: "empty", icon: "info", title: "选择一个节点", desc: "点击左侧任意一行，查看完整值、参数范围、路径，并可收藏或提交修改建议。" })));
        return;
      }
      var idx = S.idx;
      var value = idx.v[i];
      var t = idx.t[i];
      var fs = favState();
      var id = idx.id[i];
      var parts = [];

      if (value !== null) {
        parts.push(section("值", h("div.insp-value", value), MC.copyButton(value, "复制值")));
      } else {
        parts.push(section("节点", h("div.small.muted", (idx.childCount[i] || 0) + " 个直接子项 · 共 " + (idx.size[i] - 1) + " 个后代节点")));
      }

      if (idx.isParam[i]) {
        var pm = idx.paramMeta[i];
        var cells = [];
        if (pm.default !== undefined) cells.push(["默认值", pm.default]);
        if (pm.min !== undefined) cells.push(["最小值", pm.min + (pm.incMin === "false" ? "（不含）" : pm.incMin === "true" ? "（含）" : "")]);
        if (pm.max !== undefined) cells.push(["最大值", pm.max + (pm.incMax === "false" ? "（不含）" : pm.incMax === "true" ? "（含）" : "")]);
        parts.push(
          section(
            "参数信息",
            h(
              "div.col",
              { style: "gap:10px" },
              h(
                "dl.kv",
                pm.description ? [h("dt", "描述"), h("dd", pm.description)] : null,
                pm.editPrivilege ? [h("dt", "编辑权限"), h("dd", pm.editPrivilege)] : null,
                pm.type ? [h("dt", "类型"), h("dd.mono", pm.type)] : null,
                pm.units ? [h("dt", "单位"), h("dd.mono", pm.units)] : null
              ),
              cells.length ? h("div.range-grid", cells.map(function (c) { return h("div.range-cell", h("div.range-k", c[0]), h("div.range-v", c[1])); })) : null,
              rangeWarning(value, pm)
            )
          )
        );
      } else if (t === "element") {
        var attrs = idx.attrsOf(i);
        if (attrs.length) {
          parts.push(section("属性", h("dl.kv", attrs.map(function (a) { return [h("dt.mono", idx.l[a]), h("dd.mono", idx.v[a])]; }))));
        }
      }

      var vpath = S.name + "/" + idx.displayPath(i, "/");
      parts.push(
        section(
          "位置",
          h(
            "div.col",
            { style: "gap:8px" },
            h("div.copyable", h("span.copy-text", { style: "font-family:var(--font-ui)" }, idx.displayPath(i)), MC.copyButton(idx.displayPath(i, "/"), "复制路径")),
            h("div.field-help", "绑定路径（用于「变量绑定」「DL 系数绑定」）"),
            h("div.copyable", h("span.copy-text", vpath), MC.copyButton(vpath, "复制绑定路径")),
            h("details", h("summary.xsmall.muted", { style: "cursor:pointer" }, "技术定位信息"), h("dl.kv", { style: "margin-top:6px" }, h("dt", "定位符"), h("dd.mono.xsmall", idx.loc[i]), h("dt", "节点 ID"), h("dd.mono.xsmall", id), h("dt", "类型"), h("dd.mono.xsmall", t)))
          )
        )
      );

      if (!isTemp) {
        var favBtn;
        if (!MC.caps.favorites) favBtn = h("button.btn", { type: "button", disabled: true, title: UNWIRED.favorites }, icon("star"), value === null ? "收藏此节点及子项" : "收藏到速览");
        else if (fs.direct[id]) favBtn = h("button.btn", { type: "button" }, h("span", { style: "color:var(--c-star)" }, icon("star", "is-filled")), "取消收藏");
        else if (fs.covered[id]) favBtn = h("button.btn", { type: "button", disabled: true, title: "已被父级收藏「" + fs.covered[id].label + "」包含" }, icon("star"), "已被父级收藏包含");
        else favBtn = h("button.btn", { type: "button" }, icon("star"), value === null ? "收藏此节点及子项" : "收藏到速览");
        if (MC.caps.favorites)
          favBtn.addEventListener("click", function () {
            MC.withBusy(favBtn, function () { return toggleFav(i).then(function () { if (S.refreshFavDecor) S.refreshFavDecor(); renderInspector(); }); });
          });
        var fav = fs.direct[id];
        parts.push(section("收藏", h("div.col", favBtn, fav && fav.note ? h("div.small.muted", "备注：" + fav.note) : null)));
        parts.push(renderSuggest(i));
      }

      util.replace(
        box,
        h(
          "div.inspector-head",
          h("h3.mono", { title: idx.l[i] }, idx.l[i]),
          h("span.badge", t),
          h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "清除选择", onclick: function () { S.selected = -1; renderInspector(); } }, icon("x"))
        ),
        h("div.inspector-body", parts)
      );
    }

    function rangeWarning(value, pm) {
      var v = Number(value);
      if (!isFinite(v)) return null;
      var lo = pm.min !== undefined ? Number(pm.min) : null;
      var hi = pm.max !== undefined ? Number(pm.max) : null;
      var below = lo !== null && (pm.incMin === "false" ? v <= lo : v < lo);
      var above = hi !== null && (pm.incMax === "false" ? v >= hi : v > hi);
      if (!below && !above) return null;
      return h("div.banner.banner-danger", icon("alert"), h("div.banner-body", "当前值超出参数范围"));
    }

    function renderSuggest(i) {
      var idx = S.idx;
      var value = idx.v[i];
      var loc = idx.loc[i];
      var list = S.remarks.filter(function (r) { return r.locator ? r.locator === loc : r.node_path === idx.id[i]; });
      var listEl = list.length
        ? h(
            "ul.suggestion-list",
            list.map(function (r) {
              return h("li.suggestion", h("div.s-head", icon("user"), h("span", r.actor_display), h("span.spacer"), h("span", util.formatTime(r.created_at))), h("div.s-value", r.original_value + " → " + r.proposed_value));
            })
          )
        : h("div.small.muted", "暂无待审建议");

      var form = null;
      if (S.version !== "current") {
        form = h("div.field-help", "只能对当前版本提交修改建议。");
      } else if (!MC.caps.remarks) {
        form = h("div.field-help", UNWIRED.remarks + "；接入后可在此对当前版本提交建议值。");
      } else if (value === null) {
        form = h("div.field-help", "该节点没有直接值，请选择具体的值节点。");
      } else {
        var input = h("input.input.input-mono", { type: "text", placeholder: "输入建议值", "aria-label": "建议值" });
        S.draftInput = input;
        var fld = MC.field({ label: "提交修改建议", control: input, help: "提交人：" + util.identityName(app.identity) + "。建议由管理员审阅，通过后生成新文件，不会直接覆盖原文件。" });
        var submit = h("button.btn.btn-primary", { type: "button" }, icon("message"), "提交建议");
        if (idx.t[i] === "NoneType") {
          input.disabled = true;
          submit.disabled = true;
          input.title = submit.title = NULL_VALUE_HINT;
        }
        input.addEventListener("input", function () { app.renderTabbar(); fld.setError(""); });
        input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit.click(); });
        submit.addEventListener("click", function () {
          var v = input.value.trim();
          if (!v) return fld.setError("请输入建议值");
          if (v === value) return fld.setError("建议值与当前值相同");
          MC.withBusy(submit, function () {
            return api.post(app.p("/remarks"), { file: S.name, sha256: S.doc.file.sha256, loc: loc, id: idx.id[i], label: idx.l[i], original_value: value, proposed_value: v, node_type: idx.t[i] }).then(
              function (res) {
                input.value = "";
                S.remarks.push(res.item);
                MC.toast.success("修改建议已提交", "等待管理员审阅");
                app.syncRevision();
                app.refreshFiles();
                S.tree.refresh();
                renderInspector();
                app.renderTabbar();
              },
              function (err) {
                if (err.code === "SOURCE_CHANGED") { S.stale = true; renderHeader(); }
                if (err.detail && err.detail.field) fld.setError(MC.errorText(err));
                else MC.toastError("提交失败", err);
              }
            );
          });
        });
        form = h("div.col", { style: "gap:8px" }, fld, h("div.row", h("span.spacer"), submit));
      }
      return section("修改建议" + (list.length ? "（" + list.length + "）" : ""), h("div.col", { style: "gap:12px" }, listEl, form));
    }

    /* ---------------- history ---------------- */
    function loadArchives() {
      return api.get(app.p("/files/" + api.seg(S.name) + "/archives")).then(function (res) { S.archives = res; return res; });
    }

    function renderHistory() {
      if (S.tree) { S.tree.destroy(); S.tree = null; }
      util.replace(body, MC.stateBlock({ kind: "loading", title: "正在读取历史版本…" }));
      loadArchives().then(
        function (res) {
          var picked = [];
          var cmpSel = h("button.btn.btn-primary.btn-sm", { type: "button", disabled: true }, icon("compare"), "对比所选");
          cmpSel.addEventListener("click", function () {
            var sorted = picked.slice().sort(function (a, b) { return order(a) - order(b); });
            nav({ view: "compare", base: sorted[0], target: sorted[1] });
          });
          function order(v) {
            if (v === "current") return 1e9;
            return -res.items.findIndex(function (x) { return "archive:" + x.filename === v; });
          }
          function pickBox(v, label) {
            var cb = h("input", { type: "checkbox", "aria-label": "选择 " + label + " 参与对比" });
            cb.addEventListener("change", function () {
              if (cb.checked) picked.push(v);
              else picked = picked.filter(function (x) { return x !== v; });
              if (picked.length > 2) {
                var drop = picked.shift();
                var other = table.querySelector('input[data-v="' + CSS.escape(drop) + '"]');
                if (other) other.checked = false;
              }
              cmpSel.disabled = picked.length !== 2;
              cmpSel.lastChild.textContent = picked.length === 2 ? "对比所选" : "对比所选（已选 " + picked.length + "/2）";
            });
            cb.dataset.v = v;
            return cb;
          }
          var rows = [
            h(
              "tr",
              h("td", pickBox("current", "当前版本")),
              h("td", h("div.row", h("span.version-name", res.current.name), h("span.badge.badge-success", "当前"))),
              h("td.num", util.formatTime(res.current.updated_at)),
              h("td.num", util.formatBytes(res.current.size)),
              h("td.muted", "—"),
              h("td.cell-actions", h("button.btn.btn-sm", { type: "button", onclick: function () { nav({ view: "tree" }); } }, "查看"), h("button.btn.btn-sm", { type: "button", onclick: function () { app.downloadFile(S.name); } }, icon("download")))
            ),
          ];
          res.items.forEach(function (a) {
            var v = "archive:" + a.filename;
            rows.push(
              h(
                "tr",
                h("td", pickBox(v, a.filename)),
                h("td", h("span.version-name", a.filename)),
                h("td.num", util.formatTime(a.date)),
                h("td.num", util.formatBytes(a.size)),
                h("td", { title: "与较新的 " + a.compared_to + " 相比" }, MC.diffStats(a.diff)),
                h(
                  "td.cell-actions",
                  h("button.btn.btn-sm", { type: "button", onclick: function () { nav({ view: "tree", version: v }); } }, "查看"),
                  h("button.btn.btn-sm", { type: "button", onclick: function () { nav({ view: "compare", base: v, target: "current" }); } }, "与当前对比"),
                  h("button.btn.btn-sm", { type: "button", "aria-label": "下载 " + a.filename, onclick: function () { app.downloadFile(S.name, v); } }, icon("download"))
                )
              )
            );
          });
          var table = h("table.table", h("thead", h("tr", h("th", { style: "width:36px" }, h("span.sr-only", "选择")), h("th", "版本"), h("th", "保存时间"), h("th", "大小"), h("th", "变更（相对较新版本）"), h("th.cell-actions", "操作"))), h("tbody", rows));
          util.replace(
            body,
            h(
              "div.view-body",
              h(
                "div.container",
                h(
                  "div.panel",
                  h("div.panel-header", h("h2", "历史版本"), h("span.muted.small", res.items.length + " 个归档"), cmpSel),
                  res.items.length
                    ? h("div.table-wrap", table)
                    : MC.stateBlock({ kind: "empty", icon: "history", title: "还没有历史版本", desc: "每次通过上传或更新源覆盖该文件时，旧版本会自动归档到这里。" })
                ),
                h("p.xsmall.muted", { style: "margin-top:10px" }, "勾选任意两个版本可直接对比；变更统计中 + 新增、− 删除、~ 修改，⛓ 为涉及绑定变量的条目。")
              )
            )
          );
        },
        function (err) { util.replace(body, MC.errorBlock(err, renderHistory)); }
      );
    }

    /* ---------------- compare ---------------- */
    function renderCompare() {
      if (S.tree) { S.tree.destroy(); S.tree = null; }
      var C = S.compare;
      if (isTemp) {
        C.base = C.base || "temp:" + S.token;
        C.target = C.target || (app.files.some(function (f) { return f.name === S.name; }) ? "current" : null);
      }
      var load = isTemp ? Promise.resolve({ items: [] }) : S.archives ? Promise.resolve(S.archives) : loadArchives();
      var recLoad = isTemp ? Promise.resolve({ items: [] }) : S.records ? Promise.resolve(S.records) : api.get(app.p("/files/" + api.seg(S.name) + "/records")).then(function (r) { S.records = r; return r; });
      util.replace(body, MC.stateBlock({ kind: "loading" }));
      Promise.all([load, recLoad]).then(function (res) {
        var archives = res[0].items;
        var records = res[1].items;
        if (!isTemp) {
          if (!C.base) C.base = archives[0] ? "archive:" + archives[0].filename : null;
          if (!C.target) C.target = "current";
        }
        var uploads = C.uploads || (C.uploads = []);

        function picker(side, label, cls) {
          var sel = h("select.select", { "aria-label": label });
          function opt(v, text) { return h("option", { value: v, selected: C[side] === v }, text); }
          var groups = [];
          if (isTemp) groups.push(opt("temp:" + S.token, "临时文件 " + S.name));
          if (!isTemp || app.files.some(function (f) { return f.name === S.name; })) groups.push(opt("current", "当前版本"));
          if (archives.length) groups.push(h("optgroup", { label: "历史版本" }, archives.map(function (a) { return opt("archive:" + a.filename, a.filename + " · " + util.formatTime(a.date)); })));
          if (records.length) groups.push(h("optgroup", { label: "修改记录" }, records.map(function (a) { return opt("record:" + a.filename, a.filename); })));
          if (uploads.length) groups.push(h("optgroup", { label: "已上传" }, uploads.map(function (u) { return opt("temp:" + u.token, u.name); })));
          // 上传入口在 M2-11 才接通：先让选项可见（UC-17），但禁用并说明原因，
          // 免得在生产模式下点出 /temp 的 404。
          var uploadOption = h("option", { value: "__upload__" }, MC.caps.temp ? "上传本地文件…" : "上传本地文件（M2-11 接入）");
          if (!MC.caps.temp) {
            uploadOption.disabled = true;
            uploadOption.title = UNWIRED.temp;
          }
          groups.push(uploadOption);
          if (!C[side]) groups.unshift(h("option", { value: "", selected: true, disabled: true }, "请选择"));
          sel.append.apply(sel, groups);
          sel.addEventListener("change", function () {
            if (sel.value === "__upload__") {
              sel.value = C[side] || "";
              pickUpload(side);
              return;
            }
            C[side] = sel.value;
            C.result = null;
            nav({ view: "compare", base: C.base, target: C.target });
          });
          return h("div.side-picker", h("div.side-label", h("span." + cls, label)), sel);
        }

        function pickUpload(side) {
          var fi = h("input", { type: "file", accept: ".xml,.json" });
          fi.addEventListener("change", function () {
            var f = fi.files[0];
            if (!f) return;
            api.upload(app.p("/temp"), f, f.name).then(function (r) {
              uploads.push({ token: r.token, name: r.name });
              C[side] = "temp:" + r.token;
              C.result = null;
              MC.toast.info("已上传 " + r.name, "仅用于本次对比，不会覆盖服务器文件");
              renderCompare();
            }, function (err) { MC.toastError("上传失败", err); });
          });
          fi.click();
        }

        var swap = h("button.btn.btn-icon", { type: "button", "aria-label": "交换基准与目标", "data-tip": "交换" }, icon("swap"));
        swap.addEventListener("click", function () {
          var b = C.base;
          C.base = C.target;
          C.target = b;
          C.result = null;
          nav({ view: "compare", base: C.base, target: C.target });
        });
        var runBtn = h("button.btn.btn-primary", { type: "button", disabled: !C.base || !C.target }, icon("compare"), "开始对比");
        var resultHost = h("div.col", { style: "gap:16px;margin-top:16px" });
        runBtn.addEventListener("click", function () { run(); });

        function run() {
          if (C.base === C.target) {
            MC.toast.warning("请选择两个不同的版本");
            return;
          }
          MC.withBusy(runBtn, function () {
            return api.post(app.p("/compare"), { base: sideToBody(S.name, C.base), target: sideToBody(S.name, C.target), text_limit: 2000 }).then(
              function (r) { C.result = r; renderResult(); },
              function (err) { util.replace(resultHost, MC.errorBlock(err, run)); }
            );
          });
        }

        function renderResult() {
          var r = C.result;
          if (!r) {
            util.replace(resultHost, h("div.panel", MC.stateBlock({ kind: "empty", icon: "compare", title: "选择两个版本后开始对比", desc: "左侧为基准（旧），右侧为目标（新）。结果分为结构化差异与文本差异。" })));
            return;
          }
          if (!r.has_changes) {
            util.replace(resultHost, h("div.panel", MC.stateBlock({ kind: "empty", icon: "check-circle", title: "两个版本内容完全相同", desc: r.base.label + " 与 " + r.target.label + " 没有任何差异。" })));
            return;
          }
          var st = r.structural;
          var s = st.summary;
          function card(key, label, n, cls) {
            var c = h("button.stat-card." + cls + (C.filter === key ? ".is-active" : ""), { type: "button", "aria-pressed": String(C.filter === key) }, h("span.stat-n", String(n)), h("span", label));
            c.addEventListener("click", function () { C.filter = C.filter === key ? "all" : key; renderResult(); });
            return c;
          }
          var qInput = h("input.input.input-sm", { type: "search", placeholder: "筛选路径或值", value: C.q, "aria-label": "筛选差异" });
          var tableHost = h("div");
          qInput.addEventListener("input", util.debounce(function () { C.q = qInput.value; renderTable(); }, 150));
          var boundSw = MC.switchEl("仅绑定变量", C.boundOnly, function (on) { C.boundOnly = on; renderTable(); });
          boundSw.classList.add("small");

          function renderTable() {
            var q = C.q.trim().toLowerCase();
            var list = st.items.filter(function (it) {
              if (C.filter !== "all" && it.op !== C.filter) return false;
              if (C.boundOnly && !it.bound_group) return false;
              if (q && (it.path + " " + it.old + " " + it.new).toLowerCase().indexOf(q) < 0) return false;
              return true;
            });
            var shown = list.slice(0, C.showAll ? list.length : 300);
            var canLocate = C.target === "current" || (isTemp && C.target === "temp:" + S.token);
            util.replace(
              tableHost,
              list.length
                ? h(
                    "div.table-wrap",
                    h(
                      "table.table.is-compact.diff-table",
                      h("thead", h("tr", h("th", "类型"), h("th", "路径"), h("th", "基准值"), h("th", "目标值"), h("th", "绑定组"), h("th.cell-actions", ""))),
                      h(
                        "tbody",
                        shown.map(function (it) {
                          return h(
                            "tr",
                            h("td", h("span.op-tag.op-" + it.op, { "aria-label": { added: "新增", removed: "删除", modified: "修改" }[it.op] }, { added: "新增", removed: "删除", modified: "修改" }[it.op])),
                            h("td.path", util.highlight(it.path, q)),
                            h("td.val.old", it.old === null ? h("span.muted", "—") : util.highlight(it.old, q)),
                            h("td.val.new", it.new === null ? h("span.muted", "—") : util.highlight(it.new, q)),
                            h("td", it.bound_group ? h("span.badge.badge-bind", it.bound_group) : null),
                            h("td.cell-actions", canLocate && it.op !== "removed" ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { nav({ view: "tree", node: it.loc }); } }, "定位") : null)
                          );
                        })
                      )
                    ),
                    list.length > shown.length ? h("div", { style: "padding:10px 16px" }, h("button.link-btn", { type: "button", onclick: function () { C.showAll = true; renderTable(); } }, "显示全部 " + list.length + " 条")) : null
                  )
                : MC.stateBlock({ kind: "empty", title: "没有符合筛选条件的差异" })
            );
          }

          var udiff = r.unified;
          var udiffEl;
          if (udiff.too_large) udiffEl = MC.stateBlock({ kind: "empty", title: "文件过大，未生成文本差异", desc: "请以结构化差异为准，或下载两个版本后使用本地工具对比。" });
          else
            udiffEl = h(
              "pre.udiff",
              udiff.lines.map(function (l) {
                var cls = /^(\+\+\+|---)/.test(l) ? "meta" : l.charAt(0) === "@" ? "hunk" : l.charAt(0) === "+" ? "add" : l.charAt(0) === "-" ? "del" : "";
                return h("span.ul" + (cls ? "." + cls : ""), l || " ");
              })
            );
          var textOpen = util.storageGet("mc.udiffOpen", false);
          var textPanel = h(
            "details.panel",
            { open: textOpen },
            h("summary.panel-header", { style: "cursor:pointer;list-style:none" }, icon("chevron-right"), h("h3", "文本差异（unified diff）"), h("span.muted.small", udiff.total + " 行" + (udiff.truncated ? "，仅显示前 " + udiff.lines.length + " 行" : "")), MC.copyButton(function () { return udiff.lines.join("\n"); }, "复制差异文本"), h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "下载 .diff 文件", "data-tip": "下载 .diff", onclick: function (e) { e.preventDefault(); util.downloadBlob(udiff.lines.join("\n"), S.name + ".diff"); } }, icon("download"))),
            h("div.panel-body.flush", udiffEl)
          );
          textPanel.addEventListener("toggle", function () { util.storageSet("mc.udiffOpen", textPanel.open); });

          util.replace(
            resultHost,
            h("div.compare-summary", h("span.small.muted", r.base.label + " → " + r.target.label + "："), card("added", "新增", s.added, "s-add"), card("removed", "删除", s.removed, "s-del"), card("modified", "修改", s.modified, "s-mod"), s.bound ? h("span.stat-card.s-bind", h("span.stat-n", String(s.bound)), h("span", "涉及绑定变量")) : null),
            st.type === "structural"
              ? h("div.panel", h("div.panel-header", h("h3", "结构化差异"), h("div.input-wrap", { style: "width:240px" }, icon("search"), qInput), boundSw), h("div.panel-body.flush", tableHost))
              : h("div.banner.banner-neutral", icon("info"), h("div.banner-body", "至少一个版本无法解析为 XML/JSON，仅提供文本差异。")),
            textPanel
          );
          if (st.type === "structural") renderTable();
        }

        util.replace(
          body,
          h(
            "div.view-body",
            h(
              "div.container",
              h("div.compare-pickers", picker("base", "基准（旧）", "tag-old"), h("div", { style: "padding-bottom:6px" }, swap), picker("target", "目标（新）", "tag-new"), h("div", { style: "padding-bottom:6px" }, runBtn)),
              resultHost
            )
          )
        );
        renderResult();
        if (!C.result && C.base && C.target && C.base !== C.target) run();
      }, function (err) { util.replace(body, MC.errorBlock(err, renderCompare)); });
    }

    /* ---------------- records ---------------- */
    function loadRecords() {
      return api.get(app.p("/files/" + api.seg(S.name) + "/records")).then(function (r) { S.records = r; if (S.view === "records") drawRecords(); return r; });
    }

    function renderRecords() {
      if (S.tree) { S.tree.destroy(); S.tree = null; }
      util.replace(body, MC.stateBlock({ kind: "loading", title: "正在读取修改记录…" }));
      loadRecords().then(null, function (err) { util.replace(body, MC.errorBlock(err, renderRecords)); });
    }

    function drawRecords() {
      var res = S.records;
      // 「立即拉取」要 POST /tasks，属 M3-08；生产模式下禁用并说明原因，别打空接口。
      var mayFetch = app.can("deployer") && res.record_url && MC.caps.tools;
      var fetchTitle = !res.record_url ? "未配置修改记录链接" : !app.can("deployer") ? "仅部署者可手动拉取" : !MC.caps.tools ? "拉取修改记录需要任务接口（M3-07 / M3-09）" : null;
      var refresh = h("button.btn.btn-sm", { type: "button", disabled: !mayFetch, title: fetchTitle }, icon("refresh"), "立即拉取");
      refresh.addEventListener("click", function () {
        MC.withBusy(refresh, function () {
          return app.startTask({ type: "refresh_records", name: S.name }).then(function () { MC.toast.info("已开始拉取修改记录", "完成后列表会自动刷新"); }, function (err) { MC.toastError("无法开始拉取", err); });
        });
      });
      var rows = res.items.map(function (r, k) {
        var v = "record:" + r.filename;
        var prev = res.items[k + 1];
        return h(
          "tr",
          h("td", h("span.version-name", r.filename)),
          h("td.num", util.formatTime(r.date)),
          h("td.num", util.formatBytes(r.size)),
          h("td", prev ? MC.diffStats(r.diff_prev) : h("span.muted.xsmall", "最早一条")),
          h(
            "td.cell-actions",
            h("button.btn.btn-sm", { type: "button", onclick: function () { nav({ view: "tree", version: v }); } }, "查看"),
            prev ? h("button.btn.btn-sm", { type: "button", onclick: function () { nav({ view: "compare", base: "record:" + prev.filename, target: v }); } }, "与上一条对比") : null,
            h("button.btn.btn-sm", { type: "button", "aria-label": "下载 " + r.filename, onclick: function () { app.downloadFile(S.name, v); } }, icon("download"))
          )
        );
      });
      util.replace(
        body,
        h(
          "div.view-body",
          h(
            "div.container",
            h(
              "div.panel",
              h(
                "div.panel-header",
                h("h2", "修改记录"),
                h("span.small.muted.truncate", { style: "max-width:420px", title: res.record_url }, res.record_url ? "来源：" + res.record_url : "未配置修改记录链接"),
                refresh
              ),
              res.items.length
                ? h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "记录文件"), h("th", "拉取时间"), h("th", "大小"), h("th", "相对上一条"), h("th.cell-actions", "操作"))), h("tbody", rows)))
                : MC.stateBlock({
                    kind: "empty",
                    icon: "records",
                    title: "暂无修改记录",
                    desc: res.record_url ? "定时更新时会自动拉取；也可点击「立即拉取」。" : "请在「设置 › 更新源」为该文件配置修改记录链接。",
                    actions: !res.record_url && app.can("deployer") ? [h("button.btn", { type: "button", onclick: function () { app.go({ kind: "settings", arg: "sources" }); } }, "前往配置")] : null,
                  })
            ),
            h("p.xsmall.muted", { style: "margin-top:10px" }, "修改记录由更新源服务器提供，与“历史版本”（本系统归档的配置文件）是两类不同的数据。")
          )
        )
      );
    }

    applyRoute(route);
    return inst;
  }

  MC.views.f = factory;
  MC.views.t = factory;
})(window);
