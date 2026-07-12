"""Print Zerodha Kite Connect login URL from environment variables."""

from __future__ import annotations

from aadithya_quantlab.trading.zerodha_live import build_login_url_from_env


def main() -> int:
    print(build_login_url_from_env())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
