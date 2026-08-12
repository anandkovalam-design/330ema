from __future__ import annotations

import logging
from typing import Any, Callable

from kiteconnect import KiteConnect

from .errors import BrokerOperationError
from .models import OrderModification, OrderRequest, OrderSnapshot

logger = logging.getLogger(__name__)


class KiteBroker:
    def __init__(self, client: KiteConnect, *, environment: str) -> None:
        self._kite = client
        self._environment = environment

    def place_order(self, order: OrderRequest) -> OrderSnapshot:
        params: dict[str, Any] = {
            "variety": order.variety,
            "exchange": order.exchange,
            "tradingsymbol": order.tradingsymbol,
            "transaction_type": order.side,
            "quantity": order.quantity,
            "product": order.product,
            "order_type": order.order_type,
            "validity": order.validity,
            "price": order.price,
            "trigger_price": order.trigger_price,
            "validity_ttl": order.validity_ttl,
            "tag": order.tag,
            "market_protection": order.market_protection,
            "autoslice": order.autoslice,
        }
        order_id = self._call("place order", self._kite.place_order, **params)
        logger.warning(
            "kite_order_submitted environment=%s order_id=%s symbol=%s side=%s quantity=%s",
            self._environment,
            order_id,
            order.tradingsymbol,
            order.side,
            order.quantity,
        )
        # An order_id only confirms OMS submission, not execution.
        return self.get_order(str(order_id))

    def modify_order(
        self, order_id: str, changes: OrderModification, *, variety: str = "regular"
    ) -> OrderSnapshot:
        self._call(
            "modify order",
            self._kite.modify_order,
            variety=variety,
            order_id=order_id,
            **changes.changes(),
        )
        logger.warning(
            "kite_order_modify_submitted environment=%s order_id=%s",
            self._environment,
            order_id,
        )
        return self.get_order(order_id)

    def cancel_order(
        self, order_id: str, *, variety: str = "regular"
    ) -> OrderSnapshot:
        self._call(
            "cancel order",
            self._kite.cancel_order,
            variety=variety,
            order_id=order_id,
        )
        logger.warning(
            "kite_order_cancel_submitted environment=%s order_id=%s",
            self._environment,
            order_id,
        )
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> OrderSnapshot:
        history = self._call("get order history", self._kite.order_history, order_id)
        if not history:
            raise BrokerOperationError(f"Kite returned no history for order {order_id}.")
        return OrderSnapshot.from_kite(history[-1])

    def list_orders(self) -> list[OrderSnapshot]:
        orders = self._call("list orders", self._kite.orders)
        return [OrderSnapshot.from_kite(item) for item in orders]

    @staticmethod
    def _call(operation: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            logger.exception("kite_operation_failed operation=%s", operation)
            raise BrokerOperationError(
                f"Kite could not {operation}: {type(exc).__name__}: {exc}"
            ) from exc
