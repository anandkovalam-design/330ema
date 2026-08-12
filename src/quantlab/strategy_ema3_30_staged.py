from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


TIMESTAMP_SEMANTICS = "candle_close_time"
IST = ZoneInfo("Asia/Kolkata")

EXIT_MODE_FIXED_TARGET = "FIXED_TARGET"
EXIT_MODE_STAGED_TRAILING = "STAGED_TRAILING"

INTRABAR_POLICY_STOP_FIRST = "CONSERVATIVE_STOP_FIRST"
INTRABAR_POLICY_TARGET_FIRST = "AGGRESSIVE_TARGET_FIRST"


@dataclass(frozen=True)
class StrategyConfig:
    fast_ema: int = 3
    slow_ema: int = 30
    stop_loss_pct: float = 0.10
    target_pct: float | None = None
    no_fixed_target: bool = True
    exit_mode: str = EXIT_MODE_STAGED_TRAILING
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
    entry_end_time: time = time(15, 20)
    forced_square_off_time: time = time(15, 20)
    intrabar_execution_policy: str = INTRABAR_POLICY_STOP_FIRST

    def __post_init__(self) -> None:
        mode = self.exit_mode.strip().upper()
        if mode not in {EXIT_MODE_FIXED_TARGET, EXIT_MODE_STAGED_TRAILING}:
            raise ValueError("exit_mode must be FIXED_TARGET or STAGED_TRAILING")
        if self.intrabar_execution_policy not in {INTRABAR_POLICY_STOP_FIRST, INTRABAR_POLICY_TARGET_FIRST}:
            raise ValueError("intrabar_execution_policy must be CONSERVATIVE_STOP_FIRST or AGGRESSIVE_TARGET_FIRST")

        if mode == EXIT_MODE_FIXED_TARGET:
            if self.no_fixed_target:
                raise ValueError("FIXED_TARGET mode conflicts with no_fixed_target=True")
            if self.target_pct is None or self.target_pct <= 0:
                raise ValueError("FIXED_TARGET mode requires positive target_pct")

        if mode == EXIT_MODE_STAGED_TRAILING:
            if not self.no_fixed_target:
                raise ValueError("STAGED_TRAILING mode requires no_fixed_target=True")
            if self.target_pct not in {None, 0.0}:
                raise ValueError("STAGED_TRAILING mode conflicts with target_pct")


@dataclass(frozen=True)
class ExecutionCostConfig:
    entry_slippage_bps: float = 2.0
    exit_slippage_bps: float = 2.0
    brokerage_per_order: float = 20.0
    stt_rate: float = 0.0005
    exchange_txn_rate: float = 0.00053
    sebi_rate: float = 0.000001
    stamp_duty_rate: float = 0.00003
    gst_rate: float = 0.18


@dataclass
class TradeResult:
    side: str
    contract_symbol: str
    trade_date: str
    signal_candle_close_time: pd.Timestamp
    entry_candle_open_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_underlying: float
    entry_option: float
    exit_time: pd.Timestamp
    exit_option: float
    pnl_points: float
    gross_pnl_points: float
    gross_pnl_currency: float
    total_cost_currency: float
    net_pnl_currency: float
    exit_reason: str
    partial_book_price: float
    partial_book_qty: int
    final_exit_qty: int
    quantity: int
    candle_interval: str
    timezone: str = "Asia/Kolkata"
    timestamp_semantics: str = TIMESTAMP_SEMANTICS


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_ist_timestamp(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(pd.to_datetime(value, errors="coerce"))
    if pd.isna(ts):
        raise ValueError(f"Invalid timestamp: {value}")
    if ts.tzinfo is None:
        return ts.tz_localize(IST)
    return ts.tz_convert(IST)


def _apply_slippage(price: float, side: str, slippage_bps: float) -> float:
    slip = price * (max(0.0, slippage_bps) / 10000.0)
    if side.upper() == "BUY":
        return price + slip
    return max(0.0, price - slip)


def _calculate_trade_costs(
    *,
    entry_price: float,
    quantity: int,
    partial_exit_price: float,
    partial_exit_qty: int,
    final_exit_price: float,
    final_exit_qty: int,
    costs: ExecutionCostConfig,
) -> float:
    if quantity <= 0:
        return 0.0

    buy_turnover = entry_price * quantity
    sell_turnover = (partial_exit_price * partial_exit_qty) + (final_exit_price * final_exit_qty)
    total_turnover = buy_turnover + sell_turnover

    order_legs = 1 + (1 if partial_exit_qty > 0 else 0) + (1 if final_exit_qty > 0 else 0)
    brokerage = costs.brokerage_per_order * order_legs
    stt = sell_turnover * max(0.0, costs.stt_rate)
    exchange = total_turnover * max(0.0, costs.exchange_txn_rate)
    sebi = total_turnover * max(0.0, costs.sebi_rate)
    stamp = buy_turnover * max(0.0, costs.stamp_duty_rate)
    gst = (brokerage + exchange) * max(0.0, costs.gst_rate)
    return brokerage + stt + exchange + sebi + stamp + gst


def compute_signals(underlying_bars: pd.DataFrame, config: StrategyConfig | None = None) -> pd.DataFrame:
    """Add EMA 3/30 crossover signals to underlying OHLC bars.

    Timestamp convention: each row timestamp represents candle close time.
    Required columns: timestamp, open, high, low, close
    """
    cfg = config or StrategyConfig()
    bars = underlying_bars.copy()
    if "timestamp" not in bars.columns:
        raise ValueError("underlying_bars must contain a 'timestamp' column")

    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="coerce")
    bars = bars.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if not bars.empty:
        first_ts = pd.Timestamp(bars.iloc[0]["timestamp"])
        if first_ts.tzinfo is None:
            bars["timestamp"] = bars["timestamp"].dt.tz_localize(IST)
        else:
            bars["timestamp"] = bars["timestamp"].dt.tz_convert(IST)
    bars["ema_fast"] = bars["close"].ewm(span=cfg.fast_ema, adjust=False).mean()
    bars["ema_slow"] = bars["close"].ewm(span=cfg.slow_ema, adjust=False).mean()
    prev_fast = bars["ema_fast"].shift(1)
    prev_slow = bars["ema_slow"].shift(1)
    bars["cross_up"] = (bars["ema_fast"] > bars["ema_slow"]) & (prev_fast <= prev_slow)
    bars["cross_down"] = (bars["ema_fast"] < bars["ema_slow"]) & (prev_fast >= prev_slow)
    return bars


def _option_bar_at(
    option_lookup: dict[pd.Timestamp, dict[str, Any]],
    ts: pd.Timestamp,
    expected_contract_symbol: str,
) -> dict[str, Any] | None:
    if ts in option_lookup:
        row = option_lookup[ts]
        row_symbol = str(row.get("contract_symbol", row.get("symbol", ""))).strip()
        if row_symbol and row_symbol != expected_contract_symbol:
            return None
        return row
    earlier = [key for key in option_lookup if key <= ts]
    if not earlier:
        return None
    row = option_lookup[max(earlier)]
    row_symbol = str(row.get("contract_symbol", row.get("symbol", ""))).strip()
    if row_symbol and row_symbol != expected_contract_symbol:
        return None
    return row


def _normalize_side(side: str) -> str:
    normalized = side.strip().upper()
    if normalized in {"CALL", "CE"}:
        return "CALL"
    if normalized in {"PUT", "PE"}:
        return "PUT"
    raise ValueError("side must be CALL/PUT or CE/PE")


def select_groww_fno_contract(
    side: str,
    *,
    spot_price: float,
    trade_date: date,
    option_chain: list[dict[str, Any]],
    strike_step: int = 50,
) -> dict[str, Any] | None:
    """Select nearest-expiry ATM Groww FNO option contract for CALL or PUT.

    Supported option-chain keys include: symbol/tradingsymbol, strike, expiry,
    option_type/instrument_type.
    """

    if spot_price <= 0 or not option_chain:
        return None

    normalized_side = _normalize_side(side)
    option_type = "CE" if normalized_side == "CALL" else "PE"
    atm_strike = int(round(spot_price / strike_step) * strike_step)

    candidates: list[tuple[date, int, dict[str, Any]]] = []
    for row in option_chain:
        row_option_type = str(row.get("option_type", row.get("instrument_type", ""))).strip().upper()
        if row_option_type != option_type:
            continue
        expiry = pd.to_datetime(row.get("expiry"), errors="coerce")
        if pd.isna(expiry):
            continue
        expiry_date = expiry.date()
        if expiry_date < trade_date:
            continue
        strike = int(_to_float(row.get("strike", 0.0), 0.0))
        if strike <= 0:
            continue
        candidates.append((expiry_date, abs(strike - atm_strike), row))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _in_entry_window(ts: pd.Timestamp, cfg: StrategyConfig) -> bool:
    local_time = ts.time()
    return cfg.entry_start_time <= local_time <= cfg.entry_end_time


def simulate_day(
    underlying_signal_bars: pd.DataFrame,
    call_option_lookup_by_time: dict[pd.Timestamp, dict[str, Any]],
    put_option_lookup_by_time: dict[pd.Timestamp, dict[str, Any]],
    *,
    lot_size: int,
    lots: int,
    config: StrategyConfig | None = None,
    costs: ExecutionCostConfig | None = None,
    option_chain: list[dict[str, Any]] | None = None,
    trading_date: date | None = None,
) -> list[TradeResult]:
    """Simulate one session of EMA 3/30 entries with staged exit logic.

    Timestamp convention: all timestamps are candle close times.
    `*_option_lookup_by_time` values must contain open/high/low/close for each timestamp.
    """
    cfg = config or StrategyConfig()
    cost_cfg = costs or ExecutionCostConfig()
    bars = underlying_signal_bars.copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="coerce")
    bars = bars.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if not bars.empty:
        first_ts = pd.Timestamp(bars.iloc[0]["timestamp"])
        if first_ts.tzinfo is None:
            bars["timestamp"] = bars["timestamp"].dt.tz_localize(IST)
        else:
            bars["timestamp"] = bars["timestamp"].dt.tz_convert(IST)
    if bars.empty:
        return []

    for lookup in (call_option_lookup_by_time, put_option_lookup_by_time):
        normalized: dict[pd.Timestamp, dict[str, Any]] = {}
        for key, value in lookup.items():
            normalized[_ensure_ist_timestamp(key)] = value
        lookup.clear()
        lookup.update(normalized)

    session_date = trading_date or bars.iloc[0]["timestamp"].date()
    bars = bars[bars["timestamp"].dt.date == session_date].reset_index(drop=True)
    if bars.empty:
        return []

    quantity = int(lot_size) * int(lots)
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    trades: list[TradeResult] = []
    intervals = bars["timestamp"].diff().dropna()
    interval_minutes = int(intervals.dt.total_seconds().min() // 60) if not intervals.empty else 5
    candle_interval = f"{interval_minutes}m"
    i = 1

    while i < len(bars) - 1:
        row = bars.iloc[i]
        ts = pd.Timestamp(row["timestamp"])
        if ts.time() >= cfg.forced_square_off_time:
            break
        if not _in_entry_window(ts, cfg):
            i += 1
            continue

        side: str | None = None
        if bool(row.get("cross_up", False)):
            side = "CALL"
        elif bool(row.get("cross_down", False)):
            side = "PUT"
        if side is None:
            i += 1
            continue

        next_row = bars.iloc[i + 1]
        entry_ts = pd.Timestamp(next_row["timestamp"])
        if entry_ts.date() != session_date:
            i += 1
            continue
        if entry_ts.time() > cfg.forced_square_off_time or not _in_entry_window(entry_ts, cfg):
            i += 1
            continue

        contract_symbol = f"{side}_CONTRACT"
        if option_chain:
            contract = select_groww_fno_contract(
                side,
                spot_price=_to_float(next_row.get("open"), 0.0),
                trade_date=session_date,
                option_chain=option_chain,
            )
            if contract is not None:
                contract_symbol = str(contract.get("symbol", contract.get("tradingsymbol", contract_symbol)))

        side_lookup = call_option_lookup_by_time if side == "CALL" else put_option_lookup_by_time
        entry_bar = _option_bar_at(side_lookup, entry_ts, contract_symbol)
        if entry_bar is None:
            i += 1
            continue

        entry_underlying = _to_float(next_row.get("open"), 0.0)
        entry_option_raw = _to_float(entry_bar.get("open", entry_bar.get("close", 0.0)), 0.0)
        if entry_option_raw <= 0:
            i += 1
            continue

        entry_option = _apply_slippage(entry_option_raw, "BUY", cost_cfg.entry_slippage_bps)

        stop_price = entry_option * (1.0 - cfg.stop_loss_pct)
        target_price = 0.0 if cfg.no_fixed_target or cfg.target_pct is None else entry_option * (1.0 + cfg.target_pct)
        breakeven_trigger_price = entry_option * (1.0 + cfg.sl_to_cost_profit_pct)
        book_half_price = entry_option * (1.0 + cfg.book_half_profit_pct)
        trail_start_price = entry_option * (1.0 + cfg.trail_after_profit_pct)

        active_stop = stop_price
        breakeven_armed = False
        trailing_active = False
        next_trail_trigger_price = trail_start_price + cfg.trail_trigger_points

        partial_booked = False
        partial_book_price = 0.0
        partial_book_qty = 0
        partial_book_fill = 0.0
        remaining_qty = quantity

        max_high_since_entry = entry_option
        exit_ts = entry_ts
        exit_option_raw = entry_option_raw
        exit_reason = "EOD_CLOSE"

        future_times = sorted(
            [
                bar_ts
                for bar_ts in side_lookup.keys()
                if bar_ts >= entry_ts and bar_ts.date() == session_date and bar_ts.time() <= cfg.forced_square_off_time
            ]
        )
        if not future_times:
            i += 1
            continue

        closes_series = pd.Series(
            [_to_float(side_lookup[bar_ts].get("close"), 0.0) for bar_ts in future_times],
            index=future_times,
            dtype="float64",
        )
        ema9_series = closes_series.ewm(span=9, adjust=False).mean()

        for future_ts in future_times:
            bar = side_lookup[future_ts]
            low_px = _to_float(bar.get("low"), 0.0)
            high_px = _to_float(bar.get("high"), 0.0)
            close_px = _to_float(bar.get("close"), 0.0)
            ema9_px = _to_float(ema9_series.loc[future_ts], close_px)

            max_high_since_entry = max(max_high_since_entry, high_px)

            touched_stop = low_px <= active_stop
            touched_target = (not cfg.no_fixed_target) and target_price > 0 and high_px >= target_price

            # Intrabar limitation: OHLC does not reveal event ordering when both
            # stop and target are hit in the same candle. Policy resolves that tie.
            if touched_stop and touched_target:
                if cfg.intrabar_execution_policy == INTRABAR_POLICY_STOP_FIRST:
                    touched_target = False
                else:
                    touched_stop = False

            if touched_stop:
                exit_ts = future_ts
                exit_option_raw = active_stop
                if trailing_active:
                    exit_reason = "TRAIL_STOP_MFE"
                elif breakeven_armed:
                    exit_reason = "BREAKEVEN"
                else:
                    exit_reason = "STOP_LOSS"
                break

            if touched_target:
                exit_ts = future_ts
                exit_option_raw = target_price
                exit_reason = "TARGET"
                break

            # 30% profit rule: stop-loss moves to entry only.
            if (not breakeven_armed) and high_px >= breakeven_trigger_price:
                breakeven_armed = True
                active_stop = max(active_stop, entry_option)

            if (not partial_booked) and high_px >= book_half_price and remaining_qty > 1:
                partial_booked = True
                partial_book_price = book_half_price
                partial_book_qty = remaining_qty // 2
                remaining_qty -= partial_book_qty
                partial_book_fill = _apply_slippage(partial_book_price, "SELL", cost_cfg.exit_slippage_bps)

            # Dynamic trailing starts only after 50% profit.
            if (not trailing_active) and high_px >= trail_start_price:
                trailing_active = True

            if trailing_active:
                dynamic_trail_stop = entry_option + (max_high_since_entry - entry_option) * (1.0 - cfg.mfe_giveback_pct)
                active_stop = max(active_stop, dynamic_trail_stop)

                if close_px < ema9_px:
                    exit_ts = future_ts
                    exit_option_raw = close_px
                    exit_reason = "EMA9_CLOSE_EXIT"
                    break

                while high_px >= next_trail_trigger_price:
                    active_stop += cfg.trail_step_points
                    next_trail_trigger_price += cfg.trail_trigger_points

            exit_ts = future_ts
            exit_option_raw = close_px

        if exit_ts.time() >= cfg.forced_square_off_time:
            exit_reason = "FORCED_SQUARE_OFF"

        exit_option = _apply_slippage(exit_option_raw, "SELL", cost_cfg.exit_slippage_bps)
        gross_pnl_currency = (
            (partial_book_fill - entry_option) * partial_book_qty
            + (exit_option - entry_option) * remaining_qty
        )
        total_cost_currency = _calculate_trade_costs(
            entry_price=entry_option,
            quantity=quantity,
            partial_exit_price=partial_book_fill,
            partial_exit_qty=partial_book_qty,
            final_exit_price=exit_option,
            final_exit_qty=remaining_qty,
            costs=cost_cfg,
        )
        net_pnl_currency = gross_pnl_currency - total_cost_currency
        gross_pnl_points = gross_pnl_currency / quantity
        pnl_points = net_pnl_currency / quantity

        trades.append(
            TradeResult(
                side=side,
                contract_symbol=contract_symbol,
                trade_date=session_date.isoformat(),
                signal_candle_close_time=ts,
                entry_candle_open_time=entry_ts - pd.Timedelta(minutes=interval_minutes),
                entry_time=entry_ts,
                entry_underlying=entry_underlying,
                entry_option=entry_option,
                exit_time=exit_ts,
                exit_option=exit_option,
                pnl_points=pnl_points,
                gross_pnl_points=gross_pnl_points,
                gross_pnl_currency=gross_pnl_currency,
                total_cost_currency=total_cost_currency,
                net_pnl_currency=net_pnl_currency,
                exit_reason=exit_reason,
                partial_book_price=partial_book_price,
                partial_book_qty=partial_book_qty,
                final_exit_qty=remaining_qty,
                quantity=quantity,
                candle_interval=candle_interval,
            )
        )

        # Prevent overlapping positions by skipping until the bar after exit.
        next_index = int(bars["timestamp"].searchsorted(exit_ts, side="right"))
        i = max(next_index, i + 1)
        if i >= len(bars) - 1:
            break

    return trades


def to_frame(trades: list[TradeResult]) -> pd.DataFrame:
    return pd.DataFrame([trade.__dict__ for trade in trades])
