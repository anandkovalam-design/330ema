from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aadithya_quantlab.trading.apex_live import build_live_nifty_snapshot, run_live_nifty_apex_pipeline
from quantlab.apex.orchestration.pipeline import ApexPipeline


IST = ZoneInfo("Asia/Kolkata")


class FakeKite:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 14, 11, 0, tzinfo=IST)

    def instruments(self, exchange: str | None = None) -> list[dict[str, object]]:
        future_expiry = date.today() + timedelta(days=30)
        option_expiry = date.today() + timedelta(days=7)
        rows = [
            {
                "tradingsymbol": "INDIAVIX",
                "name": "INDIA VIX",
                "instrument_token": 9001,
                "segment": "INDICES",
            },
            {
                "tradingsymbol": "NIFTY26JULFUT",
                "name": "NIFTY",
                "instrument_token": 7001,
                "instrument_type": "FUT",
                "segment": "NFO-FUT",
                "expiry": future_expiry,
            },
            {
                "tradingsymbol": "NIFTY26JUL25000CE",
                "instrument_token": 8001,
                "instrument_type": "CE",
                "segment": "NFO-OPT",
                "expiry": option_expiry,
                "strike": 25000,
            },
            {
                "tradingsymbol": "NIFTY26JUL25000PE",
                "instrument_token": 8002,
                "instrument_type": "PE",
                "segment": "NFO-OPT",
                "expiry": option_expiry,
                "strike": 25000,
            },
        ]
        if exchange == "NFO":
            return [row for row in rows if str(row.get("segment", "")).startswith("NFO")]
        return rows

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        *,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, object]]:
        del interval, continuous, oi
        base_price = {
            256265: 25000.0,
            7001: 25020.0,
            9001: 15.0,
        }.get(instrument_token, 100.0)
        rows = []
        current = from_date
        i = 0
        while current <= to_date and i < 80:
            close = base_price + (0.8 if i % 3 != 0 else -0.2) * i / 4.0
            rows.append(
                {
                    "date": current,
                    "open": close - 1.0,
                    "high": close + 1.0,
                    "low": close - 1.5,
                    "close": close,
                    "volume": 1000 + i,
                }
            )
            current += timedelta(minutes=5)
            i += 1
        return rows

    def quote_many(self, instruments: list[str]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for instrument in instruments:
            if instrument.endswith("CE"):
                payload[instrument] = {
                    "last_price": 122.0,
                    "oi": 5000,
                    "depth": {"buy": [{"price": 121.0}], "sell": [{"price": 123.0}]},
                }
            else:
                payload[instrument] = {
                    "last_price": 118.0,
                    "oi": 5100,
                    "depth": {"buy": [{"price": 117.0}], "sell": [{"price": 119.0}]},
                }
        return payload


def test_build_live_nifty_snapshot_includes_all_required_feeds() -> None:
    snapshot = build_live_nifty_snapshot(FakeKite(), lookback_bars=40)
    assert len(snapshot.nifty_spot) >= 40
    assert len(snapshot.nifty_futures) >= 40
    assert len(snapshot.india_vix) >= 40
    assert snapshot.option_chain


def test_run_live_nifty_apex_pipeline_produces_audited_reports(tmp_path: Path) -> None:
    snapshot, result, pipeline = run_live_nifty_apex_pipeline(
        FakeKite(),
        pipeline=ApexPipeline(audit_dir=tmp_path),
        lookback_bars=40,
        audit_dir=tmp_path,
    )
    assert snapshot.snapshot_id.startswith("nifty-live-")
    assert "market_data" in result.reports
    assert "decision" in result.reports
    assert pipeline.state.audits
