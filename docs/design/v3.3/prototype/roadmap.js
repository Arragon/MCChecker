/* Configuration roadmap view. Pure projection of saved revisions/parentIds.
 * No model mutation, event listeners, network, dependencies or fabricated edges.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MCPrototypeRoadmap = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const COLORS = Object.freeze(['#2563eb', '#148366', '#8058b5', '#b77b20', '#147e9b', '#ad527d', '#607994']);
  const ROW_HEIGHT = 56, LANE_PITCH = 48, GUTTER = 28;
  const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const integer = (value, fallback) => Number.isFinite(Number(value)) ? Math.floor(Number(value)) : fallback;
  function error(code, message) { const e = new Error(message); e.code = code; throw e; }
  function branchName(branch) { return branch?.kind === 'baseline' ? '版本基线' : branch?.label || '归属未记录'; }
  function formatRevision(file, input, options) {
    options = options || {};
    const r = typeof input === 'string' ? file?.revisions?.find(r => r.id === input) : input;
    if (!r) return options.compact ? '记录未载入' : '保存记录未载入';
    const release = r.releaseId ? '软件 ' + r.releaseId : '软件版本未记录';
    if (r.kind === 'initial') return options.compact ? '初始文件' : '初始文件 · ' + release;
    const match = /^(?:B|r)(\d+)$/i.exec(String(r.label || ''));
    const count = match && Number.isSafeInteger(Number(match[1])) ? Number(match[1]) : null;
    const saved = count !== null ? '第' + count + '次保存' : '保存记录';
    if (options.compact) return saved;
    const branch = file?.branches?.find(b => b.id === r.branchId);
    return branchName(branch) + ' · ' + release + ' · ' + saved;
  }
  function timestamp(record) { const value = Date.parse(record.at || ''); return Number.isFinite(value) ? value : -Infinity; }
  // Latest-first topological order: clock skew cannot place a parent above a child.
  function ordered(revisions, byId) {
    const pending = new Map(revisions.map(r => [r.id, 0]));
    const index = new Map(revisions.map((r, i) => [r.id, i]));
    revisions.forEach(r => [...new Set(r.parentIds || [])].forEach(id => { if (byId.has(id)) pending.set(id, pending.get(id) + 1); }));
    const heap = [], ahead = (a, b) => timestamp(a) > timestamp(b) || timestamp(a) === timestamp(b) && index.get(a.id) > index.get(b.id);
    function push(value) { let i = heap.length; heap.push(value); while (i) { const parent = (i - 1) >> 1; if (!ahead(heap[i], heap[parent])) break; [heap[i], heap[parent]] = [heap[parent], heap[i]]; i = parent; } }
    function pop() { const first = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; while (true) { let best = i, left = i * 2 + 1, right = left + 1; if (left < heap.length && ahead(heap[left], heap[best])) best = left; if (right < heap.length && ahead(heap[right], heap[best])) best = right; if (best === i) break; [heap[i], heap[best]] = [heap[best], heap[i]]; i = best; } } return first; }
    revisions.forEach(r => { if (!pending.get(r.id)) push(r); });
    const result = [];
    while (heap.length) { const r = pop(); result.push(r); for (const id of new Set(r.parentIds || [])) if (byId.has(id)) { pending.set(id, pending.get(id) - 1); if (!pending.get(id)) push(byId.get(id)); } }
    if (result.length !== revisions.length) error('INVALID_DAG', '保存关系存在循环，请核对来源记录');
    return result;
  }
  function relatedIds(focus, branches, byId, revisions) {
    if (!focus || focus === 'all') return new Set(revisions.map(r => r.id));
    if (focus === 'baseline') return new Set(revisions.filter(r => branches.get(r.branchId)?.kind === 'baseline').map(r => r.id));
    const queue = revisions.filter(r => r.branchId === focus).map(r => r.id), found = new Set();
    for (let i = 0; i < queue.length; i++) { const id = queue[i]; if (found.has(id)) continue; found.add(id); const r = byId.get(id); if (r) queue.push(...(r.parentIds || [])); }
    return found;
  }
  function edgeKind(child, parent, order, branches) {
    if (child.restoreRevisionId === parent?.id) return 'restore';
    const a = branches.get(child.branchId), b = branches.get(parent?.branchId);
    if (a?.kind === 'machine' && b?.kind === 'baseline') return (child.parentIds || []).length > 1 ? 'adopt' : 'fork';
    if (order > 0) return 'merge';
    if (parent?.kind === 'initial') return 'initialize';
    return 'lineage';
  }
  function curve(x1, y1, x2, y2) { const middle = (y1 + y2) / 2; return x1 === x2 ? `M ${x1} ${y1} L ${x2} ${y2}` : `M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}`; }
  function layout(file, options) {
    options = options || {};
    if (!file || !Array.isArray(file.revisions) || !Array.isArray(file.branches)) error('INVALID_FILE', '配置文件的保存关系不可读取');
    const byId = new Map(), branches = new Map();
    file.branches.forEach(b => { if (!b.id || branches.has(b.id)) error('DUPLICATE_BRANCH', '配置对象身份重复'); branches.set(b.id, b); });
    file.revisions.forEach(r => { if (!r.id || byId.has(r.id)) error('DUPLICATE_REVISION', '保存记录身份重复'); if (!Array.isArray(r.parentIds)) error('INVALID_PARENTS', '保存记录缺少明确的父记录列表'); byId.set(r.id, r); });
    const sorted = ordered(file.revisions, byId), lanes = [{ id: 'baseline', label: '基线主线', kind: 'baseline', index: 0, x: GUTTER, color: COLORS[0], branchIds: file.branches.filter(b => b.kind === 'baseline').map(b => b.id) }];
    file.branches.filter(b => b.kind !== 'baseline').forEach(b => { const index = lanes.length; lanes.push({ id: b.id, label: branchName(b), kind: b.kind || 'unknown', index, x: GUTTER + index * LANE_PITCH, color: COLORS[1 + (index - 1) % (COLORS.length - 1)], branchIds: [b.id] }); });
    sorted.filter(r => !branches.has(r.branchId)).forEach(r => { if (!lanes.some(lane => lane.id === 'unknown:' + r.branchId)) { const index = lanes.length; lanes.push({ id: 'unknown:' + r.branchId, label: '归属未记录', kind: 'unknown', index, x: GUTTER + index * LANE_PITCH, color: '#7d8795', branchIds: [r.branchId] }); } });
    const laneByBranch = new Map(); lanes.forEach(lane => lane.branchIds.forEach(id => laneByBranch.set(id, lane)));
    const heads = new Map(), adopted = new Map();
    file.branches.forEach(b => {
      if (b.headId && byId.has(b.headId)) { const list = heads.get(b.headId) || []; list.push(b); heads.set(b.headId, list); }
      const head = byId.get(b.headId);
      if (b.kind === 'machine' && head?.baseRevisionId) { const list = adopted.get(head.baseRevisionId) || []; list.push({ branchId: b.id, label: branchName(b), machineId: b.machineId, headId: head.id }); adopted.set(head.baseRevisionId, list); }
    });
    const focus = String(options.branchFilter || options.focusBranch || 'all'), related = relatedIds(focus, branches, byId, sorted);
    const nodes = sorted.map((r, row) => { const lane = laneByBranch.get(r.branchId), branch = branches.get(r.branchId); return { id: r.id, revision: r, branchId: r.branchId, branchLabel: branchName(branch), kind: r.kind === 'initial' ? 'initial' : branch?.kind || 'unknown', laneId: lane.id, laneIndex: lane.index, x: lane.x, row, y: row * ROW_HEIGHT + ROW_HEIGHT / 2, color: lane.color, parentIds: [...new Set(r.parentIds)], isHead: heads.has(r.id), heads: (heads.get(r.id) || []).map(b => ({ id: b.id, label: branchName(b), releaseId: b.releaseId, kind: b.kind })), adoptedBy: adopted.get(r.id) || [], focused: related.has(r.id), selected: options.selectedRevisionId === r.id, title: formatRevision(file, r) }; });
    const nodeById = new Map(nodes.map(node => [node.id, node]));
    const edges = nodes.flatMap(node => node.parentIds.map((id, order) => { const parent = nodeById.get(id); return { id: JSON.stringify([node.id, id]), childId: node.id, parentId: id, from: node.id, to: id, parentExists: !!parent, childLane: node.laneId, parentLane: parent?.laneId || null, kind: parent ? edgeKind(node.revision, parent.revision, order, branches) : 'unknown', order, color: node.kind === 'baseline' && parent?.kind === 'machine' ? parent.color : node.color, focused: node.focused && (!parent || parent.focused) }; }));
    // Keep canonical nodes/edges/lanes intact. Focus projects the selected
    // object's real ancestry onto only the tracks it needs, reclaiming space
    // when a file is used by many machines. Search applies inside that scope.
    const scoped = focus === 'all' ? nodes : nodes.filter(node => node.focused);
    const scopeIds = new Set(scoped.map(node => node.id)), scopeLanes = new Set(scoped.map(node => node.laneId));
    const visibleLanes = (focus === 'all' ? lanes : lanes.filter(lane => scopeLanes.has(lane.id))).map((lane, index) => Object.assign({}, lane, { index, x: GUTTER + index * LANE_PITCH }));
    const projectedLanes = new Map(visibleLanes.map(lane => [lane.id, lane]));
    const query = String(options.query || '').trim().toLocaleLowerCase();
    const matching = (query ? scoped.filter(node => [node.title, node.branchLabel, node.revision.releaseId, node.revision.message, node.revision.reason, node.revision.authorName].filter(Boolean).join(' ').toLocaleLowerCase().includes(query)) : scoped).map(node => Object.assign({}, node, { laneIndex: projectedLanes.get(node.laneId).index, x: projectedLanes.get(node.laneId).x }));
    const matchingIds = matching.map(node => node.id);
    const pageSize = Math.min(100, Math.max(1, integer(options.pageSize, 30))), total = matching.length, pageCount = Math.max(1, Math.ceil(total / pageSize)), page = Math.max(0, Math.min(integer(options.page, 0), pageCount - 1)), offset = page * pageSize;
    const displayIndex = new Map(matching.map((node, i) => [node.id, i]));
    const visibleNodes = matching.slice(offset, offset + pageSize).map((node, i) => Object.assign({}, node, { displayIndex: offset + i, visibleRow: i, y: i * ROW_HEIGHT + ROW_HEIGHT / 2 }));
    const visibleIds = new Set(visibleNodes.map(node => node.id)), height = visibleNodes.length * ROW_HEIGHT;
    // Draw only edges incident to a visible record. Off-page endpoints continue
    // to the viewport edge; a filtered endpoint gets an explicit short stub.
    // We never join the next displayed record in place of the actual parent.
    const scopedEdges = edges.filter(edge => scopeIds.has(edge.childId));
    const visibleEdges = scopedEdges.filter(edge => visibleIds.has(edge.childId) || visibleIds.has(edge.parentId)).map(edge => {
      const child = nodeById.get(edge.childId), parent = nodeById.get(edge.parentId), childIndex = displayIndex.get(edge.childId), parentIndex = displayIndex.get(edge.parentId);
      const childX = projectedLanes.get(child.laneId)?.x, parentX = parent && projectedLanes.get(parent.laneId)?.x;
      const childVisible = visibleIds.has(edge.childId), parentVisible = visibleIds.has(edge.parentId);
      let path, boundary = null;
      if (childIndex !== undefined && parentIndex !== undefined) {
        const cy = (childIndex - offset) * ROW_HEIGHT + ROW_HEIGHT / 2, py = (parentIndex - offset) * ROW_HEIGHT + ROW_HEIGHT / 2;
        path = curve(childX, cy, parentX, py);
        if (!childVisible || !parentVisible) boundary = !childVisible ? 'newer-page' : 'older-page';
      } else if (childVisible) {
        const y = (childIndex - offset) * ROW_HEIGHT + ROW_HEIGHT / 2;
        path = curve(childX, y, childX, y + 18); boundary = parent ? 'filtered-parent' : 'missing-parent';
      } else {
        const y = (parentIndex - offset) * ROW_HEIGHT + ROW_HEIGHT / 2;
        path = curve(parentX, y - 18, parentX, y); boundary = 'filtered-child';
      }
      return Object.assign({}, edge, { path, boundary, childVisible, parentVisible });
    });
    return { nodes, edges, lanes, visibleLanes, matchingIds, visibleNodes, visibleEdges, rowHeight: ROW_HEIGHT, graphWidth: Math.max(164, GUTTER * 2 + (visibleLanes.length - 1) * LANE_PITCH), canvasHeight: height, page, pageSize, pageCount, offset, total, totalRecords: nodes.length, scopeTotal: scoped.length, branchFilter: focus, query, pendingBranches: file.branches.filter(b => !b.headId && (focus === 'all' || focus === 'baseline' && b.kind === 'baseline' || b.id === focus)).map(b => ({ id: b.id, label: branchName(b), releaseId: b.releaseId })), unknownParents: scopedEdges.filter(e => !e.parentExists).map(e => ({ childId: e.childId, parentId: e.parentId })) };
  }
  function timeLabel(record, zone) { const date = new Date(record.at); if (!Number.isFinite(date.getTime())) return '时间未记录'; return date.toLocaleString('zh-CN', { timeZone: zone || 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }); }
  function reason(record, options) { const supplied = options.getReason ? options.getReason(record) : record.reason || record.message; return typeof supplied === 'string' ? supplied : supplied?.text || supplied?.reason || supplied?.summary || ''; }
  function button(action, text, attrs, klass) { return '<button type="button" class="' + (klass || 'rm-link') + '" data-action="' + action + '" ' + (attrs || '') + '>' + text + '</button>'; }
  function recordAttrs(id) { return 'data-revision="' + escape(id) + '"'; }
  function tag(text, type) { return '<span class="rm-tag ' + (type || '') + '">' + escape(text) + '</span>'; }
  function render(options) {
    options = options || {}; const file = options.file; let graph;
    try { graph = layout(file, options); }
    catch (err) { return '<section class="rm-view"><div class="rm-empty" role="alert"><strong>保存关系需要核对</strong><p>' + escape(err.message) + '</p></div></section>'; }
    const byId = new Map(graph.nodes.map(node => [node.id, node]));
    const focusOptions = [['all', '全部配置'], ['baseline', '基线主线'], ...file.branches.filter(b => b.kind === 'machine').map(b => [b.id, branchName(b)])];
    const toolbar = '<div class="rm-toolbar"><label class="rm-focus-label">聚焦 <select id="roadmap-focus" aria-label="聚焦配置">' + focusOptions.map(([id, name]) => '<option value="' + escape(id) + '" ' + (graph.branchFilter === id ? 'selected' : '') + '>' + escape(name) + '</option>').join('') + '</select></label><label class="rm-search"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0"/></svg><input id="roadmap-search" type="search" data-search="roadmap" aria-label="搜索路线图" placeholder="搜索机台、软件版本或修改说明" value="' + escape(options.query || '') + '"></label><span class="rm-toolbar-spacer"></span><span class="rm-record-count">' + graph.total + ' 条记录</span></div>';
    const pending = graph.pendingBranches.length ? '<div class="rm-pending"><span>待初始化</span>' + graph.pendingBranches.map(b => '<a href="#/files/' + encodeURIComponent(file.id) + '/parameters?branch=' + encodeURIComponent(b.id) + '">软件 ' + escape(b.releaseId) + '</a>').join('') + '</div>' : '';
    const axis = '<div class="rm-axis">' + graph.visibleLanes.map(lane => button('roadmap-focus', escape(lane.label), 'data-branch="' + escape(lane.id) + '" title="聚焦' + escape(lane.label) + '" style="left:' + (lane.x - 23) + 'px;--rm-lane-color:' + lane.color + '"', 'rm-lane-label' + (graph.branchFilter === lane.id ? ' is-focused' : ''))).join('') + '</div>';
    const header = '<div class="rm-header" role="row">' + '<div class="rm-header-cell rm-graph-heading" role="columnheader">' + axis + '</div><div class="rm-header-cell" role="columnheader">保存记录 / 修改说明</div><div class="rm-header-cell" role="columnheader">软件版本</div><div class="rm-header-cell" role="columnheader">配置对象 / 采用基线</div><div class="rm-header-cell" role="columnheader">操作人 / 时间</div><div class="rm-header-cell" role="columnheader"><span class="visually-hidden">详情</span></div></div>';
    const paths = graph.visibleEdges.map(edge => '<path class="rm-edge' + (!edge.focused ? ' is-dimmed' : '') + (edge.boundary?.startsWith('filtered') || edge.boundary === 'missing-parent' ? ' is-continuation' : '') + '" data-child="' + escape(edge.childId) + '" data-parent="' + escape(edge.parentId) + '" data-kind="' + edge.kind + '"' + (edge.boundary ? ' data-boundary="' + edge.boundary + '"' : '') + ' d="' + edge.path + '" stroke="' + edge.color + '"/>' ).join('');
    const symbols = graph.visibleNodes.map(node => {
      const common = 'stroke="' + node.color + '"', opacity = node.focused || node.selected ? '' : ' opacity=".3"';
      const halo = node.selected ? '<circle cx="' + node.x + '" cy="' + node.y + '" r="12" fill="#dceaff"/>' : '';
      const mark = node.kind === 'initial' ? '<rect x="' + (node.x - 4.5) + '" y="' + (node.y - 4.5) + '" width="9" height="9" rx="1" fill="white" ' + common + ' stroke-width="2"/>' : node.parentIds.length > 1 ? '<path d="M ' + node.x + ' ' + (node.y - 6) + ' l 6 6 -6 6 -6 -6 Z" fill="' + node.color + '" stroke="white" stroke-width="1.5"/>' : '<circle cx="' + node.x + '" cy="' + node.y + '" r="4.5" fill="' + node.color + '" stroke="white" stroke-width="1.5"/>';
      return '<g data-node-symbol="' + escape(node.id) + '"' + opacity + '>' + halo + (node.isHead ? '<circle cx="' + node.x + '" cy="' + node.y + '" r="8.5" fill="white" ' + common + ' stroke-width="1.5"/>' : '') + mark + '</g>';
    }).join('');
    const svg = '<svg class="rm-lines" width="' + graph.graphWidth + '" height="' + graph.canvasHeight + '" viewBox="0 0 ' + graph.graphWidth + ' ' + Math.max(1, graph.canvasHeight) + '" aria-hidden="true" focusable="false">' + paths + symbols + '</svg>';
    const rows = graph.visibleNodes.map(node => {
      const r = node.revision, actor = options.actorLabel ? options.actorLabel(r.authorId, r) : r.authorName || '作者未记录', summary = reason(r, options), attrs = recordAttrs(r.id);
      const parentNames = node.parentIds.map(id => byId.get(id)?.title || '来源记录未载入');
      const state = node.isHead ? tag(node.kind === 'baseline' ? '当前基线' : node.kind === 'machine' ? '当前配置' : '当前', 'is-head') : '';
      const operation = r.restoreRevisionId ? tag('恢复') : r.parentIds.length > 1 ? tag('合并') : r.kind === 'initial' ? tag('初始文件') : '';
      const adopted = node.adoptedBy.length ? button('roadmap-detail', escape(node.adoptedBy.length === 1 ? node.adoptedBy[0].label + ' 正在采用' : node.adoptedBy.length + ' 台机台正在采用'), attrs + ' title="' + escape(node.adoptedBy.map(b => b.label).join('、')) + '"', 'rm-adoption') : '';
      const basis = node.kind === 'machine' && r.baseRevisionId ? byId.get(r.baseRevisionId) : null;
      const basisText = basis ? button('roadmap-select', '采用 ' + escape(basis.revision.releaseId) + ' · ' + escape(formatRevision(file, basis.revision, { compact: true })), recordAttrs(basis.id) + ' title="' + escape(basis.title) + '"', 'rm-basis') : node.kind === 'machine' ? '<span class="rm-unrecorded">采用基线未记录</span>' : adopted;
      const filteredParents = graph.visibleEdges.filter(edge => edge.childId === node.id && ['filtered-parent', 'missing-parent'].includes(edge.boundary));
      const continuation = filteredParents.length ? '<span class="rm-outside" title="' + escape(parentNames.join('；')) + '">' + (filteredParents.some(e => e.boundary === 'missing-parent') ? '来源记录未载入' : '来源在筛选范围外') + '</span>' : '';
      return '<div class="rm-row' + (node.selected ? ' is-selected' : '') + (!node.focused && !node.selected ? ' is-dimmed' : '') + '" role="row" aria-rowindex="' + (node.displayIndex + 2) + '" data-roadmap-row="' + escape(node.id) + '"' + '><div class="rm-cell rm-graph-cell" role="cell">' + button('roadmap-select', '<span class="visually-hidden">' + escape(node.title) + '</span>', attrs + ' aria-label="选择 ' + escape(node.title + (parentNames.length ? '；来源：' + parentNames.join('；') : '；独立起点')) + '" title="' + escape(node.title) + '" style="left:' + (node.x - 14) + 'px"' + (node.selected ? ' aria-pressed="true"' : ' aria-pressed="false"'), 'rm-node') + '</div><div class="rm-cell rm-record-cell" role="cell"><div class="rm-record-title-row">' + button('roadmap-select', escape(node.title), attrs + ' title="' + escape(node.title) + '"', 'rm-record-title') + state + operation + '</div><div class="rm-reason" title="' + escape(summary) + '">' + escape(summary || '未填写修改说明') + continuation + '</div></div><div class="rm-cell rm-release-cell" role="cell"><span class="rm-release">' + escape(r.releaseId || '未记录') + '</span></div><div class="rm-cell rm-object-cell" role="cell"><span class="rm-object-name" title="' + escape(node.branchLabel) + '">' + escape(node.branchLabel) + '</span>' + basisText + '</div><div class="rm-cell rm-author-cell" role="cell"><span class="rm-author">' + escape(actor) + '</span><time datetime="' + escape(r.at || '') + '">' + escape(timeLabel(r, options.timeZone)) + '</time></div><div class="rm-cell rm-detail-cell" role="cell">' + button('roadmap-detail', '详情', attrs + ' aria-label="查看 ' + escape(node.title) + ' 详情"', 'rm-detail') + '</div></div>';
    }).join('');
    const empty = !graph.visibleNodes.length ? '<div class="rm-empty"><strong>' + (graph.totalRecords ? '没有匹配的保存记录' : '还没有保存记录') + '</strong><p>' + (graph.totalRecords ? '调整搜索条件后继续查看。' : '从配置文件初始化，或明确选择已有记录作为起点。') + '</p></div>' : '';
    const continuationCount = graph.visibleEdges.filter(edge => edge.boundary && edge.boundary.endsWith('page')).length;
    const footer = '<div class="rm-footer"><span>' + (graph.total ? (graph.offset + 1) + '–' + Math.min(graph.offset + graph.pageSize, graph.total) + ' / ' + graph.total + ' 条' : '0 条记录') + '</span>' + (continuationCount ? '<span class="rm-page-context">' + continuationCount + ' 条连线延续至其他页</span>' : '') + (graph.unknownParents.length ? '<span class="rm-warning">' + graph.unknownParents.length + ' 条来源未载入</span>' : '') + '<span class="rm-toolbar-spacer"></span><label>每页 <select id="roadmap-page-size" aria-label="路线图每页记录数">' + [30, 50, 100].map(n => '<option value="' + n + '" ' + (graph.pageSize === n ? 'selected' : '') + '>' + n + '</option>').join('') + '</select></label>' + button('roadmap-prev', '上一页', 'data-page="' + Math.max(0, graph.page - 1) + '" ' + (graph.page ? '' : 'disabled'), 'btn sm') + '<span>' + (graph.page + 1) + ' / ' + graph.pageCount + '</span>' + button('roadmap-next', '下一页', 'data-page="' + Math.min(graph.pageCount - 1, graph.page + 1) + '" ' + (graph.page + 1 < graph.pageCount ? '' : 'disabled'), 'btn sm') + '</div>';
    return '<section class="rm-view" aria-label="版本路线图" data-roadmap-file="' + escape(file.id) + '">' + toolbar + pending + '<div class="rm-scroll" tabindex="0" aria-label="保存关系与记录；可横向滚动查看机台轨道"><div class="rm-table" role="table" aria-label="配置文件保存关系" aria-rowcount="' + (graph.total + 1) + '" aria-colcount="6" style="--rm-graph-width:' + graph.graphWidth + 'px;--rm-row-height:' + ROW_HEIGHT + 'px">' + header + '<div class="rm-canvas" style="min-height:' + graph.canvasHeight + 'px">' + svg + rows + '</div>' + empty + '</div></div>' + footer + '</section>';
  }
  return { layout, render, formatRevision, colors: COLORS, rowHeight: ROW_HEIGHT };
});
