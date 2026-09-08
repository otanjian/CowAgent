# encoding:utf-8
"""Tests for the bounded login rate limiter (task 2.7)."""

import os
import time
import unittest
from unittest.mock import patch

from auth.ratelimit import (LoginRateLimiter, RateLimitDecision,
                            DeploymentError, reject_multi_worker_identity)


class FakeClock:
    def __init__(self):
        self.t = 1_000_000

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class LoginRateLimiterTests(unittest.TestCase):
    def test_below_account_limit_allowed(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=3, source_max=100,
                                   window_seconds=900, clock=clock)
        limiter.record_failure("alice", "1.2.3.4")
        limiter.record_failure("alice", "1.2.3.4")
        decision = limiter.check("alice", "1.2.3.4")
        self.assertTrue(decision.allowed)

    def test_account_limit_exceeded_blocks(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=2, source_max=100,
                                   window_seconds=900, clock=clock)
        for _ in range(2):
            limiter.record_failure("alice", "1.2.3.4")
        decision = limiter.check("alice", "1.2.3.4")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.category, "account")
        self.assertGreaterEqual(decision.retry_after, 1)

    def test_source_limit_exceeded_blocks_distinct_accounts(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=100, source_max=3,
                                   window_seconds=900, clock=clock)
        for acct in ("alice", "bob", "carol"):
            limiter.record_failure(acct, "9.9.9.9")
        decision = limiter.check("dave", "9.9.9.9")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.category, "source")

    def test_window_resets_allows_again(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=2, source_max=100,
                                   window_seconds=900, clock=clock)
        for _ in range(2):
            limiter.record_failure("alice", "1.2.3.4")
        self.assertFalse(limiter.check("alice", "1.2.3.4").allowed)
        clock.advance(900)
        self.assertTrue(limiter.check("alice", "1.2.3.4").allowed)

    def test_capacity_saturates_new_keys(self):
        # When the per-category map is at capacity, a brand-new key is limited,
        # never evicting a live counter.
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=100, source_max=100,
                                   window_seconds=900, max_capacity=3, clock=clock)
        for i in range(3):
            limiter.record_failure(f"user{i}", f"1.1.1.{i}")
        # Existing key "user0" still within its count (1 < 100) -> allowed.
        self.assertTrue(limiter.check("user0", "1.1.1.0").allowed)
        # Brand-new key beyond capacity -> limited (not evicted).
        decision = limiter.check("brandnew", "5.5.5.5")
        self.assertFalse(decision.allowed)

    def test_capacity_does_not_evict_live_limit(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=1, source_max=100,
                                   window_seconds=900, max_capacity=2, clock=clock)
        limiter.record_failure("alice", "1.1.1.1")
        self.assertFalse(limiter.check("alice", "1.1.1.1").allowed)
        # Adding a new key must not evict alice's live counter.
        limiter.record_failure("bob", "2.2.2.2")
        limiter.record_failure("carol", "3.3.3.3")
        self.assertFalse(limiter.check("alice", "1.1.1.1").allowed)

    def test_retry_after_reflects_window_remaining(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=1, source_max=100,
                                   window_seconds=900, clock=clock)
        limiter.record_failure("alice", "1.2.3.4")
        decision = limiter.check("alice", "1.2.3.4")
        self.assertFalse(decision.allowed)
        self.assertLessEqual(decision.retry_after, 900)
        clock.advance(1)
        # ~899 seconds remain.
        decision2 = limiter.check("alice", "1.2.3.4")
        self.assertGreaterEqual(decision2.retry_after, 1)

    def test_reset_clears_all(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(account_max=1, source_max=100,
                                   window_seconds=900, clock=clock)
        limiter.record_failure("alice", "1.2.3.4")
        limiter.reset()
        self.assertTrue(limiter.check("alice", "1.2.3.4").allowed)
        self.assertEqual(limiter.live_account_keys(), 0)


class MultiWorkerRejectionTests(unittest.TestCase):
    def _conf(self, mode):
        return {"identity_mode": mode}

    def test_database_mode_rejects_multi_worker(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "2"}), \
                patch("config.conf", return_value=self._conf("database")):
            with self.assertRaises(DeploymentError):
                reject_multi_worker_identity()

    def test_single_worker_database_mode_allowed(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "1"}, clear=False), \
                patch("config.conf", return_value=self._conf("database")):
            # A single worker is the supported baseline.
            reject_multi_worker_identity()  # no raise

    def test_legacy_mode_allows_multi_worker(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "4"}, clear=False), \
                patch("config.conf", return_value=self._conf("legacy")):
            reject_multi_worker_identity()  # no raise


if __name__ == "__main__":
    unittest.main()
