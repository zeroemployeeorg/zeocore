"""Fixed-origin, read-only Revolut Business HTTP boundary with no implicit retries."""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr

from .models import RevolutEnvironment

_ORIGINS = {
    RevolutEnvironment.PRODUCTION: "https://b2b.revolut.com",
    RevolutEnvironment.SANDBOX: "https://sandbox-b2b.revolut.com",
}
_API = "/api/1.0"
_ID = r"[A-Za-z0-9_-]{1,100}"
# Every path this integration may request. A path parameter admits no "/",
# "?", "#", "%" or "." characters, so no caller value can add a segment, a
# query or a traversal. Reads only: there is no method other than GET here.
_ROUTES = tuple(
    re.compile(pattern)
    for pattern in (
        r"/accounts",
        r"/transactions",
        r"/expenses",
        rf"/expenses/{_ID}",
        rf"/expenses/{_ID}/receipts/{_ID}/content",
        r"/label-groups",
        rf"/label-groups/{_ID}/labels",
    )
)
MAX_UPSTREAM_BYTES = 8 * 1024 * 1024

_private_request: ContextVar[bool] = ContextVar(
    "revolut_private_request", default=False
)


class _PrivateHTTPLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _private_request.get()


_private_filter = _PrivateHTTPLogs()


def _protect_http_logs() -> None:
    # Filter only this request's standard library HTTP logs. Other callers and
    # threads retain their logging. Host tracing/injected transports remain a
    # separate redaction responsibility.
    for name in (
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    ):
        logging.getLogger(name).addFilter(_private_filter)


class RevolutAPIError(RuntimeError):
    """Safe diagnostic: never retain provider bodies, request URLs or credentials."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class RevolutTransport:
    """A caller may inject an HTTP transport, never a provider base URL.

    The access token is supplied by the caller for the life of this object.
    Signing, exchange, refresh and custody belong to the credential owner.
    """

    def __init__(
        self,
        access_token: SecretStr,
        *,
        environment: RevolutEnvironment,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not access_token.get_secret_value().strip():
            raise ValueError("Revolut access token is required")
        if not 0 < timeout <= 120:
            raise ValueError("Revolut timeout must be between zero and 120 seconds")
        _protect_http_logs()
        self._token = access_token
        self._origin = _ORIGINS[RevolutEnvironment(environment)]
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def get(
        self, path: str, *, params: dict[str, str | int] | None = None
    ) -> list[dict[str, Any]]:
        data = self._json(path, params)
        if not isinstance(data, list) or not all(isinstance(i, dict) for i in data):
            raise RevolutAPIError(
                "RESPONSE", "Revolut returned an unexpected response shape"
            )
        return data

    def get_object(
        self, path: str, *, params: dict[str, str | int] | None = None
    ) -> dict[str, Any]:
        data = self._json(path, params)
        if not isinstance(data, dict):
            raise RevolutAPIError(
                "RESPONSE", "Revolut returned an unexpected response shape"
            )
        return data

    def get_bytes(self, path: str, *, max_bytes: int) -> tuple[bytes, str]:
        """A file body, bounded while it is read; never parsed, never logged."""

        _require_route(path)
        status, retry, content, media_type = self._fetch(
            path, None, accept="*/*", limit=max_bytes
        )
        if status != 200:
            raise _refusal(status, retry)
        if len(content) > max_bytes:
            raise RevolutAPIError(
                "RESPONSE_TOO_LARGE",
                "Revolut file exceeded the size limit; nothing was kept",
            )
        if self._token.get_secret_value().encode() in content:
            raise RevolutAPIError(
                "RESPONSE", "Revolut response repeated the request credential"
            )
        return content, media_type

    def _json(self, path: str, params: dict[str, str | int] | None) -> object:
        _require_route(path)
        status, retry, content, _ = self._fetch(
            path, params, accept="application/json", limit=MAX_UPSTREAM_BYTES
        )
        if status != 200:
            raise _refusal(status, retry)
        if len(content) > MAX_UPSTREAM_BYTES:
            raise RevolutAPIError(
                "RESPONSE_TOO_LARGE",
                "Revolut response exceeded the upstream size limit; nothing was kept",
            )
        # Defence in depth for this one credential, not a general secret scan.
        if self._token.get_secret_value().encode() in content:
            raise RevolutAPIError(
                "RESPONSE", "Revolut response repeated the request credential"
            )
        try:
            # Amounts arrive as JSON numbers; binary floats would corrupt money.
            return json.loads(content, parse_float=Decimal)
        except ValueError:
            raise RevolutAPIError(
                "RESPONSE", "Revolut returned an invalid JSON response"
            ) from None

    def _fetch(
        self,
        path: str,
        params: dict[str, str | int] | None,
        *,
        accept: str,
        limit: int,
    ) -> tuple[int, str, bytes, str]:
        content = bytearray()
        private_token = _private_request.set(True)
        try:
            with self._http.stream(
                "GET",
                self._origin + _API + path,
                headers={
                    "Authorization": "Bearer " + self._token.get_secret_value(),
                    "Accept": accept,
                },
                params=params,
            ) as response:
                status = response.status_code
                retry = response.headers.get("Retry-After", "")
                media_type = response.headers.get("Content-Type", "")
                # Bound the body before it is buffered or parsed; an error
                # body is never read at all.
                if status == 200:
                    for chunk in response.iter_bytes():
                        content += chunk
                        if len(content) > limit:
                            break
        except httpx.HTTPError:
            raise RevolutAPIError("TRANSPORT", "Revolut request failed") from None
        finally:
            _private_request.reset(private_token)
        return status, retry, bytes(content), media_type


def _require_route(path: str) -> None:
    if not any(route.fullmatch(path) for route in _ROUTES):
        raise ValueError("Revolut route is outside the read integration")


def _refusal(status: int, retry: str) -> RevolutAPIError:
    # Categories only. A 401 does not establish expiry and a 403 does not
    # establish revocation; the credential owner decides that.
    code, message = {
        401: ("AUTHENTICATION", "Revolut did not accept the access token"),
        403: ("ACCESS", "Revolut refused access to the requested data"),
        404: ("NOT_FOUND", "Revolut resource was not found"),
        429: (
            "RATE_LIMIT",
            "Revolut rate limit reached; no automatic retry was attempted",
        ),
    }.get(status, ("HTTP", "Revolut rejected the read request"))
    return RevolutAPIError(
        code,
        message,
        status_code=status,
        retry_after_seconds=int(retry)
        if retry.isascii() and retry.isdigit() and len(retry) < 9
        else None,
    )
