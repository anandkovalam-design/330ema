"""Safe Zerodha Kite Connect helpers for authentication and live data only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aadithya_quantlab.data.contracts import normalize_intraday_ohlcv, validate_intraday_ohlcv_schema
from aadithya_quantlab.trading.zerodha import build_kite_login_url


DEFAULT_ACCESS_TOKEN_FILE = Path("outputs/zerodha/access_token.txt")


class NoHistoricalCandlesError(ValueError):
    """Raised when the broker returns no historical candles for a query."""


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} before running this command.")
    return value


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def latest_weekday_session_hint(reference: datetime | None = None) -> str:
    current = reference or datetime.now(UTC)
    session_day = current.date()
    while session_day.weekday() >= 5:
        session_day -= timedelta(days=1)
    return f"Try {session_day.isoformat()} 09:15 to 15:30 IST for an active weekday session."


def load_access_token(
    *,
    env_var: str = "ZERODHA_ACCESS_TOKEN",
    fallback_path: str | Path = DEFAULT_ACCESS_TOKEN_FILE,
) -> str:
    token = os.getenv(env_var, "").strip()
    if token:
        return token

    path = Path(fallback_path)
    if path.exists():
        file_token = path.read_text(encoding="utf-8").strip()
        if file_token:
            return file_token

    raise ValueError(
        f"Set {env_var} or provide a non-empty token file at {path}."
    )


def access_token_preview(token: str) -> str:
    return _mask_secret(token)


def _load_kite_connect_class() -> Any:
    from kiteconnect import KiteConnect

    return KiteConnect


@dataclass(frozen=True)
class AccessTokenResult:
    token_output_path: Path
    masked_access_token: str


@dataclass(frozen=True)
class DailyLoginInputs:
    """Inputs required for the once-per-day Zerodha login flow."""

    api_key: str
    api_secret: str
    request_token: str
    token_output_path: Path = DEFAULT_ACCESS_TOKEN_FILE


class SafeKiteClient:
    """Wrapper that intentionally exposes read-only broker operations."""

    def __init__(self, kite: Any) -> None:
        self._kite = kite

    def profile(self) -> dict[str, Any]:
        return self._kite.profile()

    def ltp(self, instrument: str) -> dict[str, Any]:
        return self._kite.ltp(instrument)

    def quote(self, instrument: str) -> dict[str, Any]:
        return self._kite.quote(instrument)

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        *,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]:
        return self._kite.historical_data(
            instrument_token,
            from_date,
            to_date,
            interval,
            continuous=continuous,
            oi=oi,
        )

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Live order placement is disabled in Aadithya QuantLab.")


def build_login_url_from_env() -> str:
    api_key = _get_required_env("ZERODHA_API_KEY")
    return build_kite_login_url(api_key)


def generate_access_token_from_env(token_output_path: str | Path) -> AccessTokenResult:
    return generate_access_token(
        api_key=_get_required_env("ZERODHA_API_KEY"),
        api_secret=_get_required_env("ZERODHA_API_SECRET"),
        request_token=_get_required_env("ZERODHA_REQUEST_TOKEN"),
        token_output_path=token_output_path,
    )


def generate_access_token(
    *,
    api_key: str,
    api_secret: str,
    request_token: str,
    token_output_path: str | Path,
) -> AccessTokenResult:
    """Exchange a fresh request token for an access token and save it locally."""

    kite_connect_class = _load_kite_connect_class()
    kite = kite_connect_class(api_key=api_key)
    session_data = kite.generate_session(request_token, api_secret=api_secret)

    access_token = str(session_data.get("access_token", "")).strip()
    if not access_token:
        raise ValueError("Zerodha session response did not include an access_token.")

    output_path = Path(token_output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(access_token, encoding="utf-8")
    return AccessTokenResult(
        token_output_path=output_path,
        masked_access_token=_mask_secret(access_token),
    )


def run_daily_login(inputs: DailyLoginInputs) -> AccessTokenResult:
    """Run the daily login token exchange without printing sensitive values."""

    return generate_access_token(
        api_key=inputs.api_key.strip(),
        api_secret=inputs.api_secret.strip(),
        request_token=inputs.request_token.strip(),
        token_output_path=inputs.token_output_path,
    )


def build_safe_kite_client_from_env() -> SafeKiteClient:
    api_key = _get_required_env("ZERODHA_API_KEY")
    access_token = load_access_token()

    kite_connect_class = _load_kite_connect_class()
    kite = kite_connect_class(api_key=api_key)
    kite.set_access_token(access_token)
    return SafeKiteClient(kite)


def fetch_live_data_from_env(instrument: str, mode: str = "ltp") -> dict[str, Any]:
    kite = build_safe_kite_client_from_env()
    selected_mode = mode.strip().lower()
    if selected_mode == "ltp":
        return kite.ltp(instrument)
    if selected_mode == "quote":
        return kite.quote(instrument)
    raise ValueError("mode must be either ltp or quote")


def fetch_historical_to_canonical_csv(
    *,
    instrument_token: int,
    symbol: str,
    interval: str,
    from_date: datetime,
    to_date: datetime,
    output_csv: str | Path,
    source: str = "zerodha_kite",
) -> Path:
    kite = build_safe_kite_client_from_env()
    rows = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
        continuous=False,
        oi=False,
    )

    bars = pd.DataFrame(rows)
    if bars.empty:
        hint = latest_weekday_session_hint()
        raise NoHistoricalCandlesError(
            f"No historical candles returned for {symbol} ({interval}). {hint}"
        )

    bars = bars.rename(columns={"date": "timestamp"})
    bars["symbol"] = symbol.strip()
    bars["session_date"] = pd.to_datetime(bars["timestamp"], errors="coerce").dt.date
    if "volume" not in bars.columns:
        bars["volume"] = 0.0
    bars["source"] = source.strip()
    bars["adjustment_flag"] = ""

    canonical_columns = [
        "symbol",
        "timestamp",
        "session_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
        "adjustment_flag",
    ]
    canonical = normalize_intraday_ohlcv(bars[canonical_columns])
    validate_intraday_ohlcv_schema(canonical).raise_for_errors()

    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canonical.to_csv(output_path, index=False)
    return output_path
