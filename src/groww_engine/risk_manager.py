from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import RiskConfig


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, risk: RiskConfig) -> None:
        self.risk = risk
        self._last_trade_time: datetime | None = None
        self._recent_signals: dict[str, datetime] = {}
        self._consecutive_losses = 0
        self._api_failures = 0

    def on_trade_closed(self, realized_pnl: float, closed_at: datetime) -> None:
        self._last_trade_time = closed_at
        if realized_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def on_api_failure(self) -> None:
        self._api_failures += 1

    def on_api_success(self) -> None:
        self._api_failures = 0

    def check_time_window(self, now: datetime) -> RiskDecision:
        current = now.time()
        if self.risk.emergency_kill_switch:
            return RiskDecision(False, "Emergency kill switch is enabled")
        if current < self.risk.trading_start_time:
            return RiskDecision(False, "Trading window not started")
        if current >= self.risk.mandatory_square_off_time:
            return RiskDecision(False, "Past mandatory square-off time")
        return RiskDecision(True, "Within trading window")

    def check_new_entry_window(self, now: datetime) -> RiskDecision:
        current = now.time()
        if current >= self.risk.new_entry_cutoff_time:
            return RiskDecision(False, "Past new-entry cutoff time")
        return RiskDecision(True, "Entry window open")

    def check_signal_freshness(self, signal_id: str, signal_ts: datetime, now: datetime) -> RiskDecision:
        # Duplicate signal protection in configured window.
        prev = self._recent_signals.get(signal_id)
        if prev is not None:
            elapsed = (now - prev).total_seconds()
            if elapsed <= self.risk.duplicate_signal_window_seconds:
                return RiskDecision(False, "Duplicate signal in protection window")
        self._recent_signals[signal_id] = signal_ts
        return RiskDecision(True, "Signal accepted")

    def check_market_data_staleness(self, tick_time: datetime, now: datetime) -> RiskDecision:
        stale_seconds = (now - tick_time).total_seconds()
        if stale_seconds > self.risk.max_market_data_staleness_seconds:
            return RiskDecision(False, f"Market data stale by {stale_seconds:.1f}s")
        return RiskDecision(True, "Market data fresh")

    def check_entry_limits(
        self,
        *,
        quantity: int,
        trades_today: int,
        open_positions: int,
        daily_realized_pnl: float,
        projected_max_loss: float,
        has_open_position_for_symbol: bool,
        now: datetime,
    ) -> RiskDecision:
        if quantity > self.risk.max_quantity_per_trade:
            return RiskDecision(False, "Quantity exceeds max per trade")
        if trades_today >= self.risk.max_trades_per_day:
            return RiskDecision(False, "Maximum trades per day reached")
        if open_positions >= self.risk.max_open_positions:
            return RiskDecision(False, "Maximum open positions reached")
        if self.risk.one_position_per_symbol and has_open_position_for_symbol:
            return RiskDecision(False, "One-position-per-symbol protection")
        if daily_realized_pnl <= -abs(self.risk.max_daily_loss):
            return RiskDecision(False, "Maximum daily loss breached")
        if abs(projected_max_loss) > abs(self.risk.max_loss_per_trade):
            return RiskDecision(False, "Projected max trade loss exceeds limit")
        if self._consecutive_losses >= self.risk.consecutive_loss_limit:
            return RiskDecision(False, "Consecutive-loss limit reached")
        if self._api_failures >= self.risk.max_api_failures_before_halt:
            return RiskDecision(False, "API failure protection halted trading")
        if self._last_trade_time is not None:
            elapsed = (now - self._last_trade_time).total_seconds()
            if elapsed < self.risk.cooldown_seconds:
                return RiskDecision(False, "Cooldown between trades is active")
        return RiskDecision(True, "Entry checks passed")

    def should_square_off(self, now: datetime) -> bool:
        return now.time() >= self.risk.mandatory_square_off_time
