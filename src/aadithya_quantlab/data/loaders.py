"""Data loading helpers for canonical intraday OHLCV files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from aadithya_quantlab.data.contracts import (
    normalize_intraday_ohlcv,
    validate_intraday_ohlcv_schema,
)


def load_intraday_ohlcv_csv(path: str | Path, validate: bool = True) -> pd.DataFrame:
    """Load a canonical intraday OHLCV CSV file."""

    csv_path = Path(path)
    bars = normalize_intraday_ohlcv(pd.read_csv(csv_path))
    if validate:
        validate_intraday_ohlcv_schema(bars).raise_for_errors()
    return bars

