from __future__ import annotations

import logging

from .config import Settings, TradingMode
from .errors import LiveTradingLocked
from .kite_broker import KiteBroker
from .kite_session import authenticated_client
from .paper import PaperBroker
from .protocol import Broker
from .token_store import KeyringTokenStore

LIVE_CONFIRMATION_PHRASE = "ENABLE ZERODHA LIVE ORDERS"


def create_broker(
    settings: Settings, *, live_confirmation: str | None = None
) -> Broker:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if settings.mode is TradingMode.PAPER:
        return PaperBroker()

    if settings.mode is TradingMode.LIVE:
        if not settings.live_trading_enabled:
            raise LiveTradingLocked(
                "Live trading is disabled. Set KITE_LIVE_TRADING_ENABLED=true only "
                "for a supervised live session."
            )
        if live_confirmation != LIVE_CONFIRMATION_PHRASE:
            raise LiveTradingLocked(
                f"Live trading requires the exact runtime confirmation phrase: "
                f"{LIVE_CONFIRMATION_PHRASE}"
            )

    store = KeyringTokenStore(settings.account)
    client = authenticated_client(settings, store)
    return KiteBroker(client, environment=settings.mode.value)
