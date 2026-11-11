/* MCChecker configuration prototype model — reconstructed 2026-11-11.
 * The previous scratch files were lost. This is a newly implemented, freshly
 * verified demonstration model, not a byte-for-byte recovery or production API.
 * No network, persistence, external dependencies, or trusted authentication.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MCPrototypeModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const fields = Object.freeze(['name', 'path', 'type', 'unit', 'min', 'max', 'value', 'mode']);
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const equal = (a, b) => Object.is(a, b);
  const copy = value => Array.isArray(value) ? value.map(copy) : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, copy(v)])) : value;
  function frozen(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {
      Object.values(value).forEach(frozen);
      Object.freeze(value);
    }
    return value;
  }
  function fail(code, message, details) {
    const error = new Error(message); error.code = code;
    if (details) Object.assign(error, details);
    throw error;
  }
  function normalizeLinks(value) {
    if (value === undefined) return [];
    const entries = typeof value === 'string' ? value.split(/\r\n?|\n/) : value;
    if (!Array.isArray(entries)) fail('INVALID_LINKS', '关联链接请逐行填写，或提供链接列表', { field: 'links' });
    const result = [], seen = new Set();
    entries.forEach((entry, linkIndex) => {
      const reject = () => fail('INVALID_LINK', '第 ' + (linkIndex + 1) + ' 个关联链接无效，请填写完整的 HTTP(S) 地址，不含账号密码或控制字符', { field: 'links', linkIndex });
      if (typeof entry !== 'string' || /[\u0000-\u001f\u007f-\u009f]/.test(entry)) reject();
      const text = entry.trim();
      if (!text) return;
      const authority = /^https?:\/\/([^/?#\\\s]+)/i.exec(text);
      if (!authority || /[\s\\]/u.test(text) || /@|:$/.test(authority[1]) || /%(?![0-9a-f]{2})/i.test(text) || /%(?:0[0-9a-f]|1[0-9a-f]|7f)|%c2%(?:8[0-9a-f]|9[0-9a-f])/i.test(text)) reject();
      let url;
      try { url = new URL(text); } catch (_) { reject(); }
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || /^\.+$/.test(url.hostname) || url.username || url.password) reject();
      const normalized = url.href;
      if (!seen.has(normalized)) { seen.add(normalized); result.push(normalized); }
    });
    return result;
  }
  // Only an explicitly supplied review title may be used as fallbackTitle.
  // Automatic operation descriptions must never fill in a missing reason.
  function changeMetadata(input, fallbackTitle) {
    input = input || {};
    const hasReason = own(input, 'reason'), hasMessage = own(input, 'message');
    const raw = hasReason ? input.reason : hasMessage ? input.message : fallbackTitle;
    if (raw === undefined) fail('REASON_REQUIRED', '请填写修改原因', { field: 'reason' });
    if (typeof raw !== 'string') fail('INVALID_REASON', '修改原因必须是文字', { field: 'reason' });
    const reason = raw.trim();
    if (!reason) fail('REASON_REQUIRED', '请填写修改原因', { field: 'reason' });
    if (hasReason && hasMessage) {
      if (typeof input.message !== 'string') fail('INVALID_REASON', '修改原因必须是文字', { field: 'reason' });
      if (input.message.trim() !== reason) fail('REASON_CONFLICT', '修改原因与兼容说明字段不一致，请保留一份明确原因', { field: 'reason' });
    }
    return { reason, message: reason, links: normalizeLinks(input.links) };
  }
  const numericPattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  // Compare decimal meaning without converting integer strings or fractional
  // text to binary floating point. Number() is only a finite-value guard.
  function decimal(value, label) {
    if (typeof value !== 'number' && typeof value !== 'string') fail('INVALID_VALUE', label + '：请输入有效数值');
    if (typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)) fail('PRECISION_RISK', label + '：大整数请以精确数字文本输入');
    const text = String(value);
    if (!numericPattern.test(text) || !Number.isFinite(Number(text))) fail('INVALID_VALUE', label + '：数值必须有限且格式有效');
    const match = /^([+-]?)(\d*\.?\d*)(?:[eE]([+-]?\d+))?$/.exec(text);
    const exponent = Number(match[3] || 0);
    if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 10000 || text.length > 12000) fail('INVALID_VALUE', label + '：数值超出原型处理范围');
    const parts = match[2].split('.');
    let digits = ((parts[0] || '') + (parts[1] || '')).replace(/^0+/, '') || '0';
    let scale = exponent - (parts[1] || '').length;
    while (digits.length > 1 && digits.endsWith('0')) { digits = digits.slice(0, -1); scale++; }
    return { sign: digits === '0' ? 0 : match[1] === '-' ? -1 : 1, digits, scale };
  }
  function compareDecimal(a, b) {
    const x = decimal(a, '数值'), y = decimal(b, '数值');
    if (x.sign !== y.sign) return x.sign < y.sign ? -1 : 1;
    if (!x.sign) return 0;
    const magnitudeA = x.digits.length + x.scale, magnitudeB = y.digits.length + y.scale;
    if (magnitudeA !== magnitudeB) return (magnitudeA < magnitudeB ? -1 : 1) * x.sign;
    const length = Math.max(x.digits.length, y.digits.length);
    const left = x.digits.padEnd(length, '0'), right = y.digits.padEnd(length, '0');
    return (left === right ? 0 : left < right ? -1 : 1) * x.sign;
  }
  function validated(params, requireContent) {
    if (!Array.isArray(params) || (requireContent && !params.length)) fail('INVALID_PARAMS', '请提供有效的参数列表');
    const ids = new Set(), paths = new Set();
    params.forEach(p => {
      if (!p || typeof p.id !== 'string' || !p.id.trim() || ids.has(p.id)) fail('INVALID_ID', '参数身份为空或重复');
      ids.add(p.id);
      if (typeof p.name !== 'string' || !p.name.trim()) fail('INVALID_NAME', '参数名称不能为空', { paramId: p.id });
      if (typeof p.path !== 'string' || !p.path.startsWith('/') || p.path.length < 2 || paths.has(p.path)) fail('INVALID_PATH', p.name + '：文件字段为空或重复', { paramId: p.id });
      paths.add(p.path);
      if (p.path.split('/').some(key => ['__proto__', 'prototype', 'constructor'].includes(key))) fail('INVALID_PATH', p.name + '：不支持此字段名');
      if (!['inherit', 'override', 'pin'].includes(p.mode)) fail('INVALID_MODE', p.name + '：取值方式无效');
      if (!['number', 'string', 'boolean'].includes(p.type)) fail('INVALID_TYPE', p.name + '：参数类型无效');
      if (typeof p.unit !== 'string') fail('INVALID_TYPE', p.name + '：单位必须是文字');
      if (!own(p, 'min') || !own(p, 'max')) fail('INVALID_BOUNDS', p.name + '：请明确填写范围或使用 null');
      if (p.type === 'number') {
        decimal(p.value, p.name);
        for (const key of ['min', 'max']) if (p[key] !== null) {
          try { decimal(p[key], p.name + ' ' + key); }
          catch (error) { if (error.code === 'PRECISION_RISK') throw error; fail('INVALID_BOUNDS', p.name + '：范围必须为有限数值或 null', { paramId: p.id, field: key }); }
        }
        if (p.min !== null && p.max !== null && compareDecimal(p.min, p.max) > 0) fail('INVALID_BOUNDS', p.name + '：最小值不能大于最大值', { paramId: p.id });
        if ((p.min !== null && compareDecimal(p.value, p.min) < 0) || (p.max !== null && compareDecimal(p.value, p.max) > 0)) fail('OUT_OF_RANGE', p.name + '：数值超出允许范围', { paramId: p.id, field: 'value' });
      } else {
        if (typeof p.value !== p.type) fail('INVALID_VALUE', p.name + '：参数值与类型不一致', { paramId: p.id });
        if (p.min !== null || p.max !== null) fail('INVALID_BOUNDS', p.name + '：非数值参数不使用数值范围');
      }
    });
    return copy(params);
  }
  function diff(before, after) {
    const a = new Map((before || []).map(p => [p.id, p])), b = new Map((after || []).map(p => [p.id, p]));
    return [...new Set([...a.keys(), ...b.keys()])].flatMap(paramId => {
      const left = a.get(paramId), right = b.get(paramId);
      const changed = fields.filter(field => !equal(left?.[field], right?.[field])).map(field => ({ field, before: copy(left?.[field]), after: copy(right?.[field]) }));
      return changed.length ? [{ paramId, name: (right || left).name, kind: !left ? 'added' : !right ? 'removed' : 'changed', before: left ? copy(left) : null, after: right ? copy(right) : null, fields: changed }] : [];
    });
  }
  const p = (id, name, path, value, unit, min, max, type) => ({ id, name, path, type: type || 'number', unit: unit || '', min: min ?? null, max: max ?? null, value, mode: 'inherit' });
  function changed(params, updates) { return params.map(param => Object.assign(copy(param), updates[param.id] || {})); }
  function revision(input, historical) {
    const metadata = historical ? { reason: null, message: input.message, links: normalizeLinks(input.links) } : changeMetadata(input);
    return frozen(Object.assign({ sourceRevisionId: null, restoreRevisionId: null, reviewId: null }, input, metadata, { params: validated(input.params) }));
  }
  function machineFile(lab) {
    const parameters = [
      p('p-speed', '传送速度', '/Motion/FeedSpeed', 100, 'mm/s', 0, 250),
      p('p-accel', '加速度', '/Motion/Acceleration', 500, 'mm/s²', 10, 2000),
      p('p-decel', '减速度', '/Motion/Deceleration', 600, 'mm/s²', 10, 2000),
      p('p-position', '定位精度', '/Motion/PositionTolerance', 0.05, 'mm', 0.001, 1),
      p('p-offset', '原点偏移', '/Motion/HomeOffset', 0, 'mm', -10, 10),
      p('p-temp', '温控设定', '/Thermal/Setpoint', 40, '°C', 10, 80),
      p('p-temp-max', '温度报警上限', '/Thermal/AlarmLimit', 60, '°C', 40, 100),
      p('p-temp-delay', '稳定等待', '/Thermal/StabilizeDelay', 30, 's', 0, 600),
      p('p-pressure', '供气压力', '/Pneumatic/SupplyPressure', 100000, 'Pa', 0, 500000),
      p('p-vacuum', '真空阈值', '/Pneumatic/VacuumThreshold', -60, 'kPa', -100, 0),
      p('p-pid-p', '比例系数', '/Control/PID/P', 2.4, '', 0, 100),
      p('p-pid-i', '积分系数', '/Control/PID/I', 0.8, '', 0, 100),
      p('p-pid-d', '微分系数', '/Control/PID/D', 0.1, '', 0, 100),
      p('p-cooling', '启用冷却', '/Thermal/CoolingEnabled', true, '', null, null, 'boolean'),
      p('p-door', '门禁联锁', '/Safety/DoorInterlock', true, '', null, null, 'boolean'),
      p('p-timeout', '通信超时', '/Communication/Timeout', 5000, 'ms', 100, 30000),
      p('p-retries', '重试次数', '/Communication/Retries', 3, '次', 0, 10),
      p('p-station', '工位名称', '/General/StationName', lab ? '验证工位' : '装配工位 A', '', null, null, 'string')
    ];
    const b10 = changed(parameters, lab ? { 'p-speed': { value: 60 }, 'p-temp': { value: 32 } } : {});
    const b11 = changed(b10, { 'p-speed': { name: '送料速度', value: lab ? 70 : 105 }, 'p-temp': { value: lab ? 33 : 41 }, 'p-pressure': { value: 100, unit: 'kPa', max: 500 } });
    const b12 = changed(b11, { 'p-speed': { value: lab ? 80 : 120 }, 'p-temp': { value: lab ? 35 : 42 }, 'p-timeout': { value: 6000 } });
    const m12 = changed(b11, { 'p-speed': { value: lab ? 72 : 115, mode: 'override' }, 'p-offset': { value: 0.15, mode: 'pin' } });
    const m13 = changed(m12, { 'p-speed': { value: lab ? 78 : 118 } });
    const make = (id, label, branchId, releaseId, parents, baseId, params, authorId, at, message, sourceId) => revision({ id, label, branchId, releaseId, parentIds: parents, baseRevisionId: baseId, sourceRevisionId: sourceId || null, authorId, at, message, links: id === 'r-b12' && !lab ? ['https://docs.example.invalid/demo/feed-speed'] : [], reviewId: id === 'r-b12' && !lab ? 'CR-025' : null, params }, true);
    const file = { id: 'f-machine', name: 'MachineConfig.xml', format: 'xml', description: lab ? '验证机台运动、温控与安全参数' : '设备运动、温控与安全参数', initialRevisionId: null,
      releaseFamilies: [{ id: 'family-69x', label: '69X', order: 1 }],
      releases: [
        { id: '690', version: '690', familyId: 'family-69x', order: 1 },
        { id: '691', version: '691', familyId: 'family-69x', order: 2 }
      ],
      branches: [
        { id: 'base-691', label: '版本基线 · 691', kind: 'baseline', releaseId: '691', headId: 'r-b12', machineId: null, baseRevisionId: null, initialRevisionId: null },
        { id: 'base-690', label: '版本基线 · 690', kind: 'baseline', releaseId: '690', headId: 'r-b10', machineId: null, baseRevisionId: null, initialRevisionId: null },
        { id: 'machine-01', label: 'MC-001', kind: 'machine', releaseId: '691', headId: 'r-m13', machineId: 'asset-01', baseRevisionId: 'r-b11' },
        { id: 'machine-02', label: 'MC-002', kind: 'machine', releaseId: '691', headId: 'r-m22', machineId: 'asset-02', baseRevisionId: 'r-b12' }
      ],
      revisions: [
        make('r-b10', 'B10', 'base-690', '690', [], null, b10, 'lin', '2026-10-20T01:00:00Z', '保存 690 参数基线'),
        make('r-b11', 'B11', 'base-691', '691', ['r-b10'], null, b11, 'wang', '2026-10-28T02:00:00Z', '691 参数适配：速度更名与压力单位换算'),
        make('r-b12', 'B12', 'base-691', '691', ['r-b11'], null, b12, 'wang', '2026-11-08T04:00:00Z', '提高送料速度并优化通信等待'),
        make('r-m11', 'r1', 'machine-01', '690', ['r-b10'], 'r-b10', b10, 'lin', '2026-10-23T03:10:00Z', '从 B10 建立 MC-001 配置', 'r-b10'),
        make('r-m12', 'r2', 'machine-01', '691', ['r-m11', 'r-b11'], 'r-b11', m12, 'lin', '2026-10-30T02:45:00Z', '采用 691 基线并保存现场校准', 'r-b11'),
        make('r-m13', 'r3', 'machine-01', '691', ['r-m12'], 'r-b11', m13, 'lin', '2026-11-04T05:30:00Z', '微调 MC-001 送料速度'),
        make('r-m21', 'r1', 'machine-02', '691', ['r-b11'], 'r-b11', b11, 'wang', '2026-11-01T03:10:00Z', '从 B11 建立 MC-002 配置', 'r-b11'),
        make('r-m22', 'r2', 'machine-02', '691', ['r-m21', 'r-b12'], 'r-b12', changed(b12, { 'p-offset': { value: -0.08, mode: 'pin' } }), 'wang', '2026-11-09T02:00:00Z', '采用 B12 并保留原点校准', 'r-b12')
      ] };
    if (!lab) {
      // Explicit demonstration metadata groups releases for navigation only.
      // It does not infer parentage or replace immutable revision identities.
      file.releaseFamilies.push({ id: 'family-6ax', label: '6AX', order: 2 });
      file.releases.push(
        { id: '6A0', version: '6A0', familyId: 'family-6ax', order: 3 },
        { id: '6A1', version: '6A1', familyId: 'family-6ax', order: 4 }
      );
      const links = frozen(['https://docs.example.invalid/demo/software-6a']);
      const makeDemo = (id, label, branchId, releaseId, parentIds, baseRevisionId, params, authorId, at, reason, sourceRevisionId) => revision({ id, label, branchId, releaseId, parentIds, baseRevisionId, params, authorId, at, reason, links, sourceRevisionId: sourceRevisionId || null });
      const b13 = makeDemo('r-b13', 'B13', 'base-6A0', '6A0', ['r-b12'], null, changed(b12, { 'p-timeout': { value: 6500 } }), 'wang', '2026-11-09T06:00:00Z', '演示：核对 6A0 通信等待配置', 'r-b12');
      const b14 = makeDemo('r-b14', 'B14', 'base-6A1', '6A1', ['r-b13'], null, changed(b13.params, { 'p-accel': { value: 550, max: 2200 } }), 'wang', '2026-11-10T01:00:00Z', '演示：适配 6A1 的加速度范围', 'r-b13');
      const b15 = makeDemo('r-b15', 'B15', 'base-6A1', '6A1', ['r-b14'], null, changed(b14.params, { 'p-temp': { value: 43 } }), 'lin', '2026-11-10T03:00:00Z', '演示：按验证结果微调温控设定', null);
      const m31 = makeDemo('r-roadmap-m31', 'r1', 'machine-roadmap-03', '6A0', ['r-b13'], 'r-b13', changed(b13.params, { 'p-offset': { value: 0.12, mode: 'pin' } }), 'lin', '2026-11-09T08:00:00Z', '演示：以 6A0 标准建立 MC-003 并完成原点校准', 'r-b13');
      const m32 = makeDemo('r-roadmap-m32', 'r2', 'machine-roadmap-03', '6A1', ['r-roadmap-m31', 'r-b15'], 'r-b15', changed(b15.params, { 'p-offset': { value: 0.12, mode: 'pin' } }), 'lin', '2026-11-10T04:00:00Z', '演示：MC-003 采用 6A1 并保留现场校准', 'r-b15');
      file.branches.push(
        { id: 'base-6A0', label: '版本基线 · 6A0', kind: 'baseline', releaseId: '6A0', headId: b13.id, machineId: null, baseRevisionId: null, initialRevisionId: null, creationReason: b13.reason, links },
        { id: 'base-6A1', label: '版本基线 · 6A1', kind: 'baseline', releaseId: '6A1', headId: b15.id, machineId: null, baseRevisionId: null, initialRevisionId: null, creationReason: b14.reason, links },
        { id: 'machine-roadmap-03', label: 'MC-003', kind: 'machine', releaseId: '6A1', headId: m32.id, machineId: 'asset-roadmap-03', baseRevisionId: b15.id, initialRevisionId: null, creationReason: m31.reason, links }
      );
      file.revisions.push(b13, b14, b15, m31, m32);
    }
    return file;
  }
  function smallFile(key, name, description, params, overrides) {
    const baseline = revision({ id: 'r-' + key + '-b1', label: 'B1', branchId: 'base-691', releaseId: '691', parentIds: [], baseRevisionId: null, authorId: 'wang', at: '2026-11-01T01:00:00Z', message: '建立 691 版本基线', params }, true);
    const machine = revision({ id: 'r-' + key + '-m1', label: 'r1', branchId: 'machine-01', releaseId: '691', parentIds: [baseline.id], baseRevisionId: baseline.id, sourceRevisionId: baseline.id, authorId: 'lin', at: '2026-11-03T02:30:00Z', message: 'MC-001 现场校准', params: changed(params, overrides) }, true);
    return { id: 'f-' + key, name, format: 'json', description, initialRevisionId: null,
      branches: [{ id: 'base-691', label: '版本基线 · 691', kind: 'baseline', releaseId: '691', headId: baseline.id, machineId: null, baseRevisionId: null, initialRevisionId: null }, { id: 'machine-01', label: 'MC-001', kind: 'machine', releaseId: '691', headId: machine.id, machineId: 'asset-01', baseRevisionId: baseline.id }], revisions: [baseline, machine] };
  }
  function create() {
    let sequence = 1000, reviewSequence = 25, clock = 0;
    const uid = prefix => prefix + (++sequence);
    const stamp = () => new Date(Date.now() + clock++).toISOString();
    const machine = machineFile(false);
    const process = smallFile('process', 'ProcessRecipe.json', '生产节拍与工艺设置', [
      p('p-cycle', '生产节拍', '/recipe/cycleTime', 9, 's', 1, 120), p('p-dwell', '保压时间', '/recipe/dwellTime', 1.2, 's', 0, 60),
      p('p-batch', '每批数量', '/recipe/batchSize', 100, '件', 1, 10000), p('p-recipe-speed', '进给倍率', '/recipe/feedRatio', 1, '', 0.1, 2),
      p('p-inspection', '自动检测', '/recipe/inspectionEnabled', true, '', null, null, 'boolean'), p('p-recipe-name', '配方名称', '/recipe/name', '标准配方 A', '', null, null, 'string')
    ], { 'p-cycle': { value: 8.5, mode: 'override' } });
    const coeff = smallFile('coeff', 'Calibration.json', '传感器与运动校准系数', [
      p('p-gain', '传感器增益', '/calibration/gain', 1.01, '', 0.5, 2), p('p-zero', '零点偏置', '/calibration/zero', 0, 'mm', -10, 10),
      p('p-scale-x', 'X 轴比例', '/calibration/scaleX', 1, '', 0.9, 1.1), p('p-scale-y', 'Y 轴比例', '/calibration/scaleY', 1, '', 0.9, 1.1),
      p('p-compensation', '温度补偿', '/calibration/temperatureCompensation', true, '', null, null, 'boolean'), p('p-calibration-name', '校准标识', '/calibration/certificate', 'CAL-691-A', '', null, null, 'string')
    ], { 'p-gain': { value: 1.015, mode: 'override' } });
    const state = { workspaceId: 'ws-prod', actorId: 'lin', actors: [{ id: 'lin', name: '林工', role: 'admin' }, { id: 'wang', name: '王工', role: 'admin' }, { id: 'guest', name: '访客', role: 'guest' }],
      workspaces: [{ id: 'ws-prod', name: '生产配置', files: [machine, process, coeff] }, { id: 'ws-lab', name: '验证环境', files: [machineFile(true)] }],
      assets: [{ id: 'asset-01', name: 'MC-001', model: '69X' }, { id: 'asset-02', name: 'MC-002', model: '69X' }, { id: 'asset-roadmap-03', name: 'MC-003', model: 'MX-820' }], reviews: [], events: [] };
    function workspace() { const w = state.workspaces.find(w => w.id === state.workspaceId); if (!w) fail('WORKSPACE_NOT_FOUND', '当前工作区不存在'); return w; }
    function actor() { const a = state.actors.find(a => a.id === state.actorId); if (!a) fail('FORBIDDEN', '当前操作人无效'); return a; }
    function admin() { if (actor().role !== 'admin') fail('FORBIDDEN', '需要管理员执行此操作'); }
    function getFile(fileId) { const file = workspace().files.find(file => file.id === fileId); if (!file) fail('FILE_NOT_FOUND', '当前工作区没有此配置文件'); return file; }
    function getBranch(file, id) { const b = file.branches.find(b => b.id === id); if (!b) fail('BRANCH_NOT_FOUND', '此配置分支不存在'); return b; }
    function getRevision(file, id) { const r = file.revisions.find(r => r.id === id); if (!r) fail('REVISION_NOT_FOUND', '此保存记录不存在'); return r; }
    function getReview(id) { const r = state.reviews.find(r => r.id === id && r.workspaceId === state.workspaceId); if (!r) fail('REVIEW_NOT_FOUND', '当前工作区没有此评审'); return r; }
    function event(action, data) { const ev = frozen(Object.assign({ id: uid('event-'), workspaceId: state.workspaceId, actorId: actor().id, at: stamp(), action, fileId: null, branchId: null, revisionId: null, reviewId: null, details: {} }, copy(data))); state.events.unshift(ev); return ev; }
    function nextLabel(file, branch) { const pattern = branch.kind === 'baseline' ? /^B(\d+)$/ : /^r(\d+)$/; const numbers = file.revisions.filter(r => branch.kind === 'baseline' || r.branchId === branch.id).map(r => pattern.exec(r.label)).filter(Boolean).map(match => Number(match[1])); return (branch.kind === 'baseline' ? 'B' : 'r') + (Math.max(0, ...numbers) + 1); }
    function cas(branch, expected) { if (branch.headId !== expected) fail('STALE_HEAD', '配置已被他人保存。你的输入已保留，请重新核对。', { expectedHeadId: expected, actualHeadId: branch.headId }); }
    function sourceContext(file, branch, input) {
      const restore = input.restoreRevisionId ? getRevision(file, input.restoreRevisionId) : null;
      if (restore && input.sourceRevisionId && input.sourceRevisionId !== restore.id) fail('INVALID_SOURCE', '恢复记录与声明的来源不一致');
      const source = input.sourceRevisionId ? getRevision(file, input.sourceRevisionId) : restore;
      let baseRevisionId = branch.baseRevisionId, releaseId = branch.releaseId;
      if (branch.kind === 'machine' && restore && getBranch(file, restore.branchId).kind === 'machine') { baseRevisionId = restore.baseRevisionId; releaseId = restore.releaseId; }
      else if (branch.kind === 'machine' && source && getBranch(file, source.branchId).kind === 'baseline') { baseRevisionId = source.id; releaseId = source.releaseId; }
      return { source, restore, baseRevisionId, releaseId, changed: baseRevisionId !== branch.baseRevisionId || releaseId !== branch.releaseId };
    }
    function inheritance(file, branch, params, context) {
      if (branch.kind !== 'machine') return;
      const base = file.revisions.find(r => r.id === context.baseRevisionId);
      if (!base) fail('BASIS_UNKNOWN', '采用的基线不可核验，请明确选择来源');
      const source = new Map(base.params.map(p => [p.id, p]));
      params.filter(p => p.mode === 'inherit').forEach(param => {
        const original = source.get(param.id);
        const sameValue = original && (param.type === 'number' && original.type === 'number' ? compareDecimal(param.value, original.value) === 0 : equal(param.value, original.value));
        if (!original || !sameValue || original.type !== param.type || original.unit !== param.unit) fail('INHERIT_MISMATCH', param.name + '：采用基线时应使用该基线的值；自定义值请选择单独设置或保持此值', { paramId: param.id, field: 'value', baseRevisionId: base.id });
      });
    }
    function hasChanges(head, params, context) { return diff(head?.params || [], params).length || context.changed || (context.source && context.source.id !== head?.id && context.source.id !== head?.sourceRevisionId && !head?.parentIds.includes(context.source.id)); }
    function save(input) {
      admin(); const file = getFile(input.fileId), branch = getBranch(file, input.branchId);
      if (!own(input, 'expectedHeadId') || input.expectedHeadId === undefined) fail('HEAD_REQUIRED', '保存前必须明确当前记录');
      cas(branch, input.expectedHeadId);
      const metadata = changeMetadata(input);
      const head = branch.headId ? getRevision(file, branch.headId) : null, params = validated(input.params), context = sourceContext(file, branch, input);
      inheritance(file, branch, params, context);
      if (!hasChanges(head, params, context)) fail('NO_CHANGES', '没有需要保存的修改');
      const parents = head ? [head.id] : []; if (context.source && !parents.includes(context.source.id)) parents.push(context.source.id);
      const next = revision({ ...metadata, id: uid('rev-'), label: nextLabel(file, branch), branchId: branch.id, releaseId: context.releaseId, parentIds: parents, baseRevisionId: context.baseRevisionId, sourceRevisionId: context.source?.id || null, restoreRevisionId: context.restore?.id || null, authorId: actor().id, at: stamp(), params });
      file.revisions.push(next); branch.headId = next.id; branch.baseRevisionId = context.baseRevisionId; branch.releaseId = context.releaseId;
      event('configuration_saved', { fileId: file.id, branchId: branch.id, revisionId: next.id, details: { ...metadata, before: head?.id || null, after: next.id, changed: diff(head?.params || [], params).length, sourceRevisionId: next.sourceRevisionId, restoreRevisionId: next.restoreRevisionId } });
      return next;
    }
    function protectReview(review) {
      Object.assign(review, changeMetadata(review, review.title));
      review.proposedParams = frozen(copy(review.proposedParams));
      review.links = frozen(review.links);
      for (const key of ['id', 'workspaceId', 'fileId', 'branchId', 'title', 'description', 'systemDescription', 'reason', 'message', 'links', 'authorId', 'baseRevisionId', 'sourceRevisionId', 'restoreRevisionId', 'proposedParams', 'at']) Object.defineProperty(review, key, { value: review[key], enumerable: true, writable: false, configurable: false });
      return review;
    }
    function createReview(input) {
      actor(); const file = getFile(input.fileId), branch = getBranch(file, input.branchId);
      if (!branch.headId) fail('NOT_INITIALIZED', '请先保存初始配置，再提交修改评审');
      const metadata = changeMetadata(input, input.title);
      if (own(input, 'title') && (typeof input.title !== 'string' || !input.title.trim())) fail('INVALID_TITLE', '请填写评审标题');
      const title = own(input, 'title') ? input.title.trim() : metadata.reason;
      const head = getRevision(file, branch.headId), params = validated(input.params), context = sourceContext(file, branch, input);
      inheritance(file, branch, params, context);
      if (!hasChanges(head, params, context)) fail('NO_CHANGES', '没有需要评审的修改');
      const r = protectReview({ ...metadata, id: 'CR-' + String(++reviewSequence).padStart(3, '0'), workspaceId: state.workspaceId, fileId: file.id, branchId: branch.id, title, description: String(input.description || ''), systemDescription: context.restore ? '从 ' + context.restore.label + ' 创建历史恢复提案' : '', authorId: actor().id, baseRevisionId: head.id, sourceRevisionId: context.source?.id || null, restoreRevisionId: context.restore?.id || null, proposedParams: params, status: 'open', comments: [], approvals: [], at: stamp(), mergedRevisionId: null });
      state.reviews.push(r); event('review_created', { fileId: file.id, branchId: branch.id, reviewId: r.id, details: { ...metadata, title: r.title, baseRevisionId: head.id } }); return r;
    }
    function addComment(reviewId, input) {
      const a = actor(), r = getReview(reviewId); if (r.status === 'merged') fail('REVIEW_CLOSED', '已合并评审不能再添加评论');
      if (typeof input.body !== 'string' || !input.body.trim()) fail('EMPTY_COMMENT', '请输入评论内容');
      if (input.paramId && !r.proposedParams.some(p => p.id === input.paramId) && !getRevision(getFile(r.fileId), r.baseRevisionId).params.some(p => p.id === input.paramId)) fail('PARAM_NOT_FOUND', '评论关联的参数不存在');
      if (input.field && !fields.includes(input.field)) fail('INVALID_FIELD', '评论字段无效');
      const comment = { id: uid('comment-'), authorId: a.id, paramId: input.paramId || null, field: input.field || null, body: input.body.trim(), at: stamp(), resolved: false, resolvedBy: null, resolvedAt: null, baseRevisionId: r.baseRevisionId };
      r.comments.push(comment); if (r.status === 'approved') { r.status = 'open'; r.approvals = []; }
      event('comment_added', { fileId: r.fileId, branchId: r.branchId, reviewId: r.id, details: { reason: r.reason, message: r.message, links: r.links, commentId: comment.id, paramId: comment.paramId } }); return comment;
    }
    function resolveComment(reviewId, commentId) {
      const r = getReview(reviewId), a = actor(), c = r.comments.find(c => c.id === commentId); if (!c) fail('COMMENT_NOT_FOUND', '评论不存在');
      if (a.role !== 'admin' && c.authorId !== a.id) fail('FORBIDDEN', '只能处理自己的评论，或由管理员处理');
      if (c.resolved) return c;
      if (r.status === 'merged') fail('REVIEW_CLOSED', '评审已经合并');
      c.resolved = true; c.resolvedBy = a.id; c.resolvedAt = stamp();
      event('comment_resolved', { fileId: r.fileId, branchId: r.branchId, reviewId: r.id, details: { reason: r.reason, message: r.message, links: r.links, commentId: c.id } }); return c;
    }
    function reviewReady(r) { if (r.comments.some(c => !c.resolved)) fail('UNRESOLVED', '仍有未解决的讨论'); cas(getBranch(getFile(r.fileId), r.branchId), r.baseRevisionId); }
    function approve(reviewId) {
      admin(); const r = getReview(reviewId); if (r.status === 'merged') fail('REVIEW_CLOSED', '此评审已经合并');
      if (r.authorId === actor().id) fail('SELF_APPROVAL', '需要另一位管理员确认此评审');
      reviewReady(r);
      if (!r.approvals.some(a => a.actorId === actor().id)) {
        r.approvals.push(frozen({ actorId: actor().id, at: stamp(), baseRevisionId: r.baseRevisionId }));
        event('review_approved', { fileId: r.fileId, branchId: r.branchId, reviewId: r.id, details: { title: r.title, reason: r.reason, message: r.message, links: r.links } });
      }
      r.status = 'approved'; return r;
    }
    function merge(reviewId) {
      admin(); const r = getReview(reviewId), file = getFile(r.fileId), branch = getBranch(file, r.branchId);
      if (r.status === 'merged') return getRevision(file, r.mergedRevisionId);
      reviewReady(r);
      if (r.status !== 'approved' || !r.approvals.some(a => a.actorId !== r.authorId && a.baseRevisionId === r.baseRevisionId && state.actors.some(actor => actor.id === a.actorId && actor.role === 'admin'))) fail('APPROVAL_REQUIRED', '请先由另一位管理员通过评审');
      const params = validated(r.proposedParams), context = sourceContext(file, branch, r); inheritance(file, branch, params, context);
      const parents = [r.baseRevisionId]; if (context.source && !parents.includes(context.source.id)) parents.push(context.source.id);
      const next = revision({ reason: r.reason, message: r.message, links: r.links, id: uid('rev-'), label: nextLabel(file, branch), branchId: branch.id, releaseId: context.releaseId, parentIds: parents, baseRevisionId: context.baseRevisionId, sourceRevisionId: context.source?.id || null, restoreRevisionId: context.restore?.id || null, authorId: r.authorId, mergedBy: actor().id, at: stamp(), reviewId: r.id, params });
      file.revisions.push(next); branch.headId = next.id; branch.baseRevisionId = context.baseRevisionId; branch.releaseId = context.releaseId; r.status = 'merged'; r.mergedRevisionId = next.id;
      event('review_merged', { fileId: file.id, branchId: branch.id, revisionId: next.id, reviewId: r.id, details: { title: r.title, reason: r.reason, message: r.message, links: r.links, before: r.baseRevisionId, after: next.id, sourceRevisionId: next.sourceRevisionId, restoreRevisionId: next.restoreRevisionId } }); return next;
    }
    function fork(fileId, input) {
      admin(); const file = getFile(fileId), source = getRevision(file, input.fromRevisionId), asset = state.assets.find(a => a.id === input.machineId);
      if (!asset) fail('ASSET_NOT_FOUND', '请先登记机台');
      if (file.branches.some(b => b.machineId === asset.id)) fail('DUPLICATE_MACHINE', '此机台已经关联该配置文件');
      if (getBranch(file, source.branchId).kind !== 'baseline') fail('INVALID_SOURCE', '请选择一个明确的版本基线作为机台起点');
      const metadata = changeMetadata(input);
      const branch = { id: uid('machine-'), label: asset.name, kind: 'machine', releaseId: source.releaseId, headId: null, machineId: asset.id, baseRevisionId: source.id, creationReason: metadata.reason, links: frozen(copy(metadata.links)) };
      const next = revision({ ...metadata, id: uid('rev-'), label: 'r1', branchId: branch.id, releaseId: source.releaseId, parentIds: [source.id], baseRevisionId: source.id, sourceRevisionId: source.id, authorId: actor().id, at: stamp(), systemDescription: '从 ' + source.label + ' 初始化 ' + asset.name, params: source.params });
      branch.headId = next.id; file.branches.push(branch); file.revisions.push(next);
      event('branch_created', { fileId, branchId: branch.id, revisionId: next.id, details: { ...metadata, machineId: asset.id, sourceRevisionId: source.id } }); return branch;
    }
    function releaseName(value) { if (typeof value !== 'string' || !value.trim() || value.length > 64) fail('INVALID_RELEASE', '请输入软件版本'); return value.trim(); }
    function initialize(input) {
      admin(); const w = workspace(), releaseId = releaseName(input.releaseId), params = validated(input.params, true);
      if (typeof input.name !== 'string' || !input.name.trim()) fail('INVALID_NAME', '请输入配置文件名');
      if (!['xml', 'json'].includes(input.format)) fail('INVALID_FORMAT', '原型支持 XML 或 JSON');
      if (w.files.some(file => file.name.toLowerCase() === input.name.trim().toLowerCase())) fail('FILE_EXISTS', '此文件已存在，请打开对应配置或使用新的名称');
      const metadata = changeMetadata(input);
      const file = { id: uid('f-'), name: input.name.trim(), format: input.format, description: String(input.description || ''), initialRevisionId: null, branches: [], revisions: [] };
      const branch = { id: 'base-' + releaseId, label: '版本基线 · ' + releaseId, kind: 'baseline', releaseId, headId: null, machineId: null, baseRevisionId: null, initialRevisionId: null, creationReason: metadata.reason, links: frozen(copy(metadata.links)) };
      const initial = revision({ ...metadata, id: uid('rev-initial-'), label: '初始快照', kind: 'initial', branchId: branch.id, releaseId, parentIds: [], baseRevisionId: null, authorId: actor().id, at: stamp(), systemDescription: '从确认的输入建立初始快照', params });
      const baseline = revision({ ...metadata, id: uid('rev-'), label: 'B1', branchId: branch.id, releaseId, parentIds: [initial.id], baseRevisionId: initial.id, sourceRevisionId: initial.id, authorId: actor().id, at: stamp(), systemDescription: '建立 ' + releaseId + ' 首个版本基线', params });
      file.initialRevisionId = initial.id; branch.initialRevisionId = initial.id; branch.baseRevisionId = initial.id; branch.headId = baseline.id;
      file.branches.push(branch); file.revisions.push(initial, baseline); w.files.push(file);
      event('file_initialized', { fileId: file.id, branchId: branch.id, revisionId: baseline.id, details: { ...metadata, initialRevisionId: initial.id, releaseId } }); return file;
    }
    function addRelease(fileId, input) {
      admin(); const file = getFile(fileId), releaseId = releaseName(input.releaseId);
      if (file.branches.some(b => b.kind === 'baseline' && b.releaseId === releaseId)) fail('RELEASE_EXISTS', '此软件版本已存在');
      const metadata = changeMetadata(input);
      const source = input.sourceRevisionId ? getRevision(file, input.sourceRevisionId) : null;
      const params = own(input, 'params') && input.params !== undefined ? validated(input.params, true) : source ? copy(source.params) : null;
      const branch = { id: 'base-' + releaseId, label: '版本基线 · ' + releaseId, kind: 'baseline', releaseId, headId: null, machineId: null, baseRevisionId: null, initialRevisionId: null, creationReason: metadata.reason, links: frozen(copy(metadata.links)), systemDescription: '建立软件版本 ' + releaseId };
      let snapshots = [];
      if (params) {
        const initial = revision({ ...metadata, id: uid('rev-initial-'), label: '初始快照', kind: 'initial', branchId: branch.id, releaseId, parentIds: source ? [source.id] : [], baseRevisionId: source?.id || null, sourceRevisionId: source?.id || null, authorId: actor().id, at: stamp(), systemDescription: source ? '显式采用 ' + source.label + ' 初始化' : '从确认的输入初始化', params });
        const baseline = revision({ ...metadata, id: uid('rev-'), label: nextLabel(file, branch), branchId: branch.id, releaseId, parentIds: [initial.id], baseRevisionId: initial.id, sourceRevisionId: initial.id, authorId: actor().id, at: stamp(), systemDescription: '建立 ' + releaseId + ' 首个版本基线', params });
        branch.headId = baseline.id; branch.baseRevisionId = initial.id; branch.initialRevisionId = initial.id; snapshots = [initial, baseline];
      }
      file.branches.push(branch); file.revisions.push(...snapshots);
      event('release_created', { fileId, branchId: branch.id, revisionId: branch.headId, details: { ...metadata, releaseId, sourceRevisionId: source?.id || null, initialized: !!params } }); return branch;
    }
    function revertReview(fileId, branchId, revisionId, input) {
      const file = getFile(fileId), target = getRevision(file, revisionId);
      input = typeof input === 'string' ? { message: input } : input || {};
      const metadata = changeMetadata(input, input.title);
      return createReview({ ...metadata, fileId, branchId, title: own(input, 'title') ? input.title : metadata.reason, description: input.description || '', params: target.params, sourceRevisionId: target.id, restoreRevisionId: target.id });
    }
    function isAncestor(file, ancestorId, childId) {
      const visited = new Set(), queue = [childId];
      for (let i = 0; i < queue.length; i++) { const id = queue[i]; if (id === ancestorId) return true; if (visited.has(id)) continue; visited.add(id); const r = file.revisions.find(r => r.id === id); if (r) queue.push(...r.parentIds); }
      return false;
    }
    function planMerge(fileId, input) {
      const file = getFile(fileId), base = getRevision(file, input.baseRevisionId), target = getRevision(file, input.targetRevisionId), source = getRevision(file, input.sourceRevisionId);
      if (!isAncestor(file, base.id, target.id) || !isAncestor(file, base.id, source.id)) fail('BASE_NOT_COMMON', '所选起点不是双方可核验的共同祖先');
      const maps = [base, target, source].map(r => new Map(r.params.map(p => [p.id, p]))), rows = [], result = [];
      const same = (a, b) => !a && !b || !!a && !!b && fields.every(field => equal(a[field], b[field]));
      const ids = [...new Set(maps.flatMap(map => [...map.keys()]))];
      for (const paramId of ids) {
        const [b, t, s] = maps.map(map => map.get(paramId)), conflicts = [];
        let merged = t ? copy(t) : null;
        const fieldSet = new Set([...diff(b ? [b] : [], t ? [t] : []), ...diff(b ? [b] : [], s ? [s] : [])].flatMap(d => d.fields.map(f => f.field)));
        if (same(t, s)) merged = t ? copy(t) : null;
        else if (!b) { if (!t) merged = copy(s); else if (!s) merged = copy(t); else { conflicts.push('membership'); fieldSet.add('membership'); } }
        else if (!t) { if (!same(b, s)) { conflicts.push('membership'); fieldSet.add('membership'); } }
        else if (!s) { if (same(b, t) && t.mode !== 'pin') merged = null; else { conflicts.push('membership'); fieldSet.add('membership'); } }
        else {
          for (const field of fields) {
            if (field === 'mode' && t.mode === 'pin') { merged.mode = 'pin'; continue; }
            if (field === 'value' && t.mode === 'pin') { merged.value = copy(t.value); continue; }
            if (equal(t[field], s[field]) || equal(s[field], b[field])) merged[field] = copy(t[field]);
            else if (equal(t[field], b[field]) && !(field === 'value' && t.mode === 'override')) merged[field] = copy(s[field]);
            else conflicts.push(field);
          }
          if ((t.mode === 'pin' || t.mode === 'override' || !equal(t.value, b.value)) && (s.type !== b.type || s.unit !== b.unit)) {
            for (const field of ['type', 'unit']) if (s[field] !== b[field]) { conflicts.push(field); fieldSet.add(field); merged[field] = copy(t[field]); }
            merged.value = copy(t.value); if (t.mode !== 'pin') conflicts.push('value'); fieldSet.add('value');
          }
        }
        let validationError = null;
        if (merged) {
          try { validated([merged]); }
          catch (error) { validationError = { code: error.code, message: error.message }; for (const field of ['value', 'type', 'min', 'max']) if (fieldSet.has(field)) conflicts.push(field); if (!conflicts.length) conflicts.push('membership'); merged = t ? copy(t) : null; }
        }
        if (merged) result.push(copy(merged));
        if (fieldSet.size || conflicts.length) rows.push({ paramId, name: (t || s || b).name, base: b ? copy(b) : null, target: t ? copy(t) : null, source: s ? copy(s) : null, merged: merged ? copy(merged) : null,
          fields: [...fieldSet].map(field => ({ field, before: copy(b?.[field]), after: copy(merged?.[field]) })), conflicts: [...new Set(conflicts)], status: conflicts.length ? 'conflict' : 'auto', ...(validationError ? { validationError } : {}) });
      }
      return { baseRevisionId: base.id, targetRevisionId: target.id, sourceRevisionId: source.id, rows, params: result, conflicts: rows.filter(r => r.conflicts.length) };
    }
    function history(fileId, paramId, branchId) {
      const file = getFile(fileId);
      if (branchId) getBranch(file, branchId);
      return file.revisions.flatMap((r, index) => {
        if (branchId && r.branchId !== branchId) return [];
        const previous = file.revisions.find(p => p.id === r.parentIds[0]);
        const change = diff(previous?.params || [], r.params).find(d => d.paramId === paramId);
        return change ? [Object.assign(change, { revisionId: r.id, label: r.label, branchId: r.branchId, releaseId: r.releaseId, authorId: r.authorId, at: r.at, reason: r.reason, message: r.message, links: copy(r.links), reviewId: r.reviewId, parentIds: copy(r.parentIds), knownParent: !r.parentIds.length || !!previous, _index: index })] : [];
      }).sort((a, b) => b.at.localeCompare(a.at) || b._index - a._index).map(({ _index, ...row }) => row);
    }
    function blame(fileId, revisionId, paramId) {
      const file = getFile(fileId), selected = getRevision(file, revisionId), param = selected.params.find(p => p.id === paramId);
      if (!param) fail('PARAM_NOT_FOUND', '此保存记录没有该参数');
      function trace(r, field, seen) {
        if (!r || seen.has(r.id)) return { known: false, originRevisionId: null, appliedRevisionId: null, authorId: null, at: null, reviewId: null };
        const nextSeen = new Set(seen); nextSeen.add(r.id); const current = r.params.find(p => p.id === paramId);
        if (!current) return { known: false, originRevisionId: null, appliedRevisionId: null, authorId: null, at: null, reviewId: null };
        const primary = file.revisions.find(p => p.id === r.parentIds[0]), prior = primary?.params.find(p => p.id === paramId);
        if (prior && equal(prior[field], current[field])) {
          const inherited = trace(primary, field, nextSeen);
          // A first copy into another branch is an application event even
          // when its first parent already holds the same field value.
          if (primary.branchId !== r.branchId || (primary.kind === 'initial' && r.sourceRevisionId === primary.id)) return Object.assign(inherited, { appliedRevisionId: r.id });
          return inherited;
        }
        const sources = [...new Set([r.sourceRevisionId, ...r.parentIds.slice(1)].filter(Boolean))].map(id => file.revisions.find(p => p.id === id)).filter(Boolean);
        const source = sources.find(source => { const p = source.params.find(p => p.id === paramId); return p && equal(p[field], current[field]); });
        if (source) { const result = trace(source, field, nextSeen); return Object.assign(result, { appliedRevisionId: r.id }); }
        if (r.parentIds.length && !primary) return { known: false, originRevisionId: null, appliedRevisionId: r.id, authorId: null, at: null, reviewId: null };
        return { known: !!state.actors.find(a => a.id === r.authorId), originRevisionId: r.id, appliedRevisionId: r.id, authorId: r.authorId || null, at: r.at, reviewId: r.reviewId || null };
      }
      return fields.map(field => Object.assign({ field, value: copy(param[field]) }, trace(selected, field, new Set())));
    }
    const current = getRevision(machine, 'r-b12');
    state.reviews.push(protectReview({ id: 'CR-024', workspaceId: 'ws-prod', fileId: 'f-machine', branchId: 'base-691', title: '调整温控设定与报警上限', description: '产线验证后将温控设定调整为 45 °C，并同步提高上限。请核对热稳定与保护阈值。', links: ['https://docs.example.invalid/demo/thermal-validation', 'https://config.example.invalid/demo/temperature-limits'], authorId: 'wang', baseRevisionId: 'r-b12', sourceRevisionId: null, restoreRevisionId: null, proposedParams: changed(current.params, { 'p-temp': { value: 45 }, 'p-temp-max': { value: 65 } }), status: 'open', comments: [{ id: 'comment-024-1', authorId: 'lin', paramId: 'p-temp', field: 'value', body: '请补充 45 °C 下的热稳定验证结果，再合并到基线。', at: '2026-11-10T02:20:00Z', resolved: false, resolvedBy: null, resolvedAt: null, baseRevisionId: 'r-b12' }], approvals: [], at: '2026-11-10T02:00:00Z', mergedRevisionId: null }));
    state.reviews.push(protectReview({ id: 'CR-025', workspaceId: 'ws-prod', fileId: 'f-machine', branchId: 'base-691', title: '提高送料速度并优化通信等待', description: '经过节拍验证后采用此版本。', links: current.links, authorId: 'wang', baseRevisionId: 'r-b11', sourceRevisionId: null, restoreRevisionId: null, proposedParams: current.params, status: 'merged', comments: [], approvals: [frozen({ actorId: 'lin', at: '2026-11-08T03:40:00Z', baseRevisionId: 'r-b11' })], at: '2026-11-07T08:00:00Z', mergedRevisionId: 'r-b12' }));
    for (const w of state.workspaces) for (const file of w.files) for (const r of file.revisions) state.events.push(frozen({ id: uid('seed-event-'), workspaceId: w.id, actorId: r.authorId, at: r.at, action: r.reviewId ? 'review_merged' : 'configuration_saved', fileId: file.id, branchId: r.branchId, revisionId: r.id, reviewId: r.reviewId, details: { reason: r.reason, message: r.message, links: r.links, before: r.parentIds[0] || null, after: r.id } }));
    state.events.sort((a, b) => b.at.localeCompare(a.at));
    return { state, getFile, diff, save, createReview, addComment, resolveComment, approve, merge, fork, initialize, addRelease, revertReview, planMerge, history, blame };
  }
  return { create, diff, fields, reconstruction: '2026-11-11', compareDecimal, normalizeLinks, changeMetadata };
});
