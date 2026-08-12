"""BearResearchAgent builds structured bearish evidence for PUT setups."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, utc_now


@dataclass
class BearResearchReport(AgentReport):
    historical_base_rate: float = 0.0


class BearResearchAgent(ApexAgent):
    agent_name = "BearResearchAgent"

    def run(self, features: dict[str, float], state_probs: dict[str, float], sample_size: int) -> BearResearchReport:
        support: list[str] = []
        conflict: list[str] = []

        if features.get("ema_3", 0.0) < features.get("ema_30", 0.0):
            support.append("EMA3 below EMA30")
        else:
            conflict.append("EMA3 not below EMA30")

        if features.get("ema_slope", 0.0) < 0:
            support.append("Short-term EMA slope falling")
        else:
            conflict.append("EMA slope not falling")

        if features.get("momentum", 0.0) < 0:
            support.append("Negative momentum")
        else:
            conflict.append("Momentum not negative")

        if state_probs.get("directional_bearish", 0.0) > 0.25:
            support.append("MarketState bearish probability elevated")
        else:
            conflict.append("Bearish state probability weak")

        historical_base_rate = 0.54
        confidence = min(0.95, 0.2 + 0.15 * len(support))

        return BearResearchReport(
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
