"""EXP-001 baseline deterministic labeler runner."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aadithya_quantlab.research.market_state_taxonomy import LabelerConfig, label_market_states
from aadithya_quantlab.validation.leakage import check_replay_safe_intraday_bars
from aadithya_quantlab.validation.market_state_reports import (
    MarketStateReport,
    build_market_state_report,
)


@dataclass(frozen=True)
class Exp001Result:
    """Outputs produced by the EXP-001 runner."""

    labeled_bars: pd.DataFrame
    report: MarketStateReport


def run_exp001(
    bars: pd.DataFrame,
    config: LabelerConfig | None = None,
    forward_horizons: tuple[int, ...] = (1, 3, 6),
) -> Exp001Result:
    """Run Paper 001 baseline labeling and diagnostics."""

    check_replay_safe_intraday_bars(bars).raise_for_errors()
    labeled = label_market_states(bars, config)
    report = build_market_state_report(labeled, forward_horizons)
    return Exp001Result(labeled_bars=labeled, report=report)
