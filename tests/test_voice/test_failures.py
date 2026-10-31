# Modified from RasaHQ/rasa-community-resources@9f85e2be
# patterns/voice-vendor-router/tests/test_voicerouter.py
# Apache-2.0, see LICENSE and NOTICE
"""Failure classification: how long a provider is skipped."""

from __future__ import annotations

import unittest

from zeo_core.voice.failures import (
    FailureKind,
    classify,
    cooldown_for,
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


class _ClientError(Exception):
    """T5: a stand-in for botocore.exceptions.ClientError (not a Core dependency)."""

    def __init__(self, response: dict, operation_name: str) -> None:
        super().__init__(f"An error occurred when calling {operation_name}")
        self.response = response
        self.operation_name = operation_name


class TestFailureClassification(unittest.TestCase):
    """The classifier decides how long a provider is skipped, so it has to be right."""

    def test_rejected_key_is_permanent(self):
        for status in (401, 403):
            v = classify(http_error(status, "Unauthorized"))
            self.assertEqual(v.kind, FailureKind.AUTH)
            self.assertTrue(v.permanent, status)

    def test_malformed_request_is_permanent(self):
        # Retrying a request the vendor cannot parse is never going to work.
        v = classify(http_error(400, "Bad Request"))
        self.assertEqual(v.kind, FailureKind.CONFIG)
        self.assertTrue(v.permanent)

    def test_out_of_credits_is_a_long_park_not_a_disable(self):
        v = classify(http_error(402, "Payment Required"))
        self.assertEqual(v.kind, FailureKind.QUOTA)
        self.assertFalse(v.permanent)
        self.assertGreater(cooldown_for(v, 30), 60)

    def test_rate_limit_honours_retry_after(self):
        v = classify(http_error(429, "Too Many Requests", {"Retry-After": "7"}))
        self.assertEqual(v.kind, FailureKind.RATE_LIMIT)
        self.assertEqual(cooldown_for(v, 30), 7.0)

    def test_429_carrying_quota_text_is_quota_not_rate_limit(self):
        # Vendors dress billing up as throttling; the body is the giveaway.
        v = classify(http_error(429, "You exceeded your current quota"))
        self.assertEqual(v.kind, FailureKind.QUOTA)

    def test_aws_throttling_arrives_as_400_and_must_not_disable(self):
        # The regression this branch exists for: a status-only reading would
        # permanently disable a vendor that only asked us to slow down.
        # T5: botocore is not a Core dependency; the stand-in carries the same
        # `response` mapping the classifier reads from a botocore ClientError.
        exc = _ClientError(
            {
                "Error": {"Code": "ThrottlingException"},
                "ResponseMetadata": {"HTTPStatusCode": 400},
            },
            "SynthesizeSpeech",
        )
        v = classify(exc)
        self.assertEqual(v.kind, FailureKind.RATE_LIMIT)
        self.assertFalse(v.permanent)

    def test_status_is_read_from_the_message_when_wrapped(self):
        # Rasa's own wrapper keeps the number but loses the attribute, and the
        # word "Connection" used to win, filing a permanent config error as a
        # transient blip retried every thirty seconds.
        v = classify(RuntimeError("Connection to Rime TTS failed with status 400"))
        self.assertEqual(v.kind, FailureKind.CONFIG)
        self.assertTrue(v.permanent)

    def test_a_number_that_is_not_a_status_is_not_read_as_one(self):
        v = classify(RuntimeError("failed after 500 attempts"))
        self.assertEqual(v.kind, FailureKind.UNKNOWN)

    def test_classify_never_raises_even_on_a_broken_exception(self):
        class Hostile(Exception):
            def __str__(self):
                raise ValueError("nope")

        # A classifier that dies takes the call with it.
        self.assertIsInstance(classify(Hostile()), type(classify(RuntimeError("x"))))
