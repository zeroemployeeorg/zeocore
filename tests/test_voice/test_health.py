# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/tests/test_voicerouter.py
# Apache-2.0, see LICENSE and NOTICE
"""Provider health: cool-down, permanent failures, shared registries."""

from __future__ import annotations

import time
import unittest

from zeo_core.voice.health import (
    HealthRegistry,
    reset_shared_registries,
    shared_registry,
)


class _ResponseError(Exception):
    """T1: a stand-in for aiohttp.ClientResponseError (aiohttp is not a Core dependency).

    It carries the attributes the classifier reads (`status`, `headers`) and the same text form
    (`"<status>, message=<message!r>, url=<url!r>"`) that the message-based rules see.
    """

    def __init__(self, status: int, message: str, headers: dict[str, str]) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers

    def __str__(self) -> str:
        return (
            f"{self.status}, message={self.message!r}, url='https://vendor.example/v1'"
        )


def http_error(status: int, message: str = "", headers=None):
    """A realistic HTTP client error (see _ResponseError)."""
    return _ResponseError(status, message, headers or {})


class TestHealth(unittest.TestCase):
    def setUp(self):
        reset_shared_registries()

    def test_permanent_failure_disables_and_success_does_not_revive_it(self):
        h = HealthRegistry().get("elevenlabs")
        h.record_failure(http_error(401, "Unauthorized"))
        self.assertTrue(h.disabled)
        self.assertFalse(h.is_available())
        # A later success cannot happen for a rejected key; if one somehow
        # does, it must not quietly re-enable a provider an operator has to fix.
        h.record_success()
        self.assertTrue(h.disabled)

    def test_rate_limit_uses_the_vendors_own_window(self):
        h = HealthRegistry().get("openai")
        h.record_failure(http_error(429, "Too Many", {"Retry-After": "7"}))
        self.assertFalse(h.is_available())
        # The vendor's hint wins over any local default.
        self.assertAlmostEqual(h.current_cooldown, 7.0, places=3)

    def test_retry_after_has_a_floor_so_we_never_hammer(self):
        # A vendor asking for 50ms should still not be retried 20x a second.
        h = HealthRegistry().get("openai")
        h.record_failure(http_error(429, "Too Many", {"Retry-After": "0.05"}))
        self.assertGreaterEqual(h.current_cooldown, 1.0)

    def test_a_circuit_actually_reopens(self):
        h = HealthRegistry(cooldown_seconds=0.05).get("deepgram")
        h.record_failure(RuntimeError("something odd"))  # unknown -> default
        h.current_cooldown = 0.05  # shorten for the test
        self.assertFalse(h.is_available())
        time.sleep(0.07)
        self.assertTrue(h.is_available())
        self.assertEqual(h.state, "half-open")

    def test_configured_cooldown_reaches_the_kinds_it_is_meant_to(self):
        # It was once configurable and inert: every kind had a hard-coded entry,
        # so the number an operator set was read and then never used.
        reg = HealthRegistry(cooldown_seconds=120.0)
        reg.get("gone").record_failure(ConnectionRefusedError("refused"))
        self.assertEqual(reg.get("gone").current_cooldown, 120.0)
        reg.get("odd").record_failure(RuntimeError("something nobody has seen"))
        self.assertEqual(reg.get("odd").current_cooldown, 120.0)

    def test_vendor_semantics_still_win_over_the_configured_default(self):
        # A billing window is not a matter of taste, so the knob must not
        # shorten it.
        reg = HealthRegistry(cooldown_seconds=5.0)
        reg.get("broke").record_failure(http_error(402, "Payment Required"))
        self.assertEqual(reg.get("broke").current_cooldown, 900.0)

    def test_quota_parks_far_longer_than_a_transient_error(self):
        reg = HealthRegistry()
        reg.get("a").record_failure(http_error(402, "Payment Required"))
        reg.get("b").record_failure(http_error(503, "Unavailable"))
        self.assertGreater(reg.get("a").reopens_in, reg.get("b").reopens_in * 5)

    def test_registry_is_shared_per_kind_so_it_outlives_a_call(self):
        # Rasa builds engines per call; call-scoped health would be forgotten
        # at every hangup and rediscovered by the next caller.
        shared_registry("tts").get("rime").record_failure(http_error(401, "no"))
        self.assertTrue(shared_registry("tts").get("rime").disabled)
        self.assertFalse(shared_registry("asr").get("rime").disabled)

    def test_reset_clears_it(self):
        shared_registry("tts").get("rime").record_failure(http_error(401, "no"))
        reset_shared_registries()
        self.assertFalse(shared_registry("tts").get("rime").disabled)
