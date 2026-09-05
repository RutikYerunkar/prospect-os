"""Fernet-based encryption for the Gmail refresh token at rest (V2-G).

Same rotation posture as `api/operator_auth.py`'s `SESSION_SIGNING_KEY`/
`SESSION_SIGNING_KEY_OLD`: `TOKEN_ENCRYPTION_KEY` encrypts every new value;
`TOKEN_ENCRYPTION_KEY_OLD` is accepted for decryption only, so one key
rotation doesn't strand an already-encrypted refresh token. Fails closed —
a missing/wrong key raises rather than ever returning a guessed or partial
plaintext, and a missing key at encrypt time raises rather than ever
persisting a refresh token unencrypted.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from groundwork.config import settings

# Informational only (persisted alongside the ciphertext) — decryption never
# branches on it and always tries the current key before the old one,
# exactly like `operator_auth.verify_session_cookie`'s key-rotation order.
CURRENT_KEY_VERSION = 1


class TokenEncryptionError(Exception):
    """Encryption/decryption could not be performed — the caller must fail
    closed (never persist a connection, never treat a partial result as
    good) rather than catch this and continue."""


def _fernet(key: str) -> Fernet:
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt_refresh_token(plaintext: str) -> tuple[str, int]:
    """Always encrypts with the CURRENT key — `TOKEN_ENCRYPTION_KEY_OLD` is
    never used for new writes. Returns `(ciphertext, key_version)`."""
    if not settings.token_encryption_key:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not configured — cannot encrypt a refresh token")
    ciphertext = _fernet(settings.token_encryption_key).encrypt(plaintext.encode("utf-8"))
    return ciphertext.decode("utf-8"), CURRENT_KEY_VERSION


def decrypt_refresh_token(ciphertext: str, key_version: int | None = None) -> str:
    """Tries the CURRENT key first, then `TOKEN_ENCRYPTION_KEY_OLD` if
    configured — `key_version` is not required to disambiguate (Fernet
    tokens are self-describing; either key either decrypts them or doesn't).
    Raises `TokenEncryptionError` if neither key succeeds — never returns a
    partial/guessed value."""
    if settings.token_encryption_key:
        try:
            return _fernet(settings.token_encryption_key).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            pass
    if settings.token_encryption_key_old:
        try:
            return _fernet(settings.token_encryption_key_old).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            pass
    raise TokenEncryptionError("refresh token could not be decrypted with any configured key")
