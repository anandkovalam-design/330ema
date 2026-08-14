from __future__ import annotations

import importlib
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from groww_engine.config import EffectiveTradingMode, load_engine_config
from groww_engine.groww_client import GrowwClient
from groww_engine.live_order_client import LiveOrderClient
from groww_engine.market_data_client import MarketDataClient
from groww_engine.models import OrderRequest
from groww_engine.paper_broker import PaperBroker
from groww_engine.risk_manager import RiskManager
from groww_engine.storage import TradeStorage


IST = ZoneInfo("Asia/Kolkata")


class FakeMarketDataClient:
    def __init__(self) -> None:
        self.last_option_contract_symbol: str | None = None

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int):
        del interval, lookback_bars
        assert symbol == "NIFTY"
        market_day = date.today()
        start = datetime(market_day.year, market_day.month, market_day.day, 9, 15, tzinfo=IST)
        closes = [100.0] * 39 + [130.0]
        rows = []
        for i, close in enumerate(closes):
            ts = start + timedelta(minutes=5 * i)
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": close,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1000,
                }
            )
        return rows

    def get_ltp(self, instrument: str):
        if instrument.endswith("CE"):
            ltp = 121.25
        elif instrument.endswith("PE"):
            ltp = 118.90
        else:
            ltp = 120.0
        return {
            "ltp": ltp,
            "timestamp": datetime.now(IST).isoformat(),
        }

    def list_option_contracts(self, underlying: str, trade_date: date | None = None):
        assert underlying == "NIFTY"
        assert trade_date is not None
        return [
            {
                "symbol": "NIFTY26AUG100CE",
                "option_type": "CE",
                "strike": 100,
                "expiry": (trade_date + timedelta(days=7)).isoformat(),
            },
            {
                "symbol": "NIFTY26AUG100PE",
                "option_type": "PE",
                "strike": 100,
                "expiry": (trade_date + timedelta(days=7)).isoformat(),
            },
        ]

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int):
        del interval, lookback_bars
        self.last_option_contract_symbol = contract_symbol
        base = datetime(2026, 7, 31, 12, 25, tzinfo=IST)
        if contract_symbol.endswith("CE"):
            open_px = 118.0
            close_px = 121.0
        else:
            open_px = 116.0
            close_px = 118.0
        return [
            {
                "timestamp": base.isoformat(),
                "open": open_px,
                "high": close_px + 1.0,
                "low": open_px - 1.0,
                "close": close_px,
                "symbol": contract_symbol,
            }
        ]


class OrderCapableMarketDataClient(FakeMarketDataClient):
    def create_order(self, payload):
        del payload
        raise AssertionError("Must never be called")


class FullSdkWithOrderMethods:
    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int):
        del symbol, interval, lookback_bars
        return []

    def get_ltp(self, instrument: str):
        del instrument
        return {"ltp": 100.0, "timestamp": datetime.now(IST).isoformat()}

    def list_option_contracts(self, underlying: str, trade_date: date | None = None):
        del underlying, trade_date
        return []

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int):
        del contract_symbol, interval, lookback_bars
        return []

    def create_order(self, payload):
        del payload
        return {"order_id": "x"}


class OrderOnlySdk:
    def create_order(self, payload):
        del payload
        return {"order_id": "x"}

    def modify_order(self, order_id, payload):
        del order_id, payload
        return {"order_id": "x"}

    def cancel_order(self, order_id):
        del order_id
        return {"order_id": "x"}


def _clear_mode_env(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False)
    monkeypatch.delenv("I_UNDERSTAND_LIVE_ORDER_RISK", raising=False)


@pytest.fixture(autouse=True)
def _isolate_mode_env(monkeypatch):
    _clear_mode_env(monkeypatch)


def test_paper_mode_never_calls_groww_order_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_START_TIME", "00:00")
    monkeypatch.setenv("NEW_ENTRY_CUTOFF_TIME", "23:59")
    monkeypatch.setenv("MANDATORY_SQUARE_OFF_TIME", "23:59")

    cfg = load_engine_config()
    client: MarketDataClient = FakeMarketDataClient()
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    from groww_engine.engine import TradingEngine

    engine = TradingEngine(cfg, client, storage)

    status = engine.run_cycle()
    assert cfg.effective_trading_mode == EffectiveTradingMode.PAPER
    assert status.last_message == "PAPER entry recorded"

    records = storage.list_trade_records()
    assert len(records) == 1
    assert str(records.iloc[0]["mode"]) == "PAPER"
    assert str(records.iloc[0]["status"]) == "OPEN"
    assert str(records.iloc[0]["option_type"]) == "CE"
    assert str(records.iloc[0]["instrument"]) == "NIFTY26AUG100CE"
    assert str(records.iloc[0]["instrument"]).endswith("CE")
    assert str(records.iloc[0]["instrument"]) == str(client.last_option_contract_symbol)
    assert "NIFTY_FNO" not in str(records.iloc[0]["instrument"])
    assert int(records.iloc[0]["quantity"]) == min(50, cfg.risk.max_quantity_per_trade)

    signal_id = str(records.iloc[0]["signal_id"])
    assert signal_id.startswith("NIFTY:")

    csv_frame = pd.read_csv(tmp_path / "paper.csv")
    assert len(csv_frame) == 1
    assert str(csv_frame.iloc[0]["mode"]) == "PAPER"
    assert str(csv_frame.iloc[0]["instrument"]) == "NIFTY26AUG100CE"
    assert str(csv_frame.iloc[0]["option_type"]) == "CE"
    assert int(csv_frame.iloc[0]["quantity"]) == min(50, cfg.risk.max_quantity_per_trade)
    assert str(csv_frame.iloc[0]["signal_id"]) == signal_id


def test_paper_engine_rejects_order_capable_market_data_client(tmp_path):
    cfg = load_engine_config()
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    from groww_engine.engine import TradingEngine

    with pytest.raises(TypeError, match="order-capable"):
        TradingEngine(cfg, OrderCapableMarketDataClient(), storage)


def test_paper_engine_rejects_full_sdk_with_order_methods(tmp_path):
    cfg = load_engine_config()
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    from groww_engine.engine import TradingEngine

    with pytest.raises(TypeError, match="order-capable"):
        TradingEngine(cfg, FullSdkWithOrderMethods(), storage)


def test_paper_engine_rejects_groww_client_wrapper(tmp_path):
    cfg = load_engine_config()
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    from groww_engine.engine import TradingEngine

    wrapped = GrowwClient(FakeMarketDataClient())
    with pytest.raises(TypeError, match="pure MarketDataClient"):
        TradingEngine(cfg, wrapped, storage)


def test_paper_engine_rejects_live_order_client_object(tmp_path):
    cfg = load_engine_config()
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    from groww_engine.engine import TradingEngine

    live_client = LiveOrderClient(OrderOnlySdk())
    with pytest.raises(TypeError, match="order-capable"):
        TradingEngine(cfg, live_client, storage)


def test_missing_configuration_defaults_to_paper(monkeypatch):
    cfg = load_engine_config()
    assert cfg.effective_trading_mode == EffectiveTradingMode.PAPER


@pytest.mark.parametrize(
    ("enable_live_orders", "risk_ack", "expected_mode"),
    [
        (False, "", EffectiveTradingMode.PAPER),
        (False, "NO", EffectiveTradingMode.PAPER),
        (False, "YES", EffectiveTradingMode.PAPER),
        (False, "YES ", EffectiveTradingMode.PAPER),
        (False, "yes", EffectiveTradingMode.PAPER),
        (False, "Yes", EffectiveTradingMode.PAPER),
        (False, "true", EffectiveTradingMode.PAPER),
        (False, "1", EffectiveTradingMode.PAPER),
        (True, "", EffectiveTradingMode.PAPER),
        (True, "NO", EffectiveTradingMode.PAPER),
        (True, "yes", EffectiveTradingMode.PAPER),
        (True, "Yes", EffectiveTradingMode.PAPER),
        (True, "YES ", EffectiveTradingMode.PAPER),
        (True, "true", EffectiveTradingMode.PAPER),
        (True, "1", EffectiveTradingMode.PAPER),
        (True, "YES", EffectiveTradingMode.LIVE),
    ],
)
def test_live_mode_confirmation_truth_table(monkeypatch, enable_live_orders, risk_ack, expected_mode):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("ENABLE_LIVE_ORDERS", "true" if enable_live_orders else "false")
    if risk_ack:
        monkeypatch.setenv("I_UNDERSTAND_LIVE_ORDER_RISK", risk_ack)
    else:
        monkeypatch.delenv("I_UNDERSTAND_LIVE_ORDER_RISK", raising=False)

    cfg = load_engine_config()
    assert cfg.effective_trading_mode == expected_mode


def test_non_live_request_always_resolves_to_paper(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ENABLE_LIVE_ORDERS", "true")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_ORDER_RISK", "YES")

    cfg = load_engine_config()
    assert cfg.effective_trading_mode == EffectiveTradingMode.PAPER


def test_daily_loss_limit_blocks_new_trades(monkeypatch):
    monkeypatch.setenv("TRADING_TIMEZONE", "Asia/Kolkata")
    cfg = load_engine_config()
    risk = RiskManager(cfg.risk)
    now = datetime.now(IST)
    decision = risk.check_entry_limits(
        quantity=1,
        trades_today=0,
        open_positions=0,
        daily_realized_pnl=-abs(cfg.risk.max_daily_loss),
        projected_max_loss=10.0,
        has_open_position_for_symbol=False,
        now=now,
    )
    assert not decision.allowed


def test_max_trade_limit_blocks_new_trades():
    cfg = load_engine_config()
    risk = RiskManager(cfg.risk)
    now = datetime.now(IST)
    decision = risk.check_entry_limits(
        quantity=1,
        trades_today=cfg.risk.max_trades_per_day,
        open_positions=0,
        daily_realized_pnl=0.0,
        projected_max_loss=10.0,
        has_open_position_for_symbol=False,
        now=now,
    )
    assert not decision.allowed


def test_duplicate_signals_are_blocked_across_restart_and_persisted(tmp_path):
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    cfg = load_engine_config()
    broker_a = PaperBroker(storage, cfg.costs)
    req = OrderRequest(
        symbol="NIFTY",
        instrument="NIFTY_FNO",
        side="BUY",
        quantity=10,
        requested_price=100.0,
        stop_loss=90.0,
        target=120.0,
        signal_id="sig-1",
        option_type="CE",
    )
    first = broker_a.submit_entry(req, market_price=100.0)

    broker_b = PaperBroker(storage, cfg.costs)
    second = broker_b.submit_entry(req, market_price=101.0)

    assert first.accepted
    assert not second.accepted
    assert second.reason == "Duplicate signal blocked"
    assert len(broker_b.open_positions) == 1

    db_frame = storage.list_trade_records()
    assert len(db_frame) == 1
    assert str(db_frame.iloc[0]["mode"]) == "PAPER"
    assert str(db_frame.iloc[0]["signal_id"]) == "sig-1"

    csv_frame = pd.read_csv(tmp_path / "paper.csv")
    assert len(csv_frame) == 1
    assert str(csv_frame.iloc[0]["mode"]) == "PAPER"
    assert str(csv_frame.iloc[0]["signal_id"]) == "sig-1"


def test_stale_market_data_prevents_trading():
    cfg = load_engine_config()
    risk = RiskManager(cfg.risk)
    now = datetime.now(IST)
    stale_tick = now - timedelta(seconds=cfg.risk.max_market_data_staleness_seconds + 5)
    decision = risk.check_market_data_staleness(stale_tick, now)
    assert not decision.allowed


def test_restart_restores_paper_positions(tmp_path):
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    cfg = load_engine_config()
    broker_a = PaperBroker(storage, cfg.costs)
    req = OrderRequest(
        symbol="NIFTY",
        instrument="NIFTY_FNO",
        side="BUY",
        quantity=10,
        requested_price=100.0,
        stop_loss=90.0,
        target=120.0,
        signal_id="sig-restore",
        option_type="CE",
    )
    broker_a.submit_entry(req, market_price=100.0)

    broker_b = PaperBroker(storage, cfg.costs)
    assert len(broker_b.open_positions) == 1
    assert broker_b.open_positions[0].signal_id == "sig-restore"


def test_market_hours_rules_use_ist_timezone():
    cfg = load_engine_config()
    risk = RiskManager(cfg.risk)

    pre_open = datetime(2026, 7, 31, 9, 10, tzinfo=IST)
    in_window = datetime(2026, 7, 31, 10, 0, tzinfo=IST)
    post_squareoff = datetime(2026, 7, 31, 15, 25, tzinfo=IST)
    post_entry_cutoff = datetime(2026, 7, 31, 15, 12, tzinfo=IST)

    assert not risk.check_time_window(pre_open).allowed
    assert risk.check_time_window(in_window).allowed
    assert not risk.check_time_window(post_squareoff).allowed
    assert not risk.check_new_entry_window(post_entry_cutoff).allowed


def test_paper_engine_module_does_not_import_live_clients(tmp_path, monkeypatch):
    sys.modules.pop("groww_engine.live_broker", None)
    sys.modules.pop("groww_engine.live_order_client", None)
    importlib.invalidate_caches()

    engine_module = importlib.import_module("groww_engine.engine")
    assert "groww_engine.live_broker" not in sys.modules
    assert "groww_engine.live_order_client" not in sys.modules

    monkeypatch.setenv("TRADING_START_TIME", "00:00")
    monkeypatch.setenv("NEW_ENTRY_CUTOFF_TIME", "23:59")
    monkeypatch.setenv("MANDATORY_SQUARE_OFF_TIME", "23:59")
    cfg = load_engine_config()
    storage = TradeStorage(tmp_path / "paper.db", tmp_path / "paper.csv")
    engine = engine_module.TradingEngine(cfg, FakeMarketDataClient(), storage)
    engine.run_cycle()

    assert "groww_engine.live_broker" not in sys.modules
    assert "groww_engine.live_order_client" not in sys.modules


def test_auth_accepts_access_token_only(monkeypatch):
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    monkeypatch.setenv("GROWW_ACCESS_TOKEN", "token_only")
    GrowwClient.assert_auth_configured()


def test_auth_accepts_api_key_and_secret_only(monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "key_only")
    monkeypatch.setenv("GROWW_API_SECRET", "secret_only")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    GrowwClient.assert_auth_configured()


def test_auth_accepts_full_triplet(monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "key")
    monkeypatch.setenv("GROWW_API_SECRET", "secret")
    monkeypatch.setenv("GROWW_ACCESS_TOKEN", "token")
    GrowwClient.assert_auth_configured()


def test_auth_rejects_partial_pair_without_leaking_values(monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "my_real_key")
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)

    try:
        GrowwClient.assert_auth_configured()
    except ValueError as exc:
        message = str(exc)
        assert "my_real_key" not in message
        assert "GROWW_API_KEY" in message
        assert "GROWW_API_SECRET" in message
        return

    raise AssertionError("Expected auth validation error for partial key/secret configuration.")


def test_auth_rejects_empty_configuration_without_leaking_values(monkeypatch):
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)

    with pytest.raises(ValueError) as exc_info:
        GrowwClient.assert_auth_configured()

    message = str(exc_info.value)
    assert "GROWW_ACCESS_TOKEN" in message
    assert "GROWW_API_KEY" in message
    assert "GROWW_API_SECRET" in message
