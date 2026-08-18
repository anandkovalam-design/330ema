import pandas as pd
from datetime import datetime, time as wall_time, timedelta

from aadithya_quantlab.zerodha_live_trading import main
from aadithya_quantlab.zerodha_live_trading import app
from aadithya_quantlab.zerodha_live_trading.app import (
    ENTRY_CUTOFF_IST,
    HEIKIN_ASHI_STOP_POINTS,
    MANDATORY_EXIT_IST,
    STRATEGY_HEIKIN_ASHI,
    _build_heikin_ashi_chart,
    _build_price_chart,
    _completed_5m_bars,
    _compute_heikin_ashi,
    _compute_signals,
    _credential_preview,
    _clear_login_credentials,
    _default_state_for,
    _entries_blocked_for_expiry_day,
    _front_future_row,
    _has_opposite_heikin_ashi_candle,
    _latest_confirmed_heikin_ashi_signal,
    _contract_lot_size,
    _load_login_credentials,
    _load_risk_settings,
    _mcx_order_quantity,
    MCX_CONTRACT_SPECS,
    _run_parallel_index_engines,
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


def test_all_mcx_commodities_treat_live_lot_size_one_as_one_contract_lot() -> None:
    rows = [
        {
            "name": name,
            "tradingsymbol": f"{name}TESTCE",
            "instrument_type": "CE",
            "lot_size": 1,
        }
        for name in ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER")
    ]

    for name in ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"):
        lots, broker_lot_size, order_quantity = _mcx_order_quantity(
            f"MCX:{name}TESTCE",
            rows,
            requested_lots=1,
        )
        assert lots == 1
        assert broker_lot_size == 1
        assert order_quantity == 1

        lots, broker_lot_size, order_quantity = _mcx_order_quantity(
            f"MCX:{name}TESTCE",
            rows,
            requested_lots=3,
        )
        assert lots == 3
        assert broker_lot_size == 1
        assert order_quantity == 3


def test_mcx_display_contract_sizes_are_separate_from_broker_quantity() -> None:
    assert MCX_CONTRACT_SPECS["CRUDEOIL"] == (100, "barrels")
    assert MCX_CONTRACT_SPECS["NATURALGAS"] == (1250, "MMBtu")
    assert MCX_CONTRACT_SPECS["GOLD"] == (100, "x 10 g = 1 kg")
    assert MCX_CONTRACT_SPECS["SILVER"] == (30, "kg")


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


def test_heikin_ashi_formula_colour_and_chart() -> None:
    timestamps = pd.date_range("2026-08-19 09:15", periods=3, freq="5min", tz="Asia/Kolkata")
    raw = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0, 95.0, 105.0],
            "high": [102.0, 108.0, 116.0],
            "low": [88.0, 94.0, 103.0],
            "close": [90.0, 106.0, 114.0],
        }
    )

    bars = _compute_heikin_ashi(raw)

    assert bars["ha_close"].tolist() == [95.0, 100.75, 109.5]
    assert bars["ha_open"].tolist() == [95.0, 95.0, 97.875]
    assert bars["ha_color"].tolist() == ["NEUTRAL", "GREEN", "GREEN"]
    spec = _build_heikin_ashi_chart(bars, "NIFTY").to_dict()
    assert len(spec["layer"]) == 2
    assert spec["layer"][0]["mark"]["type"] == "rule"
    assert spec["layer"][1]["mark"]["type"] == "bar"


def test_heikin_ashi_signal_requires_second_new_colour_candle() -> None:
    timestamps = pd.date_range("2026-08-19 09:15", periods=5, freq="5min", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "close": [100.0, 99.0, 101.0, 102.0, 103.0],
            "ha_color": ["RED", "RED", "GREEN", "GREEN", "GREEN"],
        }
    )

    assert _latest_confirmed_heikin_ashi_signal(bars.iloc[:3]) is None
    signal = _latest_confirmed_heikin_ashi_signal(bars)
    assert signal is not None
    assert signal["timestamp"] == timestamps[3]
    assert signal["ha_color"] == "GREEN"


def test_heikin_ashi_uses_closed_bars_and_exits_on_first_opposite_colour() -> None:
    timestamps = pd.date_range("2026-08-19 09:40", periods=4, freq="5min", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "ha_color": ["RED", "GREEN", "GREEN", "RED"],
        }
    )
    at_0959 = datetime.fromisoformat("2026-08-19T09:59:00+05:30")
    at_1000 = datetime.fromisoformat("2026-08-19T10:00:00+05:30")

    assert len(_completed_5m_bars(bars, at_0959)) == 3
    completed = _completed_5m_bars(bars, at_1000)
    assert len(completed) == 4
    assert _has_opposite_heikin_ashi_candle(completed, "GREEN", timestamps[2]) is True


def test_heikin_ashi_engine_opens_unlimited_paper_trade_with_fixed_stop(monkeypatch) -> None:
    now = datetime.fromisoformat("2026-08-19T10:00:00+05:30")
    timestamps = pd.date_range("2026-08-19 09:40", periods=3, freq="5min", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [24000.0, 24010.0, 24020.0],
            "high": [24010.0, 24020.0, 24030.0],
            "low": [23990.0, 24000.0, 24010.0],
            "close": [24000.0, 24010.0, 24020.0],
        }
    )
    ha_bars = bars.assign(
        ha_open=[24005.0, 24000.0, 24005.0],
        ha_high=[24010.0, 24020.0, 24030.0],
        ha_low=[23990.0, 24000.0, 24005.0],
        ha_close=[23995.0, 24010.0, 24020.0],
        ha_color=["RED", "GREEN", "GREEN"],
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(app, "datetime", FixedDatetime)
    monkeypatch.setattr(app, "_fetch_spot_5m", lambda *args, **kwargs: bars)
    monkeypatch.setattr(app, "_compute_heikin_ashi", lambda frame: ha_bars)
    monkeypatch.setattr(app, "_select_option_contract", lambda *args, **kwargs: "NFO:NIFTYATMCE")
    monkeypatch.setattr(app, "_extract_ltp", lambda *args, **kwargs: 100.0)
    monkeypatch.setattr(app, "_persist_open_trade", lambda *args, **kwargs: None)
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine.update({"strategy_mode": STRATEGY_HEIKIN_ASHI, "trades_taken": 99, "max_trades": 1})

    result = _run_engine_for(
        object(), UNDERLYINGS["NIFTY"], engine,
        wall_time(9, 16), wall_time(15, 0), wall_time(15, 15), 1, "PAPER", False,
    )

    trade = result["open_trade"]
    assert trade is not None
    assert trade["instrument"] == "NFO:NIFTYATMCE"
    assert trade["side"] == "CALL"
    assert trade["stop_price"] == 100.0 - HEIKIN_ASHI_STOP_POINTS
    assert trade["trade_mode"] == "PAPER"
    assert trade["strategy_mode"] == STRATEGY_HEIKIN_ASHI


def test_heikin_ashi_strategy_refuses_real_entries(monkeypatch) -> None:
    now = datetime.fromisoformat("2026-08-19T10:00:00+05:30")
    timestamps = pd.date_range("2026-08-19 09:40", periods=3, freq="5min", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0],
        }
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(app, "datetime", FixedDatetime)
    monkeypatch.setattr(app, "_fetch_spot_5m", lambda *args, **kwargs: bars)
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine["strategy_mode"] = STRATEGY_HEIKIN_ASHI

    result = _run_engine_for(
        object(), UNDERLYINGS["NIFTY"], engine,
        wall_time(9, 16), wall_time(15, 0), wall_time(15, 15), 1, "REAL", True,
    )

    assert result["open_trade"] is None
    assert result["entry_status"] == "Heikin-Ashi test strategy is PAPER-only"


def test_heikin_ashi_trade_exits_on_first_closed_opposite_candle(monkeypatch) -> None:
    now = datetime.fromisoformat("2026-08-19T10:05:00+05:30")
    timestamps = pd.date_range("2026-08-19 09:40", periods=4, freq="5min", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [24000.0, 24010.0, 24020.0, 24015.0],
            "high": [24010.0, 24020.0, 24030.0, 24020.0],
            "low": [23990.0, 24000.0, 24010.0, 24000.0],
            "close": [24000.0, 24010.0, 24020.0, 24005.0],
        }
    )
    ha_bars = bars.assign(
        ha_open=[24005.0, 24000.0, 24005.0, 24015.0],
        ha_high=[24010.0, 24020.0, 24030.0, 24020.0],
        ha_low=[23990.0, 24000.0, 24005.0, 24000.0],
        ha_close=[23995.0, 24010.0, 24020.0, 24005.0],
        ha_color=["RED", "GREEN", "GREEN", "RED"],
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(app, "datetime", FixedDatetime)
    monkeypatch.setattr(app, "_fetch_spot_5m", lambda *args, **kwargs: bars)
    monkeypatch.setattr(app, "_compute_heikin_ashi", lambda frame: ha_bars)
    monkeypatch.setattr(app, "_extract_ltp", lambda *args, **kwargs: 112.0)
    monkeypatch.setattr(app, "_complete_persistent_trade", lambda *args, **kwargs: None)
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine["strategy_mode"] = STRATEGY_HEIKIN_ASHI
    engine["last_signal_ts"] = timestamps[2].isoformat()
    engine["open_trade"] = {
        "instrument": "NFO:NIFTYATMCE",
        "entry_option": 100.0,
        "quantity": 130,
        "stop_price": 80.0,
        "max_ltp": 100.0,
        "signal_time": timestamps[2].isoformat(),
        "entry_ha_color": "GREEN",
        "strategy_mode": STRATEGY_HEIKIN_ASHI,
        "trade_mode": "PAPER",
    }

    result = _run_engine_for(
        object(), UNDERLYINGS["NIFTY"], engine,
        wall_time(9, 16), wall_time(15, 0), wall_time(15, 15), 1, "PAPER", False,
    )

    assert result["open_trade"] is None
    assert result["trades_taken"] == 1
    assert result["realized_pnl"] == 1560.0
    assert any("EXIT HA_COLOR_REVERSAL" in event for event in result["events"])


def test_index_runs_ema_and_heikin_ashi_in_parallel_with_independent_modes(monkeypatch) -> None:
    calls: list[tuple[str, str, bool]] = []
    fetch_calls = 0

    def fake_fetch(*args, **kwargs):
        nonlocal fetch_calls
        fetch_calls += 1
        return pd.DataFrame()

    def fake_run_engine_for(*, engine, trade_mode, real_mode_armed, **kwargs):
        strategy = str(engine["strategy_mode"])
        calls.append((strategy, trade_mode, real_mode_armed))
        if strategy == STRATEGY_HEIKIN_ASHI:
            engine["open_trade"] = {"instrument": "NFO:NIFTYHACE", "trade_mode": "PAPER"}
            engine["realized_pnl"] = 25.0
            engine["trades_taken"] = 4
            engine["entry_status"] = "HA running"
        else:
            engine["open_trade"] = {"instrument": "NFO:NIFTYEMACE", "trade_mode": trade_mode}
            engine["realized_pnl"] = 50.0
            engine["trades_taken"] = 2
            engine["entry_status"] = "EMA running"
        return engine

    monkeypatch.setattr(app, "_run_engine_for", fake_run_engine_for)
    monkeypatch.setattr(app, "_fetch_spot_5m", fake_fetch)
    engine = _default_state_for(UNDERLYINGS["NIFTY"])

    result = _run_parallel_index_engines(
        object(), UNDERLYINGS["NIFTY"], engine,
        wall_time(9, 16), wall_time(15, 0), wall_time(15, 15), 1, "REAL", True,
    )

    assert calls == [
        (app.STRATEGY_EMA, "REAL", True),
        (STRATEGY_HEIKIN_ASHI, "PAPER", False),
    ]
    assert fetch_calls == 1
    assert result["open_trade"]["instrument"] == "NFO:NIFTYEMACE"
    assert result["ha_open_trade"]["instrument"] == "NFO:NIFTYHACE"
    assert result["realized_pnl"] == 50.0
    assert result["ha_realized_pnl"] == 25.0
    assert result["trades_taken"] == 2
    assert result["ha_trades_taken"] == 4


def test_parallel_upgrade_preserves_an_existing_heikin_ashi_position(monkeypatch) -> None:
    seen_open_trades: list[tuple[str, object]] = []

    def fake_run_engine_for(*, engine, **kwargs):
        seen_open_trades.append((str(engine["strategy_mode"]), engine.get("open_trade")))
        return engine

    monkeypatch.setattr(app, "_run_engine_for", fake_run_engine_for)
    monkeypatch.setattr(app, "_fetch_spot_5m", lambda *args, **kwargs: pd.DataFrame())
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine["strategy_mode"] = STRATEGY_HEIKIN_ASHI
    engine["last_signal_ts"] = "2026-08-19T09:50:00+05:30"
    engine["open_trade"] = {
        "instrument": "NFO:NIFTYHACE",
        "strategy_mode": STRATEGY_HEIKIN_ASHI,
        "trade_mode": "PAPER",
    }

    result = _run_parallel_index_engines(
        object(), UNDERLYINGS["NIFTY"], engine,
        wall_time(9, 16), wall_time(15, 0), wall_time(15, 15), 1, "PAPER", False,
    )

    assert seen_open_trades[0] == (app.STRATEGY_EMA, None)
    assert seen_open_trades[1][0] == STRATEGY_HEIKIN_ASHI
    assert seen_open_trades[1][1]["instrument"] == "NFO:NIFTYHACE"
    assert result["ha_open_trade"]["instrument"] == "NFO:NIFTYHACE"


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
