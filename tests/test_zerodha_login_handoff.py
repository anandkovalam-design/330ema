import pytest

from aadithya_quantlab.zerodha_live_trading.login_handoff import LoginHandoffStore


def test_login_handoff_is_consumed_once() -> None:
    store = LoginHandoffStore()
    handoff_id = store.create("dashboard-session-token")

    assert store.consume(handoff_id) == "dashboard-session-token"
    assert store.consume(handoff_id) is None


def test_login_handoff_rejects_empty_values() -> None:
    store = LoginHandoffStore()

    with pytest.raises(ValueError, match="dashboard_token is required"):
        store.create("")
    assert store.consume("") is None