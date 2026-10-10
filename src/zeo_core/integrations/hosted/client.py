"""Typed, credential-free client boundary for ZEOconnect operations."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Final, Literal, Protocol, cast, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

from zeo_core.contracts.connections import NormalizedError, NormalizedErrorCode

_SECRET_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)


class HostedOperationStatus(StrEnum):
    """Closed dispositions returned by the hosted connection broker."""

    CONFIRMED = "confirmed"
    REFUSED = "refused"
    FAILED_SAFE = "failed_safe"
    AMBIGUOUS = "ambiguous"
    APPROVAL_REQUIRED = "approval_required"


class HostedArtifactDescriptor(BaseModel):
    """Bounded metadata for bytes fetched through the authenticated transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(..., pattern=r"^art_[A-Za-z0-9_-]{8,200}$")
    content_sha256: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(..., ge=0)
    media_type: str = Field(..., min_length=1, max_length=200)
    filename: str = Field(..., min_length=1, max_length=255)

    @model_validator(mode="after")
    def _filename_is_one_safe_segment(self) -> HostedArtifactDescriptor:
        if (
            self.filename in {".", ".."}
            or "/" in self.filename
            or "\\" in self.filename
        ):
            raise ValueError("artifact filename must be one safe path segment")
        return self


#: A connection's enrolment revision (contract 1.2.0 §6a.3): a decimal
#: positive integer as a string, compared as a string.
CONNECTION_REVISION_PATTERN: Final = r"^[1-9][0-9]{0,18}$"


class HostedExpectedBinding(BaseModel):
    """The enrolment a fenced invocation expects (contract 1.2.0 §6a).

    The Broker compares both values with the connection as it is enrolled,
    before custody and again before each provider call, and refuses a
    mismatch. Take them from a fresh ``list_connections`` through
    ``HostedConnectionSummary.expected_binding()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_identity: str = Field(..., min_length=1, max_length=500)
    connection_revision: str = Field(..., pattern=CONNECTION_REVISION_PATTERN)


class HostedOperationRequest(BaseModel):
    """Exact named operation request; no tenant, URL, method, header, or token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_id: str = Field(..., min_length=1, max_length=200)
    # Compatibility-only input. New managed-profile callers leave this unset;
    # ZEOconnect derives the immutable revision from the authenticated
    # connection and returns it in the broker receipt. Existing 0.9 callers
    # may continue to send their binding until the private server migrates.
    connector_revision: str | None = Field(default=None, min_length=1, max_length=200)
    operation_id: str = Field(..., min_length=1, max_length=200)
    arguments: dict[str, JsonValue]
    idempotency_key: str = Field(..., min_length=1, max_length=200)
    # Absent means unfenced. It is never sent as null: the body is dumped
    # with exclude_none, and the Broker refuses a null (contract 1.2.0 §6a).
    expect: HostedExpectedBinding | None = None


class HostedOperationResponse(BaseModel):
    """Bounded broker response with mutually exclusive result shapes.

    Unknown top-level fields are dropped, never passed through: a 1.y Broker
    may add response fields that a 1.0 client must ignore (contract §10).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    status: HostedOperationStatus
    execution_id: str = Field(..., min_length=1, max_length=200)
    result: JsonValue | None = None
    artifact: HostedArtifactDescriptor | None = None
    approval_url: HttpUrl | None = None
    normalized_error: NormalizedError | None = None
    receipt: dict[str, JsonValue] | None = None

    @property
    def replayed(self) -> bool:
        """The Broker served this from its stored record (contract 1.1.0 §6).

        A replay is a read of an earlier outcome, never a fresh act: it made no
        provider call. A ``confirmed`` replay is not a new success.
        """
        return self.receipt is not None and self.receipt.get("replayed") is True

    @field_validator("artifact", "normalized_error", mode="before")
    @classmethod
    def _ignore_unknown_nested_fields(
        cls, value: object, info: ValidationInfo
    ) -> object:
        # Contract §10 covers every level of a response, not only the top.
        # Both nested models refuse unknown fields when built directly, and
        # NormalizedError is shared beyond hosted access, so drop them here.
        model: type[BaseModel] = (
            HostedArtifactDescriptor
            if info.field_name == "artifact"
            else NormalizedError
        )
        if isinstance(value, dict):
            return {
                key: item for key, item in value.items() if key in model.model_fields
            }
        return value

    @model_validator(mode="after")
    def _shape_matches_status(self) -> HostedOperationResponse:
        if self.normalized_error is not None and self.normalized_error.provider_detail:
            raise ValueError("provider detail is forbidden in hosted responses")
        if _contains_secret_key(self.result) or _contains_secret_key(self.receipt):
            raise ValueError("hosted response contains a secret-bearing field")
        representations = int(self.result is not None) + int(self.artifact is not None)
        if self.status is HostedOperationStatus.CONFIRMED:
            if representations != 1 or self.approval_url is not None:
                raise ValueError("confirmed response requires exactly one result")
            if self.normalized_error is not None:
                raise ValueError("confirmed response forbids normalized_error")
        elif self.status is HostedOperationStatus.APPROVAL_REQUIRED:
            if self.approval_url is None or representations or self.normalized_error:
                raise ValueError(
                    "approval-required response requires only approval_url"
                )
        elif representations or self.approval_url is not None:
            raise ValueError("non-confirmed response cannot carry a result")
        return self


@runtime_checkable
class HostedAuthorizedTransport(Protocol):
    """Authenticated transport owned by ZEOconnect pairing/custody code."""

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse: ...

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes: ...


class HostedConnectionClient:
    """Invoke curated operations; credentials remain entirely inside transport."""

    def __init__(self, *, transport: HostedAuthorizedTransport) -> None:
        self._transport = transport

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        return self._transport.invoke(request)

    def download_artifact(self, artifact: HostedArtifactDescriptor) -> bytes:
        content = self._transport.fetch_artifact(
            artifact_id=artifact.artifact_id,
            max_bytes=artifact.size_bytes,
        )
        if len(content) != artifact.size_bytes:
            raise HostedClientError("hosted artifact size did not match receipt")
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest != artifact.content_sha256:
            raise HostedClientError("hosted artifact digest did not match receipt")
        return content


class HostedClientError(RuntimeError):
    """Sanitized failure at the hosted-client trust boundary.

    Apart from ``HostedStoppedError``, a failure says nothing about whether an
    effect happened. "Refused" and "unavailable" are never evidence that the
    Broker did not accept an effectful request.
    """


class HostedUnavailableError(HostedClientError):
    """An outage: the Broker, or the path to it, could not serve the request.

    Not a stop, and not proof that an effectful request was never accepted
    (council ruling E7). It grants no retry: only a read named as safe is
    attempted again, and only after a failure that produced no response.
    """

    def __init__(self, message: str = "hosted transport is unavailable") -> None:
        super().__init__(message)


class HostedUnreachableError(HostedUnavailableError):
    """No Broker response arrived: the connection failed or broke off."""


class HostedStoppedError(HostedClientError):
    """The Broker positively reported an operational stop (contract 1.0.0 §9).

    A stop is deliberate and is never retried or redispatched. ``control`` is
    the control that stopped the request and ``scope`` is where it applies.
    """

    def __init__(self, *, control: str, scope: str) -> None:
        self.control = control
        self.scope = scope
        super().__init__(f"hosted request was stopped by {control} ({scope})")


FenceUnsupportedReason = Literal["no_revision", "invalid_fenced_request"]
_FENCE_UNSUPPORTED: dict[str, str] = {
    "no_revision": (
        "ZEOconnect Broker listed no connection revision, so it cannot check"
        " the expected connection binding; the request was not sent"
    ),
    "invalid_fenced_request": (
        "ZEOconnect Broker refused the fenced request as invalid: either it"
        " cannot check the expected binding or the request itself is invalid;"
        " the request was not sent unfenced"
    ),
}


class HostedFenceUnsupportedError(HostedClientError):
    """A fence was asked for, and zeocore holds (contract 1.2.0 §3 and §6a).

    ``reason`` is ``"no_revision"`` when the listing carried no
    ``connection_revision``, so nothing was sent. It is
    ``"invalid_fenced_request"`` when the Broker answered a fenced
    invocation with 422. A 1.1 Broker refuses ``expect`` that way, but a 1.2
    Broker gives the same ``request is invalid`` to invalid arguments, and
    the contract leaves the two indistinguishable. Either way, the contract's
    rule is to hold. zeocore never resends the request without ``expect``.
    Like any refusal, this is no proof that an earlier attempt was not
    accepted.
    """

    def __init__(self, reason: FenceUnsupportedReason) -> None:
        self.reason = reason
        super().__init__(_FENCE_UNSUPPORTED[reason])


class HostedConnectionChangedError(HostedClientError):
    """The connection was re-enrolled with a changed subject, scopes, resources
    or credential, so this connection id can't serve fresh calls.

    The Broker refuses before custody or any provider call, and records
    nothing. The connection needs ZEOconnect's repair path. Retrying won't
    help, and this is no proof that an earlier attempt was not accepted.
    """

    def __init__(self) -> None:
        super().__init__(
            "ZEOconnect connection changed since it was enrolled; repair it in"
            " ZEOconnect before using it again"
        )


class HostedRequestChangedError(HostedClientError):
    """This idempotency key already carries a different request (contract 1.2.1 §6a.5).

    The arguments or ``expect`` changed under a used key. The Broker records
    nothing for the changed request, and the key's original outcome stands.
    A changed request is a new occurrence and needs a new key, sent only
    under the caller's own authority. It is never an automatic retry.
    """

    def __init__(self) -> None:
        super().__init__(
            "hosted request changed under an idempotency key already used;"
            " a changed request needs a new key"
        )


class HostedUpgradeRequiredError(HostedClientError):
    """The Broker does not speak this client's protocol version (426)."""

    def __init__(self) -> None:
        super().__init__(
            "ZEOconnect Broker does not accept this zeocore's protocol;"
            " upgrade zeocore to use hosted access"
        )


def is_outage(response: HostedOperationResponse) -> bool:
    """An orchestrated outage: ``failed_safe`` with ``PROVIDER_UNAVAILABLE``.

    For example ``controls_unavailable:<control>`` (contract 1.1.0 §9): the
    Broker could not read a control, so no stop is established and the
    provider was not called. Temporary, like a 503.

    Unlike a 503, this outcome is recorded against the idempotency key.
    Sending the same key again returns it as a replay (``replayed`` is
    true). Another attempt needs a new key: a new occurrence, sent only under
    the caller's own authority, never an automatic retry (contract 1.2 §4.5).
    """
    error = response.normalized_error
    return (
        response.status is HostedOperationStatus.FAILED_SAFE
        and error is not None
        and error.code is NormalizedErrorCode.PROVIDER_UNAVAILABLE
    )


def stop_of(response: HostedOperationResponse) -> HostedStoppedError | None:
    """The stop an orchestrated answer reports, or ``None`` (contract §9).

    The Broker reports a stopped invocation as ``failed_safe`` carrying either
    the STOPPED code or, to clients without that capability, REQUEST_REFUSED
    with the stable message ``stopped:<control>:<scope>``.
    """
    error = response.normalized_error
    if response.status is not HostedOperationStatus.FAILED_SAFE or error is None:
        return None
    if error.code not in {
        NormalizedErrorCode.STOPPED,
        NormalizedErrorCode.REQUEST_REFUSED,
    }:
        return None
    parts = error.message.split(":", 2)
    if len(parts) == 3 and parts[0] == "stopped" and parts[1] and parts[2]:
        return HostedStoppedError(control=parts[1], scope=parts[2])
    if error.code is NormalizedErrorCode.STOPPED:
        return HostedStoppedError(control="unknown", scope="unknown")
    return None


BindingField = Literal["external_identity", "connection_revision"]
_BINDING_FIELDS: frozenset[str] = frozenset(
    {"external_identity", "connection_revision"}
)


def binding_mismatch_of(response: HostedOperationResponse) -> BindingField | None:
    """Which expected value differed, or ``None`` (contract 1.2.0 §6a).

    The Broker refuses a fenced invocation whose binding has changed as
    ``failed_safe`` with ``REQUEST_REFUSED`` and the stable message
    ``binding_mismatch:<field>``. No further provider call and no effect
    were made. ``receipt["binding"]`` holds the values the Broker found.
    """
    error = response.normalized_error
    if (
        response.status is not HostedOperationStatus.FAILED_SAFE
        or error is None
        or error.code is not NormalizedErrorCode.REQUEST_REFUSED
    ):
        return None
    prefix, _, field = error.message.partition(":")
    if prefix == "binding_mismatch" and field in _BINDING_FIELDS:
        return cast("BindingField", field)
    return None


REQUEST_CHANGED_UNDER_KEY: Final = "request_changed_under_key"


def request_changed_of(response: HostedOperationResponse) -> bool:
    """Whether a read was refused as a changed request under a used key.

    Contract 1.2.1 §6a.5 marks it, to a client declaring
    ``expected-binding``, as ``failed_safe`` with ``REQUEST_REFUSED`` and the
    exact message ``request_changed_under_key``. Nothing was recorded under
    the key and no provider call was made. A changed effect is the 400 that
    ``HostedRequestChangedError`` reports.
    """
    error = response.normalized_error
    return (
        response.status is HostedOperationStatus.FAILED_SAFE
        and error is not None
        and error.code is NormalizedErrorCode.REQUEST_REFUSED
        and error.message == REQUEST_CHANGED_UNDER_KEY
    )


def _contains_secret_key(value: JsonValue | dict[str, JsonValue] | None) -> bool:
    if isinstance(value, Mapping):
        if any(str(key).lower() in _SECRET_KEYS for key in value):
            return True
        return any(_contains_secret_key(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_secret_key(item) for item in value)
    return False


__all__ = [
    "CONNECTION_REVISION_PATTERN",
    "FenceUnsupportedReason",
    "REQUEST_CHANGED_UNDER_KEY",
    "BindingField",
    "HostedArtifactDescriptor",
    "HostedAuthorizedTransport",
    "HostedClientError",
    "HostedConnectionChangedError",
    "HostedConnectionClient",
    "HostedExpectedBinding",
    "HostedFenceUnsupportedError",
    "HostedOperationRequest",
    "HostedOperationResponse",
    "HostedOperationStatus",
    "HostedRequestChangedError",
    "HostedStoppedError",
    "HostedUnavailableError",
    "HostedUnreachableError",
    "HostedUpgradeRequiredError",
    "binding_mismatch_of",
    "is_outage",
    "request_changed_of",
    "stop_of",
]
