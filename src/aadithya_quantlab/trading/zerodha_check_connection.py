"""Verify Zerodha connectivity using Kite profile endpoint."""

from __future__ import annotations

from aadithya_quantlab.trading.zerodha_live import build_safe_kite_client_from_env


def main() -> int:
    profile = build_safe_kite_client_from_env().profile()
    user_id = str(profile.get("user_id", ""))
    user_name = str(profile.get("user_name", ""))
    print(f"Connected to Zerodha as user_id={user_id} user_name={user_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
