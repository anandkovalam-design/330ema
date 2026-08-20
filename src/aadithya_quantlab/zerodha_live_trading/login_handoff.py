"""Short-lived, one-time dashboard session handoffs for broker login redirects."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class _Handoff:
    dashboard_token: str
    expires_at: float


class LoginHandoffStore:
    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._handoffs: dict[str, _Handoff] = {}
        self._lock = threading.Lock()

    def create(self, dashboard_token: str) -> str:
        if not dashboard_token:
            raise ValueError("dashboard_token is required")
        now = time.monotonic()
        handoff_id = secrets.token_urlsafe(32)
        with self._lock:
            self._discard_expired(now)
            self._handoffs[handoff_id] = _Handoff(
                dashboard_token=dashboard_token,
                expires_at=now + self._ttl_seconds,
            )
        return handoff_id

    def consume(self, handoff_id: str) -> str | None:
        if not handoff_id:
            return None
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            handoff = self._handoffs.pop(handoff_id, None)
        return handoff.dashboard_token if handoff else None

    def _discard_expired(self, now: float) -> None:
        expired = [
            handoff_id
            for handoff_id, handoff in self._handoffs.items()
            if handoff.expires_at <= now
        ]
        for handoff_id in expired:
            self._handoffs.pop(handoff_id, None)