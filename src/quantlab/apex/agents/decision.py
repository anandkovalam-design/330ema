"""DecisionAgent fuses structured outputs into one deterministic decision."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.apex.agents.base import (
    AgentReport,
    AgentStatus,
    ApexAgent,
    DecisionAction,
    RiskApproval,
    utc_now,
)
from quantlab.apex.orchestration.state import DecisionRecord


@dataclass
class DecisionReport(AgentReport):
    action: DecisionAction = DecisionAction.INSUFFICIENT_DATA
    decision_record: DecisionRecord | None = None

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["action"] = self.action.value
        payload["decision_record"] = None
        if self.decision_record is not None:
            payload["decision_record"] = self.decision_record.to_dict()
        return payload


class DecisionAgent(ApexAgent):
    agent_name = "DecisionAgent"

    def run(
        self,
        snapshot_id: str,
        bull_confidence: float,
        bear_confidence: float,
        skeptic_veto: bool,
        skeptic_reasons: list[str],
        risk_approval: RiskApproval,
        risk_reasons: list[str],
        options_approved: bool,
        options_reasons: list[str],
        expected_movement: float,
        selected_state: str = "uncertain",
        decision_time=None,
    ) -> DecisionReport:
        reasons: list[str] = []
        status = AgentStatus.OK

        if risk_approval == RiskApproval.REJECTED:
            action = DecisionAction.NO_TRADE
            reasons.extend(risk_reasons)
            status = AgentStatus.WARNING
        elif selected_state == "range_bound":
            action = DecisionAction.NO_TRADE
            reasons.append("RANGE_BOUND_REGIME")
            status = AgentStatus.WARNING
        elif skeptic_veto:
            action = DecisionAction.WATCH
            reasons.extend(skeptic_reasons)
            status = AgentStatus.WARNING
        elif not options_approved:
            action = DecisionAction.INSUFFICIENT_DATA
            reasons.extend(options_reasons)
            status = AgentStatus.WARNING
        elif bull_confidence >= 0.65 and bull_confidence - bear_confidence >= 0.10:
            action = DecisionAction.TRADE_CALL
        elif bear_confidence >= 0.65 and bear_confidence - bull_confidence >= 0.10:
            action = DecisionAction.TRADE_PUT
        elif abs(bull_confidence - bear_confidence) < 0.10:
            action = DecisionAction.WATCH
            reasons.append("DIRECTIONAL_DISAGREEMENT")
            status = AgentStatus.WARNING
        else:
            action = DecisionAction.NO_TRADE
            reasons.append("INSUFFICIENT_CONVICTION")
            status = AgentStatus.WARNING

        confidence = max(bull_confidence, bear_confidence)
        record_time = decision_time if decision_time is not None else utc_now()
        record = DecisionRecord(
            action=action,
            confidence=confidence,
            expected_movement=expected_movement,
            expected_duration_bars=6,
            entry_trigger="Break prior bar high/low with volume confirmation",
            invalidation="Close back inside opening range",
            target="1.8R from entry",
            rejection_reasons=reasons,
            snapshot_id=snapshot_id,
            created_at=record_time,
        )
        record.sign()

        evidence = [f"Decision selected: {action.value}"]
        if action in {DecisionAction.TRADE_CALL, DecisionAction.TRADE_PUT}:
            evidence.append("Directional and risk constraints aligned")

        return DecisionReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=status,
            evidence=evidence,
            conflicting_evidence=[] if not reasons else ["Decision contains rejection conditions"],
            confidence=confidence,
            sample_size=1,
            reason_codes=reasons,
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            action=action,
            decision_record=record,
        )
