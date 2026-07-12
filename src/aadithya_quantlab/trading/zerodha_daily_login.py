"""Guided once-per-day Zerodha login setup."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from aadithya_quantlab.trading.zerodha import build_kite_login_url
from aadithya_quantlab.trading.zerodha_live import (
    DEFAULT_ACCESS_TOKEN_FILE,
    DailyLoginInputs,
    run_daily_login,
)


def _prompt_required(
    label: str,
    *,
    env_name: str,
    secret: bool = False,
) -> str:
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    prompt = f"{label}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guided daily Zerodha login. Use once each trading day, then reuse saved token."
    )
    parser.add_argument(
        "--token-output",
        default=str(DEFAULT_ACCESS_TOKEN_FILE),
        help="Local file path for the day access token. This path is gitignored.",
    )
    parser.add_argument(
        "--skip-url",
        action="store_true",
        help="Do not print the login URL before requesting the token.",
    )
    args = parser.parse_args(argv)

    api_key = _prompt_required("ZERODHA_API_KEY", env_name="ZERODHA_API_KEY")
    if not args.skip_url:
        print("Open this Zerodha login URL:")
        print(build_kite_login_url(api_key))
        print("After login, copy request_token from the redirected browser URL.")

    request_token = _prompt_required("ZERODHA_REQUEST_TOKEN", env_name="ZERODHA_REQUEST_TOKEN")
    api_secret = _prompt_required(
        "ZERODHA_API_SECRET",
        env_name="ZERODHA_API_SECRET",
        secret=True,
    )

    result = run_daily_login(
        DailyLoginInputs(
            api_key=api_key,
            api_secret=api_secret,
            request_token=request_token,
            token_output_path=Path(args.token_output),
        )
    )
    print(f"Daily access token saved to: {result.token_output_path}")
    print(f"Access token (masked): {result.masked_access_token}")
    print("For today, later commands can reuse this saved token automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
