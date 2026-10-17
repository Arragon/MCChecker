"""修订号计数（§7.3；M2-01）。"""

from __future__ import annotations

import threading
import time

import pytest

from app.services import revision


class TestSnapshot:
    def test_fresh_profile_starts_at_one(self):
        revision.reset()
        snap = revision.snapshot("pA")
        assert snap["rev"] == "1.1.1.1.1"
        assert set(snap["parts"]) == set(revision.PARTS)
        assert all(value == 1 for value in snap["parts"].values())

    def test_rev_string_order_matches_parts(self):
        revision.reset()
        revision.bump("pA", "favorites")
        revision.bump("pA", "favorites")
        snap = revision.snapshot("pA")
        assert snap["rev"] == ".".join(str(snap["parts"][part]) for part in revision.PARTS)

    def test_snapshot_returns_a_copy(self):
        revision.reset()
        snap = revision.snapshot("pY")
        snap["parts"]["files"] = 999
        assert revision.snapshot("pY")["parts"]["files"] == 1


class TestBump:
    def test_only_the_named_part_moves(self):
        revision.reset()
        revision.bump("pA", "favorites")
        revision.bump("pA", "favorites")
        revision.bump("pB", "files")
        a = revision.snapshot("pA")
        b = revision.snapshot("pB")
        assert a["parts"]["favorites"] == 3
        assert a["parts"]["files"] == 1
        assert a["rev"] == "1.3.1.1.1"
        assert b["parts"]["files"] == 2
        assert b["parts"]["favorites"] == 1

    def test_profiles_are_isolated(self):
        revision.reset()
        revision.bump("pA", "remarks")
        assert revision.snapshot("pA")["parts"]["remarks"] == 2
        assert revision.snapshot("pC")["parts"]["remarks"] == 1

    def test_unknown_part_is_a_coding_error(self):
        with pytest.raises(ValueError):
            revision.bump("pA", "nonsense")


class TestConcurrency:
    def test_parallel_bumps_do_not_lose_counts(self):
        revision.reset()
        workers = 20
        rounds = 25
        barrier = threading.Barrier(workers)

        def worker():
            barrier.wait()
            for _ in range(rounds):
                revision.bump("pX", "files")

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert revision.snapshot("pX")["parts"]["files"] == 1 + workers * rounds


class TestReadCost:
    def test_snapshot_stays_within_the_5ms_budget(self):
        """§7.3 要求 `/revision` 极轻量（P99 < 5 ms）：这里断言平均耗时远低于预算。"""
        revision.reset()
        iterations = 3000
        started = time.perf_counter()
        for _ in range(iterations):
            revision.snapshot("pPerf")
        per_call = (time.perf_counter() - started) / iterations
        assert per_call < 0.005, "单次 %.6f s，超过 5 ms 预算" % per_call
