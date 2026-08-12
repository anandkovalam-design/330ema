from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .config import CostConfig
from .models import OpenPosition, OrderRequest, TradeRecord
from .storage import TradeStorage


@dataclass(frozen=True)
class PaperBrokerResult:
    accepted: bool
    reason: str
    trade_record: TradeRecord | None = None


class PaperBroker:
    def __init__(self, storage: TradeStorage, costs: CostConfig) -> None:
        self.storage = storage
        self.costs = costs
        self._open_positions: dict[str, OpenPosition] = {
            pos.position_id: pos for pos in storage.load_open_positions()
        }
        self._processed_signals: set[str] = set()
        for pos in self._open_positions.values():
            self._processed_signals.add(pos.signal_id)

    @property
    def open_positions(self) -> list[OpenPosition]:
        return list(self._open_positions.values())

    def has_signal(self, signal_id: str) -> bool:
        return signal_id in self._processed_signals

    def submit_entry(self, order: OrderRequest, market_price: float) -> PaperBrokerResult:
        if order.signal_id in self._processed_signals:
            return PaperBrokerResult(False, "Duplicate signal blocked")

        if market_price <= 0:
            return PaperBrokerResult(False, "Invalid market price for paper fill")

        fill_price = self._apply_slippage(market_price, order.side)
        fees = self._fees(fill_price, order.quantity)
        position_id = str(uuid4())
        now = datetime.now(timezone.utc)

        position = OpenPosition(
            position_id=position_id,
            symbol=order.symbol,
            instrument=order.instrument,
            expiry=order.expiry,
            strike=order.strike,
            option_type=order.option_type,
            side=order.side,
            quantity=order.quantity,
            entry_time=now,
            entry_price=fill_price,
            stop_loss=order.stop_loss,
            target=order.target,
            signal_id=order.signal_id,
        )
        self._open_positions[position_id] = position
        self._processed_signals.add(order.signal_id)
        self.storage.upsert_open_position(position)

        trade = TradeRecord(
            trade_id=str(uuid4()),
            mode="PAPER",
            timestamp=now,
            symbol=order.symbol,
            instrument=order.instrument,
            expiry=order.expiry,
            strike=order.strike,
            option_type=order.option_type,
            signal_id=order.signal_id,
            side=order.side,
            quantity=order.quantity,
            requested_price=order.requested_price,
            fill_price=fill_price,
            stop_loss=order.stop_loss,
            target=order.target,
            status="OPEN",
            broker_fee=fees["broker_fee"],
            taxes=fees["taxes"],
            slippage_cost=fees["slippage_cost"],
        )
        self.storage.append_trade(trade)
        return PaperBrokerResult(True, "Paper entry recorded", trade)

    def close_position(self, position_id: str, market_price: float, exit_reason: str) -> PaperBrokerResult:
        position = self._open_positions.get(position_id)
        if position is None:
            return PaperBrokerResult(False, "Position not found")
        if market_price <= 0:
            return PaperBrokerResult(False, "Invalid market price for paper exit")

        fill_price = self._apply_slippage(market_price, "SELL")
        fees = self._fees(fill_price, position.quantity)
        gross = (fill_price - position.entry_price) * position.quantity
        realized = gross - fees["broker_fee"] - fees["taxes"]

        now = datetime.now(timezone.utc)
        trade = TradeRecord(
            trade_id=str(uuid4()),
            mode="PAPER",
            timestamp=now,
            symbol=position.symbol,
            instrument=position.instrument,
            expiry=position.expiry,
            strike=position.strike,
            option_type=position.option_type,
            signal_id=position.signal_id,
            side="SELL",
            quantity=position.quantity,
            requested_price=market_price,
            fill_price=fill_price,
            stop_loss=position.stop_loss,
            target=position.target,
            status="CLOSED",
            exit_reason=exit_reason,
            realized_pnl=realized,
            broker_fee=fees["broker_fee"],
            taxes=fees["taxes"],
            slippage_cost=fees["slippage_cost"],
        )
        self.storage.append_trade(trade)
        self.storage.remove_open_position(position_id)
        del self._open_positions[position_id]
        return PaperBrokerResult(True, "Paper exit recorded", trade)

    def mark_to_market(self, latest_prices: dict[str, float]) -> float:
        unrealized = 0.0
        for position in self._open_positions.values():
            ltp = float(latest_prices.get(position.instrument, 0.0) or 0.0)
            if ltp <= 0:
                continue
            unrealized += (ltp - position.entry_price) * position.quantity
        return unrealized

    def _apply_slippage(self, price: float, side: str) -> float:
        slip = price * (self.costs.slippage_bps / 10000.0)
        if side.strip().upper() in {"BUY", "CALL", "CE", "PUT", "PE"}:
            return price + slip
        return max(0.0, price - slip)

    def _fees(self, fill_price: float, quantity: int) -> dict[str, float]:
        turnover = max(0.0, fill_price * quantity)
        tax = turnover * self.costs.tax_rate
        return {
            "broker_fee": float(self.costs.brokerage_per_order),
            "taxes": float(tax),
            "slippage_cost": turnover * (self.costs.slippage_bps / 10000.0),
        }
