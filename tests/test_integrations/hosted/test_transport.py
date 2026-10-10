"""The native transport is fixed-origin, bounded, versioned, and secret-safe."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from zeo_core.integrations.hosted import (
    ZEOCONNECT_PROTOCOL_HEADER,
    ZEOCONNECT_PROTOCOL_VERSION,
    DeviceSession,
    HostedClientError,
    HostedConnectionStatus,
    HostedOperationRequest,
    HostedOperationStatus,
    HostedUnreachableError,
    InMemorySecureSessionStore,
    PairingPendingError,
    ZEOconnectHTTPTransport,
)

PRODUCTION = "https://connect.zeroemployee.org"
NOW = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)


def session(now: datetime = NOW) -> DeviceSession:
    return DeviceSession(
        device_id="dev_12345678-1234-4234-9234-123456789012",
        access_token=SecretStr("access-authority-canary"),
        refresh_token=SecretStr("refresh-authority-canary"),
        access_expires_at=now + timedelta(minutes=15),
        refresh_expires_at=now + timedelta(days=30),
    )


def response(
    status: int,
    payload: object | None = None,
    *,
    request: httpx.Request,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    merged = {ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION}
    merged.update(headers or {})
    if payload is None:
        return httpx.Response(status, headers=merged, request=request)
    return httpx.Response(status, headers=merged, json=payload, request=request)


def transport(
    handler: httpx.MockTransport,
    store: InMemorySecureSessionStore | None = None,
) -> tuple[ZEOconnectHTTPTransport, InMemorySecureSessionStore]:
    sessions = store or InMemorySecureSessionStore()
    client = httpx.Client(
        base_url=PRODUCTION, transport=handler, follow_redirects=False
    )
    return (
        ZEOconnectHTTPTransport(
            session_store=sessions,
            http_client=client,
            clock=lambda: NOW,
        ),
        sessions,
    )


def request(operation: str = "google.drive.file.download") -> HostedOperationRequest:
    return HostedOperationRequest(
        connection_id="con_google_12345678",
        operation_id=operation,
        arguments={"file_id": "file-selected"},
        idempotency_key="same-idempotency-key",
    )


def test_origin_is_fixed_and_supabase_is_not_a_client_configuration() -> None:
    parameters = inspect.signature(ZEOconnectHTTPTransport).parameters
    assert not {
        "supabase_url",
        "supabase_key",
        "service_role",
        "database_url",
        "vault_ref",
    }.intersection(parameters)
    store = InMemorySecureSessionStore()
    for rejected in (
        "https://project.supabase.co",
        "https://user:password@connect.zeroemployee.org",
        "https://connect.zeroemployee.org/private",
        "https://connect.zeroemployee.org?token=value",
    ):
        with pytest.raises(ValueError, match="origin|credentials|path"):
            ZEOconnectHTTPTransport(session_store=store, base_url=rejected)
    local = ZEOconnectHTTPTransport(
        session_store=store,
        base_url="http://127.0.0.1:8080",
        allow_development_origin=True,
    )
    local.close()


def test_pairing_uses_existing_routes_and_requires_protocol_version() -> None:
    seen: list[tuple[str, str]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append((http_request.method, http_request.url.path))
        assert (
            http_request.headers[ZEOCONNECT_PROTOCOL_HEADER]
            == ZEOCONNECT_PROTOCOL_VERSION
        )
        if http_request.url.path == "/v1/device/authorizations":
            return response(
                200,
                {
                    "pairing_id": "pair_12345678",
                    "device_code": "device-code-canary",
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://connect.zeroemployee.org/device",
                    "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
                    "interval_seconds": 5,
                },
                request=http_request,
            )
        return response(428, {"code": "authorization_pending"}, request=http_request)

    hosted, _store = transport(httpx.MockTransport(handler))
    challenge = hosted.begin_pairing(device_name="Member Mac")
    assert "device-code-canary" not in challenge.model_dump_json()
    with pytest.raises(PairingPendingError, match="pending"):
        hosted.poll_pairing(challenge)
    assert seen == [
        ("POST", "/v1/device/authorizations"),
        ("POST", "/v1/device/token"),
    ]


def test_connection_catalog_is_sanitized_and_grouped_by_service() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.headers["Authorization"] == "Bearer access-authority-canary"
        return response(
            200,
            [
                {
                    "connection_id": "con_google_12345678",
                    "provider": "google_workspace",
                    "external_identity": "member@example.com",
                    "status": "active",
                    "operations": [
                        "google.drive.file.download",
                        "google.docs.document.read",
                    ],
                    "resources": [
                        {
                            "external_id": "file-selected",
                            "display_name": "selected.csv",
                            "media_type": "text/csv",
                            "operations": ["google.drive.file.download"],
                        }
                    ],
                }
            ],
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(handler))
    current = session(NOW)
    store.save(current)
    catalog = hosted.list_connections(current)

    assert {item.service for item in catalog} == {"google.drive", "google.docs"}
    assert all(item.status is HostedConnectionStatus.ACTIVE for item in catalog)
    rendered = "".join(item.model_dump_json() for item in catalog)
    assert "organization_id" not in rendered
    assert "connector_revision" not in rendered
    assert "secret_ref" not in rendered


def test_safe_read_retry_preserves_body_and_effect_never_retries() -> None:
    bodies: list[bytes] = []

    def read_handler(http_request: httpx.Request) -> httpx.Response:
        bodies.append(http_request.content)
        if len(bodies) == 1:
            raise httpx.ConnectError("offline canary", request=http_request)
        return response(
            200,
            {
                "status": "confirmed",
                "execution_id": "execution-read",
                "result": "selected",
            },
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(read_handler))
    store.save(session(NOW))
    result = hosted.invoke(request())
    assert result.status is HostedOperationStatus.CONFIRMED
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    decoded = json.loads(bodies[0])
    assert decoded["idempotency_key"] == "same-idempotency-key"
    assert "connector_revision" not in decoded

    effect_calls = 0

    def effect_handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal effect_calls
        effect_calls += 1
        raise httpx.ConnectError("uncertain effect", request=http_request)

    effect, effect_store = transport(httpx.MockTransport(effect_handler))
    effect_store.save(session(NOW))
    with pytest.raises(HostedUnreachableError):
        effect.invoke(request("bluesky.post.create"))
    assert effect_calls == 1


def test_expired_access_rotates_before_read_and_persists_new_session() -> None:
    paths: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        paths.append(http_request.url.path)
        if http_request.url.path == "/v1/device/token/refresh":
            return response(
                200,
                {
                    "device_id": "dev_12345678-1234-4234-9234-123456789012",
                    "access_token": "rotated-access",
                    "refresh_token": "rotated-refresh",
                    "access_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
                    "refresh_expires_at": (NOW + timedelta(days=30)).isoformat(),
                },
                request=http_request,
            )
        assert http_request.headers["Authorization"] == "Bearer rotated-access"
        return response(
            200,
            {
                "status": "confirmed",
                "execution_id": "execution-read",
                "result": "selected",
            },
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(handler))
    store.save(
        session(NOW).model_copy(
            update={"access_expires_at": NOW - timedelta(seconds=1)}
        )
    )
    hosted.invoke(request())
    assert paths == [
        "/v1/device/token/refresh",
        "/v1/operations/google.drive.file.download:invoke",
    ]
    assert store.load() is not None
    assert store.load().access_token.get_secret_value() == "rotated-access"  # type: ignore[union-attr]


def test_redirect_version_body_and_secret_failures_are_sanitized() -> None:
    cases = (
        httpx.Response(
            302,
            headers={"Location": "https://attacker.example"},
        ),
        httpx.Response(200, headers={ZEOCONNECT_PROTOCOL_HEADER: "2"}, json={}),
        httpx.Response(
            200,
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            content=b"x" * (1024 * 1024 + 1),
        ),
        httpx.Response(
            200,
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            json={
                "status": "confirmed",
                "execution_id": "execution-read",
                "result": {"access_token": "RESPONSE-SECRET-CANARY"},
            },
        ),
    )
    for raw_response in cases:

        def handler(
            http_request: httpx.Request,
            selected: httpx.Response = raw_response,
        ) -> httpx.Response:
            selected.request = http_request
            return selected

        hosted, store = transport(httpx.MockTransport(handler))
        store.save(session(NOW))
        with pytest.raises(HostedClientError) as captured:
            hosted.invoke(request())
        rendered = repr(captured.value) + str(captured.value)
        assert "RESPONSE-SECRET-CANARY" not in rendered


def test_artifact_fetch_and_device_revoke_use_bounded_versioned_routes() -> None:
    seen: list[tuple[str, str]] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append((http_request.method, http_request.url.path))
        assert http_request.headers["Authorization"] == "Bearer access-authority-canary"
        if http_request.url.path.startswith("/v1/artifacts/"):
            return httpx.Response(
                200,
                content=b"bounded artifact",
                headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
                request=http_request,
            )
        return response(204, request=http_request)

    hosted, store = transport(httpx.MockTransport(handler))
    current = session(NOW)
    store.save(current)
    assert (
        hosted.fetch_artifact(artifact_id="art_selected", max_bytes=32)
        == b"bounded artifact"
    )
    hosted.revoke_device(current)
    assert seen == [
        ("GET", "/v1/artifacts/art_selected"),
        ("POST", "/v1/device/revoke"),
    ]


def test_artifact_and_request_limits_fail_closed() -> None:
    def artifact_handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"too large",
            headers={ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION},
            request=http_request,
        )

    hosted, store = transport(httpx.MockTransport(artifact_handler))
    store.save(session(NOW))
    for maximum in (-1, 10 * 1024 * 1024 + 1):
        with pytest.raises(HostedClientError, match="client limit"):
            hosted.fetch_artifact(artifact_id="art_selected", max_bytes=maximum)
    with pytest.raises(HostedClientError, match="declared size"):
        hosted.fetch_artifact(artifact_id="art_selected", max_bytes=2)
    with pytest.raises(HostedClientError, match="request exceeds"):
        hosted.invoke(
            HostedOperationRequest(
                connection_id="con_google_12345678",
                operation_id="google.drive.file.download",
                arguments={"file_id": "x" * (64 * 1024)},
                idempotency_key="large-request",
            )
        )


def test_session_and_response_shape_failures_are_sanitized() -> None:
    hosted, _store = transport(
        httpx.MockTransport(
            lambda request: response(200, {"not": "a list"}, request=request)
        )
    )
    with pytest.raises(HostedClientError, match="session is unavailable"):
        hosted.invoke(request())
    with pytest.raises(HostedClientError, match="shape"):
        hosted.list_connections(session(NOW))

    expired_store = InMemorySecureSessionStore()
    expired_store.save(
        session(NOW).model_copy(
            update={"refresh_expires_at": NOW - timedelta(seconds=1)}
        )
    )
    expired, _store = transport(
        httpx.MockTransport(lambda request: response(200, {}, request=request)),
        expired_store,
    )
    with pytest.raises(HostedClientError, match="session is expired"):
        expired.invoke(request())
