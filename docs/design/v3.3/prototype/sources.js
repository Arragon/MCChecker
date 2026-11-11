/* MCChecker v3.3 interaction prototype only. In-memory .invalid fixtures.
 * No network, DOM, production credentials, scheduler, or device writes.
 * The restricted sample parser does not replace app/core/parser.py templates.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MCPrototypeSources = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const LIMITS = Object.freeze({bytes: 5 * 1024 * 1024, params: 10000, depth: 32, nodes: 20000, numericChars: 4096, previewFiles: 25});
  const NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
  const XML_NAME = /^[A-Za-z_][A-Za-z0-9_.-]*$/;
  const RESERVED = new Set(['__proto__', 'prototype', 'constructor']);
  const KINDS = new Set(['auto', 'file', 'html', 'json', 'xml', 'zip']);
  function fail(code, message, details) {const error = new Error(message); error.code = code; if (details) error.details = details; throw error;}
  function copy(value) {return value === undefined ? undefined : JSON.parse(JSON.stringify(value));}
  function decimalIdentity(raw) {
    const m = String(raw).match(/^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/);
    if (!m || String(raw).length > LIMITS.numericChars) return null;
    let digits = (m[2] + (m[3] || '')).replace(/^0+/, '');
    if (!digits) return m[1] ? '-0' : '0';
    let exponent = BigInt(m[4] || '0') - BigInt((m[3] || '').length);
    const trailing = digits.match(/0+$/)?.[0].length || 0;
    if (trailing) {digits = digits.slice(0, -trailing); exponent += BigInt(trailing);}
    return m[1] + digits + 'e' + exponent.toString();
  }
  function numericValue(raw) {
    if (typeof raw !== 'string' || raw.length > LIMITS.numericChars || !NUMBER.test(raw)) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || Object.is(value, -0) || Number.isInteger(value) && !Number.isSafeInteger(value)) return raw;
    return decimalIdentity(raw) === decimalIdentity(String(value)) ? value : raw;
  }
  function checkText(text) {
    if (typeof text !== 'string') fail('TEXT_REQUIRED', '请选择文本格式的 XML / JSON 文件');
    const bytes = typeof TextEncoder === 'function' ? new TextEncoder().encode(text).length : Array.from(text).reduce((n, c) => n + (c.codePointAt(0) > 0xFFFF ? 4 : c.charCodeAt(0) > 0x7FF ? 3 : c.charCodeAt(0) > 0x7F ? 2 : 1), 0);
    if (bytes > LIMITS.bytes) fail('SAMPLE_TOO_LARGE', '原型只处理 5 MB 内的样本；正式大文件处理使用服务端解析');
  }
  function fieldKey(key) {
    if (!key || key.includes('/') || RESERVED.has(key)) fail('UNSUPPORTED_KEY', '原型不支持空字段名、含 / 的字段名或保留名称');
  }
  function validXmlChars(text) {
    for (const char of text) {
      const c = char.codePointAt(0);
      if (!(c === 9 || c === 10 || c === 13 || c >= 0x20 && c <= 0xD7FF || c >= 0xE000 && c <= 0xFFFD || c >= 0x10000 && c <= 0x10FFFF)) fail('XML_CHARACTER', 'XML 包含不支持的控制字符或无效 Unicode');
    }
    return text;
  }
  function decodeXml(text) {
    if (text.includes(']]>')) fail('XML_TEXT', 'XML 文本中的 ]]> 必须正确转义');
    return validXmlChars(text.replace(/&([^;]*);|&/g, (all, entity) => {
      const known = {lt: '<', gt: '>', amp: '&', quot: '"', apos: "'"};
      if (Object.hasOwn(known, entity)) return known[entity];
      if (/^#(?:[0-9]+|x[0-9a-fA-F]+)$/.test(entity || '')) {
        const code = entity[1] === 'x' ? parseInt(entity.slice(2), 16) : Number(entity.slice(1));
        if (Number.isSafeInteger(code) && code <= 0x10FFFF && code >= 0) return validXmlChars(String.fromCodePoint(code));
      }
      fail('XML_ENTITY', '原型只接受 XML 标准字符实体与有效数字字符引用');
    }));
  }
  function parseText(text, name, reference) {
    checkText(text); name = String(name || 'sample'); reference = reference || [];
    const format = /\.json$/i.test(name) ? 'json' : /\.xml$/i.test(name) ? 'xml' : text.trimStart().startsWith('<') ? 'xml' : 'json';
    const fields = []; let rootName = null;
    function field(path, value, type, rawNumeric) {
      if (fields.length >= LIMITS.params) fail('TOO_MANY_PARAMS', '原型最多提取 10,000 项参数');
      fields.push({path, value, type, rawNumeric});
    }
    if (format === 'json') {
      let pos = 0;
      const skip = () => {while (/[ \t\r\n]/.test(text[pos] || '') && pos < text.length) pos++;};
      function string() {
        const start = pos; if (text[pos++] !== '"') fail('JSON_STRING', 'JSON 字符串格式错误');
        let escaped = false;
        for (; pos < text.length; pos++) {
          const char = text[pos];
          if (char === '"' && !escaped) {pos++; try {return JSON.parse(text.slice(start, pos));} catch (_) {fail('JSON_STRING', 'JSON 字符串转义或控制字符无效');}}
          if (char === '\\' && !escaped) escaped = true; else escaped = false;
        }
        fail('JSON_STRING', 'JSON 字符串未闭合');
      }
      function value(path, depth) {
        if (depth > LIMITS.depth) fail('TOO_DEEP', '原型最多处理 32 层结构');
        skip(); const char = text[pos];
        if (char === '{') {
          pos++; skip(); if (text[pos] === '}') fail('EMPTY_OBJECT', '原型不导入空对象，请使用包含参数的样本');
          const seen = new Set();
          while (pos < text.length) {
            skip(); const key = string(); fieldKey(key);
            if (seen.has(key)) fail('DUPLICATE_KEY', 'JSON 字段重复：' + key); seen.add(key); skip();
            if (text[pos++] !== ':') fail('JSON_SYNTAX', 'JSON 字段缺少冒号');
            value(path + '/' + key, depth + 1); skip(); const delimiter = text[pos++];
            if (delimiter === '}') return;
            if (delimiter !== ',') fail('JSON_SYNTAX', 'JSON 对象格式错误');
          }
          fail('JSON_SYNTAX', 'JSON 对象未闭合');
        }
        if (char === '[' || text.slice(pos, pos + 4) === 'null') fail('UNSUPPORTED_SHAPE', '原型仅支持非空对象中的数值、文本和布尔参数；数组与 null 需由正式解析器处理');
        if (char === '"') {field(path, string(), 'string'); return;}
        if (text.slice(pos, pos + 4) === 'true') {pos += 4; field(path, true, 'boolean'); return;}
        if (text.slice(pos, pos + 5) === 'false') {pos += 5; field(path, false, 'boolean'); return;}
        const match = text.slice(pos).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
        if (!match) fail('JSON_VALUE', 'JSON 参数值格式错误');
        const numeric = numericValue(match[0]); if (numeric === null) fail('NUMBER_LIMIT', '数值文本超过原型限制或格式无效');
        pos += match[0].length; field(path, numeric, 'number', match[0]);
      }
      skip(); if (text[pos] !== '{') fail('ROOT_OBJECT', '原型要求 JSON 顶层为非空对象');
      value('', 0); skip(); if (pos !== text.length) fail('JSON_SYNTAX', 'JSON 末尾含多余内容');
    } else {
      if (/<!DOCTYPE|<!ENTITY/i.test(text)) fail('XML_DTD', '不接受包含 DTD 或实体定义的 XML');
      validXmlChars(text);
      let xml = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n'), pos = 0, nodes = 0;
      if (xml.startsWith('<?xml')) {
        const end = xml.indexOf('?>'); if (end < 0) fail('XML_DECLARATION', 'XML 声明未闭合');
        const declaration = xml.slice(0, end + 2);
        if (!/^<\?xml\s+version\s*=\s*(['"])1\.0\1(?:\s+encoding\s*=\s*(['"])UTF-8\2)?(?:\s+standalone\s*=\s*(['"])(?:yes|no)\3)?\s*\?>$/i.test(declaration)) fail('XML_DECLARATION', '原型仅支持 XML 1.0 / UTF-8 声明');
        pos = end + 2;
      }
      const skip = () => {while (/[ \t\n]/.test(xml[pos] || '') && pos < xml.length) pos++;};
      function readName() {
        const match = xml.slice(pos).match(/^[A-Za-z_][A-Za-z0-9_.-]*/);
        if (!match) fail('XML_NAME', '原型仅支持简单 XML 元素名');
        pos += match[0].length; fieldKey(match[0]);
        if (xml[pos] === ':') fail('XML_NAMESPACE', 'XML 命名空间需要正式模板映射');
        return match[0];
      }
      function element(depth) {
        if (depth > LIMITS.depth) fail('TOO_DEEP', '原型最多处理 32 层结构');
        if (++nodes > LIMITS.nodes) fail('TOO_MANY_NODES', 'XML 节点数超过原型限制');
        if (xml[pos++] !== '<' || /[!?/]/.test(xml[pos] || '')) fail('XML_STRUCTURE', '原型不处理 XML 注释、CDATA、处理指令或无效元素');
        const tag = readName(); skip();
        if (xml.slice(pos, pos + 2) === '/>') {pos += 2; return {tag, children: [], text: ''};}
        if (xml[pos++] !== '>') fail('XML_ATTRIBUTES', 'XML 属性需要正式模板映射，原型不忽略属性');
        const children = [], names = new Set(); let content = '';
        while (pos < xml.length) {
          if (xml.slice(pos, pos + 2) === '</') {
            pos += 2; const closing = readName(); skip();
            if (closing !== tag || xml[pos++] !== '>') fail('XML_STRUCTURE', 'XML 结束元素不匹配');
            if (children.length && /[^ \t\n\r]/.test(content)) fail('XML_MIXED', '混合文本与子元素需要正式模板映射');
            return {tag, children, text: content};
          }
          if (xml[pos] === '<') {
            const child = element(depth + 1);
            if (names.has(child.tag)) fail('XML_REPEATED', '同名兄弟元素需要明确的模板映射：' + child.tag);
            names.add(child.tag); children.push(child);
          } else {
            const end = xml.indexOf('<', pos); if (end < 0) fail('XML_STRUCTURE', 'XML 元素未闭合');
            content += decodeXml(xml.slice(pos, end)); pos = end;
          }
        }
        fail('XML_STRUCTURE', 'XML 元素未闭合');
      }
      skip(); const root = element(0); skip(); if (pos !== xml.length) fail('XML_STRUCTURE', 'XML 必须只有一个完整根元素');
      if (!root.children.length) fail('XML_ROOT', '原型要求根元素中包含参数子元素');
      rootName = root.tag;
      function collect(node, path) {
        if (node.children.length) node.children.forEach(child => collect(child, path + '/' + child.tag));
        else {
          const numeric = numericValue(node.text);
          if (NUMBER.test(node.text) && numeric === null) fail('NUMBER_LIMIT', '数值文本超过原型限制');
          if (node.text === 'true' || node.text === 'false') field(path, node.text === 'true', 'boolean');
          else if (numeric !== null) field(path, numeric, 'number', node.text);
          else field(path, node.text, 'string');
        }
      }
      root.children.forEach(child => collect(child, '/' + child.tag));
    }
    const referenceByPath = new Map(), used = new Set(); let matched = 0;
    reference.forEach(p => {if (referenceByPath.has(p.path)) fail('REFERENCE_COLLISION', '当前参数有重复路径，不能自动对应'); referenceByPath.set(p.path, p);});
    const params = fields.map((entry, i) => {
      if (used.has(entry.path)) fail('PATH_COLLISION', '文件中有重复字段路径：' + entry.path); used.add(entry.path);
      const ref = referenceByPath.get(entry.path); if (ref) matched++;
      const p = Object.assign(ref ? copy(ref) : {id: 'import-' + i, name: entry.path.split('/').pop(), path: entry.path, unit: '', min: null, max: null, mode: 'inherit'}, {value: entry.value, type: entry.type});
      if (entry.rawNumeric !== undefined) p.rawNumeric = entry.rawNumeric; else delete p.rawNumeric;
      return p;
    });
    if (!params.length) fail('NO_PARAMETERS', '未找到可提取的参数');
    return {name, format, rootName, params, matched, unknown: params.length - matched, sampleOnly: true};
  }
  function parameterTree(params) {
    if (!Array.isArray(params) || !params.length || params.length > LIMITS.params) fail('PARAMETER_LIMIT', '请选择 1–10,000 项有效参数');
    const root = {children: new Map()};
    params.forEach(p => {
      if (typeof p.path !== 'string' || !/^\/[^/]+(?:\/[^/]+)*$/.test(p.path)) fail('PATH_INVALID', '文件字段路径无效：' + p.path);
      const keys = p.path.slice(1).split('/'); if (keys.length > LIMITS.depth) fail('TOO_DEEP', '字段路径超过原型层级限制');
      let node = root;
      keys.forEach((key, i) => {
        fieldKey(key); if (node.param) fail('PATH_COLLISION', '文件字段相互覆盖：' + p.path);
        if (!node.children.has(key)) node.children.set(key, {children: new Map()}); node = node.children.get(key);
        if (i === keys.length - 1) {if (node.param || node.children.size) fail('PATH_COLLISION', '文件字段重复或相互覆盖：' + p.path); node.param = p;}
      });
    });
    return root;
  }
  function scalar(p) {
    const type = p.type || typeof p.value;
    if (type === 'number') {
      if (typeof p.value !== 'string' && typeof p.value !== 'number') fail('NUMBER_INVALID', '数值尚未确认：' + p.name);
      if (typeof p.value === 'number' && (!Number.isFinite(p.value) || Number.isInteger(p.value) && !Number.isSafeInteger(p.value))) fail('NUMBER_PRECISION', '此数值已超出可靠精度，请使用原始精确数值文本');
      let text = Object.is(p.value, -0) ? '-0' : String(p.value);
      if (numericValue(text) === null) fail('NUMBER_INVALID', '数值格式无效：' + p.name);
      if (typeof p.rawNumeric === 'string' && numericValue(p.rawNumeric) !== null && decimalIdentity(text) === decimalIdentity(p.rawNumeric)) text = p.rawNumeric;
      return {type, text};
    }
    if (type === 'boolean' && typeof p.value === 'boolean') return {type, text: String(p.value)};
    if (type === 'string' && typeof p.value === 'string') return {type, text: p.value};
    fail('VALUE_UNSUPPORTED', '原型只导出已确认的数值、文本和布尔值：' + p.name);
  }
  function escapeXml(text) {return validXmlChars(text).replace(/[&<>\r]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '\r': '&#13;'}[c]));}
  function serialize(params, format, options) {
    const root = parameterTree(params); options = options || {};
    if (format === 'json') {
      function render(node, depth) {
        if (node.param) {const s = scalar(node.param); return s.type === 'string' ? JSON.stringify(s.text) : s.text;}
        return '{\n' + Array.from(node.children, ([key, child]) => '  '.repeat(depth + 1) + JSON.stringify(key) + ': ' + render(child, depth + 1)).join(',\n') + '\n' + '  '.repeat(depth) + '}';
      }
      return render(root, 0) + '\n';
    }
    if (format !== 'xml') fail('FORMAT_UNSUPPORTED', '请选择 XML 或 JSON');
    const rootName = options.rootName || 'Configuration'; if (!XML_NAME.test(rootName) || RESERVED.has(rootName)) fail('XML_NAME', 'XML 根元素名称不适用于通用示例导出');
    function render(node, depth) {
      return Array.from(node.children, ([key, child]) => {
        if (!XML_NAME.test(key)) fail('XML_NAME', '该字段不适合通用 XML 示例导出，请选择 JSON 或正式模板');
        const pad = '  '.repeat(depth);
        return child.param ? pad + '<' + key + '>' + escapeXml(scalar(child.param).text) + '</' + key + '>\n' : pad + '<' + key + '>\n' + render(child, depth + 1) + pad + '</' + key + '>\n';
      }).join('');
    }
    return '<?xml version="1.0" encoding="UTF-8"?>\n<' + rootName + '>\n' + render(root, 1) + '</' + rootName + '>\n';
  }
  function composeUrl(template, values) {
    const result = String(template || '').replace(/\{(model|version)\}/g, (_, key) => encodeURIComponent(String(values?.[key] || '')));
    if (/\{[^}]*\}/.test(result)) fail('URL_TEMPLATE', '地址模板只支持 {model} 和 {version}');
    const url = new URL(result);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) fail('URL_INVALID', '仅接受无内嵌账号密码的 HTTP(S) 地址');
    return url.href;
  }
  function redactUrl(input) {try {const u = new URL(input); u.username = ''; u.password = ''; const query = u.search; u.search = ''; u.hash = ''; return u.href + (query ? '?…' : '');} catch (_) {return '地址未确认';}}
  function sampleDraft(kind, target) {
    if (!KINDS.has(kind) || kind === 'auto') kind = 'html';
    const urls = {file: 'files/MachineConfig.xml', html: 'releases/691/', zip: 'releases/691/config.zip', json: 'api/691/files.json', xml: 'api/691/files.xml'};
    return {id: '', name: '设备参数中心', url: 'https://config.example.invalid/' + urls[kind], kind, connection: '无', schedule: '仅手动更新', enabled: true, path: kind === 'json' ? '/files' : kind === 'xml' ? './files/file' : 'a[href]', urlField: kind === 'json' ? 'downloadUrl' : kind === 'xml' ? '@href' : 'href', nameField: kind === 'json' ? 'name' : kind === 'xml' ? '@name' : 'text', include: '*.xml; *.json', archive: '**/*.xml; **/*.json', target: target || '', sourceVersion: 0};
  }
  const FIELDS = ['name', 'path', 'type', 'unit', 'min', 'max', 'value', 'mode'];
  function sameField(a, b, field) {
    if ((field === 'value' && a.type === 'number' && b.type === 'number' || ['min', 'max'].includes(field)) && a[field] !== null && b[field] !== null && a[field] !== undefined && b[field] !== undefined) {
      const x = numericValue(String(a[field])), y = numericValue(String(b[field]));
      if (x !== null && y !== null) return decimalIdentity(String(a[field])) === decimalIdentity(String(b[field]));
    }
    return JSON.stringify(a[field]) === JSON.stringify(b[field]);
  }
  function countChanges(before, after) {
    const left = new Map((before || []).map(p => [p.id || p.path, p])), right = new Map((after || []).map(p => [p.id || p.path, p]));
    let count = 0; new Set([...left.keys(), ...right.keys()]).forEach(id => {const a = left.get(id), b = right.get(id); if (!a || !b || FIELDS.some(field => !sameField(a, b, field))) count++;}); return count;
  }
  function pause(ms, signal) {
    if (signal?.aborted) return Promise.reject(Object.assign(new Error('操作已取消'), {code: 'CANCELLED'}));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(done, ms);
      function done() {signal?.removeEventListener('abort', abort); resolve();}
      function abort() {clearTimeout(timer); signal?.removeEventListener('abort', abort); reject(Object.assign(new Error('操作已取消'), {code: 'CANCELLED'}));}
      signal?.addEventListener('abort', abort, {once: true});
    });
  }
  function create(options) {
    options = options || {};
    if (typeof options.getFiles !== 'function' || typeof options.getHead !== 'function') fail('HOOKS_REQUIRED', '来源模块需要 getFiles 与 getHead');
    const stores = new Map(), histories = new Map(), candidatesBySource = new Map(), payloads = new Map(), contexts = new Map(), proofs = new Map(), active = new Map();
    const delay = options.delayMs ?? 300; let sequence = 0;
    const clock = () => options.now ? options.now() : new Date().toISOString();
    const keyFor = (wid, id) => JSON.stringify([wid, id]);
    const actor = (wid, arg) => {const current = options.getActor?.(wid); return arg?.actorId || current?.id || current || 'lin';};
    const files = wid => {const result = options.getFiles(wid); if (!Array.isArray(result)) fail('WORKSPACE_NOT_FOUND', '工作区不存在'); return result;};
    function store(wid) {files(wid); if (!stores.has(wid)) stores.set(wid, new Map()); if (!histories.has(wid)) histories.set(wid, []); return stores.get(wid);}
    function getInternal(wid, id) {const source = store(wid).get(id); if (!source) fail('SOURCE_NOT_FOUND', '当前工作区没有此更新来源'); return source;}
    function targetBranch(wid, target, requested) {const file = files(wid).find(f => f.id === target); if (!file) fail('TARGET_NOT_FOUND', '请选择当前工作区的接收配置'); const branch = requested ? (file.branches || []).find(b => b.id === requested) : (file.branches || []).find(b => b.kind === 'baseline') || file.branches?.[0]; if (!branch) fail('BRANCH_NOT_FOUND', '接收配置分支不存在'); return branch.id;}
    function emit(action, wid, actorId, sourceId, details) {options.onEvent?.({id: 'source-event-' + (++sequence), workspaceId: wid, actorId, sourceId, at: clock(), action, details: copy(details)});}
    function fingerprint(wid, draft) {return JSON.stringify([wid, draft.id || '', ...['url', 'kind', 'connection', 'path', 'urlField', 'nameField', 'include', 'archive', 'target'].map(k => draft[k] || '')]);}
    function invalidate(contextKey) {const key = contextKey || 'source-editor'; contexts.set(key, (contexts.get(key) || 0) + 1); for (const [id, proof] of proofs) if (proof.contextKey === key) proofs.delete(id);}
    function draft(wid, id, target) {
      const old = id ? getInternal(wid, id) : null;
      return Object.assign(sampleDraft('html', target), old ? copy(old.rules) : {}, {id: old?.id || '', name: old?.name || '', url: old?.url || '', sourceVersion: old?.version || 0}, target ? {target} : {});
    }
    function validateMockUrl(d) {
      let url; try {url = new URL(d.url);} catch (_) {fail('URL_INVALID', '请输入完整的 HTTP(S) 地址');}
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) fail('URL_INVALID', '仅接受无内嵌账号密码的 HTTP(S) 地址');
      if (!url.hostname.endsWith('.invalid')) fail('MOCK_ONLY', '此原型不访问真实网址；请载入 .invalid 样例，正式接入尚未实现');
      if (!KINDS.has(d.kind || 'auto')) fail('KIND_INVALID', '请选择支持的演示内容类型');
      return url;
    }
    async function detect(wid, input, opts) {
      opts = opts || {}; files(wid); const d = copy(input), url = validateMockUrl(d), contextKey = opts.contextKey || 'source-editor';
      invalidate(contextKey); const requestId = contexts.get(contextKey);
      const stamp = fingerprint(wid, d); await pause(delay, opts.signal);
      if (contexts.get(contextKey) !== requestId) fail('STALE_CONTEXT', '当前输入或页面已改变，请重新识别预览');
      const path = url.pathname.toLowerCase(), kind = d.kind && d.kind !== 'auto' ? d.kind : path.endsWith('.zip') ? 'zip' : path.endsWith('files.json') ? 'json' : path.endsWith('files.xml') ? 'xml' : /\.(xml|json)$/.test(path) ? 'file' : 'html';
      const names = kind === 'file' ? [decodeURIComponent(url.pathname.split('/').pop() || 'MachineConfig.xml')] : ['MachineConfig.xml', 'ProcessRecipe.json'];
      const old = d.id ? store(wid).get(d.id) : null;
      const entries = names.slice(0, LIMITS.previewFiles).map((name, index) => {
        const path = kind === 'zip' ? 'release-691/config/' + name : '/files/' + name;
        const selectedFile = kind === 'file' && d.target ? files(wid).find(f => f.id === d.target) : files(wid).find(f => f.name === name);
        const saved = old?.mappings.find(m => m.path === path);
        const target = saved ? saved.target : selectedFile?.id || '';
        let branchId = ''; try {if (target) branchId = targetBranch(wid, target, saved?.branchId);} catch (_) {}
        let count = 0; try {count = branchId ? options.getHead(wid, target, branchId)?.params.length || 0 : 0;} catch (_) {}
        return {key: path, path, name, format: /\.json$/i.test(name) ? 'JSON' : 'XML', count, target, branchId, selected: saved ? saved.selected !== false : !!target || index === 0, status: 'demo', message: '示例条目；未执行真实下载或提取规则'};
      });
      const preview = {testId: 'source-test-' + (++sequence), workspaceId: wid, fingerprint: stamp, contextKey, requestId, kind, title: ({file: '直接下载文件', html: '网页文件列表', zip: 'ZIP 压缩包', json: 'JSON 文件列表', xml: 'XML 文件列表'})[kind], mock: true, rulesExecuted: false, testedAt: clock(), files: entries, sampled: entries.length, total: names.length, limits: {files: LIMITS.previewFiles, bytes: LIMITS.bytes}, message: '仅演示文件对应流程；此结果不证明网址、登录或提取规则可用'};
      proofs.set(preview.testId, {workspaceId: wid, fingerprint: stamp, contextKey, requestId, files: entries.map(e => ({key: e.key, path: e.path, name: e.name}))});
      return copy(preview);
    }
    function save(wid, input, preview, opts) {
      const d = copy(input), s = store(wid), old = d.id ? s.get(d.id) : null, proof = proofs.get(preview?.testId);
      if (!proof || proof.workspaceId !== wid || preview.workspaceId !== wid || proof.fingerprint !== fingerprint(wid, d) || preview.fingerprint !== proof.fingerprint || contexts.get(proof.contextKey) !== proof.requestId) fail('STALE_TEST', '提取设置已改变，请重新识别当前输入');
      if (d.id && !old) fail('SOURCE_NOT_FOUND', '当前工作区没有此更新来源');
      if (old && d.sourceVersion !== old.version) fail('STALE_SOURCE', '来源设置已有更新，请保留输入并重新核对');
      if (old?.status === 'running') fail('SOURCE_RUNNING', '此来源正在获取，请等待完成后保存设置');
      if (!String(d.name || '').trim()) fail('NAME_REQUIRED', '请填写来源名称');
      const seen = new Set();
      if (!Array.isArray(preview.files) || preview.files.length !== proof.files.length) fail('STALE_TEST', '文件清单已改变，请重新识别');
      const mappings = preview.files.map(entry => {
        const original = proof.files.find(p => p.key === entry.key);
        if (!original || original.path !== entry.path || original.name !== entry.name || seen.has(entry.key)) fail('MAPPING_INVALID', '来源文件身份已改变，请重新识别'); seen.add(entry.key);
        const branchId = entry.selected !== false ? targetBranch(wid, entry.target, entry.branchId) : entry.branchId || '';
        return {key: entry.key, path: entry.path, name: entry.name, target: entry.target || '', branchId, selected: entry.selected !== false};
      });
      const selected = mappings.filter(m => m.selected); if (!selected.length) fail('MAPPING_EMPTY', '请至少选择一个文件');
      const targets = selected.map(m => m.target); if (new Set(targets).size !== targets.length) fail('MAPPING_COLLISION', '多个来源文件不能覆盖同一接收配置');
      const id = old?.id || 'src-' + (++sequence), changed = !old || fingerprint(wid, old.rules) !== fingerprint(wid, d) || JSON.stringify(old.mappings) !== JSON.stringify(mappings);
      const item = {id, workspaceId: wid, name: d.name.trim(), url: d.url, kind: preview.kind, connection: d.connection || '无', schedule: d.schedule || '仅手动更新', enabled: d.enabled !== false, status: 'ready', last: old?.last || null, lastSuccess: old?.lastSuccess || null, pending: changed ? 0 : old.pending, rules: Object.assign(copy(d), {id, sourceVersion: (old?.version || 0) + 1}), mappings, files: targets, version: (old?.version || 0) + 1, ruleVersion: (old?.ruleVersion || 0) + (changed ? 1 : 0)};
      s.set(id, item); if (changed) {candidatesBySource.set(keyFor(wid, id), new Map()); payloads.delete(keyFor(wid, id));}
      proofs.delete(preview.testId); emit('source_saved', wid, actor(wid, opts), id, {name: item.name, version: item.version, ruleVersion: item.ruleVersion, files: targets.length}); return copy(item);
    }
    function sampleParams(wid, mapping, head) {
      if (options.makeSampleParams) return copy(options.makeSampleParams(wid, mapping, copy(head)));
      const params = copy(head.params); let changed = false;
      params.forEach(p => {if (p.id === 'p-temp') {p.value = 45; delete p.rawNumeric; changed = true;} if (p.id === 'p-temp-max') {p.value = 65; delete p.rawNumeric; changed = true;}});
      if (!changed) {
        const p = params.find(p => p.type === 'number' && typeof p.value === 'number' && Number.isFinite(p.value) && Number.isSafeInteger(p.value));
        if (p) {const next = p.max !== null && p.max !== undefined && p.value + 1 > Number(p.max) ? p.value - 1 : p.value + 1; if ((p.min === null || p.min === undefined || next >= Number(p.min)) && next !== p.value) p.value = next; delete p.rawNumeric;}
        else {const other = params.find(p => p.type === 'boolean' || p.type === 'string'); if (other) other.value = other.type === 'boolean' ? !other.value : other.value + '（示例）';}
      }
      return params;
    }
    function candidateMap(wid, id) {const key = keyFor(wid, id); if (!candidatesBySource.has(key)) candidatesBySource.set(key, new Map()); return candidatesBySource.get(key);}
    function recompute(source) {source.pending = Array.from(candidateMap(source.workspaceId, source.id).values()).reduce((n, c) => n + c.changed, 0);}
    function run(wid, id, opts) {
      opts = opts || {}; const source = getInternal(wid, id), key = keyFor(wid, id);
      if (active.has(key)) return active.get(key);
      const actorId = actor(wid, opts), mappingSnapshot = copy(source.mappings.filter(m => m.selected));
      source.status = 'running';
      const task = (async () => {
        const record = {id: 'source-run-' + (++sequence), workspaceId: wid, sourceId: id, actorId, name: source.name, at: clock(), status: 'unchanged', files: 0, changed: 0, errors: [], message: '', mock: true};
        try {
          await pause(delay, opts.signal); if (opts.fail) fail('DEMO_FETCH_FAILED', '模拟获取失败；保留上一份有效内容和待核对变化');
          let cache = payloads.get(key); if (!cache) {cache = new Map(); payloads.set(key, cache);}
          for (const mapping of mappingSnapshot) {
            try {
              if (opts.failPaths?.includes(mapping.path)) fail('DEMO_FILE_FAILED', '此示例文件获取失败');
              targetBranch(wid, mapping.target, mapping.branchId);
              const head = options.getHead(wid, mapping.target, mapping.branchId); if (!head?.id || !Array.isArray(head.params)) fail('TARGET_EMPTY', '接收配置尚未初始化');
              const payloadKey = JSON.stringify([mapping.key, mapping.target, mapping.branchId, source.ruleVersion]);
              if (!cache.has(payloadKey)) cache.set(payloadKey, sampleParams(wid, mapping, head));
              const params = cache.get(payloadKey), changed = countChanges(head.params, params), cid = id + ':' + mapping.key;
              if (changed) candidateMap(wid, id).set(cid, {id: cid, workspaceId: wid, sourceId: id, sourceVersion: source.ruleVersion, fileId: mapping.target, branchId: mapping.branchId, headId: head.id, params: copy(params), changed, createdAt: clock(), status: 'pending', path: mapping.path});
              else candidateMap(wid, id).delete(cid);
              record.files++; record.changed += changed;
            } catch (error) {record.errors.push({path: mapping.path, message: error.message});}
          }
          record.status = record.errors.length ? record.files ? 'partial' : 'error' : record.changed ? 'changed' : 'unchanged';
          source.status = record.errors.length ? 'error' : 'ready'; if (record.files) source.lastSuccess = clock();
          record.message = record.status === 'partial' ? '部分示例文件失败；成功项可独立核对，失败项保留上一份内容' : record.status === 'error' ? '示例获取失败，上一份有效内容已保留' : record.changed ? record.changed + ' 项示例变化等待核对；未修改正式配置' : '示例内容没有变化';
        } catch (error) {source.status = 'error'; record.status = error.code === 'CANCELLED' ? 'cancelled' : 'error'; record.errors.push({path: '', message: error.message}); record.message = error.message;}
        finally {source.last = clock(); recompute(source); histories.get(wid).unshift(record); active.delete(key); emit('source_run', wid, actorId, id, record);}
        return copy(record);
      })();
      active.set(key, task); return task;
    }
    function candidates(wid, id) {getInternal(wid, id); return copy(Array.from(candidateMap(wid, id).values()));}
    function toDraft(wid, id, candidateId) {
      const source = getInternal(wid, id), candidate = candidateMap(wid, id).get(candidateId);
      if (!candidate || candidate.workspaceId !== wid || candidate.sourceVersion !== source.ruleVersion) fail('CANDIDATE_STALE', '待核对内容已失效，请重新获取');
      const head = options.getHead(wid, candidate.fileId, candidate.branchId);
      if (head?.id !== candidate.headId) fail('STALE_HEAD', '目标已有新保存记录，请重新获取并核对更新');
      return Object.assign(copy(candidate), {candidateId: candidate.id, message: '采用更新来源“' + source.name + '”的示例变化'});
    }
    function markApplied(wid, id, candidateId, savedRevisionId, opts) {
      const source = getInternal(wid, id), candidate = candidateMap(wid, id).get(candidateId);
      if (!candidate) return {applied: false, pending: source.pending, reason: '待核对内容已不存在'};
      const head = options.getHead(wid, candidate.fileId, candidate.branchId);
      if (!savedRevisionId || head?.id !== savedRevisionId || countChanges(head.params, candidate.params)) return {applied: false, pending: source.pending, reason: '保存结果尚未完整采用来源内容'};
      candidateMap(wid, id).delete(candidateId); recompute(source);
      emit('source_candidate_applied', wid, actor(wid, opts), id, {candidateId, fileId: candidate.fileId, branchId: candidate.branchId, revisionId: savedRevisionId});
      return {applied: true, pending: source.pending};
    }
    Object.entries(options.seed || {}).forEach(([wid, sources]) => sources.forEach(raw => {
      const s = store(wid), id = raw.id || 'src-' + (++sequence), rules = Object.assign(sampleDraft(raw.kind || 'html'), raw.rules || {}, {id, name: raw.name || '示例来源', url: raw.url || raw.rules?.url || sampleDraft('html').url, sourceVersion: raw.version || 1});
      const mappings = raw.mappings || (raw.files || []).map(target => {const file = files(wid).find(f => f.id === target); if (!file) fail('TARGET_NOT_FOUND', '示例来源目标不属于工作区'); const path = '/files/' + file.name; return {key: path, path, name: file.name, target, branchId: targetBranch(wid, target), selected: true};});
      s.set(id, {id, workspaceId: wid, name: rules.name, url: rules.url, kind: rules.kind, connection: rules.connection, schedule: rules.schedule, enabled: raw.enabled !== false, status: raw.status === 'error' ? 'error' : 'ready', last: raw.last || null, lastSuccess: null, pending: 0, rules, mappings: copy(mappings), files: mappings.filter(m => m.selected !== false).map(m => m.target), version: rules.sourceVersion, ruleVersion: 1});
    }));
    return {list: wid => copy(Array.from(store(wid).values())), get: (wid, id) => copy(getInternal(wid, id)), listRuns: wid => {store(wid); return copy(histories.get(wid));}, draft, fingerprint, invalidate, detect, save, run, candidates, toDraft, markApplied};
  }
  return {LIMITS, numericValue, parseText, serialize, composeUrl, redactUrl, sampleDraft, countChanges, create};
});
