"""Owner authentication, server-side sessions, and security audit services."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from aadithya_quantlab.zerodha_live_trading.database import Database


IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=12)
RATE_LIMIT_WINDOW = timedelta(minutes=15)
MAX_FAILED_ATTEMPTS = 5
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


@dataclass(frozen=True)
class ClientContext:
    ip_address: str = "Unavailable"
    country: str = "Unavailable"
    region: str = "Unavailable"
    city: str = "Unavailable"
    user_agent: str = "Unavailable"
    device_type: str = "Unavailable"
    browser: str = "Unavailable"
    operating_system: str = "Unavailable"

    @property
    def approximate_location(self) -> str:
        values = [value for value in (self.city, self.region, self.country) if value != "Unavailable"]
        return ", ".join(values) if values else "Unavailable"


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return bool(PASSWORD_HASHER.verify(password_hash, password))
    except (VerifyMismatchError, InvalidHashError):
        return False


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _classify_user_agent(user_agent: str) -> tuple[str, str, str]:
    lowered = user_agent.lower()
    device = "Mobile" if any(item in lowered for item in ("mobile", "android", "iphone")) else "Desktop"
    browser = "Edge" if "edg/" in lowered else "Chrome" if "chrome/" in lowered else "Firefox" if "firefox/" in lowered else "Safari" if "safari/" in lowered else "Other"
    operating_system = "Windows" if "windows" in lowered else "macOS" if "mac os" in lowered else "Android" if "android" in lowered else "iOS" if any(item in lowered for item in ("iphone", "ipad")) else "Linux" if "linux" in lowered else "Other"
    return device, browser, operating_system


def resolve_client_context(headers: Mapping[str, str], *, trust_proxy_headers: bool = False) -> ClientContext:
    """Resolve network metadata; forwarded headers are ignored unless explicitly trusted."""

    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    ip_address = normalized.get("remote-addr", "Unavailable")
    if trust_proxy_headers:
        forwarded = normalized.get("x-forwarded-for", "").split(",", 1)[0].strip()
        ip_address = forwarded or normalized.get("x-real-ip", ip_address)
    user_agent = normalized.get("user-agent", "Unavailable")
    device, browser, operating_system = _classify_user_agent(user_agent)
    # Provider intentionally defaults to unavailable. A future resolver can populate these fields.
    return ClientContext(
        ip_address=ip_address,
        user_agent=user_agent,
        device_type=device,
        browser=browser,
        operating_system=operating_system,
    )


class AuthenticationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def bootstrap_owner_from_environment(self) -> bool:
        if self.database.owner_exists():
            return False
        username = os.getenv("ZERODHA_OWNER_USERNAME", "").strip()
        password = os.getenv("ZERODHA_OWNER_PASSWORD", "")
        if not username or not password:
            return False
        user_id = self.database.create_user(username, hash_password(password), role="OWNER")
        self.database.log_security_event("OWNER_BOOTSTRAPPED", user_id=user_id, details="environment bootstrap")
        return True

    def bootstrap_local_owner(self, username: str, password: str, confirmation: str) -> int:
        """Create the first OWNER interactively without persisting plaintext credentials."""

        if self.database.owner_exists():
            raise ValueError("An OWNER account already exists.")
        normalized_username = username.strip().lower()
        if not normalized_username:
            raise ValueError("OWNER username is required.")
        if password != confirmation:
            raise ValueError("Password and confirmation do not match.")
        try:
            user_id = self.database.create_user(
                normalized_username,
                hash_password(password),
                role="OWNER",
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("That username already exists.") from error
        self.database.log_security_event(
            "OWNER_BOOTSTRAPPED",
            user_id=user_id,
            details="interactive local bootstrap",
        )
        return user_id

    def login(self, username: str, password: str, client: ClientContext) -> str | None:
        normalized_username = username.strip().lower()
        now = datetime.now(timezone.utc)
        since = (now - RATE_LIMIT_WINDOW).isoformat()
        failed_count = self.database.recent_failed_logins(normalized_username, client.ip_address, since)
        if failed_count >= MAX_FAILED_ATTEMPTS:
            self.database.log_security_event(
                "LOGIN_RATE_LIMITED",
                ip_address=client.ip_address,
                approximate_location=client.approximate_location,
                user_agent=client.user_agent,
                details=f"username={normalized_username}",
            )
            return None

        user = self.database.get_user_by_username(normalized_username)
        if not user or not bool(user["active"]) or not verify_password(str(user["password_hash"]), password):
            self.database.log_security_event(
                "LOGIN_FAILED",
                user_id=int(user["id"]) if user else None,
                ip_address=client.ip_address,
                approximate_location=client.approximate_location,
                user_agent=client.user_agent,
                details=f"username={normalized_username}",
            )
            return None

        token = secrets.token_urlsafe(48)
        session_id = _session_digest(token)
        self.database.create_session(
            {
                "session_id": session_id,
                "user_id": int(user["id"]),
                "created_at": now.isoformat(),
                "last_activity": now.isoformat(),
                "expires_at": (now + ABSOLUTE_TIMEOUT).isoformat(),
                "revoked": 0,
                "ip_address": client.ip_address,
                "country": client.country,
                "region": client.region,
                "city": client.city,
                "user_agent": client.user_agent,
                "device_type": client.device_type,
                "browser": client.browser,
                "operating_system": client.operating_system,
            }
        )
        self.database.log_security_event(
            "LOGIN_SUCCESS",
            user_id=int(user["id"]),
            ip_address=client.ip_address,
            approximate_location=client.approximate_location,
            user_agent=client.user_agent,
        )
        return token

    def validate_session(self, token: str | None, *, touch: bool = True) -> dict[str, object] | None:
        if not token:
            return None
        session_id = _session_digest(token)
        session = self.database.get_session(session_id)
        if not session or bool(session["revoked"]) or not bool(session["active"]):
            return None
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(str(session["expires_at"]))
        last_activity = datetime.fromisoformat(str(session["last_activity"]))
        if now >= expires_at or now - last_activity >= IDLE_TIMEOUT:
            self.database.revoke_session(session_id)
            self.database.log_security_event("SESSION_EXPIRED", user_id=int(session["user_id"]))
            return None
        if touch:
            self.database.touch_session(session_id, now.isoformat())
        session["session_id"] = session_id
        if touch:
            session["last_activity"] = now.isoformat()
        return session

    def logout(self, token: str, client: ClientContext) -> None:
        session_id = _session_digest(token)
        session = self.database.get_session(session_id)
        self.database.revoke_session(session_id)
        self.database.log_security_event(
            "LOGOUT",
            user_id=int(session["user_id"]) if session else None,
            ip_address=client.ip_address,
            approximate_location=client.approximate_location,
            user_agent=client.user_agent,
        )

    @staticmethod
    def require_owner(session: Mapping[str, object]) -> None:
        if str(session.get("role", "")).upper() != "OWNER":
            raise PermissionError("OWNER role required.")

    def create_viewer(
        self,
        actor: Mapping[str, object],
        username: str,
        password: str,
        confirmation: str,
    ) -> int:
        self.require_owner(actor)
        normalized_username = username.strip().lower()
        if not normalized_username:
            raise ValueError("Viewer username is required.")
        if password != confirmation:
            raise ValueError("Password and confirmation do not match.")
        if self.database.get_user_by_username(normalized_username):
            raise ValueError("That username already exists.")
        try:
            user_id = self.database.create_user(normalized_username, hash_password(password), role="USER")
        except sqlite3.IntegrityError as error:
            raise ValueError("That username already exists.") from error
        self.database.log_security_event(
            "VIEWER_CREATED",
            user_id=user_id,
            details=f"created_by_user={actor['user_id']}",
        )
        return user_id

    def set_viewer_active(self, actor: Mapping[str, object], user_id: int, active: bool) -> int:
        self.require_owner(actor)
        viewer = self.database.get_user(user_id)
        if not viewer or str(viewer["role"]).upper() != "USER":
            raise ValueError("Viewer account not found.")
        self.database.set_user_active(user_id, active)
        revoked = 0 if active else self.database.revoke_user_sessions(user_id)
        self.database.log_security_event(
            "VIEWER_ENABLED" if active else "VIEWER_DISABLED",
            user_id=user_id,
            details=f"changed_by_user={actor['user_id']};sessions_revoked={revoked}",
        )
        return revoked

    def revoke_session(self, actor: Mapping[str, object], target_session_id: str) -> None:
        self.require_owner(actor)
        target = self.database.get_session(target_session_id)
        if not target:
            return
        if str(target["role"]).upper() == "OWNER" and int(target["user_id"]) != int(actor["user_id"]):
            raise PermissionError("An owner session cannot be terminated by another account.")
        self.database.revoke_session(target_session_id)
        self.database.log_security_event(
            "SESSION_REVOKED", user_id=int(target["user_id"]), details=f"revoked_by_user={actor['user_id']}"
        )

    def revoke_all_other_sessions(self, actor: Mapping[str, object]) -> int:
        self.require_owner(actor)
        count = self.database.revoke_other_sessions(int(actor["user_id"]), str(actor["session_id"]))
        self.database.log_security_event(
            "OTHER_SESSIONS_REVOKED", user_id=int(actor["user_id"]), details=f"count={count}"
        )
        return count

    def change_owner_password(
        self,
        actor: Mapping[str, object],
        current_password: str,
        new_password: str,
        confirmation: str,
    ) -> int:
        self.require_owner(actor)
        if new_password != confirmation:
            raise ValueError("New password and confirmation do not match.")
        user = self.database.get_user(int(actor["user_id"]))
        if not user or not verify_password(str(user["password_hash"]), current_password):
            raise ValueError("Current password is incorrect.")
        self.database.update_password(int(user["id"]), hash_password(new_password))
        revoked = self.database.revoke_other_sessions(int(user["id"]), str(actor["session_id"]))
        self.database.log_security_event("PASSWORD_CHANGED", user_id=int(user["id"]), details=f"other_sessions_revoked={revoked}")
        return revoked
