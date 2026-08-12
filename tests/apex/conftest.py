from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quantlab.apex.agents.base import Candle, MarketSnapshot, OptionContract


@pytest.fixture
def base_snapshot() -> MarketSnapshot:
    t0 = datetime(2026, 7, 11, 9, 15, tzinfo=timezone.utc)
    candles = []
    price = 25000.0
    for i in range(40):
        ts = t0 + timedelta(minutes=i)
        open_price = price
        close_price = price + (0.8 if i % 3 != 0 else -0.2)
        high = max(open_price, close_price) + 0.5
        low = min(open_price, close_price) - 0.5
        candles.append(
            Candle(
                timestamp=ts,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=1000 + i * 20,
            )
        )
        price = close_price

    asof = candles[-1].timestamp
    expiry = asof + timedelta(days=(3 - asof.weekday()) % 7 + 1)
    option_chain = [
        OptionContract(
            symbol="NIFTY26JUL25000CE",
            strike=25000,
            expiry=expiry,
            option_type="CE",
            ltp=120.0,
            bid=118.0,
            ask=122.0,
            oi=5000,
        ),
        OptionContract(
            symbol="NIFTY26JUL25000PE",
            strike=25000,
            expiry=expiry,
            option_type="PE",
            ltp=115.0,
            bid=113.0,
            ask=117.0,
            oi=5100,
        ),
    ]

    futures = candles.copy()
    vix = [
        Candle(
            timestamp=c.timestamp,
            open=15.0,
            high=15.2,
            low=14.8,
            close=15.0 + (0.02 if i % 2 == 0 else -0.02),
            volume=1000.0,
        )
        for i, c in enumerate(candles)
    ]

    return MarketSnapshot(
        snapshot_id="snap-001",
        captured_at=asof,
        nifty_spot=candles,
        nifty_futures=futures,
        india_vix=vix,
        option_chain=option_chain,
    )
