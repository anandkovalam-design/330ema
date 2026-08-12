import pandas as pd
from datetime import time as wall_time

from aadithya_quantlab.zerodha_live_trading import main
from aadithya_quantlab.zerodha_live_trading import app
from aadithya_quantlab.zerodha_live_trading.app import (
    _build_price_chart,
    _compute_signals,
    _credential_preview,
    _clear_login_credentials,
    _default_state_for,
    _entries_blocked_for_expiry_day,
    _load_login_credentials,
    _load_risk_settings,
    _run_engine_for,
    _save_login_credentials,
    _save_risk_settings,
    _update_trailing_stop,
    UNDERLYINGS,
)


def test_standalone_dashboard_entry_point() -> None:
    assert main.__module__ == "aadithya_quantlab.zerodha_live_trading.app"


def test_price_chart_contains_candles_and_ema_layers() -> None:
    timestamps = pd.date_range("2026-08-03 09:15", periods=3, freq="5min", tz="Asia/Kolkata")
    bars = _compute_signals(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": [24700.0, 24712.0, 24705.0],
                "high": [24718.0, 24720.0, 24715.0],
                "low": [24695.0, 24700.0, 24698.0],
                "close": [24712.0, 24705.0, 24714.0],
            }
        )
    )

    spec = _build_price_chart(bars, "NIFTY").to_dict()

    assert len(spec["layer"]) == 4
    assert spec["layer"][0]["mark"]["type"] == "rule"
    assert spec["layer"][1]["mark"]["type"] == "bar"
    assert spec["layer"][2]["mark"]["type"] == "line"
    assert spec["layer"][3]["mark"]["type"] == "line"


def test_trailing_stop_moves_to_cost_and_never_moves_down() -> None:
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    trade = {"entry_option": 100.0, "stop_price": 80.0, "max_ltp": 100.0}

    assert _update_trailing_stop(trade, engine, 130.0) == "SL_TO_COST"
    assert trade["stop_price"] == 100.0
    assert _update_trailing_stop(trade, engine, 120.0) is None
    assert trade["stop_price"] == 100.0


def test_trailing_stop_raises_with_mfe_and_point_steps() -> None:
    engine = _default_state_for(UNDERLYINGS["SENSEX"])
    trade = {"entry_option": 100.0, "stop_price": 50.0, "max_ltp": 100.0}

    stage = _update_trailing_stop(trade, engine, 180.0)

    assert stage == "STEP_TRAIL"
    assert trade["trailing_active"] is True
    assert trade["stop_price"] > 150.0
    raised_stop = trade["stop_price"]
    _update_trailing_stop(trade, engine, 160.0)
    assert trade["stop_price"] == raised_stop


def test_trailing_stop_never_exceeds_observed_ltp() -> None:
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine["mfe_giveback_pct"] = 0.0
    engine["trail_step_points"] = 20.0
    trade = {"entry_option": 100.0, "stop_price": 80.0, "max_ltp": 100.0}

    _update_trailing_stop(trade, engine, 180.0)

    assert trade["stop_price"] == 180.0


def test_expiry_day_blocks_current_week_but_allows_next_week() -> None:
    assert _entries_blocked_for_expiry_day(1, UNDERLYINGS["NIFTY"].expiry_weekday, 0) is True
    assert _entries_blocked_for_expiry_day(1, UNDERLYINGS["NIFTY"].expiry_weekday, 1) is False
    assert _entries_blocked_for_expiry_day(3, UNDERLYINGS["SENSEX"].expiry_weekday, 0) is True
    assert _entries_blocked_for_expiry_day(3, UNDERLYINGS["SENSEX"].expiry_weekday, 1) is False


def test_nifty_hard_stop_closes_paper_position_and_keeps_engine_off(monkeypatch) -> None:
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "timestamp": [now - pd.Timedelta(minutes=5), now],
            "open": [24000.0, 24010.0],
            "high": [24020.0, 24030.0],
            "low": [23990.0, 24000.0],
            "close": [24010.0, 24020.0],
        }
    )
    monkeypatch.setattr(app, "_fetch_spot_5m", lambda kite, cfg: bars)
    monkeypatch.setattr(app, "_extract_ltp", lambda kite, instrument: 110.0)
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine["enabled"] = False
    engine["hard_stop_requested"] = True
    engine["open_trade"] = {
        "instrument": "NFO:NIFTYTESTCE",
        "entry_option": 100.0,
        "quantity": 130,
        "stop_price": 80.0,
        "trade_mode": "PAPER",
    }

    result = _run_engine_for(
        kite=object(),
        cfg=UNDERLYINGS["NIFTY"],
        engine=engine,
        entry_start=wall_time(9, 16),
        entry_end=wall_time(12, 59),
        force_exit=wall_time(15, 25),
        expiry_week_offset=1,
        trade_mode="PAPER",
        real_mode_armed=False,
    )

    assert result["open_trade"] is None
    assert result["realized_pnl"] == 1300.0
    assert result["enabled"] is False
    assert result["entry_status"] == "Hard stopped; entries disabled"


def test_credentials_round_trip_through_keyring(monkeypatch) -> None:
    stored: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        @staticmethod
        def set_password(service: str, account: str, value: str) -> None:
            stored[(service, account)] = value

        @staticmethod
        def get_password(service: str, account: str) -> str | None:
            return stored.get((service, account))

        @staticmethod
        def delete_password(service: str, account: str) -> None:
            stored.pop((service, account), None)

    monkeypatch.setattr(app, "_credential_keyring", lambda: (FakeKeyring, RuntimeError))

    _save_login_credentials("api-key-value", "api-secret-value")

    assert _load_login_credentials() == {
        "api_key": "api-key-value",
        "api_secret": "api-secret-value",
    }
    assert _credential_preview("api-key-value") == "api-...ue"
    _clear_login_credentials()
    assert _load_login_credentials() is None


def test_risk_settings_persist_for_both_underlyings(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app, "RISK_SETTINGS_PATH", tmp_path / "risk-settings.json")
    engine_state = {
        name: _default_state_for(config)
        for name, config in UNDERLYINGS.items()
    }
    engine_state["NIFTY"]["sl_points"] = 25.0
    engine_state["NIFTY"]["mfe_giveback_pct"] = 0.20
    engine_state["SENSEX"]["trail_trigger_points"] = 20.0

    _save_risk_settings(engine_state)
    loaded = _load_risk_settings()

    assert loaded["NIFTY"]["sl_points"] == 25.0
    assert loaded["NIFTY"]["mfe_giveback_pct"] == 0.20
    assert loaded["SENSEX"]["trail_trigger_points"] == 20.0