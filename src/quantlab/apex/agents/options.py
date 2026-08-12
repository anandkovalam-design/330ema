"""OptionsAgent selects weekly ATM options and validates tradability constraints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quantlab.apex.agents.base import AgentReport, AgentStatus, ApexAgent, OptionContract, utc_now
from quantlab.apex.orchestration.state import OptionsSelection


@dataclass
class OptionsReport(AgentReport):
    approved: bool = False
    selection: OptionsSelection | None = None
    is_proxy: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["approved"] = self.approved
        payload["is_proxy"] = self.is_proxy
        payload["selection"] = None
        if self.selection is not None:
            payload["selection"] = {
                "contract_symbol": self.selection.contract_symbol,
                "strike": self.selection.strike,
                "expiry": self.selection.expiry.isoformat(),
                "option_type": self.selection.option_type,
                "spread": self.selection.spread,
                "is_proxy": self.selection.is_proxy,
            }
        return payload


class OptionsAgent(ApexAgent):
    agent_name = "OptionsAgent"

    def _weekly_expiry(self, contracts: list[OptionContract], asof: datetime) -> datetime | None:
        future_expiries = sorted({c.expiry for c in contracts if c.expiry >= asof})
        return future_expiries[0] if future_expiries else None

    def run(
        self,
        spot_price: float,
        contracts: list[OptionContract],
        asof: datetime,
        option_type: str,
    ) -> OptionsReport:
        if option_type not in {"CE", "PE"}:
            raise ValueError("option_type must be CE or PE")

        if not contracts:
            return OptionsReport(
                agent_name=self.agent_name,
                timestamp=utc_now(),
                status=AgentStatus.WARNING,
                evidence=["No historical options data, using proxy contract model"],
                conflicting_evidence=["OPTIONS_DATA_MISSING"],
                confidence=0.35,
                sample_size=0,
                reason_codes=["OPTIONS_DATA_MISSING"],
                data_version="nifty-live-v1",
                feature_version="apex-tech-v1",
                strategy_version="apex-v1",
                approved=False,
                selection=None,
                is_proxy=True,
            )

        expiry = self._weekly_expiry(contracts, asof)
        if expiry is None:
            return OptionsReport(
                agent_name=self.agent_name,
                timestamp=utc_now(),
                status=AgentStatus.WARNING,
                evidence=[],
                conflicting_evidence=["No weekly expiry available"],
                confidence=0.2,
                sample_size=0,
                reason_codes=["EXPIRY_UNAVAILABLE"],
                data_version="nifty-live-v1",
                feature_version="apex-tech-v1",
                strategy_version="apex-v1",
                approved=False,
                selection=None,
                is_proxy=False,
            )

        atm_strike = int(round(spot_price / 50.0) * 50)
        candidates = [
            c
            for c in contracts
            if c.expiry == expiry and c.strike == atm_strike and c.option_type == option_type
        ]

        if not candidates:
            return OptionsReport(
                agent_name=self.agent_name,
                timestamp=utc_now(),
                status=AgentStatus.WARNING,
                evidence=[],
                conflicting_evidence=["ATM contract unavailable"],
                confidence=0.2,
                sample_size=0,
                reason_codes=["CONTRACT_UNAVAILABLE"],
                data_version="nifty-live-v1",
                feature_version="apex-tech-v1",
                strategy_version="apex-v1",
                approved=False,
                selection=None,
                is_proxy=False,
            )

        contract = candidates[0]
        spread = contract.ask - contract.bid
        spread_ratio = spread / max(contract.ltp, 1e-6)

        reason_codes: list[str] = []
        conflict: list[str] = []
        if spread_ratio > 0.10:
            reason_codes.append("WIDE_SPREAD")
            conflict.append("Bid-ask spread too wide")
        if contract.oi < 500:
            reason_codes.append("LOW_LIQUIDITY")
            conflict.append("Open interest below minimum")
        days_to_expiry = (contract.expiry.date() - asof.date()).days
        if days_to_expiry < 1:
            reason_codes.append("EXPIRY_PROXIMITY_RISK")
            conflict.append("Contract is too close to expiry")
        if contract.ltp < contract.bid or contract.ltp > contract.ask * 1.20:
            reason_codes.append("PREMIUM_BEHAVIOR_INVALID")
            conflict.append("Premium behavior inconsistent with quoted spread")

        approved = len(reason_codes) == 0
        status = AgentStatus.OK if approved else AgentStatus.WARNING
        selection = OptionsSelection(
            contract_symbol=contract.symbol,
            strike=contract.strike,
            expiry=contract.expiry,
            option_type=contract.option_type,
            spread=spread,
            is_proxy=False,
        )

        return OptionsReport(
            agent_name=self.agent_name,
            timestamp=utc_now(),
            status=status,
            evidence=[f"Selected weekly ATM {option_type} strike {atm_strike}"] if approved else [],
            conflicting_evidence=conflict,
            confidence=0.7 if approved else 0.3,
            sample_size=len(candidates),
            reason_codes=reason_codes,
            data_version="nifty-live-v1",
            feature_version="apex-tech-v1",
            strategy_version="apex-v1",
            approved=approved,
            selection=selection,
            is_proxy=False,
        )
