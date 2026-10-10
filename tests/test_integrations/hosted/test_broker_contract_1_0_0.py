"""zeocore against ZEOconnect Broker contract 1.0.0 (zeoconnect #35, 24759346).

One test per conformance item: the Broker origin, capability declaration,
``403 stopped``, one refresh on a 401, the 426 upgrade message, the off-network
wording, unknown response fields, and the council's E7 rule that a Broker 503
is an outage, never a stop and never a retry for an effect.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from tests.test_integrations.hosted.test_transport import (
    NOW,
    request,
    response,
    session,
    transport,
)
from zeo_core.contracts.connections import NormalizedErrorCode
from zeo_core.integrations.hosted import (
    ZEOCONNECT_CAPABILITIES_HEADER,
    ZEOCONNECT_PRODUCTION_ORIGIN,
    ZEOCONNECT_PROTOCOL_HEADER,
    HostedClientError,
    HostedOperationResponse,
    HostedOperationStatus,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
    HostedUpgradeRequiredError,
    InMemorySecureSessionStore,
    ZEOconnectHTTPTransport,
)
from zeo_core.integrations.hosted.client import is_outage, stop_of
from zeo_core.integrations.hosted.pairing import DeviceSession

EFFECT = "bluesky.post.create"
SAFE_READ = "google.drive.file.download"
CONFIRMED = {"status": "confirmed", "execution_id": "exe-1", "result": "ok"}
ROTATED = {
    "device_id": "dev_12345678-1234-4234-9234-123456789012",
    "access_token": "rotated-access",
    "refresh_token": "rotated-refresh",
    "access_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
    "refresh_expires_at": (NOW + timedelta(days=30)).isoformat(),
}


def _invoke_path(operation: str) -> str:
    return f"/v1/operations/{operation}:invoke"


# -- §2 origins -------------------------------------------------------------------


def test_the_default_origin_is_the_tailnet_broker() -> None:
    assert ZEOCONNECT_PRODUCTION_ORIGIN == "https://broker.connect.zeo.ac"
    store = InMemorySecureSessionStore()
    ZEOconnectHTTPTransport(session_store=store).close()
    for retired in (
        "https://connect.zeroemployee.org",
        # WEB serves browsers; a device client never calls it.
        "https://connect.zeo.ac",
        "https://broker.connect.zeo.ac/v1",
        "https://user:pw@broker.connect.zeo.ac",
    ):
        with pytest.raises(ValueError):
            ZEOconnectHTTPTransport(session_store=store, base_url=retired)


def test_off_network_wording_names_the_broker_and_refuses_a_local_fallback() -> None:
    def down(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=http_request)

    hosted, store = transport(httpx.MockTransport(down))
    store.save(session(NOW))
    with pytest.raises(HostedUnreachableError) as caught:
        hosted.invoke(request(EFFECT))
    message = str(caught.value)
    assert message.startswith(
        "ZEOconnect Broker https://broker.connect.zeo.ac cannot be reached"
    )
    assert "will not use local credentials" in message
    assert "select the local profile explicitly" in message


def test_a_development_origin_keeps_the_plain_unavailable_message() -> None:
    def down(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=http_request)

    store = InMemorySecureSessionStore()
    store.save(session(NOW))
    hosted = ZEOconnectHTTPTransport(
        session_store=store,
        base_url="http://127.0.0.1:8080",
        allow_development_origin=True,
        http_client=httpx.Client(
            base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(down)
        ),
        clock=lambda: NOW,
    )
    with pytest.raises(
        HostedUnreachableError, match="^hosted transport is unavailable$"
    ):
        hosted.invoke(request(EFFECT))


# -- §9, §10 capability -----------------------------------------------------------


def test_every_request_declares_the_stopped_code_capability() -> None:
    seen: list[str | None] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(http_request.headers.get(ZEOCONNECT_CAPABILITIES_HEADER))
        return response(200, CONFIRMED, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    hosted.invoke(request(EFFECT))
    assert len(seen) == 1 and seen[0] is not None
    assert "stopped-code" in {token.strip() for token in seen[0].split(",")}


def test_a_stopped_code_answer_is_read_as_a_stop() -> None:
    for code, message in (
        (NormalizedErrorCode.STOPPED, "stopped:dispatch:org-1"),
        (NormalizedErrorCode.REQUEST_REFUSED, "stopped:dispatch:org-1"),
    ):
        answer = HostedOperationResponse.model_validate(
            {
                "status": "failed_safe",
                "execution_id": "exe-1",
                "normalized_error": {"code": code.value, "message": message},
            }
        )
        stop = stop_of(answer)
        assert stop is not None and (stop.control, stop.scope) == ("dispatch", "org-1")
    plain = HostedOperationResponse.model_validate(
        {
            "status": "failed_safe",
            "execution_id": "exe-1",
            "normalized_error": {"code": "REQUEST_REFUSED", "message": "no"},
        }
    )
    assert stop_of(plain) is None


def test_unknown_response_fields_are_ignored_never_passed_through() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(
            200,
            {**CONFIRMED, "replayed": True, "added_in_1_1": {"x": 1}},
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    answer = hosted.invoke(request(EFFECT))
    assert answer.status is HostedOperationStatus.CONFIRMED
    assert "replayed" not in answer.model_dump()


def test_unknown_fields_nested_in_a_response_are_ignored_too() -> None:
    artifact = {
        "artifact_id": "art_12345678",
        "content_sha256": "sha256:" + "0" * 64,
        "size_bytes": 3,
        "media_type": "text/plain",
        "filename": "a.txt",
        "added_in_1_2": "x",
    }
    confirmed = HostedOperationResponse.model_validate(
        {"status": "confirmed", "execution_id": "exe-1", "artifact": artifact}
    )
    assert confirmed.artifact is not None
    assert "added_in_1_2" not in confirmed.artifact.model_dump()
    refused = HostedOperationResponse.model_validate(
        {
            "status": "failed_safe",
            "execution_id": "exe-2",
            "normalized_error": {
                "code": "REQUEST_REFUSED",
                "message": "stopped:dispatch:global",
                "added_in_1_2": "x",
            },
        }
    )
    stop = stop_of(refused)
    assert stop is not None
    assert (stop.control, stop.scope) == ("dispatch", "global")


def test_provider_detail_is_still_refused_in_a_nested_error() -> None:
    with pytest.raises(ValueError, match="provider detail"):
        HostedOperationResponse.model_validate(
            {
                "status": "failed_safe",
                "execution_id": "exe-3",
                "normalized_error": {
                    "code": "REQUEST_REFUSED",
                    "message": "refused",
                    "provider_detail": "raw provider text",
                },
            }
        )


# -- §9 403 stopped ---------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"code": "stopped", "control": "dispatch"},
        {"code": "stopped", "control": "Dispatch Now", "scope": "global"},
        {"code": "stopped", "control": 1, "scope": "global"},
        {"detail": "relay is unavailable"},
    ],
)
def test_a_403_that_is_not_a_stop_shape_stays_a_refusal(body: object) -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(403, body, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(HostedClientError, match="refused") as caught:
        hosted.invoke(request(EFFECT))
    assert not isinstance(caught.value, HostedStoppedError)


# -- E7: a Broker 503 ---------------------------------------------------------------


def test_a_broker_503_on_an_effect_is_one_dispatch_and_claims_nothing() -> None:
    sent: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request.url.path)
        return response(
            503, {"detail": "provider is unavailable"}, request=http_request
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(HostedUnavailableError) as caught:
        hosted.invoke(request(EFFECT))
    assert sent == [_invoke_path(EFFECT)]
    assert not isinstance(caught.value, HostedStoppedError)
    assert "refused" not in str(caught.value)


def test_the_safe_read_retries_only_when_no_response_arrived() -> None:
    answers: list[int] = []

    def flaky(http_request: httpx.Request) -> httpx.Response:
        answers.append(1)
        if len(answers) == 1:
            raise httpx.ReadError("reset", request=http_request)
        return response(200, CONFIRMED, request=http_request)

    hosted, store = transport(httpx.MockTransport(flaky))
    store.save(session(NOW))
    assert hosted.invoke(request(SAFE_READ)).status is HostedOperationStatus.CONFIRMED
    assert len(answers) == 2

    outages: list[int] = []

    def outage(http_request: httpx.Request) -> httpx.Response:
        outages.append(1)
        return response(
            503, {"detail": "provider is unavailable"}, request=http_request
        )

    hosted, store = transport(httpx.MockTransport(outage))
    store.save(session(NOW))
    with pytest.raises(HostedUnavailableError):
        hosted.invoke(request(SAFE_READ))
    # A Broker 503 is never retried by status (contract §9), even for a read.
    assert len(outages) == 1


def test_a_headerless_503_is_a_protocol_failure_and_never_retried() -> None:
    sent: list[int] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(1)
        return httpx.Response(503, json={}, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(
        HostedClientError, match="did not come from the Broker"
    ) as caught:
        hosted.invoke(request(SAFE_READ))
    assert not isinstance(caught.value, HostedUnavailableError)
    assert len(sent) == 1


# -- §9 401: refresh once, else re-pair ----------------------------------------------


def _bearer_handler(
    paths: list[str], *, invoke_401s: int, refresh_status: int = 200
) -> httpx.MockTransport:
    refused = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal refused
        paths.append(http_request.url.path)
        if http_request.url.path == "/v1/device/token/refresh":
            if refresh_status != 200:
                return response(refresh_status, {"detail": "no"}, request=http_request)
            return response(200, ROTATED, request=http_request)
        if refused < invoke_401s:
            refused += 1
            return response(
                401,
                {"detail": "device authorization is unavailable"},
                request=http_request,
            )
        assert http_request.headers["Authorization"] == "Bearer rotated-access"
        return response(200, CONFIRMED, request=http_request)

    return httpx.MockTransport(handler)


def test_a_401_refreshes_once_then_resends_the_same_request() -> None:
    paths: list[str] = []
    hosted, store = transport(_bearer_handler(paths, invoke_401s=1))
    store.save(session(NOW))
    assert hosted.invoke(request(EFFECT)).status is HostedOperationStatus.CONFIRMED
    assert paths == [
        _invoke_path(EFFECT),
        "/v1/device/token/refresh",
        _invoke_path(EFFECT),
    ]
    saved = store.load()
    assert (
        saved is not None and saved.access_token.get_secret_value() == "rotated-access"
    )


def test_a_second_401_asks_to_pair_again() -> None:
    paths: list[str] = []
    hosted, store = transport(_bearer_handler(paths, invoke_401s=2))
    store.save(session(NOW))
    with pytest.raises(HostedClientError, match="pair this device again"):
        hosted.invoke(request(EFFECT))
    assert paths.count(_invoke_path(EFFECT)) == 2
    assert paths.count("/v1/device/token/refresh") == 1


def test_a_refused_refresh_asks_to_pair_again_without_resending() -> None:
    paths: list[str] = []
    hosted, store = transport(_bearer_handler(paths, invoke_401s=1, refresh_status=401))
    store.save(session(NOW))
    with pytest.raises(HostedClientError, match="pair this device again"):
        hosted.invoke(request(EFFECT))
    assert paths == [_invoke_path(EFFECT), "/v1/device/token/refresh"]


def _rotated_by_another_process() -> DeviceSession:
    return session(NOW).model_copy(
        update={
            "access_token": SecretStr("other-access"),
            "refresh_token": SecretStr("other-refresh"),
        }
    )


def test_a_pair_rotated_by_another_process_is_used_not_refreshed_again() -> None:
    paths: list[str] = []
    store = InMemorySecureSessionStore()

    def handler(http_request: httpx.Request) -> httpx.Response:
        paths.append(http_request.url.path)
        if http_request.headers["Authorization"] == "Bearer other-access":
            return response(200, CONFIRMED, request=http_request)
        # The other process rotated the pair while this request was refused.
        store.save(_rotated_by_another_process())
        return response(401, {"detail": "no"}, request=http_request)

    hosted, _ = transport(httpx.MockTransport(handler), store)
    store.save(session(NOW))
    assert hosted.invoke(request(EFFECT)).status is HostedOperationStatus.CONFIRMED
    assert paths == [_invoke_path(EFFECT), _invoke_path(EFFECT)]


def test_a_refresh_lost_to_another_process_uses_its_pair_not_a_re_pair() -> None:
    paths: list[str] = []
    store = InMemorySecureSessionStore()

    def handler(http_request: httpx.Request) -> httpx.Response:
        paths.append(http_request.url.path)
        if http_request.url.path == "/v1/device/token/refresh":
            # Both refreshed with the same single-use token; the other won.
            store.save(_rotated_by_another_process())
            return response(401, {"detail": "no"}, request=http_request)
        if http_request.headers["Authorization"] == "Bearer other-access":
            return response(200, CONFIRMED, request=http_request)
        return response(401, {"detail": "no"}, request=http_request)

    hosted, _ = transport(httpx.MockTransport(handler), store)
    store.save(session(NOW))
    assert hosted.invoke(request(EFFECT)).status is HostedOperationStatus.CONFIRMED
    assert paths == [
        _invoke_path(EFFECT),
        "/v1/device/token/refresh",
        _invoke_path(EFFECT),
    ]
    saved = store.load()
    assert saved is not None and saved.access_token.get_secret_value() == "other-access"


def test_an_expired_access_token_also_takes_a_pair_rotated_elsewhere() -> None:
    paths: list[str] = []
    store = InMemorySecureSessionStore()
    expired = session(NOW - timedelta(minutes=20))

    def handler(http_request: httpx.Request) -> httpx.Response:
        paths.append(http_request.url.path)
        if http_request.url.path == "/v1/device/token/refresh":
            store.save(_rotated_by_another_process())
            return response(401, {"detail": "no"}, request=http_request)
        assert http_request.headers["Authorization"] == "Bearer other-access"
        return response(200, CONFIRMED, request=http_request)

    hosted, _ = transport(httpx.MockTransport(handler), store)
    store.save(expired)
    assert hosted.invoke(request(EFFECT)).status is HostedOperationStatus.CONFIRMED
    assert paths == ["/v1/device/token/refresh", _invoke_path(EFFECT)]


# -- §9 426 ----------------------------------------------------------------------------


def test_a_426_says_to_upgrade() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(
            426,
            {
                "code": "protocol_incompatible",
                "reason": "version_unsupported",
                "required": f"{ZEOCONNECT_PROTOCOL_HEADER}: 1",
            },
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    with pytest.raises(HostedUpgradeRequiredError, match="upgrade zeocore"):
        hosted.invoke(request(EFFECT))


# -- 1.1.0 additions (zeoconnect #40, 7a62288a): additive, read without a re-pin ---


def test_a_replay_is_marked_and_is_not_a_fresh_success() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return response(
            200, {**CONFIRMED, "receipt": {"replayed": True}}, request=http_request
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    answer = hosted.invoke(request(EFFECT))
    assert answer.status is HostedOperationStatus.CONFIRMED and answer.replayed
    fresh = HostedOperationResponse.model_validate(CONFIRMED)
    assert not fresh.replayed
    assert not HostedOperationResponse.model_validate(
        {**CONFIRMED, "receipt": {"replayed": "true"}}
    ).replayed


def test_unreadable_controls_are_an_outage_never_a_stop() -> None:
    answer = HostedOperationResponse.model_validate(
        {
            "status": "failed_safe",
            "execution_id": "exe-1",
            "normalized_error": {
                "code": "PROVIDER_UNAVAILABLE",
                "message": "controls_unavailable:dispatch",
            },
        }
    )
    assert is_outage(answer) and stop_of(answer) is None


# -- Request bodies (zeoconnect #49) ---------------------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_number_json_cannot_carry_is_refused_before_sending(value: float) -> None:
    sent: list[httpx.Request] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(http_request)
        return response(200, CONFIRMED, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(session(NOW))
    effect = request(EFFECT).model_copy(update={"arguments": {"nested": [value]}})
    with pytest.raises(HostedClientError, match="JSON cannot carry"):
        hosted.invoke(effect)
    assert sent == []
