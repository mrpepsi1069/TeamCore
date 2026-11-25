"""shared/security.py — token hashing and at-rest encryption helpers."""

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only the SHA-256 of a session token is stored, so a database leak can't be replayed as a login."""
    return hashlib.sha256(token.encode()).hexdigest()


def safe_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class Vault:
    """Fernet wrapper. `Vault(key)`; encrypt()/decrypt() work on str."""

    def __init__(self, key: str):
        if not key:
            raise RuntimeError("DATA_ENCRYPTION_KEY is not set (generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")")
        self._f = Fernet(key.encode())

    def encrypt(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._f.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None


def mask_secret(value: str, keep: int = 6) -> str:
    return "•" * 8 + value[-keep:] if value else ""
