import os

import pytest


class TestFavoritesLive:
    def test_update_missing_and_restore(self):
        from app.core import storage
        from app.core.favorites_live import MISSING_TEXT, resolve_overview_favorites

        content_v1 = b'{"obj":{"a":1,"b":2},"x":0}'
        storage.save_config_file("test.json", content_v1)

        favorites = [
            {
                "path": "root.x",
                "label": "x",
                "value": "0",
                "children": [],
                "source_file": "test.json",
                "note": "",
            },
            {
                "path": "root.obj",
                "label": "obj",
                "value": None,
                "children": [
                    {"label": "a", "value": "1", "path": "root.obj.a", "children": []},
                    {"label": "b", "value": "2", "path": "root.obj.b", "children": []},
                ],
                "source_file": "test.json",
                "note": "",
            },
        ]

        active, deleted = resolve_overview_favorites(favorites)
        assert "test.json" in active
        assert deleted == {}
        leaf = [f for f in active["test.json"] if f["path"] == "root.x"][0]
        assert leaf["value"] == "0"
        parent = [f for f in active["test.json"] if f["path"] == "root.obj"][0]
        assert parent.get("_missing") is False
        child_a = [c for c in parent["children"] if c["path"] == "root.obj.a"][0]
        child_b = [c for c in parent["children"] if c["path"] == "root.obj.b"][0]
        assert child_a["value"] == "1"
        assert child_b["value"] == "2"

        content_v2 = b'{"obj":{"a":1}}'
        storage.save_config_file("test.json", content_v2)

        active, deleted = resolve_overview_favorites(favorites)
        leaf = [f for f in active["test.json"] if f["path"] == "root.x"][0]
        assert leaf.get("_missing") is True
        assert leaf["value"] == MISSING_TEXT
        parent = [f for f in active["test.json"] if f["path"] == "root.obj"][0]
        child_a = [c for c in parent["children"] if c["path"] == "root.obj.a"][0]
        child_b = [c for c in parent["children"] if c["path"] == "root.obj.b"][0]
        assert child_a["value"] == "1"
        assert child_b["value"] == MISSING_TEXT

        os.remove(storage.get_config_path("test.json"))
        active, deleted = resolve_overview_favorites(favorites)
        assert active == {}
        assert "test.json" in deleted
        assert len(deleted["test.json"]) == 2

        content_v3 = b'{"obj":{"a":9,"b":10},"x":11}'
        storage.save_config_file("test.json", content_v3)
        active, deleted = resolve_overview_favorites(favorites)
        assert deleted == {}
        leaf = [f for f in active["test.json"] if f["path"] == "root.x"][0]
        assert leaf["value"] == "11"
        parent = [f for f in active["test.json"] if f["path"] == "root.obj"][0]
        child_a = [c for c in parent["children"] if c["path"] == "root.obj.a"][0]
        child_b = [c for c in parent["children"] if c["path"] == "root.obj.b"][0]
        assert child_a["value"] == "9"
        assert child_b["value"] == "10"

