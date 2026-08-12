"""TechnicalAgent computes approved deterministic features from frozen candles."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, MarketSnapshot, utc_now


@dataclass
class TechnicalReport(AgentReport):
    features: dict[str, float] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["features"] = self.features or {}
        return payload


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    ema_value = values[0]
    for value in values[1:]:
        ema_value = alpha * value + (1.0 - alpha) * ema_value
    return ema_value


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_val = sum(values) / len(values)
    var = sum((v - mean_val) ** 2 for v in values) / (len(values) - 1)
    return sqrt(max(var, 0.0))


class TechnicalAgent(ApexAgent):
    agent_name = "TechnicalAgent"

    def run(self, snapshot: MarketSnapshot) -> TechnicalReport:
        candles = sorted(snapshot.nifty_spot, key=lambda c: c.timestamp)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        if len(closes) < 35:
            return TechnicalReport(
                agent_name=self.agent_name,
                timestamp=utc_now(),
                status=AgentStatus.ERROR,
                evidence=[],
                conflicting_evidence=["Insufficient candle history for EMA30 and ADX"],
                confidence=0.0,
                sample_size=len(closes),
                reason_codes=["INSUFFICIENT_HISTORY"],
                data_version="nifty-live-v1",
                feature_version="apex-tech-v1",
                strategy_version="apex-v1",
                features={},
            )

        ema3 = _ema(closes[-30:], 3)
        ema30 = _ema(closes[-60:], 30)
        ema3_prev = _ema(closes[-31:-1], 3)
        ema_slope = ema3 - ema3_prev

        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
        cumulative_tpv = sum(tp * vol for tp, vol in zip(typical_prices, volumes))
        cumulative_vol = sum(volumes) if sum(volumes) else 1.0
        vwap = cumulative_tpv / cumulative_vol

        trs = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(closes))
        ]
        atr14 = sum(trs[-14:]) / 14.0

        up_moves = [max(highs[i] - highs[i - 1], 0.0) for i in range(1, len(highs))]
        down_moves = [max(lows[i - 1] - lows[i], 0.0) for i in range(1, len(lows))]
        plus_di = 100.0 * (sum(up_moves[-14:]) / max(sum(trs[-14:]), 1e-9))
        minus_di = 100.0 * (sum(down_moves[-14:]) / max(sum(trs[-14:]), 1e-9))
        adx = abs(plus_di - minus_di)

        first_hour = candles[: min(12, len(candles))]
        opening_range = max(c.high for c in first_hour) - min(c.low for c in first_hour)

        momentum = closes[-1] - closes[-6]
        log_returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]
        ]
        volatility = _stddev(log_returns[-20:])

        features = {
            "ema_3": ema3,
            "ema_30": ema30,
            "ema_slope": ema_slope,
            "vwap": vwap,
            "adx": adx,
            "atr": atr14,
            "opening_range": opening_range,
            "momentum": momentum,
            "volatility": volatility,
        }

        return TechnicalReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=AgentStatus.OK,
            evidence=["Approved technical feature set computed from frozen snapshot"],
            conflicting_evidence=[],
            confidence=0.85,
            sample_size=len(closes),
            reason_codes=[],
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            features=features,
        )
