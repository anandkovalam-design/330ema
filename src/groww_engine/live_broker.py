from __future__ import annotations

import logging

from .config import EngineConfig, EffectiveTradingMode
from .live_order_client import LiveOrderClient
from .models import LiveOrderContext, OrderRequest


logger = logging.getLogger(__name__)

class LiveBroker:
    def __init__(self, config: EngineConfig, client: LiveOrderClient) -> None:
        self.config = config
        self.client = client

    def _assert_live_enabled(self) -> None:
        if self.config.effective_trading_mode is not EffectiveTradingMode.LIVE:
            raise RuntimeError("LIVE broker blocked: effective mode is PAPER")

    def validate_order_context(self, context: LiveOrderContext) -> None:
        checks = {
            "Authentication": context.authenticated,
            "Available margin": context.available_margin > 0,
            "Symbol and contract": context.symbol_valid,
            "Quantity limit": context.quantity_valid,
            "Current position": not context.has_open_position,
            "Daily loss limit": context.daily_loss_ok,
            "Number of trades": context.trades_remaining,
            "Trading time": context.within_trading_time,
            "Market-data freshness": context.market_data_fresh,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError("LIVE validation failed: " + ", ".join(failed))

    def place_order(self, order: OrderRequest, context: LiveOrderContext) -> dict[str, object]:
        self._assert_live_enabled()
        self.validate_order_context(context)
        logger.warning("LIVE MODE ACTIVE: real order placement enabled for supervised session")

        payload = {
            "symbol": order.symbol,
            "instrument": order.instrument,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.requested_price,
            "order_type": "MARKET",
            "product": "INTRADAY",
            "expiry": order.expiry,
            "strike": order.strike,
            "option_type": order.option_type,
            "signal_id": order.signal_id,
        }
        # The real order endpoint call is intentionally isolated in this file.
        return self.client.create_order(payload)

    def modify_order(self, order_id: str, changes: dict[str, object]) -> dict[str, object]:
        self._assert_live_enabled()
        return self.client.modify_order(order_id, changes)

    def cancel_order(self, order_id: str) -> dict[str, object]:
        self._assert_live_enabled()
        return self.client.cancel_order(order_id)
