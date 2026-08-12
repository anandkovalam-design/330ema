from __future__ import annotations

from typing import Protocol

from .models import OrderModification, OrderRequest, OrderSnapshot


class Broker(Protocol):
    def place_order(self, order: OrderRequest) -> OrderSnapshot: ...

    def modify_order(
        self, order_id: str, changes: OrderModification, *, variety: str = "regular"
    ) -> OrderSnapshot: ...

    def cancel_order(
        self, order_id: str, *, variety: str = "regular"
    ) -> OrderSnapshot: ...

    def get_order(self, order_id: str) -> OrderSnapshot: ...

    def list_orders(self) -> list[OrderSnapshot]: ...
