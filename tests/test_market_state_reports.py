from aadithya_quantlab.data.samples import make_synthetic_intraday_bars
from aadithya_quantlab.experiments.exp001_baseline_labeler import run_exp001
from aadithya_quantlab.research.market_state_taxonomy import MarketState
from aadithya_quantlab.validation.market_state_reports import (
    format_market_state_report_markdown,
)


def test_exp001_builds_core_reports() -> None:
    bars = make_synthetic_intraday_bars(("up", "range", "down"), bars_per_session=30)
    result = run_exp001(bars, forward_horizons=(1, 3))

    assert not result.labeled_bars.empty
    assert not result.report.state_counts.empty
    assert not result.report.transition_matrix.empty
    assert not result.report.duration_summary.empty
    assert not result.report.forward_return_summary.empty


def test_exp001_state_counts_cover_all_labeled_bars() -> None:
    bars = make_synthetic_intraday_bars(("up", "failed_up"), bars_per_session=20)
    result = run_exp001(bars)

    assert result.report.state_counts["bar_count"].sum() == len(result.labeled_bars)
    assert MarketState.OPENING_DISCOVERY.value in set(result.report.state_counts["state_id"])


def test_exp001_markdown_report_contains_core_sections() -> None:
    bars = make_synthetic_intraday_bars(("up", "range"), bars_per_session=20)
    result = run_exp001(bars)

    markdown = format_market_state_report_markdown(result.report)

    assert "## State Counts" in markdown
    assert "## Transition Matrix" in markdown
    assert "## Duration Summary" in markdown
    assert "## Forward Return Summary" in markdown
