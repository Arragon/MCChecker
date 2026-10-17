"""审阅写回：只在原文上改选中节点（规范 §6.7 第 5–6 步；T-16 / M3-01、INH-1886）。

`reviewing.apply_edits(src, fmt, edits) -> bytes` 是 `core` 层的纯函数：输入原文字节与
`(loc, new_value, kind)` 列表，输出新原文字节；任何不安全情况都以 `AppError` 抛出且
**不返回任何字节**（幂等与 legacy 建议两条属 M3-02，不在本文件）。

按卡面已知限制，断言只针对**业务值与节点集合**（compact 的 `loc`/`t`/`v`），不断言引号
风格、空元素写法、实体写法——标准库必然会统一它们（§6.7 已知限制）。
"""

from __future__ import annotations

import codecs
import json
from typing import Dict, List

import pytest

from app.core import parser
from app.core.errors import (
    INVALID_INPUT,
    PARSE_FAILED,
    REVIEW_CONFLICT,
    REVIEW_UNSAFE,
    AppError,
)
from app.core.reviewing import apply_edits

# M1-01 的既有断言样本形状（`tests/test_compact.py`）：属性、同名兄弟、混合内容、
# 参数元数据、无值节点，外加声明/注释/处理指令与缩进，用来回归「同名兄弟只改第 2 个」。
XML_DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<R a="1">\n'
    "  <!-- 参数说明 -->\n"
    "  <?mc-check keep?>\n"
    "  <item>first</item>\n"
    "  <item>second</item>\n"
    "  <mixed>y<sub>z</sub></mixed>\n"
    '  <param_1 description="d" min="0" max="9" type="int">5</param_1>\n'
    "  <empty/>\n"
    "</R>\n"
).encode("utf-8")

# URI 里带 `/`：`loc` 的 `名称` 段就是 ET 原始 tag（§6.2），所以定位符里会出现裸 `/`。
NS_DOC = (
    b'<r xmlns="http://ns.example/a" xmlns:p="http://ns.example/p">'
    b'<v p:k="1">t</v></r>'
)

PREFIXED_DOC = b'<a:root xmlns:a="urn:x" xmlns:unused="urn:u"><a:child a:k="v">t</a:child></a:root>'

JSON_DOC = (
    b'{"n": 1, "f": 1.5, "b": true, "s": "x", "z": null,'
    b' "nest": {"k~y": 2, "a/b": 3}, "arr": [10, {"k": "v"}]}'
)


def values(content: bytes, name: str = "doc.xml") -> Dict[str, str]:
    """compact 里有值节点的 `loc → v`（§6.2）：「业务值」的唯一口径。"""
    nodes = parser.to_compact(content, name)["nodes"]
    return {loc: v for loc, v in zip(nodes["loc"], nodes["v"]) if v is not None}


def node_types(content: bytes, name: str = "doc.xml") -> List[str]:
    """compact 的 `t` 序列：结构没被动过的判据（§6.7 第 6 步）。"""
    return parser.to_compact(content, name)["nodes"]["t"]


def labels(content: bytes, name: str = "doc.xml") -> List[str]:
    return parser.to_compact(content, name)["nodes"]["l"]


def expected_of(content: bytes, name: str, changes: Dict[str, str]) -> Dict[str, str]:
    merged = values(content, name)
    merged.update(changes)
    return merged


class TestApplyXml:
    """T-16 第 1、2、3 条：属性值、元素文本、同名兄弟只改第 2 个。"""

    def test_attribute_value_is_replaced_and_nothing_else_moves(self):
        out = apply_edits(XML_DOC, "xml", [("/R[1]/@a", "2", "attribute")])
        assert values(out) == expected_of(XML_DOC, "doc.xml", {"/R[1]/@a": "2"})
        assert node_types(out) == node_types(XML_DOC)

    def test_element_text_is_replaced_and_attribute_metadata_survives(self):
        out = apply_edits(XML_DOC, "xml", [("/R[1]/param_1[1]", "7", "element")])
        changed = {"/R[1]/param_1[1]": "7"}
        assert values(out) == expected_of(XML_DOC, "doc.xml", changed)
        # 元数据属性是独立节点，必须原样留着
        assert values(out)["/R[1]/param_1[1]/@type"] == "int"
        assert values(out)["/R[1]/param_1[1]/@description"] == "d"

    def test_entity_and_quote_style_differences_do_not_break_the_round_trip(self):
        """已知限制：标准库统一引号与实体写法。断言业务值，不断言写法（§6.7）。"""
        src = b'<R a="say &quot;hi&quot; &amp;amp; bye" b="1"><t>x&lt;y</t></R>'
        out = apply_edits(src, "xml", [("/R[1]/@b", 'A"B & C', "attribute")])
        after = values(out)
        assert after["/R[1]/@b"] == 'A"B & C', "写入的引号与 & 必须原样读回来"
        assert after["/R[1]/@a"] == 'say "hi" &amp; bye', "未选中节点按值不变，实体写法可变"
        assert after["/R[1]/t[1]"] == "x<y"

    def test_only_the_second_same_name_sibling_changes(self):
        """v1 的根因：同名兄弟共享旧 ID，只有 `loc` 的 occurrence 能区分。"""
        nodes = parser.to_compact(XML_DOC, "doc.xml")["nodes"]
        assert nodes["id"][nodes["loc"].index("/R[1]/item[1]")] == "root/R/item"
        assert nodes["id"][nodes["loc"].index("/R[1]/item[2]")] == "root/R/item"
        out = apply_edits(XML_DOC, "xml", [("/R[1]/item[2]", "second2", "element")])
        after = values(out)
        assert after["/R[1]/item[2]"] == "second2"
        assert after["/R[1]/item[1]"] == "first", "同名兄弟第一个不能被改动"

    def test_mixed_content_text_row_is_replaced_without_touching_children(self):
        out = apply_edits(XML_DOC, "xml", [("/R[1]/mixed[1]/#text", "Y", "text")])
        after = values(out)
        assert after["/R[1]/mixed[1]/#text"] == "Y"
        assert after["/R[1]/mixed[1]/sub[1]"] == "z"

    def test_several_edits_apply_together(self):
        out = apply_edits(
            XML_DOC,
            "xml",
            [
                ("/R[1]/@a", "9", "attribute"),
                ("/R[1]/item[1]", "one", "element"),
                ("/R[1]/param_1[1]/@max", "10", "attribute"),
            ],
        )
        assert values(out) == expected_of(
            XML_DOC,
            "doc.xml",
            {"/R[1]/@a": "9", "/R[1]/item[1]": "one", "/R[1]/param_1[1]/@max": "10"},
        )

    def test_whitespace_and_declaration_are_not_reformatted(self):
        """不调用 `ET.indent`：源有声明就保留声明，缩进按原文留着（§6.7 第 5 步）。"""
        out = apply_edits(XML_DOC, "xml", [("/R[1]/item[1]", "first2", "element")])
        assert out.startswith(b"<?xml")
        assert b"\n  <item>first2</item>" in out

    def test_absent_declaration_is_not_added(self):
        src = b"<R><a>1</a></R>"
        out = apply_edits(src, "xml", [("/R[1]/a[1]", "2", "element")])
        assert not out.startswith(b"<?xml")
        assert values(out, "doc.xml") == {"/R[1]/a[1]": "2"}

    def test_stylesheet_pi_is_not_mistaken_for_a_declaration(self):
        """`<?xml-stylesheet?>` 合法地出现在根之前：不能被认成声明而凭空加一行 `<?xml?>`。"""
        src = b'<?xml-stylesheet type="text/xsl" href="s.xsl"?>\n<R><a>1</a></R>'
        out = apply_edits(src, "xml", [("/R[1]/a[1]", "2", "element")])
        assert not out.startswith(b"<?xml version")
        assert values(out) == {"/R[1]/a[1]": "2"}

    def test_unknown_encoding_declaration_is_parse_failed(self):
        """expat 对不认识的编码名抛 `LookupError`，不能被当成 500。"""
        src = b'<?xml version="1.0" encoding="NO-SUCH-CODEC"?><R><a>1</a></R>'
        with pytest.raises(AppError) as excinfo:
            apply_edits(src, "xml", [("/R[1]/a[1]", "2", "element")])
        assert excinfo.value.code == PARSE_FAILED

    def test_comments_and_processing_instructions_survive(self):
        """T-16 第 5 条：`insert_comments=True` 的可观测断言。"""
        out = apply_edits(XML_DOC, "xml", [("/R[1]/item[1]", "one", "element")])
        assert out.count(b"<!--") == XML_DOC.count(b"<!--")
        assert out.count(b"<?mc-check") == XML_DOC.count(b"<?mc-check")
        assert "参数说明" in out.decode("utf-8")

    def test_utf8_bom_is_kept_at_the_front(self):
        src = codecs.BOM_UTF8 + b'<?xml version="1.0" encoding="UTF-8"?><R><a>1</a></R>'
        out = apply_edits(src, "xml", [("/R[1]/a[1]", "2", "element")])
        assert out.startswith(codecs.BOM_UTF8)
        assert values(out) == {"/R[1]/a[1]": "2"}

    def test_namespaced_document_keeps_its_prefixes(self):
        """T-16 第 4 条：注册原前缀后重写，显示标签仍用作者写的 `a:`，不是 `ns0:`。"""
        base = values(PREFIXED_DOC, "ns.xml")
        assert base == {
            "/{urn:x}root[1]/{urn:x}child[1]": "t",
            "/{urn:x}root[1]/{urn:x}child[1]/@{urn:x}k": "v",
        }
        loc = "/{urn:x}root[1]/{urn:x}child[1]"
        out = apply_edits(PREFIXED_DOC, "xml", [(loc, "T2", "element")])
        assert values(out, "ns.xml") == expected_of(PREFIXED_DOC, "ns.xml", {loc: "T2"})
        assert node_types(out, "ns.xml") == node_types(PREFIXED_DOC, "ns.xml")
        after = labels(out, "ns.xml")
        assert "a:root" in after and "a:child" in after and "@a:k" in after
        assert parser.to_compact(out, "ns.xml")["warnings"] == []
        # 已知限制：`xmlns:unused="urn:u"` 声明了却没被任何节点使用，标准库会把它丢掉。
        # 它不对应任何业务节点（§6.2 跳过 `xmlns*`），所以第 6 步校验不会拦，这里显式记下。
        assert "urn:u" not in out.decode("utf-8")

    def test_locator_whose_name_contains_a_slash_is_not_split(self):
        """`{http://…}tag` 里天然有 `/`：定位必须与 `to_compact` 同源，不能切字符串。"""
        base = values(NS_DOC, "ns.xml")
        root_loc = "/{http://ns.example/a}r[1]"
        attr_loc = root_loc + "/{http://ns.example/a}v[1]/@{http://ns.example/p}k"
        text_loc = root_loc + "/{http://ns.example/a}v[1]"
        assert set(base) == {attr_loc, text_loc}
        out = apply_edits(NS_DOC, "xml", [(attr_loc, "2", "attribute"), (text_loc, "T", "element")])
        assert values(out, "ns.xml") == expected_of(
            NS_DOC, "ns.xml", {attr_loc: "2", text_loc: "T"}
        )


class TestApplyJson:
    """T-16 第 6、7 条：类型保持与类型不符拒绝。"""

    def test_int_float_bool_str_keep_their_json_types(self):
        edits = [
            ("/n", "42", "int"),
            ("/f", "2.5", "float"),
            ("/b", "false", "bool"),
            ("/s", "y", "str"),
        ]
        out = apply_edits(JSON_DOC, "json", edits)
        data = json.loads(out.decode("utf-8"))
        assert (data["n"], data["f"], data["b"], data["s"]) == (42, 2.5, False, "y")
        assert isinstance(data["n"], int) and not isinstance(data["n"], bool)
        assert isinstance(data["f"], float) and isinstance(data["b"], bool)
        assert values(out, "c.json") == expected_of(
            JSON_DOC, "c.json", {"/n": "42", "/f": "2.5", "/b": "false", "/s": "y"}
        )
        assert node_types(out, "c.json") == node_types(JSON_DOC, "c.json")

    def test_escaped_pointer_tokens_and_array_items(self):
        out = apply_edits(
            JSON_DOC, "json", [("/nest/k~0y", "3", "int"), ("/nest/a~1b", "7", "int"), ("/arr/0", "11", "int")]
        )
        data = json.loads(out.decode("utf-8"))
        assert data["nest"]["k~y"] == 3 and data["nest"]["a/b"] == 7
        assert data["arr"][0] == 11 and data["arr"][1] == {"k": "v"}
        assert values(out, "c.json") == expected_of(
            JSON_DOC, "c.json", {"/nest/k~0y": "3", "/nest/a~1b": "7", "/arr/0": "11"}
        )

    def test_key_order_and_container_shape_are_unchanged(self):
        out = apply_edits(JSON_DOC, "json", [("/s", "y", "str")])
        assert labels(out, "c.json") == labels(JSON_DOC, "c.json")
        assert list(json.loads(out.decode("utf-8"))) == list(json.loads(JSON_DOC.decode("utf-8")))

    def test_empty_string_key(self):
        """空键名的 `loc` 就是 `"/"`：切 token 时不能把它当成根。"""
        src = b'{"": 1, "a": {"": 2}}'
        assert set(values(src, "c.json")) == {"/", "/a/"}
        out = apply_edits(src, "json", [("/a/", "3", "int")])
        assert json.loads(out.decode("utf-8")) == {"": 1, "a": {"": 3}}
        assert values(out, "c.json") == {"/": "1", "/a/": "3"}

    def test_integer_proposed_with_leading_zero_lands_as_the_same_number(self):
        """JSON 里 `05` 与 `5` 是同一个数：语义校验按 compact 口径比对，不误报。"""
        out = apply_edits(JSON_DOC, "json", [("/n", "05", "int")])
        assert json.loads(out.decode("utf-8"))["n"] == 5
        assert values(out, "c.json") == expected_of(JSON_DOC, "c.json", {"/n": "5"})

    @pytest.mark.parametrize(
        "loc,new_value,kind",
        [
            ("/n", "abc", "int"),
            ("/n", "1.5", "int"),
            ("/f", "x", "float"),
            ("/f", "1e400", "float"),
            ("/b", "yes", "bool"),
            ("/b", "TRUE", "bool"),
        ],
    )
    def test_type_mismatch_is_rejected(self, loc: str, new_value: str, kind: str):
        with pytest.raises(AppError) as excinfo:
            apply_edits(JSON_DOC, "json", [(loc, new_value, kind)])
        assert excinfo.value.code == INVALID_INPUT

    def test_empty_string_is_a_legitimate_new_value_for_a_json_string_node(self):
        src = b'{"s": "x"}'
        out = apply_edits(src, "json", [("/s", "", "str")])
        assert json.loads(out.decode("utf-8")) == {"s": ""}
        assert values(out, "c.json") == {"/s": ""}

    def test_utf8_bom_is_kept_at_the_front(self):
        src = codecs.BOM_UTF8 + b'{"s": "x"}'
        out = apply_edits(src, "json", [("/s", "y", "str")])
        assert out.startswith(codecs.BOM_UTF8)
        assert values(out, "c.json") == {"/s": "y"}


class TestSemanticGuard:
    """T-16 第 8 条：第 6 步校验不过 → `REVIEW_UNSAFE`，且不返回任何字节。"""

    def test_noop_edit_is_rejected(self):
        """差异必须「恰好等于」本次修改：改回原值等于没改，不能算成功。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(JSON_DOC, "json", [("/s", "x", "str")])
        assert excinfo.value.code == REVIEW_UNSAFE

    def test_null_node_is_read_only(self):
        """D1：null 节点的当前值恒为 `"null"`，「建议写回 null」是无操作 → 校验拒绝。

        服务层在 §6.7 第 1 步就会拦下（见 `TestNullIsReadOnly`），这里是 core 的兜底。
        """
        with pytest.raises(AppError) as excinfo:
            apply_edits(JSON_DOC, "json", [("/z", "null", "NoneType")])
        assert excinfo.value.code == REVIEW_UNSAFE

    def test_two_edits_on_the_same_locator_are_rejected(self):
        """同一节点收到两条不同建议：只能落一个值，另一个会被静默丢弃 → 拒绝。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(JSON_DOC, "json", [("/s", "p", "str"), ("/s", "q", "str")])
        assert excinfo.value.code == REVIEW_UNSAFE

    def test_container_locator_is_not_a_writable_target(self):
        """容器节点在 compact 里没有值（§6.2）：它不是「选中节点的值」，定位不到。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(JSON_DOC, "json", [("/nest", "x", "str")])
        assert excinfo.value.code == REVIEW_CONFLICT

    def test_value_element_locator_is_not_a_writable_target(self):
        """`<empty/>` 定位得到但没有值：写它会凭空长出一个值，一律按定位不到拒绝。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(XML_DOC, "xml", [("/R[1]/empty[1]", "boom", "element")])
        assert excinfo.value.code == REVIEW_CONFLICT

    def test_mixed_content_element_locator_is_not_a_writable_target(self):
        """混合内容元素自身的值是 `#text` 行，不是元素行。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(XML_DOC, "xml", [("/R[1]/mixed[1]", "boom", "element")])
        assert excinfo.value.code == REVIEW_CONFLICT

    def test_wrong_kind_breaks_the_type_sequence_and_is_unsafe(self):
        """`kind` 取自建议时的节点类型；类型变了说明写回动了结构（§6.7 第 6 步 ③）。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(JSON_DOC, "json", [("/n", "42", "str")])
        assert excinfo.value.code == REVIEW_UNSAFE

    def test_clearing_a_text_row_is_rejected_because_the_row_disappears(self):
        with pytest.raises(AppError) as excinfo:
            apply_edits(XML_DOC, "xml", [("/R[1]/mixed[1]/#text", "   ", "text")])
        assert excinfo.value.code == REVIEW_UNSAFE

    # 「校验失败不写文件」由 `TestPurity.test_apply_edits_never_touches_the_filesystem` 覆盖：
    # `apply_edits` 只吃字节、只吐字节，失败时不返回任何字节。


class TestLocatingErrors:
    """`loc` 定位不到 → `REVIEW_CONFLICT`（卡面接口契约）。"""

    @pytest.mark.parametrize(
        "src,fmt,loc,name",
        [
            (XML_DOC, "xml", "/R[1]/nope[1]", "doc.xml"),
            (XML_DOC, "xml", "/R[1]/item[3]", "doc.xml"),
            (XML_DOC, "xml", "/R[1]/item[1]/#text", "doc.xml"),
            (XML_DOC, "xml", "/R[1]/@b", "doc.xml"),
            (JSON_DOC, "json", "/nope", "c.json"),
            (JSON_DOC, "json", "/arr/5", "c.json"),
            (JSON_DOC, "json", "/arr/01", "c.json"),
            (JSON_DOC, "json", "", "c.json"),
            (XML_DOC, "xml", "", "doc.xml"),
        ],
    )
    def test_locator_that_is_not_in_the_document_is_conflict(self, src, fmt, loc, name):
        with pytest.raises(AppError) as excinfo:
            apply_edits(src, fmt, [(loc, "1", "str")])
        assert excinfo.value.code == REVIEW_CONFLICT
        assert "无法唯一定位" in excinfo.value.message

    def test_empty_edit_list_is_invalid_input(self):
        with pytest.raises(AppError) as excinfo:
            apply_edits(XML_DOC, "xml", [])
        assert excinfo.value.code == INVALID_INPUT

    @pytest.mark.parametrize("fmt", ["yaml", "", "XML"])
    def test_unsupported_format_is_invalid_input(self, fmt: str):
        with pytest.raises(AppError) as excinfo:
            apply_edits(XML_DOC, fmt, [("/R[1]/item[1]", "x", "element")])
        assert excinfo.value.code == INVALID_INPUT

    def test_document_with_dtd_is_refused(self):
        """风险条：标准库重写会丢掉 DTD，第 6 步看不到差异 → 必须在写回前明确拒绝。"""
        src = b'<!DOCTYPE r SYSTEM "m.dtd"><r><a>1</a></r>'
        with pytest.raises(AppError) as excinfo:
            apply_edits(src, "xml", [("/r[1]/a[1]", "2", "element")])
        assert excinfo.value.code == REVIEW_UNSAFE
        assert "DTD" in excinfo.value.message

    def test_junk_after_document_element_is_refused(self):
        """显示容错（§6.1 第 2 步）不等于写回容错：截断会丢内容，一律拒绝。"""
        src = b"<R><a>1</a></R>\n<stray/>"
        assert parser.to_compact(src, "doc.xml")["warnings"]
        with pytest.raises(AppError) as excinfo:
            apply_edits(src, "xml", [("/R[1]/a[1]", "2", "element")])
        assert excinfo.value.code == REVIEW_UNSAFE

    def test_unbound_prefix_is_refused(self):
        """显示时会自动补声明，但补出来的 `urn:前缀` 是假的，不能写进原文。"""
        src = b"<a:r><a:c>1</a:c></a:r>"
        assert parser.to_compact(src, "doc.xml")["warnings"]
        with pytest.raises(AppError) as excinfo:
            apply_edits(src, "xml", [("/{urn:a}r[1]/{urn:a}c[1]", "2", "element")])
        assert excinfo.value.code == REVIEW_UNSAFE

    @pytest.mark.parametrize(
        "src,fmt,loc,kind",
        [
            (b'{"s": "\xff\xfe"}', "json", "/s", "str"),
            (b"{not json", "json", "/s", "str"),
            (b"<R><unclosed></R>", "xml", "/R[1]/a[1]", "element"),
        ],
    )
    def test_unparsable_source_is_parse_failed(self, src, fmt, loc, kind):
        """读不出来的原文与显示链路同口径（§6.1 `PARSE_FAILED`），不另造错误码。"""
        with pytest.raises(AppError) as excinfo:
            apply_edits(src, fmt, [(loc, "2", kind)])
        assert excinfo.value.code == PARSE_FAILED


class TestNullIsReadOnly:
    """D1（§6.7 第 4 步注）：null 是只读值。本卡只管 core 侧的这一半。

    提交侧（§6.7 第 1 步「与当前值相同」）的回归在 `tests/test_api_remarks.py::
    TestRemarkSubmit::test_json_type_checks`——那是 `services/review.py::submit` 的
    归属，`apply_edits` 不做该检查（执行期裁定 R22）。这里钉两件事：null 在 compact
    里确实是「有值节点」（规则成立的前提），以及 `apply_edits` 会把「改成 null」这种
    无变更写回按 `REVIEW_UNSAFE` 拒绝（见 `TestSemanticGuard.test_null_node_is_read_only`）。
    """

    def test_null_node_is_a_valued_node_in_compact(self):
        found = values(b'{"z": null}', "c.json")
        assert found == {"/z": "null"}
        assert node_types(b'{"z": null}', "c.json") == ["json", "NoneType"]

    def test_xml_has_no_nonetype_nodes(self):
        """XML 的属性/文本都是字符串，只读规则只可能来自 JSON 的 null。"""
        assert "NoneType" not in node_types(XML_DOC)


class TestParserHelpers:
    """本卡新增的两个公开薄封装（M3-02 与 `apply_edits` 同源）。"""

    def test_collect_ns_prefixes_maps_uri_to_first_prefix(self):
        content = b'<r xmlns:a="urn:x"><b xmlns:a="urn:y"><c xmlns:d="urn:z"/></b></r>'
        assert parser.collect_ns_prefixes(content) == {"urn:x": "a", "urn:y": "a", "urn:z": "d"}

    def test_collect_ns_prefixes_is_the_same_mapping_as_the_private_one(self):
        # `apply_edits` 注册前缀与 `to_compact` 显示标签必须同源，不能各算各的。
        content = b'<a:r xmlns:a="urn:x"><a:c a:k="v"/></a:r>'
        assert parser.collect_ns_prefixes(content) == parser._collect_ns_prefixes(content)

    @pytest.mark.parametrize(
        "token",
        ["plain", "a/b", "k~y", "~0", "~1", "", "///", "~01", "~10", "中文/键~名"],
    )
    def test_pointer_unescape_inverts_pointer_escape(self, token: str):
        assert parser.pointer_unescape(parser._pointer_escape(token)) == token

    def test_pointer_unescape_follows_rfc6901_order(self):
        """`~01` 必须是 `~1`→`/` 先、`~0`→`~` 后：反序会得到 `/1` 之外的错值。"""
        assert parser.pointer_unescape("~01") == "~1"
        assert parser.pointer_unescape("~1") == "/"
        assert parser.pointer_unescape("~0") == "~"


class TestPurity:
    """分层红线（§3.3）+ 卡面禁令：`core` 不读写业务文件，新路径不调 `serialize_tree`。"""

    def test_apply_edits_never_touches_the_filesystem(self, tmp_path, monkeypatch):
        from app.core import reviewing

        def boom(*_args, **_kwargs):
            raise AssertionError("新路径不得调用 serialize_tree（旧函数 M4-07 才删）")

        monkeypatch.setattr(reviewing, "serialize_tree", boom)
        path = tmp_path / "cfg.xml"
        path.write_bytes(XML_DOC)
        before = sorted(item.name for item in tmp_path.iterdir())

        ok = apply_edits(XML_DOC, "xml", [("/R[1]/item[1]", "one", "element")])
        assert isinstance(ok, bytes) and ok != XML_DOC
        with pytest.raises(AppError) as excinfo:
            apply_edits(XML_DOC, "xml", [("/R[1]/mixed[1]/#text", "   ", "text")])
        assert excinfo.value.code == REVIEW_UNSAFE

        assert sorted(item.name for item in tmp_path.iterdir()) == before
        assert path.read_bytes() == XML_DOC, "写回由 storage 负责，core 不碰文件"
