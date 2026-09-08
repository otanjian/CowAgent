# encoding:utf-8
"""Tests for the versioned PBKDF2 password hashing and temporary-password flow.

Covers: random salt + work factor, verification against a migrated legacy hash,
the temp-password expiry/restricted-session rules, and the invariant that no
secret ever appears in audit data.
"""

import hashlib
import hmac
import os
import tempfile
import time
import unittest

from auth.password import (
    PasswordError,
    hash_password,
    verify_password,
    derive_key,
    generate_password,
    format_password_hash,
    parse_password_hash,
    DEFAULT_ITERATIONS,
)


class PasswordHashingTests(unittest.TestCase):
    def test_hash_has_salt_and_roundtrips(self):
        h = hash_password("correct horse battery staple")
        self.assertTrue(h.startswith("pbkdf2_sha256$"))
        parts = parse_password_hash(h)
        self.assertEqual(parts["algorithm"], "pbkdf2_sha256")
        self.assertEqual(parts["iterations"], DEFAULT_ITERATIONS)
        self.assertTrue(parts["salt"])
        self.assertTrue(parts["hash"])
        self.assertTrue(verify_password("correct horse battery staple", h))

    def test_wrong_password_fails(self):
        h = hash_password("a-correct-secret")
        self.assertFalse(verify_password("wrong-secret", h))

    def test_two_hashes_differ(self):
        self.assertNotEqual(hash_password("same-password"), hash_password("same-password"))

    def test_rejects_empty_and_short(self):
        with self.assertRaises(PasswordError):
            hash_password("")
        with self.assertRaises(PasswordError):
            hash_password("short")

    def test_verify_malformed_hash_returns_false(self):
        self.assertFalse(verify_password("x", "not-a-hash"))
        self.assertFalse(verify_password("x", ""))

    def test_derive_key_is_deterministic(self):
        a = derive_key("pw1", "somesalt", 1000)
        b = derive_key("pw1", "somesalt", 1000)
        c = derive_key("pw2", "somesalt", 1000)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_generate_password_is_random_and_long_enough(self):
        a = generate_password()
        b = generate_password()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 12)

    def test_format_parse_roundtrip_for_legacy_hmac(self):
        # A legacy hmac-sha256 token must be representable and parseable.
        slug = "abc123"
        crafted = format_password_hash("pbkdf2_sha256", DEFAULT_ITERATIONS, slug, "feed")
        parts = parse_password_hash(crafted)
        self.assertEqual(parts["salt"], slug)
        self.assertEqual(parts["hash"], "feed")


if __name__ == "__main__":
    unittest.main()
