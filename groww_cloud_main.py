from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from groww_engine.config import EffectiveTradingMode, load_engine_config
from groww_engine.engine import TradingEngine
from groww_engine.market_data_client import NullMarketDataClient
from groww_engine.storage import TradeStorage


def _setup_logging(logs_dir: str) -> None:
    path = Path(logs_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "engine.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Groww Cloud trading engine (safe PAPER default).")
    parser.add_argument("--max-cycles", type=int, default=0, help="Run this many cycles then exit. 0 means run forever.")
    args = parser.parse_args(argv)

    config = load_engine_config()
    _setup_logging(config.logs_dir)

    logger = logging.getLogger("groww_cloud_main")
    logger.info(
        "Engine mode requested=%s effective=%s reason=%s",
        config.requested_trading_mode,
        config.effective_trading_mode.value,
        config.mode_reason,
    )
    logger.info("Note: Groww access tokens expire daily at 06:00 AM; refresh before market start.")

    if config.effective_trading_mode is EffectiveTradingMode.LIVE:
        logger.warning("LIVE MODE ENABLED: real orders may be placed after all validations.")

    storage = TradeStorage(config.sqlite_path, config.csv_path)
    client = NullMarketDataClient()
    engine = TradingEngine(config, client, storage)

    should_stop = False

    def _stop_handler(signum: int, frame: object) -> None:
        nonlocal should_stop
        should_stop = True
        logger.info("Received signal=%s, shutting down safely.", signum)

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    cycle = 0
    while not should_stop:
        cycle += 1
        status = engine.run_cycle()
        logger.info(
            "cycle=%s mode=%s open_positions=%s status=%s",
            cycle,
            status.mode,
            status.open_positions,
            status.last_message,
        )
        if args.max_cycles > 0 and cycle >= args.max_cycles:
            break
        time.sleep(max(1, config.poll_seconds))

    final_status = engine.shutdown()
    logger.info("shutdown mode=%s message=%s", final_status.mode, final_status.last_message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
