"""Shared pipeline state and typed records for APEX orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from quantlab.apex.agents.base import DecisionAction


@dataclass
class FeatureSnapshot:
    values: dict[str, float]
    feature_version: str


@dataclass
class MarketStateSnapshot:
    state_probabilities: dict[str, float]
    selected_state: str


@dataclass
class ResearchSnapshot:
    side: str
    supporting_evidence: list[str]
    conflicting_evidence: list[str]
    historical_base_rate: float
    confidence: float
    sample_size: int


@dataclass
class OptionsSelection:
    contract_symbol: str
    strike: int
    expiry: datetime
    option_type: str
    spread: float
    is_proxy: bool


@dataclass
class DecisionRecord:
    action: DecisionAction
    confidence: float
    expected_movement: float
    expected_duration_bars: int
    entry_trigger: str
    invalidation: str
    target: str
    rejection_reasons: list[str]
    snapshot_id: str
    created_at: datetime
    signature: str = ""

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("DecisionRecord timestamp must be timezone-aware")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")

    def sign(self) -> str:
        payload = {
            "action": self.action.value,
            "confidence": round(self.confidence, 6),
            "expected_movement": round(self.expected_movement, 6),
            "expected_duration_bars": self.expected_duration_bars,
            "entry_trigger": self.entry_trigger,
            "invalidation": self.invalidation,
            "target": self.target,
            "rejection_reasons": self.rejection_reasons,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at.isoformat(),
        }
        digest = sha256(repr(payload).encode("utf-8")).hexdigest()
        self.signature = digest
        return digest

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["created_at"] = self.created_at.isoformat()
        return data


@dataclass
class PaperTrade:
    trade_id: str
    action: DecisionAction
    contract_symbol: str
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: datetime
    exit_time: datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None


@dataclass
class SessionRiskState:
    open_position: PaperTrade | None = None
    trades_today: int = 0
    daily_realized_pnl: float = 0.0
    cooldown_until: datetime | None = None


@dataclass
class PipelineState:
    """In-memory state used by orchestration and replay."""

    paper_mode: bool = True
    max_trades_per_session: int = 3
    daily_loss_limit: float = -3000.0
    session_risk: SessionRiskState = field(default_factory=SessionRiskState)
    audits: list[dict[str, Any]] = field(default_factory=list)

    def can_trade_now(self, at_time: datetime) -> bool:
        if self.session_risk.cooldown_until is None:
            return True
        return at_time >= self.session_risk.cooldown_until


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
