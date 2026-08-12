from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from .errors import ConfigurationError


class TradingMode(str, Enum):
    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


@dataclass(frozen=True)
class Settings:
    mode: TradingMode = TradingMode.PAPER
    api_key: str | None = None
    api_secret: str | None = None
    account: str = "default"
    live_trading_enabled: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        raw_mode = os.getenv("TRADING_MODE", "PAPER").strip().upper()
        try:
            mode = TradingMode(raw_mode)
        except ValueError as exc:
            raise ConfigurationError(
                f"TRADING_MODE must be PAPER, SANDBOX, or LIVE; got {raw_mode!r}"
            ) from exc

        settings = cls(
            mode=mode,
            api_key=_optional_env("KITE_API_KEY"),
            api_secret=_optional_env("KITE_API_SECRET"),
            account=os.getenv("KITE_ACCOUNT", "default").strip() or "default",
            live_trading_enabled=_env_bool("KITE_LIVE_TRADING_ENABLED", False),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
        settings.validate()
        return settings

    @property
    def effective_api_key(self) -> str | None:
        return "sandboxdemo" if self.mode is TradingMode.SANDBOX else self.api_key

    @property
    def effective_api_secret(self) -> str | None:
        return (
            "sandboxdemo-secret"
            if self.mode is TradingMode.SANDBOX
            else self.api_secret
        )

    def validate(self) -> None:
        if self.mode is TradingMode.LIVE and not self.api_key:
            raise ConfigurationError("KITE_API_KEY is required in LIVE mode.")
        if self.mode is TradingMode.LIVE and not self.api_secret:
            raise ConfigurationError("KITE_API_SECRET is required in LIVE mode.")


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")
