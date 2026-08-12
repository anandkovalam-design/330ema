"""Verify Zerodha connectivity using Kite profile endpoint."""

from __future__ import annotations

from aadithya_quantlab.trading.zerodha_live import build_safe_kite_client_from_env


def main() -> int:
    try:
        profile = build_safe_kite_client_from_env().profile()
    except Exception as error:
        print(f"No valid Zerodha session found: {type(error).__name__}: {error}")
        print("Starting guided daily login now...")

        from aadithya_quantlab.trading.zerodha_daily_login import main as daily_login_main

        if daily_login_main([]) != 0:
            return 1

        try:
            profile = build_safe_kite_client_from_env().profile()
        except Exception as retry_error:
            print(f"Zerodha connection check failed after daily login: {type(retry_error).__name__}: {retry_error}")
            return 1

    user_id = str(profile.get("user_id", ""))
    user_name = str(profile.get("user_name", ""))
    print(f"Connected to Zerodha as user_id={user_id} user_name={user_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
