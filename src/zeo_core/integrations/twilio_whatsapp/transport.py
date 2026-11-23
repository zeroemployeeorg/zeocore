"""Fixed-origin Twilio Messages API boundary.

The origin is fixed; a caller may inject an HTTP transport, never an origin.
Errors carry a code and the Twilio error number only: never the auth token, the
account sid in a URL, or the response body. A send is never retried: a repeat
would message the user twice.
"""

from __future__ import annotations

from typing import Any

import httpx

from .models import TwilioCredentials

_ORIGIN = "https://api.twilio.com"
_UNKNOWN_EFFECT = frozenset(
    {"timeout", "network", "invalid_response", "server_error", "unexpected_status"}
)
_HTTP_OK = (200, 201)
_CLIENT_ERROR_MIN, _CLIENT_ERROR_MAX = 400, 499
_SERVER_ERROR_MIN = 500


class TwilioAPIError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        twilio_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.twilio_code = twilio_code
        #: True when the message may or may not have been sent: a timeout, a network
        #: failure, an unusable 2xx body, a server error or an unexpected status.
        #: The caller must reconcile by looking the message up, never resend. Only
        #: a definite client-side rejection (4xx) is False.
        self.effect_unknown = code in _UNKNOWN_EFFECT


class TwilioTransport:
    def __init__(
        self,
        credentials: TwilioCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not 0 < timeout <= 120:
            raise ValueError("Twilio timeout must be between zero and 120 seconds")
        self._sid = credentials.account_sid
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            auth=credentials.basic_auth(),
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def post_message(self, form: dict[str, str]) -> dict[str, Any]:
        url = f"{_ORIGIN}/2010-04-01/Accounts/{self._sid}/Messages.json"
        try:
            response = self._http.post(url, data=form)
        except httpx.TimeoutException:
            raise TwilioAPIError("timeout", "Twilio request timed out") from None
        except httpx.HTTPError:
            raise TwilioAPIError("network", "Twilio request failed") from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        status = response.status_code
        if status in _HTTP_OK:
            sid = payload.get("sid") if isinstance(payload, dict) else None
            if isinstance(payload, dict) and isinstance(sid, str) and sid:
                return payload
            raise TwilioAPIError(
                "invalid_response",
                "Twilio response had no usable message sid",
                status_code=status,
            )
        twilio_code = (
            payload.get("code")
            if isinstance(payload, dict) and isinstance(payload.get("code"), int)
            else None
        )
        if _CLIENT_ERROR_MIN <= status <= _CLIENT_ERROR_MAX:
            code = {
                400: "bad_request",
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                429: "rate_limited",
            }.get(status, "client_error")
        elif status >= _SERVER_ERROR_MIN:
            code = "server_error"
        else:
            code = "unexpected_status"
        raise TwilioAPIError(
            code,
            f"Twilio send failed with HTTP {status}",
            status_code=status,
            twilio_code=twilio_code,
        )
