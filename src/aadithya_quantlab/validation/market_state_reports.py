"""Validation reports for Paper 001 market-state labels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class MarketStateReport:
    """Container for EXP-001 diagnostic outputs."""

    state_counts: pd.DataFrame
    transition_matrix: pd.DataFrame
    duration_summary: pd.DataFrame
    forward_return_summary: pd.DataFrame


def build_market_state_report(
    labeled_bars: pd.DataFrame,
    forward_horizons: tuple[int, ...] = (1, 3, 6),
) -> MarketStateReport:
    """Build EXP-001 diagnostics from labeled intraday bars."""

    required = {"symbol", "session_date", "timestamp", "close", "state_id", "state_name"}
    missing = sorted(required.difference(labeled_bars.columns))
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    bars = labeled_bars.sort_values(["symbol", "session_date", "timestamp"]).copy()
    return MarketStateReport(
        state_counts=_state_counts(bars),
        transition_matrix=_transition_matrix(bars),
        duration_summary=_duration_summary(bars),
        forward_return_summary=_forward_return_summary(bars, forward_horizons),
    )


def format_market_state_report_markdown(
    report: MarketStateReport,
    title: str = "EXP-001 Market State Report",
) -> str:
    """Render an EXP-001 report as Markdown."""

    sections = [
        f"# {title}",
        "## State Counts",
        _to_markdown(report.state_counts),
        "## Transition Matrix",
        _to_markdown(report.transition_matrix.reset_index()),
        "## Duration Summary",
        _to_markdown(report.duration_summary),
        "## Forward Return Summary",
        _to_markdown(report.forward_return_summary),
    ]
    return "\n\n".join(sections) + "\n"


def write_market_state_report_markdown(
    report: MarketStateReport,
    output_path: str | Path,
    title: str = "EXP-001 Market State Report",
) -> Path:
    """Write an EXP-001 Markdown report and return its path."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_market_state_report_markdown(report, title), encoding="utf-8")
    return path


def _state_counts(bars: pd.DataFrame) -> pd.DataFrame:
    counts = (
        bars.groupby(["state_id", "state_name"], observed=True)
        .size()
        .rename("bar_count")
        .reset_index()
    )
    counts["bar_percent"] = counts["bar_count"] / counts["bar_count"].sum()
    return counts.sort_values(["state_id"]).reset_index(drop=True)


def _transition_matrix(bars: pd.DataFrame) -> pd.DataFrame:
    grouped = bars.groupby(["symbol", "session_date"], sort=False, group_keys=False)
    transitions = bars[["state_id"]].copy()
    transitions["next_state_id"] = grouped["state_id"].shift(-1)
    transitions = transitions.dropna(subset=["next_state_id"])
    if transitions.empty:
        return pd.DataFrame()
    return pd.crosstab(
        transitions["state_id"],
        transitions["next_state_id"],
        normalize="index",
    )


def _duration_summary(bars: pd.DataFrame) -> pd.DataFrame:
    grouped = bars.groupby(["symbol", "session_date"], sort=False, group_keys=False)
    state_changed = grouped["state_id"].shift(1) != bars["state_id"]
    segments = bars[["symbol", "session_date", "state_id", "state_name"]].copy()
    segments["segment_id"] = state_changed.groupby(
        [bars["symbol"], bars["session_date"]], sort=False
    ).cumsum()

    durations = (
        segments.groupby(["symbol", "session_date", "segment_id", "state_id", "state_name"])
        .size()
        .rename("duration_bars")
        .reset_index()
    )
    return (
        durations.groupby(["state_id", "state_name"], observed=True)["duration_bars"]
        .agg(segment_count="count", median_bars="median", mean_bars="mean", max_bars="max")
        .reset_index()
        .sort_values("state_id")
        .reset_index(drop=True)
    )


def _forward_return_summary(
    bars: pd.DataFrame, forward_horizons: tuple[int, ...]
) -> pd.DataFrame:
    grouped = bars.groupby(["symbol", "session_date"], sort=False, group_keys=False)
    rows: list[dict[str, object]] = []
    for horizon in forward_horizons:
        forward_close = grouped["close"].shift(-horizon)
        forward_return = forward_close / bars["close"] - 1.0
        summary_frame = bars[["state_id", "state_name"]].copy()
        summary_frame["forward_return"] = forward_return
        summary = (
            summary_frame.dropna(subset=["forward_return"])
            .groupby(["state_id", "state_name"], observed=True)["forward_return"]
            .agg(observation_count="count", mean_return="mean", median_return="median")
            .reset_index()
        )
        summary["horizon_bars"] = horizon
        rows.extend(summary.to_dict("records"))

    if not rows:
        return pd.DataFrame(
            columns=[
                "state_id",
                "state_name",
                "observation_count",
                "mean_return",
                "median_return",
                "horizon_bars",
            ]
        )
    return pd.DataFrame(rows).sort_values(["horizon_bars", "state_id"]).reset_index(drop=True)


def _to_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    rows = [_format_markdown_row(frame.columns)]
    rows.append(_format_markdown_row(["---"] * len(frame.columns)))
    for _, row in frame.iterrows():
        rows.append(_format_markdown_row(_format_cell(value) for value in row.tolist()))
    return "\n".join(rows)


def _format_markdown_row(values) -> str:  # type: ignore[no-untyped-def]
    return "| " + " | ".join(str(value) for value in values) + " |"


def _format_cell(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)
