"""Automatic paper-trade state machine for short-horizon NIFTY options bias execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class AutoPaperConfig:
    stop_loss_pct: float = 0.10
    profit_trigger_points: float = 10.0
    trailing_points: float = 5.0
    target_points: float = 25.0
    profit_trigger_pct: float | None = None
    target_pct: float | None = None


def _resolve_profit_trigger_points(entry_price: float, config: AutoPaperConfig) -> float:
    if config.profit_trigger_pct is not None:
        return max(0.0, entry_price * float(config.profit_trigger_pct))
    return max(0.0, float(config.profit_trigger_points))


def _resolve_target_points(entry_price: float, config: AutoPaperConfig) -> float:
    if config.target_pct is not None:
        return max(0.0, entry_price * float(config.target_pct))
    return max(0.0, float(config.target_points))


@dataclass
class AutoPaperPosition:
    side: str
    entry_price: float
    entry_time: str
    stop_loss_price: float
    target_price: float | None = None
    contract_symbol: str | None = None
    trailing_active: bool = False
    trailing_stop: float | None = None
    high_watermark: float | None = None
    low_watermark: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AutoPaperUpdate:
    status: str
    message: str
    position: AutoPaperPosition | None
    closed_trade: dict[str, object] | None = None


def open_auto_paper_position(
    side: str,
    entry_price: float,
    config: AutoPaperConfig,
    *,
    contract_symbol: str | None = None,
) -> AutoPaperPosition:
    normalized_side = side.strip().upper()
    if normalized_side not in {"CALL", "PUT"}:
        raise ValueError("side must be CALL or PUT")
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")

    # Both CALL and PUT are bought options; risk is managed on option premium.
    stop_loss_price = entry_price * (1.0 - config.stop_loss_pct)
    target_price = entry_price + _resolve_target_points(entry_price, config)

    return AutoPaperPosition(
        side=normalized_side,
        contract_symbol=contract_symbol,
        entry_price=entry_price,
        entry_time=datetime.now(timezone.utc).isoformat(),
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        high_watermark=entry_price,
        low_watermark=entry_price,
    )


def update_auto_paper_position(
    position: AutoPaperPosition,
    current_price: float,
    config: AutoPaperConfig,
) -> AutoPaperUpdate:
    if current_price <= 0:
        raise ValueError("current_price must be positive")

    side_label = "CALL" if position.side == "CALL" else "PUT"
    position.high_watermark = max(float(position.high_watermark or position.entry_price), current_price)
    profit_points = current_price - position.entry_price

    if position.target_price is not None and current_price >= position.target_price:
        return AutoPaperUpdate(
            status="closed",
            message=f"{side_label} position exited by target.",
            position=None,
            closed_trade=_build_closed_trade(position, current_price, position.target_price, "TARGET"),
        )

    trigger_points = _resolve_profit_trigger_points(position.entry_price, config)
    if (not position.trailing_active) and profit_points >= trigger_points:
        position.trailing_active = True
        position.trailing_stop = position.high_watermark - config.trailing_points

    if position.trailing_active and position.trailing_stop is not None:
        candidate_stop = position.high_watermark - config.trailing_points
        position.trailing_stop = max(position.trailing_stop, candidate_stop)

    active_stop = position.trailing_stop if position.trailing_active else position.stop_loss_price
    if current_price <= active_stop:
        return AutoPaperUpdate(
            status="closed",
            message=f"{side_label} position exited by stop/trailing stop.",
            position=None,
            closed_trade=_build_closed_trade(
                position,
                current_price,
                active_stop,
                "TRAILING_STOP" if position.trailing_active else "STOP_LOSS",
            ),
        )

    return AutoPaperUpdate(
        status="open",
        message=f"{side_label} position remains open.",
        position=position,
    )


def append_auto_paper_trade(path: str | Path, trade: dict[str, object]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([trade])
    header = not output.exists()
    frame.to_csv(output, mode="a", header=header, index=False)
    return output


def append_auto_paper_entry(path: str | Path, position: AutoPaperPosition) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "mode": "PAPER_AUTO",
                "side": position.side,
                "contract_symbol": position.contract_symbol,
                "entry_time": position.entry_time,
                "entry_price": position.entry_price,
                "stop_loss_price": position.stop_loss_price,
                "target_price": position.target_price,
                "trailing_active": position.trailing_active,
            }
        ]
    )
    header = not output.exists()
    frame.to_csv(output, mode="a", header=header, index=False)
    return output


def _build_closed_trade(
    position: AutoPaperPosition,
    exit_price: float,
    trigger_price: float,
    exit_reason: str,
) -> dict[str, object]:
    pnl_points = exit_price - position.entry_price

    return {
        "mode": "PAPER_AUTO",
        "side": position.side,
        "contract_symbol": position.contract_symbol,
        "entry_time": position.entry_time,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "entry_price": position.entry_price,
        "exit_price": exit_price,
        "target_price": position.target_price,
        "stop_trigger_price": trigger_price,
        "exit_reason": exit_reason,
        "pnl_points": pnl_points,
        "trailing_active": position.trailing_active,
    }
