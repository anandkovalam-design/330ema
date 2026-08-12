from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pandas as pd
from quantlab.strategy_ema3_30_staged import select_groww_fno_contract

from .config import EngineConfig, EffectiveTradingMode
from .market_data_client import MarketDataClient
from .models import LiveOrderContext, OrderRequest
from .paper_broker import PaperBroker
from .risk_manager import RiskManager
from .storage import TradeStorage
from .strategy import build_exit_reason, build_signal_from_ohlc

if TYPE_CHECKING:
    from .groww_client import GrowwClient
    from .live_order_client import LiveOrderClient
    from .live_broker import LiveBroker


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineStatus:
    mode: str
    reason: str
    open_positions: int
    last_message: str


class TradingEngine:
    def __init__(
        self,
        config: EngineConfig,
        market_data_client: MarketDataClient,
        storage: TradeStorage,
        live_order_client: "LiveOrderClient | None" = None,
    ) -> None:
        self._validate_market_data_client(config, market_data_client)
        self.config = config
        self.client = market_data_client
        self.storage = storage
        self.risk = RiskManager(config.risk)
        self.paper_broker = PaperBroker(storage, config.costs)
        self.live_broker: "LiveBroker | None" = None
        if self.config.effective_trading_mode is EffectiveTradingMode.LIVE:
            if live_order_client is None:
                raise ValueError("LiveOrderClient is required when effective trading mode is LIVE")
            from .live_broker import LiveBroker

            self.live_broker = LiveBroker(config, live_order_client)
        self.tz = ZoneInfo(config.timezone)

    @staticmethod
    def _validate_market_data_client(config: EngineConfig, market_data_client: object) -> None:
        if config.effective_trading_mode is EffectiveTradingMode.PAPER:
            if market_data_client.__class__.__name__ == "GrowwClient":
                raise TypeError("PAPER mode requires a pure MarketDataClient, not GrowwClient")

            blocked = [
                method
                for method in ("create_order", "modify_order", "cancel_order")
                if callable(getattr(market_data_client, method, None))
            ]
            if blocked:
                raise TypeError(
                    "PAPER mode rejected order-capable market-data client exposing: " + ", ".join(blocked)
                )

        required_methods = (
            "get_historical_ohlc",
            "get_ltp",
            "list_option_contracts",
            "get_option_ohlc",
        )
        missing = [name for name in required_methods if not callable(getattr(market_data_client, name, None))]
        if missing:
            raise TypeError("MarketDataClient is missing required methods: " + ", ".join(missing))

    @staticmethod
    def _parse_ltp(payload: dict[str, Any]) -> tuple[float, datetime]:
        ltp = float(payload.get("ltp", 0.0) or 0.0)
        ts = pd.to_datetime(payload.get("timestamp"), errors="coerce")
        if ltp <= 0 or pd.isna(ts):
            raise ValueError("Invalid LTP payload")
        return ltp, ts.to_pydatetime()

    @staticmethod
    def _normalized_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return frame

    @staticmethod
    def _assert_contract_candles(contract_symbol: str, frame: pd.DataFrame) -> None:
        if frame.empty:
            raise ValueError("No option candles returned")
        for column in ("contract_symbol", "symbol", "tradingsymbol"):
            if column in frame.columns:
                symbols = set(frame[column].dropna().astype(str))
                if symbols and symbols != {contract_symbol}:
                    raise ValueError("Option candle payload does not match selected contract symbol")

    def status(self, last_message: str = "") -> EngineStatus:
        return EngineStatus(
            mode=self.config.effective_trading_mode.value,
            reason=self.config.mode_reason,
            open_positions=len(self.paper_broker.open_positions),
            last_message=last_message,
        )

    def _daily_realized_pnl(self) -> float:
        frame = self.storage.list_trade_records()
        if frame.empty:
            return 0.0
        frame["timestamp"] = frame["timestamp"].astype(str)
        today = datetime.now(self.tz).date().isoformat()
        day = frame[frame["timestamp"].str.startswith(today)]
        if day.empty:
            return 0.0
        return float(day.get("realized_pnl", 0.0).fillna(0.0).sum())

    def _trades_today(self) -> int:
        frame = self.storage.list_trade_records()
        if frame.empty:
            return 0
        today = datetime.now(self.tz).date().isoformat()
        return int(frame[frame["timestamp"].astype(str).str.startswith(today)].shape[0])

    def run_cycle(self) -> EngineStatus:
        now = datetime.now(self.tz)
        time_gate = self.risk.check_time_window(now)
        if not time_gate.allowed:
            return self.status(time_gate.reason)

        if self.risk.should_square_off(now):
            closed = 0
            for position in list(self.paper_broker.open_positions):
                try:
                    ltp_payload = self.client.get_ltp(position.instrument)
                    ltp, tick_time = self._parse_ltp(ltp_payload)
                    staleness = self.risk.check_market_data_staleness(tick_time, now)
                    if not staleness.allowed:
                        continue
                    result = self.paper_broker.close_position(position.position_id, ltp, "MANDATORY_SQUARE_OFF")
                    if result.accepted and result.trade_record is not None:
                        self.risk.on_trade_closed(result.trade_record.realized_pnl, now)
                        closed += 1
                except Exception:
                    self.risk.on_api_failure()
                    logger.exception("square_off_failed")
            return self.status(f"Square-off processed, closed={closed}")

        entry_gate = self.risk.check_new_entry_window(now)
        if not entry_gate.allowed:
            return self.status(entry_gate.reason)

        try:
            bars_rows = self.client.get_historical_ohlc(
                symbol=self.config.underlying_symbol,
                interval="5m",
                lookback_bars=150,
            )
            bars = self._normalized_frame(bars_rows)
            if bars.empty:
                raise ValueError("No market data returned")
            self.risk.on_api_success()
        except Exception as exc:
            self.risk.on_api_failure()
            return self.status(f"Market data failure: {exc}")

        spot_for_selection = float(bars.iloc[-1].get("close", 0.0) or 0.0)
        signal = build_signal_from_ohlc(
            bars,
            symbol=self.config.underlying_symbol,
            instrument="",
        )
        if signal is None:
            return self.status("No strategy signal")

        dedupe = self.risk.check_signal_freshness(signal.signal_id, signal.timestamp, now)
        if not dedupe.allowed:
            return self.status(dedupe.reason)

        if self.paper_broker.has_signal(signal.signal_id):
            return self.status("Duplicate signal already processed")

        try:
            option_chain = self.client.list_option_contracts(self.config.underlying_symbol, now.date())
            side = "CALL" if str(signal.option_type or "").upper() == "CE" else "PUT"
            selected = select_groww_fno_contract(
                side,
                spot_price=spot_for_selection,
                trade_date=now.date(),
                option_chain=option_chain,
            )
            if selected is None:
                return self.status("No eligible option contract found")

            contract_symbol = str(selected.get("symbol", selected.get("tradingsymbol", ""))).strip()
            if not contract_symbol:
                return self.status("Selected option contract missing symbol")

            option_rows = self.client.get_option_ohlc(contract_symbol, interval="5m", lookback_bars=5)
            option_frame = self._normalized_frame(option_rows)
            self._assert_contract_candles(contract_symbol, option_frame)
            option_price_from_candle = float(option_frame.iloc[-1].get("close", 0.0) or 0.0)

            ltp_payload = self.client.get_ltp(contract_symbol)
            ltp, tick_time = self._parse_ltp(ltp_payload)
            if ltp <= 0:
                ltp = option_price_from_candle

            self.risk.on_api_success()
        except Exception as exc:
            self.risk.on_api_failure()
            return self.status(f"Option data failure: {exc}")

        freshness = self.risk.check_market_data_staleness(tick_time, now)
        if not freshness.allowed:
            return self.status(freshness.reason)

        stop_loss = max(0.0, ltp * 0.9)
        target = ltp * 1.2
        qty = min(50, self.config.risk.max_quantity_per_trade)
        max_loss = (ltp - stop_loss) * qty
        has_open_symbol = any(pos.symbol == signal.symbol for pos in self.paper_broker.open_positions)

        risk_gate = self.risk.check_entry_limits(
            quantity=qty,
            trades_today=self._trades_today(),
            open_positions=len(self.paper_broker.open_positions),
            daily_realized_pnl=self._daily_realized_pnl(),
            projected_max_loss=max_loss,
            has_open_position_for_symbol=has_open_symbol,
            now=now,
        )
        if not risk_gate.allowed:
            return self.status(risk_gate.reason)

        order = OrderRequest(
            symbol=signal.symbol,
            instrument=contract_symbol,
            side=signal.side,
            quantity=qty,
            requested_price=ltp,
            stop_loss=stop_loss,
            target=target,
            signal_id=signal.signal_id,
            expiry=str(selected.get("expiry", "")) or None,
            strike=float(selected.get("strike", 0.0) or 0.0) or signal.strike,
            option_type=str(selected.get("option_type", selected.get("instrument_type", signal.option_type or ""))).upper() or signal.option_type,
        )

        if self.config.effective_trading_mode is EffectiveTradingMode.PAPER:
            result = self.paper_broker.submit_entry(order, ltp)
            if not result.accepted:
                return self.status(result.reason)
            return self.status("PAPER entry recorded")

        # LIVE path with mandatory validations before real order call.
        if self.live_broker is None:
            raise RuntimeError("LIVE mode requires a configured LiveBroker")

        context = LiveOrderContext(
            authenticated=True,
            available_margin=1.0,
            symbol_valid=True,
            quantity_valid=(qty <= self.config.risk.max_quantity_per_trade),
            has_open_position=has_open_symbol,
            daily_loss_ok=(self._daily_realized_pnl() > -abs(self.config.risk.max_daily_loss)),
            trades_remaining=(self._trades_today() < self.config.risk.max_trades_per_day),
            within_trading_time=True,
            market_data_fresh=True,
        )
        self.live_broker.place_order(order, context)
        return self.status("LIVE order submitted")

    def shutdown(self) -> EngineStatus:
        now = datetime.now(self.tz)
        closed = 0
        for position in list(self.paper_broker.open_positions):
            try:
                ltp_payload = self.client.get_ltp(position.instrument)
                ltp, tick_time = self._parse_ltp(ltp_payload)
                freshness = self.risk.check_market_data_staleness(tick_time, now)
                if not freshness.allowed:
                    continue
                result = self.paper_broker.close_position(position.position_id, ltp, build_exit_reason(now))
                if result.accepted and result.trade_record is not None:
                    self.risk.on_trade_closed(result.trade_record.realized_pnl, now)
                    closed += 1
            except Exception:
                self.risk.on_api_failure()
                logger.exception("shutdown_close_failed")
        return self.status(f"Shutdown completed, closed={closed}")
