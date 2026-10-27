"""DL 文案和文件页分段的快速核对。

不启动 Chrome、不走界面子代理。标签或分段被改掉时，这条用例在一次 pytest 里失败。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DL = (ROOT / "app/web/assets/js/views/dltool.js").read_text(encoding="utf-8")
FILEVIEW = (ROOT / "app/web/assets/js/views/fileview.js").read_text(encoding="utf-8")


def test_dl_item_and_axis_labels():
    for label in ("PP 能量", "RP 能量", "RP 脉宽", "PPz", "DL 快捷计算", "保存系数", "系数历史"):
        assert label in DL
    for axis in (
        "电动波片角度",
        "PP@PF能量",
        "RP能量",
        "TFP2角度(SMTM页面)",
        "RP脉宽",
        "AOM2 delay(SMIM1页面)",
        "AOM2 width(SMIM1页面)",
        "AOM2 delay 模式",
        "AOM2 width 模式",
        "调焦镜发散角",
        "BTS_ppz",
    ):
        assert axis in DL, axis
    assert "SMSM1" not in DL


def test_file_view_segments():
    assert '{ value: "tree", label: "结构"' in FILEVIEW
    assert '{ value: "history", label: "历史版本"' in FILEVIEW
    assert '{ value: "compare", label: "对比"' in FILEVIEW
    assert '{ value: "records", label: "修改记录"' in FILEVIEW
    temp_line = next(line for line in FILEVIEW.splitlines() if line.strip().startswith("? [{ value: \"tree\""))
    assert 'label: "结构"' in temp_line
    assert 'label: "对比"' in temp_line
    assert "历史版本" not in temp_line
    assert "修改记录" not in temp_line


def test_rp_width_modes_have_their_own_parameter_set():
    """RP 脉宽两种模式各用一套参数：页面必须按模式取 aom_width，而不是共用顶层系数。"""
    assert "aom_width" in DL
    assert "activeSet" in DL
    assert "固定和" in DL
