from __future__ import annotations

import importlib.util
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from quantlab.strategy_ema3_30_staged import (
    EXIT_MODE_FIXED_TARGET,
    EXIT_MODE_STAGED_TRAILING,
    INTRABAR_POLICY_STOP_FIRST,
    ExecutionCostConfig,
    StrategyConfig,
    TIMESTAMP_SEMANTICS,
    select_groww_fno_contract,
    simulate_day,
)


def _underlying_frame(day: date, rows: list[tuple[str, float, float, float, float, bool, bool]]) -> pd.DataFrame:
    items = []
    for hhmm, open_px, high_px, low_px, close_px, cross_up, cross_down in rows:
        ts = pd.Timestamp(f"{day.isoformat()} {hhmm}:00")
        items.append(
            {
                "timestamp": ts,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "cross_up": cross_up,
                "cross_down": cross_down,
            }
        )
    return pd.DataFrame(items)


def _lookup(day: date, rows: list[tuple[str, float, float, float, float]]) -> dict[pd.Timestamp, dict[str, float]]:
    data: dict[pd.Timestamp, dict[str, float]] = {}
    for hhmm, open_px, high_px, low_px, close_px in rows:
        ts = pd.Timestamp(f"{day.isoformat()} {hhmm}:00")
        data[ts] = {
            "open": open_px,
            "high": high_px,
            "low": low_px,
            "close": close_px,
        }
    return data


def test_select_contract_separates_call_and_put() -> None:
    d = date(2026, 7, 31)
    chain = [
        {"symbol": "NIFTY26AUG25000CE", "option_type": "CE", "strike": 25000, "expiry": "2026-08-04"},
        {"symbol": "NIFTY26AUG25000PE", "option_type": "PE", "strike": 25000, "expiry": "2026-08-04"},
    ]
    call_contract = select_groww_fno_contract("CALL", spot_price=25020, trade_date=d, option_chain=chain)
    put_contract = select_groww_fno_contract("PUT", spot_price=25020, trade_date=d, option_chain=chain)
    assert call_contract is not None
    assert put_contract is not None
    assert str(call_contract["symbol"]).endswith("CE")
    assert str(put_contract["symbol"]).endswith("PE")


def test_simulation_uses_call_stream_for_call_signal() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 120, 95, 110)])
    call_lookup[pd.Timestamp(f"{d.isoformat()} 09:25:00")]["contract_symbol"] = "CALL_CONTRACT"
    put_lookup = _lookup(d, [("09:25", 10, 11, 9, 10)])
    put_lookup[pd.Timestamp(f"{d.isoformat()} 09:25:00")]["contract_symbol"] = "PUT_CONTRACT"
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1)
    assert len(trades) == 1
    assert trades[0].side == "CALL"
    assert trades[0].entry_option > 100


def test_prevents_overlapping_positions() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 108, 103, 107, False, True),
            ("09:30", 107, 109, 106, 108, False, False),
            ("09:35", 108, 109, 100, 101, False, False),
            ("09:40", 101, 103, 100, 102, False, False),
        ],
    )
    call_lookup = _lookup(
        d,
        [
            ("09:25", 100, 110, 95, 106),
            ("09:30", 106, 108, 96, 107),
            ("09:35", 107, 107, 88, 90),
            ("09:40", 90, 92, 89, 90),
        ],
    )
    put_lookup = _lookup(
        d,
        [
            ("09:25", 100, 101, 99, 100),
            ("09:30", 100, 101, 99, 100),
            ("09:35", 100, 101, 99, 100),
            ("09:40", 100, 101, 99, 100),
        ],
    )
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1)
    assert len(trades) == 1


def test_contract_symbol_must_match_candle_source() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 120, 95, 110)])
    call_lookup[pd.Timestamp(f"{d.isoformat()} 09:25:00")]["contract_symbol"] = "WRONG_CONTRACT"
    put_lookup = _lookup(d, [("09:25", 10, 11, 9, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1)
    assert trades == []


def test_move_stop_to_entry_at_30pct_only() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
            ("09:30", 105, 106, 100, 102, False, False),
        ],
    )
    call_lookup = _lookup(
        d,
        [
            ("09:25", 100, 131, 110, 125),
            ("09:30", 120, 122, 99, 100),
        ],
    )
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10), ("09:30", 10, 10, 10, 10)])
    cfg = StrategyConfig(no_fixed_target=True, target_pct=None, exit_mode=EXIT_MODE_STAGED_TRAILING)
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1, config=cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "BREAKEVEN"


def test_dynamic_trailing_starts_only_after_50pct_profit() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
            ("09:30", 105, 106, 100, 101, False, False),
        ],
    )
    call_lookup = _lookup(
        d,
        [
            ("09:25", 100, 149, 120, 140),
            ("09:30", 140, 140, 99, 100),
        ],
    )
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10), ("09:30", 10, 10, 10, 10)])
    cfg = StrategyConfig(no_fixed_target=True, target_pct=None, exit_mode=EXIT_MODE_STAGED_TRAILING)
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1, config=cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "BREAKEVEN"


def test_forced_square_off_time_applied() -> None:
    d = date(2026, 7, 31)
    cfg = StrategyConfig(
        no_fixed_target=True,
        target_pct=None,
        exit_mode=EXIT_MODE_STAGED_TRAILING,
        forced_square_off_time=time(9, 30),
        entry_end_time=time(9, 30),
    )
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
            ("09:30", 105, 106, 104, 105, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 101, 100, 101), ("09:30", 101, 102, 100, 102)])
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10), ("09:30", 10, 10, 10, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1, config=cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "FORCED_SQUARE_OFF"


def test_trade_restricted_to_same_trading_date() -> None:
    d = date(2026, 7, 31)
    next_day = d + timedelta(days=1)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
        ],
    )
    call_lookup = _lookup(
        d,
        [("09:25", 100, 110, 95, 106)],
    )
    call_lookup.update(_lookup(next_day, [("09:25", 1000, 1200, 900, 1100)]))
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1)
    assert len(trades) == 1
    assert trades[0].entry_time.date() == d
    assert trades[0].exit_time.date() == d


def test_entry_uses_open_price_to_avoid_lookahead() -> None:
    d = date(2026, 7, 31)
    costs = ExecutionCostConfig(entry_slippage_bps=0.0, exit_slippage_bps=0.0)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 200, 90, 160)])
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1, costs=costs)
    assert len(trades) == 1
    assert trades[0].entry_option == 100.0


def test_timestamp_semantics_exposed_in_trade_result() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 110, 95, 106)])
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1)
    assert len(trades) == 1
    assert trades[0].timestamp_semantics == TIMESTAMP_SEMANTICS
    assert trades[0].signal_candle_close_time.tzinfo is not None
    assert trades[0].entry_candle_open_time.tzinfo is not None
    assert trades[0].candle_interval == "5m"


def test_exit_mode_conflict_rejected() -> None:
    try:
        StrategyConfig(exit_mode=EXIT_MODE_STAGED_TRAILING, no_fixed_target=False, target_pct=0.2)
    except ValueError as exc:
        assert "STAGED_TRAILING" in str(exc)
    else:
        raise AssertionError("Expected conflict validation error")


def test_intrabar_policy_stop_first_when_both_hit() -> None:
    d = date(2026, 7, 31)
    cfg = StrategyConfig(
        exit_mode=EXIT_MODE_FIXED_TARGET,
        no_fixed_target=False,
        target_pct=0.05,
        intrabar_execution_policy=INTRABAR_POLICY_STOP_FIRST,
    )
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 106, 89, 102)])
    put_lookup = _lookup(d, [("09:25", 10, 11, 9, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1, config=cfg)
    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP_LOSS"


def test_trading_costs_are_applied() -> None:
    d = date(2026, 7, 31)
    frame = _underlying_frame(
        d,
        [
            ("09:15", 100, 101, 99, 100, False, False),
            ("09:20", 100, 105, 99, 104, True, False),
            ("09:25", 104, 106, 103, 105, False, False),
            ("09:30", 105, 120, 104, 118, False, False),
        ],
    )
    call_lookup = _lookup(d, [("09:25", 100, 101, 99, 100), ("09:30", 110, 120, 109, 118)])
    put_lookup = _lookup(d, [("09:25", 10, 10, 10, 10), ("09:30", 10, 10, 10, 10)])
    trades = simulate_day(frame, call_lookup, put_lookup, lot_size=1, lots=1)
    assert len(trades) == 1
    assert trades[0].total_cost_currency > 0
    assert trades[0].net_pnl_currency < trades[0].gross_pnl_currency


class _FakePaperOnlySdk:
    def __init__(self) -> None:
        self.create_order_calls = 0
        self.modify_order_calls = 0
        self.cancel_order_calls = 0

    def authenticate(self, api_key: str, api_secret: str, access_token: str) -> None:
        assert api_key
        assert api_secret
        assert access_token

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int):
        del symbol, interval, lookback_bars
        base = pd.Timestamp("2026-07-31 09:15:00")
        rows = []
        for i in range(40):
            ts = base + pd.Timedelta(minutes=5 * i)
            close = 25000.0 + i
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": close - 1.0,
                    "high": close + 1.0,
                    "low": close - 2.0,
                    "close": close,
                }
            )
        return rows

    def list_option_contracts(self, underlying: str, trade_date: date):
        del underlying, trade_date
        return [
            {"symbol": "NIFTY26AUG25000CE", "option_type": "CE", "strike": 25000, "expiry": "2026-08-04"},
            {"symbol": "NIFTY26AUG25000PE", "option_type": "PE", "strike": 25000, "expiry": "2026-08-04"},
        ]

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int):
        del interval, lookback_bars
        base = pd.Timestamp("2026-07-31 09:15:00")
        rows = []
        px = 100.0 if contract_symbol.endswith("CE") else 90.0
        for i in range(40):
            ts = base + pd.Timedelta(minutes=5 * i)
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": px,
                    "high": px + 2.0,
                    "low": px - 2.0,
                    "close": px + 1.0,
                }
            )
            px += 0.5
        return rows

    def get_ltp(self, instrument: str):
        return {"ltp": 120.0, "timestamp": pd.Timestamp("2026-07-31 09:30:00").isoformat()}

    def create_order(self, *args, **kwargs):
        self.create_order_calls += 1
        raise AssertionError("create_order must never be called in paper mode")

    def modify_order(self, *args, **kwargs):
        self.modify_order_calls += 1
        raise AssertionError("modify_order must never be called in paper mode")

    def cancel_order(self, *args, **kwargs):
        self.cancel_order_calls += 1
        raise AssertionError("cancel_order must never be called in paper mode")


def test_realtime_runner_never_calls_order_endpoints(tmp_path, monkeypatch):
    sdk = _FakePaperOnlySdk()
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_API_SECRET", "s")
    monkeypatch.setenv("GROWW_ACCESS_TOKEN", "t")

    from groww_engine.config import RiskConfig
    from groww_engine.paper_realtime_runner import PaperRealtimeRunner, PaperRunnerConfig

    cfg = PaperRunnerConfig(
        sqlite_path=str(tmp_path / "paper.db"),
        csv_path=str(tmp_path / "paper.csv"),
        poll_seconds=1,
        max_trades_per_day=1,
    )
    runner = PaperRealtimeRunner(sdk=sdk, config=cfg, risk=RiskConfig(max_open_positions=1, max_trades_per_day=1))
    runner.authenticate()
    runner.run_cycle()

    assert (tmp_path / "paper.db").exists()
    assert sdk.create_order_calls == 0
    assert sdk.modify_order_calls == 0
    assert sdk.cancel_order_calls == 0
