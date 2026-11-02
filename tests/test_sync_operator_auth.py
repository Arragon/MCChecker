"""M16-18 operator authorization tests."""

import pytest

from app.core.errors import FORBIDDEN, AppError
from app.services import sync_operator_auth


def test_operator_token_required_and_constant_time_compare(monkeypatch):
    monkeypatch.delenv(sync_operator_auth.OPERATOR_ENV, raising=False)
    assert sync_operator_auth.operator_enabled() is False
    with pytest.raises(AppError) as exc:
        sync_operator_auth.require_operator("anything")
    assert exc.value.code == FORBIDDEN

    monkeypatch.setenv(sync_operator_auth.OPERATOR_ENV, "high-entropy-token")
    assert sync_operator_auth.operator_enabled() is True
    sync_operator_auth.require_operator("high-entropy-token")

    for bad in ("", "wrong-token", None):
        with pytest.raises(AppError) as exc:
            sync_operator_auth.require_operator(bad)
        assert exc.value.code == FORBIDDEN
        assert "high-entropy-token" not in str(exc.value)
