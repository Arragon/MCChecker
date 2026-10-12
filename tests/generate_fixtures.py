#!/usr/bin/env python3
"""生成多工况测试文件

生成各种边界场景的 JSON 和 XML 测试文件，用于解析器、对比引擎等模块的测试。
"""

import json
import os
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES_DIR.mkdir(exist_ok=True)


def gen_json_files():
    """生成各种场景的 JSON 测试文件"""

    # 1. 简单扁平 JSON
    simple = {
        "host": "localhost",
        "port": 8080,
        "debug": True,
        "timeout": 30,
        "version": "1.0.0"
    }
    (FIXTURES_DIR / "simple.json").write_text(
        json.dumps(simple, ensure_ascii=False, indent=2)
    )

    # 2. 深层嵌套 JSON (10 层)
    deep = {"level1": {"level2": {"level3": {"level4": {"level5": {
        "level6": {"level7": {"level8": {"level9": {"level10": "deep_value"}}}}}}}}}}
    (FIXTURES_DIR / "deep_nested.json").write_text(
        json.dumps(deep, ensure_ascii=False, indent=2)
    )

    # 3. 宽 JSON (100 个键)
    wide = {f"key_{i:03d}": f"value_{i}" for i in range(100)}
    (FIXTURES_DIR / "wide.json").write_text(
        json.dumps(wide, ensure_ascii=False, indent=2)
    )

    # 4. 数组密集 JSON
    arrays = {
        "numbers": list(range(100)),
        "strings": [f"item_{i}" for i in range(50)],
        "objects": [{"id": i, "name": f"obj_{i}"} for i in range(20)],
        "nested_arrays": [[i * j for j in range(10)] for i in range(10)],
        "mixed": [1, "two", None, True, {"key": "value"}, [1, 2, 3]]
    }
    (FIXTURES_DIR / "array_heavy.json").write_text(
        json.dumps(arrays, ensure_ascii=False, indent=2)
    )

    # 5. 混合类型 JSON
    mixed_types = {
        "string": "text",
        "integer": 42,
        "float": 3.14159,
        "boolean_true": True,
        "boolean_false": False,
        "null_value": None,
        "empty_string": "",
        "empty_object": {},
        "empty_array": [],
        "negative": -100,
        "zero": 0,
        "scientific": 1.23e-4,
        "unicode": "中文 日本語 한국어 🎉",
        "special_chars": "line1\nline2\ttab\\slash\"quote"
    }
    (FIXTURES_DIR / "mixed_types.json").write_text(
        json.dumps(mixed_types, ensure_ascii=False, indent=2)
    )

    # 6. 特殊字符键 JSON
    special_keys = {
        "key/with/slashes": "value1",
        "key~with~tildes": "value2",
        "key with spaces": "value3",
        "key.with.dots": "value4",
        "key-with-dashes": "value5",
        "key_with_underscores": "value6",
        "123numeric": "value7",
        "": "empty_key"
    }
    (FIXTURES_DIR / "special_keys.json").write_text(
        json.dumps(special_keys, ensure_ascii=False, indent=2)
    )

    # 7. 空结构 JSON
    empty_structures = {
        "empty_object": {},
        "empty_array": [],
        "nested_empty": {"empty_obj": {}, "empty_arr": []},
        "null_field": None
    }
    (FIXTURES_DIR / "empty_structures.json").write_text(
        json.dumps(empty_structures, ensure_ascii=False, indent=2)
    )

    # 8. 大 JSON 文件 (>200KB)
    large_data = {
        f"section_{i}": {
            f"item_{j}": {
                "id": i * 100 + j,
                "name": f"Item {i}-{j}",
                "description": "x" * 100,
                "tags": [f"tag_{k}" for k in range(5)],
                "metadata": {
                    "created": "2024-01-01",
                    "updated": "2024-12-31",
                    "version": i + j
                }
            }
            for j in range(50)
        }
        for i in range(100)
    }
    (FIXTURES_DIR / "large.json").write_text(
        json.dumps(large_data, ensure_ascii=False, indent=2)
    )

    # 9. 配置场景 JSON
    config = {
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "workers": 4,
            "ssl": {
                "enabled": True,
                "cert": "/path/to/cert.pem",
                "key": "/path/to/key.pem"
            }
        },
        "database": {
            "host": "localhost",
            "port": 5432,
            "name": "mydb",
            "user": "admin",
            "password": "secret",
            "pool_size": 10
        },
        "logging": {
            "level": "INFO",
            "format": "json",
            "outputs": ["console", "file"],
            "file": {
                "path": "/var/log/app.log",
                "max_size": "100MB",
                "rotation": "daily"
            }
        },
        "features": {
            "enable_cache": True,
            "cache_ttl": 3600,
            "rate_limit": 1000
        }
    }
    (FIXTURES_DIR / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2)
    )

    # 10. 配置场景 JSON v2 (用于对比测试)
    config_v2 = {
        "server": {
            "host": "0.0.0.0",
            "port": 9090,  # 修改
            "workers": 8,  # 修改
            "ssl": {
                "enabled": False,  # 修改
                "cert": "/path/to/cert.pem",
                "key": "/path/to/key.pem"
            }
        },
        "database": {
            "host": "db.example.com",  # 修改
            "port": 5432,
            "name": "mydb",
            "user": "admin",
            "password": "new_secret",  # 修改
            "pool_size": 20  # 修改
        },
        "logging": {
            "level": "DEBUG",  # 修改
            "format": "json",
            "outputs": ["console", "file", "syslog"],  # 修改
            "file": {
                "path": "/var/log/app.log",
                "max_size": "200MB",  # 修改
                "rotation": "hourly"  # 修改
            }
        },
        "features": {
            "enable_cache": True,
            "cache_ttl": 7200,  # 修改
            "rate_limit": 2000,  # 修改
            "new_feature": True  # 新增
        },
        "monitoring": {  # 新增
            "enabled": True,
            "endpoint": "/metrics"
        }
    }
    (FIXTURES_DIR / "config_v2.json").write_text(
        json.dumps(config_v2, ensure_ascii=False, indent=2)
    )

    print(f"✓ 生成 {10} 个 JSON 测试文件")


def gen_xml_files():
    """生成各种场景的 XML 测试文件"""

    # 1. 简单扁平 XML
    simple_xml = """<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <host>localhost</host>
    <port>8080</port>
    <debug>true</debug>
    <timeout>30</timeout>
    <version>1.0.0</version>
</configuration>
"""
    (FIXTURES_DIR / "simple.xml").write_text(simple_xml)

    # 2. 深层嵌套 XML (10 层)
    deep_xml = """<?xml version="1.0" encoding="UTF-8"?>
<level1>
    <level2>
        <level3>
            <level4>
                <level5>
                    <level6>
                        <level7>
                            <level8>
                                <level9>
                                    <level10>deep_value</level10>
                                </level9>
                            </level8>
                        </level7>
                    </level6>
                </level5>
            </level4>
        </level3>
    </level2>
</level1>
"""
    (FIXTURES_DIR / "deep_nested.xml").write_text(deep_xml)

    # 3. 多属性 XML
    attrs = " ".join([f'attr{i}="value{i}"' for i in range(20)])
    many_attrs_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<root>
    <item {attrs}>
        <child>text</child>
    </item>
</root>
"""
    (FIXTURES_DIR / "many_attributes.xml").write_text(many_attrs_xml)

    # 4. 命名空间 XML
    namespace_xml = """<?xml version="1.0" encoding="UTF-8"?>
<root xmlns:ns1="http://example.com/ns1" xmlns:ns2="http://example.com/ns2">
    <ns1:element1>
        <ns2:element2>namespaced_value</ns2:element2>
    </ns1:element1>
    <ns1:config ns2:attribute="value">content</ns1:config>
</root>
"""
    (FIXTURES_DIR / "namespaced.xml").write_text(namespace_xml)

    # 5. 未绑定命名空间前缀 (需要自动修复)
    unbound_ns_xml = """<?xml version="1.0" encoding="UTF-8"?>
<root>
    <ns:item>value</ns:item>
    <other>normal</other>
</root>
"""
    (FIXTURES_DIR / "unbound_namespace.xml").write_text(unbound_ns_xml)

    # 6. 重复兄弟元素 XML
    siblings_xml = """<?xml version="1.0" encoding="UTF-8"?>
<root>
    <item id="1">First</item>
    <item id="2">Second</item>
    <item id="3">Third</item>
    <item id="4">Fourth</item>
    <item id="5">Fifth</item>
    <group>
        <entry>A</entry>
        <entry>B</entry>
        <entry>C</entry>
    </group>
</root>
"""
    (FIXTURES_DIR / "repeated_siblings.xml").write_text(siblings_xml)

    # 7. 混合内容 XML (文本 + 元素)
    mixed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<document>
    <paragraph>
        This is <bold>bold</bold> and <italic>italic</italic> text.
    </paragraph>
    <list>
        <item>Item <emphasis>one</emphasis></item>
        <item>Item <emphasis>two</emphasis></item>
    </list>
</document>
"""
    (FIXTURES_DIR / "mixed_content.xml").write_text(mixed_xml)

    # 8. 空元素 XML
    empty_xml = """<?xml version="1.0" encoding="UTF-8"?>
<root>
    <empty1/>
    <empty2></empty2>
    <with_attrs attr1="val1" attr2="val2"/>
    <nested>
        <empty_child/>
    </nested>
</root>
"""
    (FIXTURES_DIR / "empty_elements.xml").write_text(empty_xml)

    # 9. 大 XML 文件 (>200KB)
    large_parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<root>']
    for i in range(500):
        large_parts.append(f'  <section id="{i}">')
        for j in range(20):
            large_parts.append(f'    <item id="{i}_{j}">{"x" * 50}</item>')
        large_parts.append('  </section>')
    large_parts.append('</root>')
    (FIXTURES_DIR / "large.xml").write_text("\n".join(large_parts))

    # 10. 配置场景 XML
    config_xml = """<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <server host="0.0.0.0" port="8080" workers="4">
        <ssl enabled="true">
            <cert>/path/to/cert.pem</cert>
            <key>/path/to/key.pem</key>
        </ssl>
    </server>
    <database>
        <host>localhost</host>
        <port>5432</port>
        <name>mydb</name>
        <user>admin</user>
        <password>secret</password>
        <pool_size>10</pool_size>
    </database>
    <logging level="INFO" format="json">
        <outputs>
            <output>console</output>
            <output>file</output>
        </outputs>
        <file path="/var/log/app.log" max_size="100MB" rotation="daily"/>
    </logging>
    <features>
        <enable_cache>true</enable_cache>
        <cache_ttl>3600</cache_ttl>
        <rate_limit>1000</rate_limit>
    </features>
</configuration>
"""
    (FIXTURES_DIR / "config.xml").write_text(config_xml)

    # 11. 配置场景 XML v2 (用于对比测试)
    config_v2_xml = """<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <server host="0.0.0.0" port="9090" workers="8">
        <ssl enabled="false">
            <cert>/path/to/cert.pem</cert>
            <key>/path/to/key.pem</key>
        </ssl>
    </server>
    <database>
        <host>db.example.com</host>
        <port>5432</port>
        <name>mydb</name>
        <user>admin</user>
        <password>new_secret</password>
        <pool_size>20</pool_size>
    </database>
    <logging level="DEBUG" format="json">
        <outputs>
            <output>console</output>
            <output>file</output>
            <output>syslog</output>
        </outputs>
        <file path="/var/log/app.log" max_size="200MB" rotation="hourly"/>
    </logging>
    <features>
        <enable_cache>true</enable_cache>
        <cache_ttl>7200</cache_ttl>
        <rate_limit>2000</rate_limit>
        <new_feature>true</new_feature>
    </features>
    <monitoring enabled="true">
        <endpoint>/metrics</endpoint>
    </monitoring>
</configuration>
"""
    (FIXTURES_DIR / "config_v2.xml").write_text(config_v2_xml)

    # 12. Unicode 内容 XML
    unicode_xml = """<?xml version="1.0" encoding="UTF-8"?>
<root>
    <chinese>中文测试</chinese>
    <japanese>日本語テスト</japanese>
    <korean>한국어 테스트</korean>
    <emoji>🎉🚀💡</emoji>
    <mixed>Hello 世界 🌍</mixed>
</root>
"""
    (FIXTURES_DIR / "unicode.xml").write_text(unicode_xml)

    print(f"✓ 生成 {12} 个 XML 测试文件")


def main():
    print(f"生成测试文件到: {FIXTURES_DIR}\n")
    gen_json_files()
    gen_xml_files()

    # 统计文件大小
    print("\n文件大小统计:")
    for f in sorted(FIXTURES_DIR.iterdir()):
        size = f.stat().st_size
        if size > 1024 * 1024:
            size_str = f"{size / 1024 / 1024:.2f} MB"
        elif size > 1024:
            size_str = f"{size / 1024:.2f} KB"
        else:
            size_str = f"{size} B"
        print(f"  {f.name:30s} {size_str:>10s}")


if __name__ == "__main__":
    main()
