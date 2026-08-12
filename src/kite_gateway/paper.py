from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from .errors import BrokerOperationError
from .models import OrderModification, OrderRequest, OrderSnapshot

logger = logging.getLogger(__name__)


class PaperBroker:
    """In-memory broker with no network or real-money capability."""

    def __init__(self) -> None:
        self._orders: dict[str, OrderSnapshot] = {}
        self._lock = RLock()

    def place_order(self, order: OrderRequest) -> OrderSnapshot:
        order_id = f"PAPER-{uuid4().hex[:12].upper()}"
        filled = order.order_type == "MARKET"
        snapshot = OrderSnapshot(
            order_id=order_id,
            status="COMPLETE" if filled else "OPEN",
            exchange=order.exchange,
            tradingsymbol=order.tradingsymbol,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.quantity if filled else 0,
            average_price=float(order.price or 0.0),
            updated_at=datetime.now(timezone.utc),
            raw={"mode": "PAPER", "request": vars(order)},
        )
        with self._lock:
            self._orders[order_id] = snapshot
        logger.info("paper_order_placed order_id=%s status=%s", order_id, snapshot.status)
        return snapshot

    def modify_order(
        self, order_id: str, changes: OrderModification, *, variety: str = "regular"
    ) -> OrderSnapshot:
        del variety
        with self._lock:
            current = self._require_open(order_id)
            updated = replace(
                current,
                quantity=changes.quantity or current.quantity,
                updated_at=datetime.now(timezone.utc),
                raw={**(current.raw or {}), "last_modification": changes.changes()},
            )
            self._orders[order_id] = updated
        logger.info("paper_order_modified order_id=%s", order_id)
        return updated

    def cancel_order(
        self, order_id: str, *, variety: str = "regular"
    ) -> OrderSnapshot:
        del variety
        with self._lock:
            current = self._require_open(order_id)
            updated = replace(
                current, status="CANCELLED", updated_at=datetime.now(timezone.utc)
            )
            self._orders[order_id] = updated
        logger.info("paper_order_cancelled order_id=%s", order_id)
        return updated

    def get_order(self, order_id: str) -> OrderSnapshot:
        with self._lock:
            try:
                return self._orders[order_id]
            except KeyError as exc:
                raise BrokerOperationError(f"Unknown paper order {order_id}") from exc

    def list_orders(self) -> list[OrderSnapshot]:
        with self._lock:
            return list(self._orders.values())

    def _require_open(self, order_id: str) -> OrderSnapshot:
        current = self.get_order(order_id)
        if current.status not in {"OPEN", "TRIGGER PENDING"}:
            raise BrokerOperationError(
                f"Order {order_id} cannot change from status {current.status}"
            )
        return current
