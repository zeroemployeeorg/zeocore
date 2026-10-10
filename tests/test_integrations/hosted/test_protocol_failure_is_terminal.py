"""A Broker response without its protocol header is a terminal protocol failure.

The council's E5 ruling (org #787): on every Broker-protocol response, HTTP
errors included, a missing or incompatible ``ZEOconnect-Protocol-Version`` ends
that operation. Nothing retries, refreshes, reconciles or replays around it,
and a headerless refusal is never read as a stop. A recognized stop carries
the header and the agreed shape.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from tests.test_integrations.hosted.test_transport import (
    NOW,
    request,
    response,
    session,
    transport,
)
from zeo_core.integrations.google.youtube.relay import RelayByteHttp
from zeo_core.integrations.google.youtube.transfer import (
    FileIdentity,
    ResumableTransfer,
    TransferOutcome,
    TransferState,
)
from zeo_core.integrations.hosted import (
    ZEOCONNECT_PROTOCOL_HEADER,
    HostedClientError,
    HostedStoppedError,
    InMemorySecureSessionStore,
    PairingPendingError,
)

Answer = Callable[[int, object, httpx.Request], httpx.Response]
# A missing header: not from the Broker. Another value, or two: a mismatch.
PROTOCOL_FAILURE = "did not come from the Broker|protocol version is incompatible"
STOP = {"code": "stopped", "control": "dispatch", "scope": "global"}
LINK = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=u1"


def headerless(
    status: int, payload: object, http_request: httpx.Request
) -> httpx.Response:
    return httpx.Response(status, json=payload, request=http_request)


def wrong_version(
    status: int, payload: object, http_request: httpx.Request
) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={ZEOCONNECT_PROTOCOL_HEADER: "2"},
        request=http_request,
    )


def doubled(
    status: int, payload: object, http_request: httpx.Request
) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers=[(ZEOCONNECT_PROTOCOL_HEADER, "1"), (ZEOCONNECT_PROTOCOL_HEADER, "1")],
        request=http_request,
    )


BAD_HEADERS = pytest.mark.parametrize(
    "answer", [headerless, wrong_version, doubled], ids=["missing", "v2", "twice"]
)


@BAD_HEADERS
@pytest.mark.parametrize("status", [200, 403, 429, 502, 503])
def test_the_one_retried_operation_is_not_retried_on_a_protocol_failure(
    answer: Answer, status: int
) -> None:
    """``google.drive.file.download`` may be sent twice, but only on no answer."""

    sent: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        return answer(status, STOP, http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))

    with pytest.raises(HostedClientError, match=PROTOCOL_FAILURE):
        hosted.invoke(request("google.drive.file.download"))
    assert len(sent) == 1


def test_a_recognized_stop_needs_the_header_and_is_still_sent_once() -> None:
    sent: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        return response(403, STOP, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))

    with pytest.raises(HostedStoppedError) as stopped:
        hosted.invoke(request("google.drive.file.download"))
    assert (stopped.value.control, stopped.value.scope) == ("dispatch", "global")
    assert len(sent) == 1


@BAD_HEADERS
def test_a_headerless_stop_body_is_never_read_as_a_refusal(answer: Answer) -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return answer(403, STOP, http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))

    with pytest.raises(HostedClientError) as caught:
        hosted.invoke(request())
    assert "refused" not in str(caught.value)


@BAD_HEADERS
def test_a_protocol_failure_on_refresh_is_not_retried_and_keeps_the_session(
    answer: Answer,
) -> None:
    sent: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request.url.path)
        return answer(401, {"code": "invalid_grant"}, http_request)

    store = InMemorySecureSessionStore()
    stale = session(NOW - timedelta(minutes=20))
    store.save(stale)
    hosted, _ = transport(httpx.MockTransport(handler), store)

    with pytest.raises(HostedClientError, match=PROTOCOL_FAILURE):
        hosted.invoke(request())
    assert sent == ["/v1/device/token/refresh"]
    assert store.load() == stale


@BAD_HEADERS
def test_a_headerless_pending_answer_ends_pairing(answer: Answer) -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path == "/v1/device/authorizations":
            return response(
                200,
                {
                    "pairing_id": "pair-1",
                    "device_code": "device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://connect.zeroemployee.org/pair",
                    "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
                    "interval_seconds": 5,
                },
                request=http_request,
            )
        return answer(428, {"code": "authorization_pending"}, http_request)

    hosted, _ = transport(httpx.MockTransport(handler))
    challenge = hosted.begin_pairing(device_name="studio")

    with pytest.raises(HostedClientError, match=PROTOCOL_FAILURE) as caught:
        hosted.poll_pairing(challenge)
    assert not isinstance(caught.value, PairingPendingError)


def _relayed_upload(
    tmp_path: Path, answer: Answer, status: int
) -> tuple[TransferOutcome, list[httpx.Request], list[float]]:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"0123456789")
    sent: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        return answer(status, STOP, http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    sleeps: list[float] = []
    outcome = ResumableTransfer(
        url=LINK,
        file=FileIdentity(
            path=video,
            size_bytes=10,
            mtime_ms=video.stat().st_mtime_ns // 1_000_000,
        ),
        mime_type="video/mp4",
        http=RelayByteHttp(
            hosted, connection_id="con_youtube0001", seal="s", mime_type="video/mp4"
        ),
        sleep=sleeps.append,
    ).run()
    return outcome, sent, sleeps


@BAD_HEADERS
@pytest.mark.parametrize("status", [403, 502, 503, 504])
def test_a_headerless_relay_answer_ends_the_upload_as_a_failure_not_a_stop(
    tmp_path: Path, answer: Answer, status: int
) -> None:
    outcome, sent, sleeps = _relayed_upload(tmp_path, answer, status)

    assert outcome.state is TransferState.REJECTED
    assert len(sent) == 1
    assert sleeps == []


def test_a_recognized_relay_stop_is_a_refusal_after_one_request(
    tmp_path: Path,
) -> None:
    def stopped(
        status: int, payload: object, http_request: httpx.Request
    ) -> httpx.Response:
        return response(status, payload, request=http_request)

    outcome, sent, sleeps = _relayed_upload(tmp_path, stopped, 403)

    assert outcome.state is TransferState.REFUSED
    assert len(sent) == 1
    assert sleeps == []


def test_a_broker_outage_with_the_header_is_still_retried(tmp_path: Path) -> None:
    """A 503 with the header: the contract's outage, so it is retried."""

    def outage(
        status: int, payload: object, http_request: httpx.Request
    ) -> httpx.Response:
        return response(status, {"code": "unavailable"}, request=http_request)

    outcome, sent, sleeps = _relayed_upload(tmp_path, outage, 503)

    assert outcome.state is TransferState.PAUSED
    assert len(sent) > 1
    assert len(sleeps) == len(sent) - 1
