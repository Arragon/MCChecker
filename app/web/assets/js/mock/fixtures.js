/*
 * [模板专用] 演示数据。结构与 data/profiles/<pid>/ 下各 JSON 文件的真实格式一致（规范 §5）。
 * scenario_a.xml 取自仓库 test.xml（脱敏样例）。
 */
(function (global) {
  "use strict";
  var MC = (global.MC = global.MC || {});

  var SCENARIO_A_V3 = `<?xml version="1.0" encoding="UTF-8"?>
<CONFIG_ROOT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" machineID="MACHINE_ID_01" softwareVersion="SOFTWARE_VER_GENERIC">
  <SCENARIO_BLOCK type="SCENARIO_TYPE_A">
    <header type="HEADER_TYPE_A">
      <scenario_name type="string">SCENARIO_NAME_GENERIC</scenario_name>
      <visible type="bool">true</visible>
      <manual type="bool">true</manual>
      <default_scenario_on_gui type="bool">true</default_scenario_on_gui>
      <enable_overrule_inputs type="bool">false</enable_overrule_inputs>
    </header>
    <input type="INPUT_CONFIG_A">
      <param_01 type="int32" description="参数描述占位符 A" default="1" min="0" incMin="true" max="255" incMax="true" editPrivilege="EDITABLE">1</param_01>
      <param_02 type="int32" description="参数描述占位符 B" default="0" min="0" incMin="true" max="255" incMax="true" editPrivilege="EDITABLE">12</param_02>
      <param_03 type="double" description="参数描述占位符 C" units="TIME_UNIT" default="5.00e+05" min="1.00e+02" incMin="true" max="9.90e+06" incMax="true" editPrivilege="EDITABLE" precisionLength="2">5.00e+05</param_03>
      <param_04 type="double" description="参数描述占位符 D" units="TIME_UNIT" default="5.00e+05" min="1.00e+04" incMin="true" max="3.00e+08" incMax="true" editPrivilege="EDITABLE" precisionLength="2">5.00e+05</param_04>
      <param_05 type="string" description="参数描述占位符 E" default="TIME_RANGE_GENERIC" editPrivilege="EDITABLE">0~30 min</param_05>
      <param_06 type="bool" description="参数描述占位符 F" default="true" editPrivilege="EDITABLE">true</param_06>
      <param_07 type="bool" description="参数描述占位符 G" default="true" editPrivilege="EDITABLE">true</param_07>
      <param_08 type="bool" description="参数描述占位符 H" default="true" editPrivilege="READ_ONLY">true</param_08>
      <param_09 type="bool" description="参数描述占位符 I" default="true" editPrivilege="EDITABLE">true</param_09>
      <param_10 type="double" description="参数描述占位符 J" units="ENERGY_UNIT" default="5.00e+00" min="1.00e+00" incMin="true" max="5.00e+01" incMax="true" editPrivilege="EDITABLE" precisionLength="2">6.50e+00</param_10>
      <param_11 type="double" description="参数描述占位符 K" units="PERCENT_UNIT" default="1.00e-01" min="0.00e+00" incMin="true" max="1.00e+00" incMax="false" editPrivilege="EDITABLE" precisionLength="2">1.00e-01</param_11>
      <param_12 type="double" description="参数描述占位符 L" units="TIME_UNIT" default="3.00e+02" min="4.00e+01" incMin="true" max="1.00e+03" incMax="true" editPrivilege="EDITABLE" precisionLength="2">3.20e+02</param_12>
      <param_13 type="double" description="参数描述占位符 M" units="TIME_UNIT" default="3.00e+02" min="1.00e+02" incMin="true" max="1.00e+03" incMax="true" editPrivilege="EDITABLE" precisionLength="2">3.00e+02</param_13>
      <param_14 type="double" description="参数描述占位符 N" units="TIME_UNIT" default="3.00e+02" min="1.00e+02" incMin="true" max="1.00e+03" incMax="true" editPrivilege="EDITABLE" precisionLength="2">3.00e+02</param_14>
      <param_15 type="bool" description="参数描述占位符 O" default="true" editPrivilege="EDITABLE">true</param_15>
      <param_16 type="bool" description="参数描述占位符 P" default="true" editPrivilege="EDITABLE">true</param_16>
      <param_25 type="bool" description="参数描述占位符 Y" default="false" editPrivilege="EDITABLE">false</param_25>
      <param_26 type="bool" description="参数描述占位符 Z" default="false" editPrivilege="EDITABLE">false</param_26>
    </input>
    <output type="OUTPUT_CONFIG_A">
      <channel id="1" enabled="true">CH_MAIN</channel>
      <channel id="2" enabled="false">CH_AUX</channel>
      <note>输出通道配置<sub>保留</sub></note>
    </output>
  </SCENARIO_BLOCK>
</CONFIG_ROOT>
`;

  function variant(src, pairs) {
    var out = src;
    pairs.forEach(function (p) { out = out.replace(p[0], p[1]); });
    return out;
  }

  var SCENARIO_A_V2 = variant(SCENARIO_A_V3, [
    [">12</param_02>", ">9</param_02>"],
    [">6.50e+00</param_10>", ">5.00e+00</param_10>"],
    [">3.20e+02</param_12>", ">3.00e+02</param_12>"],
  ]);

  var SCENARIO_A_V1 = variant(SCENARIO_A_V2, [
    [">9</param_02>", ">8</param_02>"],
    ['      <param_25 type="bool" description="参数描述占位符 Y" default="false" editPrivilege="EDITABLE">false</param_25>\n', ""],
    ['<channel id="2" enabled="false">CH_AUX</channel>', '<channel id="2" enabled="true">CH_AUX</channel>'],
  ]);

  var SCENARIO_B = variant(SCENARIO_A_V3, [
    ['machineID="MACHINE_ID_01"', 'machineID="MACHINE_ID_02"'],
    ['type="SCENARIO_TYPE_A"', 'type="SCENARIO_TYPE_B"'],
    [">12</param_02>", ">16</param_02>"],
    [">5.00e+05</param_03>", ">4.20e+05</param_03>"],
  ]);

  var DEVICE_JSON = JSON.stringify(
    {
      device: {
        name: "DL-200",
        firmware: "3.4.1",
        serial: "SN-2026-00731",
        laser: {
          pp: { m1: 0.82, m2: -0.0031, m3: 0.00002, n: 1.5 },
          rp: { m1: 4, m2: -0.05, n: 2 },
          width: { m1: 0.5, n: 0 },
        },
        limits: { max_power: 45.5, min_power: 0.5, interlock: true },
        channels: [
          { id: 1, label: "主通道", gain: 1.0 },
          { id: 2, label: "辅助通道", gain: 0.85 },
        ],
        maintainer: null,
      },
    },
    null,
    2
  );

  var DEVICE_JSON_V1 = DEVICE_JSON.replace('"firmware": "3.4.1"', '"firmware": "3.3.9"').replace('"max_power": 45.5', '"max_power": 42');

  var RECORD_1 = `<?xml version="1.0" encoding="UTF-8"?>
<changes file="scenario_a.xml">
  <change time="2026-10-08 09:12:00" user="li.na" param="param_02" from="8" to="9"/>
  <change time="2026-10-08 09:13:30" user="li.na" param="param_25" from="" to="false"/>
</changes>
`;
  var RECORD_2 = `<?xml version="1.0" encoding="UTF-8"?>
<changes file="scenario_a.xml">
  <change time="2026-10-08 09:12:00" user="li.na" param="param_02" from="8" to="9"/>
  <change time="2026-10-08 09:13:30" user="li.na" param="param_25" from="" to="false"/>
  <change time="2026-10-15 10:05:10" user="wang.qiang" param="param_02" from="9" to="12"/>
  <change time="2026-10-15 10:06:42" user="wang.qiang" param="param_10" from="5.00e+00" to="6.50e+00"/>
</changes>
`;

  /** 生成约 4 万节点的大文件，用于验证虚拟滚动性能。 */
  function largeXml() {
    var parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<PARAM_TABLE version="7">\n'];
    for (var g = 1; g <= 40; g++) {
      parts.push('  <group name="G' + g + '" module="MOD_' + ((g % 6) + 1) + '">\n');
      for (var k = 1; k <= 150; k++) {
        var v = ((g * 37 + k * 11) % 997) / 10;
        parts.push(
          '    <param_' + k + ' type="double" description="分组 ' + g + ' 参数 ' + k + '" default="' + v + '" min="0" incMin="true" max="100" incMax="true" editPrivilege="EDITABLE">' + v + "</param_" + k + ">\n"
        );
      }
      parts.push("  </group>\n");
    }
    parts.push("</PARAM_TABLE>\n");
    return parts.join("");
  }

  function build() {
    return {
      models: [
        { id: "default", name: "默认机型" },
        { id: "dl-200-line2", name: "DL-200 二号线" },
      ],
      people: [
        { ip: "10.12.3.21", name: "李娜", updated_at: "2026-09-20 10:00:00" },
        { ip: "10.12.3.22", name: "王强", updated_at: "2026-09-20 10:02:00" },
        { ip: "127.0.0.1", name: "张伟", updated_at: "2026-09-18 08:30:00" },
      ],
      profiles: {
        default: {
          files: {
            "scenario_a.xml": {
              current: { text: SCENARIO_A_V3, mtime: "2026-10-15 10:08:00" },
              archives: [
                { filename: "scenario_a_20261008.xml", text: SCENARIO_A_V2, mtime: "2026-10-15 10:08:00" },
                { filename: "scenario_a_20260928.xml", text: SCENARIO_A_V1, mtime: "2026-10-08 09:20:00" },
              ],
              records: [
                { filename: "record_20261015_101500.xml", text: RECORD_2, mtime: "2026-10-15 10:15:00" },
                { filename: "record_20261008_092000.xml", text: RECORD_1, mtime: "2026-10-08 09:20:00" },
              ],
            },
            "scenario_b.xml": { current: { text: SCENARIO_B, mtime: "2026-10-12 16:40:00" }, archives: [], records: [] },
            "device_profile.json": {
              current: { text: DEVICE_JSON, mtime: "2026-10-14 11:20:00" },
              archives: [{ filename: "device_profile_20261001.json", text: DEVICE_JSON_V1, mtime: "2026-10-14 11:20:00" }],
              records: [],
            },
            "large_params.xml": { current: { text: null, generate: largeXml, mtime: "2026-10-02 08:00:00" }, archives: [], records: [] },
          },
          mapping: [
            { name: "scenario_a.xml", url: "http://10.20.1.15/cfg/scenario_a.xml", record_url: "http://10.20.1.15/cfg/scenario_a.changes.xml" },
            { name: "scenario_b.xml", url: "http://10.20.1.15/cfg/scenario_b.xml", record_url: "" },
            { name: "device_profile.json", url: "http://10.20.1.16:8080/api/device/profile.json", record_url: "" },
          ],
          sourceStatus: {
            "scenario_a.xml": { status: "success", label: "已更新", at: "2026-10-15 10:08:00" },
            "scenario_b.xml": { status: "unchanged", label: "无变化", at: "2026-10-15 10:08:01" },
            "device_profile.json": { status: "download_failed", label: "下载失败", at: "2026-10-15 10:08:31", reason: "连接超时（30 秒）" },
          },
          favorites: [
            {
              path: "root/CONFIG_ROOT/SCENARIO_BLOCK/input/param_02",
              label: "param_02",
              value: "12",
              source_file: "scenario_a.xml",
              note: "脉冲计数上限",
              children: [],
              added_at: "2026-10-01 09:00:00",
            },
            {
              path: "root/CONFIG_ROOT/SCENARIO_BLOCK/input/param_10",
              label: "param_10",
              value: "6.50e+00",
              source_file: "scenario_a.xml",
              note: "能量设定，改动需审批",
              children: [],
              added_at: "2026-10-01 09:01:00",
            },
            {
              path: "root/CONFIG_ROOT/SCENARIO_BLOCK/header",
              label: "header",
              value: null,
              source_file: "scenario_a.xml",
              note: "",
              children: [
                { label: "@type", value: "HEADER_TYPE_A", path: "root/CONFIG_ROOT/SCENARIO_BLOCK/header@type", children: [] },
                { label: "scenario_name", value: "SCENARIO_NAME_GENERIC", path: "root/CONFIG_ROOT/SCENARIO_BLOCK/header/scenario_name", children: [] },
                { label: "visible", value: "true", path: "root/CONFIG_ROOT/SCENARIO_BLOCK/header/visible", children: [] },
                { label: "manual", value: "true", path: "root/CONFIG_ROOT/SCENARIO_BLOCK/header/manual", children: [] },
              ],
              added_at: "2026-10-02 14:00:00",
            },
            {
              path: "root.device.firmware",
              label: "firmware",
              value: "3.4.1",
              source_file: "device_profile.json",
              note: "固件版本",
              children: [],
              added_at: "2026-10-03 10:00:00",
            },
            {
              path: "root.device.limits.max_power",
              label: "max_power",
              value: "45.5",
              source_file: "device_profile.json",
              note: "",
              children: [],
              added_at: "2026-10-03 10:01:00",
            },
            {
              path: "root/LEGACY/timeout",
              label: "timeout",
              value: "30",
              source_file: "old_gateway.xml",
              note: "旧网关超时",
              children: [],
              added_at: "2026-08-01 10:00:00",
            },
          ],
          remarks: [
            remark("r1", "scenario_a.xml", "param_03", "5.00e+05", "6.00e+05", "李娜", "10.12.3.21", "2026-10-15 11:02:00"),
            remark("r2", "scenario_a.xml", "param_03", "5.00e+05", "5.50e+05", "王强", "10.12.3.22", "2026-10-15 13:40:00"),
            remark("r3", "scenario_a.xml", "param_10", "6.50e+00", "7.00e+00", "李娜", "10.12.3.21", "2026-10-15 11:05:00"),
            remark("r4", "scenario_a.xml", "param_12", "3.00e+02", "3.50e+02", "10.12.3.40", "10.12.3.40", "2026-10-09 15:00:00"),
            legacyRemark("r5", "scenario_b.xml", "root/CONFIG_ROOT/SCENARIO_BLOCK/header/manual", "manual", "true", "false", "王强", "10.12.3.22", "2026-10-11 09:30:00"),
          ],
          bindings: [
            {
              group_name: "PP 能量系数",
              variables: [
                { file: "device_profile.json", path: "device_profile.json/device/laser/pp/m1" },
                { file: "scenario_a.xml", path: "scenario_a.xml/CONFIG_ROOT/SCENARIO_BLOCK/input/param_10" },
              ],
            },
            {
              group_name: "脉冲计数",
              variables: [
                { file: "scenario_a.xml", path: "scenario_a.xml/CONFIG_ROOT/SCENARIO_BLOCK/input/param_02" },
                { file: "scenario_b.xml", path: "scenario_b.xml/CONFIG_ROOT/SCENARIO_BLOCK/input/param_02" },
              ],
            },
          ],
          tools: [
            { name: "工单系统", description: "内网变更申请与审批", url: "http://10.20.0.8/tickets", added_at: "2026-09-01 10:00:00" },
            { name: "设备监控看板", description: "产线实时状态", url: "http://10.20.0.9:3000/d/line", added_at: "2026-09-01 10:05:00" },
          ],
          schedule: { enabled: true, interval_hours: 24, last_run: { at: "2026-10-15 10:08:00", success: 1, unchanged: 1, failed: 1, skipped: 0 } },
          admins: [
            { name: "王强", updated_at: "2026-09-20 10:05:00" },
            { name: "张伟", updated_at: "2026-09-18 08:31:00" },
          ],
          dl: {
            editing: false,
            items: {
              pp_energy: {
                name: "计算PP能量",
                trans_coeff: 0.92,
                power_conv_coeff: 1.05,
                coeffs: { k1: 0.82, k2: -0.0031, k3: 0.00002, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 1.5 },
                binding: { source_file: "device_profile.json", variable_path: "root.device.laser.pp", field_map: { m1: "k1", m2: "k2", m3: "k3", n: "b" } },
                x_min: 0,
                x_max: 120,
                y_min: null,
                y_max: null,
              },
              rp_energy: {
                name: "计算RP能量",
                coeffs: { k1: 4, k2: -0.05, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 2 },
                binding: { source_file: "", variable_path: "", field_map: {} },
                x_min: 0,
                x_max: 80,
                y_min: null,
                y_max: null,
              },
              rp_width: {
                name: "计算RP脉宽",
                coeffs: { k1: 0.5, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 0 },
                binding: { source_file: "", variable_path: "", field_map: {} },
                x_min: 0,
                x_max: 100,
                y_min: null,
                y_max: null,
                aom_sum: 100,
                aom_width: {
                  coeffs: { k1: 0.8, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 10 },
                  binding: { source_file: "", variable_path: "", field_map: {} },
                  x_min: 0,
                  x_max: 100,
                  y_min: null,
                  y_max: null,
                },
              },
              ppz: {
                name: "计算PPz",
                coeffs: { k1: 0.2, k2: 0, k3: 0, k4: 0, k5: 0, k6: 0, k7: 0, k8: 0, b: 1 },
                binding: { source_file: "", variable_path: "", field_map: {} },
                x_min: 0,
                x_max: 200,
                y_min: null,
                y_max: null,
              },
            },
          },
        },
        "dl-200-line2": {
          files: {
            "scenario_a.xml": { current: { text: SCENARIO_B, mtime: "2026-10-10 09:00:00" }, archives: [], records: [] },
          },
          mapping: [{ name: "scenario_a.xml", url: "http://10.20.2.15/cfg/scenario_a.xml", record_url: "" }],
          sourceStatus: {},
          favorites: [],
          remarks: [],
          bindings: [],
          tools: [],
          schedule: { enabled: false, interval_hours: 24, last_run: null },
          admins: [{ name: "张伟", updated_at: "2026-09-18 08:31:00" }],
          dl: null,
        },
      },
    };
  }

  function remark(id, file, param, orig, proposed, name, ip, at) {
    return {
      id: id,
      source_file: file,
      node_key: "",
      node_path: "root/CONFIG_ROOT/SCENARIO_BLOCK/input/" + param,
      node_label: param,
      original_value: orig,
      proposed_value: proposed,
      node_type: "element",
      tree_type: "xml",
      actor_ip: ip,
      actor_name: name === ip ? "" : name,
      actor_display: name,
      status: "pending",
      reviewed_by: "",
      reviewer_ip: "",
      reviewed_at: "",
      generated_file: "",
      created_at: at,
      locator: "/CONFIG_ROOT[1]/SCENARIO_BLOCK[1]/input[1]/" + param + "[1]",
      source_sha256: "",
      schema_version: 2,
    };
  }

  function legacyRemark(id, file, path, label, orig, proposed, name, ip, at) {
    return {
      id: id,
      source_file: file,
      node_key: "0.0.0.3",
      node_path: path,
      node_label: label,
      original_value: orig,
      proposed_value: proposed,
      node_type: "element",
      tree_type: "xml",
      actor_ip: ip,
      actor_name: name,
      actor_display: name,
      status: "pending",
      reviewed_by: "",
      reviewer_ip: "",
      reviewed_at: "",
      generated_file: "",
      created_at: at,
    };
  }

  MC.mockFixtures = { build: build };
})(window);
