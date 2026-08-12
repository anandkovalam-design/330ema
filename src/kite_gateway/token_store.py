from __future__ import annotations

from dataclasses import dataclass

from .errors import AuthenticationRequired, BrokerOperationError

SERVICE_NAME = "kite-safe-gateway"


@dataclass
class KeyringTokenStore:
    account: str

    def save(self, access_token: str) -> None:
        keyring, keyring_error = _keyring()
        try:
            keyring.set_password(SERVICE_NAME, self.account, access_token)
        except keyring_error as exc:
            raise BrokerOperationError(
                "Could not save the access token in the operating-system keyring."
            ) from exc

    def load(self) -> str:
        keyring, keyring_error = _keyring()
        try:
            token = keyring.get_password(SERVICE_NAME, self.account)
        except keyring_error as exc:
            raise AuthenticationRequired(
                "Could not read the access token from the operating-system keyring."
            ) from exc
        if not token:
            raise AuthenticationRequired(
                "No access token is stored. Complete the Kite login flow first."
            )
        return token

    def clear(self) -> None:
        keyring, keyring_error = _keyring()
        try:
            if keyring.get_password(SERVICE_NAME, self.account):
                keyring.delete_password(SERVICE_NAME, self.account)
        except keyring_error as exc:
            raise BrokerOperationError("Could not clear the stored access token.") from exc


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise BrokerOperationError(
            "The 'keyring' package is required for Kite authentication. "
            "Install the project dependencies first."
        ) from exc
    return keyring, KeyringError
