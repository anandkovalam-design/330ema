from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from aadithya_quantlab.zerodha_live_trading import app
from aadithya_quantlab.zerodha_live_trading.persistence import (
    load_daily_connection,
    load_engine_state,
    save_daily_connection,
    save_engine_state,
)


def test_cloud_credentials_use_environment_without_keyring(monkeypatch) -> None:
    monkeypatch.setenv("ZERODHA_CLOUD_MODE", "true")
    monkeypatch.setenv("ZERODHA_API_KEY", "cloud-api-key")
    monkeypatch.setenv("ZERODHA_API_SECRET", "cloud-api-secret")
    monkeypatch.setattr(
        app,
        "_credential_keyring",
        lambda: (_ for _ in ()).throw(AssertionError("keyring must not be used")),
    )

    assert app._load_login_credentials() == {
        "api_key": "cloud-api-key",
        "api_secret": "cloud-api-secret",
    }


def test_cloud_runtime_hard_blocks_real_order_permission(monkeypatch) -> None:
    monkeypatch.setenv("ZERODHA_CLOUD_MODE", "true")
    engine = app._default_state_for(app.UNDERLYINGS["NIFTY"])

    assert app._live_orders_permitted(engine, real_mode_armed=True) is False


def test_recovered_real_activity_requires_reconciliation(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "engine-state.json"
    state = {
        name: app._default_state_for(config)
        for name, config in app.UNDERLYINGS.items()
    }
    state["NIFTY"]["enabled"] = True
    state["NIFTY"]["open_trade"] = {
        "instrument": "NFO:NIFTYTESTCE",
        "quantity": 130,
        "entry_order_id": "ORDER-123",
        "trade_mode": "REAL",
        "stop_price": 80.0,
    }
    save_engine_state(
        state_path,
        session_date="2026-08-13",
        engine_state=state,
        saved_at=datetime.fromisoformat("2026-08-13T10:00:00+05:30"),
    )
    monkeypatch.setattr(app, "TRADING_STATE_PATH", state_path)

    restored, metadata = app._restore_persistent_engine_state()

    assert metadata["session_date"] == "2026-08-13"
    assert restored["NIFTY"]["open_trade"]["entry_order_id"] == "ORDER-123"
    assert restored["NIFTY"]["reconciliation_required"] is True
    assert restored["NIFTY"]["enabled"] is False


def test_daily_token_envelope_is_date_scoped_and_does_not_store_secret(tmp_path: Path) -> None:
    token_path = tmp_path / "daily-token.json"

    save_daily_connection(
        token_path,
        session_date="2026-08-13",
        api_key=None,
        access_token="daily-access-token",
    )

    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert "api_secret" not in payload
    assert load_daily_connection(token_path, session_date="2026-08-13") == {
        "access_token": "daily-access-token"
    }
    assert load_daily_connection(token_path, session_date="2026-08-14") is None


def test_engine_state_store_excludes_transient_market_frames(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = {
        "NIFTY": {
            "open_trade": {"entry_order_id": "PAPER", "stop_price": 95.0},
            "last_signal_ts": "2026-08-13T09:30:00+05:30",
            "trades_taken": 1,
            "realized_pnl": 125.0,
            "latest_bars": "large-transient-frame",
        }
    }

    save_engine_state(
        state_path,
        session_date="2026-08-13",
        engine_state=state,
        saved_at=datetime.fromisoformat("2026-08-13T10:00:00+05:30"),
    )
    restored, _ = load_engine_state(state_path)

    assert restored["NIFTY"]["open_trade"]["entry_order_id"] == "PAPER"
    assert restored["NIFTY"]["last_signal_ts"].startswith("2026-08-13")
    assert restored["NIFTY"]["trades_taken"] == 1
    assert "latest_bars" not in restored["NIFTY"]


class _ReconciliationKite:
    def orders(self):
        return [{"order_id": "ORDER-123", "status": "COMPLETE"}]

    def positions(self):
        return {
            "net": [
                {
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTYTESTCE",
                    "quantity": 130,
                }
            ]
        }


def test_broker_reconciliation_hook_clears_gate_on_exact_match() -> None:
    engine_state = {
        "NIFTY": {
            "open_trade": {
                "instrument": "NFO:NIFTYTESTCE",
                "quantity": 130,
                "trade_mode": "REAL",
            },
            "order_logs": [
                {
                    "instrument": "NFO:NIFTYTESTCE",
                    "order_id": "ORDER-123",
                }
            ],
            "reconciliation_required": True,
        }
    }

    report = app._reconcile_engine_state_with_broker(_ReconciliationKite(), engine_state)

    assert report["status"] == "MATCHED"
    assert engine_state["NIFTY"]["reconciliation_required"] is False
