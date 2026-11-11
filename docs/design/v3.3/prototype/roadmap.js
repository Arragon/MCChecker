/* Horizontal configuration roadmap: immutable saved records projected into
 * family, release and history views. No event handlers, model writes or network.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MCPrototypeRoadmap = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const COLORS = Object.freeze(['#2563eb', '#148366', '#8058b5', '#147e9b', '#ad527d', '#a57925']);
  const COLUMN_WIDTH = 292, ROW_HEIGHT = 200, CARD_WIDTH = 252, CARD_HEIGHT = 164, LABEL_WIDTH = 164, HEADER_HEIGHT = 76;
  const UNKNOWN_RELEASE = '__unknown_release__', UNKNOWN_FAMILY = '__ungrouped__';
  const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const integer = (value, fallback) => value !== null && value !== '' && Number.isFinite(Number(value)) ? Math.floor(Number(value)) : fallback;
  const limit = (value, fallback) => Math.min(30, Math.max(1, integer(value, fallback)));
  const unique = values => [...new Set(values)];
  const releaseKey = value => value == null || value === '' ? UNKNOWN_RELEASE : String(value);
  const domId = value => 'rm-' + Array.from(String(value)).map(c => c.codePointAt(0).toString(16)).join('-');
  function error(code, message) { const e = new Error(message); e.code = code; throw e; }
  function branchName(branch) { return branch?.kind === 'baseline' ? '版本基线' : branch?.label || '归属未记录'; }
  function readableRelease(file, id) {
    const entries = file?.releases;
    const entry = Array.isArray(entries) ? entries.find(r => String(typeof r === 'object' && r ? r.id || r.releaseId || r.version : r) === String(id)) : entries && typeof entries === 'object' ? entries[id] : null;
    return entry && typeof entry === 'object' ? entry.label || entry.version || id : typeof entry === 'string' ? entry : id;
  }
  function formatRevision(file, input, options) {
    options = options || {};
    const r = typeof input === 'string' ? file?.revisions?.find(r => r.id === input) : input;
    if (!r) return options.compact ? '记录未载入' : '保存记录未载入';
    const release = r.releaseId ? '软件 ' + (options.releaseLabel || readableRelease(file, r.releaseId)) : '软件版本未记录';
    if (r.kind === 'initial') return options.compact ? '初始文件' : '初始文件 · ' + release;
    const match = /^(?:B|r)(\d+)$/i.exec(String(r.label || ''));
    const count = match && Number.isSafeInteger(Number(match[1])) ? Number(match[1]) : null;
    const saved = count !== null ? '第' + count + '次保存' : '保存记录';
    if (options.compact) return saved;
    return (options.objectLabel || branchName(file?.branches?.find(b => b.id === r.branchId))) + ' · ' + release + ' · ' + saved;
  }
  function timestamp(record) { const value = Date.parse(record.at || ''); return Number.isFinite(value) ? value : -Infinity; }
  // Parent-first constraints outrank wall-clock time. Returned order is newest first.
  function ordered(revisions, byId) {
    const pending = new Map(revisions.map(r => [r.id, 0])), index = new Map(revisions.map((r, i) => [r.id, i]));
    revisions.forEach(r => unique(r.parentIds).forEach(id => { if (byId.has(id)) pending.set(id, pending.get(id) + 1); }));
    const heap = [], ahead = (a, b) => timestamp(a) > timestamp(b) || timestamp(a) === timestamp(b) && index.get(a.id) > index.get(b.id);
    function push(value) { let i = heap.length; heap.push(value); while (i) { const p = (i - 1) >> 1; if (!ahead(heap[i], heap[p])) break; [heap[i], heap[p]] = [heap[p], heap[i]]; i = p; } }
    function pop() { const first = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; while (true) { let best = i, left = i * 2 + 1, right = left + 1; if (left < heap.length && ahead(heap[left], heap[best])) best = left; if (right < heap.length && ahead(heap[right], heap[best])) best = right; if (best === i) break; [heap[i], heap[best]] = [heap[best], heap[i]]; i = best; } } return first; }
    revisions.forEach(r => { if (!pending.get(r.id)) push(r); });
    const result = [];
    while (heap.length) { const r = pop(); result.push(r); for (const id of unique(r.parentIds)) if (byId.has(id)) { pending.set(id, pending.get(id) - 1); if (!pending.get(id)) push(byId.get(id)); } }
    if (result.length !== revisions.length) error('INVALID_DAG', '保存关系存在循环，请核对来源记录');
    return result;
  }
  function inputEntries(value) {
    if (Array.isArray(value)) return value.map(x => typeof x === 'object' && x ? Object.assign({}, x) : { id: String(x) });
    if (value && typeof value === 'object') return Object.entries(value).map(([id, x]) => typeof x === 'object' && x ? Object.assign({ id }, x) : { id, label: String(x) });
    return [];
  }
  function versionMetadata(file) {
    const familyMap = new Map(), releaseMap = new Map(), familyMembership = new Map();
    inputEntries(file.releaseFamilies).forEach((entry, i) => {
      const id = String(entry.id || entry.familyId || entry.label || '');
      if (!id) return;
      familyMap.set(id, { id, label: String(entry.label || entry.name || id), order: integer(entry.order, i), releaseIds: [] });
      const members = entry.releaseIds || entry.releases || [];
      (Array.isArray(members) ? members : Object.keys(members)).forEach(r => familyMembership.set(releaseKey(typeof r === 'object' ? r.id || r.version : r), id));
    });
    function fallbackFamily(id) {
      const known = /^69[0-9a-z]$/i.test(id) ? '69X' : /^6a[0-9a-z]$/i.test(id) ? '6AX' : null;
      if (!known) return UNKNOWN_FAMILY;
      return [...familyMap.values()].find(f => f.id.toUpperCase() === known || f.label.toUpperCase() === known)?.id || known;
    }
    function ensureRelease(id, entry) {
      id = releaseKey(id); entry = entry || {};
      const previous = releaseMap.get(id);
      const familyId = String(entry.familyId || familyMembership.get(id) || previous?.familyId || fallbackFamily(id));
      if (!familyMap.has(familyId)) familyMap.set(familyId, { id: familyId, label: familyId === UNKNOWN_FAMILY ? '未分组' : familyId, order: Number.MAX_SAFE_INTEGER, releaseIds: [] });
      const r = { id, label: String(entry.label || entry.version || previous?.label || (id === UNKNOWN_RELEASE ? '版本未记录' : id)), familyId, order: integer(entry.order, previous?.order ?? Number.MAX_SAFE_INTEGER) };
      releaseMap.set(id, r); return r;
    }
    inputEntries(file.releases).forEach(entry => ensureRelease(entry.id || entry.releaseId || entry.version, entry));
    familyMembership.forEach((familyId, id) => { if (!releaseMap.has(id)) ensureRelease(id, { familyId }); });
    file.branches.forEach(b => { if (!releaseMap.has(releaseKey(b.releaseId))) ensureRelease(b.releaseId); });
    file.revisions.forEach(r => { if (!releaseMap.has(releaseKey(r.releaseId))) ensureRelease(r.releaseId); });
    const compare = (a, b) => a.order - b.order || a.id.localeCompare(b.id, 'en', { numeric: true, sensitivity: 'base' });
    const families = [...familyMap.values()].sort(compare);
    const releases = [];
    families.forEach((family, familyIndex) => {
      const members = [...releaseMap.values()].filter(r => r.familyId === family.id).sort(compare);
      family.index = familyIndex; family.releaseIds = members.map(r => r.id); family.releases = members;
      members.forEach(r => { r.index = releases.length; releases.push(r); });
    });
    return { families, releases, familyMap: new Map(families.map(f => [f.id, f])), releaseMap };
  }
  function relatedIds(focus, branches, byId, revisions) {
    if (!focus || focus === 'all') return new Set(revisions.map(r => r.id));
    if (focus === 'baseline') return new Set(revisions.filter(r => branches.get(r.branchId)?.kind === 'baseline').map(r => r.id));
    const queue = revisions.filter(r => r.branchId === focus).map(r => r.id), found = new Set();
    for (let i = 0; i < queue.length; i++) { const id = queue[i]; if (found.has(id)) continue; found.add(id); const r = byId.get(id); if (r) queue.push(...r.parentIds); }
    return found;
  }
  function edgeKind(child, parent, order, branches) {
    if (child.restoreRevisionId === parent?.id) return 'restore';
    const a = branches.get(child.branchId), b = branches.get(parent?.branchId);
    if (a?.kind === 'machine' && b?.kind === 'baseline') return child.parentIds.length > 1 ? 'adopt' : 'fork';
    if (order > 0) return 'merge';
    if (parent?.kind === 'initial') return 'initialize';
    return 'lineage';
  }
  function reason(record, options) {
    const supplied = options.getReason ? options.getReason(record) : record.reason || record.message;
    const text = typeof supplied === 'string' ? supplied : supplied?.text || supplied?.reason || supplied?.summary || '';
    return record.reason === null ? '原因未记录' + (text ? ' · 历史说明：' + text : '') : text;
  }
  function actorLabel(record, options) { return options.actorLabel ? String(options.actorLabel(record.authorId, record)) : record.authorName || '作者未记录'; }
  function canonical(file, options) {
    if (!file || !Array.isArray(file.revisions) || !Array.isArray(file.branches)) error('INVALID_FILE', '配置文件的保存关系不可读取');
    const byId = new Map(), branches = new Map();
    file.branches.forEach(b => { if (!b.id || branches.has(b.id)) error('DUPLICATE_BRANCH', '配置对象身份重复'); branches.set(b.id, b); });
    file.revisions.forEach(r => { if (!r.id || byId.has(r.id)) error('DUPLICATE_REVISION', '保存记录身份重复'); if (!Array.isArray(r.parentIds)) error('INVALID_PARENTS', '保存记录缺少明确的父记录列表'); byId.set(r.id, r); });
    const sorted = ordered(file.revisions, byId), metadata = versionMetadata(file);
    const assets = new Map((options.assets || []).map(a => [a.id, a]));
    const lanes = [{ id: 'baseline', label: '版本基线', kind: 'baseline', index: 0, model: null, color: COLORS[0], branchIds: file.branches.filter(b => b.kind === 'baseline').map(b => b.id) }];
    file.branches.filter(b => b.kind !== 'baseline').forEach(b => {
      const asset = assets.get(b.machineId), index = lanes.length;
      lanes.push({ id: b.id, label: asset?.name || branchName(b), kind: b.kind || 'unknown', index, machineId: b.machineId || null, model: asset?.model || null, color: COLORS[1 + (index - 1) % (COLORS.length - 1)], branchIds: [b.id] });
    });
    sorted.filter(r => !branches.has(r.branchId)).forEach(r => {
      if (!lanes.some(l => l.id === 'unknown:' + r.branchId)) lanes.push({ id: 'unknown:' + r.branchId, label: '归属未记录', kind: 'unknown', index: lanes.length, model: null, color: '#7d8795', branchIds: [r.branchId] });
    });
    const laneByBranch = new Map(); lanes.forEach(lane => lane.branchIds.forEach(id => laneByBranch.set(id, lane)));
    const heads = new Map(), adopted = new Map();
    file.branches.forEach(b => {
      if (b.headId && byId.has(b.headId)) { const list = heads.get(b.headId) || []; list.push(b); heads.set(b.headId, list); }
      const head = byId.get(b.headId);
      if (b.kind === 'machine' && head?.baseRevisionId) { const list = adopted.get(head.baseRevisionId) || []; list.push({ branchId: b.id, label: assets.get(b.machineId)?.name || branchName(b), machineId: b.machineId, headId: head.id }); adopted.set(head.baseRevisionId, list); }
    });
    const focus = String(options.branchFilter || 'all'), related = relatedIds(focus, branches, byId, sorted);
    const nodes = sorted.map((r, row) => {
      const lane = laneByBranch.get(r.branchId), b = branches.get(r.branchId), release = metadata.releaseMap.get(releaseKey(r.releaseId));
      return { id: r.id, revision: r, branchId: r.branchId, branchLabel: branchName(b), kind: r.kind === 'initial' ? 'initial' : b?.kind || 'unknown', objectKind: b?.kind || 'unknown', laneId: lane.id, laneIndex: lane.index, color: lane.color, row, parentIds: unique(r.parentIds), releaseId: release.id, familyId: release.familyId, isHead: heads.has(r.id), heads: (heads.get(r.id) || []).map(b => ({ id: b.id, label: branchName(b), releaseId: b.releaseId, kind: b.kind })), adoptedBy: adopted.get(r.id) || [], focused: related.has(r.id), selected: options.selectedRevisionId === r.id, title: formatRevision(file, r, { releaseLabel: release.label, objectLabel: branchName(b) }), historyStep: 1 };
    });
    const nodeById = new Map(nodes.map(n => [n.id, n])), lastInLane = new Map();
    // Within a release, parent constraints and lane occupancy define steps.
    // Date labels never determine the horizontal axis; gaps are not new saves.
    nodes.slice().reverse().forEach(node => {
      const key = JSON.stringify([node.releaseId, node.laneId]);
      const parentSteps = node.parentIds.map(id => nodeById.get(id)).filter(p => p && p.releaseId === node.releaseId).map(p => p.historyStep);
      node.historyStep = Math.max(lastInLane.get(key) || 0, ...parentSteps, 0) + 1;
      lastInLane.set(key, node.historyStep);
    });
    const edges = nodes.flatMap(node => node.parentIds.map((id, order) => {
      const parent = nodeById.get(id);
      return { id: JSON.stringify([node.id, id]), childId: node.id, parentId: id, from: node.id, to: id, parentExists: !!parent, childLane: node.laneId, parentLane: parent?.laneId || null, kind: parent ? edgeKind(node.revision, parent.revision, order, branches) : 'unknown', order, color: node.objectKind === 'baseline' && parent?.objectKind === 'machine' ? parent.color : node.color };
    }));
    return { nodes, edges, lanes, nodeById, branches, byId, metadata, focus };
  }
  function contextFor(data, options) {
    const level = ['families', 'releases', 'history'].includes(options.level) ? options.level : 'families';
    let family = null, release = null;
    if (level === 'releases') {
      family = options.familyId ? data.metadata.familyMap.get(String(options.familyId)) : data.metadata.families[0] || null;
      if (options.familyId && !family) error('INVALID_FAMILY', '此大版本不存在，请返回大版本选择');
    }
    if (level === 'history') {
      release = options.releaseId ? data.metadata.releaseMap.get(String(options.releaseId)) : null;
      if (options.releaseId && !release) error('INVALID_RELEASE', '此小版本不存在，请返回大版本选择');
      if (!release) {
        family = options.familyId ? data.metadata.familyMap.get(String(options.familyId)) : data.metadata.families[0] || null;
        release = family?.releases[0] || null;
      }
      family = release ? data.metadata.familyMap.get(release.familyId) : family;
    }
    return { level, familyId: family?.id || null, releaseId: release?.id || null, family, release };
  }
  function connector(parent, child) {
    const py = parent.y + CARD_HEIGHT / 2, cy = child.y + CARD_HEIGHT / 2;
    if (parent.columnIndex === child.columnIndex) {
      const x = parent.x + CARD_WIDTH, outer = x + 15;
      return 'M ' + x + ' ' + py + ' C ' + outer + ' ' + py + ', ' + outer + ' ' + cy + ', ' + (child.x + CARD_WIDTH) + ' ' + cy;
    }
    const forward = child.columnIndex > parent.columnIndex;
    const px = parent.x + (forward ? CARD_WIDTH : 0), cx = child.x + (forward ? 0 : CARD_WIDTH), middle = (px + cx) / 2;
    return 'M ' + px + ' ' + py + ' C ' + middle + ' ' + py + ', ' + middle + ' ' + cy + ', ' + cx + ' ' + cy;
  }
  function boundaryPath(node, direction, incoming) {
    const y = node.y + CARD_HEIGHT / 2, edge = node.x + (direction === 'right' ? CARD_WIDTH : 0), outside = edge + (direction === 'right' ? 17 : -17);
    return incoming ? 'M ' + outside + ' ' + y + ' L ' + edge + ' ' + y : 'M ' + edge + ' ' + y + ' L ' + outside + ' ' + y;
  }
  function layout(file, options) {
    options = options || {};
    const data = canonical(file, options), context = contextFor(data, options);
    const { nodes, edges, lanes, nodeById, metadata, focus } = data;
    const scoped = focus === 'all' ? nodes : nodes.filter(n => n.focused), scopeIds = new Set(scoped.map(n => n.id));
    const query = String(options.query || '').trim().toLocaleLowerCase();
    const laneMap = new Map(lanes.map(l => [l.id, l]));
    const matches = node => !query || [node.title, node.branchLabel, laneMap.get(node.laneId)?.model, metadata.familyMap.get(node.familyId)?.label, metadata.releaseMap.get(node.releaseId)?.label, reason(node.revision, options), actorLabel(node.revision, options)].filter(Boolean).join(' ').toLocaleLowerCase().includes(query);
    const searched = scoped.filter(matches);
    const inContext = n => context.level === 'families' || context.level === 'releases' && n.familyId === context.familyId || context.level === 'history' && n.releaseId === context.releaseId;
    const matching = searched.filter(inContext), matchingIds = matching.map(n => n.id), matchingSet = new Set(matchingIds);
    const expandedFamilies = unique(Array.isArray(options.expandedFamilies) ? options.expandedFamilies.map(String) : []);
    let columns = [];
    function familyColumn(f) { return { id: 'family:' + f.id, kind: 'family', familyId: f.id, releaseId: null, label: f.label, familyLabel: f.label }; }
    function releaseColumn(r) { return { id: 'release:' + r.id, kind: 'release', familyId: r.familyId, releaseId: r.id, label: r.label, familyLabel: metadata.familyMap.get(r.familyId).label }; }
    if (context.level === 'families') {
      metadata.families.forEach(family => {
        if (expandedFamilies.includes(family.id) && family.releases.length) columns.push(...family.releases.map(releaseColumn));
        else columns.push(familyColumn(family));
      });
    } else if (context.level === 'releases') columns = (context.family?.releases || []).map(releaseColumn);
    else if (context.release) {
      const steps = unique(scoped.filter(n => n.releaseId === context.releaseId).map(n => n.historyStep)).sort((a, b) => a - b);
      columns = (steps.length ? steps : [null]).map(step => ({ id: 'history:' + context.releaseId + ':' + (step || 'empty'), kind: 'history', familyId: context.familyId, releaseId: context.releaseId, familyLabel: context.family.label, label: '保存记录', step }));
    }
    const membersByColumn = new Map(), columnIds = new Set(columns.map(c => c.id)), expandedSet = new Set(expandedFamilies);
    matching.forEach(node => {
      const id = context.level === 'history' ? 'history:' + node.releaseId + ':' + node.historyStep : context.level === 'releases' || expandedSet.has(node.familyId) ? 'release:' + node.releaseId : 'family:' + node.familyId;
      if (!columnIds.has(id)) return;
      const members = membersByColumn.get(id) || []; members.push(node); membersByColumn.set(id, members);
    });
    if (query) columns = columns.filter(column => membersByColumn.has(column.id));
    columns = columns.map((c, index) => Object.assign({}, c, { index, x: index * COLUMN_WIDTH, width: COLUMN_WIDTH }));
    const scopedLaneIds = new Set((query ? searched : scoped).map(n => n.laneId));
    const projectedLanes = lanes.filter(l => l.id === 'baseline' || !query && focus === 'all' || scopedLaneIds.has(l.id) || !query && l.id === focus).map((lane, index) => Object.assign({}, lane, { projectedIndex: index }));
    const machines = projectedLanes.filter(l => l.id !== 'baseline');
    const columnLimit = limit(options.columnLimit, context.level === 'history' ? limit(options.pageSize, 12) : 12), laneLimit = limit(options.laneLimit, 12);
    const columnPageCount = Math.max(1, Math.ceil(columns.length / columnLimit)), lanePageCount = Math.max(1, Math.ceil(machines.length / laneLimit));
    const columnPage = Math.max(0, Math.min(integer(options.columnPage, context.level === 'history' ? integer(options.page, 0) : 0), columnPageCount - 1));
    const lanePage = Math.max(0, Math.min(integer(options.lanePage, 0), lanePageCount - 1));
    const columnOffset = columnPage * columnLimit, laneOffset = lanePage * laneLimit;
    const visibleColumns = columns.slice(columnOffset, columnOffset + columnLimit).map((c, i) => Object.assign({}, c, { visibleIndex: i, x: i * COLUMN_WIDTH }));
    const visibleLanes = [projectedLanes.find(l => l.id === 'baseline'), ...machines.slice(laneOffset, laneOffset + laneLimit)].filter(Boolean).map((lane, i) => Object.assign({}, lane, { visibleIndex: i, y: i * ROW_HEIGHT }));
    const visibleColumnIds = new Set(visibleColumns.map(c => c.id)), visibleLaneIds = new Set(visibleLanes.map(l => l.id));
    const machineIndices = new Map(machines.map((l, i) => [l.id, i]));
    const projectedNodes = [], representatives = new Map();
    for (const column of columns) {
      const members = membersByColumn.get(column.id) || [];
      if (column.kind === 'history') {
        members.forEach(node => {
          const projected = Object.assign({}, node, { kind: 'revision', recordKind: node.kind, revisionIds: [node.id], columnId: column.id, columnIndex: column.index });
          projectedNodes.push(projected); representatives.set(node.id, projected);
        });
      } else {
        const groups = new Map();
        members.forEach(node => { const group = groups.get(node.laneId) || []; group.push(node); groups.set(node.laneId, group); });
        groups.forEach((group, laneId) => {
          const lane = laneMap.get(laneId), newest = group[0];
          const projected = { id: JSON.stringify(['summary', column.id, laneId]), kind: column.kind === 'family' ? 'family-summary' : 'release-summary', objectKind: lane.kind, laneId, laneIndex: lane.index, color: lane.color, familyId: column.familyId, releaseId: column.releaseId, columnId: column.id, columnIndex: column.index, revisionIds: group.map(n => n.id), releaseIds: unique(group.map(n => n.releaseId)).sort((a, b) => metadata.releaseMap.get(a).index - metadata.releaseMap.get(b).index), newestRevisionId: newest.id, headRevisionIds: group.filter(n => n.isHead).map(n => n.id), title: column.label, selected: false, hiddenInternalEdgeIds: [] };
          projectedNodes.push(projected); group.forEach(node => representatives.set(node.id, projected));
        });
      }
    }
    function position(node) {
      const row = node.laneId === 'baseline' ? 0 : machineIndices.get(node.laneId) - laneOffset + 1;
      return Object.assign({}, node, { x: (node.columnIndex - columnOffset) * COLUMN_WIDTH + (COLUMN_WIDTH - CARD_WIDTH) / 2, y: row * ROW_HEIGHT + (ROW_HEIGHT - CARD_HEIGHT) / 2, width: CARD_WIDTH, height: CARD_HEIGHT, visibleRow: row });
    }
    const visibleNodes = projectedNodes.filter(n => visibleColumnIds.has(n.columnId) && visibleLaneIds.has(n.laneId)).map(position);
    const visibleNodeMap = new Map(visibleNodes.map(n => [n.id, n]));
    const aggregate = new Map(), hiddenInternalEdgeIds = [];
    const relevantEdges = edges.filter(edge => scopeIds.has(edge.childId));
    relevantEdges.forEach(edge => {
      const child = representatives.get(edge.childId), parent = representatives.get(edge.parentId);
      if (child && parent && child.id === parent.id) {
        hiddenInternalEdgeIds.push(edge.id); child.hiddenInternalEdgeIds?.push(edge.id); return;
      }
      const childVisible = child && visibleNodeMap.has(child.id), parentVisible = parent && visibleNodeMap.has(parent.id);
      if (!childVisible && !parentVisible) return;
      let boundary = null, direction = 'left', path;
      if (child && parent) {
        const a = position(parent), b = position(child);
        path = connector(a, b);
        if (!parentVisible || !childVisible) {
          const missing = !parentVisible ? parent : child;
          boundary = (!visibleLaneIds.has(missing.laneId) ? 'lane-page-' : 'column-page-') + (!parentVisible ? 'parent' : 'child');
        }
      } else {
        const childNode = nodeById.get(edge.childId), parentNode = nodeById.get(edge.parentId);
        const absent = child ? parentNode : childNode, visible = child ? visibleNodeMap.get(child.id) : visibleNodeMap.get(parent.id);
        const side = child ? 'parent' : 'child';
        boundary = !absent ? 'missing-' + side : !scopeIds.has(absent.id) ? 'outside-scope-' + side : !inContext(absent) ? 'outside-level-' + side : 'filtered-' + side;
        const absentRelease = absent && metadata.releaseMap.get(absent.releaseId), currentRelease = child ? metadata.releaseMap.get(childNode.releaseId) : metadata.releaseMap.get(parentNode.releaseId);
        direction = absentRelease && currentRelease && absentRelease.index > currentRelease.index ? 'right' : 'left';
        path = boundaryPath(visible, direction, !!child);
      }
      const key = JSON.stringify([child?.id || null, parent?.id || null, boundary, child ? null : nodeById.get(edge.childId)?.releaseId, parent ? null : nodeById.get(edge.parentId)?.releaseId, direction]);
      let group = aggregate.get(key);
      if (!group) {
        group = { id: key, childNodeId: child?.id || null, parentNodeId: parent?.id || null, childId: edge.childId, parentId: edge.parentId, childVisible: !!childVisible, parentVisible: !!parentVisible, boundary, direction, path, color: edge.color, kind: edge.kind, originalEdgeIds: [], originalEdges: [] };
        aggregate.set(key, group);
      }
      group.originalEdgeIds.push(edge.id); group.originalEdges.push(edge);
    });
    const visibleEdges = [...aggregate.values()].map(e => Object.assign(e, { count: e.originalEdgeIds.length }));
    const pendingBranches = file.branches.filter(b => !b.headId && (focus === 'all' || focus === 'baseline' && b.kind === 'baseline' || b.id === focus)).map(b => ({ id: b.id, label: branchName(b), releaseId: releaseKey(b.releaseId), familyId: metadata.releaseMap.get(releaseKey(b.releaseId)).familyId }));
    return Object.assign({}, context, {
      nodes, edges, lanes, families: metadata.families, releases: metadata.releases, visibleNodes, visibleEdges, projectedNodes, projectedLanes, columns, visibleColumns, visibleLanes, matchingIds, hiddenInternalEdgeIds, expandedFamilies,
      columnPage, columnLimit, columnPageCount, columnCount: columns.length, columnOffset, lanePage, laneLimit, lanePageCount, laneCount: machines.length, laneOffset,
      total: matching.length, totalRecords: nodes.length, scopeTotal: scoped.length, query, branchFilter: focus, pendingBranches,
      unknownParents: relevantEdges.filter(e => !e.parentExists).map(e => ({ childId: e.childId, parentId: e.parentId })),
      graphWidth: Math.max(1, visibleColumns.length) * COLUMN_WIDTH, canvasHeight: visibleLanes.length * ROW_HEIGHT, matrixWidth: LABEL_WIDTH + Math.max(1, visibleColumns.length) * COLUMN_WIDTH,
      rowHeight: ROW_HEIGHT, columnWidth: COLUMN_WIDTH, labelWidth: LABEL_WIDTH, headerHeight: HEADER_HEIGHT,
      // Read-only compatibility for callers transitioning from one-axis history.
      page: columnPage, pageSize: columnLimit, pageCount: columnPageCount
    });
  }
  function locateRevision(file, id, options) {
    options = options || {};
    const data = canonical(file, options), node = data.nodeById.get(id);
    if (!node) error('REVISION_NOT_FOUND', '此保存记录不存在或尚未载入');
    const branchFilter = data.focus === 'all' || node.focused ? data.focus : 'all';
    const target = { level: 'history', familyId: node.familyId, releaseId: node.releaseId, branchFilter, query: '', columnPage: 0, lanePage: 0, columnLimit: limit(options.columnLimit, 12), laneLimit: limit(options.laneLimit, 12), selectedRevisionId: node.id };
    const graph = layout(file, Object.assign({}, options, target));
    const column = graph.columns.find(c => c.kind === 'history' && c.step === node.historyStep);
    const machineIndex = graph.projectedLanes.filter(l => l.id !== 'baseline').findIndex(l => l.id === node.laneId);
    target.columnPage = Math.floor((column?.index || 0) / graph.columnLimit);
    target.lanePage = node.laneId === 'baseline' ? Math.max(0, Math.min(integer(options.lanePage, 0), graph.lanePageCount - 1)) : Math.floor(Math.max(0, machineIndex) / graph.laneLimit);
    return target;
  }
  function timeLabel(record, zone) {
    const date = new Date(record.at); if (!Number.isFinite(date.getTime())) return '时间未记录';
    return date.toLocaleString('zh-CN', { timeZone: zone || 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  }
  function button(action, text, attrs, klass) { return '<button type="button" class="' + (klass || 'rm-link') + '" data-action="' + action + '" ' + (attrs || '') + '>' + text + '</button>'; }
  function recordAttrs(id) { return 'data-revision="' + escape(id) + '"'; }
  function tag(text, type) { return '<span class="rm-tag ' + (type || '') + '">' + escape(text) + '</span>'; }
  function icon(kind) {
    return kind === 'baseline' ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3v18M16 3v18M4 8h16M4 16h16"/></svg>' : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v12H4zM8 17v3m8-3v3M7 9h5v4H7zM16 9h1m-1 4h1"/></svg>';
  }
  function safeLinks(record) {
    if (!Array.isArray(record.links)) return [];
    return record.links.map(link => typeof link === 'string' ? { url: link, label: link } : link).filter(link => {
      const raw = link?.url || link?.href || '';
      if (typeof raw !== 'string' || !/^https?:\/\//i.test(raw) || /[\u0000-\u0020\u007f-\u009f\\]/.test(raw)) return false;
      try { const parsed = new URL(raw); return ['https:', 'http:'].includes(parsed.protocol) && parsed.hostname && !parsed.username && !parsed.password; } catch (_) { return false; }
    }).map(link => ({ url: new URL(link.url || link.href).href, label: link.label || link.title || link.url || link.href }));
  }
  function releaseLabel(graph, id) { return graph.releases.find(r => r.id === id)?.label || (id === UNKNOWN_RELEASE ? '版本未记录' : id); }
  function summaryCard(node, graph, file, options) {
    const latest = graph.nodes.find(n => n.id === node.newestRevisionId)?.revision, lane = graph.lanes.find(l => l.id === node.laneId);
    const action = node.kind === 'family-summary' ? 'roadmap-open-family' : 'roadmap-open-release';
    const attrs = 'data-family="' + escape(node.familyId) + '"' + (node.releaseId ? ' data-release="' + escape(node.releaseId) + '"' : '');
    const versions = node.releaseIds.map(id => releaseLabel(graph, id));
    const range = versions.length > 1 ? versions[0] + ' – ' + versions[versions.length - 1] : versions[0] || '尚无小版本';
    const head = node.headRevisionIds.length ? tag(node.objectKind === 'baseline' ? '含当前基线' : '含当前配置', 'is-head') : '';
    return '<button type="button" class="rm-card rm-summary ' + (node.objectKind === 'baseline' ? 'is-baseline' : 'is-machine') + '" data-action="' + action + '" ' + attrs + ' data-roadmap-summary="' + (node.kind === 'family-summary' ? 'family' : 'release') + '" data-summary-id="' + escape(node.id) + '" style="--rm-lane-color:' + node.color + '" aria-label="' + escape(lane.label + ' · ' + node.title + ' · ' + node.revisionIds.length + ' 条保存记录，查看' + (node.kind === 'family-summary' ? '小版本' : '保存记录')) + '"><span class="rm-card-head"><span class="rm-card-object">' + icon(node.objectKind) + '<strong>' + escape(node.title) + '</strong></span>' + head + '</span><span class="rm-card-body"><span class="rm-summary-range">' + escape(range) + '<span>' + node.revisionIds.length + ' 条记录</span></span><span class="rm-reason" title="' + escape(reason(latest, options)) + '">' + escape(reason(latest, options) || '未填写修改原因') + '</span><span class="rm-card-meta">' + escape(actorLabel(latest, options)) + '<time datetime="' + escape(latest.at || '') + '">' + escape(timeLabel(latest, options.timeZone)) + '</time></span><span class="rm-summary-open">查看' + (node.kind === 'family-summary' ? '小版本' : '保存记录') + '<span aria-hidden="true">→</span></span></span></button>';
  }
  function recordCard(node, graph, file, options) {
    const r = node.revision, attrs = recordAttrs(r.id), links = safeLinks(r);
    const basis = node.objectKind === 'machine' && r.baseRevisionId ? graph.nodes.find(n => n.id === r.baseRevisionId) : null;
    const status = node.isHead ? tag(node.objectKind === 'baseline' ? '当前基线' : '当前配置', 'is-head') : r.restoreRevisionId ? tag('恢复') : r.parentIds.length > 1 ? tag('合并') : '';
    const basisLine = node.objectKind === 'machine' ? basis ? button('roadmap-select', '采用 ' + escape(releaseLabel(graph, basis.releaseId)) + ' · ' + escape(formatRevision(file, basis.revision, { compact: true })), recordAttrs(basis.id) + ' title="' + escape(basis.title) + '"', 'rm-basis') : '<span class="rm-unrecorded">采用基线未记录</span>' : node.adoptedBy.length ? button('roadmap-detail', escape(node.adoptedBy.length === 1 ? node.adoptedBy[0].label + ' 正在采用' : node.adoptedBy.length + ' 台机台正在采用'), attrs, 'rm-basis') : '<span class="rm-record-object">版本基线 · ' + escape(releaseLabel(graph, node.releaseId)) + '</span>';
    const outside = graph.visibleEdges.filter(edge => edge.boundary && edge.originalEdges.some(e => e.childId === node.id)).flatMap(e => e.originalEdges.filter(e => e.childId === node.id));
    const missing = outside.find(e => !e.parentExists), source = outside.find(e => e.parentExists && e.parentId !== basis?.id);
    const sourceNode = source && graph.nodes.find(n => n.id === source.parentId);
    const sourceLink = missing ? '<span class="rm-source-note">部分来源未载入</span>' : sourceNode ? button('roadmap-select', '来源 ' + escape(releaseLabel(graph, sourceNode.releaseId)), recordAttrs(sourceNode.id) + ' title="' + escape(sourceNode.title) + '"', 'rm-source-note') : '';
    const firstLink = links.length ? '<a class="rm-related-link" href="' + escape(links[0].url) + '" target="_blank" rel="noopener noreferrer" title="' + escape(links[0].label) + '">' + escape(links[0].label) + '</a>' : sourceLink;
    const more = links.length > 1 ? button('roadmap-detail', '+' + (links.length - 1) + ' 链接', attrs, 'rm-more-links') : '';
    return '<article class="rm-card rm-revision ' + (node.objectKind === 'baseline' ? 'is-baseline' : 'is-machine') + (node.selected ? ' is-selected' : '') + '" data-roadmap-record="' + escape(r.id) + '" data-roadmap-row="' + escape(r.id) + '" ' + attrs + ' style="--rm-lane-color:' + node.color + '" aria-label="' + escape(node.title) + '"><div class="rm-card-head">' + button('roadmap-select', icon(node.objectKind) + '<strong>' + escape(formatRevision(file, r, { compact: true })) + '</strong>', attrs + ' aria-pressed="' + (node.selected ? 'true' : 'false') + '" aria-label="选择 ' + escape(node.title) + '"', 'rm-card-select') + status + '</div><div class="rm-card-body"><p class="rm-reason" title="' + escape(reason(r, options)) + '">' + escape(reason(r, options) || '未填写修改原因') + '</p><div class="rm-card-meta"><span>' + escape(actorLabel(r, options)) + '</span><time datetime="' + escape(r.at || '') + '">' + escape(timeLabel(r, options.timeZone)) + '</time></div><div class="rm-basis-line">' + basisLine + '</div><div class="rm-card-footer">' + firstLink + more + '<span class="rm-spacer"></span>' + button('roadmap-detail', '详情', attrs + ' aria-label="查看 ' + escape(node.title) + ' 详情"', 'rm-detail') + '</div></div></article>';
  }
  function breadcrumbs(graph) {
    const first = button('roadmap-overview', '大版本', graph.level === 'families' ? 'aria-current="page"' : '', 'rm-crumb' + (graph.level === 'families' ? ' is-current' : ''));
    const second = graph.family ? button('roadmap-open-family', escape(graph.family.label) + ' · 小版本', 'data-family="' + escape(graph.familyId) + '"' + (graph.level === 'releases' ? ' aria-current="page"' : ''), 'rm-crumb' + (graph.level === 'releases' ? ' is-current' : '')) : '<span class="rm-crumb is-unavailable">小版本</span>';
    const third = graph.release ? '<span class="rm-crumb is-current" aria-current="page">' + escape(graph.release.label) + ' · 保存记录</span>' : '<span class="rm-crumb is-unavailable">保存记录</span>';
    return '<nav class="rm-breadcrumbs" aria-label="路线图层级">' + first + '<span aria-hidden="true">/</span>' + second + '<span aria-hidden="true">/</span>' + third + '</nav>';
  }
  function matrixHeader(graph, matrixId) {
    const groups = [];
    graph.visibleColumns.forEach((column, index) => {
      const previous = groups[groups.length - 1];
      if (previous && previous.familyId === column.familyId) previous.span++;
      else groups.push({ familyId: column.familyId, label: column.familyLabel, start: index, span: 1 });
    });
    if (!groups.length && graph.family) groups.push({ familyId: graph.family.id, label: graph.family.label, start: 0, span: 1 });
    const corner = '<div class="rm-axis-corner" data-roadmap-corner role="columnheader" aria-colindex="1" aria-rowspan="2"><strong>配置对象</strong><span>软件版本 →</span></div>';
    const familyHeads = groups.map(group => {
      const family = graph.families.find(f => f.id === group.familyId), expanded = graph.expandedFamilies.includes(group.familyId);
      const title = graph.level === 'families' ? button('roadmap-open-family', escape(group.label), 'data-family="' + escape(group.familyId) + '"', 'rm-family-name') : '<strong>' + escape(group.label) + '</strong>';
      const toggle = graph.level === 'families' && family.releaseIds.length ? button('roadmap-toggle-family', expanded ? '收起' : '展开小版本', 'data-family="' + escape(group.familyId) + '" aria-expanded="' + expanded + '" aria-controls="' + matrixId + '"', 'rm-expand') : '';
      return '<div class="rm-family-heading" data-roadmap-family="' + escape(group.familyId) + '" role="columnheader" aria-colindex="' + (group.start + 2) + '" aria-colspan="' + group.span + '" style="grid-column:' + (group.start + 2) + ' / span ' + group.span + '">' + title + toggle + '</div>';
    }).join('');
    const lower = graph.level === 'history' && graph.release ? '<div class="rm-column-heading is-group-start" data-roadmap-release="' + escape(graph.releaseId) + '" role="columnheader" aria-colindex="2" aria-colspan="' + Math.max(1, graph.visibleColumns.length) + '" style="grid-column:2 / span ' + Math.max(1, graph.visibleColumns.length) + '"><span class="rm-history-heading">软件 ' + escape(graph.release.label) + ' · 保存记录</span></div>' : graph.visibleColumns.map((column, index) => {
      let content = escape(column.kind === 'family' ? '全部小版本' : column.label);
      if (column.kind === 'release') content = button('roadmap-open-release', escape(column.label), 'data-family="' + escape(column.familyId) + '" data-release="' + escape(column.releaseId) + '"', 'rm-release-heading-link');
      const releaseAttr = column.releaseId ? ' data-roadmap-release="' + escape(column.releaseId) + '"' : '';
      const previous = graph.visibleColumns[index - 1];
      return '<div class="rm-column-heading' + (!previous || previous.familyId !== column.familyId ? ' is-group-start' : '') + '" data-roadmap-column="' + escape(column.id) + '"' + releaseAttr + ' role="columnheader" aria-colindex="' + (index + 2) + '" style="grid-column:' + (index + 2) + '">' + content + (column.kind === 'history' ? '<span>' + escape(releaseLabel(graph, column.releaseId)) + '</span>' : '') + '</div>';
    }).join('');
    return '<div class="rm-axis" data-roadmap-axis role="rowgroup"><div class="rm-axis-row" role="row" aria-rowindex="1">' + corner + familyHeads + '</div><div class="rm-axis-row" role="row" aria-rowindex="2">' + lower + '</div></div>';
  }
  function windowControls(graph) {
    function select(id, value, label) { return '<label>' + label + ' <select id="' + id + '" aria-label="' + label + '">' + [6, 12, 20, 30].map(n => '<option value="' + n + '"' + (value === n ? ' selected' : '') + '>' + n + '</option>').join('') + '</select></label>'; }
    const columnSummary = graph.level === 'history' ? '保存记录 · ' + (graph.columnPage + 1) + ' / ' + graph.columnPageCount + ' 组' : '版本列 ' + (graph.columnCount ? graph.columnOffset + 1 + '–' + Math.min(graph.columnOffset + graph.columnLimit, graph.columnCount) : '0') + ' / ' + graph.columnCount;
    const columns = '<div class="rm-window-control"><span>' + columnSummary + '</span>' + (graph.columnCount > 12 || graph.columnLimit !== 12 ? button('roadmap-column-prev', '前一组', 'data-page="' + Math.max(0, graph.columnPage - 1) + '" ' + (graph.columnPage ? '' : 'disabled'), 'rm-page-button') + button('roadmap-column-next', '后一组', 'data-page="' + Math.min(graph.columnPageCount - 1, graph.columnPage + 1) + '" ' + (graph.columnPage + 1 < graph.columnPageCount ? '' : 'disabled'), 'rm-page-button') + select('roadmap-column-limit', graph.columnLimit, '每组列数') : '') + '</div>';
    const lanes = '<div class="rm-window-control"><span>机台 ' + (graph.laneCount ? graph.laneOffset + 1 + '–' + Math.min(graph.laneOffset + graph.laneLimit, graph.laneCount) : '0') + ' / ' + graph.laneCount + '</span>' + (graph.laneCount > 12 || graph.laneLimit !== 12 ? button('roadmap-lane-prev', '上一组', 'data-page="' + Math.max(0, graph.lanePage - 1) + '" ' + (graph.lanePage ? '' : 'disabled'), 'rm-page-button') + button('roadmap-lane-next', '下一组', 'data-page="' + Math.min(graph.lanePageCount - 1, graph.lanePage + 1) + '" ' + (graph.lanePage + 1 < graph.lanePageCount ? '' : 'disabled'), 'rm-page-button') + select('roadmap-lane-limit', graph.laneLimit, '每组机台') : '') + '</div>';
    return '<footer class="rm-footer">' + columns + '<span class="rm-spacer"></span>' + lanes + '</footer>';
  }
  function render(options, preparedGraph) {
    options = options || {}; const file = options.file; let graph = preparedGraph;
    try { if (!graph) graph = layout(file, options); }
    catch (err) { return '<section class="rm-view" data-roadmap-level="error"><div class="rm-empty" role="alert"><strong>保存关系需要核对</strong><p>' + escape(err.message) + '</p>' + button('roadmap-overview', '返回大版本', '', 'rm-page-button') + '</div></section>'; }
    const matrixId = domId(file.id + ':matrix'), arrowId = domId(file.id + ':arrow'), marker = 'url(#' + arrowId + ')';
    const focusOptions = [['all', '全部配置'], ['baseline', '版本基线'], ...graph.lanes.filter(l => l.kind === 'machine').map(l => [l.id, l.label])];
    const toolbar = '<div class="rm-toolbar">' + breadcrumbs(graph) + '<span class="rm-spacer"></span><label class="rm-focus-label">聚焦 <select id="roadmap-focus" aria-label="聚焦配置">' + focusOptions.map(([id, name]) => '<option value="' + escape(id) + '" ' + (graph.branchFilter === id ? 'selected' : '') + '>' + escape(name) + '</option>').join('') + '</select></label><label class="rm-search"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0"/></svg><input id="roadmap-search" type="search" data-search="roadmap" aria-label="搜索路线图" placeholder="搜索机台、版本或修改原因" value="' + escape(options.query || '') + '"></label></div>';
    const pending = graph.pendingBranches.filter(b => graph.level === 'families' || graph.level === 'releases' && b.familyId === graph.familyId || graph.level === 'history' && b.releaseId === graph.releaseId);
    const pendingBar = pending.length ? '<div class="rm-pending"><span>待初始化</span>' + pending.map(b => '<a href="#/files/' + encodeURIComponent(file.id) + '/parameters?branch=' + encodeURIComponent(b.id) + '">软件 ' + escape(releaseLabel(graph, b.releaseId)) + '</a>').join('') + '</div>' : '';
    const paths = graph.visibleEdges.map(edge => {
      const description = edge.originalEdges.slice(0, 3).map(e => {
        const child = graph.nodes.find(n => n.id === e.childId), parent = graph.nodes.find(n => n.id === e.parentId);
        return (parent?.title || '来源记录未载入') + ' → ' + child.title;
      }).join('；');
      return '<path class="rm-edge' + (edge.boundary ? ' is-continuation' : '') + '" data-child-node="' + escape(edge.childNodeId || '') + '" data-parent-node="' + escape(edge.parentNodeId || '') + '" data-original-edge-count="' + edge.count + '"' + (edge.boundary ? ' data-boundary="' + edge.boundary + '"' : '') + ' d="' + edge.path + '" stroke="' + edge.color + '" marker-end="' + marker + '"><title>' + escape(description + (edge.count > 3 ? '；共 ' + edge.count + ' 条来源关系' : '')) + '</title></path>';
    }).join('');
    const svg = '<svg class="rm-links" width="' + graph.graphWidth + '" height="' + graph.canvasHeight + '" viewBox="0 0 ' + graph.graphWidth + ' ' + Math.max(1, graph.canvasHeight) + '" aria-hidden="true" focusable="false"><defs><marker id="' + arrowId + '" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M1 1l6 3-6 3" fill="none" stroke="#7f92aa" stroke-width="1.4"/></marker></defs>' + paths + '</svg>';
    const nodeByCell = new Map(graph.visibleNodes.map(n => [JSON.stringify([n.laneId, n.columnId]), n]));
    const rows = graph.visibleLanes.map(lane => {
      const head = '<div class="rm-lane-head" data-roadmap-lane-header="' + escape(lane.id) + '" role="rowheader"><span class="rm-lane-icon">' + icon(lane.kind) + '</span><strong title="' + escape(lane.label) + '">' + escape(lane.label) + '</strong><span>' + escape(lane.kind === 'baseline' ? '软件配置主线' : lane.model || '型号未记录') + '</span>' + (lane.kind === 'machine' ? button('roadmap-focus', '聚焦', 'data-branch="' + escape(lane.id) + '"', 'rm-lane-focus') : '') + '</div>';
      const cells = graph.visibleColumns.map((column, index) => {
        const node = nodeByCell.get(JSON.stringify([lane.id, column.id])), previous = graph.visibleColumns[index - 1];
        let content = node ? node.kind === 'revision' ? recordCard(node, graph, file, options) : summaryCard(node, graph, file, options) : '';
        const pendingHere = lane.id === 'baseline' ? pending.find(b => column.kind === 'family' ? b.familyId === column.familyId : b.releaseId === column.releaseId) : null;
        if (!content && pendingHere) content = '<div class="rm-cell-empty"><span>尚未初始化</span><a href="#/files/' + encodeURIComponent(file.id) + '/parameters?branch=' + encodeURIComponent(pendingHere.id) + '">设置初始配置</a></div>';
        return '<div class="rm-cell' + (!previous || previous.familyId !== column.familyId ? ' is-group-start' : '') + '" role="cell" data-roadmap-cell="' + escape(column.id) + '">' + content + '</div>';
      }).join('');
      return '<div class="rm-matrix-row' + (lane.kind === 'baseline' ? ' is-baseline' : '') + '" data-roadmap-lane="' + escape(lane.id) + '" role="row" style="--rm-lane-color:' + lane.color + '">' + head + cells + '</div>';
    }).join('');
    const empty = !graph.total && !pending.length ? '<div class="rm-empty rm-empty-overlay"><strong>' + (graph.query ? '没有匹配的保存记录' : graph.totalRecords ? '此范围还没有保存记录' : '还没有保存记录') + '</strong><p>' + (graph.query ? '调整搜索条件后继续查看。' : '选择其他版本，或从配置文件初始化。') + '</p></div>' : '';
    const vars = '--rm-column-count:' + Math.max(1, graph.visibleColumns.length) + ';--rm-column-width:' + COLUMN_WIDTH + 'px;--rm-row-height:' + ROW_HEIGHT + 'px;--rm-label-width:' + LABEL_WIDTH + 'px;--rm-header-height:' + HEADER_HEIGHT + 'px;--rm-matrix-width:' + graph.matrixWidth + 'px';
    return '<section class="rm-view" aria-label="版本路线图" data-roadmap-level="' + graph.level + '" data-roadmap-file="' + escape(file.id) + '">' + toolbar + pendingBar + '<div class="rm-scroll" data-roadmap-scroll tabindex="0" aria-label="版本与配置对象矩阵，可横向和纵向滚动"><div class="rm-matrix" id="' + matrixId + '" role="table" aria-label="配置版本关系" aria-rowcount="' + (graph.visibleLanes.length + 2) + '" aria-colcount="' + (Math.max(1, graph.visibleColumns.length) + 1) + '" style="' + vars + '">' + matrixHeader(graph, matrixId) + '<div class="rm-body" role="rowgroup" style="height:' + graph.canvasHeight + 'px">' + svg + rows + empty + '</div></div></div>' + windowControls(graph) + '</section>';
  }
  return { layout, render, locateRevision, formatRevision, colors: COLORS, rowHeight: ROW_HEIGHT, columnWidth: COLUMN_WIDTH, labelWidth: LABEL_WIDTH, headerHeight: HEADER_HEIGHT };
});
