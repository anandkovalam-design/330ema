import pandas as pd

from aadithya_quantlab.data.contracts import (
    normalize_intraday_ohlcv,
    validate_intraday_ohlcv_schema,
)
from aadithya_quantlab.data.loaders import load_intraday_ohlcv_csv
from aadithya_quantlab.data.samples import make_synthetic_intraday_bars


def _canonical_bars() -> pd.DataFrame:
    bars = make_synthetic_intraday_bars(("up",), bars_per_session=5)
    bars["source"] = "synthetic"
    bars["adjustment_flag"] = ""
    return bars


def test_validate_intraday_ohlcv_accepts_canonical_bars() -> None:
    result = validate_intraday_ohlcv_schema(_canonical_bars())

    assert result.ok


def test_validate_intraday_ohlcv_rejects_bad_high() -> None:
    bars = _canonical_bars()
    bars.loc[0, "high"] = bars.loc[0, "close"] - 1.0

    result = validate_intraday_ohlcv_schema(bars)

    assert not result.ok
    assert any(issue.code == "invalid_high" for issue in result.issues)


def test_load_intraday_ohlcv_csv_normalizes_and_validates(tmp_path) -> None:
    bars = _canonical_bars()
    csv_path = tmp_path / "bars.csv"
    bars.to_csv(csv_path, index=False)

    loaded = load_intraday_ohlcv_csv(csv_path)

    assert loaded["timestamp"].dtype.kind == "M"
    assert len(loaded) == len(bars)


def test_normalize_intraday_ohlcv_adds_adjustment_flag() -> None:
    bars = _canonical_bars().drop(columns=["adjustment_flag"])

    normalized = normalize_intraday_ohlcv(bars)

    assert "adjustment_flag" in normalized.columns
