/*
 * V3.1 更新管理工作区级入口（M16-31 / M16-32 / INH-2196、INH-2197）。
 *
 * 壳：来源/计划/连接/运行记录/候选五个 tab 与旧设置深链收口。
 * M16-32：来源真实列表、筛选、分页、排序、编辑抽屉与单项更新；不加载 mock。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var TABS = [
    { id: "sources", label: "更新源", desc: "工作区级来源、筛选、单项与批量更新。" },
    { id: "plan", label: "更新计划", desc: "默认频率、来源覆盖、全量预览与手动任务。" },
    { id: "connections", label: "连接设置", desc: "SDA 连接、保活、自动重登与凭据状态。" },
    { id: "runs", label: "运行记录", desc: "持久运行、阶段日志、失败重试与脱敏诊断。" },
    { id: "candidates", label: "更新候选", desc: "候选差异、冲突决议与写入 V3 草稿。" },
  ];

  var KIND_LABEL = { http_file: "HTTP 文件", http_record: "HTTP 修改记录", sda_catalog: "SDA 目录", sda_table: "SDA 表", sda_file: "SDA 文件" };
  var SOURCE_STATUS = [
    { id: "", label: "全部状态" },
    { id: "enabled", label: "已启用" },
    { id: "disabled", label: "已停用" },
  ];
  var SORTS = [
    { id: "name", label: "按名称" },
    { id: "updated", label: "按更新时间" },
  ];

  function tabById(id) {
    return TABS.filter(function (tab) { return tab.id === id; })[0] || TABS[0];
  }

  function workspaceId() {
    try {
      if (MC.workspaces && MC.workspaces.scopeId) return MC.workspaces.scopeId();
    } catch (e) { /* 测试页/旧壳 */ }
    return "default";
  }

  function sourcesPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/sources";
  }

  function scheduleText(source) {
    if (source.schedule_mode === "manual") return "仅手动";
    if (source.schedule_mode === "custom") return String(source.interval_hours || "?") + " 小时";
    return "继承计划";
  }

  function sourcePath(source) {
    return sourcesPath() + "/" + encodeURIComponent(source.source_id);
  }

  function errorText(err) {
    if (!err) return "操作失败";
    if (err.code === "REVISION_CONFLICT") return "来源已在其他客户端更新；当前草稿已保留，请刷新后重试";
    return err.message || "操作失败";
  }

  MC.views.updates = function (app, route) {
    var state = {
      tab: tabById(route && route.arg).id,
      sources: {
        items: [],
        total: 0,
        next_cursor: null,
        query: "",
        status: "",
        sort: "name",
        loading: false,
        error: "",
        expanded: false,
      },
    };
    var el = h("div");
    var tabbar = h("div.row", { role: "tablist", "aria-label": "更新管理分类", style: "gap:6px;flex-wrap:wrap;margin:10px 0 14px" });
    var body = h("div");

    function renderTabs() {
      util.clear(tabbar);
      TABS.forEach(function (tab) {
        var active = state.tab === tab.id;
        var button = h(
          "button.btn" + (active ? ".btn-primary" : ""),
          {
            type: "button",
            role: "tab",
            "aria-selected": active ? "true" : "false",
            "data-sync-tab": tab.id,
          },
          tab.label
        );
        button.addEventListener("click", function () {
          state.tab = tab.id;
          renderTabs();
          renderBody();
          if (app && app.go) app.go({ kind: "updates", arg: tab.id });
        });
        tabbar.appendChild(button);
      });
    }

    function canWrite() {
      return !!(app && app.can && app.can("settings_write"));
    }

    function loadSources(reset) {
      var S = state.sources;
      if (S.loading) return Promise.resolve();
      S.loading = true;
      S.error = "";
      if (reset) {
        S.items = [];
        S.next_cursor = null;
        S.expanded = false;
      }
      renderBody();
      var query = {
        q: S.query,
        status: S.status || undefined,
        limit: 50,
        cursor: S.expanded && S.next_cursor ? S.next_cursor : "0",
      };
      return MC.api.requestV3("GET", sourcesPath(), { query: query }).then(function (data) {
        var incoming = (data && data.items) || [];
        S.items = S.expanded ? S.items.concat(incoming) : incoming;
        S.total = data && typeof data.total === "number" ? data.total : S.items.length;
        S.next_cursor = data ? data.next_cursor : null;
        S.loading = false;
        S.error = "";
        renderBody();
      }, function (err) {
        S.loading = false;
        S.error = errorText(err);
        renderBody();
      });
    }

    function sortedItems() {
      var rows = state.sources.items.slice();
      if (state.sources.sort === "updated") {
        rows.sort(function (a, b) { return String(b.updated_at || "").localeCompare(String(a.updated_at || "")); });
      } else {
        rows.sort(function (a, b) { return String(a.name || "").localeCompare(String(b.name || "")); });
      }
      return rows;
    }

    function field(label, control) {
      return h("div.col", { style: "gap:4px" }, h("label.small.muted", label), control);
    }

    function openEditor(source) {
      var draft = {
        name: source.name || "",
        resource_ref: source.resource_ref || "",
        record_source: source.record_source || "",
        keyword: source.keyword || "",
        display_path: source.display_path || "",
        enabled: source.enabled !== false,
        schedule_mode: source.schedule_mode || "inherit",
        interval_hours: source.interval_hours == null ? "" : String(source.interval_hours),
      };
      var error = h("div.form-error", { style: "display:none" });
      var nameInput = h("input.input", { value: draft.name, autofocus: true });
      var refInput = h("input.input", { value: draft.resource_ref, placeholder: "下载 URL / SDA 资源引用" });
      var displayInput = h("input.input", { value: draft.display_path, placeholder: "业务路径，例如 /synthetic/A.xml" });
      var recordInput = h("input.input", { value: draft.record_source, placeholder: "修改记录链接（可选）" });
      var keywordInput = h("input.input", { value: draft.keyword, placeholder: "压缩包内文件名/正则（可选）" });
      var enabledInput = h("input", { type: "checkbox", checked: draft.enabled });
      var modeSelect = h("select.input", {},
        h("option", { value: "inherit" }, "继承工作区计划"),
        h("option", { value: "custom" }, "自定义间隔"),
        h("option", { value: "manual" }, "仅手动")
      );
      var intervalInput = h("input.input", { type: "number", min: "1", max: "2160", value: draft.interval_hours, placeholder: "小时" });
      modeSelect.value = draft.schedule_mode;
      var intervalWrap = h("div.col", { style: "gap:4px" }, h("label.small.muted", "间隔（小时）"), intervalInput);
      intervalWrap.style.display = draft.schedule_mode === "custom" ? "" : "none";
      modeSelect.addEventListener("change", function () {
        intervalWrap.style.display = modeSelect.value === "custom" ? "" : "none";
      });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "编辑更新源",
        subtitle: (KIND_LABEL[source.kind] || source.kind) + " · " + source.source_id,
        size: "wide",
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("资源引用", refInput),
          field("业务路径", displayInput),
          field("修改记录链接", recordInput),
          field("关键词", keywordInput),
          h("div.row", { style: "gap:18px;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用")),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "计划"), modeSelect)
          ),
          intervalWrap,
          h("p.xsmall.muted", "修改会以来源 revision " + source.revision + " 做 CAS；409 时保留当前草稿。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-sync-save": "1",
              onclick: function () {
                var payload = {
                  name: nameInput.value.trim(),
                  resource_ref: refInput.value.trim() || null,
                  record_source: recordInput.value.trim() || null,
                  keyword: keywordInput.value.trim(),
                  display_path: displayInput.value.trim() || null,
                  enabled: !!enabledInput.checked,
                  schedule_mode: modeSelect.value,
                  interval_hours: modeSelect.value === "custom" ? Number(intervalInput.value) : null,
                };
                if (!payload.name) { showError("名称不能为空"); return; }
                if (payload.display_path && payload.display_path.charAt(0) !== "/") {
                  showError("业务路径必须以 / 开始（目录 ID 与路径不一致会被拒绝）");
                  return;
                }
                if (payload.schedule_mode === "custom" && (!payload.interval_hours || payload.interval_hours < 1 || payload.interval_hours > 2160)) {
                  showError("自定义间隔必须在 1–2160 小时");
                  return;
                }
                showError("");
                MC.api.requestV3("PATCH", sourcePath(source), {
                  body: payload,
                  headers: { "If-Match": String(source.revision) },
                }).then(function () {
                  close();
                  MC.toast.success("更新源已保存");
                  loadSources(true);
                }, function (err) {
                  showError(errorText(err));
                });
              },
            }, "保存"),
          ];
        },
      });
      return modal;
    }

    function runSource(source, button) {
      if (button) button.disabled = true;
      MC.api.requestV3("POST", sourcePath(source) + "/run", { body: {} }).then(function (data) {
        MC.toast.success("已提交单项更新");
        if (data && data.run && data.run.run_id) {
          MC.bus.emit("sync-run-started", data.run);
        }
        if (button) button.disabled = false;
      }, function (err) {
        MC.toast.error("单项更新提交失败", errorText(err));
        if (button) button.disabled = false;
      });
    }

    function renderSources() {
      var S = state.sources;
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var search = h("input.input", { type: "search", placeholder: "搜索名称/路径", value: S.query, style: "min-width:200px" });
      var status = h("select.input", {},
        SOURCE_STATUS.map(function (item) { return h("option", { value: item.id }, item.label); })
      );
      var sort = h("select.input", {},
        SORTS.map(function (item) { return h("option", { value: item.id }, item.label); })
      );
      function searchNow() {
        S.query = search.value.trim();
        loadSources(true);
      }
      search.addEventListener("keydown", function (event) {
        if (event.key === "Enter") searchNow();
      });
      var searchBtn = h("button.btn.btn-sm", { type: "button", onclick: searchNow }, "搜索");
      status.value = S.status;
      sort.value = S.sort;
      status.addEventListener("change", function () { S.status = status.value; loadSources(true); });
      sort.addEventListener("change", function () { S.sort = sort.value; renderBody(); });
      var refresh = h("button.btn.btn-sm", { type: "button", onclick: function () { loadSources(true); } }, "刷新");
      toolbar.appendChild(search);
      toolbar.appendChild(searchBtn);
      toolbar.appendChild(status);
      toolbar.appendChild(sort);
      toolbar.appendChild(refresh);

      if (S.error) {
        body.appendChild(h("div.form-error", S.error));
      }
      if (S.loading && !S.items.length) {
        body.appendChild(h("p.muted", "正在读取更新源…"));
        return;
      }
      if (!S.items.length) {
        body.appendChild(h("div.card", h("div.card-b", h("p.muted", "当前工作区还没有更新源。"))));
        return;
      }
      var tbody = h("tbody");
      sortedItems().forEach(function (source) {
        var actions = h("td.row", { style: "gap:6px" });
        if (canWrite()) {
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-edit": source.source_id,
            onclick: function () { openEditor(source); },
          }, "编辑"));
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-run": source.source_id,
            onclick: function (event) { runSource(source, event.currentTarget); },
          }, "更新"));
        } else {
          actions.appendChild(h("span.muted.xsmall", "只读"));
        }
        tbody.appendChild(h("tr", { "data-sync-source": source.source_id },
          h("td", h("div", h("strong", source.name)), h("div.xsmall.muted", source.source_id)),
          h("td", h("span.badge", KIND_LABEL[source.kind] || source.kind)),
          h("td", h("div.small", source.display_path || source.resource_ref || "—"), h("div.xsmall.muted", source.record_source ? "含修改记录" : "无记录链接")),
          h("td", source.enabled === false ? h("span.badge.badge-muted", "已停用") : h("span.badge.badge-ok", "已启用")),
          h("td", h("div.small", scheduleText(source)), h("div.xsmall.muted", source.keyword ? "关键词 " + source.keyword : "无关键词")),
          actions
        ));
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "更新源"), h("span.muted.small", "共 " + S.total + " 条")),
        h("div.card-b",
          toolbar,
          h("table.table", h("thead", h("tr", h("th", "名称"), h("th", "类型"), h("th", "路径/引用"), h("th", "状态"), h("th", "计划"), h("th", ""))), tbody),
          S.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { S.expanded = true; loadSources(false); } }, "加载更多") : null
        )
      ));
    }

    function renderPlaceholder(tab) {
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", tab.label), h("span.muted.small", "工作区级")),
        h("div.card-b", h("p", tab.desc), h("p.muted.small", "该子页的真实 API/交互将在后续卡接入；当前不加载原型 mock 数据。"))
      ));
    }

    function renderBody() {
      util.clear(body);
      var tab = tabById(state.tab);
      if (tab.id === "sources") {
        renderSources();
      } else {
        renderPlaceholder(tab);
      }
    }

    el.appendChild(h("div.view-body",
      h("div.page-head", h("div", h("h2", "更新管理"), h("p.muted", "更新源、计划、连接、运行记录与候选的统一入口；旧设置里的更新源/定时更新入口已收口到这里。"))),
      tabbar,
      body
    ));
    renderTabs();
    renderBody();
    if (state.tab === "sources") loadSources(true);

    return {
      el: el,
      title: "更新管理",
      icon: "settings",
      update: function (next) {
        state.tab = tabById(next && next.arg).id;
        renderTabs();
        renderBody();
        if (state.tab === "sources") loadSources(true);
      },
      isDirty: function () { return false; },
      destroy: function () {},
    };
  };
})(typeof window !== "undefined" ? window : this);
