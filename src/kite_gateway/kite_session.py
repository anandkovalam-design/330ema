from __future__ import annotations

from urllib.parse import urlencode

from kiteconnect import KiteConnect

from .config import Settings, TradingMode
from .errors import AuthenticationRequired, BrokerOperationError, ConfigurationError
from .token_store import KeyringTokenStore

SANDBOX_ROOT = "https://sandbox.kite.trade"
SANDBOX_LOGIN = "https://sandbox.kite.trade/connect/login"


def build_client(settings: Settings, access_token: str | None = None) -> KiteConnect:
    api_key = settings.effective_api_key
    if not api_key:
        raise ConfigurationError("A Kite API key is required.")
    if settings.mode is TradingMode.SANDBOX:
        kite = KiteConnect(api_key=api_key, root=SANDBOX_ROOT)
        _patch_sandbox_routes(kite)
    else:
        kite = KiteConnect(api_key=api_key)
    if access_token:
        kite.set_access_token(access_token)
    return kite


def login_url(settings: Settings) -> str:
    if settings.mode is TradingMode.PAPER:
        raise ConfigurationError("PAPER mode does not require Kite login.")
    if settings.mode is TradingMode.SANDBOX:
        return f"{SANDBOX_LOGIN}?{urlencode({'api_key': settings.effective_api_key})}"
    return build_client(settings).login_url()


def exchange_request_token(
    settings: Settings, request_token: str, store: KeyringTokenStore
) -> dict:
    if settings.mode is TradingMode.PAPER:
        raise ConfigurationError("PAPER mode does not exchange Kite tokens.")
    secret = settings.effective_api_secret
    if not secret:
        raise ConfigurationError("KITE_API_SECRET is required for token exchange.")
    if not request_token.strip():
        raise ConfigurationError("request_token is required.")
    kite = build_client(settings)
    try:
        session = kite.generate_session(request_token.strip(), api_secret=secret)
        access_token = session["access_token"]
        kite.set_access_token(access_token)
        profile = kite.profile()
    except Exception as exc:
        raise AuthenticationRequired(
            f"Kite token exchange failed: {type(exc).__name__}: {exc}"
        ) from exc
    store.save(access_token)
    return {
        "user_id": profile.get("user_id"),
        "user_name": profile.get("user_name"),
        "login_time": session.get("login_time"),
    }


def authenticated_client(settings: Settings, store: KeyringTokenStore) -> KiteConnect:
    kite = build_client(settings, store.load())
    try:
        kite.profile()
    except Exception as exc:
        raise AuthenticationRequired(
            "The stored Kite session is invalid or expired; log in again."
        ) from exc
    return kite


def _patch_sandbox_routes(kite: KiteConnect) -> None:
    # Required by Zerodha's official sandbox: all SDK routes except instruments
    # are served below /oms.
    passthrough = {"market.instruments.all", "market.instruments"}
    kite._routes = {
        key: value if key in passthrough else "/oms" + value
        for key, value in kite._routes.items()
    }
