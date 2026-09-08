# encoding:utf-8
"""Versioned PBKDF2-HMAC-SHA256 password hashing for the IAM service.

Only the standard library is used. The hash string format is:

    ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``

Work factor is stored in the hash so it can be raised later without a one-off
re-hash; a re-hash happens lazily on successful verify when the stored
iterations differ from the current ``DEFAULT_ITERATIONS``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import string
import secrets

#: Default PBKDF2 work factor. Chosen to be slow enough for a password but fast
#: enough not to stall login on modest hardware. Raise deliberately only after
#: measuring on the deployment platform (see the design.md benchmark note).
DEFAULT_ITERATIONS = 260_000

#: Hash identifier stored in the leading field of the hash string.
ALGORITHM = "pbkdf2_sha256"

#: Characters allowed in generated temporary passwords (no ambiguous lookalikes).
_TEMP_ALPHABET = string.ascii_letters + string.digits

#: Minimum usable password length for newly set passwords.
MIN_PASSWORD_LENGTH = 8


class PasswordError(ValueError):
    """Raised when a password cannot be hashed because it is invalid."""


def _check_password(password: str, min_length: int = MIN_PASSWORD_LENGTH) -> None:
    if not isinstance(password, str):
        raise PasswordError("password must be a string")
    if len(password) < min_length:
        raise PasswordError(f"password must be at least {min_length} characters")
    if not password.strip():
        raise PasswordError("password must not be blank")


def derive_key(password: str, salt: str, iterations: int) -> bytes:
    """Derive the raw PBKDF2-HMAC-SHA256 key for ``password`` + ``salt``."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )


def format_password_hash(algorithm: str, iterations: int, salt: str, digest_hex: str) -> str:
    return (
        f"{algorithm}${iterations}${base64.urlsafe_b64encode(salt.encode()).decode()}"
        f"${base64.urlsafe_b64encode(bytes.fromhex(digest_hex)).decode()}"
    )


def parse_password_hash(stored: str) -> dict:
    """Parse a stored hash string into its components.

    Returns a dict with keys ``algorithm``, ``iterations``, ``salt``, ``hash``.
    Raises ``PasswordError`` when the string is malformed.
    """
    if not isinstance(stored, str) or "$" not in stored:
        raise PasswordError("malformed password hash")
    parts = stored.split("$")
    if len(parts) != 4:
        raise PasswordError("malformed password hash")
    algorithm, iterations_s, salt_b64, hash_b64 = parts
    try:
        iterations = int(iterations_s)
    except ValueError:
        raise PasswordError("malformed password hash") from None
    if algorithm != ALGORITHM or iterations <= 0:
        raise PasswordError("unsupported password hash")
    try:
        salt = base64.urlsafe_b64decode(salt_b64.encode()).decode("utf-8")
        digest = base64.urlsafe_b64decode(hash_b64.encode()).hex()
    except Exception:
        raise PasswordError("malformed password hash") from None
    return {
        "algorithm": algorithm,
        "iterations": iterations,
        "salt": salt,
        "hash": digest,
    }


def hash_password(password: str, *, min_length: int = MIN_PASSWORD_LENGTH) -> str:
    """Return a fresh salted hash string for ``password``."""
    _check_password(password, min_length)
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode()[:22]
    digest = derive_key(password, salt, DEFAULT_ITERATIONS)
    return format_password_hash(ALGORITHM, DEFAULT_ITERATIONS, salt, digest.hex())


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of ``password`` against a stored hash string."""
    if not isinstance(password, str) or not password:
        return False
    try:
        parts = parse_password_hash(stored)
    except PasswordError:
        return False
    digest = derive_key(password, parts["salt"], parts["iterations"])
    actual = digest.hex()
    return hmac.compare_digest(actual, parts["hash"])


def generate_password(length: int = 16) -> str:
    """Generate a random, URL-safe temporary password."""
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))
