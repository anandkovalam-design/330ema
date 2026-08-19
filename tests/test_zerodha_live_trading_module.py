import pandas as pd
from datetime import datetime, time as wall_time, timedelta
from types import SimpleNamespace

from aadithya_quantlab.zerodha_live_trading import main
from aadithya_quantlab.zerodha_live_trading import app
from aadithya_quantlab.zerodha_live_trading.app import (
    ENTRY_CUTOFF_IST,
    DEFAULT_INDEX_EXPIRY_MODE,
    HEIKIN_ASHI_STOP_POINTS,
    MANDATORY_EXIT_IST,
    STRATEGY_HEIKIN_ASHI,
    _build_heikin_ashi_chart,
    _build_price_chart,
    _completed_5m_bars,
    _compute_heikin_ashi,
    _compute_signals,
    _complete_zerodha_login_callback,
    _credential_preview,
    _clear_login_credentials,
    _default_state_for,
    _apply_pending_hard_stops,
    _apply_open_trade_quote,
    _collect_live_quote_instruments,
    _enforce_universal_trail_start,
    _entries_blocked_for_expiry_day,
    _front_future_row,
    _format_ist_timestamp,
    _has_real_trade_recovery,
    _has_opposite_heikin_ashi_candle,
    _latest_confirmed_heikin_ashi_signal,
    _contract_lot_size,
    _load_login_credentials,
    _load_risk_settings,
    _migrate_natural_gas_risk_defaults,
    _mcx_order_quantity,
    _mcx_position_size_details,
    _trade_pnl_quantity,
    MCX_CONTRACT_SPECS,
    _run_parallel_index_engines,
    _request_hard_stop_from_controls,
    _refresh_live_signal_quote,
    _reset_durable_paper_trade_state,
    _sync_scaled_metal_risk_settings,
    _run_engine_for,
    _select_option_contract,
    _save_login_credentials,
    _save_risk_settings,
    _safe_trade_frame,
    _update_trailing_stop,
    UNDERLYINGS,
)
from aadithya_quantlab.zerodha_live_trading.persistence import (
    acknowledge_hard_stop_requests,
    load_hard_stop_requests,
    request_hard_stop,
)


def test_dashboard_timestamp_formatter_converts_all_values_to_ist() -> None:
    assert _format_ist_timestamp("2026-08-18T12:30:00+00:00") == "18 Aug 2026, 06:00:00 PM IST"
    assert _format_ist_timestamp("2026-08-18T18:00:00+05:30") == "18 Aug 2026, 06:00:00 PM IST"
    assert _format_ist_timestamp("2026-08-18T18:00:00") == "18 Aug 2026, 06:00:00 PM IST"
    assert _format_ist_timestamp(None) == "—"


def test_trade_history_displays_human_readable_strategy() -> None:
    frame = _safe_trade_frame(
        [
            {
                "trade_date": "2026-08-18",
                "mode": "PAPER",
                "strategy": STRATEGY_HEIKIN_ASHI,
                "underlying": "NIFTY",
                "option_symbol": "NFO:NIFTYTESTCE",
                "side": "CALL",
                "quantity": 65,
                "entry_time": "2026-08-18T10:00:00+05:30",
                "exit_time": None,
                "entry_price": 100.0,
                "exit_price": None,
                "realized_pnl": None,
                "exit_reason": None,
            }
        ]
    )

    assert frame.loc[0, "strategy"] == "Heikin-Ashi reversal"


class _FakeSessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def test_durable_paper_reset_callback_runs_before_widget_render(monkeypatch) -> None:
    fake_state = _FakeSessionState(
        engine_state={
            name: {**_default_state_for(config), "enabled": False, "trades_taken": 2}
            for name, config in UNDERLYINGS.items()
        },
        recovery_metadata={"saved_at": "2026-08-18T10:00:00+05:30"},
        reconciliation_report={"status": "MATCHED"},
    )
    persisted = []
    monkeypatch.setattr(app.st, "session_state", fake_state)
    monkeypatch.setattr(app, "_persist_engine_state", lambda state: persisted.append(state))

    _reset_durable_paper_trade_state()

    assert fake_state["paper_reset_completed"] is True
    assert fake_state["recovery_metadata"] == {}
    assert fake_state["reconciliation_report"] is None
    assert persisted == [fake_state["engine_state"]]
    for name in UNDERLYINGS:
        assert fake_state[f"{name.lower()}_turn_off"] is False
        assert fake_state["engine_state"][name]["trades_taken"] == 0


def test_durable_paper_reset_remains_blocked_for_real_recovery() -> None:
    engine_state = {"NIFTY": {"reconciliation_required": True}}
    assert _has_real_trade_recovery(engine_state) is True


def test_live_trading_schedule_runs_entries_to_1500_and_exits_at_1515() -> None:
    assert ENTRY_CUTOFF_IST == "15:00"
    assert MANDATORY_EXIT_IST == "15:15"


def test_nifty_and_sensex_default_to_current_week_expiry() -> None:
    assert DEFAULT_INDEX_EXPIRY_MODE == "Current week"


def test_mcx_schedule_runs_entries_to_2230_and_exits_at_2250() -> None:
    for name in ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"):
        assert UNDERLYINGS[name].entry_cutoff_ist == "22:30"
        assert UNDERLYINGS[name].mandatory_exit_ist == "22:50"


def test_natural_gas_uses_dedicated_risk_defaults() -> None:
    state = _default_state_for(UNDERLYINGS["NATURALGAS"])

    assert state["sl_points"] == 2.0
    assert state["sl_to_cost_profit_pct"] == 0.25
    assert state["trail_after_profit_pct"] == 0.30
    assert state["mfe_giveback_pct"] == 0.275
    assert state["trail_trigger_points"] == 3.0
    assert state["trail_step_points"] == 1.0


def test_natural_gas_legacy_defaults_are_migrated_without_overwriting_custom_values() -> None:
    legacy_state = {
        "NATURALGAS": {
            "sl_points": 20.0,
            "sl_to_cost_profit_pct": 0.30,
            "trail_after_profit_pct": 0.50,
            "mfe_giveback_pct": 0.275,
            "trail_trigger_points": 15.0,
            "trail_step_points": 5.0,
        }
    }
    assert _migrate_natural_gas_risk_defaults(legacy_state) is True
    assert legacy_state["NATURALGAS"] == {
        "sl_points": 2.0,
        "sl_to_cost_profit_pct": 0.25,
        "trail_after_profit_pct": 0.30,
        "mfe_giveback_pct": 0.275,
        "trail_trigger_points": 3.0,
        "trail_step_points": 1.0,
    }

    custom_state = {"NATURALGAS": {**legacy_state["NATURALGAS"], "mfe_giveback_pct": 0.20}}
    assert _migrate_natural_gas_risk_defaults(custom_state) is False
    assert custom_state["NATURALGAS"]["mfe_giveback_pct"] == 0.20


def test_mcx_position_size_separates_lots_broker_quantity_and_contract_size() -> None:
    state = {"lot_size": 1}
    silver = _mcx_position_size_details(
        "SILVER", state, {"quantity": 1, "lots": 1, "exchange_lot_size": 1}
    )
    crude = _mcx_position_size_details(
        "CRUDEOIL", state, {"quantity": 1, "lots": 1, "exchange_lot_size": 1}
    )

    assert silver == (1, 1, "30 kg")
    assert crude == (1, 1, "100 barrels")


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


def test_trailing_start_is_normalized_to_30_percent_for_every_instrument() -> None:
    engine_state = {
        name: {**_default_state_for(config), "trail_after_profit_pct": 0.75}
        for name, config in UNDERLYINGS.items()
    }

    assert _enforce_universal_trail_start(engine_state) is True
    assert all(state["trail_after_profit_pct"] == 0.30 for state in engine_state.values())
    assert _enforce_universal_trail_start(engine_state) is False


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


def test_commodity_live_signal_quote_refreshes_from_front_future(monkeypatch) -> None:
    requested: list[str] = []
    engine = {"signal_symbol": "SILVER26SEPFUT"}

    def fake_extract_ltp(kite, instrument: str) -> float:
        requested.append(instrument)
        return 234567.0

    monkeypatch.setattr(app, "_extract_ltp", fake_extract_ltp)

    _refresh_live_signal_quote(object(), UNDERLYINGS["SILVER"], engine)

    assert requested == ["MCX:SILVER26SEPFUT"]
    assert engine["live_signal_ltp"] == 234567.0
    assert engine["live_signal_error"] == ""
    assert engine["live_signal_time"] is not None


def test_commodity_live_signal_quote_failure_keeps_engine_running(monkeypatch) -> None:
    engine = {"signal_symbol": "CRUDEOIL26SEPFUT", "live_signal_ltp": 7000.0}
    monkeypatch.setattr(
        app,
        "_extract_ltp",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quote unavailable")),
    )

    _refresh_live_signal_quote(object(), UNDERLYINGS["CRUDEOIL"], engine)

    assert engine["live_signal_ltp"] == 7000.0
    assert engine["live_signal_error"] == "RuntimeError: quote unavailable"


def test_open_trade_full_quote_exposes_depth_and_last_trade_time() -> None:
    trade: dict = {}
    quote = {
        "last_price": 4618.0,
        "timestamp": "2026-08-19T19:20:05+05:30",
        "last_trade_time": "2026-08-19T18:47:12+05:30",
        "depth": {
            "buy": [{"price": 4575.0, "quantity": 1}],
            "sell": [{"price": 4680.0, "quantity": 1}],
        },
    }

    assert _apply_open_trade_quote(trade, "MCX:SILVER26AUG229000CE", quote) == 4618.0
    assert trade["best_bid"] == 4575.0
    assert trade["best_ask"] == 4680.0
    assert trade["last_trade_time"] == "2026-08-19T18:47:12+05:30"


def test_live_quotes_are_collected_for_one_batch_request() -> None:
    expiry = (pd.Timestamp.now(tz="Asia/Kolkata") + timedelta(days=10)).date()
    rows = [
        {
            "name": name,
            "tradingsymbol": f"{name}26SEPFUT",
            "instrument_type": "FUT",
            "expiry": expiry,
            "instrument_token": index,
        }
        for index, name in enumerate(("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"), 1)
    ]
    state = {name: _default_state_for(config) for name, config in UNDERLYINGS.items()}
    state["SILVER"]["open_trade"] = {"instrument": "MCX:SILVER26AUG229000CE"}

    instruments = _collect_live_quote_instruments(state, rows, 0)

    assert "NSE:NIFTY 50" in instruments
    assert "BSE:SENSEX" in instruments
    assert "MCX:SILVER26SEPFUT" in instruments
    assert "MCX:SILVER26AUG229000CE" in instruments
    assert len(instruments) == 7


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


def test_mcx_pnl_uses_physical_multiplier_without_changing_broker_quantity() -> None:
    trade = {"quantity": 1}
    assert _trade_pnl_quantity(UNDERLYINGS["CRUDEOIL"], trade) == 100
    assert _trade_pnl_quantity(UNDERLYINGS["NATURALGAS"], trade) == 1250
    assert _trade_pnl_quantity(UNDERLYINGS["GOLD"], trade) == 100
    assert _trade_pnl_quantity(UNDERLYINGS["SILVER"], trade) == 30
    assert trade["quantity"] == 1

    assert _trade_pnl_quantity(UNDERLYINGS["NIFTY"], {"quantity": 130}) == 130


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
    engine = _default_state_for(UNDERLYINGS["NATURALGAS"])
    trade = {"entry_option": 100.0, "stop_price": 80.0, "max_ltp": 100.0}

    assert _update_trailing_stop(trade, engine, 125.0) == "SL_TO_COST"
    assert trade["stop_price"] == 100.0
    assert _update_trailing_stop(trade, engine, 120.0) is None
    assert trade["stop_price"] == 100.0


def test_trailing_stop_starts_at_30_percent_even_with_stale_engine_setting() -> None:
    engine = _default_state_for(UNDERLYINGS["NIFTY"])
    engine["trail_after_profit_pct"] = 0.75
    trade = {"entry_option": 100.0, "stop_price": 80.0, "max_ltp": 100.0}

    assert _update_trailing_stop(trade, engine, 130.0) in {"MFE_TRAIL", "STEP_TRAIL"}
    assert trade["trailing_active"] is True
    raised_stop = trade["stop_price"]
    assert _update_trailing_stop(trade, engine, 120.0) is None
    assert trade["stop_price"] == raised_stop


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


def test_hard_stop_request_crosses_streamlit_sessions(tmp_path, monkeypatch) -> None:
    command_path = tmp_path / "hard_stop_commands.json"
    controls_session_state = _default_state_for(UNDERLYINGS["SILVER"])
    live_session_state = {"SILVER": _default_state_for(UNDERLYINGS["SILVER"])}
    monkeypatch.setattr(app, "HARD_STOP_COMMAND_PATH", command_path)

    controls_session_state["hard_stop_requested"] = True
    request_hard_stop(command_path, underlying="SILVER", requested_at=datetime.now())

    assert live_session_state["SILVER"]["hard_stop_requested"] is False
    assert _apply_pending_hard_stops(live_session_state) == {"SILVER"}
    assert live_session_state["SILVER"]["enabled"] is False
    assert live_session_state["SILVER"]["hard_stop_requested"] is True
    acknowledge_hard_stop_requests(command_path, {"SILVER"})
    assert load_hard_stop_requests(command_path) == {}


def test_hard_stop_control_callback_updates_toggle_before_widget_render(monkeypatch) -> None:
    engine_state = {"SILVER": _default_state_for(UNDERLYINGS["SILVER"])}
    session_state = {"engine_state": engine_state, "silver_turn_off": False}
    requested: list[str] = []
    persisted: list[dict[str, dict]] = []
    monkeypatch.setattr(app, "st", SimpleNamespace(session_state=session_state))
    monkeypatch.setattr(
        app,
        "request_hard_stop",
        lambda path, *, underlying, requested_at: requested.append(underlying),
    )
    monkeypatch.setattr(app, "_persist_engine_state", lambda state: persisted.append(state))

    _request_hard_stop_from_controls("SILVER")

    assert session_state["silver_turn_off"] is True
    assert engine_state["SILVER"]["enabled"] is False
    assert engine_state["SILVER"]["hard_stop_requested"] is True
    assert requested == ["SILVER"]
    assert persisted == [engine_state]


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


def test_zerodha_callback_exchanges_token_and_cleans_url(monkeypatch) -> None:
    query_params = {"request_token": "fresh-request-token", "status": "success"}
    session_state = {
        "connected": False,
        "api_key": "",
        "access_token": "",
        "connection_restored": False,
        "zerodha_login_notice": "",
    }
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app,
        "st",
        SimpleNamespace(query_params=query_params, session_state=session_state),
    )
    monkeypatch.setattr(
        app,
        "_load_login_credentials",
        lambda: {"api_key": "configured-key", "api_secret": "configured-secret"},
    )
    monkeypatch.setattr(
        app,
        "_exchange_zerodha_request_token",
        lambda api_key, api_secret, request_token: ("daily-access-token", {"user_id": "AB1234"}),
    )
    monkeypatch.setattr(
        app,
        "_save_connection_for_today",
        lambda api_key, access_token: saved.append((api_key, access_token)),
    )

    assert _complete_zerodha_login_callback() is True
    assert query_params == {}
    assert session_state["connected"] is True
    assert session_state["api_key"] == "configured-key"
    assert session_state["access_token"] == "daily-access-token"
    assert session_state["zerodha_login_notice"] == "Zerodha authentication successful: AB1234"
    assert saved == [("configured-key", "daily-access-token")]


def test_zerodha_callback_reports_failure_and_cleans_token(monkeypatch) -> None:
    query_params = {"request_token": "invalid-request-token"}
    session_state = {"connected": True, "zerodha_login_notice": ""}
    monkeypatch.setattr(
        app,
        "st",
        SimpleNamespace(query_params=query_params, session_state=session_state),
    )
    monkeypatch.setattr(app, "_load_login_credentials", lambda: None)

    assert _complete_zerodha_login_callback() is False
    assert query_params == {}
    assert session_state["connected"] is False
    assert session_state["zerodha_login_notice"].startswith("Zerodha authentication failed:")


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
