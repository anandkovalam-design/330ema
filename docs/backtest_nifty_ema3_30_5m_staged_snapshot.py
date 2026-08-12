from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd


WORK_DIR = Path(__file__).resolve().parent
SRC_DIR = WORK_DIR.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aadithya_quantlab.trading.zerodha_live import build_safe_kite_client_from_env


NIFTY_SPOT_TOKEN = 256265
IST = "Asia/Kolkata"


@dataclass(frozen=True)
class StrategyConfig:
    fast_ema: int = 3
    slow_ema: int = 30
    signal_interval: str = "minute"
    lots: int = 2
    stop_loss_pct: float = 0.10
    target_pct: float | None = 0.20
    no_fixed_target: bool = False
    trail_activate_pct: float = 0.20
    trail_lock_pct: float = 0.10
    trail_trigger_points: float = 15.0
    trail_step_points: float = 5.0
    sl_to_cost_profit_pct: float = 0.30
    book_half_profit_pct: float = 0.40
    trail_after_profit_pct: float = 0.50
    mfe_giveback_pct: float = 0.275
    use_legacy_dynamic_trail: bool = False
    entry_start_time: time = time(9, 16)
    entry_end_time: time = time(15, 24)
    max_trades_per_day: int = 999
    cooldown_minutes: int = 0


@dataclass
class TradeResult:
    session_date: str
    side: str
    contract_symbol: str
    strike: int
    expiry: str
    crossover_time: str
    entry_time: str
    entry_underlying: float
    entry_option: float
    stop_price: float
    target_price: float
    breakeven_trigger_price: float
    partial_book_price: float
    partial_book_qty: int
    final_exit_qty: int
    exit_time: str
    exit_option: float
    pnl_points: float
    result: str
    exit_reason: str
    quantity: int


TRADE_COLUMNS = list(TradeResult.__annotations__.keys())


def _parse_hhmm(value: str) -> time:
    parsed = datetime.strptime(value.strip(), "%H:%M")
    return time(parsed.hour, parsed.minute)


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fetch_candles(kite, instrument_token: int, start_dt: datetime, end_dt: datetime, interval: str) -> pd.DataFrame:
    rows = kite.historical_data(instrument_token, start_dt, end_dt, interval, oi=False)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.rename(columns={"date": "timestamp"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    frame["session_date"] = frame["timestamp"].dt.tz_convert(IST).dt.date.astype(str)
    return frame


def _compute_signals(frame: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    bars = frame.copy()
    bars["ema_fast"] = bars["close"].ewm(span=config.fast_ema, adjust=False).mean()
    bars["ema_slow"] = bars["close"].ewm(span=config.slow_ema, adjust=False).mean()
    prev_fast = bars["ema_fast"].shift(1)
    prev_slow = bars["ema_slow"].shift(1)
    bars["cross_up"] = (bars["ema_fast"] > bars["ema_slow"]) & (prev_fast <= prev_slow)
    bars["cross_down"] = (bars["ema_fast"] < bars["ema_slow"]) & (prev_fast >= prev_slow)
    return bars


def _next_tuesday(day: pd.Timestamp) -> pd.Timestamp:
    days_ahead = (1 - day.weekday()) % 7
    return day + timedelta(days=days_ahead)


def _select_option_contract(option_rows: list[dict[str, object]], entry_ts: pd.Timestamp, side: str, spot_price: float) -> dict[str, object] | None:
    atm_strike = int(round(spot_price / 50.0) * 50)
    option_type = "CE" if side == "CALL" else "PE"
    target_expiry = _next_tuesday(pd.Timestamp(entry_ts.date())).date()

    pool: list[dict[str, object]] = []
    for row in option_rows:
        symbol = str(row.get("tradingsymbol", ""))
        if not symbol.startswith("NIFTY"):
            continue
        if str(row.get("instrument_type", "")) != option_type:
            continue
        expiry = pd.to_datetime(row.get("expiry"), errors="coerce")
        if pd.isna(expiry) or expiry.date() < entry_ts.date():
            continue
        strike = int(_to_float(row.get("strike"), 0.0))
        if strike <= 0:
            continue
        pool.append(
            {
                "symbol": symbol,
                "strike": strike,
                "expiry": str(expiry.date()),
                "instrument_token": int(row.get("instrument_token", 0)),
                "lot_size": int(_to_float(row.get("lot_size"), 0.0)),
            }
        )

    if not pool:
        return None

    exact_expiry = [row for row in pool if pd.to_datetime(row["expiry"]).date() == target_expiry]
    candidate_pool = exact_expiry if exact_expiry else pool
    candidate_pool.sort(
        key=lambda row: (
            (pd.to_datetime(row["expiry"]).date() - entry_ts.date()).days,
            abs(int(row["strike"]) - atm_strike),
        )
    )
    return candidate_pool[0]


def _build_option_lookup(kite, instrument_token: int, day_start: datetime, day_end: datetime) -> dict[pd.Timestamp, dict[str, float]]:
    rows = kite.historical_data(instrument_token, day_start, day_end, "minute", oi=False)
    lookup: dict[pd.Timestamp, dict[str, float]] = {}
    for row in rows:
        ts = pd.Timestamp(pd.to_datetime(row.get("date")))
        lookup[ts] = {
            "open": _to_float(row.get("open"), 0.0),
            "high": _to_float(row.get("high"), 0.0),
            "low": _to_float(row.get("low"), 0.0),
            "close": _to_float(row.get("close"), 0.0),
        }
    return lookup


def _option_bar_at(lookup: dict[pd.Timestamp, dict[str, float]], ts: pd.Timestamp) -> dict[str, float] | None:
    if ts in lookup:
        return lookup[ts]
    earlier = [key for key in lookup if key <= ts]
    if not earlier:
        return None
    return lookup[max(earlier)]


def _simulate_day(
    day_frame: pd.DataFrame,
    option_rows: list[dict[str, object]],
    kite,
    config: StrategyConfig,
    option_cache: dict[tuple[int, str], dict[pd.Timestamp, dict[str, float]]] | None = None,
) -> list[TradeResult]:
    trades: list[TradeResult] = []
    position_open = False
    cache = option_cache if option_cache is not None else {}
    session_date = str(day_frame.iloc[0]["session_date"])
    cooldown_until: pd.Timestamp | None = None
    trades_taken = 0

    for i in range(1, len(day_frame) - 1):
        row = day_frame.iloc[i]
        ts = pd.Timestamp(row["timestamp"])
        if ts.day_name() == "Tuesday":
            continue
        if position_open:
            continue
        if cooldown_until is not None and ts < cooldown_until:
            continue
        if trades_taken >= config.max_trades_per_day:
            break
        local_time = ts.tz_convert(IST).time() if ts.tzinfo is not None else ts.time()
        if local_time < config.entry_start_time or local_time > config.entry_end_time:
            continue

        side: str | None = None
        if bool(row.get("cross_up", False)):
            side = "CALL"
        elif bool(row.get("cross_down", False)):
            side = "PUT"
        if side is None:
            continue

        next_row = day_frame.iloc[i + 1]
        entry_ts = pd.Timestamp(next_row["timestamp"])
        if entry_ts.day_name() == "Tuesday":
            continue
        entry_local_time = entry_ts.tz_convert(IST).time() if entry_ts.tzinfo is not None else entry_ts.time()
        if entry_local_time < config.entry_start_time or entry_local_time > config.entry_end_time:
            continue

        entry_underlying = _to_float(next_row.get("open"), 0.0)
        contract = _select_option_contract(option_rows, entry_ts, side, entry_underlying)
        if contract is None:
            continue

        day_start = entry_ts.to_pydatetime().replace(hour=9, minute=15, second=0, microsecond=0)
        day_end = entry_ts.to_pydatetime().replace(hour=15, minute=30, second=0, microsecond=0)
        token = int(contract["instrument_token"])
        cache_key = (token, session_date)
        if cache_key not in cache:
            cache[cache_key] = _build_option_lookup(kite, token, day_start, day_end)
        option_lookup = cache[cache_key]
        entry_bar = _option_bar_at(option_lookup, entry_ts)
        if entry_bar is None:
            continue

        entry_option = _to_float(entry_bar.get("close"), 0.0)
        if entry_option <= 0:
            continue

        stop_price = entry_option * (1.0 - config.stop_loss_pct)
        target_price = 0.0 if config.no_fixed_target or config.target_pct is None else entry_option * (1.0 + config.target_pct)
        trail_activate_price = entry_option * (1.0 + config.trail_activate_pct)
        trail_lock_price = entry_option * (1.0 + config.trail_lock_pct)
        breakeven_trigger_price = trail_activate_price
        sl_to_cost_price = entry_option * (1.0 + config.sl_to_cost_profit_pct)
        book_half_price = entry_option * (1.0 + config.book_half_profit_pct)
        trail_after_price = entry_option * (1.0 + config.trail_after_profit_pct)
        lot_size = int(contract["lot_size"])
        quantity = lot_size * int(config.lots)
        active_stop = stop_price
        breakeven_armed = False
        trailing_active = False
        next_trail_trigger_price = trail_activate_price + config.trail_trigger_points
        partial_booked = False
        partial_book_price = 0.0
        partial_book_qty = 0
        remaining_qty = quantity
        max_high_since_entry = entry_option
        total_pnl_qty_points = 0.0
        exit_ts = entry_ts
        exit_option = entry_option
        exit_reason = "EOD_CLOSE"
        position_open = True

        future_times = [bar_ts for bar_ts in option_lookup.keys() if bar_ts >= entry_ts]
        closes_series = pd.Series(
            [_to_float(option_lookup[bar_ts].get("close"), 0.0) for bar_ts in sorted(future_times)],
            index=sorted(future_times),
            dtype="float64",
        )
        ema9_series = closes_series.ewm(span=9, adjust=False).mean()
        for future_ts in sorted(future_times):
            bar = option_lookup[future_ts]
            low_px = _to_float(bar.get("low"), 0.0)
            high_px = _to_float(bar.get("high"), 0.0)
            close_px = _to_float(bar.get("close"), 0.0)
            ema9_px = _to_float(ema9_series.loc[future_ts], close_px)

            max_high_since_entry = max(max_high_since_entry, high_px)

            if low_px <= active_stop:
                exit_ts = future_ts
                exit_option = active_stop
                exit_reason = "TRAIL_STOP_MFE" if trailing_active else ("STOP_LOSS" if not breakeven_armed else "BREAKEVEN")
                break

            if (not config.no_fixed_target) and target_price > 0 and high_px >= target_price:
                exit_ts = future_ts
                exit_option = target_price
                exit_reason = "TARGET"
                break

            if config.use_legacy_dynamic_trail:
                if (not trailing_active) and high_px >= trail_activate_price:
                    trailing_active = True
                    breakeven_armed = True
                    active_stop = max(active_stop, trail_lock_price)

                if trailing_active:
                    while high_px >= next_trail_trigger_price:
                        active_stop += config.trail_step_points
                        next_trail_trigger_price += config.trail_trigger_points
            else:
                if (not breakeven_armed) and high_px >= sl_to_cost_price:
                    trailing_active = True
                    breakeven_armed = True
                    active_stop = max(active_stop, entry_option)

                if (not partial_booked) and high_px >= book_half_price and remaining_qty > 1:
                    partial_booked = True
                    partial_book_price = book_half_price
                    partial_book_qty = remaining_qty // 2
                    remaining_qty -= partial_book_qty
                    total_pnl_qty_points += (partial_book_price - entry_option) * partial_book_qty

                if (not trailing_active) and high_px >= trail_after_price:
                    trailing_active = True
                    active_stop = max(active_stop, trail_lock_price)

                if trailing_active:
                    dynamic_trail_stop = entry_option + (max_high_since_entry - entry_option) * (1.0 - config.mfe_giveback_pct)
                    active_stop = max(active_stop, dynamic_trail_stop)
                    if close_px < ema9_px:
                        exit_ts = future_ts
                        exit_option = close_px
                        exit_reason = "EMA9_CLOSE_EXIT"
                        break

                    while high_px >= next_trail_trigger_price:
                        active_stop += config.trail_step_points
                        next_trail_trigger_price += config.trail_trigger_points

            exit_ts = future_ts
            exit_option = close_px

        total_pnl_qty_points += (exit_option - entry_option) * remaining_qty
        pnl_points = total_pnl_qty_points / quantity if quantity > 0 else 0.0
        result = "WIN" if pnl_points > 0 else ("LOSS" if pnl_points < 0 else "FLAT")
        trades.append(
            TradeResult(
                session_date=session_date,
                side=side,
                contract_symbol=str(contract["symbol"]),
                strike=int(contract["strike"]),
                expiry=str(contract["expiry"]),
                crossover_time=str(ts),
                entry_time=str(entry_ts),
                entry_underlying=entry_underlying,
                entry_option=entry_option,
                stop_price=stop_price,
                target_price=target_price,
                breakeven_trigger_price=breakeven_trigger_price,
                partial_book_price=partial_book_price,
                partial_book_qty=partial_book_qty,
                final_exit_qty=remaining_qty,
                exit_time=str(exit_ts),
                exit_option=exit_option,
                pnl_points=pnl_points,
                result=result,
                exit_reason=exit_reason,
                quantity=quantity,
            )
        )
        trades_taken += 1
        if config.cooldown_minutes > 0:
            cooldown_until = exit_ts + pd.Timedelta(minutes=config.cooldown_minutes)
        position_open = False

    return trades


def run_backtest(*, days: int, config: StrategyConfig, output_tag: str, verbose: bool = True, session_date: str | None = None) -> tuple[pd.DataFrame, Path]:
    now = pd.Timestamp.now(tz=IST)
    start_dt = (now - timedelta(days=max(int(days), 1))).to_pydatetime()
    end_dt = now.to_pydatetime()

    kite = build_safe_kite_client_from_env()
    if verbose:
        print(
            f"FETCH underlying {config.signal_interval} candles start={start_dt.isoformat()} end={end_dt.isoformat()}",
            flush=True,
        )
    nifty = _fetch_candles(kite, NIFTY_SPOT_TOKEN, start_dt, end_dt, config.signal_interval)
    if nifty.empty:
        raise ValueError("No NIFTY minute candles returned.")

    if verbose:
        print(f"FETCH complete rows={len(nifty)}", flush=True)
    nifty = _compute_signals(nifty, config)
    option_rows = kite.instruments("NFO")
    if verbose:
        print(f"INSTRUMENTS loaded rows={len(option_rows)}", flush=True)
    trades: list[TradeResult] = []
    option_cache: dict[tuple[int, str], dict[pd.Timestamp, dict[str, float]]] = {}
    target_sessions = [session for session in sorted(nifty["session_date"].unique().tolist()) if pd.Timestamp(session).day_name() != "Tuesday"]
    if session_date:
        target_sessions = [session for session in target_sessions if session == session_date]
    if verbose:
        print(f"SESSIONS selected count={len(target_sessions)}", flush=True)
    for session in target_sessions:
        day_frame = nifty[nifty["session_date"] == session].sort_values("timestamp").reset_index(drop=True)
        if day_frame.empty:
            continue
        if verbose:
            print(f"SIMULATE session={session} rows={len(day_frame)}", flush=True)
        trades.extend(_simulate_day(day_frame, option_rows, kite, config, option_cache=option_cache))

    suffix = f"_{str(output_tag).strip()}" if str(output_tag).strip() else ""
    interval_tag = "1m" if config.signal_interval == "minute" else "5m"
    out_path = Path(f"validation/reports/backtest_nifty_{interval_tag}_ema3_30_last_{int(days)}_days{suffix}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([asdict(trade) for trade in trades], columns=TRADE_COLUMNS)
    frame.to_csv(out_path, index=False)
    return frame, out_path


def summarize_frame(frame: pd.DataFrame) -> dict[str, float | int]:
    trades_count = len(frame)
    wins = int((frame["result"] == "WIN").sum()) if trades_count else 0
    losses = int((frame["result"] == "LOSS").sum()) if trades_count else 0
    flats = int((frame["result"] == "FLAT").sum()) if trades_count else 0
    net = float(frame["pnl_points"].sum()) if trades_count else 0.0
    return {
        "trades": trades_count,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate_pct": (wins / trades_count * 100.0) if trades_count else 0.0,
        "net_option_points": net,
        "target_hits": int((frame["exit_reason"] == "TARGET").sum()) if trades_count else 0,
        "stop_hits": int((frame["exit_reason"] == "STOP_LOSS").sum()) if trades_count else 0,
        "breakevens": int((frame["exit_reason"] == "BREAKEVEN").sum()) if trades_count else 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest Nifty EMA 3/30 crossover strategy on Zerodha data.")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in calendar days.")
    parser.add_argument("--output-tag", default="", help="Optional output filename tag.")
    parser.add_argument("--signal-interval", choices=["minute", "5minute"], default="minute", help="Underlying candle interval used for EMA crossover signals.")
    parser.add_argument("--lots", type=int, default=2, help="Number of option lots per trade. Must be even (2, 4, 6, ...).")
    parser.add_argument("--stop-loss-pct", type=float, default=10.0, help="Stop loss percentage on option premium.")
    parser.add_argument("--target-pct", type=float, default=20.0, help="Target percentage on option premium.")
    parser.add_argument("--no-fixed-target", action="store_true", help="Disable fixed target and use trailing-stop only exits.")
    parser.add_argument("--trail-activate-pct", type=float, default=20.0, help="Activate trailing once this profit percent is reached.")
    parser.add_argument("--trail-lock-pct", type=float, default=10.0, help="When trailing activates, lock this percent profit in stop.")
    parser.add_argument("--trail-trigger-points", type=float, default=15.0, help="After trailing activation, raise stop every X points up.")
    parser.add_argument("--trail-step-points", type=float, default=5.0, help="Stop increment points per trailing trigger.")
    parser.add_argument("--sl-to-cost-profit-pct", type=float, default=30.0, help="At this profit percent, move stop loss to cost.")
    parser.add_argument("--book-half-profit-pct", type=float, default=40.0, help="At this profit percent, book 50 percent quantity.")
    parser.add_argument("--trail-after-profit-pct", type=float, default=50.0, help="At this profit percent, activate MFE giveback trail.")
    parser.add_argument("--mfe-giveback-pct", type=float, default=27.5, help="Allowed giveback percent from MFE for trailing remaining quantity.")
    parser.add_argument("--legacy-dynamic-trail", action="store_true", help="Use old dynamic trail logic: activate at --trail-activate-pct, lock --trail-lock-pct, and step trail by --trail-trigger-points/--trail-step-points.")
    parser.add_argument("--entry-start", default="09:16", help="Earliest crossover time to allow, HH:MM IST.")
    parser.add_argument("--entry-end", default="15:24", help="Latest crossover time to allow, HH:MM IST.")
    parser.add_argument("--max-trades-per-day", type=int, default=999, help="Maximum trades to take per day.")
    parser.add_argument("--cooldown-minutes", type=int, default=0, help="Cooldown after an exit before taking the next crossover.")
    parser.add_argument("--session-date", default="", help="Optional session date filter YYYY-MM-DD.")
    args = parser.parse_args(argv)

    lots = int(args.lots)
    if lots <= 0 or (lots % 2) != 0:
        raise ValueError("--lots must be a positive even number (2, 4, 6, ...)")

    resolved_target_pct = None if bool(args.no_fixed_target) else float(args.target_pct) / 100.0
    config = StrategyConfig(
        signal_interval=str(args.signal_interval),
        lots=lots,
        stop_loss_pct=float(args.stop_loss_pct) / 100.0,
        target_pct=resolved_target_pct,
        no_fixed_target=bool(args.no_fixed_target),
        trail_activate_pct=float(args.trail_activate_pct) / 100.0,
        trail_lock_pct=float(args.trail_lock_pct) / 100.0,
        trail_trigger_points=float(args.trail_trigger_points),
        trail_step_points=float(args.trail_step_points),
        sl_to_cost_profit_pct=float(args.sl_to_cost_profit_pct) / 100.0,
        book_half_profit_pct=float(args.book_half_profit_pct) / 100.0,
        trail_after_profit_pct=float(args.trail_after_profit_pct) / 100.0,
        mfe_giveback_pct=float(args.mfe_giveback_pct) / 100.0,
        use_legacy_dynamic_trail=bool(args.legacy_dynamic_trail),
        entry_start_time=_parse_hhmm(str(args.entry_start)),
        entry_end_time=_parse_hhmm(str(args.entry_end)),
        max_trades_per_day=int(args.max_trades_per_day),
        cooldown_minutes=int(args.cooldown_minutes),
    )
    selected_session = str(args.session_date).strip() or None
    frame, out_path = run_backtest(days=int(args.days), config=config, output_tag=str(args.output_tag), verbose=True, session_date=selected_session)

    stats = summarize_frame(frame)
    print(
        f"DAYS={int(args.days)} TRADES={int(stats['trades'])} WINS={int(stats['wins'])} "
        f"LOSSES={int(stats['losses'])} FLATS={int(stats['flats'])} NET_OPTION_POINTS={float(stats['net_option_points']):.2f}"
    )
    print(f"RESULT_CSV={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())