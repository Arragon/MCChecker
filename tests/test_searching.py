from app.core import searching


class TestSearching:
    def test_filter_tree_keeps_ancestors(self):
        tree = {
            "id": "root",
            "label": "root",
            "value": None,
            "children": [
                {
                    "id": "root.a",
                    "label": "a",
                    "value": None,
                    "children": [
                        {"id": "root.a.b", "label": "b", "value": "123", "children": [], "attrs": {}},
                    ],
                    "attrs": {},
                },
                {"id": "root.x", "label": "x", "value": "zzz", "children": [], "attrs": {}},
            ],
            "attrs": {},
        }
        note_map = {"root.a.b": "hello"}
        filtered = searching.filter_tree(tree, "123", note_map)
        assert filtered is not None
        assert len(filtered["children"]) == 1
        assert filtered["children"][0]["id"] == "root.a"
        assert filtered["children"][0]["children"][0]["id"] == "root.a.b"

    def test_filter_tree_matches_note(self):
        tree = {
            "id": "root",
            "label": "root",
            "value": None,
            "children": [
                {"id": "p", "label": "p", "value": "1", "children": [], "attrs": {}},
            ],
            "attrs": {},
        }
        note_map = {"p": "special-note"}
        filtered = searching.filter_tree(tree, "special", note_map)
        assert filtered is not None
        assert searching.count_value_nodes(filtered) == 1

