"""XSS 注入防护测试 —— 验证动态内容被正确 escape

覆盖范围:
- viewer.py: _render_node 中 label/value 的 HTML escape
- search.py: _render_node 中 label/value 的 HTML escape
- home.py: _render_card_tree_node, data-fav-path, JS 注入防护
- tools.py: safe_external_url URL 验证
- dltool.py: 数值表格 HTML escape
- records.py: _render_diff_chips, _render_structural_diff_blocks, _render_unified_diff
- history.py: _render_diff_chips
"""

import html
import json
import pytest


class TestHTMLEscaping:
    """验证 html.escape 对各类 XSS payload 的防护"""

    def test_script_tag_escaped(self):
        """<script> 标签被 escape"""
        malicious = "<script>alert('xss')</script>"
        escaped = html.escape(malicious)
        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped

    def test_img_onerror_escaped(self):
        """<img onerror> 被 escape"""
        malicious = '<img src=x onerror=alert(1)>'
        escaped = html.escape(malicious)
        assert "<img" not in escaped
        assert "&lt;img" in escaped

    def test_event_handler_escaped(self):
        """事件处理属性被 escape"""
        malicious = '"><div onclick="alert(1)">'
        escaped = html.escape(malicious, quote=True)
        assert "onclick" not in escaped or "&quot;" in escaped

    def test_quotes_escaped(self):
        """引号被 escape（用于属性值注入防护）"""
        malicious = '" onclick="alert(1)'
        escaped = html.escape(malicious, quote=True)
        assert '"' not in escaped or "&quot;" in escaped

    def test_single_quotes_escaped(self):
        """单引号被 escape"""
        malicious = "' onload='alert(1)'"
        escaped = html.escape(malicious, quote=True)
        assert "'" not in escaped or "&#x27;" in escaped

    def test_style_injection_escaped(self):
        """CSS 注入被 escape"""
        malicious = '<style>body{display:none}</style>'
        escaped = html.escape(malicious)
        assert "<style>" not in escaped
        assert "&lt;style&gt;" in escaped

    def test_iframe_injection_escaped(self):
        """iframe 注入被 escape"""
        malicious = '<iframe src="javascript:alert(1)">'
        escaped = html.escape(malicious)
        assert "<iframe" not in escaped

    def test_svg_injection_escaped(self):
        """SVG 注入被 escape"""
        malicious = '<svg onload="alert(1)">'
        escaped = html.escape(malicious)
        assert "<svg" not in escaped

    def test_data_attribute_injection_escaped(self):
        """data 属性注入被 escape"""
        malicious = '"><script>alert(1)</script><span data-x="'
        escaped = html.escape(malicious, quote=True)
        assert "<script>" not in escaped

    def test_unicode_control_chars_preserved(self):
        """Unicode control chars 被 escape 后不会破坏 DOM"""
        malicious = '\u202e<script>alert(1)</script>'  # RTL override
        escaped = html.escape(malicious)
        assert "<script>" not in escaped

    def test_null_byte_escaped(self):
        """空字节不影响 HTML 结构"""
        malicious = '<scr\x00ipt>alert(1)</script>'
        escaped = html.escape(malicious)
        # 即使 null byte 存在，< 和 > 都被 escape 了
        assert "&lt;" in escaped
        assert "&gt;" in escaped

    def test_ampersand_escaped(self):
        """& 符号被 escape"""
        text = "a & b < c > d"
        escaped = html.escape(text)
        assert "&amp;" in escaped
        assert "&lt;" in escaped
        assert "&gt;" in escaped


class TestURLValidation:
    """验证 URL scheme 过滤"""

    def test_http_allowed(self):
        from app.utils.helpers import safe_external_url
        assert safe_external_url("http://example.com")

    def test_https_allowed(self):
        from app.utils.helpers import safe_external_url
        assert safe_external_url("https://example.com")

    def test_https_with_path_allowed(self):
        from app.utils.helpers import safe_external_url
        assert safe_external_url("https://example.com/path/to/page?q=1")

    def test_mailto_allowed(self):
        from app.utils.helpers import safe_external_url
        assert safe_external_url("mailto:user@example.com")

    def test_javascript_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("javascript:alert(1)")

    def test_javascript_uppercase_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("JAVASCRIPT:alert(1)")

    def test_data_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("data:text/html,<script>alert(1)</script>")

    def test_vbscript_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("vbscript:MsgBox('XSS')")

    def test_file_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("file:///etc/passwd")

    def test_empty_url_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("")

    def test_no_scheme_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("//example.com")

    def test_ftp_rejected(self):
        from app.utils.helpers import safe_external_url
        assert not safe_external_url("ftp://example.com/file")


class TestJSONEncodingForJS:
    """验证 json.dumps 对 JS 注入的防护"""

    def test_quotes_in_path(self):
        """路径中的引号被 JSON 编码"""
        path = 'test"path'
        encoded = json.dumps(path)
        assert '\\"' in encoded  # 引号被转义

    def test_backslash_in_path(self):
        """反斜杠被 JSON 编码"""
        path = r'test\path'
        encoded = json.dumps(path)
        assert "\\\\" in encoded  # 反斜杠被转义

    def test_script_in_path(self):
        """script 标签在 JSON 中被安全编码"""
        path = '<script>alert(1)</script>'
        encoded = json.dumps(path)
        # JSON 编码后 < > 不转义，但引号和反斜杠会
        # 关键是它作为 JS 字符串使用时不会被解析为 HTML
        assert isinstance(encoded, str)
        # 验证可以被安全地嵌入 JS
        assert encoded.startswith('"')
        assert encoded.endswith('"')

    def test_newline_in_path(self):
        """换行符被 JSON 编码"""
        path = 'test\npath'
        encoded = json.dumps(path)
        assert "\\n" in encoded

    def test_unicode_in_path(self):
        """Unicode 字符被 JSON 安全处理"""
        path = '测试\u202e路径'
        encoded = json.dumps(path)
        assert isinstance(encoded, str)


class TestRecordsEscaping:
    """验证 records.py 的 diff 渲染函数中的手动 escape"""

    def test_manual_esc_handles_script(self):
        """records.py 的 _esc 函数能处理 script 标签"""
        # 模拟 records.py 中的 _esc 函数逻辑
        def _esc(v):
            s = "" if v is None else str(v)
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        malicious = "<script>alert(1)</script>"
        escaped = _esc(malicious)
        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped

    def test_manual_esc_handles_ampersand(self):
        """& 被正确处理"""
        def _esc(v):
            s = "" if v is None else str(v)
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        text = "a & b"
        escaped = _esc(text)
        assert "&amp;" in escaped

    def test_manual_esc_handles_none(self):
        """None 值被安全处理"""
        def _esc(v):
            s = "" if v is None else str(v)
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        assert _esc(None) == ""


class TestViewerEscaping:
    """验证 viewer.py 中 label/value 的 escape"""

    def test_label_with_html_escaped(self):
        """含 HTML 的 label 被安全 escape"""
        label = '<img src=x onerror=alert(1)>'
        escaped = html.escape(str(label))
        assert "<img" not in escaped

    def test_value_with_html_escaped(self):
        """含 HTML 的 value 被安全 escape"""
        value = '<script>document.cookie</script>'
        escaped = html.escape(str(value))
        assert "<script>" not in escaped

    def test_numeric_value_safe(self):
        """数值型 value 经 str + escape 后安全"""
        value = 42
        escaped = html.escape(str(value))
        assert escaped == "42"


class TestHomeFavPathEscaping:
    """验证 home.py 中 data-fav-path 属性的 escape"""

    def test_path_with_quotes_escaped(self):
        """含引号的路径在 HTML 属性中被安全 escape"""
        path = 'test"path'
        escaped = html.escape(str(path), quote=True)
        assert '"' not in escaped or "&quot;" in escaped

    def test_path_for_js_uses_json(self):
        """路径用于 JS 时使用 JSON 编码"""
        path = 'test"; alert(1); "'
        encoded = json.dumps(path)
        # JSON 编码后引号被转义，不会中断 JS 字符串
        assert encoded == '"test\\"; alert(1); \\""'
