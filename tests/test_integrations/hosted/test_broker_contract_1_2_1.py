"""zeocore against ZEOconnect Broker contract 1.2.1's changed-request marker.

Pinned at zeoconnect 707bb91b (#67's merge), contract/broker-contract-v1.md,
sha256 dc2e951f. §6a.5: to a client declaring ``expected-binding``, a request
changed under a used idempotency key is marked ``request_changed_under_key``.
A read is refused as ``failed_safe``, and an effect as a 400 carrying the code.
"""

from __future__ import annotations

import httpx
import pytest

from tests.test_integrations.hosted.test_transport import (
    NOW,
    request,
    response,
    session,
    transport,
)
from zeo_core.integrations.hosted import (
    HostedClientError,
    HostedConnectionChangedError,
    HostedOperationResponse,
    HostedRequestChangedError,
    ZEOconnectHTTPTransport,
    binding_mismatch_of,
    request_changed_of,
)
from zeo_core.integrations.hosted.client import is_outage, stop_of

EFFECT = "bluesky.post.create"
READ = "google.drive.file.download"
CONFLICT = "idempotency key conflicts with prior request"


def _failed_safe(code: str, message: str) -> HostedOperationResponse:
    return HostedOperationResponse.model_validate(
        {
            "status": "failed_safe",
            "execution_id": "observation-refused",
            "normalized_error": {"code": code, "message": message},
        }
    )


def _answer(
    status: int, body: object
) -> tuple[list[httpx.Request], ZEOconnectHTTPTransport]:
    sent: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        return response(status, body, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    return sent, hosted


# -- A changed effect: the marked 400 ---------------------------------------------


@pytest.mark.parametrize("operation", [EFFECT, READ])
def test_a_marked_conflict_is_a_changed_request_and_is_sent_once(
    operation: str,
) -> None:
    sent, hosted = _answer(
        400, {"detail": CONFLICT, "code": "request_changed_under_key"}
    )
    with pytest.raises(HostedRequestChangedError, match="needs a new key"):
        hosted.invoke(request(operation))
    assert len(sent) == 1


@pytest.mark.parametrize(
    "body",
    [
        # The 1.1 bytes, which an undeclared client receives.
        {"detail": CONFLICT},
        # An approved key, as answered to an undeclared client: a plain refusal.
        {"detail": "approval is unavailable"},
        {"detail": CONFLICT, "code": "request_changed_under_key."},
        {"detail": CONFLICT, "code": ["request_changed_under_key"]},
        ["request_changed_under_key"],
    ],
)
def test_only_the_exact_marker_is_a_changed_request(body: object) -> None:
    sent, hosted = _answer(400, body)
    with pytest.raises(HostedClientError, match="refused") as caught:
        hosted.invoke(request(EFFECT))
    assert not isinstance(caught.value, HostedRequestChangedError)
    assert len(sent) == 1


@pytest.mark.parametrize("status", [403, 409, 422])
def test_the_marker_counts_only_on_a_400(status: int) -> None:
    _, hosted = _answer(
        status, {"detail": CONFLICT, "code": "request_changed_under_key"}
    )
    with pytest.raises(HostedClientError) as caught:
        hosted.invoke(request(EFFECT))
    assert not isinstance(caught.value, HostedRequestChangedError)


def test_a_changed_connection_is_still_its_own_reason() -> None:
    _, hosted = _answer(
        400,
        {
            "detail": "kernel connection binding changed",
            "code": "request_changed_under_key",
        },
    )
    with pytest.raises(HostedConnectionChangedError):
        hosted.invoke(request(READ))


# -- A changed read: the marked failed_safe ---------------------------------------


def test_a_marked_read_refusal_is_returned_and_parsed() -> None:
    sent, hosted = _answer(
        200,
        {
            "status": "failed_safe",
            "execution_id": "observation-refused",
            "normalized_error": {
                "code": "REQUEST_REFUSED",
                "message": "request_changed_under_key",
            },
        },
    )
    answer = hosted.invoke(request(READ))
    assert request_changed_of(answer)
    assert stop_of(answer) is None
    assert binding_mismatch_of(answer) is None
    assert not is_outage(answer)
    assert len(sent) == 1


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("REQUEST_REFUSED", "request_changed_under_key:expect"),
        ("REQUEST_REFUSED", "binding_mismatch:external_identity"),
        ("PROVIDER_UNAVAILABLE", "request_changed_under_key"),
    ],
)
def test_only_the_exact_read_marker_is_a_changed_request(
    code: str, message: str
) -> None:
    assert not request_changed_of(_failed_safe(code, message))


def test_the_1_1_read_refusal_without_an_error_is_not_marked() -> None:
    answer = HostedOperationResponse.model_validate(
        {"status": "failed_safe", "execution_id": "observation-refused"}
    )
    assert not request_changed_of(answer)
