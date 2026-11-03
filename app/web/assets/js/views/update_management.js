/*
 * V3.1 更新管理工作区级入口（M16-31 / M16-32 / M16-33 / INH-2196、INH-2197、INH-2198）。
 *
 * 壳：来源/计划/连接/运行记录/候选五个 tab 与旧设置深链收口。
 * M16-32：来源真实列表、筛选、分页、排序、编辑抽屉与单项更新；不加载 mock。
 * M16-33：更新计划（全局/机型覆盖 CAS 保存）、工作区全量预览、失败筛选只做
 * 客户端显示过滤（请求始终 scope=workspace_all）、暂停自动后仍可手动全量更新。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});
  var util = MC.util;
  var h = util.h;
  var icon = MC.icon;

  var TABS = [
    { id: "sources", label: "更新源" },
    { id: "runs", label: "运行记录" },
    { id: "candidates", label: "更新候选" },
  ];

  //: 手动/批量任务记录跨视图保留：切换 tab 或工作区后仍能看见 run 最初归属的 W。
  var RUN_LOG = [];
  var RUN_LOG_MAX = 20;
  var FAILED_ITEM_STATUSES = ["failed", "blocked", "interrupted", "canceled"];

  //: 与服务端 SOURCE_KINDS 严格一致；不列出不存在的类型（旧 http_record 已废弃）。
  var KIND_LABEL = { http_file: "HTTP 文件", sda_catalog: "SDA 目录", sda_table: "SDA 表", sda_file: "SDA 文件" };
  var KIND_OPTIONS = ["http_file", "sda_catalog", "sda_table", "sda_file"];
  var TARGET_LABEL = {
    file_archive: "文件归档（FileResource）",
    default_candidate: "默认配置候选（固定配置 + 软件版本）",
    machine_observation: "机台观测（需要可信 device_id）",
  };
  var TARGET_OPTIONS = ["file_archive", "default_candidate", "machine_observation"];
  var EXCLUDED_REASON = {
    NOT_FOUND: "目标或连接不存在",
    SOURCE_CHANGED: "catalog 引用与路径不一致",
    INVALID_INPUT: "配置不完整",
    DOWNLOAD_FAILED: "采集器不可用（离线）",
  };
  var DECISION_LABEL = {
    use_new: "采用新值",
    inherit_old: "保留当前",
    manual: "人工处理",
    pin: "固定",
    clear_pin: "取消固定",
  };
  var CANDIDATE_STATE_LABEL = {
    pending: "待决议",
    applied: "已写入草稿",
    dismissed: "已忽略",
    superseded: "已被新内容取代",
    conflicted: "有冲突",
    failed: "失败",
  };
  var SOURCE_STATUS = [
    { id: "", label: "全部状态" },
    { id: "enabled", label: "已启用" },
    { id: "disabled", label: "已停用" },
    { id: "deleted", label: "已删除" },
  ];
  var SORTS = [
    { id: "name", label: "按名称" },
    { id: "updated", label: "按更新时间" },
  ];

  function tabById(id) {
    if (id === "plan" || id === "schedule" || id === "connections") return TABS[0];
    return TABS.filter(function (tab) { return tab.id === id; })[0] || TABS[0];
  }

  function sheetFor(route) {
    var arg = route && route.arg;
    if (arg === "plan" || arg === "schedule") return "plan";
    if (arg === "connections") return "connections";
    return "";
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

  function policyPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/policy";
  }

  function previewsPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/previews";
  }

  function runsPath() {
    return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/runs";
  }

  function workspaceProfiles() {
    try {
      var current = MC.workspaces && MC.workspaces.current ? MC.workspaces.current() : null;
      var profiles = current && current.profiles;
      if (Array.isArray(profiles) && profiles.length) {
        return profiles.map(function (profile) {
          return { id: String(profile.id), name: profile.name || String(profile.id) };
        });
      }
    } catch (e) { /* 测试页/旧壳 */ }
    return [{ id: "default", name: "默认机型" }];
  }

  function draftFromPolicy(policy) {
    var overrides = {};
    var raw = (policy && policy.profile_overrides) || {};
    Object.keys(raw).forEach(function (pid) {
      var item = raw[pid] || {};
      overrides[pid] = {
        enabled: item.enabled === true ? true : (item.enabled === false ? false : undefined),
        interval_hours: item.interval_hours == null ? undefined : item.interval_hours,
      };
    });
    return {
      enabled: !!(policy && policy.enabled),
      default_interval_hours:
        policy && policy.default_interval_hours != null ? policy.default_interval_hours : 6,
      overrides: overrides,
    };
  }

  function planErrorText(err) {
    if (err && err.code === "REVISION_CONFLICT") {
      return "计划或预览已在其他客户端更新；当前草稿已保留，请刷新后重试";
    }
    if (err && err.code === "PLAN_STALE") return "预览已过期，请重新预览";
    return errorText(err);
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
    if (err.code === "REVISION_CONFLICT") return "数据已在其他客户端更新；当前草稿已保留，请刷新后重试";
    return err.message || "操作失败";
  }

  MC.views.updates = function (app, route) {
    var state = {
      tab: tabById(route && route.arg).id,
      sheet: sheetFor(route),
      sources: {
        items: [],
        total: 0,
        next_cursor: null,
        query: "",
        status: "",
        scope: "workspace",
        sort: "name",
        loading: false,
        error: "",
        expanded: false,
        selected: {},
        testing: {},
        busy: "",
        testResults: {},
      },
      runs: {
        items: [],
        total: 0,
        next_cursor: null,
        state: "",
        loading: false,
        error: "",
        expanded: false,
      },
      candidates: {
        items: [],
        total: 0,
        next_cursor: null,
        state: "",
        profile_id: "",
        loading: false,
        error: "",
        loadedFor: "",
        selected: {},
        busy: "",
      },
      plan: {
        loadedFor: "",
        loading: false,
        error: "",
        policy: null,
        draft: null,
        sources: [],
        preview: null,
        previewWorkspaceId: "",
        previewing: false,
        validating: false,
        saving: false,
        running: false,
        failuresOnly: false,
        failedSourceIds: {},
      },
      connections: {
        loadedFor: "",
        loading: false,
        error: "",
        items: [],
        heartbeats: {},
        heartbeatErrors: {},
        operations: {},
        pollAttempts: {},
        pollTimers: [],
        loadSeq: 0,
      },
    };
    var el = h("div");
    var tabbar = h("div.sync-subnav", { role: "tablist", "aria-label": "更新管理内容" });
    var body = h("div");
    //: 编辑抽屉是否有未保存草稿：驱动切 tab / 关抽屉 / 切工作区的提示。
    var activeEditorDirty = false;

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
          if (activeEditorDirty) {
            MC.confirm({
              title: "放弃未保存的修改？",
              message: "切换分类会丢弃更新源编辑抽屉里未保存的草稿。",
              confirmText: "放弃并切换",
              danger: true,
            }).then(function (ok) {
              if (!ok) return;
              activeEditorDirty = false;
          state.sheet = "";
          state.tab = tab.id;
          renderTabs();
          renderBody();
          if (app && app.go) app.go({ kind: "updates", arg: tab.id });
            });
            return;
          }
          state.sheet = "";
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

    function canRun() {
      return !!(app && app.can && app.can("tasks_run"));
    }

    function canSavePlan() {
      return !!(app && app.can && app.can("settings_write"));
    }

    function canRunPlan() {
      return !!(app && app.can && app.can("tasks_run"));
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
        // 选中集合只保留仍在当前结果里的来源，避免"幽灵选中"。
        var alive = {};
        S.items.forEach(function (row) { alive[row.source_id] = true; });
        Object.keys(S.selected).forEach(function (sid) { if (!alive[sid]) delete S.selected[sid]; });
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

    function targetOfSource(source) {
      var target = (source && source.target) || null;
      return {
        kind: (target && target.kind) || "file_archive",
        file_id: (target && target.file_id) || "",
        configuration_id: (target && target.configuration_id) || "",
        release_id: (target && target.release_id) || "",
        device_id: (target && target.device_id) || "",
      };
    }

    function openEditor(source) {
      var isCreate = !source;
      source = source || {};
      var hadTarget = !!(source && source.target);
      var targetDirty = false;
      var target0 = targetOfSource(source);
      var draft = {
        name: source.name || "",
        profile_id: source.profile_id || workspaceProfiles()[0].id,
        kind: source.kind || "http_file",
        connection_id: source.connection_id || "",
        resource_ref: source.resource_ref || "",
        record_source: source.record_source || "",
        keyword: source.keyword || "",
        display_path: source.display_path || "",
        enabled: source.enabled !== false,
        schedule_mode: source.schedule_mode || "inherit",
        interval_hours: source.interval_hours == null ? "" : String(source.interval_hours),
        target_kind: target0.kind,
        target_file_id: target0.file_id,
        target_config_id: target0.configuration_id,
        target_release_id: target0.release_id,
        target_device_id: target0.device_id,
      };
      var error = h("div.form-error", { style: "display:none" });
      var hint = h("p.xsmall.muted", { "data-sync-hint": "1" });
      var nameInput = h("input.input", { value: draft.name, autofocus: true });
      var refInput = h("input.input", { value: draft.resource_ref, placeholder: "下载 URL / SDA 资源引用" });
      var displayInput = h("input.input", { value: draft.display_path, placeholder: "业务路径，例如 /synthetic/A.xml" });
      var recordInput = h("input.input", { value: draft.record_source, placeholder: "修改记录链接（可选）" });
      var keywordInput = h("input.input", { value: draft.keyword, placeholder: "压缩包内文件名/正则（可选）" });
      var profileSelect = h("select.input", {},
        workspaceProfiles().map(function (profile) { return h("option", { value: profile.id }, profile.name); })
      );
      profileSelect.value = draft.profile_id;
      if (!isCreate) profileSelect.disabled = true;
      var kindSelect = h("select.input", { "data-sync-kind": "1" },
        KIND_OPTIONS.map(function (kind) { return h("option", { value: kind }, KIND_LABEL[kind]); })
      );
      kindSelect.value = draft.kind;
      var connectionSelect = h("select.input", { "data-sync-connection": "1" },
        h("option", { value: "" }, "（HTTP 来源无需连接）")
      );
      connectionSelect.value = draft.connection_id;
      var catalogBlock = h("div");
      var targetBlock = h("div.col", { style: "gap:8px" });
      var connections = [];
      var catalogItems = [];
      var fileItems = [];
      var configItems = [];
      var releaseItems = [];

      function currentKind() { return kindSelect.value; }
      function needsConnection() { return currentKind().indexOf("sda_") === 0; }
      function isCatalogKind() { return currentKind() === "sda_table" || currentKind() === "sda_file"; }

      function renderCatalogBlock() {
        util.clear(catalogBlock);
        if (!isCatalogKind()) return;
        var msg = h("span.xsmall.muted", { "data-sync-catalog-msg": "1" }, "ID 与展示路径必须来自当前连接目录。");
        var select = h("select.input", { style: "display:none", "data-sync-catalog-select": "1" },
          h("option", { value: "" }, "选择目录条目…")
        );
        select.addEventListener("change", function () {
          if (select.value === "") return;
          var picked = catalogItems[Number(select.value)];
          if (!picked) return;
          refInput.value = picked.resource_ref;
          displayInput.value = picked.display_path;
          msg.textContent = "已填入 " + picked.name + "（保存时服务端会再次校验）";
        });
        var button = h("button.btn.btn-sm", {
          type: "button",
          "data-sync-catalog": "1",
          onclick: function () {
            // 连接列表读取失败时回退到来源原有连接，避免"目录按钮点了没反应"。
            var connectionId = connectionSelect.value || draft.connection_id;
            if (!connectionId) { msg.textContent = "先选择连接。"; return; }
            button.disabled = true;
            msg.textContent = "正在读取 SDA 目录…";
            var path = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/connections/" + encodeURIComponent(connectionId) + "/catalog";
            var wanted = currentKind() === "sda_table" ? "table" : "file";
            MC.api.requestV3("GET", path).then(function (data) {
              catalogItems = ((data && data.items) || []).filter(function (item) { return item.kind === wanted; });
              util.clear(select);
              select.appendChild(h("option", { value: "" }, "选择目录条目…"));
              catalogItems.forEach(function (item, index) {
                select.appendChild(h("option", { value: String(index) },
                  (item.kind === "table" ? "表" : "文件") + " · " + item.name + " · " + item.display_path));
              });
              select.style.display = catalogItems.length ? "" : "none";
              button.disabled = false;
              msg.textContent = catalogItems.length
                ? "共 " + catalogItems.length + " 项；选中后自动填入 ID/路径。"
                : "当前目录没有可选项（采集器离线时不返回目录）。";
            }, function (err) {
              button.disabled = false;
              msg.textContent = (err && (err.message || err.code)) || "目录读取失败";
            });
          },
        }, "从 SDA 目录选择");
        catalogBlock.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" }, button, select, msg));
      }

      function renderTargetBlock() {
        util.clear(targetBlock);
        var kindField = h("select.input", { "data-sync-target-kind": "1" },
          TARGET_OPTIONS.map(function (kind) { return h("option", { value: kind }, TARGET_LABEL[kind]); })
        );
        kindField.value = draft.target_kind;
        kindField.addEventListener("change", function () {
          draft.target_kind = kindField.value;
          targetDirty = true;
          renderTargetBlock();
          markDirty();
        });
        targetBlock.appendChild(field("目标类型", kindField));
        if (draft.target_kind === "file_archive") {
          var fileSelect = h("select.input", { "data-sync-target-file": "1" },
            h("option", { value: "" }, fileItems.length ? "选择目标文件…" : "正在读取文件…")
          );
          fileSelect.value = draft.target_file_id;
          fileSelect.addEventListener("change", function () {
            draft.target_file_id = fileSelect.value;
            targetDirty = true;
            markDirty();
          });
          targetBlock.appendChild(field("目标文件（FileResource）", fileSelect));
          targetBlock.appendChild(h("p.xsmall.muted", "归档到该文件资源的当前版本；不新建第二文件库。"));
          if (!fileItems.length) {
            MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/profiles/" + encodeURIComponent(profileSelect.value) + "/file-selector", {})
              .then(function (data) {
                fileItems = (data && data.items) || [];
                var keep = draft.target_file_id;
                util.clear(fileSelect);
                fileSelect.appendChild(h("option", { value: "" }, fileItems.length ? "选择目标文件…" : "当前机型没有文件资源"));
                fileItems.forEach(function (item) {
                  fileSelect.appendChild(h("option", { value: item.file_id }, item.name + (item.current_version ? " · v" + item.current_version : "")));
                });
                fileSelect.value = keep;
              }, function (err) {
                util.clear(fileSelect);
                fileSelect.appendChild(h("option", { value: "" }, "文件列表读取失败：" + errorText(err)));
              });
          }
        } else if (draft.target_kind === "default_candidate") {
          var configSelect = h("select.input", { "data-sync-target-config": "1" },
            h("option", { value: "" }, configItems.length ? "选择固定配置…" : "正在读取配置…")
          );
          configSelect.value = draft.target_config_id;
          var releaseSelect = h("select.input", { "data-sync-target-release": "1" },
            h("option", { value: "" }, "选择软件版本…")
          );
          function fillReleases() {
            util.clear(releaseSelect);
            var rows = releaseItems.filter(function (row) { return row.config_id === configSelect.value; });
            if (!rows.length) {
              releaseSelect.appendChild(h("option", { value: "" }, "该配置没有版本"));
              return;
            }
            rows.forEach(function (row) {
              releaseSelect.appendChild(h("option", { value: row.release_id }, "版本 " + (row.version || row.release_id)));
            });
            releaseSelect.value = draft.target_release_id;
          }
          configSelect.addEventListener("change", function () {
            draft.target_config_id = configSelect.value;
            draft.target_release_id = "";
            targetDirty = true;
            fillReleases();
            markDirty();
          });
          releaseSelect.addEventListener("change", function () {
            draft.target_release_id = releaseSelect.value;
            targetDirty = true;
            markDirty();
          });
          targetBlock.appendChild(field("固定配置", configSelect));
          targetBlock.appendChild(field("软件版本", releaseSelect));
          targetBlock.appendChild(h("p.xsmall.muted", "只形成候选，不自动发布 D0/Bn/rn。"));
          if (!configItems.length) {
            MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
              "/profiles/" + encodeURIComponent(profileSelect.value) + "/configs", {})
              .then(function (data) {
                configItems = (data && data.items) || [];
                util.clear(configSelect);
                configSelect.appendChild(h("option", { value: "" }, configItems.length ? "选择固定配置…" : "当前机型没有固定配置"));
                configItems.forEach(function (item) {
                  configSelect.appendChild(h("option", { value: item.config_id }, (item.code || "") + " " + (item.name || item.config_id)));
                });
                configSelect.value = draft.target_config_id;
                var pending = configItems.map(function (item) {
                  return MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
                    "/profiles/" + encodeURIComponent(profileSelect.value) + "/configs/" +
                    encodeURIComponent(item.config_id) + "/releases", {})
                    .then(function (rows) {
                      ((rows && rows.items) || []).forEach(function (row) {
                        releaseItems.push({ config_id: item.config_id, release_id: row.release_id, version: row.version });
                      });
                    }, function () { /* 单个配置读取失败不影响其他 */ });
                });
                return Promise.all(pending);
              }).then(fillReleases, function (err) {
                util.clear(configSelect);
                configSelect.appendChild(h("option", { value: "" }, "配置读取失败：" + errorText(err)));
              });
          } else {
            fillReleases();
          }
        } else {
          var deviceInput = h("input.input", {
            value: draft.target_device_id,
            placeholder: "可信 device_id（不接受按文件名/IP 猜测）",
            "data-sync-target-device": "1",
          });
          deviceInput.addEventListener("input", function () {
            draft.target_device_id = deviceInput.value.trim();
            targetDirty = true;
            markDirty();
          });
          targetBlock.appendChild(field("device_id", deviceInput));
          targetBlock.appendChild(h("p.xsmall.muted", "没有可信 device_id 时不允许保存：不会伪造 rn，也不会把观测写进机型基线。"));
        }
      }

      function syncConditional() {
        draft.kind = currentKind();
        connectionSelect.disabled = !needsConnection();
        if (needsConnection()) {
          connectionSelect.options[0].textContent = "选择已批准连接…";
        } else {
          connectionSelect.options[0].textContent = "（HTTP 来源无需连接）";
        }
        renderCatalogBlock();
        renderTargetBlock();
        markDirty();
      }

      function fillConnections() {
        MC.api.requestV3("GET", "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/connections", {})
          .then(function (data) {
            connections = (data && data.items) || [];
            util.clear(connectionSelect);
            connectionSelect.appendChild(h("option", { value: "" },
              connections.length ? (needsConnection() ? "选择已批准连接…" : "（HTTP 来源无需连接）") : "还没有连接"));
            connections.forEach(function (conn) {
              connectionSelect.appendChild(h("option", { value: conn.connection_id },
                conn.name + (conn.enabled === false ? "（已停用）" : "")));
            });
            connectionSelect.value = draft.connection_id;
          }, function (err) {
            util.clear(connectionSelect);
            connectionSelect.appendChild(h("option", { value: "" }, "连接列表读取失败：" + errorText(err)));
          });
      }

      var baseline = "";
      function readDraft() {
        return JSON.stringify({
          name: nameInput.value.trim(),
          kind: kindSelect.value,
          connection_id: connectionSelect.value,
          resource_ref: refInput.value.trim(),
          display_path: displayInput.value.trim(),
          record_source: recordInput.value.trim(),
          keyword: keywordInput.value.trim(),
          enabled: !!enabledInput.checked,
          schedule_mode: modeSelect.value,
          interval_hours: intervalInput.value,
          target_kind: draft.target_kind,
          target_file_id: draft.target_file_id,
          target_config_id: draft.target_config_id,
          target_release_id: draft.target_release_id,
          target_device_id: draft.target_device_id,
        });
      }
      function isDirtyNow() {
        return baseline !== "" && readDraft() !== baseline;
      }
      function markDirty() {
        if (baseline === "") return;
        activeEditorDirty = isDirtyNow();
      }
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
        markDirty();
      });
      [nameInput, refInput, displayInput, recordInput, keywordInput, intervalInput].forEach(function (input) {
        input.addEventListener("input", markDirty);
      });
      enabledInput.addEventListener("change", markDirty);
      kindSelect.addEventListener("change", function () {
        draft.kind = kindSelect.value;
        if (needsConnection() && !connectionSelect.value && connections.length === 1) {
          connectionSelect.value = connections[0].connection_id;
          draft.connection_id = connectionSelect.value;
        }
        syncConditional();
      });
      connectionSelect.addEventListener("change", function () {
        draft.connection_id = connectionSelect.value;
        renderCatalogBlock();
        markDirty();
      });
      profileSelect.addEventListener("change", function () {
        draft.profile_id = profileSelect.value;
        fileItems = [];
        configItems = [];
        releaseItems = [];
        renderTargetBlock();
        markDirty();
      });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      function buildPayload() {
        var target = {
          kind: draft.target_kind,
          file_id: draft.target_kind === "file_archive" ? (draft.target_file_id || null) : null,
          configuration_id: draft.target_kind === "default_candidate" ? (draft.target_config_id || null) : null,
          release_id: draft.target_kind === "default_candidate" ? (draft.target_release_id || null) : null,
          device_id: draft.target_kind === "machine_observation" ? (draft.target_device_id || null) : null,
        };
        // 旧来源可能从未绑定目标：不改目标时不发 target 键，交给服务端保留原值。
        var wantsTarget = hadTarget || targetDirty || isCreate;
        var payload = {
          name: nameInput.value.trim(),
          kind: kindSelect.value,
          resource_ref: refInput.value.trim() || null,
          record_source: recordInput.value.trim() || null,
          keyword: keywordInput.value.trim(),
          display_path: displayInput.value.trim() || null,
          enabled: !!enabledInput.checked,
          schedule_mode: modeSelect.value,
          interval_hours: modeSelect.value === "custom" ? Number(intervalInput.value) : null,
          connection_id: needsConnection() ? (connectionSelect.value || draft.connection_id || null) : null,
        };
        if (wantsTarget) payload.target = target;
        if (isCreate) payload.profile_id = profileSelect.value;
        return payload;
      }

      function validate(payload) {
        if (!payload.name) return "名称不能为空";
        if (needsConnection() && !payload.connection_id) return "SDA 来源必须选择已批准的连接";
        if (isCatalogKind() && (!payload.resource_ref || !payload.display_path)) {
          return "SDA 表/文件必须从目录选择资源 ID 与业务路径";
        }
        if (isCreate && payload.kind === "http_file" && !payload.resource_ref) {
          return "HTTP 来源必须填写下载 URL";
        }
        if (payload.display_path && payload.display_path.charAt(0) !== "/") {
          return "业务路径必须以 / 开始（目录 ID 与路径不一致会被拒绝）";
        }
        if (payload.schedule_mode === "custom" && (!payload.interval_hours || payload.interval_hours < 1 || payload.interval_hours > 2160)) {
          return "自定义间隔必须在 1–2160 小时";
        }
        if (!payload.target) return "";
        if (payload.target.kind === "file_archive" && !payload.target.file_id) return "请选择目标文件（FileResource）";
        if (payload.target.kind === "default_candidate" && (!payload.target.configuration_id || !payload.target.release_id)) {
          return "请选择固定配置与软件版本";
        }
        if (payload.target.kind === "machine_observation" && !payload.target.device_id) {
          return "机台观测必须填写可信 device_id（不允许按文件名/IP 猜测）";
        }
        return "";
      }

      var reloadButton = h("button.btn.btn-sm", {
        type: "button",
        "data-sync-reload": "1",
        style: "display:none",
      }, "重新载入最新版本");
      reloadButton.addEventListener("click", function () {
        if (isCreate) return;
        reloadButton.disabled = true;
        MC.api.requestV3("GET", sourcePath(source)).then(function (fresh) {
          reloadButton.disabled = false;
          source.revision = fresh.revision;
          hint.textContent = "已重新载入 revision " + fresh.revision + "；你的输入未改动，确认后再保存。";
          showError("");
        }, function (err) {
          reloadButton.disabled = false;
          showError("重新载入失败：" + errorText(err));
        });
      });

      var casNote = isCreate
        ? h("p.xsmall.muted", "新建后立即生效；来源只登记，不下载、不改文件。")
        : h("p.xsmall.muted", "修改会以来源 revision " + source.revision + " 做 CAS；409 时保留当前草稿。");

      var requestDismiss = function () {};
      var modal = MC.modal({
        title: isCreate ? "新建更新源" : "编辑更新源",
        subtitle: isCreate
          ? "工作区 " + workspaceId()
          : (KIND_LABEL[source.kind] || source.kind) + " · " + source.source_id,
        size: "wide",
        dismissible: false,
        onDismiss: function () { requestDismiss(); },
        body: h("div.col", { style: "gap:12px" },
          error,
          hint,
          h("div.row", { style: "gap:12px;flex-wrap:wrap" },
            field("机型", profileSelect),
            field("类型", kindSelect)
          ),
          field("已批准连接", connectionSelect),
          field("名称", nameInput),
          field("资源引用", refInput),
          field("业务路径", displayInput),
          catalogBlock,
          field("修改记录链接", recordInput),
          field("关键词", keywordInput),
          h("div.card", h("div.card-b", h("div.col", { style: "gap:8px" },
            h("strong.small", "目标绑定"),
            targetBlock
          ))),
          h("div.row", { style: "gap:18px;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用")),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "计划"), modeSelect)
          ),
          intervalWrap,
          casNote,
          reloadButton
        ),
        footer: function (close) {
          function tryClose() {
            if (!isDirtyNow()) { activeEditorDirty = false; close(); return; }
            MC.confirm({
              title: "保留这次修改？",
              message: "抽屉里有未保存的内容。保留会留在编辑框里；放弃会丢掉这些修改。",
              confirmText: "放弃修改",
              cancelText: "保留",
              danger: true,
            }).then(function (ok) {
              if (ok) { activeEditorDirty = false; close(); }
            });
          }
          requestDismiss = tryClose;
          return [
            h("button.btn", { type: "button", "data-sync-cancel": "1", onclick: tryClose }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-sync-save": "1",
              onclick: function () {
                var payload = buildPayload();
                var problem = validate(payload);
                if (problem) { showError(problem); return; }
                showError("");
                reloadButton.style.display = "none";
                var request = isCreate
                  ? MC.api.requestV3("POST", sourcesPath(), { body: payload })
                  : MC.api.requestV3("PATCH", sourcePath(source), {
                    body: payload,
                    headers: { "If-Match": String(source.revision) },
                  });
                request.then(function (data) {
                  activeEditorDirty = false;
                  close();
                  MC.toast.success(isCreate ? "更新源已创建" : "更新源已保存");
                  if (!isCreate && data && data.source_id) {
                    // 局部刷新：保留当前搜索/分页/排序，只替换这一行。
                    var items = state.sources.items;
                    for (var i = 0; i < items.length; i += 1) {
                      if (items[i].source_id === data.source_id) {
                        items[i] = data;
                        if (state.tab === "sources") renderBody();
                        return;
                      }
                    }
                  }
                  loadSources(true);
                }, function (err) {
                  if (err && err.code === "REVISION_CONFLICT") {
                    showError("该来源已在其他客户端更新（revision 冲突）；草稿已保留，可先重新载入最新版本再保存。");
                    if (!isCreate) reloadButton.style.display = "";
                    return;
                  }
                  showError(errorText(err));
                  // 网络类失败：保留草稿，允许直接重试保存（同一按钮）。
                  if (err && err.code === "NETWORK") hint.textContent = "网络不可用；恢复后可直接再次点击保存重试。";
                });
              },
            }, isCreate ? "创建" : "保存"),
          ];
        },
        onClose: function () { activeEditorDirty = false; },
      });
      baseline = readDraft();
      activeEditorDirty = false;
      syncConditional();
      fillConnections();
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

    function dropSourceRow(sourceId) {
      var S = state.sources;
      S.items = S.items.filter(function (row) { return row.source_id !== sourceId; });
      S.total = Math.max(0, S.total - 1);
      delete S.selected[sourceId];
      if (state.tab === "sources") renderBody();
    }

    function deleteSource(source) {
      MC.confirm({
        title: "删除更新源「" + source.name + "」？",
        message: "删除只写 tombstone：来源不再参与同步与预览，但 FileResource、文件版本、历史记录与归档都会保留。",
        confirmText: "删除来源",
        danger: true,
      }).then(function (ok) {
        if (!ok) return;
        MC.api.requestV3("DELETE", sourcePath(source), {
          headers: { "If-Match": String(source.revision) },
        }).then(function () {
          MC.toast.success("更新源已删除");
          // 删除后保持当前搜索/筛选/分页，只局部移除该行。
          dropSourceRow(source.source_id);
        }, function (err) {
          MC.toast.error("删除失败", errorText(err));
        });
      });
    }

    function testSource(source, button) {
      var S = state.sources;
      if (S.testing[source.source_id]) return;
      S.testing[source.source_id] = true;
      if (button) button.disabled = true;
      function finish(text, ok) {
        S.testing[source.source_id] = false;
        S.testResults[source.source_id] = { text: text, ok: ok };
        if (button) button.disabled = false;
        if (state.tab === "sources") renderBody();
      }
      if (source.enabled === false) {
        finish("已停用：不参与范围解析，启用后再测试。", false);
        return;
      }
      // 只读预览：服务端解析绑定/连接并冻结 read_set，不下载、不写文件。
      MC.api.requestV3("POST", previewsPath(), {
        body: { scope: "selected_sources", source_ids: [source.source_id] },
      }).then(function (preview) {
        var excluded = ((preview && preview.excluded) || []).filter(function (row) {
          return row.source_id === source.source_id;
        })[0];
        if (excluded) {
          var reason = EXCLUDED_REASON[excluded.reason_code] || excluded.reason_code || "未知原因";
          finish("不可解析：" + reason + (excluded.field ? "（" + excluded.field + "）" : ""), false);
          return;
        }
        var item = ((preview && preview.items) || []).filter(function (row) {
          return row.source_id === source.source_id;
        })[0];
        if (!item) { finish("未进入本次 read_set（检查启用状态与筛选条件）。", false); return; }
        var binding = item.binding || {};
        var target = (item.target && item.target.kind) || "未绑定目标";
        finish("可解析：目标 " + (TARGET_LABEL[target] || target) +
          (binding.file_id ? " · 文件 " + binding.file_id : "") +
          (binding.release_id ? " · 版本 " + binding.release_id : ""), true);
      }, function (err) {
        finish("测试失败：" + errorText(err), false);
      });
    }

    function selectedSourceRows() {
      var S = state.sources;
      return S.items.filter(function (row) { return S.selected[row.source_id]; });
    }

    function batchSources(action) {
      var rows = selectedSourceRows().filter(function (row) { return !row.deleted; });
      var S = state.sources;
      if (!rows.length || S.busy) return;
      var label = action === "enable" ? "启用" : (action === "disable" ? "停用" : "删除");
      MC.confirm({
        title: "批量" + label + " " + rows.length + " 个来源？",
        message: action === "delete"
          ? "删除只写 tombstone，FileResource、文件版本与历史会保留。"
          : "逐个按各自 revision 做 CAS，失败项会单独列出，不影响其他项。",
        confirmText: "批量" + label,
        danger: action === "delete",
      }).then(function (ok) {
        if (!ok) return;
        S.busy = action;
        if (state.tab === "sources") renderBody();
        var failures = [];
        var seq = Promise.resolve();
        rows.forEach(function (row) {
          seq = seq.then(function () {
            if (action === "delete") {
              return MC.api.requestV3("DELETE", sourcePath(row), {
                headers: { "If-Match": String(row.revision) },
              }).then(function () { delete S.selected[row.source_id]; }, function (err) {
                failures.push(row.name + "：" + errorText(err));
              });
            }
            return MC.api.requestV3("PATCH", sourcePath(row), {
              headers: { "If-Match": String(row.revision) },
              body: { enabled: action === "enable" },
            }).then(function (data) {
              row.enabled = data.enabled;
              row.revision = data.revision;
              delete S.selected[row.source_id];
            }, function (err) {
              failures.push(row.name + "：" + errorText(err));
            });
          });
        });
        seq.then(function () {
          S.busy = "";
          if (failures.length) {
            MC.toast.error("批量" + label + "部分失败（" + failures.length + "/" + rows.length + "）", failures.join("；"));
          } else {
            MC.toast.success("已批量" + label + " " + rows.length + " 个来源");
          }
          loadSources(true);
        });
      });
    }

    function rerenderPlan() {
      if (state.sheet === "plan") paintSheet();
    }

    function loadPlan() {
      var P = state.plan;
      if (P.loading) return Promise.resolve();
      var wid = workspaceId();
      P.loading = true;
      P.loadedFor = wid;
      P.error = "";
      rerenderPlan();
      return Promise.all([
        MC.api.requestV3("GET", policyPath()),
        MC.api.requestV3("GET", sourcesPath(), { query: { status: "all", limit: 200 } }),
      ]).then(function (result) {
        if (P.loadedFor !== workspaceId()) return;
        P.policy = result[0] || {};
        P.draft = draftFromPolicy(P.policy);
        P.sources = (result[1] && result[1].items) || [];
        P.loading = false;
        if (typeof paintChrome === "function") paintChrome();
        P.error = "";
        rerenderPlan();
      }, function (err) {
        if (P.loadedFor !== workspaceId()) return;
        P.loading = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function setOverride(pid, key, value) {
      var P = state.plan;
      if (!P.draft) return;
      var item = P.draft.overrides[pid] || {};
      if (value === null || value === undefined || value === "") delete item[key];
      else item[key] = value;
      if (item.enabled === undefined && item.interval_hours === undefined) {
        delete P.draft.overrides[pid];
      } else {
        P.draft.overrides[pid] = item;
      }
    }

    function buildPolicyPayload() {
      var draft = state.plan.draft;
      var hours = Number(draft.default_interval_hours);
      if (!isFinite(hours) || hours < 1 || hours > 2160) {
        return { error: "默认间隔必须在 1–2160 小时" };
      }
      var overrides = {};
      var bad = "";
      Object.keys(draft.overrides).forEach(function (pid) {
        var item = draft.overrides[pid] || {};
        var clean = {};
        if (item.enabled === true || item.enabled === false) clean.enabled = item.enabled;
        if (item.interval_hours !== undefined && item.interval_hours !== null && String(item.interval_hours).trim() !== "") {
          var value = Number(item.interval_hours);
          if (!isFinite(value) || value < 1 || value > 2160) {
            bad = "机型覆盖间隔必须在 1–2160 小时";
          } else {
            clean.interval_hours = value;
          }
        }
        if (Object.keys(clean).length) overrides[pid] = clean;
      });
      if (bad) return { error: bad };
      return {
        body: {
          enabled: !!draft.enabled,
          default_interval_hours: hours,
          profile_overrides: overrides,
        },
      };
    }

    function savePlan() {
      var P = state.plan;
      if (!canSavePlan() || P.saving || !P.draft) return;
      var built = buildPolicyPayload();
      if (built.error) {
        P.error = built.error;
        rerenderPlan();
        return;
      }
      var revision = P.policy && P.policy.revision != null ? P.policy.revision : 0;
      P.saving = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("PUT", policyPath(), {
        body: built.body,
        headers: { "If-Match": String(revision) },
      }).then(function (policy) {
        P.policy = policy || P.policy;
        P.draft = draftFromPolicy(P.policy);
        P.saving = false;
        P.error = "";
        MC.toast.success("更新计划已保存");
        rerenderPlan();
      }, function (err) {
        P.saving = false;
        // 409：不覆盖 P.draft，输入框仍显示用户当前草稿。
        P.error = err && err.code === "REVISION_CONFLICT"
          ? "计划已在其他客户端更新；当前草稿已保留，请刷新后重试"
          : planErrorText(err);
        rerenderPlan();
      });
    }

    function requestPlanPreview() {
      var P = state.plan;
      if (!canSavePlan() || P.previewing) return;
      // 失败筛选只影响下方列表显示；预览请求始终冻结 workspace_all 全量 read_set。
      var wid = workspaceId();
      P.previewing = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", previewsPath(), { body: { scope: "workspace_all" } }).then(function (preview) {
        P.previewing = false;
        if (!preview || P.loadedFor !== workspaceId()) return;
        P.preview = preview;
        P.previewWorkspaceId = wid;
        P.error = "";
        rerenderPlan();
      }, function (err) {
        P.previewing = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function validatePlanPreview() {
      var P = state.plan;
      if (!canSavePlan() || !P.preview || P.validating) return;
      var wid = P.previewWorkspaceId || workspaceId();
      var path = "/api/v3/workspaces/" + encodeURIComponent(wid)
        + "/sync/previews/" + encodeURIComponent(P.preview.preview_id) + "/validate";
      P.validating = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", path, { body: {} }).then(function (fresh) {
        P.validating = false;
        if (fresh) {
          P.preview = fresh;
          P.previewWorkspaceId = wid;
        }
        MC.toast.success("预览仍然有效");
        rerenderPlan();
      }, function (err) {
        P.validating = false;
        P.error = planErrorText(err);
        rerenderPlan();
      });
    }

    function rememberFailures(runs) {
      (runs || []).forEach(function (entry) {
        var task = entry && entry.task;
        var items = (task && task.result && task.result.items) || [];
        items.forEach(function (item) {
          if (item && item.source_id && FAILED_ITEM_STATUSES.indexOf(item.status) >= 0) {
            state.plan.failedSourceIds[item.source_id] = true;
          }
        });
      });
    }

    function runWorkspaceAll() {
      var P = state.plan;
      if (!canRunPlan() || P.running) return;
      var wid = workspaceId();
      var body = { scope: "workspace_all" };
      if (P.preview && P.preview.preview_id && P.previewWorkspaceId === wid) {
        body.preview_id = P.preview.preview_id;
      }
      P.running = true;
      P.error = "";
      rerenderPlan();
      MC.api.requestV3("POST", runsPath(), { body: body }).then(function (data) {
        P.running = false;
        var runs = (data && data.runs) || [];
        runs.forEach(function (entry) {
          RUN_LOG.push({
            workspace_id: wid,
            profile_id: entry.profile_id,
            run: entry.run,
            task: entry.task,
          });
          if (MC.bus) MC.bus.emit("sync-run-started", entry.run);
        });
        while (RUN_LOG.length > RUN_LOG_MAX) RUN_LOG.shift();
        rememberFailures(runs);
        P.error = "";
        if (!runs.length) MC.toast.info("没有可更新的来源");
        else MC.toast.success("已提交全量更新", "共 " + runs.length + " 个机型任务");
        rerenderPlan();
      }, function (err) {
        P.running = false;
        P.error = err && err.code === "REVISION_CONFLICT"
          ? "预览或计划已变化，未提交任务；请重新预览后重试"
          : planErrorText(err);
        rerenderPlan();
      });
    }

    function renderPlan() {
      var P = state.plan;
      var save = canSavePlan();
      var run = canRunPlan();
      var draft = P.draft || draftFromPolicy(P.policy || {});
      var profiles = workspaceProfiles();

      if (P.error) body.appendChild(h("div.form-error", P.error));
      if (P.loading && !P.policy) {
        body.appendChild(h("p.muted", "正在读取更新计划…"));
        return;
      }
      if (!P.policy) {
        body.appendChild(h("div.card", h("div.card-b",
          h("p.muted", "暂时无法读取更新计划。"),
          h("button.btn.btn-sm", { type: "button", "data-plan-refresh": "1", onclick: loadPlan }, "刷新")
        )));
        return;
      }
      if (!save) {
        body.appendChild(h("p.small.muted", { "data-plan-readonly": "1" }, "只读身份：可查看更新计划，不能保存或启动任务。"));
      }

      var enabledInput = h("input", {
        type: "checkbox",
        checked: draft.enabled,
        disabled: !save,
        "data-plan-enabled": "1",
      });
      enabledInput.addEventListener("change", function () {
        draft.enabled = !!enabledInput.checked;
      });
      var intervalInput = h("input.input", {
        type: "number",
        min: "1",
        max: "2160",
        value: String(draft.default_interval_hours),
        disabled: !save,
        "data-plan-interval": "1",
        style: "max-width:120px",
      });
      intervalInput.addEventListener("change", function () {
        draft.default_interval_hours = intervalInput.value;
      });

      var overrideRows = profiles.map(function (profile) {
        var item = draft.overrides[profile.id] || {};
        var select = h("select.input", {
          disabled: !save,
          "data-plan-override-enabled": profile.id,
        },
          h("option", { value: "" }, "继承工作区"),
          h("option", { value: "true" }, "启用"),
          h("option", { value: "false" }, "暂停")
        );
        select.value = item.enabled === true ? "true" : (item.enabled === false ? "false" : "");
        select.addEventListener("change", function () {
          setOverride(profile.id, "enabled", select.value === "" ? null : select.value === "true");
        });
        var overrideInterval = h("input.input", {
          type: "number",
          min: "1",
          max: "2160",
          placeholder: "继承默认",
          value: item.interval_hours == null ? "" : String(item.interval_hours),
          disabled: !save,
          "data-plan-override-interval": profile.id,
          style: "max-width:120px",
        });
        overrideInterval.addEventListener("change", function () {
          setOverride(profile.id, "interval_hours", overrideInterval.value.trim());
        });
        return h("tr", { "data-plan-profile": profile.id },
          h("td", h("div", h("strong", profile.name)), h("div.xsmall.muted", profile.id)),
          h("td", select),
          h("td", overrideInterval)
        );
      });

      var planActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;margin-top:10px" });
      if (save) {
        planActions.appendChild(h("button.btn.btn-primary", {
          type: "button",
          "data-plan-save": "1",
          disabled: P.saving,
          onclick: savePlan,
        }, P.saving ? "保存中…" : "保存计划"));
      }
      planActions.appendChild(h("button.btn.btn-sm", {
        type: "button",
        "data-plan-refresh": "1",
        onclick: loadPlan,
      }, "刷新"));
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "自动更新计划"), h("span.muted.small", "工作区 " + workspaceId())),
        h("div.card-b",
          h("div.row", { style: "gap:18px;flex-wrap:wrap;align-items:center" },
            h("label.row", { style: "gap:6px;align-items:center" },
              enabledInput, h("span", "启用自动更新")),
            h("div.row", { style: "gap:6px;align-items:center" },
              h("span.small.muted", "默认间隔"),
              intervalInput,
              h("span.small.muted", "小时")
            )
          ),
          h("p.xsmall.muted", "暂停自动更新只影响自动投递；手动全量更新仍可随时启动。"),
          h("table.table", { style: "margin-top:10px" },
            h("thead", h("tr", h("th", "机型"), h("th", "自动状态覆盖"), h("th", "间隔覆盖（小时）"))),
            h("tbody", overrideRows)
          ),
          planActions
        )
      ));

      var pv = P.preview;
      var previewActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      if (save) {
        previewActions.appendChild(h("button.btn.btn-primary", {
          type: "button",
          "data-plan-preview": "1",
          disabled: P.previewing,
          onclick: requestPlanPreview,
        }, P.previewing ? "预览中…" : "全量预览"));
      }
      var failures = h("input", {
        type: "checkbox",
        checked: P.failuresOnly,
        disabled: !save,
        "data-plan-failures": "1",
      });
      failures.addEventListener("change", function () {
        P.failuresOnly = !!failures.checked;
        rerenderPlan();
      });
      previewActions.appendChild(h("label.row", { style: "gap:6px;align-items:center" },
        failures, h("span.small", "仅显示失败来源")));
      if (save && pv) {
        previewActions.appendChild(h("button.btn.btn-sm", {
          type: "button",
          "data-plan-validate": "1",
          disabled: P.validating,
          onclick: validatePlanPreview,
        }, P.validating ? "校验中…" : "验证预览"));
      }

      var previewBody = h("div.card-b", previewActions);
      if (pv) {
        var fingerprint = pv.read_set_sha256 ? String(pv.read_set_sha256).slice(0, 16) : "—";
        previewBody.appendChild(h("p.small", { "data-plan-read-set": pv.read_set_sha256 || "" },
          "冻结来源 " + (pv.total || 0) + " 个 · 排除 " + ((pv.excluded || []).length)
          + " 个 · read_set " + fingerprint
        ));
        previewBody.appendChild(h("p.xsmall.muted",
          "失败筛选只影响本页显示；冻结 read_set 始终按 workspace_all 请求，不会缩成失败子集。"
        ));
        var rows = (pv.items || []).slice();
        if (P.failuresOnly) {
          rows = rows.filter(function (item) { return !!P.failedSourceIds[item.source_id]; });
        }
        if (!rows.length) {
          previewBody.appendChild(h("p.muted.small", P.failuresOnly
            ? "当前没有记录到失败来源；全量 read_set 仍包含上面的 " + (pv.total || 0) + " 个来源。"
            : "预览中没有可用来源。"));
        } else {
          rows.forEach(function (item) {
            previewBody.appendChild(h("div.row", {
              "data-plan-preview-source": item.source_id,
              style: "gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--border)",
            },
              h("span.small", item.name || item.source_id),
              h("span.xsmall.muted", (item.profile_id || "") + " · " + (item.schedule_mode || "inherit"))
            ));
          });
        }
      } else {
        previewBody.appendChild(h("p.muted.small", "尚未预览；全量预览会冻结所有机型下已启用来源。"));
      }
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "全量预览"), h("span.muted.small", "workspace_all")),
        previewBody
      ));

      var runRows = RUN_LOG.slice().reverse();
      var runBody = h("div.card-b");
      var runActions = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      if (run) {
        runActions.appendChild(h("button.btn.btn-primary", {
          type: "button",
          "data-plan-run": "1",
          disabled: P.running,
          onclick: runWorkspaceAll,
        }, P.running ? "提交中…" : "手动全量更新"));
      }
      runActions.appendChild(h("span.xsmall.muted", draft.enabled
        ? "自动计划已启用；手动任务独立提交。"
        : "自动计划已暂停；手动任务仍可启动。"));
      runBody.appendChild(runActions);
      if (runRows.length) {
        runBody.appendChild(h("p.small", { "data-plan-run-wid": runRows[0].workspace_id },
          "最近任务归属工作区：" + runRows[0].workspace_id));
        runRows.slice(0, 10).forEach(function (entry) {
          var runInfo = entry.run || {};
          var task = entry.task || {};
          runBody.appendChild(h("div.row", {
            "data-plan-run-row": entry.workspace_id,
            style: "gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--border)",
          },
            h("span.small", entry.profile_id),
            h("span.xsmall.muted", "工作区 " + entry.workspace_id),
            h("span.badge", runInfo.trigger || "manual"),
            h("span.small", runInfo.state || task.state || "queued"),
            h("span.xsmall.muted", runInfo.run_id || task.id || "")
          ));
        });
      } else {
        runBody.appendChild(h("p.muted.small", "尚未提交手动任务。"));
      }
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "手动全量更新"), h("span.muted.small", "按机型分组投递 sync_run")),
        runBody
      ));
    }

    /* ===================== M16-34 连接设置 / 保活 ===================== */

    var CREDENTIAL_LABELS = {
      unconfigured: "未配置",
      valid: "有效",
      locked: "已锁定",
      invalid: "无效",
    };
    var CREDENTIAL_ERROR_TEXT = {
      CONFIG_DISABLED: "凭据存储未配置",
      FORBIDDEN: "需要管理员能力与运维授权",
      INVALID_INPUT: "输入不符合要求",
      REVISION_CONFLICT: "状态已变化，请刷新后重试",
      NOT_FOUND: "连接或凭据候选不存在",
      STORAGE_FAILURE: "服务器写入失败，凭据未改变",
      BUSY: "该连接已有操作在进行",
    };
    var OPERATION_STATES = { queued: "排队中", running: "执行中", succeeded: "已完成", failed: "失败", blocked: "已阻止" };
    var OPERATION_REASONS = {
      disabled: "保活未启用",
      collector_offline: "collector 未连接",
      bad_credentials: "密码错误",
      locked: "凭据已锁定",
      auth_failed: "认证失败",
      network_error: "网络错误",
      revision_conflict: "revision 已变化",
      source_changed: "认证纪元已变化",
    };

    function connectionsPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/connections";
    }

    function connectionPath(cid) {
      return connectionsPath() + "/" + encodeURIComponent(cid);
    }

    function heartbeatPath(cid) {
      return connectionPath(cid) + "/heartbeat";
    }

    function canManageCredentials() {
      return !!(app && app.can && app.can("integration_credentials_manage"));
    }

    function credentialLabel(cred) {
      if (!cred || !cred.state) return "未配置";
      return CREDENTIAL_LABELS[cred.state] || String(cred.state);
    }

    function credentialBadge(cred) {
      if (cred && cred.state === "valid") return "badge badge-success";
      if (cred && cred.state === "locked") return "badge badge-danger";
      if (cred && cred.state && cred.state !== "unconfigured") return "badge badge-warning";
      return "badge badge-count";
    }

    function credentialErrorText(err) {
      var code = (err && err.code) || (err && err.status ? "HTTP_" + err.status : "UNKNOWN");
      var label = CREDENTIAL_ERROR_TEXT[code];
      return "凭据操作失败：" + (label ? label + "（" + code + "）" : code);
    }

    function heartbeatStatusText(hb) {
      if (!hb) return "未能读取保活状态";
      var parts = [hb.enabled ? "已启用" : "已停用"];
      parts.push("间隔 " + (hb.interval_minutes || 10) + " 分钟");
      if (hb.last_heartbeat_at) parts.push("上次 " + hb.last_heartbeat_at);
      if (hb.last_result) parts.push(hb.last_result === "success" ? "成功" : "失败");
      if (hb.reason_code) parts.push("原因 " + hb.reason_code);
      if (hb.next_due_at) parts.push("下次到期 " + hb.next_due_at);
      if (hb.heartbeat_validated) parts.push("已通过真实 idle 验证");
      else parts.push("未通过真实 idle 验证");
      return parts.join(" · ");
    }

    /** 自动重登没有独立服务端开关：由凭据状态/失败原因推导，只读展示。 */
    function autoReloginText(conn) {
      var cred = (conn && conn.credential) || {};
      if (cred.state === "valid") {
        return "自动重登：凭据有效，由 collector 在会话失效后续登；与保活开关相互独立。";
      }
      if (cred.state === "locked") {
        return "自动重登：已暂停（凭据锁定）；需人工更换凭据后恢复，系统不会自动重试。";
      }
      if (cred.last_error) {
        return "自动重登：已暂停（" + cred.last_error + "）；需人工重试或更换凭据。";
      }
      return "自动重登：未配置凭据，暂不自动重登；与保活开关相互独立。";
    }

    function refreshConnectionsView() {
      if (state.sheet !== "connections") return;
      paintSheet();
    }

    function updateCredential(cid, credential) {
      if (!credential) return;
      state.connections.items = state.connections.items.map(function (item) {
        if (item.connection_id !== cid) return item;
        return Object.assign({}, item, { credential: credential });
      });
    }

    function operationNote(cid) {
      var op = state.connections.operations[cid];
      if (!op) return null;
      var stateText = OPERATION_STATES[op.state] || op.state;
      var reason = op.reason_code ? (OPERATION_REASONS[op.reason_code] || op.reason_code) : "";
      return h("div.small.muted", { "data-sync-operation": cid },
        "最近操作：" + stateText + (op.kind ? " · " + op.kind : "") + (reason ? " · " + reason : "")
      );
    }

    function trackOperation(cid, op) {
      if (!op || !op.op_id) return;
      var C = state.connections;
      C.operations[cid] = op;
      var terminal = op.state !== "queued" && op.state !== "running";
      if (terminal) {
        var result = op.result || {};
        if (result.heartbeat) C.heartbeats[cid] = result.heartbeat;
        if (result.credential) updateCredential(cid, result.credential);
      }
      refreshConnectionsView();
      if (terminal) return;
      var attempts = C.pollAttempts[cid] || 0;
      if (attempts >= 10) return;
      C.pollAttempts[cid] = attempts + 1;
      var timer = setTimeout(function () {
        if (C.loadedFor !== workspaceId()) return;
        MC.api.requestV3("GET", connectionPath(cid) + "/operations/" + encodeURIComponent(op.op_id)).then(function (data) {
          if (C.loadedFor !== workspaceId()) return;
          trackOperation(cid, data && data.operation);
        }, function (err) {
          if (C.loadedFor !== workspaceId()) return;
          C.error = errorText(err);
          refreshConnectionsView();
        });
      }, 700);
      C.pollTimers.push(timer);
    }

    function loadConnections(reset) {
      var C = state.connections;
      if (C.loading && C.loadedFor === workspaceId()) return Promise.resolve();
      var wid = workspaceId();
      var seq = (C.loadSeq || 0) + 1;
      C.loadSeq = seq;
      C.loading = true;
      C.error = "";
      C.loadedFor = wid;
      if (reset) {
        C.items = [];
        C.heartbeats = {};
        C.heartbeatErrors = {};
        C.operations = {};
        C.pollAttempts = {};
      }
      function current() {
        return seq === C.loadSeq && C.loadedFor === workspaceId();
      }
      refreshConnectionsView();
      return MC.api.requestV3("GET", connectionsPath()).then(function (data) {
        if (!current()) return;
        C.items = (data && data.items) || [];
        var jobs = C.items.map(function (item) {
          var cid = item.connection_id;
          return MC.api.requestV3("GET", heartbeatPath(cid)).then(function (hb) {
            if (!current()) return;
            C.heartbeats[cid] = hb;
            delete C.heartbeatErrors[cid];
          }, function (err) {
            if (!current()) return;
            C.heartbeats[cid] = null;
            C.heartbeatErrors[cid] = errorText(err);
          });
        });
        return Promise.all(jobs).then(function () {
          if (!current()) return;
          C.loading = false;
          refreshConnectionsView();
          if (typeof paintChrome === "function") paintChrome();
        });
      }, function (err) {
        if (!current()) return;
        C.loading = false;
        C.error = errorText(err);
        refreshConnectionsView();
      });
    }

    function saveHeartbeat(conn, enabledInput, intervalInput, tokenInput, errorNode, autoInput) {
      var cid = conn.connection_id;
      var hb = state.connections.heartbeats[cid] || { revision: 0 };
      var interval = Number(intervalInput.value);
      if (!isFinite(interval) || interval < 1 || interval > 60) {
        errorNode.textContent = "保活间隔必须在 1–60 分钟";
        errorNode.style.display = "";
        return;
      }
      var token = tokenInput.value.trim();
      if (!token) {
        errorNode.textContent = "请输入运维 token";
        errorNode.style.display = "";
        return;
      }
      errorNode.style.display = "none";
      MC.api.requestV3("PUT", heartbeatPath(cid), {
        body: {
          interval_minutes: interval,
          enabled: !!enabledInput.checked,
          auto_relogin: !!(autoInput && autoInput.checked),
        },
        headers: { "If-Match": String(hb.revision == null ? 0 : hb.revision), "X-Operator-Token": token },
      }).then(function (saved) {
        state.connections.heartbeats[cid] = saved;
        tokenInput.value = "";
        MC.toast.success("保活设置已保存");
        refreshConnectionsView();
      }, function (err) {
        errorNode.textContent = errorText(err);
        errorNode.style.display = "";
      });
    }

    function runHeartbeat(conn, tokenInput, errorNode) {
      var cid = conn.connection_id;
      var token = tokenInput.value.trim();
      if (!token) {
        errorNode.textContent = "请输入运维 token";
        errorNode.style.display = "";
        return;
      }
      errorNode.style.display = "none";
      MC.api.requestV3("POST", heartbeatPath(cid) + "/run", {
        body: {},
        headers: { "X-Operator-Token": token },
      }).then(function (data) {
        var op = data && data.operation;
        if (!op) {
          errorNode.textContent = "保活运行未返回操作状态";
          errorNode.style.display = "";
          return;
        }
        trackOperation(cid, op);
        if (op.state === "blocked") {
          MC.toast.warning("保活未执行", OPERATION_REASONS[op.reason_code] || op.reason_code || "");
        } else if (op.state === "failed") {
          MC.toast.error("保活失败", OPERATION_REASONS[op.reason_code] || op.reason_code || "");
        } else {
          MC.toast.success("保活已执行");
        }
      }, function (err) {
        errorNode.textContent = errorText(err);
        errorNode.style.display = "";
      });
    }

    function openCreateConnection() {
      var nameInput = h("input.input", { autofocus: true, "data-conn-name": "1" });
      var originInput = h("input.input", { placeholder: "SDA Origin（部署端内网地址）", "data-conn-origin": "1" });
      var enabledInput = h("input", { type: "checkbox", checked: true, "data-conn-enabled": "1" });
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-conn-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-conn-error": "1" });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "新建连接",
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("SDA Origin", originInput),
          h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用连接")),
          field("运维 token（仅本次输入，不保存）", tokenInput),
          h("p.xsmall.muted", "连接只保存名称、Origin、协议与启用状态，不内嵌任何密码/Cookie。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-conn-create-submit": "1",
              onclick: function () {
                var name = nameInput.value.trim();
                var origin = originInput.value.trim();
                var token = tokenInput.value.trim();
                if (!name || !origin) { showError("名称与 Origin 不能为空"); return; }
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                MC.api.requestV3("POST", connectionsPath(), {
                  body: { name: name, origin: origin, enabled: !!enabledInput.checked },
                  headers: { "X-Operator-Token": token },
                }).then(function () {
                  close();
                  MC.toast.success("连接已创建");
                  loadConnections(true);
                }, function (err) {
                  showError(errorText(err));
                });
              },
            }, "创建"),
          ];
        },
        onClose: function () { tokenInput.value = ""; },
      });
      return modal;
    }

    function openConnectionEditor(conn) {
      var nameInput = h("input.input", { value: conn.name || "", autofocus: true, "data-conn-name": "1" });
      var originInput = h("input.input", { value: conn.origin || "", "data-conn-origin": "1" });
      var enabledInput = h("input", { type: "checkbox", checked: conn.enabled !== false, "data-conn-enabled": "1" });
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-conn-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-conn-error": "1" });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "编辑连接",
        subtitle: conn.name + " · " + conn.connection_id,
        body: h("div.col", { style: "gap:12px" },
          error,
          field("名称", nameInput),
          field("SDA Origin", originInput),
          h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用连接")),
          field("运维 token（仅本次输入，不保存）", tokenInput),
          h("p.xsmall.muted", "保存使用连接 revision " + conn.policy_revision + " 做 CAS；过期时 409 并保留当前输入。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-conn-save": "1",
              onclick: function () {
                var name = nameInput.value.trim();
                var origin = originInput.value.trim();
                var token = tokenInput.value.trim();
                if (!name || !origin) { showError("名称与 Origin 不能为空"); return; }
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                MC.api.requestV3("PATCH", connectionPath(conn.connection_id), {
                  body: { name: name, origin: origin, enabled: !!enabledInput.checked },
                  headers: { "If-Match": String(conn.policy_revision), "X-Operator-Token": token },
                }).then(function (updated) {
                  var credential = conn.credential;
                  state.connections.items = state.connections.items.map(function (item) {
                    if (item.connection_id !== conn.connection_id) return item;
                    return Object.assign({}, item, updated || {}, { credential: credential });
                  });
                  close();
                  MC.toast.success("连接已保存");
                  refreshConnectionsView();
                }, function (err) {
                  showError(errorText(err));
                });
              },
            }, "保存"),
          ];
        },
        onClose: function () { tokenInput.value = ""; },
      });
      return modal;
    }

    function openCredentialForm(conn) {
      var usernameInput = h("input.input", { autofocus: true, autocomplete: "off", "data-cred-username": "1" });
      var passwordInput = h("input.input", { type: "password", autocomplete: "new-password", "data-cred-password": "1" });
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-cred-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-cred-error": "1" });
      var cred = conn.credential || {};

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: "更换凭据",
        subtitle: conn.name + " · 当前状态 " + credentialLabel(cred),
        body: h("div.col", { style: "gap:12px" },
          error,
          field("账号", usernameInput),
          field("密码", passwordInput),
          field("运维 token（仅本次输入，不保存）", tokenInput),
          h("p.xsmall.muted", "提交只创建待验证候选；验证/激活需人工单独触发。失败时密码框清空，且不会回显明文。")
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-cred-submit": "1",
              onclick: function () {
                var username = usernameInput.value.trim();
                var password = passwordInput.value;
                var token = tokenInput.value.trim();
                if (!username || !password) { showError("账号与密码不能为空"); return; }
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                MC.api.requestV3("POST", connectionPath(conn.connection_id) + "/credentials/candidates", {
                  body: { username: username, password: password },
                  headers: { "X-Operator-Token": token },
                }).then(function (statePayload) {
                  passwordInput.value = "";
                  updateCredential(conn.connection_id, statePayload);
                  close();
                  MC.toast.success("已创建待验证凭据候选");
                  refreshConnectionsView();
                }, function (err) {
                  // 失败只显示安全错误码；密码框立即清空，绝不把明文带回界面。
                  passwordInput.value = "";
                  showError(credentialErrorText(err));
                });
              },
            }, "创建候选"),
          ];
        },
        onClose: function () {
          passwordInput.value = "";
          tokenInput.value = "";
        },
      });
      return modal;
    }

    function openOperatorAction(conn, title, description, submitLabel, action) {
      var tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", autofocus: true, "data-cred-token": "1" });
      var error = h("div.form-error", { style: "display:none", "data-cred-error": "1" });

      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }

      var modal = MC.modal({
        title: title,
        subtitle: conn.name,
        body: h("div.col", { style: "gap:12px" },
          error,
          h("p.small", description),
          field("运维 token（仅本次输入，不保存）", tokenInput)
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-cred-action-submit": "1",
              onclick: function () {
                var token = tokenInput.value.trim();
                if (!token) { showError("请输入运维 token"); return; }
                showError("");
                action(token).then(function (op) {
                  close();
                  if (op) trackOperation(conn.connection_id, op);
                }, function (err) {
                  showError(credentialErrorText(err));
                });
              },
            }, submitLabel),
          ];
        },
        onClose: function () { tokenInput.value = ""; },
      });
      return modal;
    }

    function openProbe(conn) {
      var candidate = (conn.credential || {}).candidate || {};
      return openOperatorAction(
        conn,
        "验证凭据",
        "验证只提交一次探测；密码错误会锁存凭据，需要人工重试或更换凭据，不会自动重复尝试。",
        "开始验证",
        function (token) {
          return MC.api.requestV3("POST", connectionPath(conn.connection_id) + "/credentials/probe", {
            body: { candidate_id: candidate.candidate_id },
            headers: { "X-Operator-Token": token },
          }).then(function (data) { return data && data.operation; });
        }
      );
    }

    function openActivate(conn) {
      var candidate = (conn.credential || {}).candidate || {};
      return openOperatorAction(
        conn,
        "激活凭据",
        "激活会覆盖当前有效凭据；probe_epoch 必须与候选一致，晚到的验证结果不能激活。",
        "激活",
        function (token) {
          return MC.api.requestV3(
            "POST",
            connectionPath(conn.connection_id) + "/credentials/candidates/"
              + encodeURIComponent(candidate.candidate_id) + "/activate",
            {
              body: { probe_epoch: candidate.auth_epoch == null ? 0 : candidate.auth_epoch },
              headers: { "X-Operator-Token": token },
            }
          ).then(function (data) { return data && data.operation; });
        }
      );
    }

    function renderHeartbeatCard(conn, manage) {
      var cid = conn.connection_id;
      var C = state.connections;
      var hb = C.heartbeats[cid];
      var cred = conn.credential || {};
      var error = h("div.form-error", { style: "display:none" });
      var controls = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" });
      var tokenInput = null;

      if (manage) {
        var enabledInput = h("input", { type: "checkbox", checked: !!(hb && hb.enabled), "data-hb-enabled": cid });
        var autoInput = h("input", {
          type: "checkbox",
          checked: !hb || hb.auto_relogin !== false,
          "data-auto-relogin-enabled": cid,
        });
        var intervalInput = h("input.input", {
          type: "number", min: "1", max: "60",
          value: String((hb && hb.interval_minutes) || 10),
          "data-hb-interval": cid,
          style: "max-width:100px",
        });
        tokenInput = h("input.input", { type: "password", autocomplete: "off", placeholder: "运维 token", "data-hb-token": cid, style: "max-width:180px" });
        controls.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, enabledInput, h("span", "启用保活")));
        controls.appendChild(h("label.row", { style: "gap:6px;align-items:center" }, autoInput, h("span", "失效后自动账密重登")));
        controls.appendChild(h("span.small.muted", "间隔"));
        controls.appendChild(intervalInput);
        controls.appendChild(h("span.small.muted", "分钟"));
        controls.appendChild(tokenInput);
        controls.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-hb-save": cid,
          onclick: function () { saveHeartbeat(conn, enabledInput, intervalInput, tokenInput, error, autoInput); },
        }, "保存保活"));
        controls.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-hb-run": cid,
          onclick: function () { runHeartbeat(conn, tokenInput, error); },
        }, "立即保活"));
      } else {
        controls.appendChild(h("span.small.muted", { "data-hb-readonly": cid },
          "只读身份：不能修改保活开关或立即运行。"));
      }

      var candidate = cred.candidate;
      return h("div.card", h("div.card-b",
        h("div.row", { style: "gap:10px;flex-wrap:wrap;align-items:center" },
          h("strong", "保活与自动重登"),
          h("span.muted.small", conn.name + " · " + cid)
        ),
        h("p.small", { "data-hb-status": cid }, heartbeatStatusText(hb)),
        C.heartbeatErrors[cid] ? h("p.form-error", C.heartbeatErrors[cid]) : null,
        h("p.small.muted", { "data-auto-relogin": cid }, autoReloginText(conn)),
        controls,
        error,
        (cred.state === "locked" || cred.last_error === "bad_credentials"
          || (C.operations[cid] && (C.operations[cid].reason_code === "bad_credentials" || C.operations[cid].reason_code === "locked")))
          ? h("p.small", { "data-cred-manual": cid }, "需人工重试/更换凭据：系统不会自动重复提交凭据。")
          : null,
        candidate && candidate.state === "pending"
          ? h("p.xsmall.muted", "候选 " + candidate.candidate_id + " 待验证；验证与激活需人工分别触发。")
          : null,
        operationNote(cid)
      ));
    }

    function renderConnections() {
      var C = state.connections;
      var manage = canManageCredentials();

      if (C.error) {
        body.appendChild(h("div.form-error", C.error));
      }
      if (C.loading && !C.items.length) {
        body.appendChild(h("p.muted", "正在读取连接与保活状态…"));
        return;
      }
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", "data-connections-refresh": "1", onclick: function () { loadConnections(true); } }, "刷新"));
      if (manage) {
        toolbar.appendChild(h("button.btn.btn-sm.btn-primary", {
          type: "button", "data-conn-create": "1",
          onclick: function () { openCreateConnection(); },
        }, "新建连接"));
      } else {
        body.appendChild(h("p.small.muted", { "data-connections-readonly": "1" },
          "只读身份：可查看连接、凭据与保活状态，不能编辑连接、更换凭据或修改保活。"));
      }

      if (!C.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar, h("p.muted", "当前工作区还没有连接。"))));
        return;
      }

      var tbody = h("tbody");
      C.items.forEach(function (conn) {
        var cred = conn.credential || {};
        var actions = h("td.row", { style: "gap:6px;flex-wrap:wrap" });
        if (manage) {
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button", "data-conn-edit": conn.connection_id,
            onclick: function () { openConnectionEditor(conn); },
          }, "编辑"));
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button", "data-conn-cred": conn.connection_id,
            onclick: function () { openCredentialForm(conn); },
          }, "更换凭据"));
          if (cred.candidate && cred.candidate.state === "pending") {
            actions.appendChild(h("button.btn.btn-sm", {
              type: "button", "data-cred-probe": conn.connection_id,
              onclick: function () { openProbe(conn); },
            }, "验证"));
            actions.appendChild(h("button.btn.btn-sm", {
              type: "button", "data-cred-activate": conn.connection_id,
              onclick: function () { openActivate(conn); },
            }, "激活"));
          }
        } else {
          actions.appendChild(h("span.muted.xsmall", "只读"));
        }
        tbody.appendChild(h("tr", { "data-connection": conn.connection_id },
          h("td", h("div", h("strong", conn.name)), h("div.xsmall.muted", conn.connection_id)),
          h("td", h("span.small", conn.origin || "—")),
          h("td", conn.enabled === false
            ? h("span.badge.badge-count", "已停用")
            : h("span.badge.badge-success", "已启用")),
          h("td", h("span", { class: credentialBadge(cred), "data-cred-state": conn.connection_id }, credentialLabel(cred))),
          h("td", h("span.small", { "data-cred-epoch": conn.connection_id }, String(cred.auth_epoch == null ? 0 : cred.auth_epoch))),
          h("td", h("span.xsmall.muted", { "data-cred-error": conn.connection_id }, cred.last_error || "—")),
          actions
        ));
      });

      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "连接"), h("span.muted.small", "工作区 " + workspaceId())),
        h("div.card-b", toolbar,
          h("table.table", h("thead", h("tr",
            h("th", "名称"), h("th", "Origin"), h("th", "启用"),
            h("th", "凭据状态"), h("th", "auth_epoch"), h("th", "最近错误"), h("th", "")
          )), tbody)
        )
      ));

      C.items.forEach(function (conn) {
        body.appendChild(renderHeartbeatCard(conn, manage));
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
      var refresh = h("button.btn.btn-sm", { type: "button", "data-sync-refresh": "1", onclick: function () { loadSources(true); } }, "刷新");
      toolbar.appendChild(search);
      toolbar.appendChild(searchBtn);
      toolbar.appendChild(status);
      toolbar.appendChild(sort);
      toolbar.appendChild(refresh);

      if (S.error) {
        // 失败也要保留工具栏：给显式重试入口，不让用户刷新整页。
        body.appendChild(h("div.form-error", { "data-sync-error": "1" },
          S.error + " ",
          h("button.btn.btn-sm", { type: "button", "data-sync-retry": "1", onclick: function () { loadSources(true); } }, "重试")
        ));
      }
      if (S.loading && !S.items.length) {
        body.appendChild(h("p.muted", "正在读取更新源…"));
        return;
      }
      var rows = sortedItems();
      var selectable = rows.filter(function (row) { return !row.deleted; });
      var selectedRows = selectable.filter(function (row) { return S.selected[row.source_id]; });
      var batchBar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px", "data-sync-batch": "1" });
      if (canWrite() && selectable.length) {
        batchBar.appendChild(h("span.small.muted", "已选 " + selectedRows.length + " / " + selectable.length));
        batchBar.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-sync-batch-enable": "1", disabled: !selectedRows.length || !!S.busy,
          onclick: function () { batchSources("enable"); },
        }, "批量启用"));
        batchBar.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-sync-batch-disable": "1", disabled: !selectedRows.length || !!S.busy,
          onclick: function () { batchSources("disable"); },
        }, "批量停用"));
        batchBar.appendChild(h("button.btn.btn-sm", {
          type: "button", "data-sync-batch-delete": "1", disabled: !selectedRows.length || !!S.busy,
          onclick: function () { batchSources("delete"); },
        }, "批量删除"));
        if (S.busy) batchBar.appendChild(h("span.xsmall.muted", "正在批量处理…"));
      }

      if (!S.items.length) {
        body.appendChild(h("div.card",
          h("div.card-b", toolbar,
            h("p.muted", S.status === "deleted" ? "没有已删除的来源。" : "当前工作区还没有更新源。"))
        ));
        return;
      }

      var headerBox = h("input", {
        type: "checkbox",
        "data-sync-select-all": "1",
        "aria-label": "全选当前页来源",
        checked: selectable.length > 0 && selectedRows.length === selectable.length,
      });
      headerBox.addEventListener("change", function () {
        selectable.forEach(function (row) {
          if (headerBox.checked) S.selected[row.source_id] = true;
          else delete S.selected[row.source_id];
        });
        renderBody();
      });

      var tbody = h("tbody");
      rows.forEach(function (source) {
        var actions = h("td.row", { style: "gap:6px;flex-wrap:wrap" });
        var deleted = !!source.deleted;
        if (!deleted && canWrite()) {
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-edit": source.source_id,
            onclick: function () { openEditor(source); },
          }, "设置"));
        }
        if (!deleted && canRun()) {
          var runBtn = h("button.btn.btn-sm", {
            type: "button",
            "data-sync-run": source.source_id,
            title: source.enabled === false ? "来源已停用：启用后才能更新" : "立即更新此来源",
            onclick: function (event) { runSource(source, event.currentTarget); },
          }, "更新");
          if (source.enabled === false) runBtn.disabled = true;
          actions.appendChild(runBtn);
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-test": source.source_id,
            disabled: !!S.testing[source.source_id],
            onclick: function (event) { testSource(source, event.currentTarget); },
          }, S.testing[source.source_id] ? "测试中…" : "测试"));
        }
        if (!deleted && canWrite()) {
          actions.appendChild(h("button.btn.btn-sm", {
            type: "button",
            "data-sync-delete": source.source_id,
            onclick: function () { deleteSource(source); },
          }, "删除"));
        }
        if (!canWrite() && !canRun()) actions.appendChild(h("span.muted.xsmall", "只读"));
        var testResult = S.testResults[source.source_id];
        var checkbox = deleted ? h("td") : h("td",
          h("input", {
            type: "checkbox",
            "data-sync-select": source.source_id,
            "aria-label": "选择来源 " + source.name,
            checked: !!S.selected[source.source_id],
            onchange: function (event) {
              if (event.currentTarget.checked) S.selected[source.source_id] = true;
              else delete S.selected[source.source_id];
              renderBody();
            },
          })
        );
        var target = (source.target && source.target.kind) || "";
        tbody.appendChild(h("tr", { "data-sync-source": source.source_id, class: source.enabled === false ? "sync-paused" : "" },
          checkbox,
          h("td",
            h("div", h("strong", source.name)),
            h("div.xsmall.muted", (KIND_LABEL[source.kind] || source.kind) + (source.profile_id ? " · " + source.profile_id : ""))
          ),
          h("td",
            h("div.small", (KIND_LABEL[source.kind] || "") + (target ? " → " + (TARGET_LABEL[target] || target) : "")),
            h("code.xsmall", source.display_path || source.resource_ref || "—")
          ),
          h("td",
            h("div.small", scheduleText(source)),
            h("div.xsmall.muted", !source.enabled ? "来源已停用" : (source.schedule_mode === "manual" ? "可手动立即更新" : "跟随工作区计划"))
          ),
          h("td", h("span.xsmall.muted", "尚无核验记录")),
          h("td", deleted
            ? h("span.badge.badge-muted", "已删除")
            : testResult
              ? h("span.xsmall", { class: testResult.ok ? "badge badge-ok" : "badge badge-muted", "data-sync-test-result": source.source_id }, testResult.text)
              : (source.enabled === false ? h("span.badge.badge-muted", "已停用") : h("span.badge.badge-ok", "已启用"))),
          h("td", actions)
        ));
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "更新源"), h("span.muted.small", "共 " + S.total + " 条 · 工作区 " + workspaceId())),
        h("div.card-b",
          toolbar,
          batchBar,
          h("div.sync-table-wrap", h("table.table", h("thead", h("tr",
            h("th", headerBox), h("th", "配置 / 文件"), h("th", "更新来源 / 写入目标"),
            h("th", "更新计划"), h("th", "最近有效核验"), h("th", "最近结果"), h("th", "操作")
          )), tbody)),
          S.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { S.expanded = true; loadSources(false); } }, "加载更多") : null
        )
      ));
    }

    function runsPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/runs";
    }

    function runPath(runId) {
      return runsPath() + "/" + encodeURIComponent(runId);
    }

    function runStateText(state) {
      return {
        queued: "排队中",
        running: "进行中",
        succeeded: "已完成",
        partial: "部分失败",
        failed: "失败",
        canceled: "已取消",
        interrupted: "中断",
      }[state] || state;
    }

    function runCounts(run) {
      var counts = run.counts || {};
      return "共 " + (counts.total || 0) + " · 成功 " + (counts.succeeded || 0) +
        " · 失败 " + (counts.failed || 0) + " · 阻断 " + (counts.blocked || 0);
    }

    function loadRuns(reset) {
      var R = state.runs;
      if (R.loading) return Promise.resolve();
      R.loading = true;
      R.error = "";
      if (reset) {
        R.items = [];
        R.next_cursor = null;
        R.expanded = false;
      }
      renderBody();
      return MC.api.requestV3("GET", runsPath(), {
        query: {
          state: R.state || undefined,
          limit: 50,
          cursor: R.expanded && R.next_cursor ? R.next_cursor : "0",
        },
      }).then(function (data) {
        var incoming = (data && data.items) || [];
        R.items = R.expanded ? R.items.concat(incoming) : incoming;
        R.total = data && typeof data.total === "number" ? data.total : R.items.length;
        R.next_cursor = data ? data.next_cursor : null;
        R.loading = false;
        renderBody();
      }, function (err) {
        R.loading = false;
        R.error = errorText(err);
        renderBody();
      });
    }

    function downloadDiagnostics(run) {
      var eventsUrl = runPath(run.run_id) + "/events";
      MC.api.requestV3("GET", eventsUrl, {}).then(function (data) {
        var payload = {
          run: run,
          events: (data && data.items) || [],
          exported_at: new Date().toISOString(),
          note: "MCChecker sync diagnostics; no secrets are included",
        };
        var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "sync-run-" + run.run_id + "-diagnostics.json";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      }, function (err) {
        MC.toast.error("诊断下载失败", errorText(err));
      });
    }

    function retryRun(run, close) {
      MC.api.requestV3("POST", runPath(run.run_id) + "/retry", { body: {} }).then(function () {
        MC.toast.success("失败项已重新入队");
        if (close) close();
        loadRuns(true);
      }, function (err) {
        MC.toast.error("重试提交失败", errorText(err));
      });
    }

    function openRun(run) {
      var bodyNode = h("div.col", { style: "gap:12px" }, h("p.muted.small", "正在读取运行详情…"));
      var modal = MC.modal({
        title: "运行 " + run.run_id,
        subtitle: run.trigger + " · " + runStateText(run.state) + " · " + runCounts(run),
        size: "xwide",
        body: bodyNode,
        footer: function (close) {
          var buttons = [];
          if (canRun() && ["partial", "failed", "interrupted"].indexOf(run.state) >= 0) {
            buttons.push(h("button.btn.btn-primary", { type: "button", "data-sync-retry": "1", onclick: function () { retryRun(run, close); } }, "重试失败项"));
          }
          buttons.push(h("button.btn", { type: "button", onclick: function () { downloadDiagnostics(run); } }, "下载诊断"));
          buttons.push(h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭"));
          return buttons;
        },
      });
      Promise.all([
        MC.api.requestV3("GET", runPath(run.run_id), {}),
        MC.api.requestV3("GET", runPath(run.run_id) + "/events", { query: { after_seq: 0 } }),
      ]).then(function (results) {
        var detail = results[0] || {};
        var events = (results[1] && results[1].items) || [];
        var items = detail.items || [];
        util.clear(bodyNode);
        var itemRows = items.length ? items.map(function (item) {
          return h("tr",
            h("td", h("div.small", item.name || item.source_id), h("div.xsmall.muted", item.source_id || "")),
            h("td", runStateText(item.state)),
            h("td", item.stage || "—"),
            h("td", item.reason_code || "—"),
            h("td", item.committed ? "是" : "否")
          );
        }) : [h("tr", h("td", { colspan: "5" }, h("span.muted", "没有 item 记录。")))];
        bodyNode.appendChild(h("table.table",
          h("thead", h("tr", h("th", "来源"), h("th", "状态"), h("th", "阶段"), h("th", "原因"), h("th", "已提交"))),
          h("tbody", itemRows)
        ));
        bodyNode.appendChild(h("h3", "事件日志"));
        var eventRows = events.length ? events.map(function (event) {
          return h("li", h("span.muted.xsmall", event.at || ""), " · ",
            h("strong", event.status || event.level || ""), " ", event.message || "", event.reason_code ? h("span.xsmall.muted", " (" + event.reason_code + ")") : null);
        }) : [h("li.muted", "没有事件日志。")];
        bodyNode.appendChild(h("ul.mini-list", eventRows));
        if (detail.run) run = detail.run;
      }, function (err) {
        util.clear(bodyNode);
        bodyNode.appendChild(h("div.form-error", errorText(err)));
      });
    }

    function renderRuns() {
      var R = state.runs;
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var filter = h("select.input", {},
        h("option", { value: "" }, "全部状态"),
        ["running", "partial", "failed", "succeeded", "canceled", "interrupted"].map(function (value) {
          return h("option", { value: value }, runStateText(value));
        })
      );
      filter.value = R.state;
      filter.addEventListener("change", function () { R.state = filter.value; loadRuns(true); });
      toolbar.appendChild(filter);
      toolbar.appendChild(h("button.btn.btn-sm", { type: "button", onclick: function () { loadRuns(true); } }, "刷新"));
      if (R.error) body.appendChild(h("div.form-error", R.error));
      if (R.loading && !R.items.length) {
        body.appendChild(h("p.muted", "正在读取运行记录…"));
        return;
      }
      if (!R.items.length) {
        body.appendChild(h("div.card", h("div.card-b", h("p.muted", "还没有同步运行记录。"))));
        return;
      }
      var tbody = h("tbody");
      R.items.forEach(function (doc) {
        var run = doc.run || doc;
        var row = h("tr", { "data-sync-run": run.run_id, style: "cursor:pointer" },
          h("td", h("div.small", run.run_id), h("div.xsmall.muted", run.parent_run_id ? "父 " + run.parent_run_id : "")),
          h("td", run.trigger),
          h("td", runStateText(run.state)),
          h("td", runCounts(run)),
          h("td", h("div.small", util.formatTime(run.created_at)), h("div.xsmall.muted", run.finished_at ? util.formatTime(run.finished_at) : "未结束"))
        );
        row.addEventListener("click", function () { openRun(run); });
        tbody.appendChild(row);
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "运行记录"), h("span.muted.small", "共 " + R.total + " 条")),
        h("div.card-b", toolbar, h("table.table",
          h("thead", h("tr", h("th", "运行"), h("th", "触发"), h("th", "状态"), h("th", "计数"), h("th", "时间"))),
          tbody
        ), R.next_cursor ? h("button.btn.btn-sm", { type: "button", onclick: function () { R.expanded = true; loadRuns(false); } }, "加载更多") : null)
      ));
    }

    function candidatesPath() {
      return "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) + "/sync/candidates";
    }

    function loadCandidates(reset, more) {
      var C = state.candidates;
      if (C.loading) return Promise.resolve();
      C.loading = true;
      C.error = "";
      if (reset) { C.items = []; C.next_cursor = null; }
      renderBody();
      return MC.api.requestV3("GET", candidatesPath(), {
        query: {
          state: C.state || undefined,
          profile_id: C.profile_id || undefined,
          limit: 50,
          cursor: more && C.next_cursor ? C.next_cursor : "0",
        },
      }).then(function (data) {
        var incoming = (data && data.items) || [];
        C.items = more ? C.items.concat(incoming) : incoming;
        C.total = data && typeof data.total === "number" ? data.total : C.items.length;
        C.next_cursor = data ? data.next_cursor : null;
        var alive = {};
        C.items.forEach(function (row) { alive[row.candidate_id] = true; });
        Object.keys(C.selected).forEach(function (cid) { if (!alive[cid]) delete C.selected[cid]; });
        C.loading = false;
        C.loadedFor = workspaceId();
        renderBody();
      }, function (err) {
        C.loading = false;
        C.loadedFor = workspaceId();
        C.error = errorText(err);
        renderBody();
      });
    }

    function openCandidate(candidate) {
      var error = h("div.form-error", { style: "display:none" });
      var configs = [];
      var configSelect = h("select.input", {});
      var releaseSelect = h("select.input", {});
      var previewBox = h("div");
      var configPath = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
        "/profiles/" + encodeURIComponent(candidate.profile_id) + "/configs";
      function showError(message) {
        error.textContent = message;
        error.style.display = message ? "" : "none";
      }
      function loadReleases() {
        var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
        if (!config) return;
        MC.api.requestV3("GET", configPath + "/" + encodeURIComponent(config.config_id) + "/releases", {})
          .then(function (data) {
            util.clear(releaseSelect);
            (data.items || []).forEach(function (release) {
              releaseSelect.appendChild(h("option", { value: release.release_id }, "版本 " + (release.version || release.release_id)));
            });
            releaseSelect.disabled = !releaseSelect.options.length;
          }, function (err) { showError(errorText(err)); });
      }
      var modal = MC.modal({
        title: "候选 " + candidate.candidate_id,
        subtitle: "来源 " + candidate.source_id + " · " + candidate.state,
        size: "xwide",
        body: h("div.col", { style: "gap:12px" },
          error,
          h("div.row", { style: "gap:8px;flex-wrap:wrap" },
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "固定配置"), configSelect),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "软件版本"), releaseSelect)
          ),
          h("div.row", { style: "gap:8px" },
            h("button.btn.btn-sm", { type: "button", "data-sync-preview": "1" }, "生成差异预览")
          ),
          previewBox
        ),
        footer: function (close) {
          return [h("button.btn", { type: "button", onclick: function () { close(); } }, "关闭")];
        },
      });
      if (!candidate.file_version_id) {
        showError("该候选没有关联文件版本，无法生成参数差异（SDA 表候选需后续协议支持）");
      }
      MC.api.requestV3("GET", configPath, {}).then(function (data) {
        configs = data.items || [];
        util.clear(configSelect);
        configs.forEach(function (config) {
          configSelect.appendChild(h("option", { value: config.config_id }, (config.code || "") + " " + (config.name || config.config_id)));
        });
        configSelect.addEventListener("change", loadReleases);
        if (configs.length) loadReleases();
      }, function (err) { showError(errorText(err)); });

      function renderPreview(preview, keepDecisions) {
        util.clear(previewBox);
        var items = preview.items || [];
        var decisions = keepDecisions ? (previewBox._decisions || {}) : {};
        previewBox._decisions = decisions;
        var selects = {};
        var conflicts = items.filter(function (item) { return item.action === "conflict"; })
          .map(function (item) { return item.parameter_id; });
        var base = preview.base || [];

        function pendingCount() {
          return conflicts.filter(function (pid) { return !decisions[pid]; }).length;
        }
        var applyBtn = null;
        var clearBtn = h("button.btn.btn-sm", {
          type: "button",
          "data-sync-clear-decisions": "1",
          disabled: true,
          onclick: function () { clearAll(); },
        }, "清空决议");
        var pendingHint = h("span.xsmall.muted", { "data-sync-pending": "1" });
        function refreshApplyState() {
          var left = pendingCount();
          if (pendingHint) {
            pendingHint.textContent = left
              ? "还有 " + left + " 项冲突待决议（可逐项选择，或用上方批量决议）"
              : "冲突已全部决议，可以写入草稿";
          }
          if (clearBtn) clearBtn.disabled = !Object.keys(decisions).length;
          if (!applyBtn) return;
          var blocked = (preview.blockers || []).filter(function (row) {
            return row.code !== "d0_frozen";
          });
          applyBtn.disabled = !canWrite() || left > 0 ||
            (preview.can_apply === false && !preview.d0_frozen && conflicts.length === 0) ||
            blocked.length > 0;
        }

        function setAll(value) {
          conflicts.forEach(function (pid) {
            decisions[pid] = value;
            if (selects[pid]) selects[pid].value = value;
          });
          refreshApplyState();
        }
        function clearAll() {
          Object.keys(decisions).forEach(function (pid) { delete decisions[pid]; });
          Object.keys(selects).forEach(function (pid) { selects[pid].value = ""; });
          refreshApplyState();
        }

        var rows = items.map(function (item) {
          var cell;
          if (item.action === "conflict") {
            var allowed = (item.allowed && item.allowed.length)
              ? item.allowed
              : ["use_new", "inherit_old", "manual"];
            var sel = h("select.input", { "data-sync-decision": item.parameter_id },
              h("option", { value: "" }, "请选择"),
              allowed.map(function (value) {
                return h("option", { value: value }, DECISION_LABEL[value] || value);
              })
            );
            if (decisions[item.parameter_id]) sel.value = decisions[item.parameter_id];
            sel.addEventListener("change", function () {
              if (sel.value) decisions[item.parameter_id] = sel.value;
              else delete decisions[item.parameter_id];
              refreshApplyState();
            });
            selects[item.parameter_id] = sel;
            cell = sel;
          } else if (item.action === "manual") {
            cell = h("span.badge.badge-warn", "需人工处理");
          } else if (item.action === "keep") {
            cell = h("span.muted.small", "保留当前");
          } else {
            cell = h("span.muted.small", "采用新值");
          }
          var baseValue = (item.base && item.base.value !== undefined)
            ? item.base.value
            : (base.filter(function (row) { return row.parameter_id === item.parameter_id; })[0] || {}).value;
          return h("tr", { "data-sync-diff": item.parameter_id },
            h("td", h("div.small", item.name || item.parameter_id), h("div.xsmall.muted", item.parameter_id)),
            h("td", h("span.badge", item.action)),
            h("td", h("span.xsmall", JSON.stringify(baseValue === undefined ? null : baseValue))),
            h("td", h("span.xsmall", JSON.stringify(item.ours && item.ours.value !== undefined ? item.ours.value : null))),
            h("td", h("span.xsmall", JSON.stringify(item.theirs && item.theirs.value !== undefined ? item.theirs.value : null))),
            h("td", cell)
          );
        });

        previewBox.appendChild(h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center" },
          h("span.small.muted", "计划 " + (preview.summary ? JSON.stringify(preview.summary) : "")),
          h("button.btn.btn-sm", { type: "button", "data-sync-all-new": "1", disabled: !conflicts.length, onclick: function () { setAll("use_new"); } }, "全部采用新值"),
          h("button.btn.btn-sm", { type: "button", "data-sync-all-old": "1", disabled: !conflicts.length, onclick: function () { setAll("inherit_old"); } }, "全部保留当前"),
          clearBtn
        ));
        previewBox.appendChild(h("table.table",
          h("thead", h("tr",
            h("th", "参数"), h("th", "计划"), h("th", "基线"), h("th", "当前"), h("th", "候选"), h("th", "决议")
          )),
          h("tbody", rows)
        ));
        if (preview.blockers && preview.blockers.length) {
          previewBox.appendChild(h("div.form-error", { "data-sync-blockers": "1" },
            preview.blockers.map(function (row) { return row.message || row.code; }).join("；")));
        }
        if (preview.d0_frozen) {
          previewBox.appendChild(h("p.small", "D0 已冻结。确认后会新建基线变更草稿并写入勾选的新值，不改 D0，也不发布。"));
        }

        var frozen = !!preview.d0_frozen;
        applyBtn = h("button.btn.btn-primary", {
          type: "button",
          "data-sync-apply": "1",
          onclick: function () {
            if (!canWrite()) return;
            var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
            if (!config || !releaseSelect.value) { showError("先选择固定配置与软件版本"); return; }
            var payload = {};
            Object.keys(decisions).forEach(function (pid) { payload[pid] = decisions[pid]; });
            // 同一预览复用同一 request_id：重试变成幂等重放，不会重复写入。
            var requestId = "apply-" + preview.preview_id;
            MC.api.requestV3("POST", candidatesPath() + "/" + encodeURIComponent(candidate.candidate_id) + "/apply", {
              body: {
                preview_id: preview.preview_id,
                request_id: requestId,
                decisions: payload,
                create_baseline: frozen,
              },
            }).then(function (result) {
              showError("");
              var done = h("div.card", h("div.card-b", h("div.col", { style: "gap:8px" },
                h("p", { "data-sync-applied": "1" },
                  (result.target === "baseline_draft" ? "已创建基线变更草稿" : "已写入准备版草稿")
                  + "（rev " + result.draft_revision + "）"
                  + (result.replayed ? "；本次为幂等重放，没有重复写入" : "")),
                h("p.xsmall.muted", "没有发布。发布仍在配置管理里显式操作。"),
                h("button.btn.btn-sm", {
                  type: "button",
                  "data-sync-goto-v3": "1",
                  onclick: function () {
                    if (app && app.go) {
                      app.go({
                        kind: "configv3",
                        query: {
                          config: config.config_id,
                          release: releaseSelect.value,
                          draft: "d0-" + releaseSelect.value,
                        },
                      });
                    }
                  },
                }, "去配置管理发布")
              )));
              previewBox.appendChild(done);
              loadCandidates(true);
            }, function (err) {
              if (err && err.code === "REVISION_CONFLICT") {
                showError("草稿或 D0 已在其他客户端变化（409）；已选决议仍保留，请重新预览后继续。");
                var rePreview = h("button.btn.btn-sm", {
                  type: "button",
                  "data-sync-repreview": "1",
                  onclick: function () {
                    requestPreview(true);
                  },
                }, "重新预览并套用原决议");
                previewBox.appendChild(rePreview);
                return;
              }
              showError(errorText(err));
            });
          },
        }, frozen ? "创建基线变更草稿" : "写入准备版草稿");
        previewBox.appendChild(h("div.row", { style: "gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center" },
          applyBtn, pendingHint,
          h("span.xsmall.muted", "只写草稿，不发布 D0/Bn/rn")
        ));
        refreshApplyState();
      }

      function requestPreview(keepDecisions) {
        var config = configs.filter(function (item) { return item.config_id === configSelect.value; })[0];
        if (!config || !releaseSelect.value) { showError("先选择固定配置与软件版本"); return; }
        showError("");
        if (!keepDecisions) util.clear(previewBox);
        MC.api.requestV3("POST", candidatesPath() + "/" + encodeURIComponent(candidate.candidate_id) + "/apply-preview", {
          body: { config_id: config.config_id, draft_id: "d0-" + releaseSelect.value },
        }).then(function (preview) {
          var kept = keepDecisions ? (previewBox._decisions || {}) : {};
          previewBox._decisions = kept;
          renderPreview(preview, true);
        }, function (err) {
          showError(errorText(err));
          if (!keepDecisions) util.clear(previewBox);
        });
      }

      modal.el.querySelector("[data-sync-preview]").addEventListener("click", function () { requestPreview(false); });
    }

    function selectedCandidates() {
      var C = state.candidates;
      return C.items.filter(function (row) { return C.selected[row.candidate_id]; });
    }

    function batchApplyCandidates() {
      var C = state.candidates;
      var rows = selectedCandidates();
      if (!rows.length || C.busy) return;
      var profiles = [];
      rows.forEach(function (row) {
        if (profiles.indexOf(row.profile_id) < 0) profiles.push(row.profile_id);
      });
      if (profiles.length > 1) {
        MC.toast.error("批量写入要求同一机型", "已选候选分属 " + profiles.length + " 个机型，请分开处理");
        return;
      }
      var pid = profiles[0];
      var configPath = "/api/v3/workspaces/" + encodeURIComponent(workspaceId()) +
        "/profiles/" + encodeURIComponent(pid) + "/configs";
      var configSelect = h("select.input", { "data-sync-batch-config": "1" },
        h("option", { value: "" }, "正在读取配置…"));
      var releaseSelect = h("select.input", { "data-sync-batch-release": "1" },
        h("option", { value: "" }, "选择软件版本…"));
      var configs = [];
      var releases = [];
      function fillReleases() {
        util.clear(releaseSelect);
        var rows2 = releases.filter(function (row) { return row.config_id === configSelect.value; });
        if (!rows2.length) { releaseSelect.appendChild(h("option", { value: "" }, "该配置没有版本")); return; }
        rows2.forEach(function (row) {
          releaseSelect.appendChild(h("option", { value: row.release_id }, "版本 " + (row.version || row.release_id)));
        });
      }
      configSelect.addEventListener("change", fillReleases);
      var modal = MC.modal({
        title: "批量写入草稿（" + rows.length + " 个候选）",
        subtitle: "目标机型 " + pid,
        body: h("div.col", { style: "gap:12px" },
          h("p.small.muted", "批量写入只处理无未决冲突的候选；有冲突的候选会列出来，请在单候选抽屉里逐项决议。"),
          h("div.row", { style: "gap:8px;flex-wrap:wrap" },
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "固定配置"), configSelect),
            h("div.col", { style: "gap:4px" }, h("label.small.muted", "软件版本"), releaseSelect)
          )
        ),
        footer: function (close) {
          return [
            h("button.btn", { type: "button", onclick: function () { close(); } }, "取消"),
            h("button.btn.btn-primary", {
              type: "button",
              "data-sync-batch-apply": "1",
              onclick: function () {
                if (!configSelect.value || !releaseSelect.value) return;
                close();
                runBatchApply(rows, configSelect.value, "d0-" + releaseSelect.value);
              },
            }, "开始批量写入"),
          ];
        },
      });
      MC.api.requestV3("GET", configPath, {}).then(function (data) {
        configs = (data && data.items) || [];
        util.clear(configSelect);
        configSelect.appendChild(h("option", { value: "" }, configs.length ? "选择固定配置…" : "该机型没有固定配置"));
        configs.forEach(function (config) {
          configSelect.appendChild(h("option", { value: config.config_id }, (config.code || "") + " " + (config.name || config.config_id)));
        });
        var pending = configs.map(function (config) {
          return MC.api.requestV3("GET", configPath + "/" + encodeURIComponent(config.config_id) + "/releases", {})
            .then(function (res) {
              ((res && res.items) || []).forEach(function (row) {
                releases.push({ config_id: config.config_id, release_id: row.release_id, version: row.version });
              });
            }, function () { /* 单个配置读取失败不影响其他 */ });
        });
        return Promise.all(pending);
      }).then(fillReleases, function (err) {
        MC.toast.error("配置读取失败", errorText(err));
      });
      return modal;
    }

    function runBatchApply(rows, configId, draftId) {
      var C = state.candidates;
      C.busy = "apply";
      renderBody();
      var stats = { ok: 0, skipped: [], failed: [] };
      var seq = Promise.resolve();
      rows.forEach(function (row) {
        seq = seq.then(function () {
          var base = candidatesPath() + "/" + encodeURIComponent(row.candidate_id);
          return MC.api.requestV3("POST", base + "/apply-preview", {
            body: { config_id: configId, draft_id: draftId },
          }).then(function (preview) {
            if (!preview) return;
            if (preview.d0_frozen) { stats.skipped.push(row.candidate_id + "：D0 已冻结"); return; }
            var needs = (preview.needs_decision || []).length ||
              (preview.items || []).filter(function (item) { return item.action === "conflict"; }).length;
            if (needs) { stats.skipped.push(row.candidate_id + "：有未决冲突"); return; }
            if (preview.can_apply === false) { stats.skipped.push(row.candidate_id + "：预览不可应用"); return; }
            return MC.api.requestV3("POST", base + "/apply", {
              body: { preview_id: preview.preview_id, request_id: "batch-" + preview.preview_id, decisions: {} },
            }).then(function () {
              stats.ok += 1;
              row.state = "applied";
            }, function (err) {
              stats.failed.push(row.candidate_id + "：" + errorText(err));
            });
          }, function (err) {
            stats.failed.push(row.candidate_id + "：" + errorText(err));
          });
        });
      });
      seq.then(function () {
        C.busy = "";
        var parts = ["成功 " + stats.ok + " 个"];
        if (stats.skipped.length) parts.push("需人工 " + stats.skipped.length + " 个");
        if (stats.failed.length) parts.push("失败 " + stats.failed.length + " 个");
        var detail = stats.skipped.concat(stats.failed).join("；");
        if (stats.failed.length) {
          MC.toast.error("批量写入完成（" + parts.join("，") + "）", detail);
        } else if (stats.skipped.length) {
          MC.toast.error("批量写入完成，部分候选需人工决议", detail);
        } else {
          MC.toast.success("批量写入完成（" + parts.join("，") + "）");
        }
        C.selected = {};
        loadCandidates(true);
      });
    }

    function renderCandidates() {
      var C = state.candidates;
      var toolbar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" });
      var stateSelect = h("select.input", { "data-sync-candidate-state": "1" },
        h("option", { value: "" }, "全部状态"),
        Object.keys(CANDIDATE_STATE_LABEL).map(function (key) {
          return h("option", { value: key }, CANDIDATE_STATE_LABEL[key]);
        })
      );
      stateSelect.value = C.state;
      stateSelect.addEventListener("change", function () { C.state = stateSelect.value; loadCandidates(true); });
      var profileSelect = h("select.input", { "data-sync-candidate-profile": "1" },
        h("option", { value: "" }, "全部机型"),
        workspaceProfiles().map(function (profile) { return h("option", { value: profile.id }, profile.name); })
      );
      profileSelect.value = C.profile_id;
      profileSelect.addEventListener("change", function () { C.profile_id = profileSelect.value; loadCandidates(true); });
      toolbar.appendChild(stateSelect);
      toolbar.appendChild(profileSelect);
      toolbar.appendChild(h("button.btn.btn-sm", {
        type: "button", "data-sync-candidate-refresh": "1", onclick: function () { loadCandidates(true); },
      }, "刷新"));

      if (C.error) body.appendChild(h("div.form-error", C.error));
      if (C.loading && !C.items.length) { body.appendChild(h("p.muted", "正在读取候选…")); return; }

      var selectable = C.items.filter(function (row) { return row.state !== "superseded"; });
      var chosen = selectable.filter(function (row) { return C.selected[row.candidate_id]; });
      var batchBar = h("div.row", { style: "gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px", "data-sync-candidate-batch": "1" });
      if (canWrite() && selectable.length) {
        batchBar.appendChild(h("span.small.muted", "已选 " + chosen.length + " / " + selectable.length));
        batchBar.appendChild(h("button.btn.btn-sm.btn-primary", {
          type: "button",
          "data-sync-candidate-batch-apply": "1",
          disabled: !chosen.length || !!C.busy,
          onclick: batchApplyCandidates,
        }, C.busy ? "正在批量写入…" : "批量写入草稿"));
      }
      if (!C.items.length) {
        body.appendChild(h("div.card", h("div.card-b", toolbar, batchBar,
          h("p.muted", C.state ? "该状态下没有候选。" : "还没有更新候选。"))));
        return;
      }
      var headerBox = h("input", {
        type: "checkbox",
        "data-sync-candidate-select-all": "1",
        "aria-label": "全选当前页候选",
        checked: selectable.length > 0 && chosen.length === selectable.length,
      });
      headerBox.addEventListener("change", function () {
        selectable.forEach(function (row) {
          if (headerBox.checked) C.selected[row.candidate_id] = true;
          else delete C.selected[row.candidate_id];
        });
        renderBody();
      });
      var tbody = h("tbody");
      C.items.forEach(function (candidate) {
        var row = h("tr", { "data-sync-candidate": candidate.candidate_id, style: "cursor:pointer" },
          h("td", candidate.state === "superseded" ? h("span.muted.xsmall", "—") : h("input", {
            type: "checkbox",
            "data-sync-candidate-select": candidate.candidate_id,
            "aria-label": "选择候选 " + candidate.candidate_id,
            checked: !!C.selected[candidate.candidate_id],
            onclick: function (event) { event.stopPropagation(); },
            onchange: function (event) {
              if (event.currentTarget.checked) C.selected[candidate.candidate_id] = true;
              else delete C.selected[candidate.candidate_id];
              renderBody();
            },
          })),
          h("td", h("div.small", candidate.candidate_id), h("div.xsmall.muted", candidate.source_id)),
          h("td", h("span.badge", CANDIDATE_STATE_LABEL[candidate.state] || candidate.state)),
          h("td", h("div.xsmall", candidate.content_hash || "—")),
          h("td", h("div.small", util.formatTime(candidate.created_at)),
            h("div.xsmall.muted", candidate.file_version_id ? "关联文件版本" : "无文件版本")),
          h("td", h("div.xsmall.muted", candidate.applied_at ? "已写入 rev " + candidate.applied_revision : "—"))
        );
        row.addEventListener("click", function () { openCandidate(candidate); });
        tbody.appendChild(row);
      });
      body.appendChild(h("div.card",
        h("div.card-h", h("strong", "更新候选"), h("span.muted.small", "共 " + C.total + " 条 · 工作区 " + workspaceId())),
        h("div.card-b",
          toolbar,
          batchBar,
          h("table.table",
            h("thead", h("tr",
              h("th", headerBox), h("th", "候选"), h("th", "状态"), h("th", "内容指纹"),
              h("th", "时间/来源"), h("th", "写入")
            )),
            tbody
          ),
          C.next_cursor ? h("button.btn.btn-sm", {
            type: "button", onclick: function () { loadCandidates(false, true); },
          }, "加载更多") : null
        )
      ));
    }

    var sheet = h("div.sync-sheet");
    sheet.hidden = true;

    function paintSheet() {
      if (!state.sheet) {
        sheet.hidden = true;
        util.clear(sheet);
        return;
      }
      sheet.hidden = false;
      util.clear(sheet);
      var host = h("div.sync-sheet-card");
      var close = h("button.btn.btn-sm", {
        type: "button",
        "data-sync-sheet-close": "1",
        onclick: function () { showSheet(""); },
      }, "关闭");
      sheet.appendChild(h("div.row", { style: "justify-content:flex-end;margin-bottom:8px" }, close));
      sheet.appendChild(host);
      var previous = body;
      body = host;
      if (state.sheet === "plan") renderPlan();
      else if (state.sheet === "connections") renderConnections();
      body = previous;
    }

    function showSheet(kind) {
      state.sheet = kind || "";
      paintSheet();
      if (state.sheet === "plan" && state.plan.loadedFor !== workspaceId() && !state.plan.loading) {
        loadPlan();
      }
      if (state.sheet === "connections" && state.connections.loadedFor !== workspaceId() && !state.connections.loading) {
        loadConnections(true);
      }
    }

    function renderBody() {
      util.clear(body);
      var tab = tabById(state.tab);
      if (tab.id === "runs") {
        renderRuns();
      } else if (tab.id === "candidates") {
        renderCandidates();
        if (state.candidates.loadedFor !== workspaceId() && !state.candidates.loading) {
          loadCandidates(true);
        }
      } else {
        renderSources();
      }
      if (state.sheet) paintSheet();
    }

    var headActions = h("div.sync-head-actions");
    function renderHead() {
      util.clear(headActions);
      if (canWrite()) {
        headActions.appendChild(h("button.btn.btn-sm", {
          type: "button",
          "data-sync-new": "1",
          onclick: function () { openEditor(null); },
        }, "新增更新源"));
      }
      if (canRun()) {
        headActions.appendChild(h("button.btn.btn-sm.btn-primary", {
          type: "button",
          "data-sync-all": "1",
          onclick: function () { showSheet("plan"); },
        }, "工作区全量更新"));
      }
    }
    function renderStrip() {
      var plan = state.plan.policy;
      var hours = plan && plan.default_interval_hours != null ? plan.default_interval_hours : 6;
      var auto = plan ? !!plan.enabled : false;
      var conn = (state.connections.items || [])[0];
      var hb = conn && state.connections.heartbeats[conn.connection_id];
      var connText = !conn
        ? "尚未配置 SDA 连接"
        : (conn.name + (hb && hb.enabled ? " · 保活 " + (hb.interval_minutes || 10) + " 分钟" : " · 保活已关闭"));
      return h("div.sync-strip",
        h("span.small", connText),
        h("button.btn.btn-sm", {
          type: "button",
          "data-sync-connections": "1",
          onclick: function () { showSheet("connections"); },
        }, conn ? "连接设置" : "配置连接"),
        h("span.sync-divider"),
        h("span.small", auto ? ("自动更新 每 " + hours + " 小时") : "自动更新 已暂停"),
        h("button.btn.btn-sm", {
          type: "button",
          "data-sync-plan": "1",
          onclick: function () { showSheet("plan"); },
        }, "计划设置")
      );
    }
    var stripHost = h("div");
    function paintChrome() {
      renderHead();
      util.clear(stripHost);
      stripHost.appendChild(renderStrip());
    }
    el.appendChild(h("div.view-body.sync-page",
      h("div.sync-head",
        h("div.sync-title", h("strong", "更新管理"), h("span.sync-caption", workspaceId())),
        headActions
      ),
      stripHost,
      tabbar,
      body,
      sheet
    ));
    renderTabs();
    paintChrome();
    renderBody();
    if (state.sheet) showSheet(state.sheet);
    if (state.tab === "sources") loadSources(true);
    if (state.tab === "runs") loadRuns(true);
    if (!state.connections.loading) loadConnections(true);
    if (state.plan.loadedFor !== workspaceId() && !state.plan.loading) loadPlan();

    return {
      el: el,
      title: "更新管理",
      icon: "settings",
      update: function (next) {
        state.tab = tabById(next && next.arg).id;
        state.sheet = sheetFor(next);
        renderTabs();
        paintChrome();
        renderBody();
        if (state.sheet) showSheet(state.sheet);
        if (state.tab === "sources") loadSources(true);
        if (state.tab === "runs") loadRuns(true);
      },
      isDirty: function () { return !!activeEditorDirty; },
      destroy: function () {
        activeEditorDirty = false;
        (state.connections.pollTimers || []).forEach(function (timer) { clearTimeout(timer); });
        state.connections.pollTimers = [];
      },
    };
  };
})(typeof window !== "undefined" ? window : this);
