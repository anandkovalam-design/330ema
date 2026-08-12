from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from kiteconnect import KiteTicker

from .config import Settings, TradingMode
from .errors import ConfigurationError
from .token_store import KeyringTokenStore

logger = logging.getLogger(__name__)
TickHandler = Callable[[list[dict[str, Any]]], None]
OrderHandler = Callable[[dict[str, Any]], None]


class KiteMarketStream:
    """Official KiteTicker wrapper for live quotes and asynchronous order updates."""

    def __init__(
        self,
        settings: Settings,
        *,
        instrument_tokens: Iterable[int],
        on_ticks: TickHandler,
        on_order_update: OrderHandler,
        mode: str = "full",
    ) -> None:
        if settings.mode is not TradingMode.LIVE:
            raise ConfigurationError(
                "KiteMarketStream is for LIVE streaming. Keep strategy testing on "
                "recorded/simulated data in PAPER or SANDBOX mode."
            )
        api_key = settings.effective_api_key
        if not api_key:
            raise ConfigurationError("KITE_API_KEY is required.")
        access_token = KeyringTokenStore(settings.account).load()
        self._tokens = list(dict.fromkeys(int(token) for token in instrument_tokens))
        if not self._tokens:
            raise ConfigurationError("At least one instrument token is required.")
        self._mode = mode
        self._on_ticks_callback = on_ticks
        self._on_order_callback = on_order_update
        self._ticker = KiteTicker(api_key, access_token, reconnect=True)
        self._wire_callbacks()

    def connect(self, *, threaded: bool = True) -> None:
        self._ticker.connect(threaded=threaded)

    def close(self) -> None:
        self._ticker.close()

    def _wire_callbacks(self) -> None:
        def on_connect(ws: KiteTicker, response: Any) -> None:
            logger.info("kite_websocket_connected subscriptions=%s", len(self._tokens))
            ws.subscribe(self._tokens)
            mode_map = {
                "ltp": ws.MODE_LTP,
                "quote": ws.MODE_QUOTE,
                "full": ws.MODE_FULL,
            }
            try:
                ws.set_mode(mode_map[self._mode], self._tokens)
            except KeyError as exc:
                raise ConfigurationError("WebSocket mode must be ltp, quote, or full.") from exc

        def on_ticks(ws: KiteTicker, ticks: list[dict[str, Any]]) -> None:
            del ws
            try:
                self._on_ticks_callback(ticks)
            except Exception:
                logger.exception("tick_handler_failed")

        def on_order_update(ws: KiteTicker, data: dict[str, Any]) -> None:
            del ws
            try:
                self._on_order_callback(data)
            except Exception:
                logger.exception("order_update_handler_failed")

        def on_error(ws: KiteTicker, code: int, reason: str) -> None:
            del ws
            logger.error("kite_websocket_error code=%s reason=%s", code, reason)

        def on_close(ws: KiteTicker, code: int, reason: str) -> None:
            del ws
            logger.warning("kite_websocket_closed code=%s reason=%s", code, reason)

        self._ticker.on_connect = on_connect
        self._ticker.on_ticks = on_ticks
        self._ticker.on_order_update = on_order_update
        self._ticker.on_error = on_error
        self._ticker.on_close = on_close
