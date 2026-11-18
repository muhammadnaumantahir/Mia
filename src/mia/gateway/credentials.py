"""OS credential-store helpers; provider secrets never enter Mia's SQLite database."""

from __future__ import annotations


class CredentialStoreUnavailable(RuntimeError):
    """The operating-system credential store is unavailable."""


class CredentialStore:
    """Store provider keys in the current user's OS credential manager.

    On Windows, the recommended keyring backend uses Windows Credential
    Manager. Install the optional credentials extra before using this API.
    """

    SERVICE_NAME = "Mia"

    def __init__(self) -> None:
        try:
            import keyring
        except ImportError as exc:
            raise CredentialStoreUnavailable(
                'Install Mia with the "credentials" extra to use OS credential storage.'
            ) from exc
        self._keyring = keyring

    def set(self, provider_id: str, secret: str) -> None:
        provider = provider_id.strip().lower()
        if not provider or not secret:
            raise ValueError("provider_id and secret are required")
        self._keyring.set_password(self.SERVICE_NAME, provider, secret)

    def get(self, provider_id: str) -> str | None:
        provider = provider_id.strip().lower()
        if not provider:
            raise ValueError("provider_id is required")
        return self._keyring.get_password(self.SERVICE_NAME, provider)

    def delete(self, provider_id: str) -> None:
        provider = provider_id.strip().lower()
        if not provider:
            raise ValueError("provider_id is required")
        try:
            self._keyring.delete_password(self.SERVICE_NAME, provider)
        except self._keyring.errors.PasswordDeleteError:
            return
