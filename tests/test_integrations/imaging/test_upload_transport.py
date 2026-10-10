"""The proposed 1.3.0 input upload on the real HTTP transport."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from tests.test_integrations.hosted.test_transport import (
    NOW,
    response,
    session,
    transport,
)
from tests.test_integrations.imaging.images import png
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedOperationRequest,
    HostedUnavailableError,
    HostedUnreachableError,
)
from zeo_core.integrations.hosted.transport import (
    ZEOCONNECT_CAPABILITIES_HEADER,
    ZEOCONNECT_PROTOCOL_HEADER,
    ZEOconnectHTTPTransport,
)

CONTENT = png(64, 64)
DIGEST = "sha256:" + hashlib.sha256(CONTENT).hexdigest()
ANSWER = {
    "artifact_id": "art_in_12345678",
    "content_sha256": DIGEST,
    "size_bytes": len(CONTENT),
    "media_type": "image/png",
    "expires_at": "2026-09-07T03:00:00Z",
}


def _hosted(
    status: int, body: object, headers: dict[str, str] | None = None
) -> tuple[list[httpx.Request], ZEOconnectHTTPTransport]:
    sent: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        return response(status, body, request=http_request, headers=headers)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    return sent, hosted


def test_the_upload_sends_raw_bytes_with_their_digest_and_connection() -> None:
    sent, hosted = _hosted(200, ANSWER)
    artifact = hosted.upload_artifact(
        connection_id="con_gemini_12345678", content=CONTENT, media_type="image/png"
    )
    (request,) = sent
    assert request.method == "POST"
    assert request.url.path == "/v1/artifacts:upload"
    assert request.content == CONTENT
    assert request.headers["Content-Type"] == "image/png"
    assert request.headers["X-Zeo-Connection"] == "con_gemini_12345678"
    assert request.headers["X-Zeo-Content-SHA256"] == DIGEST.removeprefix("sha256:")
    assert request.headers[ZEOCONNECT_PROTOCOL_HEADER] == "1"
    assert request.headers["Authorization"].startswith("Bearer ")
    assert artifact.artifact_id == "art_in_12345678"
    assert artifact.content_sha256 == DIGEST


def test_every_request_declares_billed_computation() -> None:
    sent, hosted = _hosted(200, ANSWER)
    hosted.upload_artifact(
        connection_id="con_gemini_12345678", content=CONTENT, media_type="image/png"
    )
    tokens = {
        token.strip()
        for token in sent[0].headers[ZEOCONNECT_CAPABILITIES_HEADER].split(",")
    }
    assert tokens == {"stopped-code", "expected-binding", "billed-computation"}


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (b"", "image/png"),
        (CONTENT, "image/gif"),
        (b"\0" * (10 * 1024 * 1024 + 1), "image/png"),
    ],
)
def test_a_bad_upload_is_refused_before_anything_is_sent(
    content: bytes, media_type: str
) -> None:
    sent, hosted = _hosted(200, ANSWER)
    with pytest.raises(HostedClientError):
        hosted.upload_artifact(
            connection_id="con_gemini_12345678", content=content, media_type=media_type
        )
    assert sent == []


def test_a_headered_503_is_an_outage_and_a_refusal_is_a_refusal() -> None:
    _, hosted = _hosted(503, {"detail": "unavailable"})
    with pytest.raises(HostedUnavailableError):
        hosted.upload_artifact(
            connection_id="con_gemini_12345678", content=CONTENT, media_type="image/png"
        )
    _, hosted = _hosted(400, {"detail": "content type does not match the bytes"})
    with pytest.raises(HostedClientError, match="refused"):
        hosted.upload_artifact(
            connection_id="con_gemini_12345678", content=CONTENT, media_type="image/png"
        )


def test_an_answer_without_its_fields_is_invalid() -> None:
    _, hosted = _hosted(200, {"artifact_id": "art_in_12345678"})
    with pytest.raises(HostedClientError, match="invalid"):
        hosted.upload_artifact(
            connection_id="con_gemini_12345678", content=CONTENT, media_type="image/png"
        )


def test_billed_calls_wait_longer_and_others_keep_the_default() -> None:
    timeouts: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        timeouts[http_request.url.path] = http_request.extensions["timeout"]
        return response(
            200,
            {"status": "refused", "execution_id": "exe_x"},
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    for operation in ("gemini.image.generate", "google.drive.file.download"):
        hosted.invoke(
            HostedOperationRequest(
                connection_id="con_gemini_12345678",
                operation_id=operation,
                arguments={},
                idempotency_key="k",
            )
        )
    assert timeouts["/v1/operations/gemini.image.generate:invoke"]["read"] == 180.0  # type: ignore[index]
    assert timeouts["/v1/operations/google.drive.file.download:invoke"]["read"] != 180.0  # type: ignore[index]


@pytest.mark.parametrize(
    ("error", "may_have_arrived"),
    [
        (httpx.ConnectError("refused"), False),
        (httpx.ConnectTimeout("slow"), False),
        (httpx.ReadTimeout("slow"), True),
        (httpx.RemoteProtocolError("dropped"), True),
    ],
)
def test_unreachable_says_whether_the_request_may_have_arrived(
    error: httpx.TransportError, may_have_arrived: bool
) -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        raise error

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(HostedUnreachableError) as caught:
        hosted.invoke(
            HostedOperationRequest(
                connection_id="con_gemini_12345678",
                operation_id="gemini.image.generate",
                arguments={},
                idempotency_key="k",
            )
        )
    assert caught.value.may_have_arrived is may_have_arrived
