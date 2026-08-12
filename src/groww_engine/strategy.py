from __future__ import annotations

from datetime import datetime

import pandas as pd

from quantlab.strategy_ema3_30_staged import StrategyConfig, compute_signals

from .models import StrategySignal


def build_signal_from_ohlc(
    bars: pd.DataFrame,
    *,
    symbol: str,
    instrument: str,
    reason_prefix: str = "ema3_30",
) -> StrategySignal | None:
    if bars.empty or len(bars) < 35:
        return None

    signal_bars = compute_signals(bars, StrategyConfig())
    latest = signal_bars.iloc[-1]
    latest_ts = pd.Timestamp(latest["timestamp"]).to_pydatetime()

    side: str | None = None
    if bool(latest.get("cross_up", False)):
        side = "BUY"
    elif bool(latest.get("cross_down", False)):
        side = "BUY"
    if side is None:
        return None

    option_type = "CE" if bool(latest.get("cross_up", False)) else "PE"
    close_price = float(latest.get("close", 0.0) or 0.0)
    strike = round(close_price / 50.0) * 50.0 if close_price > 0 else None
    signal_id = f"{symbol}:{option_type}:{latest_ts.isoformat()}"

    return StrategySignal(
        signal_id=signal_id,
        symbol=symbol,
        instrument=instrument,
        side=side,
        timestamp=latest_ts,
        reason=f"{reason_prefix}_{option_type.lower()}",
        strike=strike,
        option_type=option_type,
    )


def build_exit_reason(now: datetime) -> str:
    return f"strategy_exit_{now.strftime('%H%M%S')}"
