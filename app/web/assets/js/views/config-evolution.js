/* V3.2 presentation of real configuration records. No demo model or writes. */
(function (global) {
  "use strict";
  var MC = global.MC;
  var h = MC.util.h;

  function sequence(record) { return parseInt(String(record.label || "").replace(/^\D+/, ""), 10) || 0; }
  function newest(records) {
    return records.slice().sort(function (a, b) { return sequence(b) - sequence(a); })[0] || null;
  }

  function project(releases, records, machines, query, status) {
    var byId = Object.create(null);
    records.forEach(function (record) { byId[record.id] = record; });
    var groups = releases.slice().sort(function (a, b) { return a.order - b.order; }).map(function (release) {
      var initial = byId[release.initial_revision_id === undefined ? release.default_revision_id : release.initial_revision_id] || null;
      var standard = newest(records.filter(function (record) {
        return record.kind === "baseline" && record.status === "published" && record.release_id === release.release_id;
      })) || byId[release.default_revision_id] || null;
      var draft = records.filter(function (record) {
        return record.release_id === release.release_id && record.kind !== "machine" && record.status === "draft";
      })[0] || null;
      return { release: release, initial: initial, standard: standard, draft: draft, machines: [] };
    });
    machines.forEach(function (machine) {
      var saved = byId[machine.head_id] || null;
      var draft = records.filter(function (record) { return record.machine_id === machine.machine_id && record.status === "draft"; })[0] || null;
      // A pending upgrade changes the container's release_id before publication.
      var owner = saved ? saved.release_id : draft && draft.release_id;
      var group = groups.filter(function (item) { return item.release.release_id === owner; })[0];
      if (group) group.machines.push({ machine: machine, saved: saved, draft: draft });
    });
    var needle = String(query || "").trim().toLowerCase();
    return groups.map(function (group) {
      var versionMatch = group.release.version.toLowerCase().indexOf(needle) >= 0;
      group.machines = group.machines.filter(function (row) {
        return (status !== "draft" || row.draft) && (!needle || versionMatch || row.machine.machine_no.toLowerCase().indexOf(needle) >= 0);
      });
      return group;
    }).filter(function (group) {
      return (!needle || group.release.version.toLowerCase().indexOf(needle) >= 0 || group.machines.length) &&
        (status !== "draft" || group.draft || group.machines.length);
    });
  }

  function button(label, handler, options) {
    return h("button.btn.btn-sm" + (options && options.className || ""), Object.assign({ type: "button", onclick: handler }, options && options.attrs || {}), label);
  }

  function hierarchy(groups, options) {
    var table = h("table.table.cv32-hierarchy", h("thead", h("tr", ["软件版本 / 机台", "配置状态", "最近保存", "操作"].map(function (label) { return h("th", label); }))));
    groups.slice().reverse().forEach(function (group) {
      var release = group.release, id = release.release_id, open = !options.collapsed.has(id);
      var childId = MC.util.uid("machine-rows"), toggle;
      toggle = button(MC.icon(open ? "chevron-down" : "chevron-right"), function () { options.toggle(id); }, {
        className: ".btn-ghost.btn-icon", attrs: { "aria-expanded": String(open), "aria-controls": childId,
          "aria-label": (open ? "收起软件 " : "展开软件 ") + release.version, "data-version-toggle": id }
      });
      var standard = group.standard || group.draft;
      table.appendChild(h("tbody", h("tr.cv32-version-row",
        h("td", h("div.row.cv32-tree-name", toggle,
          button("软件 " + release.version, function () { options.open(standard, "standard"); }, { className: ".btn-ghost.cv32-name", attrs: { "data-version-link": id, disabled: !standard } }),
          h("span.small.muted", group.machines.length + " 台机台"))),
        h("td", h("span.small.muted", "标准参数 "), group.draft ? h("span.badge.badge-warning", "有草稿") : group.standard ? "已保存" : "尚未保存"),
        h("td.small.muted", group.standard ? MC.util.formatTime(group.standard.at) : "—"),
        h("td", button(group.draft ? "继续草稿" : "编辑标准", function () { options.edit(group.draft || group.standard); }, { className: ".btn-ghost", attrs: { disabled: !options.canWrite || !standard } }))
      )));
      table.appendChild(h("tbody", { id: childId, hidden: !open }, group.machines.map(function (row) {
        var target = row.draft && options.versionName(row.draft.release_id);
        return h("tr", h("td", h("div.row.cv32-tree-child", MC.icon("monitor"),
          button(row.machine.machine_no, function () { options.open(row.saved || row.draft, "machine"); }, { className: ".btn-ghost.cv32-name", attrs: { "data-machine-link": row.machine.machine_id } }))),
          h("td", row.draft ? h("span.badge.badge-warning", row.saved ? "有草稿" : "新建草稿") : h("span.small.muted", "已保存"),
            row.saved && row.draft && row.saved.release_id !== row.draft.release_id ? h("span.small.cv-warning", " 更新到 " + target) : null),
          h("td.small.muted", row.saved ? MC.util.formatTime(row.saved.at) : "—"),
          h("td", button(row.draft ? "继续草稿" : "编辑参数", function () { options.edit(row.draft || row.saved); }, { className: ".btn-ghost", attrs: { disabled: !options.canWrite } })));
      })));
    });
    return table;
  }

  function graph(groups, options) {
    var releases = options.releases.slice().sort(function (a, b) { return a.order - b.order; });
    var records = options.records, byId = Object.create(null), needed = new Set(), rows = [];
    records.forEach(function (record) { byId[record.id] = record; });
    groups.forEach(function (group) {
      needed.add(group.release.release_id);
      group.machines.forEach(function (row) {
        row.previous = newest(records.filter(function (record) {
          return record.machine_id === row.machine.machine_id && record.status === "published" && row.saved && record.release_id !== row.saved.release_id;
        }));
        [row.saved, row.draft, row.previous].forEach(function (record) {
          if (!record) return;
          needed.add(record.release_id);
          var source = byId[record.source];
          if (source) needed.add(source.release_id);
        });
        rows.push(row);
      });
    });
    releases = releases.filter(function (release) { return needed.has(release.release_id); });
    var full = project(releases, records, options.machines, "", "all");
    var snapshots = [];
    rows.forEach(function (row) {
      [row.saved, row.draft, row.previous].forEach(function (record) {
        var source = record && byId[record.source];
        var group = source && full.filter(function (item) { return item.release.release_id === source.release_id; })[0];
        if (source && group && source !== group.standard && source !== group.initial && !snapshots.some(function (item) { return item.id === source.id; })) snapshots.push(source);
      });
    });
    var column = 218, labelWidth = 120, header = 62, nodeWidth = 164, nodeHeight = 53;
    var extra = Math.max(0, snapshots.length) * 66;
    var laneTops = [], y = header + 2 * 87 + extra;
    rows.forEach(function (row) {
      laneTops.push(y);
      y += row.saved && row.draft && row.saved.release_id === row.draft.release_id ? 148 : 87;
    });
    var width = labelWidth + releases.length * column, height = y + 20;
    var canvas = h("div.cv-graph-canvas.cv32-canvas", { style: "width:" + width + "px;height:" + height + "px;transform:scale(" + options.zoom + ");transform-origin:top left" });
    function axisContent(w, hgt) { return h("div.cv32-axis-content", { style: "width:" + w + "px;height:" + hgt + "px;transform:scale(" + options.zoom + ");transform-origin:top left" }); }
    var columns = axisContent(width, header), labels = axisContent(labelWidth, height);
    var columnLayer = h("div.cv32-fixed-columns", { style: "width:" + width * options.zoom + "px;height:" + header * options.zoom + "px" }, columns);
    var rowLayer = h("div.cv32-fixed-rows", { style: "width:" + labelWidth * options.zoom + "px;height:" + height * options.zoom + "px" }, labels);
    var corner = h("div.cv32-fixed-corner", { "aria-hidden": "true", style: "width:" + labelWidth * options.zoom + "px;height:" + header * options.zoom + "px" });
    var nodes = [], keys = Object.create(null), edges = [];
    function add(key, record, title, top, role, style) {
      if (!record) return;
      var col = releases.findIndex(function (release) { return release.release_id === record.release_id; });
      if (col < 0) return;
      var item = { key: key, record: record, title: title, x: labelWidth + col * column + (column - nodeWidth) / 2, y: top + 17, role: role, style: style || "" };
      nodes.push(item); keys[key] = item;
      if (!keys[record.id] || role !== "initial") keys[record.id] = item;
    }
    full.forEach(function (group, index) {
      var release = group.release;
      canvas.appendChild(h("div.cv-graph-column", { style: "left:" + (labelWidth + index * column) + "px;width:" + column + "px" }));
      columns.appendChild(h("div.cv-release-band", { style: "left:" + (labelWidth + index * column) + "px;width:" + column + "px" },
        button(release.version, function () { options.open(group.standard || group.draft, "standard"); }, { className: ".btn-ghost.cv32-name", attrs: { disabled: !group.standard && !group.draft, "aria-label": "打开软件 " + release.version } }),
        h("span.badge", groups.some(function (g) { return g.release.release_id === release.release_id; }) ? group.standard ? "已保存" : "准备中" : "关联上下文")));
      add("initial-" + release.release_id, group.initial, "初始参数", header, "initial", "initial");
      add("standard-" + release.release_id, group.standard || group.draft, group.draft ? "版本标准 · 有草稿" : "版本标准", header + 87, "standard");
      if (group.initial && group.standard) edges.push({ from: "initial-" + release.release_id, to: "standard-" + release.release_id, source: true });
      if (release.predecessor_id) {
        edges.push({ from: "initial-" + release.predecessor_id, to: "initial-" + release.release_id });
        edges.push({ from: "standard-" + release.predecessor_id, to: "standard-" + release.release_id });
      }
    });
    snapshots.forEach(function (record, index) { add(record.id, record, "来源标准快照", header + 87 + (index + 1) * 66, "history", "historical"); });
    rows.forEach(function (row, index) {
      var top = laneTops[index];
      add(row.previous && row.previous.id, row.previous, "历史配置", top, "history", "historical");
      add(row.saved && row.saved.id, row.saved, row.machine.machine_no, top, "machine");
      add(row.draft && row.draft.id, row.draft, row.machine.machine_no + " · 草稿", top + (row.saved && row.draft && row.saved.release_id === row.draft.release_id ? 61 : 0), "machine");
    });
    nodes.forEach(function (node) {
      if (node.record.parent) edges.push({ from: node.record.parent, to: node.key });
      if (node.record.source) edges.push({ from: node.record.source, to: node.key, source: true });
    });
    var svg = document.createElementNS(MC.SVG_NS, "svg"), seen = new Set();
    svg.setAttribute("class", "cv-connections"); svg.setAttribute("width", width); svg.setAttribute("height", height); svg.setAttribute("aria-hidden", "true");
    edges.forEach(function (edge) {
      var from = keys[edge.from], to = keys[edge.to], identity = edge.from + "/" + edge.to + "/" + !!edge.source;
      if (!from || !to || from === to || seen.has(identity)) return;
      seen.add(identity);
      var path = document.createElementNS(MC.SVG_NS, "path");
      path.setAttribute("d", from.y === to.y ? "M" + (from.x + nodeWidth) + " " + (from.y + nodeHeight / 2) + "H" + to.x
        : "M" + (from.x + nodeWidth / 2) + " " + (from.y + nodeHeight) + "V" + (to.y - 10) + "H" + (to.x + nodeWidth / 2) + "V" + to.y);
      path.setAttribute("fill", "none"); path.setAttribute("stroke", "currentColor"); path.setAttribute("stroke-width", "1.2");
      if (edge.source) path.setAttribute("stroke-dasharray", "4 4");
      svg.appendChild(path);
    });
    canvas.appendChild(svg);
    [{ name: "初始参数", top: header }, { name: "版本标准", top: header + 87 }].concat(rows.map(function (row, index) { return { name: row.machine.machine_no, top: laneTops[index] }; })).forEach(function (lane) {
      labels.appendChild(h("div.cv-lane-label", { style: "top:" + (lane.top + 24) + "px", title: lane.name }, h("strong", lane.name)));
    });
    nodes.forEach(function (node) {
      var draft = node.record.status === "draft";
      canvas.appendChild(button(h("span.col", h("strong", node.title), h("span.small.muted", draft ? "草稿 · 待保存" : MC.util.formatTime(node.record.at))),
        function () { options.open(node.record, node.role); }, { className: ".cv-graph-node.cv32-node" + (draft ? ".is-draft" : "") + (node.style ? "." + node.style : ""),
          attrs: { style: "left:" + node.x + "px;top:" + node.y + "px", "data-config-revision": node.record.id,
            "data-config-role": node.role, title: node.title,
            "aria-label": node.title + " · 软件 " + options.versionName(node.record.release_id),
            "aria-pressed": String(options.selected === node.record.id && (options.selectedRole || "standard") === node.role) } }));
    });
    return h("div.cv32-graph-plane", { style: "width:" + width * options.zoom + "px;height:" + height * options.zoom + "px" }, canvas, columnLayer, rowLayer, corner);
  }

  function configureAxes(scroll, zoom) {
    scroll.style.setProperty("--cv-graph-zoom", zoom);
  }
  MC.configEvolution = { project: project, hierarchy: hierarchy, graph: graph, configureAxes: configureAxes };
})(window);
