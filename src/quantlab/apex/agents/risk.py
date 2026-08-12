"""RiskAgent enforces deterministic risk gates for paper trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, RiskApproval, utc_now
from quantlab.apex.orchestration.state import PipelineState


@dataclass
class RiskReport(AgentReport):
    approval: RiskApproval = RiskApproval.REJECTED

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["approval"] = self.approval.value
        return payload


class RiskAgent(ApexAgent):
    agent_name = "RiskAgent"

    def __init__(self, cooldown_minutes: int = 5, reward_risk_min: float = 1.4) -> None:
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.reward_risk_min = reward_risk_min

    def run(
        self,
        state: PipelineState,
        at_time: datetime,
        expected_movement: float,
        stop_distance: float,
        target_distance: float,
    ) -> RiskReport:
        reasons: list[str] = []

        if state.session_risk.open_position is not None:
            reasons.append("OPEN_POSITION_EXISTS")
        if state.session_risk.trades_today >= state.max_trades_per_session:
            reasons.append("DAILY_TRADE_LIMIT")
        if state.session_risk.daily_realized_pnl <= state.daily_loss_limit:
            reasons.append("DAILY_LOSS_LIMIT")
        if not state.can_trade_now(at_time):
            reasons.append("COOLDOWN_ACTIVE")

        rr = target_distance / max(stop_distance, 1e-6)
        if rr < self.reward_risk_min:
            reasons.append("REWARD_RISK_TOO_LOW")
        if expected_movement < stop_distance * 1.1:
            reasons.append("INSUFFICIENT_EXPECTED_MOVEMENT")

        approved = len(reasons) == 0
        status = AgentStatus.OK if approved else AgentStatus.WARNING

        return RiskReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=status,
            evidence=["All risk gates passed"] if approved else [],
            conflicting_evidence=[] if approved else ["Risk gate violations present"],
            confidence=0.9 if approved else 0.1,
            sample_size=state.session_risk.trades_today,
            reason_codes=reasons,
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            approval=RiskApproval.APPROVED if approved else RiskApproval.REJECTED,
        )
