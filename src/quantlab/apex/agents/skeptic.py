"""SkepticAgent challenges directional reports and can veto weak setups."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, utc_now


@dataclass
class SkepticReport(AgentReport):
    veto: bool = False


class SkepticAgent(ApexAgent):
    agent_name = "SkepticAgent"

    def run(
        self,
        bull_confidence: float,
        bear_confidence: float,
        sample_size: int,
        state_selected: str,
        expected_movement: float,
        bars_into_session: int,
        leakage_flags: int,
        feature_count: int,
    ) -> SkepticReport:
        reason_codes: list[str] = []
        conflict: list[str] = []

        if sample_size < 30:
            reason_codes.append("WEAK_SAMPLE_SIZE")
            conflict.append("Sample size too low for confidence")
        if abs(bull_confidence - bear_confidence) < 0.12:
            reason_codes.append("REGIME_CONFLICT")
            conflict.append("Directional disagreement is too high")
        if state_selected in {"range_bound", "uncertain", "contraction"}:
            reason_codes.append("REGIME_CONFLICT")
            conflict.append("State does not support directional conviction")
        if expected_movement < 0.2:
            reason_codes.append("INSUFFICIENT_EXPECTED_MOVE")
            conflict.append("Expected movement below minimum threshold")
        if leakage_flags > 0:
            reason_codes.append("DATA_LEAKAGE_RISK")
            conflict.append("Leakage controls reported non-zero flags")
        if feature_count > max(sample_size // 3, 1):
            reason_codes.append("OVERFITTING_RISK")
            conflict.append("Feature complexity too high for available sample size")
        if bars_into_session >= 66:
            reason_codes.append("LATE_ENTRY_RISK")
            conflict.append("Entry is too late in the session")

        veto = len(reason_codes) > 0
        status = AgentStatus.WARNING if veto else AgentStatus.OK

        return SkepticReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=status,
            evidence=[] if veto else ["No critical skepticism findings"],
            conflicting_evidence=conflict,
            confidence=0.85 if veto else 0.55,
            sample_size=sample_size,
            reason_codes=reason_codes,
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            veto=veto,
        )
