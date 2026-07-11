"""Paper 001 market-state taxonomy primitives."""

from dataclasses import dataclass
from enum import Enum


class MarketState(str, Enum):
    """Initial Paper 001 intraday NIFTY market states."""

    OPENING_DISCOVERY = "S01"
    DIRECTIONAL_EXPANSION_UP = "S02"
    DIRECTIONAL_EXPANSION_DOWN = "S03"
    BALANCED_RANGE = "S04"
    VOLATILITY_COMPRESSION = "S05"
    FAILED_BREAKOUT = "S06"
    LATE_SESSION_REPRICING = "S07"
    DISORDERLY_NOISY = "S08"


STATE_NAMES: dict[MarketState, str] = {
    MarketState.OPENING_DISCOVERY: "Opening Discovery",
    MarketState.DIRECTIONAL_EXPANSION_UP: "Directional Expansion Up",
    MarketState.DIRECTIONAL_EXPANSION_DOWN: "Directional Expansion Down",
    MarketState.BALANCED_RANGE: "Balanced Range",
    MarketState.VOLATILITY_COMPRESSION: "Volatility Compression",
    MarketState.FAILED_BREAKOUT: "Failed Breakout",
    MarketState.LATE_SESSION_REPRICING: "Late Session Repricing",
    MarketState.DISORDERLY_NOISY: "Disorderly / Noisy",
}


@dataclass(frozen=True)
class StateLabel:
    """A reproducible label emitted by a market-state labeler."""

    state: MarketState
    confidence: float
    reason_codes: tuple[str, ...]
    labeler_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

