"""RelayByteHttp and the device transport's relay call."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from zeo_core.integrations.google.youtube.relay import RELAY_CHUNK_BYTES, RelayByteHttp
from zeo_core.integrations.google.youtube.transfer import ByteResponse
from zeo_core.integrations.hosted.client import HostedClientError
from zeo_core.integrations.hosted.pairing import (
    DeviceSession,
    InMemorySecureSessionStore,
)
from zeo_core.integrations.hosted.transport import (
    ZEOCONNECT_PROTOCOL_HEADER,
    ZEOCONNECT_PROTOCOL_VERSION,
    ZEOconnectHTTPTransport,
)

LINK = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=u1"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class _Transport:
    def __init__(self, answer: dict[str, JsonValue] | Exception) -> None:
        self.answer = answer
        self.calls: list[dict[str, object]] = []

    def relay_youtube_chunk(
        self,
        *,
        connection_id: str,
        link: str,
        seal: str,
        content_range: str,
        content_type: str,
        body: bytes,
    ) -> dict[str, JsonValue]:
        self.calls.append(
            {
                "connection_id": connection_id,
                "link": link,
                "seal": seal,
                "content_range": content_range,
                "content_type": content_type,
                "body": body,
            }
        )
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _put(transport: _Transport) -> ByteResponse:
    return RelayByteHttp(
        transport, connection_id="con_youtube0001", seal="s", mime_type="video/mp4"
    ).put(
        LINK,
        headers={"Content-Range": "bytes 0-3/10", "Content-Type": "video/mp4"},
        content=b"abcd",
        timeout=1,
    )


def test_relay_translates_youtube_answers() -> None:
    incomplete = _put(
        _Transport(
            {"status": 308, "range": "bytes=0-3", "resource": None, "reason": ""}
        )
    )
    assert (incomplete.status_code, dict(incomplete.headers)) == (
        308,
        {"Range": "bytes=0-3"},
    )
    done = _put(
        _Transport(
            {"status": 200, "range": None, "resource": {"id": "v1"}, "reason": ""}
        )
    )
    assert json.loads(done.text) == {"id": "v1"}
    expired = _put(
        _Transport(
            {"status": 404, "range": None, "resource": None, "reason": "notFound"}
        )
    )
    assert expired.status_code == 404 and "notFound" in expired.text


def test_relay_refusal_is_terminal_and_outage_is_transient() -> None:
    refused = _put(_Transport(HostedClientError("hosted request was refused")))
    assert refused.status_code == 403
    with pytest.raises(httpx.TransportError):
        _put(_Transport(HostedClientError("hosted transport is unavailable")))
    with pytest.raises(httpx.TransportError):
        _put(_Transport({"range": None}))
    with pytest.raises(ValueError, match="4 MiB"):
        RelayByteHttp(
            _Transport({}), connection_id="c", seal="s", mime_type="video/mp4"
        ).put(
            LINK,
            headers={"Content-Range": "x"},
            content=b"\0" * (RELAY_CHUNK_BYTES + 1),
            timeout=1,
        )


def _device(handler: httpx.MockTransport) -> ZEOconnectHTTPTransport:
    store = InMemorySecureSessionStore()
    store.save(
        DeviceSession(
            device_id="dev1",
            access_token=SecretStr("device-access"),
            refresh_token=SecretStr("device-refresh"),
            access_expires_at=NOW + timedelta(minutes=15),
            refresh_expires_at=NOW + timedelta(days=30),
        )
    )
    client = httpx.Client(
        base_url="https://connect.zeroemployee.org",
        transport=handler,
        follow_redirects=False,
    )
    return ZEOconnectHTTPTransport(
        session_store=store, http_client=client, clock=lambda: NOW
    )


def test_transport_posts_the_chunk_with_its_binding() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            json={"status": 308, "range": "bytes=0-3", "resource": None, "reason": ""},
        )

    result = _device(httpx.MockTransport(handler)).relay_youtube_chunk(
        connection_id="con_youtube0001",
        link=LINK,
        seal="seal",
        content_range="bytes 0-3/10",
        content_type="video/mp4",
        body=b"abcd",
    )
    assert result["status"] == 308
    request = seen[0]
    assert (
        request.url.path == "/v1/youtube/uploads:relay" and request.content == b"abcd"
    )
    assert request.headers["Authorization"] == "Bearer device-access"
    assert (
        request.headers["X-Zeo-Upload-Link"] == LINK
        and request.headers["X-Zeo-Relay-Seal"] == "seal"
    )
    assert request.headers["X-Zeo-Content-Range"] == "bytes 0-3/10"


def test_transport_refusals_and_limits() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            json={},
        )

    with pytest.raises(HostedClientError, match="refused"):
        _device(httpx.MockTransport(refuse)).relay_youtube_chunk(
            connection_id="c",
            link=LINK,
            seal="s",
            content_range="bytes */1",
            content_type="video/mp4",
            body=b"",
        )
    with pytest.raises(HostedClientError, match="client limit"):
        _device(httpx.MockTransport(refuse)).relay_youtube_chunk(
            connection_id="c",
            link=LINK,
            seal="s",
            content_range="x",
            content_type="video/mp4",
            body=b"\0" * (RELAY_CHUNK_BYTES + 1),
        )

    def bad_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            json=[1],
        )

    with pytest.raises(HostedClientError, match="shape"):
        _device(httpx.MockTransport(bad_shape)).relay_youtube_chunk(
            connection_id="c",
            link=LINK,
            seal="s",
            content_range="bytes */1",
            content_type="video/mp4",
            body=b"",
        )

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            json={"detail": "provider is unavailable"},
        )

    with pytest.raises(HostedClientError, match="unavailable"):
        _device(httpx.MockTransport(unavailable)).relay_youtube_chunk(
            connection_id="c",
            link=LINK,
            seal="s",
            content_range="bytes */1",
            content_type="video/mp4",
            body=b"",
        )

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(HostedClientError, match="unavailable"):
        _device(httpx.MockTransport(down)).relay_youtube_chunk(
            connection_id="c",
            link=LINK,
            seal="s",
            content_range="bytes */1",
            content_type="video/mp4",
            body=b"",
        )


def test_probes_carry_the_media_type() -> None:
    transport = _Transport(
        {"status": 308, "range": None, "resource": None, "reason": ""}
    )
    RelayByteHttp(transport, connection_id="c", seal="s", mime_type="image/png").put(
        LINK, headers={"Content-Range": "bytes */10"}, content=b"", timeout=1
    )
    assert transport.calls[0]["content_type"] == "image/png"
