# encoding:utf-8
"""Reversible credential encryption (open-database-runtime 7.x).

Credentials are stored encrypted with AES-256-GCM; the key is deployment
controlled via the ``COW_CREDENTIAL_MASTER_KEY`` environment variable (32
bytes hex) or a long passphrase (any length; hashed with PBKDF2 to a key).
No master key configured => first use raises so plaintext is never silently
stored under a well-known fallback in production.

Ciphertext format: ``v1.<nonce_b64>.<tag_and_ct_b64>``.
"""

from __future__ import annotations

import base64
import hashlib
import os

from Crypto.Cipher import AES

KEY_ENV = "COW_CREDENTIAL_MASTER_KEY"
_ALGO = "sha256"
_ITER = 200_000


class CredentialCryptoError(RuntimeError):
    """Raised when credential encryption is unavailable/misconfigured."""


def _key() -> bytes:
    raw = os.environ.get(KEY_ENV, "").strip()
    if not raw:
        raise CredentialCryptoError(
            f"{KEY_ENV} is not configured; refusing to store credentials "
            "with a fallback key"
        )
    raw = raw.strip()
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    # Any passphrase: PBKDF2-HMAC-SHA256 → 32-byte key.
    return hashlib.pbkdf2_hmac(_ALGO, raw.encode("utf-8"), b"cowagent-credentials", _ITER)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret into a portable token. Raises when no key is set."""
    if plaintext is None:
        raise ValueError("cannot encrypt a null secret")
    key = _key()
    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(str(plaintext).encode("utf-8"))
    return "v1." + base64.urlsafe_b64encode(nonce).decode("ascii") + "." + \
        base64.urlsafe_b64encode(tag + ciphertext).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a token created by :func:`encrypt_secret`."""
    try:
        version, nonce_b64, body_b64 = str(token).split(".", 2)
        if version != "v1":
            raise ValueError("unsupported credential ciphertext version")
        nonce = base64.urlsafe_b64decode(nonce_b64)
        body = base64.urlsafe_b64decode(body_b64)
        if len(body) < 16:
            raise ValueError("malformed credential ciphertext")
        tag, ciphertext = body[:16], body[16:]
        key = _key()
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except CredentialCryptoError:
        raise
    except Exception as error:
        raise CredentialCryptoError(f"credential decrypt failed: {error}") from error


def mask_secret(name: str, plaintext: str, keep: int = 3) -> str:
    """Display-only masked projection, e.g. ``sap_prod •••• zX7``.

    Never reversible: only the first ``keep`` and last two characters of the
    secret survive; a short secret is fully masked.
    """
    value = str(plaintext or "")
    label = str(name or "")
    if len(value) <= (keep + 2):
        return f"{label}••••"
    return f"{label} {value[:keep]}••••{value[-2:]}"
