from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from enum import Enum


class EffectiveTradingMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(frozen=True)
class CostConfig:
    brokerage_per_order: float = 20.0
    tax_rate: float = 0.0005
    slippage_bps: float = 2.0


@dataclass(frozen=True)
class RiskConfig:
    max_quantity_per_trade: int = 200
    max_trades_per_day: int = 10
    max_open_positions: int = 3
    max_daily_loss: float = 5000.0
    max_loss_per_trade: float = 1500.0
    trading_start_time: time = time(9, 16)
    new_entry_cutoff_time: time = time(15, 10)
    mandatory_square_off_time: time = time(15, 20)
    cooldown_seconds: int = 120
    duplicate_signal_window_seconds: int = 180
    one_position_per_symbol: bool = True
    consecutive_loss_limit: int = 3
    emergency_kill_switch: bool = False
    max_market_data_staleness_seconds: int = 30
    max_api_failures_before_halt: int = 3


@dataclass(frozen=True)
class EngineConfig:
    requested_trading_mode: str = "PAPER"
    enable_live_orders: bool = False
    risk_ack_env: str = ""
    effective_trading_mode: EffectiveTradingMode = EffectiveTradingMode.PAPER
    mode_reason: str = "PAPER default"
    timezone: str = "Asia/Kolkata"
    poll_seconds: int = 15
    sqlite_path: str = "outputs/groww/paper_trades.db"
    csv_path: str = "outputs/groww/paper_trades.csv"
    logs_dir: str = "outputs/groww/logs"
    underlying_symbol: str = "NIFTY"
    instrument_type: str = "FNO"
    paper_startup_balance: float = 0.0
    groww_api_key_env: str = "GROWW_API_KEY"
    groww_api_secret_env: str = "GROWW_API_SECRET"
    groww_access_token_env: str = "GROWW_ACCESS_TOKEN"
    costs: CostConfig = CostConfig()
    risk: RiskConfig = RiskConfig()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_hhmm(name: str, default: time) -> time:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    parts = raw.split(":")
    if len(parts) != 2:
        return default
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return time(hour, minute)


def _resolve_mode(requested_mode: str, enable_live_orders: bool, risk_ack: str) -> tuple[EffectiveTradingMode, str]:
    normalized = requested_mode.strip().upper() or "PAPER"
    if normalized != "LIVE":
        return EffectiveTradingMode.PAPER, "TRADING_MODE is not LIVE"
    if not enable_live_orders:
        return EffectiveTradingMode.PAPER, "ENABLE_LIVE_ORDERS is not true"
    if risk_ack != "YES":
        return EffectiveTradingMode.PAPER, "I_UNDERSTAND_LIVE_ORDER_RISK is not YES"
    return EffectiveTradingMode.LIVE, "All live-trading confirmations passed"


def load_engine_config() -> EngineConfig:
    requested_mode = os.getenv("TRADING_MODE", "PAPER").strip().upper() or "PAPER"
    enable_live_orders = _env_bool("ENABLE_LIVE_ORDERS", False)
    risk_ack = os.getenv("I_UNDERSTAND_LIVE_ORDER_RISK", "")
    effective_mode, reason = _resolve_mode(requested_mode, enable_live_orders, risk_ack)

    risk = RiskConfig(
        max_quantity_per_trade=_env_int("MAX_QUANTITY_PER_TRADE", 200),
        max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", 10),
        max_open_positions=_env_int("MAX_OPEN_POSITIONS", 3),
        max_daily_loss=_env_float("MAX_DAILY_LOSS", 5000.0),
        max_loss_per_trade=_env_float("MAX_LOSS_PER_TRADE", 1500.0),
        trading_start_time=_parse_hhmm("TRADING_START_TIME", time(9, 16)),
        new_entry_cutoff_time=_parse_hhmm("NEW_ENTRY_CUTOFF_TIME", time(15, 10)),
        mandatory_square_off_time=_parse_hhmm("MANDATORY_SQUARE_OFF_TIME", time(15, 20)),
        cooldown_seconds=_env_int("COOLDOWN_SECONDS", 120),
        duplicate_signal_window_seconds=_env_int("DUPLICATE_SIGNAL_WINDOW_SECONDS", 180),
        one_position_per_symbol=_env_bool("ONE_POSITION_PER_SYMBOL", True),
        consecutive_loss_limit=_env_int("CONSECUTIVE_LOSS_LIMIT", 3),
        emergency_kill_switch=_env_bool("EMERGENCY_KILL_SWITCH", False),
        max_market_data_staleness_seconds=_env_int("MAX_MARKET_DATA_STALENESS_SECONDS", 30),
        max_api_failures_before_halt=_env_int("MAX_API_FAILURES_BEFORE_HALT", 3),
    )
    costs = CostConfig(
        brokerage_per_order=_env_float("BROKERAGE_PER_ORDER", 20.0),
        tax_rate=_env_float("TAX_RATE", 0.0005),
        slippage_bps=_env_float("SLIPPAGE_BPS", 2.0),
    )
    return EngineConfig(
        requested_trading_mode=requested_mode,
        enable_live_orders=enable_live_orders,
        risk_ack_env=risk_ack,
        effective_trading_mode=effective_mode,
        mode_reason=reason,
        timezone=os.getenv("TRADING_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata",
        poll_seconds=_env_int("POLL_SECONDS", 15),
        sqlite_path=os.getenv("PAPER_SQLITE_PATH", "outputs/groww/paper_trades.db").strip() or "outputs/groww/paper_trades.db",
        csv_path=os.getenv("PAPER_CSV_PATH", "outputs/groww/paper_trades.csv").strip() or "outputs/groww/paper_trades.csv",
        logs_dir=os.getenv("GROWW_LOGS_DIR", "outputs/groww/logs").strip() or "outputs/groww/logs",
        underlying_symbol=os.getenv("UNDERLYING_SYMBOL", "NIFTY").strip() or "NIFTY",
        instrument_type=os.getenv("GROWW_INSTRUMENT_TYPE", "FNO").strip().upper() or "FNO",
        paper_startup_balance=_env_float("PAPER_STARTUP_BALANCE", 0.0),
        groww_api_key_env="GROWW_API_KEY",
        groww_api_secret_env="GROWW_API_SECRET",
        groww_access_token_env="GROWW_ACCESS_TOKEN",
        costs=costs,
        risk=risk,
    )
