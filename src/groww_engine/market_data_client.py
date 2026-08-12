from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MarketDataClient(Protocol):
    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...

    def get_ltp(self, instrument: str) -> dict[str, Any]: ...

    def list_option_contracts(self, underlying: str, trade_date: date | None = None) -> list[dict[str, Any]]: ...

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...


class NullMarketDataClient:
    """Safe placeholder that blocks all market-data access until wired."""

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]:
        del symbol, interval, lookback_bars
        raise RuntimeError("NullMarketDataClient cannot fetch historical candles.")

    def get_ltp(self, instrument: str) -> dict[str, Any]:
        del instrument
        raise RuntimeError("NullMarketDataClient cannot fetch LTP.")

    def list_option_contracts(self, underlying: str, trade_date: date | None = None) -> list[dict[str, Any]]:
        del underlying, trade_date
        raise RuntimeError("NullMarketDataClient cannot list option contracts.")

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]:
        del contract_symbol, interval, lookback_bars
        raise RuntimeError("NullMarketDataClient cannot fetch option candles.")
