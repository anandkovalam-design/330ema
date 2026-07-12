"""Generate Zerodha access token without printing sensitive values."""

from __future__ import annotations

import argparse

from aadithya_quantlab.trading.zerodha_live import generate_access_token_from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Zerodha access token from request token.")
    parser.add_argument(
        "--token-output",
        default="outputs/zerodha/access_token.txt",
        help="File path where the full access token is saved.",
    )
    args = parser.parse_args(argv)

    result = generate_access_token_from_env(args.token_output)
    print(f"Access token saved to: {result.token_output_path}")
    print(f"Access token (masked): {result.masked_access_token}")
    print("Set ZERODHA_ACCESS_TOKEN from the saved file for subsequent commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
