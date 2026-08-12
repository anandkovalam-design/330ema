from dataclasses import replace

import pytest

from kite_gateway import (
    LIVE_CONFIRMATION_PHRASE,
    OrderModification,
    OrderRequest,
    Settings,
    TradingMode,
    create_broker,
)
from kite_gateway.errors import BrokerOperationError, ConfigurationError, LiveTradingLocked
from kite_gateway.kite_broker import KiteBroker


def test_default_paper_order_never_needs_credentials():
    broker = create_broker(Settings())
    order = broker.place_order(
        OrderRequest(
            exchange="NSE",
            tradingsymbol="INFY",
            side="BUY",
            quantity=1,
            product="CNC",
        )
    )
    assert order.order_id.startswith("PAPER-")
    assert order.status == "COMPLETE"


def test_open_paper_limit_order_can_be_modified_and_cancelled():
    broker = create_broker(Settings())
    order = broker.place_order(
        OrderRequest(
            exchange="NSE",
            tradingsymbol="INFY",
            side="BUY",
            quantity=1,
            product="CNC",
            order_type="LIMIT",
            price=1000,
        )
    )
    changed = broker.modify_order(order.order_id, OrderModification(quantity=2))
    assert changed.quantity == 2
    cancelled = broker.cancel_order(order.order_id)
    assert cancelled.status == "CANCELLED"
    with pytest.raises(BrokerOperationError):
        broker.cancel_order(order.order_id)


def test_invalid_limit_order_is_rejected_before_broker_call():
    with pytest.raises(ConfigurationError, match="LIMIT requires price"):
        OrderRequest(
            exchange="NSE",
            tradingsymbol="INFY",
            side="BUY",
            quantity=1,
            product="CNC",
            order_type="LIMIT",
        )


def test_live_mode_requires_environment_gate_before_authentication():
    settings = Settings(
        mode=TradingMode.LIVE,
        api_key="example",
        api_secret="example-secret",
        live_trading_enabled=False,
    )
    with pytest.raises(LiveTradingLocked, match="disabled"):
        create_broker(settings, live_confirmation=LIVE_CONFIRMATION_PHRASE)


def test_live_mode_requires_exact_runtime_phrase_before_authentication():
    settings = Settings(
        mode=TradingMode.LIVE,
        api_key="example",
        api_secret="example-secret",
        live_trading_enabled=True,
    )
    with pytest.raises(LiveTradingLocked, match="exact runtime"):
        create_broker(settings, live_confirmation="yes")


def test_sandbox_credentials_are_fixed_demo_values():
    settings = Settings(mode=TradingMode.SANDBOX)
    assert settings.effective_api_key == "sandboxdemo"
    assert settings.effective_api_secret == "sandboxdemo-secret"


class FakeKite:
    def __init__(self):
        self.last_call = None
        self.status = "OPEN"

    def place_order(self, **kwargs):
        self.last_call = ("place", kwargs)
        return "KITE-123"

    def modify_order(self, **kwargs):
        self.last_call = ("modify", kwargs)
        return kwargs["order_id"]

    def cancel_order(self, **kwargs):
        self.last_call = ("cancel", kwargs)
        self.status = "CANCELLED"
        return kwargs["order_id"]

    def order_history(self, order_id):
        return [
            {
                "order_id": order_id,
                "status": self.status,
                "exchange": "NSE",
                "tradingsymbol": "INFY",
                "transaction_type": "BUY",
                "quantity": 1,
                "filled_quantity": 0,
                "average_price": 0,
            }
        ]

    def orders(self):
        return self.order_history("KITE-123")


def test_kite_adapter_maps_order_lifecycle_without_network():
    fake = FakeKite()
    broker = KiteBroker(fake, environment="SANDBOX")
    placed = broker.place_order(
        OrderRequest(
            exchange="NSE",
            tradingsymbol="INFY",
            side="BUY",
            quantity=1,
            product="CNC",
            order_type="LIMIT",
            price=1000,
            tag="testorder",
        )
    )
    assert placed.order_id == "KITE-123"
    assert fake.last_call[1]["transaction_type"] == "BUY"
    assert fake.last_call[1]["price"] == 1000

    broker.modify_order("KITE-123", OrderModification(price=999))
    assert fake.last_call == (
        "modify",
        {"variety": "regular", "order_id": "KITE-123", "price": 999},
    )

    cancelled = broker.cancel_order("KITE-123")
    assert cancelled.status == "CANCELLED"
    assert [item.order_id for item in broker.list_orders()] == ["KITE-123"]
