from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from quantlab.strategy_ema3_30_staged import (
    EXIT_MODE_FIXED_TARGET,
    INTRABAR_POLICY_STOP_FIRST,
    StrategyConfig,
    compute_signals,
    select_groww_fno_contract,
)

from .config import CostConfig, RiskConfig
from .models import OrderRequest
from .paper_broker import PaperBroker
from .risk_manager import RiskManager
from .storage import TradeStorage


logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class GrowwRealtimeMarketDataSdkLike(Protocol):
    def authenticate(self, api_key: str, api_secret: str, access_token: str) -> None: ...

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...

    def list_option_contracts(self, underlying: str, trade_date: date) -> list[dict[str, Any]]: ...

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...

    def get_ltp(self, instrument: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PaperRunnerConfig:
    mode: str = "PAPER"
    timezone: str = "Asia/Kolkata"
    underlying_symbol: str = "NIFTY"
    interval_minutes: int = 5
    lookback_bars: int = 240
    quantity: int = 50
    stop_loss_pct: float = 0.10
    entry_cutoff_time: str = "15:10"
    forced_square_off_time: str = "15:20"
    poll_seconds: int = 15
    max_daily_loss: float = 5000.0
    max_trades_per_day: int = 10
    one_open_position_only: bool = True
    sqlite_path: str = "outputs/groww/paper_trades.db"
    csv_path: str = "outputs/groww/paper_trades.csv"
    strategy: StrategyConfig = StrategyConfig()
    costs: CostConfig = CostConfig()


@dataclass
class OpenTradeState:
    position_id: str
    contract_symbol: str
    signal_id: str
    signal_close_time: str
    entry_open_time: str
    entry_time: str
    entry_price: float
    side: str
    active_stop: float
    breakeven_armed: bool
    trailing_active: bool
    next_trail_trigger_price: float
    max_high_since_entry: float
    last_processed_close_ts: str

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @staticmethod
    def from_json(raw: str) -> "OpenTradeState":
        payload = json.loads(raw)
        return OpenTradeState(**payload)


class PaperRealtimeRunner:
    """Real-time Groww cloud runner in strict PAPER mode.

    Limitation: with candle OHLC data, if stop and target both hit within one
    candle, execution order is unknown. This runner defaults to conservative
    stop-first handling.
    """

    def __init__(
        self,
        *,
        sdk: GrowwRealtimeMarketDataSdkLike,
        config: PaperRunnerConfig,
        risk: RiskConfig,
    ) -> None:
        self.sdk = sdk
        self.config = config
        self.tz = ZoneInfo(config.timezone)
        self.storage = TradeStorage(config.sqlite_path, config.csv_path)
        self.broker = PaperBroker(self.storage, config.costs)
        self.risk_manager = RiskManager(risk)

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        hour, minute = value.split(":", 1)
        return int(hour), int(minute)

    def _entry_cutoff(self, day: date) -> datetime:
        h, m = self._parse_hhmm(self.config.entry_cutoff_time)
        return datetime(day.year, day.month, day.day, h, m, tzinfo=self.tz)

    def _square_off_cutoff(self, day: date) -> datetime:
        h, m = self._parse_hhmm(self.config.forced_square_off_time)
        return datetime(day.year, day.month, day.day, h, m, tzinfo=self.tz)

    @staticmethod
    def _to_float(value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _normalized_completed_candles(self, rows: list[dict[str, Any]], now: datetime) -> pd.DataFrame:
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if frame.empty:
            return frame
        first_ts = pd.Timestamp(frame.iloc[0]["timestamp"])
        if first_ts.tzinfo is None:
            frame["timestamp"] = frame["timestamp"].dt.tz_localize(self.tz)
        else:
            frame["timestamp"] = frame["timestamp"].dt.tz_convert(self.tz)

        min_complete_ts = now - timedelta(minutes=self.config.interval_minutes)
        frame = frame[frame["timestamp"] <= min_complete_ts].reset_index(drop=True)
        return frame

    def _load_open_state(self) -> OpenTradeState | None:
        raw = self.storage.get_state("runner_open_trade")
        if not raw:
            return None
        try:
            return OpenTradeState.from_json(raw)
        except Exception:
            logger.exception("open_state_corrupt")
            return None

    def _save_open_state(self, state: OpenTradeState | None) -> None:
        if state is None:
            self.storage.set_state("runner_open_trade", "")
            return
        self.storage.set_state("runner_open_trade", state.to_json())

    def _daily_realized_pnl(self, day: date) -> float:
        frame = self.storage.list_trade_records()
        if frame.empty:
            return 0.0
        day_text = day.isoformat()
        frame = frame[frame["timestamp"].astype(str).str.startswith(day_text)]
        if frame.empty:
            return 0.0
        return float(frame["realized_pnl"].fillna(0.0).sum())

    def _trades_today(self, day: date) -> int:
        frame = self.storage.list_trade_records()
        if frame.empty:
            return 0
        day_text = day.isoformat()
        frame = frame[frame["timestamp"].astype(str).str.startswith(day_text)]
        if frame.empty:
            return 0
        return int((frame["status"].astype(str) == "OPEN").sum())

    def authenticate(self) -> None:
        api_key = os.getenv("GROWW_API_KEY", "").strip()
        api_secret = os.getenv("GROWW_API_SECRET", "").strip()
        access_token = os.getenv("GROWW_ACCESS_TOKEN", "").strip()
        missing: list[str] = []
        if not api_key:
            missing.append("GROWW_API_KEY")
        if not api_secret:
            missing.append("GROWW_API_SECRET")
        if not access_token:
            missing.append("GROWW_ACCESS_TOKEN")
        if missing:
            raise ValueError("Missing Groww credentials: " + ", ".join(missing))
        self.sdk.authenticate(api_key, api_secret, access_token)

    def _close_open_position(self, state: OpenTradeState, price: float, reason: str) -> None:
        result = self.broker.close_position(state.position_id, price, reason)
        if result.accepted:
            logger.info("paper_exit contract=%s reason=%s fill=%.4f pnl=%.4f", state.contract_symbol, reason, price, result.trade_record.realized_pnl if result.trade_record else 0.0)
            self._save_open_state(None)
        else:
            logger.warning("paper_exit_rejected reason=%s", result.reason)

    def _manage_open_trade(self, state: OpenTradeState, option_frame: pd.DataFrame, now: datetime) -> OpenTradeState | None:
        if option_frame.empty:
            return state

        last_processed = pd.Timestamp(state.last_processed_close_ts)
        recent = option_frame[option_frame["timestamp"] > last_processed]
        if recent.empty:
            return state

        target_price = None
        if self.config.strategy.exit_mode == EXIT_MODE_FIXED_TARGET and self.config.strategy.target_pct is not None:
            target_price = state.entry_price * (1.0 + float(self.config.strategy.target_pct))

        for _, row in recent.iterrows():
            bar_ts = pd.Timestamp(row["timestamp"])
            high_px = self._to_float(row.get("high"), 0.0)
            low_px = self._to_float(row.get("low"), 0.0)
            close_px = self._to_float(row.get("close"), 0.0)

            state.max_high_since_entry = max(state.max_high_since_entry, high_px)
            touched_stop = low_px <= state.active_stop
            touched_target = target_price is not None and high_px >= target_price
            if touched_stop and touched_target and self.config.strategy.intrabar_execution_policy == INTRABAR_POLICY_STOP_FIRST:
                touched_target = False

            if touched_stop:
                self._close_open_position(state, state.active_stop, "STOP_LOSS")
                return None
            if touched_target and target_price is not None:
                self._close_open_position(state, target_price, "TARGET")
                return None

            breakeven_trigger = state.entry_price * (1.0 + self.config.strategy.sl_to_cost_profit_pct)
            trailing_trigger = state.entry_price * (1.0 + self.config.strategy.trail_after_profit_pct)

            if (not state.breakeven_armed) and high_px >= breakeven_trigger:
                state.breakeven_armed = True
                state.active_stop = max(state.active_stop, state.entry_price)

            if (not state.trailing_active) and high_px >= trailing_trigger:
                state.trailing_active = True

            if state.trailing_active:
                dynamic_stop = state.entry_price + (state.max_high_since_entry - state.entry_price) * (1.0 - self.config.strategy.mfe_giveback_pct)
                state.active_stop = max(state.active_stop, dynamic_stop)

                while high_px >= state.next_trail_trigger_price:
                    state.active_stop += self.config.strategy.trail_step_points
                    state.next_trail_trigger_price += self.config.strategy.trail_trigger_points

            state.last_processed_close_ts = bar_ts.isoformat()

            if now >= self._square_off_cutoff(now.date()):
                self._close_open_position(state, close_px, "FORCED_SQUARE_OFF")
                return None

        return state

    def run_cycle(self) -> None:
        now = self._now()
        day = now.date()
        if self.config.mode != "PAPER":
            raise RuntimeError("Runner is locked to PAPER mode")

        underlying_rows = self.sdk.get_historical_ohlc(self.config.underlying_symbol, "5m", self.config.lookback_bars)
        underlying_frame = self._normalized_completed_candles(underlying_rows, now)
        if underlying_frame.empty or len(underlying_frame) < 40:
            logger.info("insufficient_underlying_candles")
            return

        open_state = self._load_open_state()
        if open_state is not None:
            option_rows = self.sdk.get_option_ohlc(open_state.contract_symbol, "5m", self.config.lookback_bars)
            option_frame = self._normalized_completed_candles(option_rows, now)
            open_state = self._manage_open_trade(open_state, option_frame, now)
            self._save_open_state(open_state)
            if open_state is not None:
                return

        if now >= self._entry_cutoff(day):
            logger.info("entry_cutoff_reached")
            return
        if now >= self._square_off_cutoff(day):
            logger.info("squareoff_window_reached")
            return
        if self.config.one_open_position_only and len(self.broker.open_positions) > 0:
            logger.info("open_position_exists")
            return
        if self._trades_today(day) >= self.config.max_trades_per_day:
            logger.info("max_trades_reached")
            return
        if self._daily_realized_pnl(day) <= -abs(self.config.max_daily_loss):
            logger.warning("max_daily_loss_reached")
            return

        signals = compute_signals(underlying_frame, self.config.strategy)
        if len(signals) < 2:
            return

        signal_row = signals.iloc[-2]
        entry_row = signals.iloc[-1]
        side: str | None = None
        option_type = ""
        if bool(signal_row.get("cross_up", False)):
            side = "BUY"
            option_type = "CALL"
        elif bool(signal_row.get("cross_down", False)):
            side = "BUY"
            option_type = "PUT"
        if side is None:
            logger.info("no_signal")
            return

        signal_ts = pd.Timestamp(signal_row["timestamp"])
        entry_ts = pd.Timestamp(entry_row["timestamp"])
        signal_close = signal_ts
        entry_open = entry_ts - pd.Timedelta(minutes=self.config.interval_minutes)

        option_chain = self.sdk.list_option_contracts(self.config.underlying_symbol, day)
        spot_price = self._to_float(entry_row.get("open"), 0.0)
        selected = select_groww_fno_contract(option_type, spot_price=spot_price, trade_date=day, option_chain=option_chain)
        if selected is None:
            logger.warning("no_contract_selected")
            return

        contract_symbol = str(selected.get("symbol", selected.get("tradingsymbol", ""))).strip()
        if not contract_symbol:
            logger.warning("selected_contract_missing_symbol")
            return

        option_rows = self.sdk.get_option_ohlc(contract_symbol, "5m", self.config.lookback_bars)
        option_frame = self._normalized_completed_candles(option_rows, now)
        if option_frame.empty:
            logger.warning("empty_option_candles contract=%s", contract_symbol)
            return

        option_frame = option_frame[option_frame["timestamp"] == entry_ts]
        if option_frame.empty:
            logger.info("entry_candle_not_ready contract=%s", contract_symbol)
            return

        entry_price_raw = self._to_float(option_frame.iloc[0].get("open"), 0.0)
        if entry_price_raw <= 0:
            logger.warning("invalid_entry_price contract=%s", contract_symbol)
            return

        qty = int(self.config.quantity)
        stop_loss = entry_price_raw * (1.0 - self.config.stop_loss_pct)
        target = None
        if self.config.strategy.exit_mode == EXIT_MODE_FIXED_TARGET and self.config.strategy.target_pct is not None:
            target = entry_price_raw * (1.0 + float(self.config.strategy.target_pct))

        signal_id = f"{self.config.underlying_symbol}:{contract_symbol}:{signal_close.isoformat()}"
        order = OrderRequest(
            symbol=self.config.underlying_symbol,
            instrument=contract_symbol,
            side=side,
            quantity=qty,
            requested_price=entry_price_raw,
            stop_loss=stop_loss,
            target=target,
            signal_id=signal_id,
            expiry=str(selected.get("expiry", "")) or None,
            strike=float(selected.get("strike", 0) or 0) or None,
            option_type="CE" if option_type == "CALL" else "PE",
        )

        result = self.broker.submit_entry(order, market_price=entry_price_raw)
        if not result.accepted or result.trade_record is None:
            logger.warning("paper_entry_rejected reason=%s", result.reason)
            return

        filled = float(result.trade_record.fill_price)
        state = OpenTradeState(
            position_id=next(pos.position_id for pos in self.broker.open_positions if pos.signal_id == signal_id),
            contract_symbol=contract_symbol,
            signal_id=signal_id,
            signal_close_time=signal_close.isoformat(),
            entry_open_time=entry_open.isoformat(),
            entry_time=entry_ts.isoformat(),
            entry_price=filled,
            side=option_type,
            active_stop=filled * (1.0 - self.config.stop_loss_pct),
            breakeven_armed=False,
            trailing_active=False,
            next_trail_trigger_price=filled * (1.0 + self.config.strategy.trail_after_profit_pct) + self.config.strategy.trail_trigger_points,
            max_high_since_entry=filled,
            last_processed_close_ts=entry_ts.isoformat(),
        )
        self._save_open_state(state)
        logger.info(
            "paper_entry contract=%s signal_close=%s entry_open=%s entry_price=%.4f mode=%s",
            contract_symbol,
            signal_close.isoformat(),
            entry_open.isoformat(),
            filled,
            self.config.mode,
        )

    def run_forever(self, max_cycles: int = 0) -> None:
        cycle = 0
        while True:
            cycle += 1
            try:
                self.run_cycle()
            except Exception:
                logger.exception("runner_cycle_failed")
            if max_cycles > 0 and cycle >= max_cycles:
                break
            time.sleep(max(1, int(self.config.poll_seconds)))
