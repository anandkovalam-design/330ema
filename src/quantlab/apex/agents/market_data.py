"""MarketDataAgent validates snapshot quality before downstream analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from quantlab.apex.agents.base import (
    AgentReport,
    AgentStatus,
    ApexAgent,
    Candle,
    DataValidationStatus,
    MarketSnapshot,
    OptionContract,
    utc_now,
)


@dataclass
class MarketDataReport(AgentReport):
    validation_status: DataValidationStatus = DataValidationStatus.DATA_INVALID
    checks: dict[str, int] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["validation_status"] = self.validation_status.value
        payload["checks"] = self.checks or {}
        return payload


class MarketDataAgent(ApexAgent):
    agent_name = "MarketDataAgent"

    def __init__(self, max_delay: timedelta = timedelta(minutes=2)) -> None:
        self.max_delay = max_delay

    def _run_option_checks(self, contracts: list[OptionContract], asof) -> dict[str, int]:
        missing = 0
        duplicates = 0
        delayed = 0
        malformed = 0

        if not contracts:
            return {"missing": 1, "duplicates": 0, "delayed": 0, "malformed": 0}

        seen_keys: set[tuple[str, int, str]] = set()
        for contract in contracts:
            key = (contract.symbol, contract.strike, contract.option_type)
            if key in seen_keys:
                duplicates += 1
            seen_keys.add(key)

            if contract.expiry < asof:
                malformed += 1
            if contract.bid < 0 or contract.ask <= 0 or contract.ltp <= 0:
                malformed += 1
            if contract.ask < contract.bid:
                malformed += 1

        return {
            "missing": missing,
            "duplicates": duplicates,
            "delayed": delayed,
            "malformed": malformed,
        }

    def _run_checks(self, candles: list[Candle]) -> dict[str, int]:
        duplicates = 0
        missing = 0
        delayed = 0
        malformed = 0

        if not candles:
            return {"missing": 1, "duplicates": 0, "delayed": 0, "malformed": 0}

        seen = set()
        timestamps = sorted(c.timestamp for c in candles)
        if len(timestamps) > 2:
            positive_gaps = [
                timestamps[i] - timestamps[i - 1]
                for i in range(1, len(timestamps))
                if timestamps[i] > timestamps[i - 1]
            ]
            expected_step = min(positive_gaps, key=lambda x: x.total_seconds()) if positive_gaps else timedelta(minutes=5)
        else:
            expected_step = timedelta(minutes=5)

        for candle in candles:
            if candle.timestamp in seen:
                duplicates += 1
            seen.add(candle.timestamp)
            if candle.arrived_at is not None and candle.arrived_at - candle.timestamp > self.max_delay:
                delayed += 1
            if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
                malformed += 1

        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            if gap > expected_step * 2:
                missing += 1

        return {
            "missing": missing,
            "duplicates": duplicates,
            "delayed": delayed,
            "malformed": malformed,
        }

    def run(self, snapshot: MarketSnapshot) -> MarketDataReport:
        checks = {
            "spot": self._run_checks(snapshot.nifty_spot),
            "futures": self._run_checks(snapshot.nifty_futures),
            "vix": self._run_checks(snapshot.india_vix),
            "options": self._run_option_checks(snapshot.option_chain, snapshot.captured_at),
        }
        aggregate = {
            key: sum(values[key] for values in checks.values())
            for key in ("missing", "duplicates", "delayed", "malformed")
        }

        evidence: list[str] = []
        conflicting: list[str] = []
        reason_codes: list[str] = []

        if aggregate["malformed"] > 0 or aggregate["missing"] > 2:
            validation = DataValidationStatus.DATA_INVALID
            status = AgentStatus.ERROR
            reason_codes.append("DATA_INVALID")
        elif aggregate["duplicates"] > 0 or aggregate["delayed"] > 0 or aggregate["missing"] > 0:
            validation = DataValidationStatus.DATA_DEGRADED
            status = AgentStatus.WARNING
            reason_codes.append("DATA_DEGRADED")
        else:
            validation = DataValidationStatus.DATA_VALID
            status = AgentStatus.OK
            evidence.append("All required feeds passed integrity checks")

        if aggregate["missing"]:
            conflicting.append(f"Missing candle gaps detected: {aggregate['missing']}")
        if aggregate["duplicates"]:
            conflicting.append(f"Duplicate candles detected: {aggregate['duplicates']}")
        if aggregate["delayed"]:
            conflicting.append(f"Delayed candles detected: {aggregate['delayed']}")
        if aggregate["malformed"]:
            conflicting.append(f"Malformed candles detected: {aggregate['malformed']}")

        confidence = 1.0 if validation == DataValidationStatus.DATA_VALID else 0.4

        return MarketDataReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=status,
            validation_status=validation,
            checks=aggregate,
            evidence=evidence,
            conflicting_evidence=conflicting,
            confidence=confidence,
            sample_size=len(snapshot.nifty_spot),
            reason_codes=reason_codes,
            data_version="nifty-live-v1",
            feature_version="raw",
            strategy_version="apex-v1",
        )
