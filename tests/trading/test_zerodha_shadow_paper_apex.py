from __future__ import annotations

from pathlib import Path

import pytest

from quantlab.apex.agents.base import DecisionAction
from quantlab.apex.orchestration.pipeline import PipelineResult

from aadithya_quantlab.trading import zerodha_shadow_paper as shadow


class DummyPipeline:
    pass


class DummyKite:
    pass


def test_fetch_apex_trade_setup_returns_selected_contract(monkeypatch) -> None:
    def fake_run_live_nifty_apex_pipeline(kite, *, pipeline, lookback_bars):
        del kite, pipeline, lookback_bars
        return None, PipelineResult(
            action=DecisionAction.TRADE_CALL,
            reports={
                "options": {
                    "selection": {
                        "contract_symbol": "NIFTY26JUL25000CE",
                    }
                }
            },
        ), DummyPipeline()

    monkeypatch.setattr(shadow, "run_live_nifty_apex_pipeline", fake_run_live_nifty_apex_pipeline)

    setup = shadow.fetch_apex_trade_setup(DummyKite(), pipeline=DummyPipeline(), lookback_bars=40)
    assert setup == ("CALL", "NFO:NIFTY26JUL25000CE")


def test_auto_mode_rejects_manual_instrument(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="APEX selects the contract"):
        shadow.run_shadow_paper(
            instrument="NFO:NIFTY26JUL25000CE",
            side="AUTO",
            quantity=65,
            poll_seconds=0.0,
            max_ticks=1,
            reset=True,
            auto_nifty_atm=False,
            wait_for_market_open=False,
            wait_after_open_minutes=0,
            signal_poll_seconds=0.0,
            signal_max_checks=1,
            max_trades=1,
            reentry_wait_seconds=0.0,
            stop_new_entries_at=None,
            state_path=tmp_path / "state.json",
            entry_path=tmp_path / "entries.csv",
            trade_path=tmp_path / "trades.csv",
            config=shadow.AutoPaperConfig(),
        )