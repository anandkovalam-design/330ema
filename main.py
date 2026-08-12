from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from groww_engine.config import load_engine_config
from groww_engine.paper_realtime_runner import PaperRealtimeRunner, PaperRunnerConfig
from quantlab.strategy_ema3_30_staged import StrategyConfig


class GrowwRealtimeMarketDataSdkLike(Protocol):
    def authenticate(self, api_key: str, api_secret: str, access_token: str) -> None: ...

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...

    def list_option_contracts(self, underlying: str, trade_date): ...

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]: ...

    def get_ltp(self, instrument: str) -> dict[str, Any]: ...


class NullGrowwRealtimeSdk:
    """Safe placeholder: no order APIs, market-data only methods."""

    def authenticate(self, api_key: str, api_secret: str, access_token: str) -> None:
        del api_key, api_secret, access_token
        raise RuntimeError("No Groww SDK factory configured. Set GROWW_PAPER_SDK_FACTORY.")

    def get_historical_ohlc(self, symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]:
        del symbol, interval, lookback_bars
        raise RuntimeError("No Groww SDK factory configured. Set GROWW_PAPER_SDK_FACTORY.")

    def list_option_contracts(self, underlying: str, trade_date):
        del underlying, trade_date
        raise RuntimeError("No Groww SDK factory configured. Set GROWW_PAPER_SDK_FACTORY.")

    def get_option_ohlc(self, contract_symbol: str, interval: str, lookback_bars: int) -> list[dict[str, Any]]:
        del contract_symbol, interval, lookback_bars
        raise RuntimeError("No Groww SDK factory configured. Set GROWW_PAPER_SDK_FACTORY.")

    def get_ltp(self, instrument: str) -> dict[str, Any]:
        del instrument
        raise RuntimeError("No Groww SDK factory configured. Set GROWW_PAPER_SDK_FACTORY.")


def _load_sdk_from_env() -> GrowwRealtimeMarketDataSdkLike:
    locator = os.getenv("GROWW_PAPER_SDK_FACTORY", "").strip()
    if not locator:
        return NullGrowwRealtimeSdk()
    if ":" not in locator:
        raise ValueError("GROWW_PAPER_SDK_FACTORY must be module.path:function_name")
    module_name, function_name = locator.split(":", 1)
    module = __import__(module_name, fromlist=[function_name])
    factory = getattr(module, function_name)
    return factory()


def _setup_logging(log_dir: str) -> None:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "paper_runner.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
    )


def build_runner_config() -> tuple[PaperRunnerConfig, Any]:
    engine_cfg = load_engine_config()
    strategy = StrategyConfig(
        no_fixed_target=True,
        target_pct=None,
        exit_mode="STAGED_TRAILING",
        entry_end_time=engine_cfg.risk.new_entry_cutoff_time,
        forced_square_off_time=engine_cfg.risk.mandatory_square_off_time,
    )
    runner_cfg = PaperRunnerConfig(
        mode="PAPER",
        timezone=engine_cfg.timezone,
        underlying_symbol=engine_cfg.underlying_symbol,
        interval_minutes=5,
        lookback_bars=240,
        quantity=min(50, engine_cfg.risk.max_quantity_per_trade),
        stop_loss_pct=0.10,
        entry_cutoff_time=engine_cfg.risk.new_entry_cutoff_time.strftime("%H:%M"),
        forced_square_off_time=engine_cfg.risk.mandatory_square_off_time.strftime("%H:%M"),
        poll_seconds=engine_cfg.poll_seconds,
        max_daily_loss=engine_cfg.risk.max_daily_loss,
        max_trades_per_day=engine_cfg.risk.max_trades_per_day,
        one_open_position_only=True,
        sqlite_path=engine_cfg.sqlite_path,
        csv_path=engine_cfg.csv_path,
        strategy=strategy,
        costs=engine_cfg.costs,
    )
    return runner_cfg, engine_cfg.risk


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Groww Cloud realtime paper-only runner")
    parser.add_argument("--max-cycles", type=int, default=0)
    args = parser.parse_args(argv)

    runner_cfg, risk_cfg = build_runner_config()
    _setup_logging("outputs/groww/logs")

    logger = logging.getLogger("groww_paper_main")
    logger.info("Starting Groww PAPER runner mode=%s underlying=%s", runner_cfg.mode, runner_cfg.underlying_symbol)

    sdk = _load_sdk_from_env()
    runner = PaperRealtimeRunner(sdk=sdk, config=runner_cfg, risk=risk_cfg)
    runner.authenticate()
    runner.run_forever(max_cycles=max(0, args.max_cycles))
    logger.info("Runner stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
