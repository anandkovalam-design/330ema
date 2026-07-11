"""Paper 001 market-state taxonomy primitives and baseline labeler."""

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


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


@dataclass(frozen=True)
class LabelerConfig:
    """Research defaults for the Paper 001 deterministic labeler."""

    opening_window_bars: int = 3
    feature_window_bars: int = 5
    baseline_window_bars: int = 20
    late_session_start_bar: int = 60
    range_extension_threshold: float = 1.20
    efficiency_threshold: float = 0.55
    compression_threshold: float = 0.75
    near_vwap_atr: float = 0.50
    epsilon: float = 1e-12
    labeler_version: str = "paper-001-v0.2"


REQUIRED_COLUMNS = (
    "symbol",
    "timestamp",
    "session_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def compute_market_state_features(
    bars: pd.DataFrame, config: LabelerConfig | None = None
) -> pd.DataFrame:
    """Compute leak-free Paper 001 features for intraday bars."""

    config = config or LabelerConfig()
    missing = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    output = bars.copy()
    output = output.sort_values(["symbol", "session_date", "timestamp"]).reset_index(drop=True)

    output["bar_index"] = output.groupby(["symbol", "session_date"], sort=False).cumcount()
    output["typical_price"] = (output["high"] + output["low"] + output["close"]) / 3.0
    def add_session_features(session: pd.DataFrame) -> pd.DataFrame:
        session = session.copy()
        opening_rows = session.iloc[: config.opening_window_bars]
        opening_high = float(opening_rows["high"].max())
        opening_low = float(opening_rows["low"].min())
        opening_width = max(opening_high - opening_low, config.epsilon)

        session["opening_range_high"] = opening_high
        session["opening_range_low"] = opening_low
        session["opening_range_mid"] = (opening_high + opening_low) / 2.0
        session["opening_range_width"] = opening_width
        session["opening_range_location"] = (
            session["close"] - session["opening_range_low"]
        ) / opening_width

        volume = session["volume"].astype(float).clip(lower=0.0)
        weighted_price = session["typical_price"] * volume
        cumulative_volume = volume.cumsum()
        fallback_vwap = session["typical_price"].expanding(min_periods=1).mean()
        session["vwap"] = np.where(
            cumulative_volume > 0,
            weighted_price.cumsum() / cumulative_volume.replace(0.0, np.nan),
            fallback_vwap,
        )

        previous_close = session["close"].shift(1)
        true_range = pd.concat(
            [
                session["high"] - session["low"],
                (session["high"] - previous_close).abs(),
                (session["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        session["true_range"] = true_range.fillna(session["high"] - session["low"])
        session["atr"] = session["true_range"].rolling(
            config.feature_window_bars, min_periods=1
        ).mean()
        session["atr_baseline"] = session["atr"].rolling(
            config.baseline_window_bars, min_periods=1
        ).median()
        session["compression_score"] = session["atr"] / session["atr_baseline"].clip(
            lower=config.epsilon
        )

        returns = session["close"].pct_change().fillna(0.0)
        session["realized_volatility"] = returns.rolling(
            config.feature_window_bars, min_periods=1
        ).std(ddof=0).fillna(0.0)

        price_change = session["close"].diff().abs().fillna(0.0)
        path_length = price_change.rolling(config.feature_window_bars, min_periods=1).sum()
        window_start_close = session["close"].shift(config.feature_window_bars - 1)
        displacement = (session["close"] - window_start_close).fillna(
            session["close"] - session["close"].iloc[0]
        )
        efficiency = displacement.abs() / path_length.clip(lower=config.epsilon)
        session["directional_efficiency"] = efficiency.clip(upper=1.0)
        session["signed_directional_efficiency"] = (
            np.sign(displacement) * session["directional_efficiency"]
        )

        session["session_high_to_date"] = session["high"].cummax()
        session["session_low_to_date"] = session["low"].cummin()
        session["range_extension_ratio"] = (
            session["session_high_to_date"] - session["session_low_to_date"]
        ) / opening_width
        session["vwap_displacement"] = (session["close"] - session["vwap"]) / session[
            "atr"
        ].clip(lower=config.epsilon)

        previous_outside_up = session["close"].shift(1) > opening_high
        previous_outside_down = session["close"].shift(1) < opening_low
        current_inside = session["close"].between(opening_low, opening_high, inclusive="both")
        session["failed_breakout_up"] = previous_outside_up & current_inside
        session["failed_breakout_down"] = previous_outside_down & current_inside
        return session

    session_frames = [
        add_session_features(session)
        for _, session in output.groupby(["symbol", "session_date"], sort=False)
    ]
    return pd.concat(session_frames, ignore_index=True)


def label_market_states(
    bars: pd.DataFrame, config: LabelerConfig | None = None
) -> pd.DataFrame:
    """Assign deterministic Paper 001 market-state labels."""

    config = config or LabelerConfig()
    features = compute_market_state_features(bars, config)
    labels = features.apply(lambda row: _label_row(row, config), axis=1)
    features["state_id"] = [label.state.value for label in labels]
    features["state_name"] = [STATE_NAMES[label.state] for label in labels]
    features["state_confidence"] = [label.confidence for label in labels]
    features["reason_codes"] = ["|".join(label.reason_codes) for label in labels]
    features["labeler_version"] = config.labeler_version
    return features


def _label_row(row: pd.Series, config: LabelerConfig) -> StateLabel:
    if row["bar_index"] < config.opening_window_bars:
        return StateLabel(
            MarketState.OPENING_DISCOVERY,
            0.90,
            ("time_bucket_open",),
            config.labeler_version,
        )

    expansion_up = (
        row["close"] > row["opening_range_high"]
        and row["signed_directional_efficiency"] >= config.efficiency_threshold
        and row["range_extension_ratio"] >= config.range_extension_threshold
    )
    expansion_down = (
        row["close"] < row["opening_range_low"]
        and row["signed_directional_efficiency"] <= -config.efficiency_threshold
        and row["range_extension_ratio"] >= config.range_extension_threshold
    )

    if row["bar_index"] >= config.late_session_start_bar and (
        expansion_up or expansion_down or row["range_extension_ratio"] >= config.range_extension_threshold
    ):
        return StateLabel(
            MarketState.LATE_SESSION_REPRICING,
            0.70,
            ("late_session", "elevated_range_extension"),
            config.labeler_version,
        )

    if bool(row["failed_breakout_up"]) or bool(row["failed_breakout_down"]):
        direction = "failed_breakout_up" if bool(row["failed_breakout_up"]) else "failed_breakout_down"
        return StateLabel(
            MarketState.FAILED_BREAKOUT,
            0.80,
            (direction,),
            config.labeler_version,
        )

    if expansion_up:
        return StateLabel(
            MarketState.DIRECTIONAL_EXPANSION_UP,
            0.78,
            ("above_opening_range", "positive_efficiency", "range_extension"),
            config.labeler_version,
        )

    if expansion_down:
        return StateLabel(
            MarketState.DIRECTIONAL_EXPANSION_DOWN,
            0.78,
            ("below_opening_range", "negative_efficiency", "range_extension"),
            config.labeler_version,
        )

    if (
        row["compression_score"] <= config.compression_threshold
        and row["directional_efficiency"] < config.efficiency_threshold
    ):
        return StateLabel(
            MarketState.VOLATILITY_COMPRESSION,
            0.68,
            ("low_compression_score", "low_efficiency"),
            config.labeler_version,
        )

    near_vwap = abs(row["vwap_displacement"]) <= config.near_vwap_atr
    inside_opening_range = row["opening_range_low"] <= row["close"] <= row["opening_range_high"]
    if (near_vwap or inside_opening_range) and row["directional_efficiency"] < config.efficiency_threshold:
        return StateLabel(
            MarketState.BALANCED_RANGE,
            0.64,
            ("near_value_or_inside_opening_range", "low_efficiency"),
            config.labeler_version,
        )

    return StateLabel(
        MarketState.DISORDERLY_NOISY,
        0.50,
        ("fallback",),
        config.labeler_version,
    )
