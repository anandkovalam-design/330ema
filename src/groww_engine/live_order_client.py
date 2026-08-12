from __future__ import annotations

from typing import Any, Protocol


class LiveOrderSdkLike(Protocol):
    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def modify_order(self, order_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def cancel_order(self, order_id: str) -> dict[str, Any]: ...


class LiveOrderClient:
    """Isolated adapter for live order endpoints only."""

    def __init__(self, sdk_client: LiveOrderSdkLike) -> None:
        self._sdk = sdk_client

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._sdk.create_order(payload)

    def modify_order(self, order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._sdk.modify_order(order_id, payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._sdk.cancel_order(order_id)
