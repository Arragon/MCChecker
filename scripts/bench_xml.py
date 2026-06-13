import os
import sys
import time
import tracemalloc
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core import parser, parse_cache, storage


def _make_large_xml(line_count: int = 3200) -> str:
    header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    header += '<CONFIG_ROOT machineID="MACHINE_ID_01" softwareVersion="SOFTWARE_VER_GENERIC">\n'
    header += '  <SCENARIO_BLOCK type="SCENARIO_TYPE_A">\n'
    header += '    <header type="HEADER_TYPE_A">\n'
    header += '      <scenario_name type="string">SCENARIO_NAME_GENERIC</scenario_name>\n'
    header += "    </header>\n"
    header += '    <input type="INPUT_CONFIG_A">\n'

    body_lines = []
    for i in range(1, line_count + 1):
        idx = f"{i:04d}"
        body_lines.append(
            f'      <param_{idx} type="int32" description="desc {idx}" default="1" min="0" incMin="true" max="255" incMax="true" editPrivilege="EDITABLE">{i % 10}</param_{idx}>'
        )

    footer = "    </input>\n"
    footer += "  </SCENARIO_BLOCK>\n"
    footer += "</CONFIG_ROOT>\n"
    return header + "\n".join(body_lines) + "\n" + footer


def _parse_xml_dom_bytes(content: bytes, filename: str) -> dict:
    text = content.decode("utf-8-sig")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        if "unbound prefix" not in str(e):
            raise
        text = parser._preprocess_xml_namespaces(text)
        root = ET.fromstring(text)
    return {
        "id": "root",
        "label": filename,
        "value": None,
        "children": [parser._xml_node_to_tree(root, "root")],
        "attrs": {"type": "xml"},
    }


def _measure(fn):
    tracemalloc.start()
    t0 = time.perf_counter()
    tree = fn()
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dt, peak, tree


def main():
    storage.ensure_profile_layout()
    storage._ensure_dirs()

    xml_text = _make_large_xml(3200)
    with tempfile.TemporaryDirectory() as td:
        file_path = os.path.join(td, "bench_large.xml")
        with open(file_path, "wb") as f:
            f.write(xml_text.encode("utf-8"))

        def _dom_full():
            with open(file_path, "rb") as f:
                content = f.read()
            return _parse_xml_dom_bytes(content, "bench_large.xml")

        dom_t, dom_peak, dom_tree = _measure(_dom_full)
        stream_t, stream_peak, stream_tree = _measure(lambda: parser.parse_path(file_path, "bench_large.xml"))

        node_count = len(parser.flatten_tree(stream_tree))

        cache_key_path = file_path
        parse_cache.invalidate(cache_key_path)
        t0 = time.perf_counter()
        parse_cache.save_tree(cache_key_path, stream_tree)
        save_dt = time.perf_counter() - t0
        t0 = time.perf_counter()
        loaded = parse_cache.load_tree(cache_key_path)
        load_dt = time.perf_counter() - t0
        _, cache_peak, _ = _measure(lambda: parse_cache.load_tree(cache_key_path) or {})

        print("XML 行数:", xml_text.count("\n") + 1)
        print("节点数:", node_count)
        print("DOM 解析耗时(s):", round(dom_t, 4))
        print("流式解析耗时(s):", round(stream_t, 4))
        print("耗时降低比例:", round((dom_t - stream_t) / dom_t * 100, 2) if dom_t else 0)
        print("DOM 峰值内存(bytes):", dom_peak)
        print("流式峰值内存(bytes):", stream_peak)
        print("内存占用比例:", round(stream_peak / dom_peak * 100, 2) if dom_peak else 0)
        print("缓存写入耗时(s):", round(save_dt, 4))
        print("缓存读取耗时(s):", round(load_dt, 4))
        print("缓存读取峰值内存(bytes):", cache_peak)
        print("缓存读取加速比例:", round((dom_t - load_dt) / dom_t * 100, 2) if dom_t else 0)
        print("缓存有效:", bool(loaded))

        test_xml = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test.xml")
        if os.path.exists(test_xml):
            t = parser.parse_path(test_xml, "test.xml")
            values = parser.get_all_values(t)
            print("test.xml 解析成功:", "param_01" in "".join(values.keys()))


if __name__ == "__main__":
    main()
