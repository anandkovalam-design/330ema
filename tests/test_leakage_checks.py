from aadithya_quantlab.data.samples import make_synthetic_intraday_bars
from aadithya_quantlab.experiments.exp001_baseline_labeler import run_exp001
from aadithya_quantlab.validation.leakage import check_replay_safe_intraday_bars


def test_replay_safe_check_rejects_forward_columns() -> None:
    bars = make_synthetic_intraday_bars(("up",), bars_per_session=5)
    bars["forward_return"] = 0.0

    result = check_replay_safe_intraday_bars(bars)

    assert not result.ok


def test_exp001_rejects_leaky_input() -> None:
    bars = make_synthetic_intraday_bars(("up",), bars_per_session=5)
    bars["target"] = 1

    try:
        run_exp001(bars)
    except ValueError:
        return
    raise AssertionError("Expected EXP-001 to reject leaky target column.")
