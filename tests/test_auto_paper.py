from __future__ import annotations

from datetime import date

from aadithya_quantlab.trading.auto_paper import (
    AutoPaperConfig,
    open_auto_paper_position,
    update_auto_paper_position,
)
from aadithya_quantlab.trading.zerodha_shadow_paper import (
    build_closed_trade_record,
    extract_last_price,
    load_position_state,
    resolve_nifty_atm_option,
    run_shadow_paper,
    save_position_state,
    signal_side_from_candles,
)


class FakeOptionKite:
    def ltp(self, instrument):
        return {instrument: {"last_price": 24211.0}}

    def instruments(self, exchange):
        assert exchange == "NFO"
        return [
            {
                "tradingsymbol": "NIFTY26JUL24150CE",
                "instrument_type": "CE",
                "expiry": date(2026, 7, 21),
                "strike": 24150,
            },
            {
                "tradingsymbol": "NIFTY26JUL24200CE",
                "instrument_type": "CE",
                "expiry": date(2026, 7, 21),
                "strike": 24200,
            },
            {
                "tradingsymbol": "NIFTY26JUL24250CE",
                "instrument_type": "CE",
                "expiry": date(2026, 7, 21),
                "strike": 24250,
            },
            {
                "tradingsymbol": "NIFTY26JUL24200PE",
                "instrument_type": "PE",
                "expiry": date(2026, 7, 21),
                "strike": 24200,
            },
        ]


def test_call_position_hits_10_percent_stoploss() -> None:
    config = AutoPaperConfig(stop_loss_pct=0.10, profit_trigger_points=10.0, trailing_points=5.0)
    position = open_auto_paper_position("CALL", 100.0, config)

    update = update_auto_paper_position(position, 90.0, config)

    assert update.status == "closed"
    assert update.closed_trade is not None
    assert update.closed_trade["pnl_points"] == -10.0


def test_call_position_trailing_after_profit_trigger() -> None:
    config = AutoPaperConfig(stop_loss_pct=0.10, profit_trigger_points=10.0, trailing_points=5.0)
    position = open_auto_paper_position("CALL", 100.0, config)

    first = update_auto_paper_position(position, 111.0, config)
    assert first.status == "open"
    assert first.position is not None
    assert first.position.trailing_active

    second = update_auto_paper_position(first.position, 106.0, config)
    assert second.status == "closed"
    assert second.closed_trade is not None
    assert second.closed_trade["pnl_points"] == 6.0


def test_put_position_trailing_after_profit_trigger() -> None:
    config = AutoPaperConfig(stop_loss_pct=0.10, profit_trigger_points=10.0, trailing_points=5.0)
    position = open_auto_paper_position("PUT", 100.0, config)

    first = update_auto_paper_position(position, 112.0, config)
    assert first.status == "open"
    assert first.position is not None
    assert first.position.trailing_active

    second = update_auto_paper_position(first.position, 107.0, config)
    assert second.status == "closed"
    assert second.closed_trade is not None
    assert second.closed_trade["pnl_points"] == 7.0


def test_shadow_paper_extracts_ltp_payload() -> None:
    price = extract_last_price({"NFO:NIFTY26JUL24200CE": {"last_price": 125.5}}, "NFO:NIFTY26JUL24200CE")

    assert price == 125.5


def test_shadow_paper_state_round_trip(tmp_path) -> None:
    config = AutoPaperConfig()
    position = open_auto_paper_position("CALL", 100.0, config, contract_symbol="NFO:NIFTY26JUL24200CE")
    state_path = tmp_path / "shadow_state.json"

    save_position_state(
        state_path,
        position=position,
        instrument="NFO:NIFTY26JUL24200CE",
        quantity=65,
    )

    loaded = load_position_state(state_path)
    assert loaded is not None
    loaded_position, loaded_instrument, loaded_quantity = loaded
    assert loaded_position.side == "CALL"
    assert loaded_instrument == "NFO:NIFTY26JUL24200CE"
    assert loaded_quantity == 65


def test_shadow_closed_trade_includes_quantity_and_gross_pnl() -> None:
    record = build_closed_trade_record(
        {"pnl_points": 6.0, "exit_reason": "TRAILING_STOP"},
        instrument="NFO:NIFTY26JUL24200CE",
        quantity=65,
    )

    assert record["instrument"] == "NFO:NIFTY26JUL24200CE"
    assert record["quantity"] == 65
    assert record["gross_pnl"] == 390.0


def test_shadow_paper_refuses_to_resume_different_side(tmp_path, monkeypatch) -> None:
    config = AutoPaperConfig()
    state_path = tmp_path / "shadow_state.json"
    position = open_auto_paper_position("CALL", 100.0, config, contract_symbol="NFO:NIFTY26JUL24200CE")
    save_position_state(
        state_path,
        position=position,
        instrument="NFO:NIFTY26JUL24200CE",
        quantity=65,
    )
    monkeypatch.setattr(
        "aadithya_quantlab.trading.zerodha_shadow_paper.build_safe_kite_client_from_env",
        lambda: object(),
    )

    try:
        run_shadow_paper(
            instrument="NFO:NIFTY26JUL24200CE",
            side="PUT",
            quantity=65,
            poll_seconds=0,
            max_ticks=1,
            reset=False,
            state_path=state_path,
            auto_nifty_atm=False,
            wait_for_market_open=False,
            wait_after_open_minutes=0,
            signal_poll_seconds=0,
            signal_max_checks=1,
            max_trades=1,
            reentry_wait_seconds=0,
            stop_new_entries_at=None,
            entry_path=tmp_path / "entries.csv",
            trade_path=tmp_path / "trades.csv",
            config=config,
        )
    except ValueError as exc:
        assert "Saved paper state is CALL" in str(exc)
    else:
        raise AssertionError("Expected saved side mismatch to be rejected")


def test_shadow_paper_auto_selects_nifty_atm_call() -> None:
    instrument = resolve_nifty_atm_option(FakeOptionKite(), side="CALL", today=date(2026, 7, 14))

    assert instrument == "NFO:NIFTY26JUL24200CE"


def test_shadow_paper_auto_selects_nifty_atm_put() -> None:
    instrument = resolve_nifty_atm_option(FakeOptionKite(), side="PUT", today=date(2026, 7, 14))

    assert instrument == "NFO:NIFTY26JUL24200PE"


def test_previous_runner_signal_logic_selects_call() -> None:
    candles = [
        {"date": "2026-07-14T09:15:00+05:30", "high": 100, "low": 95, "close": 98},
        {"date": "2026-07-14T09:20:00+05:30", "high": 101, "low": 96, "close": 99},
        {"date": "2026-07-14T09:25:00+05:30", "high": 102, "low": 97, "close": 100},
        {"date": "2026-07-14T09:30:00+05:30", "high": 103, "low": 98, "close": 101},
        {"date": "2026-07-14T09:35:00+05:30", "high": 104, "low": 99, "close": 102},
        {"date": "2026-07-14T09:40:00+05:30", "high": 108, "low": 102, "close": 106},
    ]

    assert signal_side_from_candles(candles) == "CALL"


def test_previous_runner_signal_logic_selects_put() -> None:
    candles = [
        {"date": "2026-07-14T09:15:00+05:30", "high": 110, "low": 104, "close": 108},
        {"date": "2026-07-14T09:20:00+05:30", "high": 109, "low": 103, "close": 107},
        {"date": "2026-07-14T09:25:00+05:30", "high": 108, "low": 102, "close": 106},
        {"date": "2026-07-14T09:30:00+05:30", "high": 107, "low": 101, "close": 105},
        {"date": "2026-07-14T09:35:00+05:30", "high": 106, "low": 100, "close": 104},
        {"date": "2026-07-14T09:40:00+05:30", "high": 101, "low": 96, "close": 98},
    ]

    assert signal_side_from_candles(candles) == "PUT"
