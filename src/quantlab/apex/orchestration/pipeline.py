"""APEX orchestration pipeline with strict ordering and audit persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from quantlab.apex.agents import (
    BearResearchAgent,
    BullResearchAgent,
    DecisionAgent,
    MarketDataAgent,
    MarketStateAgent,
    OptionsAgent,
    PaperExecutionAgent,
    RiskAgent,
    SkepticAgent,
    TechnicalAgent,
)
from quantlab.apex.agents.base import DataValidationStatus, DecisionAction, MarketSnapshot, RiskApproval
from quantlab.apex.orchestration.audit import AuditStore
from quantlab.apex.orchestration.state import PipelineState


@dataclass
class PipelineResult:
    action: DecisionAction
    reports: dict[str, dict[str, Any]]


class ApexPipeline:
    """Controlled multi-agent pipeline in paper-trading mode."""

    def __init__(self, audit_dir: Path | None = None) -> None:
        self.market_data = MarketDataAgent()
        self.technical = TechnicalAgent()
        self.market_state = MarketStateAgent()
        self.bull = BullResearchAgent()
        self.bear = BearResearchAgent()
        self.skeptic = SkepticAgent()
        self.options = OptionsAgent()
        self.risk = RiskAgent()
        self.decision = DecisionAgent()
        self.execution = PaperExecutionAgent()

        self.state = PipelineState()
        self.audit_store = AuditStore(audit_dir or Path("outputs/apex_audit"))

    def _record(self, snapshot_id: str, name: str, report_dict: dict[str, Any]) -> None:
        entry = {"report_name": name, "payload": report_dict}
        self.state.audits.append(entry)
        self.audit_store.append(snapshot_id, entry)

    def _freeze_timestamp(self, report: dict[str, Any], captured_at_iso: str) -> dict[str, Any]:
        payload = dict(report)
        payload["timestamp"] = captured_at_iso
        return payload

    def run(self, snapshot: MarketSnapshot) -> PipelineResult:
        reports: dict[str, dict[str, Any]] = {}
        captured_at_iso = snapshot.captured_at.isoformat()

        reports["snapshot"] = {
            "snapshot_id": snapshot.snapshot_id,
            "captured_at": captured_at_iso,
            "spot_candles": len(snapshot.nifty_spot),
            "futures_candles": len(snapshot.nifty_futures),
            "vix_candles": len(snapshot.india_vix),
            "option_contracts": len(snapshot.option_chain),
        }
        self._record(snapshot.snapshot_id, "snapshot", reports["snapshot"])

        market_data_report = self.market_data.run(snapshot)
        reports["market_data"] = self._freeze_timestamp(market_data_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "market_data", reports["market_data"])
        if market_data_report.validation_status == DataValidationStatus.DATA_INVALID:
            return PipelineResult(action=DecisionAction.INSUFFICIENT_DATA, reports=reports)

        technical_report = self.technical.run(snapshot)
        reports["technical"] = self._freeze_timestamp(technical_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "technical", reports["technical"])
        if technical_report.status.value == "ERROR":
            reports["decision"] = {
                "agent_name": "DecisionAgent",
                "timestamp": captured_at_iso,
                "status": "WARNING",
                "action": DecisionAction.INSUFFICIENT_DATA.value,
                "reason_codes": ["INSUFFICIENT_TECHNICAL_DATA"],
            }
            self._record(snapshot.snapshot_id, "decision", reports["decision"])
            return PipelineResult(action=DecisionAction.INSUFFICIENT_DATA, reports=reports)
        features = technical_report.features or {}

        market_state_report = self.market_state.run(features, technical_report.sample_size)
        reports["market_state"] = self._freeze_timestamp(market_state_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "market_state", reports["market_state"])

        bull_report = self.bull.run(features, market_state_report.probabilities or {}, technical_report.sample_size)
        bear_report = self.bear.run(features, market_state_report.probabilities or {}, technical_report.sample_size)
        reports["bull_research"] = self._freeze_timestamp(bull_report.to_dict(), captured_at_iso)
        reports["bear_research"] = self._freeze_timestamp(bear_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "bull_research", reports["bull_research"])
        self._record(snapshot.snapshot_id, "bear_research", reports["bear_research"])

        expected_movement = abs(features.get("momentum", 0.0)) + 0.5 * features.get("atr", 0.0)
        skeptic_report = self.skeptic.run(
            bull_confidence=bull_report.confidence,
            bear_confidence=bear_report.confidence,
            sample_size=technical_report.sample_size,
            state_selected=market_state_report.selected_state,
            expected_movement=expected_movement,
            bars_into_session=technical_report.sample_size,
            leakage_flags=0,
            feature_count=len(features),
        )
        reports["skeptic"] = self._freeze_timestamp(skeptic_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "skeptic", reports["skeptic"])

        directional_conf = max(bull_report.confidence, bear_report.confidence)
        directional_side = "CE" if bull_report.confidence >= bear_report.confidence else "PE"

        options_approved = False
        options_reasons = ["DIRECTIONAL_EVIDENCE_INSUFFICIENT"]
        selected_contract_symbol = ""
        simulated_price = 100.0
        if directional_conf >= 0.60:
            options_report = self.options.run(
                spot_price=snapshot.nifty_spot[-1].close,
                contracts=snapshot.option_chain,
                asof=snapshot.captured_at,
                option_type=directional_side,
            )
            reports["options"] = self._freeze_timestamp(options_report.to_dict(), captured_at_iso)
            self._record(snapshot.snapshot_id, "options", reports["options"])
            options_approved = options_report.approved
            options_reasons = options_report.reason_codes
            if options_report.selection is not None:
                selected_contract_symbol = options_report.selection.contract_symbol
                contract = next(
                    (c for c in snapshot.option_chain if c.symbol == selected_contract_symbol),
                    None,
                )
                simulated_price = contract.ltp if contract is not None else 100.0
        else:
            reports["options"] = {
                "timestamp": captured_at_iso,
                "skipped": True,
                "reason_codes": options_reasons,
            }
            self._record(snapshot.snapshot_id, "options", reports["options"])

        risk_report = self.risk.run(
            state=self.state,
            at_time=snapshot.captured_at,
            expected_movement=expected_movement,
            stop_distance=max(features.get("atr", 0.0) * 0.8, 1.0),
            target_distance=max(features.get("atr", 0.0) * 1.4, 1.5),
        ) if directional_conf >= 0.60 else None

        if risk_report is None:
            risk_approval = RiskApproval.REJECTED
            risk_reasons = ["DIRECTIONAL_EVIDENCE_INSUFFICIENT"]
            reports["risk"] = {"timestamp": captured_at_iso, "skipped": True, "reason_codes": risk_reasons}
        else:
            risk_approval = risk_report.approval
            risk_reasons = risk_report.reason_codes
            reports["risk"] = self._freeze_timestamp(risk_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "risk", reports["risk"])

        decision_report = self.decision.run(
            snapshot_id=snapshot.snapshot_id,
            bull_confidence=bull_report.confidence,
            bear_confidence=bear_report.confidence,
            skeptic_veto=skeptic_report.veto,
            skeptic_reasons=skeptic_report.reason_codes,
            risk_approval=risk_approval,
            risk_reasons=risk_reasons,
            options_approved=options_approved,
            options_reasons=options_reasons,
            expected_movement=expected_movement,
            selected_state=market_state_report.selected_state,
            decision_time=snapshot.captured_at,
        )
        reports["decision"] = self._freeze_timestamp(decision_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "decision", reports["decision"])

        execution_report = self.execution.run(
            state=self.state,
            decision=decision_report.decision_record,
            risk_approval=risk_approval,
            contract_symbol=selected_contract_symbol or f"NIFTY-{directional_side}-SIM",
            simulated_price=simulated_price,
            allow_live_orders=False,
        )
        reports["paper_execution"] = self._freeze_timestamp(execution_report.to_dict(), captured_at_iso)
        self._record(snapshot.snapshot_id, "paper_execution", reports["paper_execution"])

        # Apply cooldown when a trade is actually simulated.
        if execution_report.executed:
            self.state.session_risk.cooldown_until = snapshot.captured_at + timedelta(minutes=5)

        return PipelineResult(action=decision_report.action, reports=reports)
