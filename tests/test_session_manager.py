"""M16-21 会话失效 singleflight 重登/锁存/退避（离线假 login）。"""

from __future__ import annotations

import threading
import time

import pytest

from collector.session_manager import SessionManager


class FakeSession:
    def __init__(self, generation):
        self.generation = generation
        self.closed = False

    def close(self):
        self.closed = True


class LoginResult:
    def __init__(self, status, reason=None, session=None):
        self.status = status
        self.reason_code = reason
        self.session = session


class FakeLogin:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def authenticate(self, credentials):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        if result.status == "succeeded" and result.session is None:
            result.session = FakeSession(self.calls)
        return result


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def ok(session=None):
    return LoginResult("succeeded", None, session)


def fail(reason):
    return LoginResult("failed", reason)


def ok_result():
    return {"status": "succeeded"}


def auth_failure():
    return {"status": "failed", "reason_code": "auth_failed"}


def test_ten_expired_sources_share_one_relogin_and_one_replay_each():
    login = FakeLogin([ok(), ok()])
    credentials = []
    manager = SessionManager(login, lambda: credentials.append(1) or object(), Clock())

    assert manager.execute(lambda session: ok_result()).status == "succeeded"
    assert login.calls == 1
    assert manager.generation == 1

    barrier = threading.Barrier(10)
    seen = {}
    calls = {}

    def operation(session):
        key = threading.get_ident()
        calls[key] = calls.get(key, 0) + 1
        if calls[key] == 1:
            barrier.wait(timeout=5)
        if session.generation < 2:
            return auth_failure()
        return ok_result()

    outcomes = []
    threads = []
    for _ in range(10):
        thread = threading.Thread(
            target=lambda: outcomes.append(manager.execute(operation))
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(outcomes) == 10
    assert all(outcome.status == "succeeded" for outcome in outcomes)
    assert login.calls == 2
    assert len(credentials) == 2
    assert all(count == 2 for count in calls.values())


def test_replay_is_at_most_once_and_stale_session_is_closed():
    first_session = FakeSession(1)
    login = FakeLogin([ok(first_session), ok()])
    manager = SessionManager(login, lambda: object(), Clock())
    manager.execute(lambda session: ok_result())

    attempts = []

    def always_expired(session):
        attempts.append(session.generation)
        return auth_failure()

    outcome = manager.execute(always_expired)
    assert outcome.status == "failed"
    assert outcome.reason_code == "auth_failed"
    assert outcome.replayed is True
    assert len(attempts) == 2  # 原调用 + 一次安全重放
    assert login.calls == 2
    assert first_session.closed is True
    assert manager.session.generation == 2


def test_bad_credentials_latch_across_five_cycles():
    login = FakeLogin([fail("bad_credentials")] + [ok() for _ in range(5)])
    calls = []
    manager = SessionManager(login, lambda: calls.append(1) or object(), Clock())

    for _ in range(5):
        outcome = manager.execute(lambda session: ok_result())
        assert outcome.status == "failed"
        assert outcome.reason_code == "bad_credentials"
    assert login.calls == 1
    assert calls == [1]

    manager.reset_lockout()
    assert manager.execute(lambda session: ok_result()).status == "succeeded"
    assert login.calls == 2


def test_network_failure_backoff_is_bounded_and_resets_after_success():
    clock = Clock(1000.0)
    login = FakeLogin([fail("network_error"), fail("network_error"), fail("network_error"), ok()])
    manager = SessionManager(login, lambda: object(), clock)

    first = manager.execute(lambda session: ok_result())
    assert first.reason_code == "network_error"
    assert login.calls == 1

    clock.value += 30
    early = manager.execute(lambda session: ok_result())
    assert early.reason_code == "network_error"
    assert early.retry_after_seconds == 30
    assert login.calls == 1

    clock.value += 31
    second = manager.execute(lambda session: ok_result())
    assert second.reason_code == "network_error"
    assert login.calls == 2

    clock.value += 5 * 60
    third = manager.execute(lambda session: ok_result())
    assert third.reason_code == "network_error"
    assert login.calls == 3

    clock.value += 15 * 60
    fourth = manager.execute(lambda session: ok_result())
    assert fourth.status == "succeeded"
    assert login.calls == 4

    clock.value += 1
    assert manager.execute(lambda session: ok_result()).status == "succeeded"
    assert login.calls == 4


def test_business_failure_does_not_trigger_login():
    login = FakeLogin([ok()])
    manager = SessionManager(login, lambda: object(), Clock())
    manager.execute(lambda session: ok_result())
    outcome = manager.execute(
        lambda session: {"status": "failed", "reason_code": "business_error"}
    )
    assert outcome.status == "failed"
    assert outcome.reason_code == "business_error"
    assert login.calls == 1


def test_exception_during_login_is_network_failure_with_backoff():
    clock = Clock()
    login = FakeLogin([RuntimeError("vault unavailable")])
    manager = SessionManager(login, lambda: object(), clock)
    outcome = manager.execute(lambda session: ok_result())
    assert outcome.status == "failed"
    assert outcome.reason_code == "network_error"
    assert login.calls == 1
    clock.value += 10
    assert manager.execute(lambda session: ok_result()).reason_code == "network_error"
    assert login.calls == 1


def test_auto_relogin_off_does_not_call_login():
    login = FakeLogin([ok()])
    calls = []
    manager = SessionManager(
        login, lambda: calls.append(1) or object(), Clock(), auto_relogin=False
    )
    outcome = manager.execute(lambda session: ok_result())
    assert outcome.status == "failed"
    assert outcome.reason_code == "auto_relogin_disabled"
    assert login.calls == 0
    assert calls == []

    manager.auto_relogin = True
    assert manager.execute(lambda session: ok_result()).status == "succeeded"
    assert login.calls == 1
