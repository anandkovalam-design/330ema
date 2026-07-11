"""Canonical data contracts for Aadithya QuantLab."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


REQUIRED_INTRADAY_OHLCV_COLUMNS = (
    "symbol",
    "timestamp",
    "session_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
)

OPTIONAL_INTRADAY_OHLCV_COLUMNS = ("adjustment_flag",)


@dataclass(frozen=True)
class ValidationIssue:
    """A single data validation issue."""

    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ValidationResult:
    """Validation result for a dataset contract."""

    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def raise_for_errors(self) -> None:
        errors = [issue for issue in self.issues if issue.severity == "error"]
        if errors:
            joined = "; ".join(f"{issue.code}: {issue.message}" for issue in errors)
            raise ValueError(joined)


def validate_intraday_ohlcv_schema(bars: pd.DataFrame) -> ValidationResult:
    """Validate canonical intraday OHLCV structure and invariants."""

    issues: list[ValidationIssue] = []
    missing = [column for column in REQUIRED_INTRADAY_OHLCV_COLUMNS if column not in bars.columns]
    if missing:
        issues.append(
            ValidationIssue("missing_columns", f"Missing required columns: {', '.join(missing)}")
        )
        return ValidationResult(tuple(issues))

    if bars.empty:
        issues.append(ValidationIssue("empty_dataset", "Dataset has no rows."))
        return ValidationResult(tuple(issues))

    numeric_columns = ("open", "high", "low", "close", "volume")
    for column in numeric_columns:
        if not pd.api.types.is_numeric_dtype(bars[column]):
            issues.append(ValidationIssue("invalid_type", f"{column} must be numeric."))

    if not pd.api.types.is_datetime64_any_dtype(bars["timestamp"]):
        issues.append(ValidationIssue("invalid_type", "timestamp must be datetime-like."))

    if bars[["symbol", "timestamp", "session_date"]].isna().any().any():
        issues.append(
            ValidationIssue(
                "null_identity_fields",
                "symbol, timestamp, and session_date cannot contain null values.",
            )
        )

    if bars[list(numeric_columns)].isna().any().any():
        issues.append(ValidationIssue("null_price_fields", "OHLCV columns cannot contain nulls."))

    if (bars["high"] < bars[["open", "close"]].max(axis=1)).any():
        issues.append(ValidationIssue("invalid_high", "high must be >= open and close."))

    if (bars["low"] > bars[["open", "close"]].min(axis=1)).any():
        issues.append(ValidationIssue("invalid_low", "low must be <= open and close."))

    if (bars["volume"] < 0).any():
        issues.append(ValidationIssue("negative_volume", "volume cannot be negative."))

    sorted_bars = bars.sort_values(["symbol", "session_date", "timestamp"])
    duplicate_keys = sorted_bars.duplicated(["symbol", "session_date", "timestamp"])
    if duplicate_keys.any():
        issues.append(
            ValidationIssue(
                "duplicate_bar_keys",
                "symbol, session_date, and timestamp must identify one bar.",
            )
        )

    monotonic = sorted_bars.groupby(["symbol", "session_date"], sort=False)["timestamp"].apply(
        lambda values: values.is_monotonic_increasing
    )
    if not monotonic.all():
        issues.append(
            ValidationIssue(
                "non_monotonic_timestamps",
                "timestamp must be monotonic within symbol and session_date.",
            )
        )

    return ValidationResult(tuple(issues))


def normalize_intraday_ohlcv(bars: pd.DataFrame) -> pd.DataFrame:
    """Normalize common CSV-loaded values into the canonical intraday contract."""

    output = bars.copy()
    output.columns = [str(column).strip() for column in output.columns]
    output["symbol"] = output["symbol"].astype(str).str.strip()
    output["timestamp"] = pd.to_datetime(output["timestamp"], errors="coerce")
    output["session_date"] = pd.to_datetime(output["session_date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close", "volume"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output["source"] = output["source"].astype(str).str.strip()
    if "adjustment_flag" not in output.columns:
        output["adjustment_flag"] = ""
    output["adjustment_flag"] = output["adjustment_flag"].fillna("").astype(str).str.strip()
    return output.sort_values(["symbol", "session_date", "timestamp"]).reset_index(drop=True)
