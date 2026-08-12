"""PaperExecutionAgent simulates execution and forbids real order placement."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, DecisionAction, RiskApproval, utc_now
from quantlab.apex.orchestration.state import DecisionRecord, PaperTrade, PipelineState


@dataclass
class PaperExecutionReport(AgentReport):
    executed: bool = False
    trade_id: str | None = None


class PaperExecutionAgent(ApexAgent):
    agent_name = "PaperExecutionAgent"

    def run(
        self,
        state: PipelineState,
        decision: DecisionRecord,
        risk_approval: RiskApproval,
        contract_symbol: str,
        simulated_price: float,
        allow_live_orders: bool = False,
    ) -> PaperExecutionReport:
        reasons: list[str] = []

        if allow_live_orders:
            raise RuntimeError("Real order placement is forbidden in APEX paper mode")
        if not state.paper_mode:
            reasons.append("PAPER_MODE_DISABLED")
        if risk_approval != RiskApproval.APPROVED:
            reasons.append("RISK_NOT_APPROVED")
        if not decision.signature:
            reasons.append("MISSING_DECISION_SIGNATURE")
        if decision.action not in {DecisionAction.TRADE_CALL, DecisionAction.TRADE_PUT}:
            reasons.append("NO_EXECUTABLE_DECISION")

        if reasons:
            return PaperExecutionReport(
                agent_name=self.agent_name,
                timestamp=utc_now(),
                status=AgentStatus.WARNING,
                evidence=[],
                conflicting_evidence=["Execution rejected by controls"],
                confidence=0.0,
                sample_size=0,
                reason_codes=reasons,
                data_version="nifty-live-v1",
                feature_version="apex-tech-v1",
                strategy_version="apex-v1",
                executed=False,
                trade_id=None,
            )

        trade_id = f"paper-{decision.snapshot_id}"
        stop = simulated_price * 0.9
        target = simulated_price * 1.18
        trade = PaperTrade(
            trade_id=trade_id,
            action=decision.action,
            contract_symbol=contract_symbol,
            entry_price=simulated_price,
            stop_price=stop,
            target_price=target,
            entry_time=utc_now(),
        )
        state.session_risk.open_position = trade
        state.session_risk.trades_today += 1

        return PaperExecutionReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=AgentStatus.OK,
            evidence=["Paper trade simulated and lifecycle initialized"],
            conflicting_evidence=[],
            confidence=0.95,
            sample_size=1,
            reason_codes=[],
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            executed=True,
            trade_id=trade_id,
        )
