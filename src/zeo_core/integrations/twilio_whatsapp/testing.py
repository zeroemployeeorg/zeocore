"""Test double for CI: a fake Twilio and signed-webhook builders. No network, no key."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from .models import TwilioCredentials
from .signature import compute_signature

FAKE_ACCOUNT_SID = "AC" + "0" * 32
FAKE_AUTH_TOKEN = "fake-auth-token-not-real"


def fake_credentials() -> TwilioCredentials:
    return TwilioCredentials(
        account_sid=FAKE_ACCOUNT_SID, auth_token=SecretStr(FAKE_AUTH_TOKEN)
    )


class FakeTwilio:
    """`httpx.MockTransport` handler recording every send; can inject failures."""

    def __init__(
        self, *, status: int = 201, body: dict[str, Any] | None = None
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body
        self.raise_timeout = False
        self._n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_timeout:
            raise httpx.ReadTimeout("fake timeout")
        self._n += 1
        payload = self.body or {
            "sid": f"SM{self._n:032x}",
            "status": "queued",
            "error_code": None,
        }
        return httpx.Response(self.status, json=payload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def signed_webhook(
    url: str,
    params: Mapping[str, str],
    token: str = FAKE_AUTH_TOKEN,
) -> tuple[dict[str, str], str]:
    """Return (params, X-Twilio-Signature) the way Twilio would send them."""
    p = dict(params)
    return p, compute_signature(SecretStr(token), url, p)
