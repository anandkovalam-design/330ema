"""Small deterministic datasets for research smoke tests."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def make_synthetic_intraday_bars(
    session_patterns: Iterable[str] = ("up", "range", "down"),
    bars_per_session: int = 78,
    symbol: str = "NIFTY",
) -> pd.DataFrame:
    """Create deterministic OHLCV bars for validation and examples."""

    rows: list[dict[str, object]] = []
    base_date = pd.Timestamp("2026-01-01")
    for session_offset, pattern in enumerate(session_patterns):
        session_date = (base_date + pd.Timedelta(days=session_offset)).date()
        start = pd.Timestamp.combine(session_date, pd.Timestamp("09:15").time())
        close = 22000.0 + session_offset * 100.0

        for bar_index in range(bars_per_session):
            timestamp = start + pd.Timedelta(minutes=5 * bar_index)
            step = _pattern_step(pattern, bar_index)
            open_price = close
            close = close + step
            range_width = 8.0 + (bar_index % 5)
            high = max(open_price, close) + range_width / 2.0
            low = min(open_price, close) - range_width / 2.0
            volume = 1000.0 + 20.0 * (bar_index % 10)
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "session_date": session_date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )

    return pd.DataFrame(rows)


def _pattern_step(pattern: str, bar_index: int) -> float:
    if bar_index < 3:
        return [2.0, -1.0, 1.5][bar_index]
    if pattern == "up":
        return 12.0 if bar_index < 55 else 5.0
    if pattern == "down":
        return -12.0 if bar_index < 55 else -5.0
    if pattern == "range":
        return 4.0 if bar_index % 2 == 0 else -4.0
    if pattern == "failed_up":
        if bar_index in (3, 4):
            return 18.0
        if bar_index == 5:
            return -26.0
        return 2.0 if bar_index % 2 == 0 else -2.0
    raise ValueError(f"unknown synthetic pattern: {pattern}")

