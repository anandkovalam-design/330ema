"""BullResearchAgent builds structured bullish evidence for CALL setups."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, utc_now


@dataclass
class BullResearchReport(AgentReport):
    historical_base_rate: float = 0.0


class BullResearchAgent(ApexAgent):
    agent_name = "BullResearchAgent"

    def run(self, features: dict[str, float], state_probs: dict[str, float], sample_size: int) -> BullResearchReport:
        support: list[str] = []
        conflict: list[str] = []

        if features.get("ema_3", 0.0) > features.get("ema_30", 0.0):
            support.append("EMA3 above EMA30")
        else:
            conflict.append("EMA3 not above EMA30")

        if features.get("ema_slope", 0.0) > 0:
            support.append("Short-term EMA slope rising")
        else:
            conflict.append("EMA slope not rising")

        if features.get("momentum", 0.0) > 0:
            support.append("Positive momentum")
        else:
            conflict.append("Momentum not positive")

        if state_probs.get("directional_bullish", 0.0) > 0.25:
            support.append("MarketState bullish probability elevated")
        else:
            conflict.append("Bullish state probability weak")

        historical_base_rate = 0.56
        confidence = min(0.95, 0.2 + 0.15 * len(support))

        return BullResearchReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=AgentStatus.OK,
            evidence=support,
            conflicting_evidence=conflict,
            confidence=confidence,
            sample_size=sample_size,
            reason_codes=[],
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            historical_base_rate=historical_base_rate,
        )
