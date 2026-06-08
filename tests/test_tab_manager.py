from app.core import tab_manager


class TestTabManager:
    def test_enforce_tab_limit_removes_oldest_non_active(self):
        tabs = [{"name": f"t{i}", "opened_at": i} for i in range(6)]
        removed = tab_manager.enforce_tab_limit(tabs, active_name="t5", limit=3)
        assert len(tabs) == 3
        assert "t5" in {t["name"] for t in tabs}
        assert removed == ["t0", "t1", "t2"]

    def test_close_tab_adjacent_when_closing_active(self):
        tabs = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        tabs, active = tab_manager.close_tab_adjacent(tabs, active_name="b", close_name="b")
        assert [t["name"] for t in tabs] == ["a", "c"]
        assert active in ("a", "c")

    def test_close_tab_adjacent_when_closing_non_active(self):
        tabs = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        tabs, active = tab_manager.close_tab_adjacent(tabs, active_name="b", close_name="a")
        assert [t["name"] for t in tabs] == ["b", "c"]
        assert active == "b"

