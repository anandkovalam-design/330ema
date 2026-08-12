"""Adapters that freeze live Zerodha data into APEX snapshots."""

from __future__ import annotations

from datetime import date, datetime, time as wall_time, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from quantlab.apex.agents.base import Candle, MarketSnapshot, OptionContract
from quantlab.apex.orchestration.pipeline import ApexPipeline, PipelineResult


IST = ZoneInfo("Asia/Kolkata")
NIFTY_SPOT_INSTRUMENT_TOKEN = 256265


class ApexKiteLike(Protocol):
    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        *,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]: ...

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]: ...

    def quote_many(self, instruments: list[str]) -> dict[str, Any]: ...


def _as_aware_datetime(value: object, *, default_tz: ZoneInfo = IST) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=default_tz)
    if isinstance(value, date):
        return datetime.combine(value, wall_time(15, 30), tzinfo=default_tz)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=default_tz)


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rows_to_candles(rows: list[dict[str, Any]]) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        timestamp = _as_aware_datetime(row.get("date"))
        if timestamp is None:
            continue
        candles.append(
            Candle(
                timestamp=timestamp,
                open=_to_float(row.get("open"), 0.0),
                high=_to_float(row.get("high"), 0.0),
                low=_to_float(row.get("low"), 0.0),
                close=_to_float(row.get("close"), 0.0),
                volume=_to_float(row.get("volume"), 0.0),
                arrived_at=timestamp,
            )
        )
    candles.sort(key=lambda candle: candle.timestamp)
    return candles


def _discover_india_vix_token(instruments: list[dict[str, Any]]) -> int | None:
    for row in instruments:
        symbol = str(row.get("tradingsymbol", "")).upper()
        name = str(row.get("name", "")).upper()
        if "VIX" not in symbol and "VIX" not in name:
            continue
        token = int(_to_float(row.get("instrument_token", 0), 0.0))
        if token > 0:
            return token
    return None


def _discover_nifty_futures_token(instruments: list[dict[str, Any]], asof: datetime) -> int | None:
    session_date = asof.date()
    candidates: list[tuple[datetime, int]] = []
    for row in instruments:
        symbol = str(row.get("tradingsymbol", "")).upper()
        segment = str(row.get("segment", "")).upper()
        instrument_type = str(row.get("instrument_type", "")).upper()
        if not symbol.startswith("NIFTY"):
            continue
        if "FUT" not in segment and "FUT" not in instrument_type:
            continue
        expiry = _as_aware_datetime(row.get("expiry"))
        if expiry is None or expiry.date() < session_date:
            continue
        token = int(_to_float(row.get("instrument_token", 0), 0.0))
        if token <= 0:
            continue
        candidates.append((expiry, token))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _build_option_chain(
    kite: ApexKiteLike,
    *,
    instruments: list[dict[str, Any]],
    asof: datetime,
    spot_price: float,
    strike_window: int = 100,
) -> list[OptionContract]:
    future_rows = []
    for row in instruments:
        symbol = str(row.get("tradingsymbol", "")).upper()
        option_type = str(row.get("instrument_type", "")).upper()
        expiry = _as_aware_datetime(row.get("expiry"))
        if not symbol.startswith("NIFTY"):
            continue
        if option_type not in {"CE", "PE"}:
            continue
        if expiry is None or expiry < asof:
            continue
        future_rows.append(row)

    if not future_rows:
        return []

    nearest_expiry = min(_as_aware_datetime(row.get("expiry")) for row in future_rows if _as_aware_datetime(row.get("expiry")) is not None)
    atm_strike = int(round(spot_price / 50.0) * 50)
    shortlisted = []
    for row in future_rows:
        expiry = _as_aware_datetime(row.get("expiry"))
        if expiry != nearest_expiry:
            continue
        strike = int(_to_float(row.get("strike", 0.0), 0.0))
        if abs(strike - atm_strike) > strike_window:
            continue
        shortlisted.append(row)

    if not shortlisted:
        return []

    quote_symbols = [f"NFO:{row['tradingsymbol']}" for row in shortlisted]
    quotes = kite.quote_many(quote_symbols)
    contracts: list[OptionContract] = []
    for row in shortlisted:
        symbol = str(row.get("tradingsymbol", ""))
        quote = quotes.get(f"NFO:{symbol}", {})
        depth = quote.get("depth", {}) if isinstance(quote, dict) else {}
        buy_depth = depth.get("buy", []) if isinstance(depth, dict) else []
        sell_depth = depth.get("sell", []) if isinstance(depth, dict) else []
        ltp = _to_float(quote.get("last_price"), 0.0)
        bid = _to_float((buy_depth[0] or {}).get("price", ltp) if buy_depth else ltp, ltp)
        ask = _to_float((sell_depth[0] or {}).get("price", ltp) if sell_depth else ltp, ltp)
        oi = int(_to_float(quote.get("oi"), 0.0))
        expiry = _as_aware_datetime(row.get("expiry"))
        strike = int(_to_float(row.get("strike", 0.0), 0.0))
        option_type = str(row.get("instrument_type", "")).upper()
        if expiry is None or strike <= 0 or ltp <= 0:
            continue
        contracts.append(
            OptionContract(
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                bid=bid,
                ask=max(ask, bid),
                ltp=ltp,
                oi=oi,
            )
        )
    return contracts


def build_live_nifty_snapshot(
    kite: ApexKiteLike,
    *,
    lookback_bars: int = 60,
    interval: str = "5minute",
    asof: datetime | None = None,
) -> MarketSnapshot:
    current = asof or datetime.now(IST)
    bars = max(lookback_bars, 40)
    from_dt = current - timedelta(minutes=5 * (bars + 5))

    instruments = kite.instruments()

    spot_rows = kite.historical_data(
        NIFTY_SPOT_INSTRUMENT_TOKEN,
        from_dt,
        current,
        interval,
        continuous=False,
        oi=False,
    )
    spot_candles = _rows_to_candles(spot_rows)[-bars:]
    captured_at = spot_candles[-1].timestamp if spot_candles else current

    futures_candles: list[Candle] = []
    future_token = _discover_nifty_futures_token(instruments, captured_at)
    if future_token is not None:
        future_rows = kite.historical_data(
            future_token,
            from_dt,
            current,
            interval,
            continuous=False,
            oi=False,
        )
        futures_candles = _rows_to_candles(future_rows)[-bars:]

    vix_candles: list[Candle] = []
    vix_token = _discover_india_vix_token(instruments)
    if vix_token is not None:
        vix_rows = kite.historical_data(
            vix_token,
            from_dt,
            current,
            interval,
            continuous=False,
            oi=False,
        )
        vix_candles = _rows_to_candles(vix_rows)[-bars:]

    option_chain = _build_option_chain(
        kite,
        instruments=instruments,
        asof=captured_at,
        spot_price=spot_candles[-1].close if spot_candles else 0.0,
    )

    snapshot_stamp = captured_at.astimezone(IST).strftime("%Y%m%dT%H%M%S")
    return MarketSnapshot(
        snapshot_id=f"nifty-live-{snapshot_stamp}",
        captured_at=captured_at,
        nifty_spot=spot_candles,
        nifty_futures=futures_candles,
        india_vix=vix_candles,
        option_chain=option_chain,
    )


def run_live_nifty_apex_pipeline(
    kite: ApexKiteLike,
    *,
    pipeline: ApexPipeline | None = None,
    lookback_bars: int = 60,
    interval: str = "5minute",
    asof: datetime | None = None,
    audit_dir: str | Path | None = None,
) -> tuple[MarketSnapshot, PipelineResult, ApexPipeline]:
    active_pipeline = pipeline or ApexPipeline(audit_dir=Path(audit_dir) if audit_dir is not None else None)
    snapshot = build_live_nifty_snapshot(kite, lookback_bars=lookback_bars, interval=interval, asof=asof)
    result = active_pipeline.run(snapshot)
    return snapshot, result, active_pipeline