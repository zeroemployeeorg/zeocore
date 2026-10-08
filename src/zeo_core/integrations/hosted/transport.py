"""Fixed-origin HTTP transport for the consent-bound ZEOconnect member API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError

from zeo_core.core.managed_execution import is_managed_execution
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedOperationRequest,
    HostedOperationResponse,
)
from zeo_core.integrations.hosted.pairing import (
    DeviceSession,
    PairingChallenge,
    PairingPendingError,
    SecureSessionStore,
)
from zeo_core.integrations.hosted.profile import (
    HostedConnectionStatus,
    HostedConnectionSummary,
    HostedResourceSummary,
    OpaqueConnectionHandle,
)

ZEOCONNECT_PRODUCTION_ORIGIN = "https://connect.zeroemployee.org"
ZEOCONNECT_PROTOCOL_VERSION = "1"
ZEOCONNECT_PROTOCOL_HEADER = "ZEOconnect-Protocol-Version"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
_SAFE_RETRY_OPERATIONS = frozenset({"google.drive.file.download"})
#: Relay chunks stay under the hosting platform's 4.5 MB request limit.
YOUTUBE_RELAY_MAX_CHUNK_BYTES = 4 * 1024 * 1024


def _require_protocol(response: httpx.Response) -> None:
    """A Broker response carries exactly one, matching protocol header.

    Broker contract 1.0.0 §3: a response without the header was not produced
    by the Broker (an edge, proxy or network failure), and one with another
    value is a mismatch. Either way, on any status including errors, it ends
    the operation (council ruling E5): never a stop, never an outage, never
    retried.
    """
    values = response.headers.get_list(ZEOCONNECT_PROTOCOL_HEADER)
    if not values:
        raise HostedClientError("hosted response did not come from the Broker")
    if values != [ZEOCONNECT_PROTOCOL_VERSION]:
        raise HostedClientError("hosted protocol version is incompatible")


class _PairingWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pairing_id: str
    device_code: SecretStr = Field(..., repr=False)
    user_code: str
    verification_url: str
    expires_at: datetime
    interval_seconds: int


class _SessionWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str
    access_token: SecretStr = Field(..., repr=False)
    refresh_token: SecretStr = Field(..., repr=False)
    access_expires_at: datetime
    refresh_expires_at: datetime


class _ResourceWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str
    display_name: str
    media_type: str
    operations: tuple[str, ...]


class _ConnectionWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: str
    provider: str
    external_identity: str
    status: HostedConnectionStatus
    operations: tuple[str, ...]
    resources: tuple[_ResourceWire, ...] = ()


class ZEOconnectHTTPTransport:
    """Authenticated member-API transport with no Supabase-facing surface."""

    def __init__(
        self,
        *,
        session_store: SecureSessionStore,
        base_url: str = ZEOCONNECT_PRODUCTION_ORIGIN,
        allow_development_origin: bool = False,
        http_client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._base_url = _validated_origin(
            base_url, allow_development=allow_development_origin
        )
        self._session_store = session_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = http_client or httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def begin_pairing(self, *, device_name: str) -> PairingChallenge:
        try:
            wire = _PairingWire.model_validate(
                self._request_json(
                    "POST",
                    "/v1/device/authorizations",
                    json_body={"device_name": device_name},
                    authenticated=False,
                )
            )
        except ValidationError:
            raise HostedClientError("hosted pairing response is invalid") from None
        return PairingChallenge(
            pairing_id=wire.pairing_id,
            device_code=wire.device_code,
            verification_url=wire.verification_url,
            user_code=wire.user_code,
            expires_at=wire.expires_at,
            polling_interval_seconds=wire.interval_seconds,
        )

    def poll_pairing(self, challenge: PairingChallenge) -> DeviceSession:
        try:
            payload = self._request_json(
                "POST",
                "/v1/device/token",
                json_body={"device_code": challenge.device_code.get_secret_value()},
                authenticated=False,
            )
        except HostedClientError as error:
            if str(error) == "hosted request is pending":
                raise PairingPendingError("device authorization is pending") from None
            raise
        try:
            return _session(_SessionWire.model_validate(payload))
        except ValidationError:
            raise HostedClientError("hosted session response is invalid") from None

    def refresh_session(self, session: DeviceSession) -> DeviceSession:
        payload = self._request_json(
            "POST",
            "/v1/device/token/refresh",
            json_body={"refresh_token": session.refresh_token.get_secret_value()},
            authenticated=False,
        )
        try:
            return _session(_SessionWire.model_validate(payload))
        except ValidationError:
            raise HostedClientError("hosted session response is invalid") from None

    def list_connections(
        self, session: DeviceSession
    ) -> tuple[HostedConnectionSummary, ...]:
        payload = self._request_json(
            "GET", "/v1/connections", session=session, authenticated=True
        )
        if not isinstance(payload, list):
            raise HostedClientError("hosted response shape is invalid")
        summaries: list[HostedConnectionSummary] = []
        for raw in payload:
            try:
                wire = _ConnectionWire.model_validate(raw)
            except ValidationError:
                raise HostedClientError(
                    "hosted connection response is invalid"
                ) from None
            for service, operations in _operations_by_service(wire.operations).items():
                summaries.append(
                    HostedConnectionSummary(
                        handle=OpaqueConnectionHandle(value=wire.connection_id),
                        service=service,
                        external_identity=wire.external_identity,
                        status=wire.status,
                        operations=operations,
                        resources=tuple(
                            HostedResourceSummary.model_validate(resource.model_dump())
                            for resource in wire.resources
                        ),
                    )
                )
        return tuple(summaries)

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        if is_managed_execution():
            raise HostedClientError(
                "managed execution requires the Runtime effect service"
            )
        body = request.model_dump(mode="json", exclude_none=True)
        attempts = 2 if request.operation_id in _SAFE_RETRY_OPERATIONS else 1
        last_error: HostedClientError | None = None
        for _attempt in range(attempts):
            try:
                payload = self._request_json(
                    "POST",
                    f"/v1/operations/{request.operation_id}:invoke",
                    json_body=body,
                    session=self._active_session(),
                    authenticated=True,
                )
                try:
                    return HostedOperationResponse.model_validate(payload)
                except ValidationError:
                    raise HostedClientError(
                        "hosted operation response is invalid"
                    ) from None
            except HostedClientError as error:
                last_error = error
                if str(error) != "hosted transport is unavailable":
                    raise
        raise last_error or HostedClientError("hosted transport is unavailable")

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
        if max_bytes < 0 or max_bytes > _MAX_ARTIFACT_BYTES:
            raise HostedClientError("hosted artifact exceeds the client limit")
        if is_managed_execution():
            raise HostedClientError(
                "managed artifact retrieval requires Runtime authority"
            )
        session = self._active_session()
        headers = self._headers(session)
        try:
            with self._client.stream(
                "GET", f"/v1/artifacts/{artifact_id}", headers=headers
            ) as response:
                self._validate_response(response)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise HostedClientError(
                            "hosted artifact exceeds the declared size"
                        )
                return bytes(content)
        except HostedClientError:
            raise
        except Exception:
            raise HostedClientError("hosted transport is unavailable") from None

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
        """Send one upload chunk through ZEOconnect, which adds the channel token.

        The fallback when YouTube refuses an upload link without a token. ZEOconnect
        checks the seal it issued with the session, so only links it opened can be
        relayed; the token never leaves custody.
        """
        if len(body) > YOUTUBE_RELAY_MAX_CHUNK_BYTES:
            raise HostedClientError("relay chunk exceeds the client limit")
        if is_managed_execution():
            raise HostedClientError("managed execution forbids member API fallback")
        headers = {
            **self._headers(self._active_session()),
            "Content-Type": "application/octet-stream",
            "X-Zeo-Connection": connection_id,
            "X-Zeo-Upload-Link": link,
            "X-Zeo-Relay-Seal": seal,
            "X-Zeo-Content-Range": content_range,
            "X-Zeo-Content-Type": content_type,
        }
        try:
            response = self._client.post(
                "/v1/youtube/uploads:relay",
                content=body,
                headers=headers,
                timeout=httpx.Timeout(300.0, connect=15.0),
            )
        except httpx.TransportError:
            raise HostedClientError("hosted transport is unavailable") from None
        # Only a Broker-produced 5xx is an outage. A headerless one came from
        # something in front of the Broker and is a terminal protocol failure.
        _require_protocol(response)
        if response.status_code in {502, 503, 504}:
            # ZEOconnect could not reach YouTube (or Google's token endpoint):
            # transient, so the transfer probes again and resumes.
            raise HostedClientError("hosted transport is unavailable")
        self._validate_response(response)
        if len(response.content) > _MAX_JSON_BYTES:
            raise HostedClientError("hosted response exceeds the client limit")
        try:
            payload = response.json()
        except Exception:
            raise HostedClientError("hosted response shape is invalid") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), int):
            raise HostedClientError("hosted response shape is invalid")
        return cast(dict[str, JsonValue], payload)

    def revoke_device(self, session: DeviceSession) -> None:
        self._request_json(
            "POST",
            "/v1/device/revoke",
            session=session,
            authenticated=True,
            expect_empty=True,
        )

    def _active_session(self) -> DeviceSession:
        session = self._session_store.load()
        if session is None:
            raise HostedClientError("paired device session is unavailable")
        if self._clock() >= session.refresh_expires_at:
            raise HostedClientError("paired device session is expired")
        if self._clock() >= session.access_expires_at:
            session = self.refresh_session(session)
            self._session_store.save(session)
        return session

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, JsonValue] | None = None,
        session: DeviceSession | None = None,
        authenticated: bool,
        expect_empty: bool = False,
    ) -> JsonValue | None:
        if is_managed_execution():
            raise HostedClientError("managed execution forbids member API fallback")
        headers = self._headers(session) if authenticated else self._headers(None)
        if json_body is not None:
            encoded = httpx.Request(
                method, "https://local.invalid", json=json_body
            ).content
            if len(encoded) > _MAX_REQUEST_BYTES:
                raise HostedClientError("hosted request exceeds the client limit")
        try:
            response = self._client.request(
                method, path, json=json_body, headers=headers
            )
        except httpx.TransportError:
            raise HostedClientError("hosted transport is unavailable") from None
        self._validate_response(response)
        if expect_empty:
            if response.content:
                raise HostedClientError("hosted response shape is invalid")
            return None
        if len(response.content) > _MAX_JSON_BYTES:
            raise HostedClientError("hosted response exceeds the client limit")
        try:
            return cast(JsonValue, response.json())
        except Exception:
            raise HostedClientError("hosted response shape is invalid") from None

    def _validate_response(self, response: httpx.Response) -> None:
        if response.is_redirect:
            raise HostedClientError("hosted redirect is forbidden")
        _require_protocol(response)
        if response.status_code in {409, 425, 428}:
            try:
                if response.json().get("code") == "authorization_pending":
                    raise HostedClientError("hosted request is pending")
            except AttributeError, ValueError:
                pass
        if response.status_code >= 400:
            raise HostedClientError("hosted request was refused")

    @staticmethod
    def _headers(session: DeviceSession | None) -> dict[str, str]:
        headers = {ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION}
        if session is not None:
            headers["Authorization"] = (
                "Bearer " + session.access_token.get_secret_value()
            )
        return headers


def _session(wire: _SessionWire) -> DeviceSession:
    return DeviceSession(
        device_id=wire.device_id,
        access_token=wire.access_token,
        refresh_token=wire.refresh_token,
        access_expires_at=wire.access_expires_at,
        refresh_expires_at=wire.refresh_expires_at,
    )


def _operations_by_service(
    operations: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for operation in operations:
        parts = operation.split(".")
        if len(parts) < 3:
            raise HostedClientError("hosted operation identity is invalid")
        service = ".".join(parts[:2]) if parts[0] == "google" else parts[0]
        grouped.setdefault(service, []).append(operation)
    return {key: tuple(value) for key, value in grouped.items()}


def _validated_origin(value: str, *, allow_development: bool) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ZEOconnect base URL must be an origin without credentials")
    if parsed.path not in {"", "/"}:
        raise ValueError("ZEOconnect base URL must not contain a path")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin == ZEOCONNECT_PRODUCTION_ORIGIN:
        return origin
    development_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        allow_development
        and parsed.scheme in {"http", "https"}
        and parsed.hostname in development_hosts
    ):
        return origin
    raise ValueError("ZEOconnect base URL is not an approved origin")


__all__ = [
    "YOUTUBE_RELAY_MAX_CHUNK_BYTES",
    "ZEOCONNECT_PRODUCTION_ORIGIN",
    "ZEOCONNECT_PROTOCOL_HEADER",
    "ZEOCONNECT_PROTOCOL_VERSION",
    "ZEOconnectHTTPTransport",
]
