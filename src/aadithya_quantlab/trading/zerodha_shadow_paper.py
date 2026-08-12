"""Live-data shadow paper trading for NIFTY option contracts.

This module never places broker orders. It watches live LTP, opens one virtual
position, and records paper exits from premium-based stop/target rules.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import date, datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aadithya_quantlab.trading.auto_paper import (
    AutoPaperConfig,
    AutoPaperPosition,
    append_auto_paper_entry,
    append_auto_paper_trade,
    open_auto_paper_position,
    update_auto_paper_position,
)
from aadithya_quantlab.trading.apex_live import run_live_nifty_apex_pipeline
from aadithya_quantlab.trading.zerodha_live import build_safe_kite_client_from_env
from quantlab.apex.agents.base import DecisionAction
from quantlab.apex.orchestration.pipeline import ApexPipeline


DEFAULT_STATE_PATH = Path("outputs/zerodha/shadow_paper_position.json")
DEFAULT_ENTRY_PATH = Path("outputs/zerodha/shadow_paper_entries.csv")
DEFAULT_TRADE_PATH = Path("outputs/zerodha/shadow_paper_trades.csv")
DEFAULT_NIFTY_QUANTITY = 65
DEFAULT_SENSEX_QUANTITY = 20
IST = ZoneInfo("Asia/Kolkata")
NIFTY_SPOT_INSTRUMENT = "NSE:NIFTY 50"
NIFTY_SPOT_INSTRUMENT_TOKEN = 256265


def extract_last_price(ltp_payload: dict[str, Any], instrument: str) -> float:
    """Extract the last traded price from a Kite LTP response."""

    instrument_data = ltp_payload.get(instrument)
    if not isinstance(instrument_data, dict):
        raise ValueError(f"No LTP returned for {instrument}.")

    price = float(instrument_data.get("last_price", 0.0) or 0.0)
    if price <= 0:
        raise ValueError(f"Invalid LTP returned for {instrument}: {price}.")
    return price


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def signal_side_from_candles(candles: list[dict[str, Any]]) -> str | None:
    """Previous runner logic: 5-bar breakout plus SMA slope."""

    if len(candles) < 6:
        return None

    ordered = sorted(candles, key=lambda row: str(row.get("date", "")))
    latest = ordered[-1]
    recent = ordered[-6:-1]
    close = _to_float(latest.get("close"), 0.0)
    high_break = max(_to_float(row.get("high"), 0.0) for row in recent)
    low_break = min(_to_float(row.get("low"), 0.0) for row in recent)
    sma5 = sum(_to_float(row.get("close"), 0.0) for row in ordered[-5:]) / 5.0
    prev_sma5 = sum(_to_float(row.get("close"), 0.0) for row in ordered[-6:-1]) / 5.0

    if close > high_break and close > sma5 and sma5 >= prev_sma5:
        return "CALL"
    if close < low_break and close < sma5 and sma5 <= prev_sma5:
        return "PUT"
    return None


def fetch_strategy_signal_side(kite: Any, *, interval: str = "5minute") -> str | None:
    now = datetime.now(IST)
    session_start = datetime.combine(now.date(), wall_time(9, 15), tzinfo=IST)
    if now <= session_start:
        return None

    candles = kite.historical_data(
        NIFTY_SPOT_INSTRUMENT_TOKEN,
        session_start,
        now,
        interval,
        continuous=False,
        oi=False,
    )
    return signal_side_from_candles(candles)


def wait_for_strategy_signal(
    kite: Any,
    *,
    poll_seconds: float,
    max_checks: int | None,
    stop_new_entries_at: wall_time | None = None,
) -> str:
    checks = 0
    while True:
        if stop_new_entries_at is not None and datetime.now(IST).time() >= stop_new_entries_at:
            raise ValueError(f"Stopped searching for new entries after {stop_new_entries_at.isoformat()} IST.")

        checks += 1
        side = fetch_strategy_signal_side(kite)
        if side is not None:
            print(f"Strategy signal selected {side}.")
            return side

        if max_checks is not None and checks >= max_checks:
            raise ValueError("No CALL/PUT strategy signal found within the requested checks.")

        print("No strategy signal yet; waiting for next check.")
        time.sleep(poll_seconds)


def fetch_apex_trade_setup(
    kite: Any,
    *,
    pipeline: ApexPipeline,
    lookback_bars: int = 60,
) -> tuple[str, str] | None:
    _, result, _ = run_live_nifty_apex_pipeline(
        kite,
        pipeline=pipeline,
        lookback_bars=lookback_bars,
    )
    if result.action not in {DecisionAction.TRADE_CALL, DecisionAction.TRADE_PUT}:
        return None

    options_report = result.reports.get("options", {})
    selection = options_report.get("selection", {}) if isinstance(options_report, dict) else {}
    contract_symbol = str(selection.get("contract_symbol", "")).strip()
    if not contract_symbol:
        return None

    side = "CALL" if result.action == DecisionAction.TRADE_CALL else "PUT"
    return side, f"NFO:{contract_symbol}"


def wait_for_apex_trade_setup(
    kite: Any,
    *,
    pipeline: ApexPipeline,
    poll_seconds: float,
    max_checks: int | None,
    stop_new_entries_at: wall_time | None = None,
    lookback_bars: int = 60,
) -> tuple[str, str]:
    checks = 0
    while True:
        if stop_new_entries_at is not None and datetime.now(IST).time() >= stop_new_entries_at:
            raise ValueError(f"Stopped searching for new entries after {stop_new_entries_at.isoformat()} IST.")

        checks += 1
        setup = fetch_apex_trade_setup(kite, pipeline=pipeline, lookback_bars=lookback_bars)
        if setup is not None:
            side, instrument = setup
            print(f"APEX selected {side} on {instrument}.")
            return setup

        if max_checks is not None and checks >= max_checks:
            raise ValueError("No APEX trade decision found within the requested checks.")

        print("APEX decision was WATCH/NO_TRADE; waiting for next check.")
        time.sleep(poll_seconds)


def parse_expiry_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def resolve_nifty_atm_option(
    kite: Any,
    *,
    side: str,
    strike_step: int = 50,
    today: date | None = None,
) -> str:
    """Select nearest-expiry ATM NIFTY option from the live Kite instrument list."""

    normalized_side = side.strip().upper()
    if normalized_side not in {"CALL", "PUT"}:
        raise ValueError("side must be CALL or PUT")

    option_type = "CE" if normalized_side == "CALL" else "PE"
    spot_price = extract_last_price(kite.ltp(NIFTY_SPOT_INSTRUMENT), NIFTY_SPOT_INSTRUMENT)
    atm_strike = int(round(spot_price / strike_step) * strike_step)
    session_date = today or datetime.now(IST).date()

    candidates: list[tuple[date, int, str]] = []
    for row in kite.instruments("NFO"):
        symbol = str(row.get("tradingsymbol", ""))
        if not symbol.startswith("NIFTY"):
            continue
        if str(row.get("instrument_type", "")) != option_type:
            continue
        expiry = parse_expiry_date(row.get("expiry"))
        if expiry is None or expiry < session_date:
            continue
        strike = int(float(row.get("strike", 0) or 0))
        if strike <= 0:
            continue
        candidates.append((expiry, abs(strike - atm_strike), symbol))

    if not candidates:
        raise ValueError(f"No live NIFTY {option_type} option found for ATM strike {atm_strike}.")

    expiry, _, symbol = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
    selected = f"NFO:{symbol}"
    print(f"Auto-selected {selected} expiry={expiry.isoformat()} spot={spot_price:.2f} atm={atm_strike}")
    return selected


def wait_until_market_open(
    *,
    enabled: bool,
    market_open: wall_time = wall_time(9, 15),
    delay_minutes: int = 0,
) -> None:
    if not enabled:
        return

    now = datetime.now(IST)
    target = datetime.combine(now.date(), market_open, tzinfo=IST) + timedelta(minutes=delay_minutes)
    if now >= target:
        print(f"Market-open wait skipped; current time is already after {target.time().isoformat()} IST.")
        return

    wait_seconds = max(0.0, (target - now).total_seconds())
    print(f"Waiting until {target.isoformat()} before opening paper trade.")
    time.sleep(wait_seconds)


def save_position_state(
    path: str | Path,
    *,
    position: AutoPaperPosition,
    instrument: str,
    quantity: int,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "instrument": instrument,
        "quantity": quantity,
        "position": asdict(position),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output


def load_position_state(path: str | Path) -> tuple[AutoPaperPosition, str, int] | None:
    state_path = Path(path)
    if not state_path.exists():
        return None

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    position_payload = payload.get("position")
    if not isinstance(position_payload, dict):
        raise ValueError(f"Invalid shadow paper state file: {state_path}")

    return (
        AutoPaperPosition(**position_payload),
        str(payload.get("instrument", "")),
        int(payload.get("quantity", DEFAULT_NIFTY_QUANTITY)),
    )


def clear_position_state(path: str | Path) -> None:
    state_path = Path(path)
    if state_path.exists():
        state_path.unlink()


def build_closed_trade_record(
    closed_trade: dict[str, object],
    *,
    instrument: str,
    quantity: int,
) -> dict[str, object]:
    pnl_points = float(closed_trade.get("pnl_points", 0.0) or 0.0)
    record = dict(closed_trade)
    record.update(
        {
            "instrument": instrument,
            "quantity": quantity,
            "gross_pnl": pnl_points * quantity,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return record


def parse_hhmm(value: str | None) -> wall_time | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return wall_time.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("time must be in HH:MM format") from exc


def run_shadow_paper(
    *,
    instrument: str | None,
    side: str,
    quantity: int,
    poll_seconds: float,
    max_ticks: int | None,
    reset: bool,
    auto_nifty_atm: bool,
    wait_for_market_open: bool,
    wait_after_open_minutes: int,
    signal_poll_seconds: float,
    signal_max_checks: int | None,
    max_trades: int,
    reentry_wait_seconds: float,
    stop_new_entries_at: wall_time | None,
    state_path: str | Path,
    entry_path: str | Path,
    trade_path: str | Path,
    config: AutoPaperConfig,
) -> int:
    """Run one shadow-paper session using live LTP only."""

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if poll_seconds < 0:
        raise ValueError("poll_seconds must be zero or positive")
    if max_trades <= 0:
        raise ValueError("max_trades must be positive")
    if reentry_wait_seconds < 0:
        raise ValueError("reentry_wait_seconds must be zero or positive")
    if side.strip().upper() == "AUTO" and instrument is not None:
        raise ValueError("Do not combine --side AUTO with --instrument; APEX selects the contract.")

    if reset:
        clear_position_state(state_path)

    wait_until_market_open(
        enabled=wait_for_market_open,
        delay_minutes=wait_after_open_minutes,
    )

    completed_trades = 0
    requested_side = side.strip().upper()
    kite = build_safe_kite_client_from_env()
    pipeline = ApexPipeline(audit_dir=Path(trade_path).parent / "apex_audit") if requested_side == "AUTO" else None

    while completed_trades < max_trades:
        state = load_position_state(state_path)
        ticks_seen = 0
        active_instrument = instrument

        if state is None:
            cycle_side = requested_side
            if cycle_side == "AUTO":
                if pipeline is None:
                    raise RuntimeError("APEX pipeline was not initialized for AUTO mode.")
                cycle_side, active_instrument = wait_for_apex_trade_setup(
                    kite,
                    pipeline=pipeline,
                    poll_seconds=signal_poll_seconds,
                    max_checks=signal_max_checks,
                    stop_new_entries_at=stop_new_entries_at,
                )
            if active_instrument is None:
                if not auto_nifty_atm:
                    raise ValueError("Provide --instrument or use --auto-nifty-atm.")
                active_instrument = resolve_nifty_atm_option(kite, side=cycle_side)
            entry_payload = kite.ltp(active_instrument)
            entry_price = extract_last_price(entry_payload, active_instrument)
            position = open_auto_paper_position(
                cycle_side,
                entry_price,
                config,
                contract_symbol=active_instrument,
            )
            append_auto_paper_entry(entry_path, position)
            save_position_state(
                state_path,
                position=position,
                instrument=active_instrument,
                quantity=quantity,
            )
            print(f"Opened PAPER {position.side} {active_instrument} qty={quantity} entry={entry_price:.2f}")
        else:
            position, saved_instrument, saved_quantity = state
            cycle_side = requested_side
            if cycle_side == "AUTO":
                cycle_side = position.side
            if active_instrument is None:
                active_instrument = saved_instrument
            if saved_instrument and saved_instrument != active_instrument:
                raise ValueError(
                    f"Saved paper state is for {saved_instrument}, but this run requested {active_instrument}. "
                    "Use --reset to discard the saved paper state, or use a different --state file."
                )
            if position.side != cycle_side:
                raise ValueError(
                    f"Saved paper state is {position.side}, but this run requested {cycle_side}. "
                    "Use --reset to discard the saved paper state, or use a different --state file."
                )
            quantity = saved_quantity
            print(f"Resumed PAPER {position.side} {active_instrument} qty={quantity} entry={position.entry_price:.2f}")

        while True:
            ticks_seen += 1
            ltp_payload = kite.ltp(active_instrument)
            current_price = extract_last_price(ltp_payload, active_instrument)
            update = update_auto_paper_position(position, current_price, config)
            print(
                f"{datetime.now().isoformat(timespec='seconds')} "
                f"{active_instrument} LTP={current_price:.2f} {update.message}"
            )

            if update.status == "closed" and update.closed_trade is not None:
                record = build_closed_trade_record(
                    update.closed_trade,
                    instrument=active_instrument,
                    quantity=quantity,
                )
                append_auto_paper_trade(trade_path, record)
                clear_position_state(state_path)
                completed_trades += 1
                print(
                    f"Closed PAPER trade {completed_trades}/{max_trades}. "
                    f"Gross P&L={float(record['gross_pnl']):.2f}"
                )
                if completed_trades >= max_trades:
                    return 0
                if stop_new_entries_at is not None and datetime.now(IST).time() >= stop_new_entries_at:
                    print(f"Stopped searching for new entries after {stop_new_entries_at.isoformat()} IST.")
                    return 0
                print(f"Waiting {reentry_wait_seconds:.0f} seconds before searching for next opportunity.")
                time.sleep(reentry_wait_seconds)
                break

            if update.position is not None:
                position = update.position
                save_position_state(
                    state_path,
                    position=position,
                    instrument=active_instrument,
                    quantity=quantity,
                )

            if max_ticks is not None and ticks_seen >= max_ticks:
                print(f"Paper position still open. State saved to {state_path}")
                return 0

            time.sleep(poll_seconds)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run live-data-only shadow paper trading. No broker order is ever placed."
    )
    parser.add_argument("--instrument", default=None, help='Example: "NFO:NIFTY26JUL24200CE"')
    parser.add_argument("--side", choices=["CALL", "PUT", "AUTO"], required=True)
    parser.add_argument("--quantity", type=int, default=DEFAULT_NIFTY_QUANTITY)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--max-ticks", type=int, default=None)
    parser.add_argument(
        "--auto-nifty-atm",
        action="store_true",
        help="Auto-select nearest-expiry ATM NIFTY option for the requested side.",
    )
    parser.add_argument(
        "--wait-for-market-open",
        action="store_true",
        help="Wait until 09:15 IST before opening a new paper trade.",
    )
    parser.add_argument(
        "--wait-after-open-minutes",
        type=int,
        default=0,
        help="Additional minutes to wait after 09:15 IST before opening a new paper trade.",
    )
    parser.add_argument(
        "--signal-poll-seconds",
        type=float,
        default=60.0,
        help="When --side AUTO is used, wait this many seconds between strategy signal checks.",
    )
    parser.add_argument(
        "--signal-max-checks",
        type=int,
        default=None,
        help="Optional maximum AUTO signal checks before exiting.",
    )
    parser.add_argument(
        "--max-trades",
        type=int,
        default=1,
        help="Maximum closed paper trades to complete before exiting.",
    )
    parser.add_argument(
        "--reentry-wait-seconds",
        type=float,
        default=300.0,
        help="Cooldown after a closed paper trade before scanning for a new opportunity.",
    )
    parser.add_argument(
        "--stop-new-entries-at",
        default="15:00",
        help="HH:MM IST cutoff for opening new paper trades. Use empty string to disable.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Discard any saved open paper position before starting this run.",
    )
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--entries", default=str(DEFAULT_ENTRY_PATH))
    parser.add_argument("--trades", default=str(DEFAULT_TRADE_PATH))
    parser.add_argument("--stop-loss-pct", type=float, default=0.10)
    parser.add_argument("--profit-trigger-points", type=float, default=10.0)
    parser.add_argument("--trailing-points", type=float, default=5.0)
    parser.add_argument("--target-points", type=float, default=25.0)
    args = parser.parse_args(argv)

    config = AutoPaperConfig(
        stop_loss_pct=args.stop_loss_pct,
        profit_trigger_points=args.profit_trigger_points,
        trailing_points=args.trailing_points,
        target_points=args.target_points,
    )

    return run_shadow_paper(
        instrument=args.instrument,
        side=args.side,
        quantity=args.quantity,
        poll_seconds=args.poll_seconds,
        max_ticks=args.max_ticks,
        reset=bool(args.reset),
        auto_nifty_atm=bool(args.auto_nifty_atm),
        wait_for_market_open=bool(args.wait_for_market_open),
        wait_after_open_minutes=args.wait_after_open_minutes,
        signal_poll_seconds=args.signal_poll_seconds,
        signal_max_checks=args.signal_max_checks,
        max_trades=args.max_trades,
        reentry_wait_seconds=args.reentry_wait_seconds,
        stop_new_entries_at=parse_hhmm(args.stop_new_entries_at),
        state_path=args.state,
        entry_path=args.entries,
        trade_path=args.trades,
        config=config,
    )


if __name__ == "__main__":
    raise SystemExit(main())
