"""链接导入的文件名要留下后缀。"""

from app.services.upload import _name_from_url


def test_path_suffix_survives_query_string():
    assert _name_from_url("http://10.20.1.15/cfg/config.json?token=1") == "config.json"


def test_query_filename_is_used_when_the_path_has_no_suffix():
    assert _name_from_url("http://10.20.1.15/download?file=MachineConstant.xml") == "MachineConstant.xml"


def test_percent_encoded_suffix_is_decoded():
    assert _name_from_url("http://10.20.1.15/cfg/My%20File.json") == "My File.json"


def test_config_suffix_wins_over_a_bare_path_segment():
    assert _name_from_url("http://10.20.1.15/download?name=cfg.xml") == "cfg.xml"
