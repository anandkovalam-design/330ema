"""Typed, validated base contracts for APEX agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AgentStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


class DataValidationStatus(str, Enum):
    DATA_VALID = "DATA_VALID"
    DATA_DEGRADED = "DATA_DEGRADED"
    DATA_INVALID = "DATA_INVALID"


class RiskApproval(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DecisionAction(str, Enum):
    TRADE_CALL = "TRADE_CALL"
    TRADE_PUT = "TRADE_PUT"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class AgentReport:
    """Common validated output schema shared by every agent."""

    agent_name: str
    timestamp: datetime
    status: AgentStatus
    evidence: list[str] = field(default_factory=list)
    conflicting_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    sample_size: int = 0
    reason_codes: list[str] = field(default_factory=list)
    data_version: str = "v0"
    feature_version: str = "v0"
    strategy_version: str = "v0"

    def __post_init__(self) -> None:
        if not self.agent_name:
            raise ValueError("agent_name is required")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.sample_size < 0:
            raise ValueError("sample_size cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        data["status"] = self.status.value
        return data


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    arrived_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Candle timestamp must be timezone-aware")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("Malformed candle high/low range")
        if self.volume < 0:
            raise ValueError("Volume cannot be negative")


@dataclass
class OptionContract:
    symbol: str
    expiry: datetime
    strike: int
    option_type: str
    bid: float
    ask: float
    ltp: float
    oi: int

    def __post_init__(self) -> None:
        if self.option_type not in {"CE", "PE"}:
            raise ValueError("option_type must be CE or PE")
        if self.expiry.tzinfo is None:
            raise ValueError("Option expiry must be timezone-aware")
        if self.ask < self.bid:
            raise ValueError("Option ask cannot be below bid")
        if self.strike <= 0:
            raise ValueError("Option strike must be positive")


@dataclass
class MarketSnapshot:
    """Frozen snapshot consumed by all agents."""

    snapshot_id: str
    captured_at: datetime
    nifty_spot: list[Candle]
    nifty_futures: list[Candle]
    india_vix: list[Candle]
    option_chain: list[OptionContract]

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if not self.snapshot_id:
            raise ValueError("snapshot_id is required")


class ApexAgent(ABC):
    """Base class for deterministic APEX agents."""

    agent_name: str

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> AgentReport:
        raise NotImplementedError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
