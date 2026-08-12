from __future__ import annotations

from datetime import timedelta

from quantlab.apex.agents.base import DecisionAction, RiskApproval
from quantlab.apex.agents.bear_researcher import BearResearchAgent
from quantlab.apex.agents.bull_researcher import BullResearchAgent
from quantlab.apex.agents.decision import DecisionAgent
from quantlab.apex.agents.market_data import MarketDataAgent
from quantlab.apex.agents.market_state import MarketStateAgent
from quantlab.apex.agents.options import OptionsAgent
from quantlab.apex.agents.paper_execution import PaperExecutionAgent
from quantlab.apex.agents.risk import RiskAgent
from quantlab.apex.agents.skeptic import SkepticAgent
from quantlab.apex.agents.technical import TechnicalAgent
from quantlab.apex.orchestration.state import DecisionRecord, PipelineState


def test_market_data_valid(base_snapshot):
    report = MarketDataAgent().run(base_snapshot)
    assert report.validation_status.value == "DATA_VALID"


def test_market_data_invalid_on_duplicate(base_snapshot):
    dup = base_snapshot.nifty_spot[-1]
    base_snapshot.nifty_spot.append(dup)
    report = MarketDataAgent().run(base_snapshot)
    assert report.validation_status.value == "DATA_DEGRADED"


def test_technical_features_present(base_snapshot):
    report = TechnicalAgent().run(base_snapshot)
    assert "ema_3" in report.features
    assert "atr" in report.features


def test_market_state_probabilities_sum_one():
    report = MarketStateAgent().run({"momentum": 0.8, "volatility": 0.7}, sample_size=50)
    total = sum(report.probabilities.values())
    assert abs(total - 1.0) < 1e-9


def test_bull_bear_outputs_structured():
    features = {"momentum": 0.9, "ema_slope": 0.6, "vwap_distance": 0.3, "adx_proxy": 35.0, "volatility": 0.4}
    probs = {"bull_trend": 0.6, "bear_trend": 0.1, "range_bound": 0.2, "breakout": 0.1, "reversal": 0.0, "uncertain": 0.0, "expansion": 0.0, "contraction": 0.0}
    bull = BullResearchAgent().run(features, probs, sample_size=60)
    bear = BearResearchAgent().run(features, probs, sample_size=60)
    assert bull.sample_size == 60
    assert bear.sample_size == 60


def test_skeptic_veto_weak_sample():
    report = SkepticAgent().run(
        bull_confidence=0.7,
        bear_confidence=0.5,
        sample_size=10,
        state_selected="directional_bullish",
        expected_movement=0.5,
        bars_into_session=20,
        leakage_flags=0,
        feature_count=9,
    )
    assert report.veto is True
    assert "WEAK_SAMPLE_SIZE" in report.reason_codes


def test_skeptic_detects_late_entry_and_leakage():
    report = SkepticAgent().run(
        bull_confidence=0.75,
        bear_confidence=0.2,
        sample_size=40,
        state_selected="directional_bullish",
        expected_movement=1.2,
        bars_into_session=70,
        leakage_flags=1,
        feature_count=20,
    )
    assert report.veto is True
    assert "LATE_ENTRY_RISK" in report.reason_codes
    assert "DATA_LEAKAGE_RISK" in report.reason_codes


def test_options_reject_missing_data(base_snapshot):
    report = OptionsAgent().run(
        spot_price=base_snapshot.nifty_spot[-1].close,
        contracts=[],
        asof=base_snapshot.captured_at,
        option_type="CE",
    )
    assert report.approved is False
    assert report.is_proxy is True


def test_options_reject_expiry_proximity(base_snapshot):
    contract = base_snapshot.option_chain[0]
    contract.expiry = base_snapshot.captured_at
    report = OptionsAgent().run(
        spot_price=base_snapshot.nifty_spot[-1].close,
        contracts=base_snapshot.option_chain,
        asof=base_snapshot.captured_at,
        option_type="CE",
    )
    assert report.approved is False
    assert "EXPIRY_PROXIMITY_RISK" in report.reason_codes


def test_risk_reject_trade_limit(base_snapshot):
    state = PipelineState(max_trades_per_session=1)
    state.session_risk.trades_today = 1
    report = RiskAgent().run(
        state=state,
        at_time=base_snapshot.captured_at,
        expected_movement=5.0,
        stop_distance=2.0,
        target_distance=4.0,
    )
    assert report.approval == RiskApproval.REJECTED
    assert "DAILY_TRADE_LIMIT" in report.reason_codes


def test_decision_respects_risk_reject():
    report = DecisionAgent().run(
        snapshot_id="snap-001",
        bull_confidence=0.8,
        bear_confidence=0.2,
        skeptic_veto=False,
        skeptic_reasons=[],
        risk_approval=RiskApproval.REJECTED,
        risk_reasons=["DAILY_TRADE_LIMIT"],
        options_approved=True,
        options_reasons=[],
        expected_movement=4.0,
    )
    assert report.action == DecisionAction.NO_TRADE
    assert "DAILY_TRADE_LIMIT" in report.reason_codes


def test_paper_execution_requires_signature(base_snapshot):
    state = PipelineState()
    record = DecisionRecord(
        action=DecisionAction.TRADE_CALL,
        confidence=0.8,
        expected_movement=4.0,
        expected_duration_bars=4,
        entry_trigger="x",
        invalidation="y",
        target="z",
        rejection_reasons=[],
        snapshot_id="snap-001",
        created_at=base_snapshot.captured_at,
    )
    report = PaperExecutionAgent().run(
        state=state,
        decision=record,
        risk_approval=RiskApproval.APPROVED,
        contract_symbol="NIFTY26JUL25000CE",
        simulated_price=120.0,
        allow_live_orders=False,
    )
    assert report.executed is False
    assert "MISSING_DECISION_SIGNATURE" in report.reason_codes


def test_paper_execution_blocks_live_orders(base_snapshot):
    state = PipelineState()
    record = DecisionRecord(
        action=DecisionAction.TRADE_CALL,
        confidence=0.8,
        expected_movement=4.0,
        expected_duration_bars=4,
        entry_trigger="x",
        invalidation="y",
        target="z",
        rejection_reasons=[],
        snapshot_id="snap-001",
        created_at=base_snapshot.captured_at,
    )
    record.sign()
    try:
        PaperExecutionAgent().run(
            state=state,
            decision=record,
            risk_approval=RiskApproval.APPROVED,
            contract_symbol="NIFTY26JUL25000CE",
            simulated_price=120.0,
            allow_live_orders=True,
        )
    except RuntimeError as exc:
        assert "forbidden" in str(exc).lower()
    else:
        assert False, "Expected RuntimeError"
