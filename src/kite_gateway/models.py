from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .errors import ConfigurationError

ALLOWED_SIDES = {"BUY", "SELL"}
ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
ALLOWED_PRODUCTS = {"CNC", "NRML", "MIS", "MTF"}
ALLOWED_VALIDITIES = {"DAY", "IOC", "TTL"}
ALLOWED_VARIETIES = {"regular", "amo", "co", "iceberg", "auction"}


@dataclass(frozen=True)
class OrderRequest:
    exchange: str
    tradingsymbol: str
    side: str
    quantity: int
    product: str
    order_type: str = "MARKET"
    variety: str = "regular"
    validity: str = "DAY"
    price: float | None = None
    trigger_price: float | None = None
    validity_ttl: int | None = None
    tag: str | None = None
    market_protection: float | None = None
    autoslice: bool = False

    def __post_init__(self) -> None:
        _require(self.exchange.strip(), "exchange is required")
        _require(self.tradingsymbol.strip(), "tradingsymbol is required")
        _require(self.side in ALLOWED_SIDES, "side must be BUY or SELL")
        _require(self.quantity > 0, "quantity must be positive")
        _require(self.product in ALLOWED_PRODUCTS, "unsupported product")
        _require(self.order_type in ALLOWED_ORDER_TYPES, "unsupported order_type")
        _require(self.validity in ALLOWED_VALIDITIES, "unsupported validity")
        _require(self.variety in ALLOWED_VARIETIES, "unsupported variety")
        if self.order_type == "LIMIT":
            _require(self.price is not None and self.price > 0, "LIMIT requires price")
        if self.order_type in {"SL", "SL-M"}:
            _require(
                self.trigger_price is not None and self.trigger_price > 0,
                f"{self.order_type} requires trigger_price",
            )
        if self.validity == "TTL":
            _require(
                self.validity_ttl is not None and self.validity_ttl > 0,
                "TTL validity requires validity_ttl",
            )
        if self.tag is not None:
            _require(
                self.tag.isalnum() and len(self.tag) <= 20,
                "tag must be alphanumeric and at most 20 characters",
            )


@dataclass(frozen=True)
class OrderModification:
    quantity: int | None = None
    price: float | None = None
    order_type: str | None = None
    trigger_price: float | None = None
    validity: str | None = None
    disclosed_quantity: int | None = None
    market_protection: float | None = None

    def __post_init__(self) -> None:
        if self.quantity is not None:
            _require(self.quantity > 0, "quantity must be positive")
        if self.order_type is not None:
            _require(self.order_type in ALLOWED_ORDER_TYPES, "unsupported order_type")
        if self.validity is not None:
            _require(self.validity in ALLOWED_VALIDITIES, "unsupported validity")

    def changes(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in vars(self).items()
            if value is not None
        }


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str
    status: str
    exchange: str
    tradingsymbol: str
    side: str
    quantity: int
    filled_quantity: int = 0
    average_price: float = 0.0
    status_message: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] | None = None

    @classmethod
    def from_kite(cls, payload: dict[str, Any]) -> "OrderSnapshot":
        return cls(
            order_id=str(payload["order_id"]),
            status=str(payload.get("status", "UNKNOWN")),
            exchange=str(payload.get("exchange", "")),
            tradingsymbol=str(payload.get("tradingsymbol", "")),
            side=str(payload.get("transaction_type", "")),
            quantity=int(payload.get("quantity", 0)),
            filled_quantity=int(payload.get("filled_quantity", 0)),
            average_price=float(payload.get("average_price") or 0.0),
            status_message=payload.get("status_message"),
            updated_at=datetime.now(timezone.utc),
            raw=dict(payload),
        )


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ConfigurationError(message)
