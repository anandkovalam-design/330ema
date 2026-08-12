"""MarketStateAgent classifies current market state with support metrics."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, utc_now


STATE_NAMES = [
    "directional_bullish",
    "directional_bearish",
    "range_bound",
    "volatility_expansion",
    "contraction",
    "breakout_attempt",
    "failed_breakout",
    "reversal",
    "uncertain",
]


@dataclass
class MarketStateReport(AgentReport):
    selected_state: str = "uncertain"
    probabilities: dict[str, float] | None = None
    support_features: dict[str, float] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["selected_state"] = self.selected_state
        payload["probabilities"] = self.probabilities or {}
        payload["support_features"] = self.support_features or {}
        return payload


class MarketStateAgent(ApexAgent):
    agent_name = "MarketStateAgent"

    def run(self, features: dict[str, float], sample_size: int) -> MarketStateReport:
        ema_gap = features.get("ema_3", 0.0) - features.get("ema_30", 0.0)
        ema_slope = features.get("ema_slope", 0.0)
        adx = features.get("adx", 0.0)
        atr = features.get("atr", 0.0)
        opening_range = features.get("opening_range", 0.0)
        momentum = features.get("momentum", 0.0)
        volatility = features.get("volatility", 0.0)

        probs = {name: 0.02 for name in STATE_NAMES}

        if ema_gap > 0 and ema_slope > 0 and momentum > 0:
            probs["directional_bullish"] += 0.45
        if ema_gap < 0 and ema_slope < 0 and momentum < 0:
            probs["directional_bearish"] += 0.45

        if adx < 15:
            probs["range_bound"] += 0.35
        if volatility > 0.004 and atr > opening_range * 0.25:
            probs["volatility_expansion"] += 0.30
        if volatility < 0.002:
            probs["contraction"] += 0.30

        if abs(momentum) > max(atr * 0.6, 1e-6) and adx > 20:
            probs["breakout_attempt"] += 0.22
        if abs(momentum) < max(atr * 0.15, 1e-6) and adx > 20:
            probs["failed_breakout"] += 0.22
        if ema_slope * momentum < 0:
            probs["reversal"] += 0.20

        total = sum(probs.values())
        normalized = {k: v / total for k, v in probs.items()}
        selected_state = max(normalized, key=normalized.get)

        return MarketStateReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=AgentStatus.OK,
            evidence=[f"State selected via deterministic scorecard: {selected_state}"],
            conflicting_evidence=["State uncertainty remains non-zero by design"],
            confidence=normalized[selected_state],
            sample_size=sample_size,
            reason_codes=[],
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            selected_state=selected_state,
            probabilities=normalized,
            support_features={
                "ema_gap": ema_gap,
                "ema_slope": ema_slope,
                "adx": adx,
                "atr": atr,
                "opening_range": opening_range,
                "momentum": momentum,
                "volatility": volatility,
            },
        )
