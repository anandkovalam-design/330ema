from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd


logger = logging.getLogger(__name__)


class GrowwSdkLike(Protocol):
    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...

    def get_ltp(self, instrument: str) -> dict[str, Any]: ...

    def list_option_contracts(self, underlying: str, trade_date: date | None = None) -> list[dict[str, Any]]: ...

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class AuthState:
    has_api_key: bool
    has_api_secret: bool
    has_access_token: bool


class GrowwClient:
    """Thin adapter for Groww market-data access.

    The concrete SDK wiring should follow official Groww API docs. This adapter
    keeps credential handling outside logs and isolates all order surfaces.
    """

    def __init__(self, sdk_client: GrowwSdkLike) -> None:
        self._sdk = sdk_client

    @staticmethod
    def auth_state() -> AuthState:
        return AuthState(
            has_api_key=bool(os.getenv("GROWW_API_KEY", "").strip()),
            has_api_secret=bool(os.getenv("GROWW_API_SECRET", "").strip()),
            has_access_token=bool(os.getenv("GROWW_ACCESS_TOKEN", "").strip()),
        )

    @staticmethod
    def assert_auth_configured() -> None:
        state = GrowwClient.auth_state()
        key_pair_complete = state.has_api_key and state.has_api_secret
        key_pair_partial = state.has_api_key != state.has_api_secret
        access_token_only = state.has_access_token and not state.has_api_key and not state.has_api_secret
        key_pair_only = key_pair_complete and not state.has_access_token
        full_triplet = key_pair_complete and state.has_access_token

        if key_pair_partial:
            raise ValueError(
                "Groww authentication is incomplete. "
                "Provide both GROWW_API_KEY and GROWW_API_SECRET together, "
                "or use GROWW_ACCESS_TOKEN-only flow."
            )

        if not (access_token_only or key_pair_only or full_triplet):
            raise ValueError(
                "Groww authentication is incomplete. Configure either: "
                "(1) GROWW_ACCESS_TOKEN, or "
                "(2) GROWW_API_KEY and GROWW_API_SECRET, or "
                "(3) all three variables."
            )

    def fetch_ohlc(self, symbol: str, *, interval: str = "5m", lookback_bars: int = 120, max_retries: int = 2) -> pd.DataFrame:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                rows = self._sdk.get_historical_ohlc(symbol=symbol, interval=interval, lookback_bars=lookback_bars)
                frame = pd.DataFrame(rows)
                if frame.empty:
                    raise ValueError("No market data returned")
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
                frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
                return frame
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Groww market-data request failed after retries: {last_error}")

    def fetch_ltp(self, instrument: str, *, max_retries: int = 2) -> tuple[float, datetime]:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                payload = self._sdk.get_ltp(instrument)
                ltp = float(payload.get("ltp", 0.0) or 0.0)
                ts_raw = payload.get("timestamp")
                ts = pd.to_datetime(ts_raw, errors="coerce")
                if ltp <= 0 or pd.isna(ts):
                    raise ValueError("Invalid LTP payload")
                return ltp, ts.to_pydatetime()
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Groww LTP request failed after retries: {last_error}")


class NullGrowwSdk:
    """Safe placeholder SDK client.

    Replace this with official Groww SDK wiring in deployment. This placeholder
    guarantees no accidental live network/order calls during local testing.
    """

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]:
        raise RuntimeError("NullGrowwSdk cannot fetch market data. Inject official Groww SDK client.")

    def get_ltp(self, instrument: str) -> dict[str, Any]:
        raise RuntimeError("NullGrowwSdk cannot fetch LTP. Inject official Groww SDK client.")

    def list_option_contracts(self, underlying: str, trade_date: date | None = None) -> list[dict[str, Any]]:
        del underlying, trade_date
        raise RuntimeError("NullGrowwSdk cannot list contracts. Inject official Groww SDK client.")

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]:
        del contract_symbol, interval, lookback_bars
        raise RuntimeError("NullGrowwSdk cannot fetch option candles. Inject official Groww SDK client.")
