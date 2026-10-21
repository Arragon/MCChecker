/*
 * 设置：更新源 / 定时更新 / 人员与权限 / 变量绑定 / 工具菜单 / 机型管理。
 * 写操作仅部署者可用；其他身份可查看，页面顶部说明原因（真实权限由服务端校验）。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;
  var api = MC.api;

  var SECTIONS = [
    { id: "sources", label: "更新源", icon: "link", desc: "为每个配置文件维护下载链接与修改记录链接，支持单条或全量立即更新。" },
    { id: "schedule", label: "定时更新", icon: "clock", desc: "按固定间隔自动执行全量更新（含修改记录拉取）。" },
    { id: "people", label: "人员与权限", icon: "users", desc: "IP 对应表与管理员名单全机型共用。IP 对应表用于识别建议提交人；管理员可审阅修改建议、删除文件。" },
    { id: "bindings", label: "变量绑定", icon: "link", desc: "把不同文件中含义相同的变量绑定为一组，对比时会以紫色标出。" },
    { id: "tools", label: "工具菜单", icon: "tool", desc: "维护「外部工具」页面中的常用链接。点击后在新窗口打开。" },
    { id: "profiles", label: "机型管理", icon: "device", desc: "每个机型拥有独立的文件、历史、收藏、绑定与设置。" },
  ];

  function fieldError(fields, err) {
    var f = err.detail && err.detail.field;
    if (f && fields[f]) fields[f].setError(MC.errorText(err));
    else MC.toastError("保存失败", err);
  }

  MC.views.settings = function (app, route) {
    var S = { section: route.arg || "sources" };
    var nav = h("nav.settings-nav", { "aria-label": "设置分类" });
    var main = h("div.settings-main");
    var el = h("div", h("div.view-body.is-flush", h("div.settings-layout", nav, main)));
    var unsub = [MC.bus.on("changed:settings", function () { renderSection(true); }), MC.bus.on("task:done", function () { if (S.section === "sources") renderSection(true); })];

    function renderNav() {
      util.clear(nav);
      nav.append(h("div.section-title", { style: "padding:0 8px" }, "设置"));
      SECTIONS.forEach(function (s) {
        var b = h("button.nav-item", { type: "button", "aria-current": S.section === s.id ? "page" : null }, icon(s.icon), s.label);
        b.addEventListener("click", function () { app.go({ kind: "settings", arg: s.id }); });
        nav.append(b);
      });
    }

    var RO_TITLE = "只有管理员可以修改";

    function pageHead(sec, actions) {
      var locked = !app.can("deployer");
      if (actions && locked) {
        actions.querySelectorAll("button").forEach(function (b) {
          b.disabled = true;
          if (!b.title) b.title = RO_TITLE;
        });
      }
      return [
        h("div.page-head", h("div.col", h("h1", sec.label), h("div.small.muted", sec.desc)), actions || null),
        locked ? h("div.banner.banner-neutral", { style: "margin-bottom:16px" }, icon("lock"), h("div.banner-body", "只读：当前身份为「" + app.identity.role_label + "」，只有管理员可以修改这些设置。")) : null,
      ];
    }

    function renderSection(silent) {
      renderNav();
      var sec = SECTIONS.filter(function (s) { return s.id === S.section; })[0] || SECTIONS[0];
      if (!silent) util.replace(main, MC.stateBlock({ kind: "loading" }));
      var fn = { sources: sources, schedule: schedule, people: people, bindings: bindings, tools: tools, profiles: profiles }[sec.id];
      fn(sec).then(function (content) { util.replace(main, h("div.container", content)); }, function (err) { util.replace(main, MC.errorBlock(err, function () { renderSection(); })); });
    }

    var ro = function () { return !app.can("deployer"); };

    /* 更新任务的失败口径走共享面 MC.taskStartError（shell.js，卡 7 复审 F1 从本闭包提升：
     * 文件页「立即拉取」撞同一条互斥规则，文案必须同源，不能再各写一份）。
     * 本视图唯一的差异是 404（名字已不在映射表）后要刷新自己的列表。 */
    function taskStartError(err) {
      MC.taskStartError(app, err, { onNotFound: function () { renderSection(true); } });
    }

    /* ---------- 更新源 ---------- */
    function sources(sec) {
      return api.get(app.p("/sources")).then(function (res) {
        var running = app.activeTasks.some(function (t) { return t.type === "update_all" && t.state === "running"; });
        var all = h(
          "button.btn.btn-primary",
          { type: "button", disabled: running || !res.items.length },
          icon("download"),
          running ? "全量更新进行中…" : "全部立即更新"
        );
        all.addEventListener("click", function () {
          MC.withBusy(all, function () {
            return app.startTask({ type: "update_all" }).then(function () { MC.toast.info("已开始全量更新", "顶部会显示进度，可继续其他操作"); }, function (err) { taskStartError(err); });
          });
        });
        var add = h("button.btn", { type: "button", onclick: function () { sourceDialog(null); } }, icon("plus"), "新增更新源");
        var rows = res.items.map(function (it) {
          var st = it.last_result;
          var dot = !st ? "" : st.status === "success" ? ".dot-success" : st.status === "unchanged" ? ".dot-primary" : st.status === "skipped" ? "" : ".dot-danger";
          var upd = h(
            "button.btn.btn-sm",
            {
              type: "button",
              disabled: ro(),
              title: ro() ? "只有管理员可以修改" : null,
            },
            icon("refresh"),
            "更新"
          );
          upd.addEventListener("click", function () {
            MC.withBusy(upd, function () {
              return app.startTask({ type: "update_source", name: it.name }).then(function () { MC.toast.info("已开始更新 " + it.name); }, function (err) { taskStartError(err); });
            });
          });
          var more = h("button.btn.btn-ghost.btn-icon.btn-sm", { type: "button", "aria-label": "更多操作", disabled: ro(), title: ro() ? "只有管理员可以修改" : null }, icon("more"));
          more.addEventListener("click", function () {
            MC.menu(more, [
              { label: "编辑", icon: "edit", onClick: function () { sourceDialog(it); } },
              { label: "打开文件", icon: "file", onClick: function () { app.openFile(it.name); } },
              "sep",
              { label: "删除更新源…", icon: "trash", danger: true, onClick: function () { removeSource(it); } },
            ], { align: "end" });
          });
          return h(
            "tr",
            h("td", h("div.cell-strong.mono", it.name), it.keyword ? h("div.xsmall.muted", "关键词 " + it.keyword) : null, it.local_updated_at ? h("div.xsmall.muted", "本地版本 " + util.formatTime(it.local_updated_at)) : h("div.xsmall", { style: "color:var(--c-warning-text)" }, "尚未下载")),
            h("td", h("div.mono.xsmall.break-all", it.url), it.record_url ? h("div.xsmall.muted.break-all", "修改记录：" + it.record_url) : h("div.xsmall.muted", "未配置修改记录链接")),
            h("td", st ? h("div.row", { style: "gap:6px" }, h("span.dot" + dot), h("span", st.label)) : h("span.muted", "—"), st ? h("div.xsmall.muted", util.formatTime(st.at) + (st.reason ? " · " + st.reason : "")) : null),
            h("td.cell-actions", upd, more)
          );
        });
        return [
          pageHead(sec, h("div.btn-group", add, all)),
          h(
            "div.panel",
            res.items.length
              ? h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "文件"), h("th", "下载链接"), h("th", "最近结果"), h("th.cell-actions", "操作"))), h("tbody", rows)))
              : MC.stateBlock({ kind: "empty", icon: "link", title: "还没有更新源", desc: "添加后可一键从链接拉取最新配置，旧版本自动归档。", actions: ro() ? null : [h("button.btn.btn-primary", { type: "button", onclick: function () { sourceDialog(null); } }, "新增更新源")] })
          ),
          h("p.xsmall.muted", { style: "margin-top:10px" }, "回读可以是 XML、JSON 或 zip/tar.gz。压缩包里的 XML/JSON 会解压进文件列表；若填写了正则关键词，文件名里匹配到的那段会成为新文件名，例如关键词 MachineConstant 会把 Default_MachineConstant_basics.xml 保存为 MachineConstant.xml。校验通过才替换当前版本；内容未变化不会产生新的历史版本。"),
        ];
      });
    }

    function sourceDialog(it) {
      var name = h("input.input.input-mono", { type: "text", value: it ? it.name : "", placeholder: "config.xml" });
      var url = h("input.input.input-mono", { type: "url", value: it ? it.url : "", placeholder: "http://…" });
      var rec = h("input.input.input-mono", { type: "url", value: it ? it.record_url : "", placeholder: "可选" });
      var keyword = h("input.input.input-mono", { type: "text", value: it && it.keyword ? it.keyword : "", placeholder: "例如 MachineConstant" });
      var migrate = h("input", { type: "checkbox", checked: true });
      var F = {
        name: MC.field({ label: "文件名", required: true, control: name }),
        url: MC.field({ label: "下载链接", required: true, control: url }),
        record_url: MC.field({ label: "修改记录链接", control: rec, help: "用于拉取上游的修改记录文件" }),
        keyword: MC.field({ label: "正则关键词", control: keyword, help: "可选。匹配文件名中的一段，保存为「匹配文字 + 扩展名」。留空则单个文件按上面的文件名保存，压缩包按包内原名加入。" }),
      };
      var migrateRow = it ? h("label.check", migrate, "改名时同时迁移本地文件、历史版本、收藏与建议") : null;
      MC.modal({
        title: it ? "编辑更新源" : "新增更新源",
        size: "wide",
        body: h("div.col", { style: "gap:12px" }, F.name, F.url, F.record_url, F.keyword, migrateRow),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "保存");
          save.addEventListener("click", function () {
            Object.keys(F).forEach(function (k) { F[k].setError(""); });
            var body = { name: name.value.trim(), url: url.value.trim(), record_url: rec.value.trim(), keyword: keyword.value.trim(), migrate_files: migrate.checked };
            MC.withBusy(save, function () {
              var p = it ? api.patch(app.p("/sources/" + api.seg(it.name)), body) : api.post(app.p("/sources"), body);
              return p.then(function () { close(); MC.toast.success("已保存"); app.syncRevision(); app.refreshFiles(); renderSection(true); }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function removeSource(it) {
      MC.confirm({ title: "删除更新源 " + it.name, message: "只删除下载链接配置，不会删除本地文件和历史版本。之后该文件将不再参与定时更新。", confirmText: "删除", danger: true }).then(function (ok) {
        if (!ok) return;
        api.del(app.p("/sources/" + api.seg(it.name))).then(function () { MC.toast.success("已删除更新源"); app.syncRevision(); app.refreshFiles(); renderSection(true); }, function (err) { MC.toastError("删除失败", err); });
      });
    }

    /* ---------- 定时更新 ---------- */
    function schedule(sec) {
      return api.get(app.p("/schedule")).then(function (s) {
        var days = s.interval_hours % 24 === 0 && s.interval_hours >= 24;
        var amount = h("input.input", { type: "number", min: "1", step: "1", value: days ? s.interval_hours / 24 : s.interval_hours, style: "width:120px", disabled: ro(), title: ro() ? "只有管理员可以修改" : null });
        var unit = h("select.select", { style: "width:100px", disabled: ro(), title: ro() ? "只有管理员可以修改" : null }, h("option", { value: "h", selected: !days }, "小时"), h("option", { value: "d", selected: days }, "天"));
        var sw = MC.switchEl("启用定时更新", s.enabled, null, ro());
        if (ro()) sw.title = "只有管理员可以修改";
        var amountField = MC.field({ label: "执行间隔", control: h("div.row", amount, unit) });
        var save = h("button.btn.btn-primary", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null }, "保存");
        save.addEventListener("click", function () {
          amountField.setError("");
          var n = Number(amount.value);
          if (!isFinite(n) || n < 1 || Math.floor(n) !== n) return amountField.setError("请输入正整数");
          var hours = unit.value === "d" ? n * 24 : n;
          MC.withBusy(save, function () {
            return api.put(app.p("/schedule"), { enabled: sw.input.checked, interval_hours: hours }).then(function () { MC.toast.success("定时设置已保存", sw.input.checked ? "每 " + n + (unit.value === "d" ? " 天" : " 小时") + "执行一次" : "已停用"); app.syncRevision(); renderSection(true); }, function (err) { fieldError({ interval: amountField }, err); });
          });
        });
        return [
          pageHead(sec),
          h(
            "div.panel",
            h("div.panel-body.col", { style: "gap:16px;max-width:520px" }, sw, amountField, h("div.field-help", "保存后立即按新间隔重新计划。间隔以小时存储（“天”会换算为 24 小时的整数倍），与旧版配置文件兼容。")),
            h("div.panel-footer", save)
          ),
          h(
            "div.panel",
            { style: "margin-top:16px" },
            h("div.panel-header", h("h3", "运行状态")),
            h(
              "div.panel-body",
              h(
                "dl.kv",
                h("dt", "状态"),
                h("dd", s.enabled ? "已启用" : "未启用"),
                h("dt", "下次执行"),
                h("dd", s.enabled && s.next_run_at ? util.formatTime(s.next_run_at) : "—"),
                h("dt", "上次执行"),
                h("dd", s.last_run ? util.formatTime(s.last_run.at) + "：" + s.last_run.success + " 个更新，" + s.last_run.unchanged + " 个无变化，" + s.last_run.failed + " 个失败" : "尚未执行")
              )
            )
          ),
        ];
      });
    }

    /* ---------- 人员与权限 ---------- */
    function people(sec) {
      return Promise.all([api.get(app.p("/ip-mapping")), api.get(app.p("/admins"))]).then(function (res) {
        var ppl = res[0].items;
        var admins = res[1].items;
        var adminSet = {};
        admins.forEach(function (a) {
          if (a.ip) adminSet[a.ip] = 1;
        });
        ppl.forEach(function (p) {
          if (!adminSet[p.ip] && admins.some(function (a) { return !a.ip && a.name && a.name === p.name; })) adminSet[p.ip] = 1;
        });
        var addP = h("button.btn.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { personDialog(null); } }, icon("plus"), "登记人员");
        var addA = h("button.btn.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { adminDialog(); } }, icon("plus"), "添加管理员");
        var me = app.identity;
        return [
          pageHead(sec),
          h(
            "div.panel",
            h("div.panel-header", h("h3", "IP 对应表"), h("span.small.muted", ppl.length + " 人"), addP),
            ppl.length
              ? h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "IP 地址"), h("th", "姓名"), h("th", "角色"), h("th", "更新时间"), h("th.cell-actions", ""))), h("tbody", ppl.map(function (p) {
                  return h(
                    "tr",
                    h("td.mono", p.ip, p.ip === me.ip ? h("span.badge.badge-primary", { style: "margin-left:6px" }, "你") : null),
                    h("td.cell-strong", p.name),
                    h("td", adminSet[p.ip] ? h("span.badge.badge-primary", "管理员") : h("span.muted", "成员")),
                    h("td.muted.num", util.formatTime(p.updated_at)),
                    h("td.cell-actions", h("button.btn.btn-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { personDialog(p); } }, "编辑"), h("button.btn.btn-danger-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { removePerson(p); } }, "删除"))
                  );
                }))))
              : MC.stateBlock({ kind: "empty", icon: "users", title: "尚未登记人员", desc: "未登记的访问者在建议中显示为其 IP 地址。" })
          ),
          h(
            "div.panel",
            { style: "margin-top:16px" },
            h("div.panel-header", h("h3", "管理员列表"), h("span.small.muted", "按 IP 授权，姓名只是助记"), addA),
            admins.length
              ? h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "IP 地址"), h("th", "助记名"), h("th", "更新时间"), h("th.cell-actions", ""))), h("tbody", admins.map(function (a) {
                  var label = a.name || (ppl.filter(function (p) { return p.ip === a.ip; })[0] || {}).name || "—";
                  return h("tr", h("td.mono", a.ip || "—"), h("td.cell-strong", label), h("td.muted.num", util.formatTime(a.updated_at)), h("td.cell-actions", h("button.btn.btn-danger-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { removeAdmin(a); } }, "移除")));
                }))))
              : MC.stateBlock({ kind: "empty", title: "暂无管理员" })
          ),
          /* §11.1 的硬要求：这句「IP 识别不是强认证」必须出现在界面上，不许只写在文档里。
           * M3-13 之前这一段是「未接入分段」的只读横幅，等于没人看得见它。 */
          h("p.xsmall.muted", { style: "margin-top:10px" }, "权限按 IP 识别，姓名只是助记，不是强认证：适用于可信内网。共用电脑或不可信网络下，任何使用该地址的人都会被认成同一个人（包括管理员），需要另行评估；经反向代理部署还必须配置受信代理，否则所有请求都会被识别为代理地址。"),
        ];
      });
    }

    function personDialog(p) {
      var ip = h("input.input.input-mono", { type: "text", value: p ? p.ip : "", placeholder: "10.12.3.21" });
      var name = h("input.input", { type: "text", value: p ? p.name : "", placeholder: "姓名" });
      var F = { ip: MC.field({ label: "IP 地址", required: true, control: ip }), name: MC.field({ label: "姓名", required: true, control: name }) };
      MC.modal({
        title: p ? "编辑人员" : "登记人员",
        body: h("div.col", { style: "gap:12px" }, F.ip, F.name),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "保存");
          save.addEventListener("click", function () {
            F.ip.setError("");
            F.name.setError("");
            MC.withBusy(save, function () {
              return api.put(app.p("/ip-mapping/" + api.seg(p ? p.ip : ip.value.trim())), { ip: ip.value.trim(), name: name.value.trim() }).then(function () { close(); MC.toast.success("已保存"); app.syncRevision(); afterIdentityChange(); }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function adminDialog() {
      var ip = h("input.input.input-mono", { type: "text", placeholder: "10.12.3.22" });
      var name = h("input.input", { type: "text", placeholder: "可空，只用于显示" });
      var F = {
        ip: MC.field({ label: "IP 地址", required: true, control: ip }),
        name: MC.field({ label: "助记名", control: name }),
      };
      MC.modal({
        title: "添加管理员",
        body: h("div.col", { style: "gap:12px" }, F.ip, F.name),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "添加");
          save.addEventListener("click", function () {
            F.ip.setError("");
            F.name.setError("");
            MC.withBusy(save, function () {
              return api.post(app.p("/admins"), { ip: ip.value.trim(), name: name.value.trim() }).then(function () { close(); MC.toast.success("已添加管理员"); app.syncRevision(); afterIdentityChange(); }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function removePerson(p) {
      MC.confirm({ title: "删除 " + p.name + "（" + p.ip + "）", message: "删除后该 IP 提交的新建议将只显示 IP。这只去掉助记名，不取消管理员权限。", confirmText: "删除", danger: true }).then(function (ok) {
        if (ok) api.del(app.p("/ip-mapping/" + api.seg(p.ip))).then(function () { MC.toast.success("已删除"); app.syncRevision(); afterIdentityChange(); }, function (err) { MC.toastError("删除失败", err); });
      });
    }

    function removeAdmin(a) {
      MC.confirm({ title: "移除管理员 " + (a.ip || a.name), message: "移除后该 IP 将失去管理员权限，正在进行的审阅提交也会被拒绝。", confirmText: "移除", danger: true }).then(function (ok) {
        if (ok) api.del(app.p("/admins/" + api.seg(a.ip || a.name))).then(function () { MC.toast.success("已移除"); app.syncRevision(); afterIdentityChange(); }, function (err) { MC.toastError("移除失败", err); });
      });
    }

    function afterIdentityChange() {
      api.get("/session", { profile: app.pid }).then(function (s) {
        app.session = s;
        app.renderHeader();
        app.renderNav();
        renderSection(true);
      });
    }

    /* ---------- 变量绑定 ---------- */
    function bindings(sec) {
      return api.get(app.p("/bindings")).then(function (res) {
        var add = h("button.btn", { type: "button", onclick: function () { bindingDialog(null); } }, icon("plus"), "新增绑定组");
        return [
          pageHead(sec, add),
          h(
            "div.panel",
            res.items.length
              ? h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "绑定组"), h("th", "变量"), h("th.cell-actions", ""))), h("tbody", res.items.map(function (b) {
                  return h(
                    "tr",
                    h("td", h("span.badge.badge-bind", b.group_name)),
                    h("td", h("ul.binding-vars", b.variables.map(function (v) { return h("li", util.fmtTag(v.file), h("span.mono", v.path)); }))),
                    h("td.cell-actions", h("button.btn.btn-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { bindingDialog(b); } }, "编辑"), h("button.btn.btn-danger-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { removeBinding(b); } }, "删除"))
                  );
                }))))
              : MC.stateBlock({ kind: "empty", icon: "link", title: "暂无绑定组", desc: "例如把 A 文件的 timeout 与 B 文件的 time_out 绑定，对比时会同时标出。" })
          ),
          h("p.xsmall.muted", { style: "margin-top:10px" }, "变量路径格式为「文件名/节点/节点…」，可在文件详情面板中点击「复制绑定路径」获得。"),
        ];
      });
    }

    function bindingDialog(b) {
      var name = h("input.input", { type: "text", value: b ? b.group_name : "", placeholder: "例如：超时时间" });
      var rowsHost = h("div.var-rows");
      var vars = b ? JSON.parse(JSON.stringify(b.variables)) : [{ file: (app.files[0] || {}).name || "", path: "" }, { file: (app.files[1] || app.files[0] || {}).name || "", path: "" }];
      function draw() {
        util.clear(rowsHost);
        vars.forEach(function (v, i) {
          var fs = h("select.select", { "aria-label": "文件" }, app.files.map(function (f) { return h("option", { value: f.name, selected: f.name === v.file }, f.name); }));
          fs.addEventListener("change", function () { v.file = fs.value; });
          var ps = h("input.input.input-mono", { type: "text", value: v.path, placeholder: v.file ? v.file + "/ROOT/node" : "文件名/节点/…", "aria-label": "变量路径" });
          ps.addEventListener("input", function () { v.path = ps.value; });
          var rm = h("button.btn.btn-ghost.btn-icon", { type: "button", "aria-label": "移除该变量", disabled: vars.length <= 2 }, icon("x"));
          rm.addEventListener("click", function () { vars.splice(i, 1); draw(); });
          rowsHost.append(h("div.var-row", fs, ps, rm));
        });
      }
      draw();
      var F = { group_name: MC.field({ label: "绑定组名称", required: true, control: name }), variables: MC.field({ label: "变量（至少 2 个）", control: rowsHost }) };
      MC.modal({
        title: b ? "编辑绑定组" : "新增绑定组",
        size: "wide",
        body: h("div.col", { style: "gap:12px" }, F.group_name, F.variables, h("div", h("button.btn.btn-sm", { type: "button", onclick: function () { vars.push({ file: (app.files[0] || {}).name || "", path: "" }); draw(); } }, icon("plus"), "添加变量"))),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "保存");
          save.addEventListener("click", function () {
            F.group_name.setError("");
            F.variables.setError("");
            MC.withBusy(save, function () {
              return api.put(app.p("/bindings/" + api.seg(b ? b.group_name : name.value.trim() || "_")), { group_name: name.value.trim(), variables: vars.map(function (v) { return { file: v.file, path: v.path.trim() }; }) }).then(function () { close(); MC.toast.success("绑定组已保存"); app.syncRevision(); renderSection(true); }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function removeBinding(b) {
      MC.confirm({ title: "删除绑定组 " + b.group_name, message: "删除后对比结果中将不再标注这些变量，已有历史版本的统计会在下次计算时更新。", confirmText: "删除", danger: true }).then(function (ok) {
        if (ok) api.del(app.p("/bindings/" + api.seg(b.group_name))).then(function () { MC.toast.success("已删除"); app.syncRevision(); renderSection(true); }, function (err) { MC.toastError("删除失败", err); });
      });
    }

    /* ---------- 工具菜单 ---------- */
    function tools(sec) {
      return api.get(app.p("/tools")).then(function (res) {
        var add = h("button.btn", { type: "button", onclick: function () { toolDialog(null); } }, icon("plus"), "添加工具");
        return [
          pageHead(sec, add),
          h(
            "div.panel",
            res.items.length
              ? h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "名称"), h("th", "说明"), h("th", "地址"), h("th.cell-actions", ""))), h("tbody", res.items.map(function (t) {
                  return h("tr", h("td.cell-strong", t.name), h("td", t.description || h("span.muted", "—")), h("td.mono.xsmall.break-all", t.url), h("td.cell-actions", h("button.btn.btn-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { toolDialog(t); } }, "编辑"), h("button.btn.btn-danger-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { removeTool(t); } }, "删除")));
                }))))
              : MC.stateBlock({ kind: "empty", icon: "tool", title: "暂无工具" })
          ),
        ];
      });
    }

    function toolDialog(t) {
      var name = h("input.input", { type: "text", value: t ? t.name : "" });
      var desc = h("input.input", { type: "text", value: t ? t.description : "" });
      var url = h("input.input.input-mono", { type: "url", value: t ? t.url : "", placeholder: "http://…" });
      var F = { name: MC.field({ label: "名称", required: true, control: name }), description: MC.field({ label: "说明", control: desc }), url: MC.field({ label: "访问地址", required: true, control: url, help: "仅支持 http / https，将在新窗口打开" }) };
      MC.modal({
        title: t ? "编辑工具" : "添加工具",
        body: h("div.col", { style: "gap:12px" }, F.name, F.description, F.url),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "保存");
          save.addEventListener("click", function () {
            Object.keys(F).forEach(function (k) { F[k].setError(""); });
            MC.withBusy(save, function () {
              return api.put(app.p("/tools/" + api.seg(t ? t.name : name.value.trim() || "_")), { name: name.value.trim(), description: desc.value.trim(), url: url.value.trim() }).then(function () { close(); MC.toast.success("已保存"); app.syncRevision(); app.refreshTools(); renderSection(true); }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function removeTool(t) {
      MC.confirm({ title: "删除工具 " + t.name, confirmText: "删除", danger: true }).then(function (ok) {
        if (ok) api.del(app.p("/tools/" + api.seg(t.name))).then(function () { MC.toast.success("已删除"); app.syncRevision(); app.refreshTools(); renderSection(true); }, function (err) { MC.toastError("删除失败", err); });
      });
    }

    /* ---------- 机型管理 ---------- */
    function profiles(sec) {
      return api.get("/session", { profile: app.pid }).then(function (s) {
        var add = h("button.btn", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: profileDialog }, icon("plus"), "新增机型");
        return [
          pageHead(sec, add),
          h(
            "div.panel",
            h("div.table-wrap", h("table.table", h("thead", h("tr", h("th", "名称"), h("th", "标识"), h("th", ""), h("th.cell-actions", ""))), h("tbody", s.profiles.map(function (m) {
              return h(
                "tr",
                h("td.cell-strong", m.name),
                h("td.mono", m.id),
                h("td", m.id === app.pid ? h("span.badge.badge-primary", "当前") : m.id === "default" ? h("span.badge", "默认") : null),
                h(
                  "td.cell-actions",
                  m.id !== app.pid ? h("button.btn.btn-ghost.btn-sm", { type: "button", onclick: function () { app.switchProfile(m.id); } }, "切换") : null,
                  h("button.btn.btn-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { renameProfile(m); } }, "重命名"),
                  m.id !== "default" ? h("button.btn.btn-danger-ghost.btn-sm", { type: "button", disabled: ro(), title: ro() ? "只有管理员可以修改" : null, onclick: function () { removeProfile(m); } }, "删除") : null
                )
              );
            }))))
          ),
          h("p.xsmall.muted", { style: "margin-top:10px" }, "机型是数据空间而不是权限边界：人员与管理员名单全机型共用一套。"),
        ];
      });
    }

    function profileDialog() {
      var name = h("input.input", { type: "text", placeholder: "例如 DL-200 三号线" });
      var copy = h("select.select", h("option", { value: "" }, "不复制，创建空白机型"), app.session.profiles.map(function (m) { return h("option", { value: m.id }, "复制「" + m.name + "」的配置"); }));
      var F = { name: MC.field({ label: "机型名称", required: true, control: name }), copy: MC.field({ label: "初始数据", control: copy, help: "复制：配置文件与历史版本、更新源、收藏、变量绑定、工具菜单、定时设置、DL 参数。不复制：修改记录、待审建议、IP 对应表、管理员名单、缓存。" }) };
      MC.modal({
        title: "新增机型",
        body: h("div.col", { style: "gap:12px" }, F.name, F.copy),
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "创建");
          save.addEventListener("click", function () {
            F.name.setError("");
            MC.withBusy(save, function () {
              return api.post("/profiles", { name: name.value.trim(), copy_from: copy.value || null }).then(function (r) {
                close();
                MC.toast.success("已创建机型 " + r.profile.name);
                app.switchProfile(r.profile.id);
              }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function renameProfile(m) {
      var name = h("input.input", { type: "text", value: m.name });
      var F = { name: MC.field({ label: "机型名称", required: true, control: name }) };
      MC.modal({
        title: "重命名机型",
        body: F.name,
        footer: function (close) {
          var save = h("button.btn.btn-primary", { type: "button" }, "保存");
          save.addEventListener("click", function () {
            MC.withBusy(save, function () {
              return api.patch("/profiles/" + api.seg(m.id), { name: name.value.trim() }).then(function () { close(); MC.toast.success("已重命名"); app.reloadSession(); }, function (err) { fieldError(F, err); });
            });
          });
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"), save];
        },
      });
    }

    function removeProfile(m) {
      MC.confirm({
        title: "删除机型 " + m.name,
        message: "机型将从列表移除，其数据目录会移入服务器恢复区（data/recovery/），不会立即物理删除。",
        confirmText: "删除机型",
        danger: true,
        requireText: m.id,
      }).then(function (ok) {
        if (!ok) return;
        api.del("/profiles/" + api.seg(m.id)).then(function () {
          MC.toast.success("已删除机型 " + m.name);
          if (m.id === app.pid) app.switchProfile("default");
          else app.reloadSession();
        }, function (err) { MC.toastError("删除失败", err); });
      });
    }

    renderSection();
    return {
      el: el,
      title: "设置",
      icon: "settings",
      update: function (r) {
        var s = r.arg || "sources";
        if (s !== S.section) { S.section = s; renderSection(); }
      },
      destroy: function () { unsub.forEach(function (f) { f(); }); },
    };
  };
})(window);
