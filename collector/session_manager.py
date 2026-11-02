"""M16-21 会话失效 singleflight 自动重登、密码错误锁存与有界退避。

本模块在 collector 进程内管理**一个连接**的登录会话；它不读 SecretStore，也
不接触 Cookie/CSRF 值，凭据由调用方通过 `credentials_provider` 在登录瞬间提供。

并发语义（卡面 U/X）：

- 10 个来源同时发现会话过期时，只允许 **一次** login；其余调用在锁内看到
  generation 已推进，直接用新会话做一次安全重放；
- 每次 `execute` 最多一次重放；重放后仍 auth_failed 就结束，不进入登录循环；
- 错密码/锁定/表单变化立即锁存，跨多个周期不再调用 credentials_provider；
  只有 `reset_lockout()`（运维明确重试或更换凭据）才解除；
- 网络故障按 1/5/15 分钟有界退避；退避期间不登录、不判密码错误；
- 会话被替换时关闭旧 session；不保存密码/Cookie 到返回值。

真实登录/重登和 idle 证据仍需 E2/E3；本模块的离线测试用假 login/session。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

BACKOFF_MINUTES = (1, 5, 15)
LOCK_REASONS = frozenset({"bad_credentials", "locked", "login_flow_changed"})
NETWORK_REASON = "network_error"
AUTH_REASON = "auth_failed"


@dataclass
class SessionOutcome:
    status: str
    reason_code: Optional[str] = None
    result: Any = None
    replayed: bool = False
    logins: int = 0
    retry_after_seconds: int = 0

    def __repr__(self) -> str:
        return "SessionOutcome(status=%r, reason_code=%r, replayed=%r, logins=%r)" % (
            self.status,
            self.reason_code,
            self.replayed,
            self.logins,
        )


def _outcome_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _is_auth_failure(result: Any) -> bool:
    return (
        str(_outcome_value(result, "status", "")) == "failed"
        and str(_outcome_value(result, "reason_code", "")) == AUTH_REASON
    )


class SessionManager:
    def __init__(
        self,
        login: Any,
        credentials_provider: Callable[[], Any],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.login = login
        self.credentials_provider = credentials_provider
        self.clock = clock
        self._lock = threading.Lock()
        self._session = None
        self._generation = 0
        self._login_count = 0
        self._locked_reason: Optional[str] = None
        self._backoff_until = 0.0
        self._backoff_index = 0

    @property
    def session(self) -> Any:
        with self._lock:
            return self._session

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def login_count(self) -> int:
        with self._lock:
            return self._login_count

    def execute(self, operation: Callable[[Any], Any]) -> SessionOutcome:
        with self._lock:
            session = self._session
            generation = self._generation
        if session is None:
            return self._recover(operation, None, attempted=False)
        result = self._call(operation, session)
        if not _is_auth_failure(result):
            return self._result(result)
        return self._recover(operation, generation, attempted=True)

    def _recover(
        self,
        operation: Callable[[Any], Any],
        seen_generation: Optional[int],
        attempted: bool,
    ) -> SessionOutcome:
        with self._lock:
            if self._session is not None and (
                seen_generation is None or self._generation != seen_generation
            ):
                session = self._session
                generation = self._generation
                replay = attempted
            else:
                blocked = self._blocked_locked()
                if blocked is not None:
                    return blocked
                login_outcome = self._login_locked()
                if login_outcome.status != "succeeded":
                    return login_outcome
                session = self._session
                generation = self._generation
                replay = attempted
        result = self._call(operation, session)
        outcome = self._result(result)
        outcome.replayed = replay
        if _is_auth_failure(result) and replay:
            outcome.status = "failed"
            outcome.reason_code = AUTH_REASON
            return outcome
        if _is_auth_failure(result) and not replay:
            # 初始无会话路径：登录后第一次调用仍 auth_failed，只允许一次，不再登录。
            outcome.status = "failed"
            outcome.reason_code = AUTH_REASON
            return outcome
        return outcome

    def _blocked_locked(self) -> Optional[SessionOutcome]:
        if self._locked_reason is not None:
            return SessionOutcome(
                "failed", self._locked_reason, logins=self._login_count
            )
        now = self.clock()
        if now < self._backoff_until:
            return SessionOutcome(
                "failed",
                NETWORK_REASON,
                logins=self._login_count,
                retry_after_seconds=max(1, int(round(self._backoff_until - now))),
            )
        return None

    def _login_locked(self) -> SessionOutcome:
        self._login_count += 1
        try:
            credentials = self.credentials_provider()
            login_result = self.login.authenticate(credentials)
        except Exception:
            logger.warning("登录会话建立失败（凭据或登录器异常）", exc_info=True)
            old = self._session
            self._session = None
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self._set_backoff_locked()
            return SessionOutcome("failed", NETWORK_REASON, logins=self._login_count)
        status = str(getattr(login_result, "status", ""))
        reason = getattr(login_result, "reason_code", None)
        if status == "succeeded":
            session = getattr(login_result, "session", None)
            if session is None:
                logger.error("登录器返回成功但没有 session")
                self._set_backoff_locked()
                return SessionOutcome("failed", NETWORK_REASON, logins=self._login_count)
            old = self._session
            self._session = session
            self._generation += 1
            self._backoff_index = 0
            self._backoff_until = 0.0
            self._locked_reason = None
            if old is not None and old is not session:
                try:
                    old.close()
                except Exception:
                    pass
            return SessionOutcome("succeeded", None, logins=self._login_count)
        reason_code = str(reason or AUTH_REASON)
        old = self._session
        self._session = None
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        if reason_code in LOCK_REASONS:
            self._locked_reason = reason_code
        elif reason_code in (NETWORK_REASON, "collector_offline"):
            self._set_backoff_locked()
        return SessionOutcome("failed", reason_code, logins=self._login_count)

    def _set_backoff_locked(self) -> None:
        minutes = BACKOFF_MINUTES[min(self._backoff_index, len(BACKOFF_MINUTES) - 1)]
        self._backoff_until = self.clock() + minutes * 60
        self._backoff_index = min(self._backoff_index + 1, len(BACKOFF_MINUTES) - 1)

    def reset_lockout(self) -> None:
        """运维明确重试/更换凭据后解除密码错误锁存。"""
        with self._lock:
            self._locked_reason = None
            self._backoff_until = 0.0
            self._backoff_index = 0

    def close(self) -> None:
        with self._lock:
            session, self._session = self._session, None
            self._generation += 1
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def _call(self, operation: Callable[[Any], Any], session: Any) -> Any:
        try:
            return operation(session)
        except Exception:
            logger.warning("会话操作异常", exc_info=True)
            return {"status": "failed", "reason_code": NETWORK_REASON}

    def _result(self, result: Any) -> SessionOutcome:
        status = str(_outcome_value(result, "status", "failed"))
        reason = _outcome_value(result, "reason_code", None)
        return SessionOutcome(
            "succeeded" if status == "succeeded" else "failed",
            None if status == "succeeded" else (str(reason) if reason else None),
            result=result,
            logins=self._login_count,
        )
