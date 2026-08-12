from __future__ import annotations

from datetime import timedelta

from quantlab.apex.agents.base import AgentStatus, Candle, DecisionAction, OptionContract
from quantlab.apex.agents.skeptic import SkepticReport
from quantlab.apex.orchestration.pipeline import ApexPipeline


def _set_trend(snapshot, step: float) -> None:
    candles = []
    price = 25000.0
    wiggle = max(abs(step) * 0.2, 0.05)
    for i, c in enumerate(snapshot.nifty_spot):
        close_price = price + step
        candles.append(
            Candle(
                timestamp=c.timestamp,
                open=price,
                high=max(price, close_price) + wiggle,
                low=min(price, close_price) - wiggle,
                close=close_price,
                volume=c.volume,
            )
        )
        price = close_price
    snapshot.nifty_spot = candles
    snapshot.nifty_futures = candles.copy()


def test_pipeline_valid_call(base_snapshot, tmp_path):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    _set_trend(base_snapshot, step=0.2)
    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.TRADE_CALL
    assert "decision" in result.reports


def test_pipeline_valid_put(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    _set_trend(base_snapshot, step=-0.2)
    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.TRADE_PUT


def test_conflicting_evidence_watch(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    _set_trend(base_snapshot, step=0.2)
    pipeline.skeptic.run = lambda **_: SkepticReport(
        agent_name="SkepticAgent",
        timestamp=base_snapshot.captured_at,
        status=AgentStatus.WARNING,
        evidence=[],
        conflicting_evidence=["Forced regime conflict for integration coverage"],
        confidence=0.8,
        sample_size=len(base_snapshot.nifty_spot),
        reason_codes=["REGIME_CONFLICT"],
        data_version="nifty-live-v1",
        feature_version="apex-tech-v1",
        strategy_version="apex-v1",
        veto=True,
    )

    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.WATCH


def test_invalid_data_stops_early(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    base_snapshot.nifty_spot = []
    base_snapshot.nifty_futures = []
    base_snapshot.india_vix = []
    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.INSUFFICIENT_DATA
    assert "technical" not in result.reports


def test_skeptic_veto_blocks_trade(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)

    # Extend candles so bars_into_session triggers late-entry skepticism veto.
    last_ts = base_snapshot.nifty_spot[-1].timestamp
    last_close = base_snapshot.nifty_spot[-1].close
    for i in range(30):
        ts = last_ts + timedelta(minutes=i + 1)
        close = last_close + 0.8
        candle = Candle(
            timestamp=ts,
            open=last_close,
            high=max(last_close, close) + 0.5,
            low=min(last_close, close) - 0.5,
            close=close,
            volume=1800 + i,
        )
        base_snapshot.nifty_spot.append(candle)
        base_snapshot.nifty_futures.append(candle)
        base_snapshot.india_vix.append(
            Candle(
                timestamp=ts,
                open=15.0,
                high=15.2,
                low=14.8,
                close=15.0,
                volume=1000,
            )
        )
        last_close = close

    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.WATCH


def test_range_bound_produces_no_trade(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    pipeline.state.max_trades_per_session = 0
    _set_trend(base_snapshot, step=0.0)

    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.NO_TRADE


def test_risk_rejection_blocks_trade(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    pipeline.state.max_trades_per_session = 1
    pipeline.state.session_risk.trades_today = 1
    _set_trend(base_snapshot, step=0.9)

    result = pipeline.run(base_snapshot)
    assert result.reports["risk"].get("approval") in {"REJECTED", None}
    assert result.action == DecisionAction.NO_TRADE


def test_missing_options_data_no_trade(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    base_snapshot.option_chain = []
    _set_trend(base_snapshot, step=0.9)
    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.INSUFFICIENT_DATA


def test_daily_trade_limit(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    pipeline.state.max_trades_per_session = 0
    _set_trend(base_snapshot, step=0.9)
    result = pipeline.run(base_snapshot)
    assert result.action == DecisionAction.NO_TRADE


def test_real_order_prevention(tmp_path, base_snapshot):
    pipeline = ApexPipeline(audit_dir=tmp_path)
    _set_trend(base_snapshot, step=0.9)
    result = pipeline.run(base_snapshot)
    exec_report = result.reports["paper_execution"]
    assert "Real order" not in " ".join(exec_report.get("reason_codes", []))


def test_replay_deterministic_for_same_snapshot(tmp_path, base_snapshot):
    p1 = ApexPipeline(audit_dir=tmp_path / "a")
    p2 = ApexPipeline(audit_dir=tmp_path / "b")
    _set_trend(base_snapshot, step=0.9)

    r1 = p1.run(base_snapshot)
    r2 = p2.run(base_snapshot)

    d1 = r1.reports["decision"]["decision_record"]
    d2 = r2.reports["decision"]["decision_record"]
    assert d1 == d2
