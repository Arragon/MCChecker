"""更新源回读：单个 XML/JSON，或压缩包按正则关键词切削后加入文件列表。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.core.bundle import expand_update_payload
from app.core.errors import AppError


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, body in members:
            archive.writestr(name, body)
    return buf.getvalue()


XML = b"<R><a>1</a></R>"
OTHER = b'{"kept": true}'


def test_keyword_trims_the_matched_span_and_keeps_the_extension():
    packed = _zip(
        [
            ("cfg/Default_MachineConstant_basics.xml", XML),
            ("notes.txt", b"skip"),
            ("Foo_Other_bar.json", OTHER),
        ]
    )
    pieces = expand_update_payload(
        packed,
        keyword="MachineConstant",
        source_name="pack.xml",
        remote_name="pack.zip",
        max_bytes=1024 * 1024,
    )
    assert pieces == [("MachineConstant.xml", XML)]


def test_archive_without_keyword_keeps_basenames():
    packed = _zip(
        [
            ("dir/Default_MachineConstant_basics.xml", XML),
            ("Foo.json", OTHER),
        ]
    )
    names = [
        name
        for name, _body in expand_update_payload(
            packed,
            keyword="",
            source_name="pack.xml",
            remote_name="pack.zip",
            max_bytes=1024 * 1024,
        )
    ]
    assert names == ["Default_MachineConstant_basics.xml", "Foo.json"]


def test_direct_file_uses_the_source_name_when_there_is_no_keyword():
    pieces = expand_update_payload(
        XML,
        keyword="",
        source_name="pack.xml",
        remote_name="Default_MachineConstant_basics.xml",
        max_bytes=1024 * 1024,
    )
    assert pieces == [("pack.xml", XML)]


def test_direct_file_is_trimmed_from_the_url_name():
    pieces = expand_update_payload(
        XML,
        keyword="MachineConstant",
        source_name="pack.xml",
        remote_name="Default_MachineConstant_basics.xml",
        max_bytes=1024 * 1024,
    )
    assert pieces == [("MachineConstant.xml", XML)]


def test_keyword_with_no_match_is_rejected():
    with pytest.raises(AppError, match="没有匹配"):
        expand_update_payload(
            _zip([("Other.xml", XML)]),
            keyword="MachineConstant",
            source_name="pack.xml",
            remote_name="pack.zip",
            max_bytes=1024 * 1024,
        )


def test_update_source_saves_the_trimmed_member(monkeypatch):
    from app.core import device_models, storage
    from app.services import updates

    pid = device_models.add_model("压缩包机型")
    packed = _zip(
        [
            ("dir/Default_MachineConstant_basics.xml", XML),
            ("Foo.json", OTHER),
        ]
    )
    monkeypatch.setattr(updates.downloader, "fetch", lambda url, policy: packed)
    with storage.use_profile(pid):
        storage.save_config_mapping(
            [
                {
                    "name": "pack.xml",
                    "url": "http://example.invalid/bundle.zip",
                    "record_url": "",
                    "keyword": "MachineConstant",
                }
            ]
        )

    result = updates.update_source(pid, "pack.xml", "deployer")

    assert result["status"] == "success", result
    assert "MachineConstant.xml" in result["reason"]
    with storage.use_profile(pid):
        names = storage.list_config_files()
        saved = open(storage.get_config_path("MachineConstant.xml"), "rb").read()
    assert names == ["MachineConstant.xml"]
    assert saved == XML
    json.loads(OTHER)
