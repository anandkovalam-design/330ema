from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from quantlab.apex.agents.base import DecisionAction
from quantlab.apex.backtest.engine import ApexBacktestConfig, ApexProBacktestEngine
from quantlab.apex.orchestration.state import PipelineState


IST = ZoneInfo("Asia/Kolkata")


@dataclass
class StepSpec:
    action: str = "NO_TRADE"
    market_data_status: str = "DATA_VALID"
    risk_status: str = "REJECTED"
    risk_agent_status: str | None = None
    risk_reasons: list[str] | None = None
    skeptic_veto: bool = False
    skeptic_status: str | None = None
    skeptic_reasons: list[str] | None = None
    options_approved: bool = False
    options_status: str | None = None
    options_reasons: list[str] | None = None
    executed: bool = False
    symbol: str = "NIFTYTEST25000CE"
    strike: int = 25000
    expiry: str = "2026-07-16"
    bull_confidence: float = 0.7
    bear_confidence: float = 0.2
    market_state: str = "directional_bullish"
    apex_confidence: float = 0.7


class ScriptedPipeline:
    def __init__(self, audit_dir: Path, script: list[StepSpec]) -> None:
        self.script = script
        self.index = 0
        self.state = PipelineState()
        self.audit_dir = audit_dir
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def run(self, snapshot):
        spec = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1

        reports = {
            "market_data": {
                "validation_status": spec.market_data_status,
                "reason_codes": [] if spec.market_data_status == "DATA_VALID" else [spec.market_data_status],
            },
            "market_state": {"selected_state": spec.market_state},
            "bull_research": {"confidence": spec.bull_confidence},
            "bear_research": {"confidence": spec.bear_confidence},
            "skeptic": {
                "status": spec.skeptic_status,
                "veto": spec.skeptic_veto,
                "reason_codes": spec.skeptic_reasons or [],
            },
            "risk": {
                "status": spec.risk_agent_status,
                "approval": spec.risk_status,
                "reason_codes": spec.risk_reasons or [],
            },
            "options": {
                "status": spec.options_status,
                "approved": spec.options_approved,
                "reason_codes": spec.options_reasons or [],
                "selection": {
                    "contract_symbol": spec.symbol,
                    "strike": spec.strike,
                    "expiry": spec.expiry,
                    "option_type": "CE" if spec.action == "TRADE_CALL" else "PE",
                },
            },
            "decision": {
                "action": spec.action,
                "confidence": spec.apex_confidence,
            },
            "paper_execution": {
                "executed": spec.executed,
            },
        }
        # Persist minimal audit marker for determinism checks.
        marker = self.audit_dir / f"{snapshot.snapshot_id}.jsonl"
        marker.write_text('{"report_name":"decision"}\n', encoding="utf-8")
        return SimpleNamespace(action=DecisionAction(spec.action), reports=reports)


def _write_underlying(path: Path, start: datetime, bars: int, *, split_days: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    price = 25000.0
    ts = start
    for i in range(bars):
        if split_days and i == bars // 2:
            ts = (start + timedelta(days=1)).replace(hour=9, minute=15)
        close = price + (2.0 if i % 2 == 0 else -1.0)
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "open": price,
                "high": max(price, close) + 1.0,
                "low": min(price, close) - 1.0,
                "close": close,
                "volume": 1000 + i,
            }
        )
        price = close
        ts = ts + timedelta(minutes=5)
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return path


def _write_options(path: Path, timestamps: list[datetime], symbol: str = "NIFTYTEST25000CE", option_type: str = "CE") -> None:
    rows = []
    price = 100.0
    for ts in timestamps:
        price += 1.0
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "symbol": symbol,
                "strike": 25000,
                "expiry": "2026-07-16",
                "option_type": option_type,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1500,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _run_engine(tmp_path: Path, script: list[StepSpec], *, with_options: bool = True, no_proxy: bool = False, split_days: bool = False, time_exit_bars: int = 2, max_trades: int = 3):
    underlying = _write_underlying(tmp_path / "nifty_5m.csv", datetime(2026, 7, 13, 9, 15, tzinfo=IST), 12, split_days=split_days)
    options_dir = tmp_path / "options"
    options_dir.mkdir(parents=True, exist_ok=True)
    if with_options:
        ts = pd.to_datetime(pd.read_csv(underlying)["timestamp"]).tolist()
        ts = [pd.Timestamp(x).to_pydatetime() for x in ts]
        _write_options(options_dir / "opt.csv", ts, symbol="NIFTYTEST25000CE", option_type="CE")
        _write_options(options_dir / "opt_put.csv", ts, symbol="NIFTYTEST25000PE", option_type="PE")

    def factory(audit_dir: Path):
        return ScriptedPipeline(audit_dir, script)

    config = ApexBacktestConfig(
        underlying_csv=underlying,
        options_dir=options_dir,
        no_proxy_options=no_proxy,
        max_trades_per_day=max_trades,
        audit_dir=tmp_path / "audit",
        trades_csv=tmp_path / "reports" / "trades.csv",
        summary_md=tmp_path / "reports" / "summary.md",
        time_exit_bars=time_exit_bars,
    )
    engine = ApexProBacktestEngine(config, pipeline_factory=factory)
    return engine.run(), config


def test_valid_call_decision_creates_call_trade(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True, symbol="NIFTYTEST25000CE"),
        StepSpec(),
    ]
    result, _ = _run_engine(tmp_path, script)
    assert not result.trades.empty
    assert result.trades.iloc[0]["direction"] == "CALL"


def test_valid_put_decision_creates_put_trade(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_PUT", risk_status="APPROVED", options_approved=True, executed=True, symbol="NIFTYTEST25000PE"),
        StepSpec(),
    ]
    result, _ = _run_engine(tmp_path, script)
    assert not result.trades.empty
    assert result.trades.iloc[0]["direction"] == "PUT"


def test_watch_creates_no_trade(tmp_path: Path):
    result, _ = _run_engine(tmp_path, [StepSpec(action="WATCH")])
    assert result.trades.empty


def test_no_trade_creates_no_trade(tmp_path: Path):
    result, _ = _run_engine(tmp_path, [StepSpec(action="NO_TRADE")])
    assert result.trades.empty


def test_data_invalid_stops_pipeline(tmp_path: Path):
    result, _ = _run_engine(tmp_path, [StepSpec(market_data_status="DATA_INVALID")])
    assert result.stopped_early is True
    assert result.stop_reason == "DATA_INVALID"


def test_skeptic_veto_prevents_trade(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True, skeptic_veto=True),
    ]
    result, _ = _run_engine(tmp_path, script)
    assert result.trades.empty


def test_risk_rejection_prevents_trade(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="REJECTED", risk_reasons=["DAILY_LIMIT"], options_approved=True, executed=True),
    ]
    result, _ = _run_engine(tmp_path, script)
    assert result.trades.empty


def test_missing_option_data_rejected_with_no_proxy(tmp_path: Path):
    script = [StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True)]
    result, _ = _run_engine(tmp_path, script, with_options=False, no_proxy=True)
    assert result.trades.empty


def test_missing_option_data_allowed_and_labeled_proxy(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True),
        StepSpec(),
    ]
    result, _ = _run_engine(tmp_path, script, with_options=False, no_proxy=False)
    assert len(result.trades) == 1
    assert bool(result.trades.iloc[0]["option_proxy_used"]) is True
    assert str(result.trades.iloc[0]["symbol"]).startswith("PROXY_")


def test_one_open_position_at_a_time_enforced(tmp_path: Path):
    script = [StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True)] * 8
    result, _ = _run_engine(tmp_path, script, time_exit_bars=10)
    assert not result.trades.empty
    frame = result.trades.sort_values("entry_time").reset_index(drop=True)
    for idx in range(1, len(frame)):
        prev_exit = pd.Timestamp(frame.loc[idx - 1, "exit_time"])
        next_entry = pd.Timestamp(frame.loc[idx, "entry_time"])
        assert next_entry >= prev_exit


def test_daily_max_3_trades_enforced(tmp_path: Path):
    script = [StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True)] * 20
    result, _ = _run_engine(tmp_path, script, time_exit_bars=1, max_trades=3)
    assert len(result.trades) <= 3


def test_no_trade_held_overnight(tmp_path: Path):
    script = [StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True)] * 10
    result, _ = _run_engine(tmp_path, script, split_days=True, time_exit_bars=20)
    assert not result.trades.empty
    for _, row in result.trades.iterrows():
        assert pd.Timestamp(row["entry_time"]).date() == pd.Timestamp(row["exit_time"]).date()


def test_trade_rows_include_required_apex_audit_fields(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True),
        StepSpec(),
    ]
    result, _ = _run_engine(tmp_path, script)
    required_cols = {
        "date",
        "direction",
        "symbol",
        "strike",
        "expiry",
        "option_type",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "pnl",
        "result",
        "exit_reason",
        "apex_action",
        "apex_confidence",
        "market_state",
        "bull_confidence",
        "bear_confidence",
        "skeptic_status",
        "skeptic_reason_codes",
        "risk_status",
        "risk_reason_codes",
        "options_status",
        "options_reason_codes",
        "option_proxy_used",
        "audit_run_id",
    }
    assert required_cols.issubset(set(result.trades.columns))


def test_trade_rows_have_non_empty_apex_action_and_audit_run_id(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True),
        StepSpec(),
    ]
    result, config = _run_engine(tmp_path, script)
    assert not result.trades.empty
    assert result.trades["apex_action"].astype(str).str.strip().ne("").all()
    assert result.trades["audit_run_id"].astype(str).str.strip().ne("").all()
    assert (config.audit_dir / result.audit_run_id).exists()


def test_audit_fields_are_populated_from_agent_reports(tmp_path: Path):
    script = [
        StepSpec(
            action="TRADE_CALL",
            market_state="breakout_attempt",
            bull_confidence=0.82,
            bear_confidence=0.21,
            skeptic_status="CLEAR",
            skeptic_reasons=["REGIME_CONFLICT", "LATE_ENTRY_RISK"],
            risk_status="APPROVED",
            risk_agent_status="APPROVED",
            risk_reasons=["OPEN_POSITION_EXISTS", "DIRECTIONAL_EVIDENCE_INSUFFICIENT"],
            options_approved=True,
            options_status="APPROVED",
            options_reasons=["SPREAD_OK", "OI_OK"],
            executed=True,
            apex_confidence=0.91,
            symbol="NIFTYTEST25000CE",
        ),
        StepSpec(),
    ]
    result, _ = _run_engine(tmp_path, script)
    assert len(result.trades) == 1
    row = result.trades.iloc[0]
    assert row["apex_action"] == "TRADE_CALL"
    assert float(row["apex_confidence"]) == 0.91
    assert row["market_state"] == "breakout_attempt"
    assert float(row["bull_confidence"]) == 0.82
    assert float(row["bear_confidence"]) == 0.21
    assert row["skeptic_status"] == "CLEAR"
    assert row["skeptic_reason_codes"] == "REGIME_CONFLICT;LATE_ENTRY_RISK"
    assert row["risk_status"] == "APPROVED"
    assert row["risk_reason_codes"] == "OPEN_POSITION_EXISTS;DIRECTIONAL_EVIDENCE_INSUFFICIENT"
    assert row["options_status"] == "APPROVED"
    assert row["options_reason_codes"] == "SPREAD_OK;OI_OK"
    assert bool(row["option_proxy_used"]) is False


def test_trade_row_rule_invariants_hold_for_trades(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True),
        StepSpec(action="TRADE_PUT", risk_status="APPROVED", options_approved=True, executed=True, symbol="NIFTYTEST25000PE"),
    ]
    result, _ = _run_engine(tmp_path, script, time_exit_bars=1)
    assert not result.trades.empty
    for _, row in result.trades.iterrows():
        if row["apex_action"] in {"TRADE_CALL", "TRADE_PUT"}:
            assert row["risk_status"] == "APPROVED"
            assert str(row["skeptic_status"]).upper() != "VETO"
            assert row["options_status"] != "REJECTED"
        if bool(row["option_proxy_used"]):
            assert str(row["symbol"]).startswith("PROXY_")


def test_replaying_same_frozen_data_produces_identical_results(tmp_path: Path):
    script = [
        StepSpec(action="TRADE_CALL", risk_status="APPROVED", options_approved=True, executed=True),
        StepSpec(),
        StepSpec(action="TRADE_PUT", risk_status="APPROVED", options_approved=True, executed=True, symbol="NIFTYTEST25000PE"),
        StepSpec(),
    ]
    first, _ = _run_engine(tmp_path / "run1", script, time_exit_bars=1)
    second, _ = _run_engine(tmp_path / "run2", script, time_exit_bars=1)
    pd.testing.assert_frame_equal(first.trades.reset_index(drop=True), second.trades.reset_index(drop=True))
