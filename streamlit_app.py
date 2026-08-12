"""Canonical entry point for the screenshot-matched trading dashboard."""

from __future__ import annotations

import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from aadithya_quantlab.trading.trading_dashboard_app import main


main()
