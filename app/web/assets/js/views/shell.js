/*
 * 应用外壳：顶栏 / 侧栏 / 标签工作区 / 路由同步 / 多用户变更轮询 / 命令面板。
 * 视图注册：MC.views[kind] = function (app, route) -> {el, title, icon, update(route), destroy(), isDirty()}
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  MC.views = MC.views || {};

  var NAV = [
    { kind: "overview", label: "速览", icon: "overview" },
    { kind: "search", label: "全局搜索", icon: "search" },
    { kind: "review", label: "审阅修改", icon: "review", admin: true },
    { kind: "dl", label: "DL 计算", icon: "calc" },
    { kind: "tools", label: "外部工具", icon: "tool" },
    { kind: "settings", label: "设置", icon: "settings" },
  ];

  var POLL_MS = 10000;

  /** 任务类型/状态的展示词汇。§7.10：task.state 只说「跑没跑完」，
   * 业务结果在条目 status 里（列表页各自显示，两者不混用）。 */
  var TASK_STATE_LABELS = { queued: "排队中", running: "进行中", succeeded: "已完成", partial: "部分失败", failed: "失败" };
  function taskLabel(t) {
    if (t.type === "update_source") return "更新 " + (t.target || "");
    return { update_all: "全量更新", refresh_records: "拉取修改记录" }[t.type] || t.type;
  }
  /**
   * 进度文案（卡 6 移交的契约）：后端 `services/tasks.py::_publish_single` 在
   * `updates.update_source` **返回之后**才写 done/total，单条任务在整个下载期间
   * 都是 0/0 ——那是「还没开始报数」，不是「0 步里完成了 0 步」。渲染成「准备中」，
   * 与 mock-api.js 同口径（本卡已把 mock 改成建任务时不预发 total）。
   */
  function taskProgressText(t) {
    return t.progress && t.progress.total ? t.progress.done + "/" + t.progress.total : "准备中";
  }

  function App(root) {
    this.root = root;
    this.session = null;
    this.pid = null;
    this.files = [];
    this.tools = [];
    this.tabs = [];
    this.instances = {};
    this.activeKey = null;
    this.rev = null;
    this.activeTasks = [];
    this.fileFilter = "";
    this.sidebarOpen = false;
  }

  /* ===================== bootstrap ===================== */
  App.prototype.start = function () {
    var self = this;
    MC.icons.inject();
    var route = MC.router.current();
    var pid = (route && route.pid) || util.storageGet("mc.profile", "default");
    this.renderSkeleton();
    return this.loadSession(pid).then(function () {
      MC.router.start();
      MC.bus.on("route", function (r) { self.onRoute(r); });
      var r = MC.router.current();
      if (!r || r.pid !== self.pid) {
        var saved = util.storageGet("mc.active." + self.pid, null);
        r = (saved && MC.router.parse(saved)) || { pid: self.pid, kind: "overview" };
        MC.router.go(r, true);
      } else self.onRoute(r);
      self.startPolling();
      document.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
          e.preventDefault();
          self.openPalette();
        }
      });
    });
  };

  App.prototype.loadSession = function (pid) {
    var self = this;
    return api.get("/session", { profile: pid }).then(
      function (s) {
        self.session = s;
        self.pid = s.profile.id;
        util.storageSet("mc.profile", self.pid);
        self.restoreTabs();
        self.renderChrome();
        return Promise.all([self.refreshFiles(), self.refreshTools()]);
      },
      function (err) {
        util.replace(self.root, MC.errorBlock(err, function () { location.reload(); }));
        throw err;
      }
    );
  };

  Object.defineProperty(App.prototype, "identity", {
    get: function () { return this.session ? this.session.identity : {}; },
  });

  App.prototype.can = function (cap) {
    var id = this.identity || {};
    var caps = id.capabilities;
    // 服务端下发能力矩阵时以它为准：未列出的键一律 false（deny-by-default）。
    // 矩阵缺失（极旧响应）时退回角色判断，避免整页入口消失。
    if (caps && Object.prototype.hasOwnProperty.call(caps, cap)) return !!caps[cap];
    if (cap === "deployer" || cap === "admin") return !!id.is_admin;
    return false;
  };

  App.prototype.p = function (rest) {
    return api.p(this.pid, rest);
  };

  /* ===================== layout ===================== */
  App.prototype.renderSkeleton = function () {
    this.header = h("header.app-header");
    this.sidebar = h("aside.app-sidebar", { "aria-label": "导航与文件" });
    this.tabbar = h("div.tabbar", { role: "tablist", "aria-label": "已打开的页面" });
    this.viewHost = h("div.view-host");
    this.main = h("main.app-main", this.tabbar, this.viewHost);
    this.shell = h("div.app", this.header, this.sidebar, this.main);
    if (util.storageGet("mc.sidebarCollapsed", false)) this.shell.classList.add("is-sidebar-collapsed");
    util.replace(this.root, this.shell);
    this.viewHost.appendChild(MC.stateBlock({ kind: "loading", title: "正在连接服务器…" }));
  };

  App.prototype.renderChrome = function () {
    this.renderHeader();
    this.renderSidebar();
    this.renderTabbar();
  };

  App.prototype.toggleSidebar = function () {
    if (global.innerWidth <= 960) {
      this.shell.classList.toggle("is-sidebar-open");
      return;
    }
    var c = this.shell.classList.toggle("is-sidebar-collapsed");
    util.storageSet("mc.sidebarCollapsed", c);
  };

  App.prototype.renderHeader = function () {
    var self = this;
    var id = this.identity;
    var shownName = util.identityName(id);
    var profileName = this.session.profile.name;

    var profileBtn = h(
      "button.btn.btn-ghost.profile-switch",
      { type: "button", "aria-haspopup": "menu", "aria-label": "当前机型：" + profileName + "，点击切换" },
      icon("device"),
      h("span.profile-caption", "机型"),
      h("span.profile-name", profileName),
      icon("chevron-down")
    );
    profileBtn.addEventListener("click", function () {
      var items = [{ header: "切换机型" }].concat(
        self.session.profiles.map(function (m) {
          return { label: m.name, meta: m.id, checked: m.id === self.pid, onClick: function () { self.switchProfile(m.id); } };
        })
      );
      items.push("sep", { label: "管理机型…", icon: "settings", onClick: function () { self.go({ kind: "settings", arg: "profiles" }); } });
      MC.menu(profileBtn, items);
    });

    var searchBtn = h(
      "button.search-trigger",
      { type: "button", "aria-label": "搜索文件、变量、值或备注（Ctrl+K）" },
      icon("search"),
      h("span.grow", "搜索文件、变量名、值或备注…"),
      h("span.search-hint", h("kbd", "Ctrl"), " ", h("kbd", "K"))
    );
    searchBtn.addEventListener("click", function () { self.openPalette(); });

    this.taskSlot = h("div.row");
    var idBtn = h(
      "button.identity-chip",
      { type: "button", "aria-haspopup": "dialog", "aria-label": "当前身份：" + shownName + "，" + id.role_label },
      h("span.avatar" + (id.is_admin ? ".is-admin" : ""), util.initials(shownName)),
      h("span.identity-name", shownName),
      h("span.badge" + (id.is_admin ? ".badge-primary" : ""), id.role_label)
    );
    idBtn.addEventListener("click", function () { self.openIdentity(idBtn); });

    var demo = null;
    if (MC.config && MC.config.mock) {
      demo = h("button.btn.btn-sm.demo-switch", { type: "button", title: "仅模板演示：切换模拟身份" }, icon("beaker"), "演示");
      demo.addEventListener("click", function () { self.openDemoMenu(demo); });
    }

    util.replace(
      this.header,
      h("button.btn.btn-ghost.btn-icon", { type: "button", "aria-label": "展开/收起侧栏", "data-tip": "侧栏", onclick: function () { self.toggleSidebar(); } }, icon("sidebar")),
      h("div.brand", h("span.brand-mark", "MC"), h("span.brand-text", "MCChecker")),
      h("span.header-sep"),
      profileBtn,
      searchBtn,
      this.taskSlot,
      demo,
      idBtn
    );
    this.renderTasks();
  };

  App.prototype.openIdentity = function (anchor) {
    var id = this.identity;
    var shown = util.identityName(id);
    function cap(ok, text) {
      return h("li.row", { style: "gap:8px;padding:3px 0" }, h("span", { style: { color: ok ? "var(--c-success)" : "var(--c-text-disabled)" } }, icon(ok ? "check" : "x")), h("span" + (ok ? "" : ".muted"), text));
    }
    var body = h(
      "div",
      { style: "padding:10px 10px 6px;width:300px" },
      h("div.row", h("span.avatar.is-admin", util.initials(shown)), h("div.col", { style: "gap:0" }, h("strong", shown), h("span.xsmall.muted", id.role_label + (id.registered === false ? " · 未登记姓名" : "")))),
      h("div.divider"),
      h(
        "ul",
        { style: "list-style:none;margin:0;padding:0;font-size:13px" },
        cap(true, "查看、搜索、对比、下载配置"),
        cap(true, "收藏变量、提交修改建议"),
        cap(id.is_admin, "审阅、删除文件、保存配置、修改设置与机型（管理员）")
      ),
      h("div.divider"),
      h("div.xsmall.muted", "身份按访问 IP 识别，姓名仅作助记。如需开通权限，请将下列地址提供给管理员，并在「设置 › 人员与权限」中登记。"),
      h("div.copyable", { style: "margin-top:8px" }, h("span.copy-text", id.ip), MC.copyButton(id.ip, "复制地址"))
    );
    MC.popover(anchor, body, { align: "end" });
  };

  App.prototype.openDemoMenu = function (anchor) {
    var self = this;
    var items = [{ header: "模拟身份（仅模板）" }];
    Object.keys(MC.mock.identities).forEach(function (k) {
      items.push({
        label: MC.mock.identities[k].label,
        checked: MC.mock.identityKey() === k,
        onClick: function () {
          MC.mock.setIdentity(k);
          self.reloadSession();
        },
      });
    });
    items.push("sep", {
      label: "模拟他人更新 scenario_a.xml",
      icon: "refresh",
      onClick: function () {
        if (MC.mock.simulateExternalChange(self.pid)) MC.toast.info("已模拟一次外部更新", "约 10 秒内各页面会提示数据已更新");
        else MC.toast.warning("当前机型没有 scenario_a.xml");
      },
    });
    MC.menu(anchor, items, { align: "end" });
  };

  App.prototype.reloadSession = function () {
    var self = this;
    var route = MC.router.current();
    Object.keys(this.instances).forEach(function (k) { self.destroyInstance(k); });
    return this.loadSession(this.pid).then(function () {
      self.onRoute(route || { pid: self.pid, kind: "overview" });
    });
  };

  App.prototype.switchProfile = function (pid) {
    if (pid === this.pid) return;
    var self = this;
    var dirty = this.tabs.filter(function (t) { var inst = self.instances[t.key]; return inst && inst.isDirty && inst.isDirty(); });
    var proceed = dirty.length
      ? MC.confirm({ title: "切换机型", message: "有 " + dirty.length + " 个页面存在未保存的修改，切换后这些修改会丢失。", confirmText: "仍然切换", danger: true })
      : Promise.resolve(true);
    proceed.then(function (ok) {
      if (!ok) return;
      Object.keys(self.instances).forEach(function (k) { self.destroyInstance(k); });
      self.loadSession(pid).then(function () {
        var saved = util.storageGet("mc.active." + pid, null);
        MC.router.go((saved && MC.router.parse(saved)) || { pid: pid, kind: "overview" });
        MC.toast.info("已切换到机型：" + self.session.profile.name);
      });
    });
  };

  /* ===================== sidebar ===================== */
  App.prototype.renderSidebar = function () {
    var self = this;
    this.navList = h("ul.nav-list");
    this.fileListEl = h("ul.file-list", { "aria-label": "配置文件" });
    this.fileCountEl = h("span.count");
    var filter = h("input.input.input-sm", { type: "search", placeholder: "筛选文件名", "aria-label": "筛选文件名", value: this.fileFilter });
    filter.addEventListener("input", function () {
      self.fileFilter = filter.value;
      self.renderFileList();
    });
    var importBtn = h(
      "button.btn.btn-ghost.btn-icon.btn-sm",
      {
        type: "button",
        "aria-label": "导入配置文件",
        "data-tip": "导入配置文件",
      },
      icon("plus")
    );
    importBtn.addEventListener("click", function () { MC.openImport(self); });

    util.replace(
      this.sidebar,
      h(
        "div.sidebar-scroll",
        h("nav", { "aria-label": "主导航" }, this.navList),
        h(
          "section.side-section",
          h("div.side-section-head", h("span", "配置文件 "), this.fileCountEl, h("span.spacer"), importBtn),
          h("div.side-filter.input-wrap", icon("filter"), filter),
          this.fileListEl
        )
      ),
      h("div.sidebar-foot", "MCChecker " + this.session.server.version)
    );
    this.renderNav();
    this.renderFileList();
  };

  App.prototype.renderNav = function () {
    var self = this;
    var active = MC.router.current();
    util.clear(this.navList);
    NAV.forEach(function (n) {
      if (n.admin && !self.can("admin")) return;
      var meta = null;
      if (n.kind === "review") {
        var pending = self.files.reduce(function (s, f) { return s + (f.pending_remarks || 0); }, 0);
        if (pending) meta = h("span.badge-count.nav-meta", { "aria-label": pending + " 条待审" }, String(pending));
      }
      var title = n.kind === "settings" && !self.can("deployer")
        ? "可以查看。只有管理员可以修改这些设置"
        : null;
      var btn = h(
        "button.nav-item",
        {
          type: "button",
          "aria-current": active && active.kind === n.kind ? "page" : null,
          title: title,
        },
        icon(n.icon),
        h("span", n.label),
        meta
      );
      btn.addEventListener("click", function () {
        self.go({ kind: n.kind });
        self.shell.classList.remove("is-sidebar-open");
      });
      self.navList.appendChild(h("li", btn));
    });
  };

  App.prototype.refreshFiles = function () {
    var self = this;
    return api.get(this.p("/files")).then(
      function (res) {
        self.files = res.items;
        self.renderFileList();
        self.renderNav();
        MC.bus.emit("files", self.files);
      },
      function (err) { MC.toastError("文件列表加载失败", err); }
    );
  };

  App.prototype.refreshTools = function () {
    var self = this;
    return api.get(this.p("/tools")).then(function (res) {
      self.tools = res.items;
      MC.bus.emit("tools", self.tools);
    }, function () {});
  };

  App.prototype.renderFileList = function () {
    var self = this;
    if (!this.fileListEl) return;
    var kw = this.fileFilter.trim().toLowerCase();
    var list = this.files.filter(function (f) { return !kw || f.name.toLowerCase().indexOf(kw) >= 0; });
    this.fileCountEl.textContent = String(this.files.length);
    util.clear(this.fileListEl);
    if (!this.files.length) {
      this.fileListEl.appendChild(
        h("li", MC.stateBlock({ kind: "empty", icon: "file", title: "暂无配置文件", desc: "点击上方 + 导入本地文件或链接" }))
      );
      return;
    }
    if (!list.length) {
      this.fileListEl.appendChild(h("li.xsmall.muted", { style: "padding:8px" }, "没有匹配“" + this.fileFilter + "”的文件"));
      return;
    }
    var activeRoute = MC.router.current();
    list.forEach(function (f) {
      var isActive = activeRoute && activeRoute.kind === "f" && activeRoute.arg === f.name;
      var meta = [util.formatTime(f.updated_at)];
      if (f.archive_count) meta.push(f.archive_count + " 个历史版本");
      if (!f.mapped) meta.push("手动导入");
      var openBtn = h("button.file-open", { type: "button", "aria-label": "打开 " + f.name });
      openBtn.addEventListener("click", function () {
        self.openFile(f.name);
        self.shell.classList.remove("is-sidebar-open");
      });
      var dl = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "下载 " + f.name, "data-tip": "下载当前版本" }, icon("download"));
      dl.addEventListener("click", function () { self.downloadFile(f.name); });
      var more = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "更多操作", "aria-haspopup": "menu" }, icon("more"));
      more.addEventListener("click", function () {
        MC.menu(more, [
          { label: "打开", icon: "file", onClick: function () { self.openFile(f.name); } },
          { label: "版本历史", icon: "history", onClick: function () { self.openFile(f.name, { view: "history" }); } },
          { label: "复制文件名", icon: "copy", onClick: function () { util.copyText(f.name).then(function () { MC.toast.success("已复制文件名"); }); } },
          "sep",
          {
            label: "删除文件…",
            icon: "trash",
            danger: true,
            disabled: !self.can("admin"),
            title: self.can("admin") ? null : "仅管理员可删除文件",
            onClick: function () { self.deleteFile(f); },
          },
        ], { align: "end" });
      });
      self.fileListEl.appendChild(
        h(
          "li.file-item" + (isActive ? ".is-active" : ""),
          openBtn,
          util.fmtTag(f.name),
          h("div.file-text", h("div.file-name", { title: f.name }, f.name), h("div.file-meta", meta.join(" · "))),
          f.pending_remarks ? h("span.file-flag.badge.badge-warning", { title: f.pending_remarks + " 条待审建议" }, icon("message"), String(f.pending_remarks)) : null,
          h("div.file-actions", dl, more)
        )
      );
    });
  };

  App.prototype.downloadFile = function (name, version) {
    var self = this;
    var q = version && version !== "current" ? { version: version } : {};
    return api.download(this.p("/files/" + api.seg(name) + "/raw"), q, name).then(
      function () { MC.toast.success("开始下载", version && version !== "current" ? version.split(":")[1] : name); },
      function (err) {
        MC.toastError("下载失败", err, { action: { label: "重试", onClick: function () { self.downloadFile(name, version); } } });
      }
    );
  };

  App.prototype.deleteFile = function (f) {
    var self = this;
    MC.confirm({
      title: "删除文件 " + f.name,
      message: "将同时删除该文件的 " + f.archive_count + " 个历史版本、" + f.record_count + " 条修改记录，以及相关的收藏与修改建议。",
      detail: h("div.banner.banner-warning", icon("alert"), h("div.banner-body", "删除后文件会移入服务器恢复区，需管理员手工恢复。")),
      confirmText: "删除",
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      api.del(self.p("/files/" + api.seg(f.name))).then(
        function (r) {
          MC.toast.success("已删除 " + f.name, "原文与历史已移入恢复区 " + r.recovery + "，需管理员在服务器上手工恢复");
          self.closeTab("f:" + f.name);
          self.syncRevision();
          self.refreshFiles();
        },
        function (err) { MC.toastError("删除失败", err); }
      );
    });
  };

  /* ===================== tabs ===================== */
  /**
   * 标签淘汰与持久化。上限不含速览。dirty 由调用方根据视图 isDirty() 填入。
   * 临时文件（kind=t）不写入 mc.tabs。
   */
  MC.tabs = {
    persistable: function (tab) {
      return !!(tab && !tab.pinned && tab.route && tab.route.kind !== "t");
    },
    victim: function (tabs, activeKey) {
      var candidates = tabs.filter(function (t) {
        return !t.pinned && t.key !== activeKey && !t.dirty;
      });
      if (!candidates.length) return null;
      candidates.sort(function (a, b) {
        return (a.lastUsed || a.opened || 0) - (b.lastUsed || b.opened || 0);
      });
      return candidates[0].key;
    },
    enforce: function (tabs, activeKey, limit) {
      var list = tabs.map(function (t) {
        return { key: t.key, pinned: !!t.pinned, dirty: !!t.dirty, lastUsed: t.lastUsed || 0, opened: t.opened || 0 };
      });
      var closed = [];
      var warning = false;
      while (list.length > limit + 1) {
        var key = MC.tabs.victim(list, activeKey);
        if (!key) {
          warning = true;
          break;
        }
        closed.push(key);
        list = list.filter(function (t) { return t.key !== key; });
      }
      return { closed: closed, warning: warning, remaining: list.length };
    },
  };

  function keyOf(r) {
    if (r.kind === "f") return "f:" + r.arg;
    if (r.kind === "t") return "t:" + r.arg;
    return r.kind;
  }

  App.prototype.restoreTabs = function () {
    var saved = util.storageGet("mc.tabs." + this.pid, []);
    this.tabs = [{ key: "overview", route: { kind: "overview" }, pinned: true }];
    var self = this;
    saved.forEach(function (t) {
      if (t.key !== "overview" && t.route && t.route.kind && MC.tabs.persistable(t)) {
        self.tabs.push({ key: t.key, route: t.route, title: t.title, opened: t.opened || 0, lastUsed: t.lastUsed || t.opened || 0 });
      }
    });
  };

  App.prototype.saveTabs = function () {
    util.storageSet(
      "mc.tabs." + this.pid,
      this.tabs.filter(MC.tabs.persistable).map(function (t) {
        return { key: t.key, route: t.route, title: t.title, opened: t.opened, lastUsed: t.lastUsed || t.opened || 0 };
      })
    );
  };

  App.prototype.go = function (r, replace) {
    MC.router.go(Object.assign({ pid: this.pid }, r), replace);
  };

  App.prototype.openFile = function (name, query) {
    this.go({ kind: "f", arg: name, query: query || {} });
  };

  App.prototype.onRoute = function (r) {
    if (!r) return this.go({ kind: "overview" }, true);
    if (r.pid !== this.pid) {
      var self = this;
      Object.keys(this.instances).forEach(function (k) { self.destroyInstance(k); });
      return this.loadSession(r.pid).then(function () { self.onRoute(r); });
    }
    var key = keyOf(r);
    var tab = this.tabs.filter(function (t) { return t.key === key; })[0];
    var routeData = { kind: r.kind, arg: r.arg, query: r.query };
    if (!tab) {
      tab = { key: key, route: routeData, opened: Date.now() };
      this.tabs.push(tab);
      this.enforceLimit(key);
    } else tab.route = routeData;
    util.storageSet("mc.active." + this.pid, global.location.hash);
    this.activate(key, r);
  };

  App.prototype.showUnwired = function (r) {
    var self = this;
    Object.keys(this.instances).forEach(function (k) {
      self.instances[k].el.classList.add("hidden");
    });
    var placeholder = this.viewHost.querySelector(":scope > .state");
    if (placeholder) placeholder.remove();
    this.viewHost.appendChild(
      MC.stateBlock({
        kind: "locked",
        title: defaultTitle({ route: r }) + " 尚未接入",
        desc: "该页面还没有接入真实后端。",
        actions: [h("button.btn.btn-primary", { type: "button", onclick: function () { self.go({ kind: "overview" }); } }, "返回速览")],
      })
    );
    this.activeKey = null;
    this.renderTabbar();
  };

  App.prototype.enforceLimit = function (activeKey) {
    var limit = (this.session.limits && this.session.limits.tab_limit) || 15;
    var self = this;
    while (this.tabs.length > limit + 1) {
      var snapshot = this.tabs.map(function (t) {
        var inst = self.instances[t.key];
        return {
          key: t.key,
          pinned: !!t.pinned,
          dirty: !!(inst && inst.isDirty && inst.isDirty()),
          lastUsed: t.lastUsed || 0,
          opened: t.opened || 0,
        };
      });
      var victim = MC.tabs.victim(snapshot, activeKey);
      if (!victim) {
        MC.toast.warning("已打开的页面过多", "其余页面都有未保存的修改，请先处理后再关闭");
        break;
      }
      var tab = this.tabs.filter(function (t) { return t.key === victim; })[0];
      this._removeTab(victim, true);
      MC.toast.info("已自动关闭最久未用的页面", (tab && (tab.title || tab.key)) || victim);
    }
  };

  App.prototype.activate = function (key, r) {
    var self = this;
    var tab = this.tabs.filter(function (t) { return t.key === key; })[0];
    tab.lastUsed = Date.now();
    Object.keys(this.instances).forEach(function (k) {
      if (k !== key) self.instances[k].el.classList.add("hidden");
    });
    var inst = this.instances[key];
    if (!inst) {
      var factory = MC.views[r.kind];
      if (!factory) return;
      var placeholder = this.viewHost.querySelector(":scope > .state");
      if (placeholder) placeholder.remove();
      inst = factory(this, r);
      inst.el.classList.add("view");
      this.instances[key] = inst;
      this.viewHost.appendChild(inst.el);
    } else {
      if (!inst.el.parentNode) this.viewHost.appendChild(inst.el);
      inst.el.classList.remove("hidden");
      if (inst.update) inst.update(r);
    }
    if (inst.onShow) inst.onShow();
    tab.title = inst.title || tab.title;
    tab.icon = inst.icon;
    this.activeKey = key;
    this.saveTabs();
    this.renderTabbar();
    this.renderNav();
    this.renderFileList();
    document.title = (tab.title || "MCChecker") + " · " + this.session.profile.name + " · MCChecker";
  };

  App.prototype.setTabTitle = function (inst, title) {
    var self = this;
    Object.keys(this.instances).forEach(function (k) {
      if (self.instances[k] === inst) {
        self.tabs.forEach(function (t) { if (t.key === k) t.title = title; });
      }
    });
    this.renderTabbar();
  };

  App.prototype.destroyInstance = function (key) {
    var inst = this.instances[key];
    if (!inst) return;
    if (inst.destroy) inst.destroy();
    if (inst.el.parentNode) inst.el.parentNode.removeChild(inst.el);
    delete this.instances[key];
  };

  App.prototype._removeTab = function (key, silent) {
    var idx = this.tabs.findIndex(function (t) { return t.key === key; });
    if (idx < 0 || this.tabs[idx].pinned) return false;
    this.destroyInstance(key);
    this.tabs.splice(idx, 1);
    this.saveTabs();
    if (this.activeKey === key && !silent) {
      var next = this.tabs[Math.min(idx, this.tabs.length - 1)];
      this.go(next.route);
    } else this.renderTabbar();
    return true;
  };

  App.prototype.closeTab = function (key, silent) {
    var self = this;
    var idx = this.tabs.findIndex(function (t) { return t.key === key; });
    if (idx < 0 || this.tabs[idx].pinned) return Promise.resolve(false);
    var inst = this.instances[key];
    var needsConfirm = !silent && inst && inst.isDirty && inst.isDirty();
    if (!needsConfirm) return Promise.resolve(this._removeTab(key, silent));
    return MC.confirm({ title: "关闭页面", message: "该页面有未保存的修改，关闭后将丢失。", confirmText: "关闭并放弃修改", danger: true }).then(function (ok) {
      if (!ok) return false;
      return self._removeTab(key, silent);
    });
  };

  App.prototype.renderTabbar = function () {
    var self = this;
    if (!this.session) return;
    var scroll = h("div.tabbar-scroll");
    this.tabs.forEach(function (t) {
      var inst = self.instances[t.key];
      var dirty = inst && inst.isDirty && inst.isDirty();
      var title = t.title || defaultTitle(t);
      var el = h(
        "div.tab" + (t.key === self.activeKey ? ".is-active" : ""),
        { role: "tab", tabindex: "0", "aria-selected": String(t.key === self.activeKey), title: title },
        icon(t.icon || defaultIcon(t.route.kind)),
        h("span.tab-label", title),
        dirty ? h("span.tab-dirty", { "aria-label": "有未保存的修改" }) : null,
        t.pinned
          ? null
          : h("button.tab-close", { type: "button", "aria-label": "关闭 " + title, onclick: function (e) { e.stopPropagation(); self.closeTab(t.key); } }, icon("x"))
      );
      el.addEventListener("click", function () { self.go(t.route); });
      el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); self.go(t.route); } });
      el.addEventListener("auxclick", function (e) { if (e.button === 1) self.closeTab(t.key); });
      scroll.appendChild(el);
    });
    var moreBtn = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "标签操作", style: "margin:0 0 4px auto;flex:none" }, icon("more"));
    moreBtn.addEventListener("click", function () {
      function closeMany(keys, goOverview) {
        var dirty = keys.some(function (k) {
          var inst = self.instances[k];
          return inst && inst.isDirty && inst.isDirty();
        });
        var proceed = dirty
          ? MC.confirm({ title: "关闭页面", message: "其中有未保存的修改，关闭后将丢失。", confirmText: "关闭并放弃修改", danger: true })
          : Promise.resolve(true);
        proceed.then(function (ok) {
          if (!ok) return;
          keys.forEach(function (k) { self._removeTab(k, true); });
          if (goOverview) self.go({ kind: "overview" });
        });
      }
      MC.menu(moreBtn, [
        { label: "关闭其他页面", onClick: function () { closeMany(self.tabs.filter(function (t) { return t.key !== self.activeKey; }).map(function (t) { return t.key; }), false); } },
        { label: "关闭全部页面", onClick: function () { closeMany(self.tabs.map(function (t) { return t.key; }), true); } },
      ], { align: "end" });
    });
    util.replace(this.tabbar, scroll, moreBtn);
    var act = scroll.querySelector(".is-active");
    if (act && act.scrollIntoView) act.scrollIntoView({ block: "nearest", inline: "nearest" });
  };

  function defaultTitle(t) {
    var k = t.route.kind;
    if (k === "f") return t.route.arg;
    return { overview: "速览", search: "全局搜索", review: "审阅修改", dl: "DL 计算", tools: "外部工具", settings: "设置", t: "临时文件" }[k] || k;
  }

  function defaultIcon(kind) {
    return { overview: "overview", search: "search", review: "review", dl: "calc", tools: "tool", settings: "settings", f: "file", t: "file-temp" }[kind] || "file";
  }

  /* ===================== polling (multi-user) ===================== */
  App.prototype.startPolling = function () {
    var self = this;
    function tick() {
      if (document.hidden) return;
      api.get(self.p("/revision")).then(function (r) {
        if (self.rev) {
          ["files", "favorites", "remarks", "settings"].forEach(function (part) {
            if (self.rev.parts[part] !== r.parts[part]) MC.bus.emit("changed:" + part, r);
          });
          if (self.rev.parts.files !== r.parts.files || self.rev.parts.remarks !== r.parts.remarks) self.refreshFiles();
          if (self.rev.parts.settings !== r.parts.settings) self.refreshTools();
          if (self.rev.parts.tasks !== r.parts.tasks) self.pollTasks();
        }
        self.rev = r;
      }, function () {});
    }
    tick();
    setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
  };

  /** 立即同步一次修订号（本地写操作后调用，避免把自己的修改当成“他人更新”）。 */
  App.prototype.syncRevision = function () {
    var self = this;
    return api.get(this.p("/revision")).then(function (r) { self.rev = r; }, function () {});
  };

  App.prototype.pollTasks = function () {
    var self = this;
    clearTimeout(this._taskTimer);
    return api.get(this.p("/tasks"), { active: "1" }).then(function (res) {
      var before = self.activeTasks.map(function (t) { return t.id; });
      self.activeTasks = res.items;
      self.renderTasks();
      var still = res.items.map(function (t) { return t.id; });
      before.forEach(function (id) {
        if (still.indexOf(id) < 0) {
          api.get("/tasks/" + api.seg(id)).then(
            function (r) {
              MC.bus.emit("task:done", r.task);
              self.refreshFiles();
            },
            /* 卡 7 第三轮 C2：这条链原先只有成功分支 ⇒ refetch 一失败就留下无人接手的
             * 拒绝。file:// 验收命令看不见它，钉死成 HTTP 以后它就是随机红。竞态本身正常：
             * 任务在这次 poll 与 refetch 之间被惰性清理摘掉 ⇒ 404 NOT_FOUND（与 showTask
             * 的「任务已结束出窗口」同一口径）。诚实的最小动作：
             * ① **不**伪造 task:done——两个消费方按 t.type / t.target 过滤（fileview
             *    的 records 刷新、settings 的 sources 重画），伪造会让别的界面拿假数据刷新；
             * ② 记一条 console.error 留诊断（同 [boot] / [bus] 的既有做法）；
             * ③ 404 静默接受，其余错误给部署者看得见的 toast。
             * 「这条已不在进行中」是真状态且已经画出来了（上面刚替换 activeTasks 并
             * renderTasks），业务结果面另有修订号轮询兜底，所以这里不补刷新。 */
            function (err) {
              console.error("[task-refetch]", id, err);
              if (!(err && err.status === 404)) MC.toastError("任务结果读取失败", err);
            }
          );
        }
      });
      if (res.items.length) self._taskTimer = setTimeout(function () { self.pollTasks(); }, 1200);
    });
  };

  App.prototype.startTask = function (body) {
    var self = this;
    return api.post(this.p("/tasks"), body).then(function (res) {
      self.activeTasks = self.activeTasks.concat([res.task]);
      self.renderTasks();
      self.pollTasks();
      return res.task;
    });
  };

  /**
   * 409 BUSY 的跳转落点（卡 6 口径：`detail.task_id` 指向那条正在跑的任务）。
   * 弹窗只讲「任务本身」：类型 / state / 进度 / 发起人；条目业务结果仍看
   * 更新源列表的「最近结果」列（§7.10 的两套词汇不混用）。
   * 任务可能在这一步已结束（GET /tasks/{id} 404）：给业务文案，不谎报服务器故障。
   */
  App.prototype.showTask = function (taskId) {
    return api.get("/tasks/" + api.seg(taskId)).then(
      function (r) {
        var t = r.task;
        MC.modal({
          title: "任务详情",
          subtitle: "409 撞车时指回正在运行的那条任务（detail.task_id）",
          body: h(
            "div.col",
            { style: "gap:10px" },
            h(
              "dl.kv",
              h("dt", "任务"),
              h("dd", taskLabel(t)),
              h("dt", "状态"),
              h("dd", (TASK_STATE_LABELS[t.state] || t.state) + "（进度 " + taskProgressText(t) + "）"),
              h("dt", "发起人"),
              h("dd", t.actor || "—"),
              h("dt", "提交时间"),
              h("dd", util.formatTime(t.created_at))
            ),
            h("div.xsmall.muted", "各项更新的结果见「设置 › 更新源」的最近结果列。任务结束后列表将自动刷新。")
          ),
          footer: function (close) {
            return [h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭")];
          },
        });
      },
      function (err) {
        /* 点「查看该任务」时那条任务可能已结束出窗口（GET 404）：业务文案，不是故障。 */
        MC.toastError("看不到这条任务", err);
      }
    );
  };

  /**
   * 更新类任务启动失败的共享口径（卡 7 复审 F1：从 settings 闭包提到与 showTask 同层）。
   * 设置页「立即更新/全部更新」与文件页「立即拉取」撞的是同一条互斥规则（三类任务
   * 同机型两两互斥），文案必须同源，否则两个界面对同一种 409 各说各话。
   * - 409 BUSY 是正常业务结果不是服务器故障：warning + 解释互斥 + 按 detail.task_id
   *   跳去那条正在跑的任务；**不重试**（重试只会再撞一次 409）。
   * - 404＝名字已不在映射表（多半刚被他人删除）：warning 显示后端业务消息，
   *   opts.onNotFound 给调用方刷新自己列表的钩子。
   * opts.notFoundTitle / opts.faultTitle 供各界面用准确动词（「更新」/「拉取」）。
   */
  MC.taskStartError = function (app, err, opts) {
    opts = opts || {};
    if (err && err.status === 409 && err.code === "BUSY") {
      var taskId = err.detail && err.detail.task_id;
      MC.toast.warning(
        "已有任务在运行",
        MC.errorText(err) + "。同一机型内「单条更新 / 全量更新 / 拉取修改记录」互斥，" + (taskId ? "点这里查看那条正在运行的任务。" : "请等它结束后再试。"),
        taskId ? { action: { label: "查看该任务", onClick: function () { app.showTask(taskId); } } } : null
      );
      return;
    }
    if (err && err.status === 404) {
      MC.toast.warning(opts.notFoundTitle || "无法开始更新", MC.errorText(err));
      if (opts.onNotFound) opts.onNotFound();
      return;
    }
    MC.toastError(opts.faultTitle || "无法开始更新", err);
  };

  App.prototype.renderTasks = function () {
    if (!this.taskSlot) return;
    util.clear(this.taskSlot);
    var running = this.activeTasks.filter(function (t) { return t.state === "running" || t.state === "queued"; });
    if (!running.length) return;
    var t = running[0];
    var self = this;
    var btn = h(
      "button.task-indicator",
      { type: "button", "aria-live": "polite" },
      h("span.spinner"),
      taskLabel(t) + " " + taskProgressText(t),
      running.length > 1 ? " 等 " + running.length + " 项" : null
    );
    btn.addEventListener("click", function () {
      MC.popover(
        btn,
        h(
          "div",
          { style: "padding:8px;width:280px" },
          h("div.section-title", "进行中的任务"),
          running.map(function (x) {
            /* 第三轮 nit（注释与 markup 矛盾，选「markup 追上注释」而不是删注释）：
             * F7 那句原本声称「total 未发布时不画 0% 空条」，可 `.progress` 是无条件画的，
             * pct=0 时照样留一条灰色空槽。现在 total 未发布⇒整条不画（util.h 跳过 null
             * 子节点），与 taskProgressText 在同一条件下改说「准备中」保持一致。
             * hasProgress 就是原来那句条件判断，只是提出来复用一次。 */
            var hasProgress = !!x.progress.total;
            var pct = hasProgress ? Math.round((x.progress.done / x.progress.total) * 100) : 0;
            /* F7（卡 7 复审）：弹窗与浮层同一套词汇——taskLabel/TASK_STATE_LABELS/
             * taskProgressText，不再裸打 type token，也不在 total 未发布时画 0% 空条。 */
            return h("div.col", { style: "gap:4px;margin-bottom:10px" }, h("div.row.small", h("span.grow.truncate", taskLabel(x)), h("span.muted.xsmall", x.actor)), h("div.row.xsmall", h("span.grow.muted", (TASK_STATE_LABELS[x.state] || x.state) + " · " + taskProgressText(x))), hasProgress ? h("div.progress", h("span", { style: { width: pct + "%" } })) : null);
          }),
          h("button.btn.btn-sm", { type: "button", onclick: function () { MC.closePopover(); self.go({ kind: "settings", arg: "sources" }); } }, "查看更新源状态")
        ),
        { align: "end" }
      );
    });
    this.taskSlot.appendChild(btn);
  };

  /* ===================== command palette ===================== */
  App.prototype.openPalette = function () {
    var self = this;
    var input = h("input.input.palette-input", { type: "text", placeholder: "输入文件名跳转，或输入关键词在全部文件中搜索", "aria-label": "搜索", autofocus: true, autocomplete: "off" });
    var list = h("div.palette-list", { role: "listbox" });
    var sel = 0;
    var items = [];
    var m;

    function build() {
      var q = input.value.trim();
      var ql = q.toLowerCase();
      items = [];
      if (q) items.push({ icon: "search", title: "在全部文件中搜索 “" + q + "”", sub: "匹配变量名、值与收藏备注", run: function () { self.go({ kind: "search", query: { q: q } }); } });
      self.files
        .filter(function (f) { return !ql || f.name.toLowerCase().indexOf(ql) >= 0; })
        .slice(0, 8)
        .forEach(function (f) {
          items.push({ icon: "file", title: f.name, sub: "打开文件 · 更新于 " + util.formatTime(f.updated_at), run: function () { self.openFile(f.name); } });
        });
      NAV.forEach(function (n) {
        if (n.admin && !self.can("admin")) return;
        if (ql && n.label.toLowerCase().indexOf(ql) < 0) return;
        items.push({ icon: n.icon, title: n.label, sub: "前往", run: function () { self.go({ kind: n.kind }); } });
      });
      sel = Math.min(sel, Math.max(0, items.length - 1));
      util.clear(list);
      if (!items.length) list.appendChild(h("div.state", h("div.state-desc", "没有匹配的结果")));
      items.forEach(function (it, i) {
        var b = h(
          "button.palette-item",
          { type: "button", role: "option", "aria-selected": String(i === sel) },
          icon(it.icon),
          h("div.grow", h("div.truncate", util.highlight(it.title, q)), h("div.p-sub", it.sub))
        );
        b.addEventListener("click", function () { m.close(); it.run(); });
        b.addEventListener("mousemove", function () { if (sel !== i) { sel = i; build(); } });
        list.appendChild(b);
      });
    }
    input.addEventListener("input", function () { sel = 0; build(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); build(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); build(); e.preventDefault(); }
      else if (e.key === "Enter" && items[sel]) { e.preventDefault(); m.close(); items[sel].run(); }
    });
    var body = h("div", h("div.input-wrap", icon("search"), input), list, h("div.palette-foot", h("span", h("kbd", "↑"), h("kbd", "↓"), " 选择"), h("span", h("kbd", "Enter"), " 打开"), h("span", h("kbd", "Esc"), " 关闭")));
    m = MC.modal({ title: "搜索与跳转", body: body });
    m.el.classList.add("palette");
    m.el.querySelector(".modal-header").classList.add("sr-only");
    m.el.querySelector(".modal-body").style.padding = "0";
    build();
  };

  MC.App = App;
})(window);
