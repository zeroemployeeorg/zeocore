# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/tests/test_voicerouter.py
# Apache-2.0, see LICENSE and NOTICE
"""Voice-change policy and candidate selection."""

from __future__ import annotations

import unittest

from zeo_core.voice.base import ProviderSpec, RouterPolicy
from zeo_core.voice.failures import (
    classify,
    justifies_voice_change,
    should_retry_same_provider,
)
from zeo_core.voice.health import (
    reset_shared_registries,
)
from zeo_core.voice.metrics import reset_shared_metrics
from zeo_core.voice.utterance import UtterancePolicy


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


class TestVoiceChangePolicy(unittest.TestCase):
    """Switching provider means the caller hears a different person mid-call."""

    def test_only_genuine_unavailability_justifies_a_new_voice(self):
        for exc, expected in (
            (http_error(402, "Payment Required"), True),  # out of credits
            (TimeoutError("timed out"), True),  # unreachable
            (http_error(401, "Unauthorized"), True),  # rejected key
            (http_error(400, "Bad Request"), True),  # broken config
            (http_error(429, "Too Many"), False),  # slow down
            (http_error(503, "Unavailable"), False),  # momentary wobble
        ):
            v = classify(exc)
            self.assertEqual(justifies_voice_change(v), expected, v.kind)

    def test_retry_same_is_the_inverse(self):
        for exc in (
            http_error(429, "x"),
            http_error(503, "x"),
            http_error(402, "x"),
            TimeoutError("x"),
        ):
            v = classify(exc)
            self.assertEqual(
                should_retry_same_provider(v), not justifies_voice_change(v)
            )


class TestCandidateSelection(unittest.TestCase):
    """Who gets tried, in what order."""

    def _router(self, labels, policy=None):
        from zeo_core.voice.base import BuiltProvider
        from zeo_core.voice.routed_tts import RoutedTTS

        providers = [
            BuiltProvider(ProviderSpec(name=l, label=l, config={}), object())
            for l in labels
        ]
        return RoutedTTS(providers, RouterPolicy.from_dict(policy or {}))

    def setUp(self):
        reset_shared_registries()
        reset_shared_metrics()

    def test_configured_order_by_default(self):
        r = self._router(["a", "b", "c"])
        self.assertEqual(r._candidates(), [0, 1, 2])

    def test_cooling_providers_drop_below_healthy_ones_but_stay(self):
        # A vendor rate-limited ten seconds ago still beats silence.
        r = self._router(["a", "b"])
        r._health.get("a").record_failure(http_error(429, "Too Many"))
        self.assertEqual(r._candidates(), [1, 0])

    def test_disabled_providers_are_dropped_entirely(self):
        # They fail identically every time; trying one only delays the one
        # that might work.
        r = self._router(["a", "b"])
        r._health.get("a").record_failure(http_error(401, "Unauthorized"))
        self.assertEqual(r._candidates(), [1])

    def test_latency_selection_prefers_the_measured_winner(self):
        r = self._router(["slow", "fast"], {"selection": "latency"})
        for _ in range(5):
            r._metrics.record_success("slow", 800.0)
            r._metrics.record_success("fast", 90.0)
        self.assertEqual(r._candidates()[0], 1)

    def test_latency_selection_keeps_configured_order_without_evidence(self):
        # One lucky call must not reorder the chain.
        r = self._router(["a", "b"], {"selection": "latency"})
        r._metrics.record_success("b", 10.0)
        self.assertEqual(r._candidates(), [0, 1])

    def test_utterance_preference_reorders_but_never_restricts(self):
        r = self._router(["premium", "cheap"])
        r._utterance = UtterancePolicy.from_dict(
            {"filler": {"max_chars": 20, "prefer": ["cheap"]}}
        )
        self.assertEqual(r._candidates_for("One moment.")[0], 1)
        # The premium voice is still reachable if the cheap one is down.
        self.assertIn(0, r._candidates_for("One moment."))
        self.assertEqual(r._candidates_for("A much longer disclosure sentence.")[0], 0)
