import pandas as pd
from datetime import time as wall_time, timedelta

from aadithya_quantlab.zerodha_live_trading import main
from aadithya_quantlab.zerodha_live_trading import app
from aadithya_quantlab.zerodha_live_trading.app import (
    ENTRY_CUTOFF_IST,
    MANDATORY_EXIT_IST,
    _build_price_chart,
    _compute_signals,
    _credential_preview,
    _clear_login_credentials,
    _default_state_for,
    _entries_blocked_for_expiry_day,
    _front_future_row,
    _contract_lot_size,
    _load_login_credentials,
    _load_risk_settings,
    _sync_scaled_metal_risk_settings,
    _run_engine_for,
    _select_option_contract,
    _save_login_credentials,
    _save_risk_settings,
    _update_trailing_stop,
    UNDERLYINGS,
)


def test_live_trading_schedule_runs_entries_to_1500_and_exits_at_1515() -> None:
    assert ENTRY_CUTOFF_IST == "15:00"
    assert MANDATORY_EXIT_IST == "15:15"


def test_mcx_schedule_runs_entries_to_2230_and_exits_at_2250() -> None:
    for name in ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"):
        assert UNDERLYINGS[name].entry_cutoff_ist == "22:30"
        assert UNDERLYINGS[name].mandatory_exit_ist == "22:50"


def test_gold_and_silver_point_values_scale_from_nifty_defaults() -> None:
    nifty = _default_state_for(UNDERLYINGS["NIFTY"])
    gold = _default_state_for(UNDERLYINGS["GOLD"])
    silver = _default_state_for(UNDERLYINGS["SILVER"])

    for key in ("sl_points", "trail_trigger_points", "trail_step_points"):
        assert gold[key] == nifty[key] * 6
        assert silver[key] == nifty[key] * 10
    for key in ("sl_to_cost_profit_pct", "trail_after_profit_pct", "mfe_giveback_pct"):
        assert gold[key] == nifty[key]
        assert silver[key] == nifty[key]


def test_gold_and_silver_risk_values_always_follow_nifty() -> None:
    engine_state = {
        name: _default_state_for(config)
        for name, config in UNDERLYINGS.items()
    }
    engine_state["NIFTY"].update(
        {
            "sl_points": 25.0,
            "sl_to_cost_profit_pct": 0.35,
            "trail_after_profit_pct": 0.55,
            "mfe_giveback_pct": 0.20,
            "trail_trigger_points": 18.0,
            "trail_step_points": 7.0,
        }
    )

    assert _sync_scaled_metal_risk_settings(engine_state) is True
    assert engine_state["GOLD"]["sl_points"] == 150.0
    assert engine_state["GOLD"]["trail_trigger_points"] == 108.0
    assert engine_state["GOLD"]["trail_step_points"] == 42.0
    assert engine_state["SILVER"]["sl_points"] == 250.0
    assert engine_state["SILVER"]["trail_trigger_points"] == 180.0
    assert engine_state["SILVER"]["trail_step_points"] == 70.0
    for name in ("GOLD", "SILVER"):
        assert engine_state[name]["sl_to_cost_profit_pct"] == 0.35
        assert engine_state[name]["trail_after_profit_pct"] == 0.55
        assert engine_state[name]["mfe_giveback_pct"] == 0.20


def test_mcx_front_future_and_atm_option_are_resolved_dynamically() -> None:
    future_expiry = (pd.Timestamp.now(tz="Asia/Kolkata") + timedelta(days=10)).date()
    later_expiry = future_expiry + timedelta(days=30)
    rows = [
        {"name": "CRUDEOIL", "tradingsymbol": "CRUDEOIL26AUGFUT", "instrument_type": "FUT", "expiry": future_expiry, "instrument_token": 101},
        {"name": "CRUDEOIL", "tradingsymbol": "CRUDEOIL26SEPFUT", "instrument_type": "FUT", "expiry": later_expiry, "instrument_token": 202},
        {"name": "CRUDEOIL", "tradingsymbol": "CRUDEOIL6000CE", "instrument_type": "CE", "expiry": future_expiry, "strike": 6000, "lot_size": 100},
        {"name": "CRUDEOIL", "tradingsymbol": "CRUDEOIL6100CE", "instrument_type": "CE", "expiry": future_expiry, "strike": 6100, "lot_size": 100},
        {"name": "CRUDEOIL", "tradingsymbol": "CRUDEOIL6200NEXTCE", "instrument_type": "CE", "expiry": later_expiry, "strike": 6200, "lot_size": 100},
    ]

    cfg = UNDERLYINGS["CRUDEOIL"]
    assert _front_future_row(cfg, rows)["instrument_token"] == 101
    instrument = _select_option_contract(object(), cfg, "CALL", 6070.0, instrument_rows=rows)
    assert instrument == "MCX:CRUDEOIL6100CE"
    assert _contract_lot_size(instrument, rows) == 100
    assert _front_future_row(cfg, rows, expiry_offset=1)["instrument_token"] == 202
    next_instrument = _select_option_contract(
        object(), cfg, "CALL", 6170.0, expiry_week_offset=1, instrument_rows=rows
    )
    assert next_instrument == "MCX:CRUDEOIL6200NEXTCE"


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
    monkeypatch.setattr(
        app,
        "_fetch_spot_5m",
        lambda kite, cfg, instrument_rows=None, expiry_offset=0: bars,
    )
    monkeypatch.setattr(app, "_extract_ltp", lambda kite, instrument: 110.0)
    monkeypatch.setattr(app, "_complete_persistent_trade", lambda *args, **kwargs: None)
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
