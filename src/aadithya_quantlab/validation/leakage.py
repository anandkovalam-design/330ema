"""Leakage and replay-readiness checks."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class LeakageCheckResult:
    """Result of replay-safety checks."""

    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0

    def raise_for_errors(self) -> None:
        if self.issues:
            raise ValueError("; ".join(self.issues))


FORBIDDEN_LABEL_INPUT_COLUMNS = (
    "forward_return",
    "future_close",
    "mfe",
    "mae",
    "target",
    "label",
)


def check_replay_safe_intraday_bars(bars: pd.DataFrame) -> LeakageCheckResult:
    """Check whether input bars are safe for replay-style feature computation."""

    issues: list[str] = []
    lower_columns = {column.lower() for column in bars.columns}
    forbidden = sorted(set(FORBIDDEN_LABEL_INPUT_COLUMNS).intersection(lower_columns))
    if forbidden:
        issues.append(f"Forbidden future/target columns present: {', '.join(forbidden)}")

    if {"symbol", "session_date", "timestamp"}.issubset(bars.columns):
        duplicate_keys = bars.duplicated(["symbol", "session_date", "timestamp"])
        if duplicate_keys.any():
            issues.append("Duplicate symbol/session_date/timestamp rows break replay order.")

        sorted_bars = bars.sort_values(["symbol", "session_date", "timestamp"])
        monotonic = sorted_bars.groupby(["symbol", "session_date"], sort=False)["timestamp"].apply(
            lambda values: values.is_monotonic_increasing
        )
        if not monotonic.all():
            issues.append("Timestamps must be monotonic inside each symbol/session_date group.")

    session_full_day_columns = [
        column
        for column in bars.columns
        if column.lower().startswith(("session_final_", "day_final_", "full_session_"))
    ]
    if session_full_day_columns:
        issues.append(
            "Full-session columns are not replay-safe before the close: "
            + ", ".join(session_full_day_columns)
        )

    return LeakageCheckResult(tuple(issues))

