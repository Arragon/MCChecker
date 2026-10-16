/*
 * [模板专用] 浏览器端 XML/JSON → compact document 解析。
 * 输出格式与服务端 app/core/parser.py 的 to_compact() 完全一致（见规范 §6.3），
 * 生产环境由服务端生成，本文件随 mock 一起移除。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});

  function jsonPointerEscape(k) {
    return String(k).replace(/~/g, "~0").replace(/\//g, "~1");
  }

  function pyStr(v) {
    if (v === null) return "null";
    if (v === true) return "true";
    if (v === false) return "false";
    return String(v);
  }

  function pyType(v) {
    if (v === null) return "NoneType";
    if (typeof v === "boolean") return "bool";
    if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
    return "str";
  }

  function newNodes() {
    return { p: [], t: [], l: [], v: [], id: [], loc: [] };
  }

  function push(N, p, t, l, v, id, loc) {
    N.p.push(p);
    N.t.push(t);
    N.l.push(l);
    N.v.push(v);
    N.id.push(id);
    N.loc.push(loc);
    return N.p.length - 1;
  }

  function parseJson(text, filename) {
    var data = JSON.parse(text.replace(/^\uFEFF/, ""));
    var N = newNodes();
    var root = push(N, -1, "json", filename, null, "root", "");
    function walk(val, parent, pid, ploc) {
      if (Array.isArray(val)) {
        val.forEach(function (item, i) {
          var id = pid + "[" + i + "]";
          var loc = ploc + "/" + i;
          if (item !== null && typeof item === "object") {
            var me = push(N, parent, Array.isArray(item) ? "array" : "object", "[" + i + "]", null, id, loc);
            walk(item, me, id, loc);
          } else {
            push(N, parent, pyType(item), "[" + i + "]", pyStr(item), id, loc);
          }
        });
      } else if (val !== null && typeof val === "object") {
        Object.keys(val).forEach(function (k) {
          var item = val[k];
          var id = pid + "." + k;
          var loc = ploc + "/" + jsonPointerEscape(k);
          if (item !== null && typeof item === "object") {
            var me = push(N, parent, Array.isArray(item) ? "array" : "object", k, null, id, loc);
            walk(item, me, id, loc);
          } else {
            push(N, parent, pyType(item), k, pyStr(item), id, loc);
          }
        });
      }
    }
    walk(data, root, "root", "");
    return N;
  }

  function parseXml(text, filename) {
    var dom = new DOMParser().parseFromString(text, "application/xml");
    var err = dom.getElementsByTagName("parsererror");
    if (err.length) throw new Error("XML 格式错误");
    var N = newNodes();
    var root = push(N, -1, "xml", filename, null, "root", "");
    function walk(el, parent, pid, ploc, occ) {
      var tag = el.nodeName;
      var id = pid + "/" + tag;
      var loc = ploc + "/" + tag + "[" + occ + "]";
      var hasChildEl = false;
      var text = "";
      for (var c = el.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 1) hasChildEl = true;
        else if (c.nodeType === 3 || c.nodeType === 4) {
          if (!hasChildEl) text += c.nodeValue;
        }
      }
      text = text.trim();
      var me = push(N, parent, "element", tag, hasChildEl || !text ? null : text, id, loc);
      for (var a = 0; a < el.attributes.length; a++) {
        var at = el.attributes[a];
        if (at.name === "xmlns" || at.name.indexOf("xmlns:") === 0) continue;
        push(N, me, "attribute", "@" + at.name, at.value, id + "@" + at.name, loc + "/@" + at.name);
      }
      if (hasChildEl && text) push(N, me, "text", "#text", text, id + "#text", loc + "/#text");
      var seen = {};
      for (c = el.firstChild; c; c = c.nextSibling) {
        if (c.nodeType !== 1) continue;
        seen[c.nodeName] = (seen[c.nodeName] || 0) + 1;
        walk(c, me, id, loc, seen[c.nodeName]);
      }
    }
    walk(dom.documentElement, root, "root", "", 1);
    return N;
  }

  /** 与服务端 sha256 等价的稳定指纹（模板内仅用于演示，FNV-1a）。 */
  function hash(text) {
    var hsh = 0x811c9dc5;
    for (var i = 0; i < text.length; i++) {
      hsh ^= text.charCodeAt(i);
      hsh = Math.imul(hsh, 0x01000193) >>> 0;
    }
    return ("00000000" + hsh.toString(16)).slice(-8) + text.length.toString(16);
  }

  function parse(text, filename) {
    var fmt = MC.util.fileFormat(filename);
    if (fmt === "json") return { format: "json", nodes: parseJson(text, filename) };
    if (fmt === "xml") return { format: "xml", nodes: parseXml(text, filename) };
    try {
      return { format: "json", nodes: parseJson(text, filename) };
    } catch (e) {
      return { format: "xml", nodes: parseXml(text, filename) };
    }
  }

  MC.mockParse = { parse: parse, hash: hash };
})(window);
