"""zeocore against ZEOconnect Broker contract 1.2.0's expected-binding fence (§6a).

The pin follows zeoconnect #56's merge commit. These tests cover zeocore's
side of §8: a static capability, the revision on each connection, ``expect``
on the request, "fence unsupported" that is never resent unfenced, and the
parsed ``binding_mismatch:<field>`` reason.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from tests.test_integrations.hosted.test_transport import (
    NOW,
    request,
    response,
    session,
    transport,
)
from zeo_core.integrations.hosted import (
    ZEOCONNECT_CAPABILITIES_HEADER,
    HostedClientError,
    HostedExpectedBinding,
    HostedFenceUnsupportedError,
    HostedOperationResponse,
    HostedUnavailableError,
    binding_mismatch_of,
)
from zeo_core.integrations.hosted.client import is_outage, stop_of

EFFECT = "bluesky.post.create"
CONFIRMED = {"status": "confirmed", "execution_id": "exe-1", "result": "ok"}
BINDING = HostedExpectedBinding(
    external_identity="member@example.com", connection_revision="3"
)


def _connection(**extra: object) -> dict[str, object]:
    return {
        "connection_id": "con_google_12345678",
        "provider": "google_workspace",
        "external_identity": "member@example.com",
        "status": "active",
        "operations": ["google.drive.file.download"],
        **extra,
    }


def _failed_safe(code: str, message: str) -> HostedOperationResponse:
    return HostedOperationResponse.model_validate(
        {
            "status": "failed_safe",
            "execution_id": "exe-1",
            "normalized_error": {"code": code, "message": message},
        }
    )


# -- §3 capabilities, declared statically ----------------------------------------


def test_every_request_declares_both_capabilities() -> None:
    seen: list[str | None] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(http_request.headers.get(ZEOCONNECT_CAPABILITIES_HEADER))
        if http_request.url.path == "/v1/connections":
            return response(200, [_connection()], request=http_request)
        return response(200, CONFIRMED, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    hosted.list_connections(session(NOW))
    hosted.invoke(request(EFFECT))
    assert len(seen) == 2
    for header in seen:
        assert header is not None
        assert {token.strip() for token in header.split(",")} == {
            "stopped-code",
            "expected-binding",
        }


# -- §6a.3 the connection revision -----------------------------------------------


def test_the_listing_carries_the_connection_revision() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(
            200, [_connection(connection_revision="3")], request=http_request
        )

    hosted, store = transport(httpx.MockTransport(handler))
    (summary,) = hosted.list_connections(session(NOW))
    assert summary.connection_revision == "3"
    assert summary.expected_binding() == BINDING


@pytest.mark.parametrize("revision", ["0", "03", "-1", "1.0", "", 3, "1" * 20])
def test_a_malformed_revision_refuses_the_listing(revision: object) -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(
            200, [_connection(connection_revision=revision)], request=http_request
        )

    hosted, _ = transport(httpx.MockTransport(handler))
    with pytest.raises(HostedClientError, match="connection response is invalid"):
        hosted.list_connections(session(NOW))


def test_without_a_revision_a_fence_is_unsupported_never_unfenced() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(200, [_connection()], request=http_request)

    hosted, _ = transport(httpx.MockTransport(handler))
    (summary,) = hosted.list_connections(session(NOW))
    assert summary.connection_revision is None
    with pytest.raises(HostedFenceUnsupportedError):
        summary.expected_binding()


# -- §6a.2 expect on the request -------------------------------------------------


def test_expect_requires_both_members_and_nothing_else() -> None:
    with pytest.raises(ValidationError):
        HostedExpectedBinding.model_validate({"external_identity": "a"})
    with pytest.raises(ValidationError):
        HostedExpectedBinding.model_validate(
            {"external_identity": "a", "connection_revision": "1", "extra": "x"}
        )
    with pytest.raises(ValidationError):
        HostedExpectedBinding(external_identity="a", connection_revision="01")


def test_expect_is_sent_when_fenced_and_absent_never_null_otherwise() -> None:
    bodies: list[dict[str, object]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        return response(200, CONFIRMED, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    hosted.invoke(request(EFFECT))
    hosted.invoke(request(EFFECT).model_copy(update={"expect": BINDING}))
    assert "expect" not in bodies[0]
    assert bodies[1]["expect"] == {
        "external_identity": "member@example.com",
        "connection_revision": "3",
    }


def test_a_422_to_a_fenced_invoke_is_fence_unsupported_and_sent_once() -> None:
    sent: list[dict[str, object]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(http_request.content))
        return response(422, {"detail": "request is invalid"}, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    fenced = request("google.drive.file.download").model_copy(
        update={"expect": BINDING}
    )
    with pytest.raises(HostedFenceUnsupportedError):
        hosted.invoke(fenced)
    assert len(sent) == 1
    assert "expect" in sent[0]


def test_a_422_to_an_unfenced_invoke_stays_a_refusal() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(422, {"detail": "request is invalid"}, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(HostedClientError, match="refused") as caught:
        hosted.invoke(request(EFFECT))
    assert not isinstance(caught.value, HostedFenceUnsupportedError)


# -- §6a answers -----------------------------------------------------------------


@pytest.mark.parametrize("field", ["external_identity", "connection_revision"])
def test_a_mismatch_names_the_field_and_is_not_a_stop(field: str) -> None:
    answer = _failed_safe("REQUEST_REFUSED", f"binding_mismatch:{field}")
    assert binding_mismatch_of(answer) == field
    assert stop_of(answer) is None
    assert not is_outage(answer)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("REQUEST_REFUSED", "binding_mismatch:display_name"),
        ("REQUEST_REFUSED", "binding_mismatch"),
        ("REQUEST_REFUSED", "stopped:dispatch:global"),
        ("PROVIDER_UNAVAILABLE", "binding_mismatch:external_identity"),
    ],
)
def test_only_the_exact_mismatch_shape_is_a_mismatch(code: str, message: str) -> None:
    assert binding_mismatch_of(_failed_safe(code, message)) is None


def test_binding_unavailable_is_an_outage_never_a_mismatch() -> None:
    answer = _failed_safe("PROVIDER_UNAVAILABLE", "binding_unavailable")
    assert is_outage(answer)
    assert binding_mismatch_of(answer) is None


def test_a_precheck_binding_503_is_unavailable() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(503, {"detail": "binding is unavailable"}, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(HostedUnavailableError):
        hosted.invoke(request(EFFECT).model_copy(update={"expect": BINDING}))


def test_the_verified_binding_is_read_from_the_receipt() -> None:
    answer = HostedOperationResponse.model_validate(
        {
            **CONFIRMED,
            "receipt": {
                "binding": {
                    "connection_id": "con_google_12345678",
                    "external_identity": "member@example.com",
                    "connection_revision": "3",
                    "connector_revision": "google-gmail-read@1",
                }
            },
        }
    )
    assert answer.receipt is not None
    assert answer.receipt["binding"] == {
        "connection_id": "con_google_12345678",
        "external_identity": "member@example.com",
        "connection_revision": "3",
        "connector_revision": "google-gmail-read@1",
    }
