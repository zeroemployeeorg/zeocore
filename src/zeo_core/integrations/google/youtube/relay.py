"""The custody relay: when YouTube wants a token on every chunk.

Google's resumable-upload guide shows ``Authorization`` on each chunk ``PUT``. If
YouTube refuses an upload link on its own (401/403), the executor switches that
step to ``RelayByteHttp``. Each chunk then goes to ZEOconnect, which checks the seal
it issued with the session, adds the channel's token inside custody, and forwards
the chunk to YouTube. The device still never holds the token, and ZEOconnect still
stores no link.

``RelayByteHttp`` is a ``ByteHttp``, so ``ResumableTransfer`` is unchanged: the
same probe first, the same exact-byte resume, the same backoff. Relay chunks are at
most 4 MiB, the hosting platform's request limit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx
from pydantic import JsonValue

from zeo_core.integrations.hosted.client import HostedClientError

#: The relay's chunk ceiling (a multiple of 256 KiB).
RELAY_CHUNK_BYTES = 4 * 1024 * 1024


@runtime_checkable
class YouTubeRelayTransport(Protocol):
    def relay_youtube_chunk(
        self,
        *,
        connection_id: str,
        link: str,
        seal: str,
        content_range: str,
        content_type: str,
        body: bytes,
    ) -> dict[str, JsonValue]: ...


@dataclass(frozen=True)
class _RelayResponse:
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    text: str = ""


class RelayByteHttp:
    """Send upload chunks through ZEOconnect instead of straight to YouTube."""

    def __init__(
        self,
        transport: YouTubeRelayTransport,
        *,
        connection_id: str,
        seal: str,
        mime_type: str,
    ) -> None:
        self._transport = transport
        self._connection = connection_id
        self._seal = seal
        #: Sent with every request, probes included: the relay checks it.
        self._mime = mime_type

    def put(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout: float
    ) -> _RelayResponse:
        del timeout  # the relay endpoint has its own
        if len(content) > RELAY_CHUNK_BYTES:
            raise ValueError("relay chunks are at most 4 MiB")
        try:
            result = self._transport.relay_youtube_chunk(
                connection_id=self._connection,
                link=url,
                seal=self._seal,
                content_range=headers["Content-Range"],
                content_type=headers.get("Content-Type", self._mime),
                body=content,
            )
        except HostedClientError as error:
            if "refused" in str(error):
                # ZEOconnect refused the relay itself (seal, link or connection).
                return _RelayResponse(403, text=_reason("relay_refused"))
            # Unavailable or pending: transient, so the transfer backs off.
            raise httpx.ConnectError(str(error)) from None
        status = result.get("status")
        if not isinstance(status, int):
            raise httpx.ConnectError("relay answer without a status")
        response_headers: dict[str, str] = {}
        if isinstance(result.get("range"), str):
            response_headers["Range"] = str(result["range"])
        resource = result.get("resource")
        if isinstance(resource, dict):
            return _RelayResponse(status, response_headers, json.dumps(resource))
        return _RelayResponse(
            status, response_headers, _reason(str(result.get("reason") or ""))
        )


def _reason(reason: str) -> str:
    return json.dumps({"error": {"errors": [{"reason": reason}]}}) if reason else ""


__all__ = ["RELAY_CHUNK_BYTES", "RelayByteHttp", "YouTubeRelayTransport"]
