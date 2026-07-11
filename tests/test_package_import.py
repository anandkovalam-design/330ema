from aadithya_quantlab import __version__
from aadithya_quantlab.research.market_state_taxonomy import MarketState, StateLabel


def test_package_imports() -> None:
    assert __version__ == "0.1.0"


def test_paper_001_taxonomy_primitives() -> None:
    label = StateLabel(
        state=MarketState.OPENING_DISCOVERY,
        confidence=0.75,
        reason_codes=("time_bucket_open",),
        labeler_version="paper-001-v0.1",
    )

    assert label.state.value == "S01"
