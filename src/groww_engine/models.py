from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketTick:
    symbol: str
    instrument: str
    timestamp: datetime
    ltp: float


@dataclass(frozen=True)
class StrategySignal:
    signal_id: str
    symbol: str
    instrument: str
    side: str
    timestamp: datetime
    reason: str
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    instrument: str
    side: str
    quantity: int
    requested_price: float
    stop_loss: float | None
    target: float | None
    signal_id: str
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None


@dataclass
class TradeRecord:
    trade_id: str
    mode: str
    timestamp: datetime
    symbol: str
    instrument: str
    expiry: str | None
    strike: float | None
    option_type: str | None
    signal_id: str | None
    side: str
    quantity: int
    requested_price: float
    fill_price: float
    stop_loss: float | None
    target: float | None
    status: str
    exit_reason: str | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    broker_fee: float = 0.0
    taxes: float = 0.0
    slippage_cost: float = 0.0


@dataclass
class OpenPosition:
    position_id: str
    symbol: str
    instrument: str
    expiry: str | None
    strike: float | None
    option_type: str | None
    side: str
    quantity: int
    entry_time: datetime
    entry_price: float
    stop_loss: float | None
    target: float | None
    signal_id: str


@dataclass(frozen=True)
class LiveOrderContext:
    authenticated: bool
    available_margin: float
    symbol_valid: bool
    quantity_valid: bool
    has_open_position: bool
    daily_loss_ok: bool
    trades_remaining: bool
    within_trading_time: bool
    market_data_fresh: bool
