"""Fixed-origin HTTP transport for the consent-bound ZEOconnect member API."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError

from zeo_core.core.managed_execution import is_managed_execution
from zeo_core.integrations.hosted.client import (
    CONNECTION_REVISION_PATTERN,
    REQUEST_CHANGED_UNDER_KEY,
    HostedClientError,
    HostedConnectionChangedError,
    HostedFenceUnsupportedError,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedRequestChangedError,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
    HostedUpgradeRequiredError,
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

#: The production BROKER: tailnet-only, a device client's only peer (contract
#: 1.0.0 §2). WEB, ``https://connect.zeo.ac``, serves browsers and is never
#: called by this transport; pairing's ``verification_url`` points there.
ZEOCONNECT_PRODUCTION_ORIGIN = "https://broker.connect.zeo.ac"
ZEOCONNECT_PROTOCOL_VERSION = "1"
ZEOCONNECT_PROTOCOL_HEADER = "ZEOconnect-Protocol-Version"
#: Declared on every request, statically (contract 1.2.0 §3): the Broker may
#: send the STOPPED code (§9) and each connection's revision (§6a). A Broker
#: that does not know a capability ignores it.
ZEOCONNECT_CAPABILITIES_HEADER = "ZEOconnect-Capabilities"
_CAPABILITIES = "stopped-code, expected-binding"
_STOP_TOKEN = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_REPAIR = "paired device session was refused; pair this device again"
#: The Broker's exact headered 400 when a connection id was re-enrolled with a
#: changed subject, scopes, resources or credential (zeoconnect #59).
_CONNECTION_CHANGED = "kernel connection binding changed"
#: The same 400's code, for a client declaring expected-binding (contract
#: 1.2.2 §9). The detail stays byte-identical, so it remains the fallback.
_CONNECTION_CHANGED_CODE = "connection_binding_changed"
_OFF_NETWORK = (
    "ZEOconnect Broker {origin} cannot be reached from this device. This hosted"
    " profile is available only on its organisation's private network. zeocore"
    " will not use local credentials in its place. To use your own Revolut"
    " account directly, select the local profile explicitly."
)
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


class _BearerRefusedError(HostedClientError):
    """A Broker 401: the access token was refused before anything ran."""

    def __init__(self) -> None:
        super().__init__("hosted request was refused")


def _stop(response: httpx.Response) -> HostedStoppedError | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("code") != "stopped":
        return None
    control, scope = body.get("control"), body.get("scope")
    if not (isinstance(control, str) and isinstance(scope, str)):
        return None
    if not (_STOP_TOKEN.match(control) and _STOP_TOKEN.match(scope)):
        return None
    return HostedStoppedError(control=control, scope=scope)


# A 1.y Broker may add response fields; a 1.0 client ignores them (§10).
class _PairingWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    pairing_id: str
    device_code: SecretStr = Field(..., repr=False)
    user_code: str
    verification_url: str
    expires_at: datetime
    interval_seconds: int


class _SessionWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    device_id: str
    access_token: SecretStr = Field(..., repr=False)
    refresh_token: SecretStr = Field(..., repr=False)
    access_expires_at: datetime
    refresh_expires_at: datetime


class _ResourceWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    external_id: str
    display_name: str
    media_type: str
    operations: tuple[str, ...]


class _ConnectionWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    connection_id: str
    provider: str
    external_identity: str
    status: HostedConnectionStatus
    operations: tuple[str, ...]
    resources: tuple[_ResourceWire, ...] = ()
    connection_revision: str | None = Field(
        default=None, pattern=CONNECTION_REVISION_PATTERN
    )


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
        try:
            return self._refresh(session)
        except _BearerRefusedError:
            raise HostedClientError(_REPAIR) from None

    def _refresh(self, session: DeviceSession) -> DeviceSession:
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
        payload = self._authorized(
            lambda current: self._request_json(
                "GET", "/v1/connections", session=current, authenticated=True
            ),
            session,
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
                        connection_revision=wire.connection_revision,
                    )
                )
        return tuple(summaries)

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        if is_managed_execution():
            raise HostedClientError(
                "managed execution requires the Runtime effect service"
            )
        body = request.model_dump(mode="json", exclude_none=True)
        # Only a safe read gets a second attempt, and only when no Broker
        # response arrived. A Broker 503 is an outage, never retried by status
        # (contract §9), and an effectful operation is sent at most once per
        # session (a 401 is refused before anything runs).
        attempts = 2 if request.operation_id in _SAFE_RETRY_OPERATIONS else 1
        for attempt in range(attempts):
            try:
                payload = self._authorized(
                    lambda current: self._request_json(
                        "POST",
                        f"/v1/operations/{request.operation_id}:invoke",
                        json_body=body,
                        session=current,
                        authenticated=True,
                        fenced=request.expect is not None,
                    )
                )
            except HostedUnreachableError:
                if attempt + 1 == attempts:
                    raise
                continue
            try:
                return HostedOperationResponse.model_validate(payload)
            except ValidationError:
                raise HostedClientError(
                    "hosted operation response is invalid"
                ) from None
        raise HostedUnreachableError()  # pragma: no cover - the loop returns or raises

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
        if max_bytes < 0 or max_bytes > _MAX_ARTIFACT_BYTES:
            raise HostedClientError("hosted artifact exceeds the client limit")
        if is_managed_execution():
            raise HostedClientError(
                "managed artifact retrieval requires Runtime authority"
            )
        return self._authorized(
            lambda current: self._fetch(artifact_id, max_bytes, current)
        )

    def _fetch(self, artifact_id: str, max_bytes: int, session: DeviceSession) -> bytes:
        try:
            with self._client.stream(
                "GET", f"/v1/artifacts/{artifact_id}", headers=self._headers(session)
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
        except httpx.TransportError as error:
            raise self._unreachable(error) from None
        except Exception:
            raise HostedUnreachableError() from None

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
        return self._authorized(
            lambda current: self._relay(
                current,
                connection_id=connection_id,
                link=link,
                seal=seal,
                content_range=content_range,
                content_type=content_type,
                body=body,
            )
        )

    def _relay(
        self,
        session: DeviceSession,
        *,
        connection_id: str,
        link: str,
        seal: str,
        content_range: str,
        content_type: str,
        body: bytes,
    ) -> dict[str, JsonValue]:
        headers = {
            **self._headers(session),
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
        except httpx.TransportError as error:
            raise self._unreachable(error) from None
        # Only a Broker-produced 5xx is an outage. A headerless one came from
        # something in front of the Broker and is a terminal protocol failure.
        _require_protocol(response)
        if response.status_code in {502, 503, 504}:
            # ZEOconnect could not reach YouTube (or Google's token endpoint):
            # transient, so the transfer probes again and resumes.
            raise HostedUnavailableError()
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

    def _authorized[T](
        self,
        call: Callable[[DeviceSession], T],
        session: DeviceSession | None = None,
    ) -> T:
        """Run ``call``; after a Broker 401, refresh once and run it once more.

        A 401 is answered before anything runs, so the second call is the
        same request, never a second effect. A second 401 means the device
        must be paired again (contract 1.0.0 §9).
        """
        current = session or self._active_session()
        try:
            return call(current)
        except _BearerRefusedError:
            pass
        current = self._refreshed(current)
        try:
            return call(current)
        except _BearerRefusedError:
            raise HostedClientError(_REPAIR) from None

    def _unreachable(self, error: httpx.TransportError) -> HostedUnreachableError:
        if self._base_url == ZEOCONNECT_PRODUCTION_ORIGIN and isinstance(
            error, (httpx.ConnectError, httpx.ConnectTimeout)
        ):
            return HostedUnreachableError(_OFF_NETWORK.format(origin=self._base_url))
        return HostedUnreachableError()

    def _active_session(self) -> DeviceSession:
        session = self._session_store.load()
        if session is None:
            raise HostedClientError("paired device session is unavailable")
        if self._clock() >= session.refresh_expires_at:
            raise HostedClientError("paired device session is expired")
        if self._clock() >= session.access_expires_at:
            session = self._refreshed(session)
        return session

    def _refreshed(self, used: DeviceSession) -> DeviceSession:
        """Refresh ``used`` and save the result, unless another process already did.

        Refresh tokens are single use (contract 1.0.0 §4). A process sharing
        this store, such as a YouTube run beside the retention sweep, may
        rotate the pair first. So the store is read again before refreshing,
        and again if the refresh is refused. A pair rotated by the other
        process is used, never reported as "pair this device again".

        A narrow window remains. If the refusal is read before the other
        process has saved its rotated pair, the refusal stands.
        """
        if (stored := self._rotated_elsewhere(used)) is not None:
            return stored
        try:
            session = self._refresh(used)
        except _BearerRefusedError:
            if (stored := self._rotated_elsewhere(used)) is not None:
                return stored
            raise HostedClientError(_REPAIR) from None
        self._session_store.save(session)
        return session

    def _rotated_elsewhere(self, used: DeviceSession) -> DeviceSession | None:
        stored = self._session_store.load()
        if stored is None or (
            stored.refresh_token.get_secret_value()
            == used.refresh_token.get_secret_value()
        ):
            return None
        return stored

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, JsonValue] | None = None,
        session: DeviceSession | None = None,
        authenticated: bool,
        expect_empty: bool = False,
        fenced: bool = False,
    ) -> JsonValue | None:
        if is_managed_execution():
            raise HostedClientError("managed execution forbids member API fallback")
        headers = self._headers(session) if authenticated else self._headers(None)
        if json_body is not None:
            # NaN and +-Infinity are not JSON, and the Broker refuses them. httpx
            # 0.27 would send them, so refuse here whatever httpx is installed.
            try:
                encoded = json.dumps(
                    json_body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            except ValueError:
                raise HostedClientError(
                    "hosted request holds a number JSON cannot carry"
                ) from None
            if len(encoded) > _MAX_REQUEST_BYTES:
                raise HostedClientError("hosted request exceeds the client limit")
        try:
            response = self._client.request(
                method, path, json=json_body, headers=headers
            )
        except httpx.TransportError as error:
            raise self._unreachable(error) from None
        self._validate_response(response, fenced=fenced)
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

    def _validate_response(
        self, response: httpx.Response, *, fenced: bool = False
    ) -> None:
        if response.is_redirect:
            raise HostedClientError("hosted redirect is forbidden")
        _require_protocol(response)
        status = response.status_code
        if status == 401:
            raise _BearerRefusedError()
        if status == 403 and (stop := _stop(response)) is not None:
            raise stop
        if status == 426:
            raise HostedUpgradeRequiredError()
        if status in {409, 425, 428} and _is_pending(response):
            raise HostedClientError("hosted request is pending")
        if status == 422 and fenced:
            # A 1.1 Broker refuses expect this way (contract 1.2.0 §6a). The
            # request is never resent without it.
            raise HostedFenceUnsupportedError("invalid_fenced_request")
        if status == 503:
            # An outage the Broker reported (council ruling E7): not a stop,
            # not a refusal, and not proof the request was never accepted.
            raise HostedUnavailableError()
        if status == 400:
            _raise_marked_refusal(response)
        if status >= 400:
            # Not evidence of non-acceptance either: only a stop is positive.
            raise HostedClientError("hosted request was refused")

    @staticmethod
    def _headers(session: DeviceSession | None) -> dict[str, str]:
        headers = {
            ZEOCONNECT_PROTOCOL_HEADER: ZEOCONNECT_PROTOCOL_VERSION,
            ZEOCONNECT_CAPABILITIES_HEADER: _CAPABILITIES,
        }
        if session is not None:
            headers["Authorization"] = (
                "Bearer " + session.access_token.get_secret_value()
            )
        return headers


def _raise_marked_refusal(response: httpx.Response) -> None:
    """Raise the reason a 400 names, if it names one exactly."""
    code = _code(response)
    if code == _CONNECTION_CHANGED_CODE or _detail(response) == _CONNECTION_CHANGED:
        raise HostedConnectionChangedError()
    if code == REQUEST_CHANGED_UNDER_KEY:
        # Contract 1.2.1 §6a.5, marked for a declared client. The key's
        # original outcome stands; a changed request needs a new key.
        raise HostedRequestChangedError()


def _detail(response: httpx.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("detail") if isinstance(body, dict) else None


def _code(response: httpx.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def _is_pending(response: httpx.Response) -> bool:
    try:
        return bool(response.json().get("code") == "authorization_pending")
    except AttributeError, ValueError:
        return False


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
    "ZEOCONNECT_CAPABILITIES_HEADER",
    "ZEOCONNECT_PRODUCTION_ORIGIN",
    "ZEOCONNECT_PROTOCOL_HEADER",
    "ZEOCONNECT_PROTOCOL_VERSION",
    "ZEOconnectHTTPTransport",
]
