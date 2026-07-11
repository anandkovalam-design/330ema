import pandas as pd

from aadithya_quantlab.research.market_state_taxonomy import (
    LabelerConfig,
    MarketState,
    label_market_states,
)


def _bars(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> pd.DataFrame:
    highs = highs or [close + 0.5 for close in closes]
    lows = lows or [close - 0.5 for close in closes]
    return pd.DataFrame(
        {
            "symbol": ["NIFTY"] * len(closes),
            "timestamp": pd.date_range("2026-01-01 09:15", periods=len(closes), freq="5min"),
            "session_date": [pd.Timestamp("2026-01-01").date()] * len(closes),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )


def test_labels_opening_discovery_first() -> None:
    labeled = label_market_states(_bars([100.0, 100.2, 100.1, 100.0]))

    assert labeled.loc[0, "state_id"] == MarketState.OPENING_DISCOVERY.value
    assert labeled.loc[2, "state_id"] == MarketState.OPENING_DISCOVERY.value


def test_labels_directional_expansion_up() -> None:
    labeled = label_market_states(
        _bars([100.0, 100.1, 100.2, 101.8, 103.2, 104.5]),
        LabelerConfig(feature_window_bars=3),
    )

    assert MarketState.DIRECTIONAL_EXPANSION_UP.value in set(labeled["state_id"])


def test_labels_directional_expansion_down() -> None:
    labeled = label_market_states(
        _bars([100.0, 99.9, 99.8, 98.2, 96.8, 95.5]),
        LabelerConfig(feature_window_bars=3),
    )

    assert MarketState.DIRECTIONAL_EXPANSION_DOWN.value in set(labeled["state_id"])


def test_labels_failed_breakout_after_reentry() -> None:
    labeled = label_market_states(
        _bars([100.0, 100.2, 100.1, 101.2, 100.3]),
        LabelerConfig(feature_window_bars=2, range_extension_threshold=1.0),
    )

    assert labeled.loc[4, "state_id"] == MarketState.FAILED_BREAKOUT.value

