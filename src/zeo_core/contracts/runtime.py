"""Versioned managed-host wire models, separate from business/audit contracts.

These describe a Runtime-supervised attempt, never organizational authority by
themselves. The trusted launcher and live Runtime channel enforce admission.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from zeo_core.contracts.capabilities.manifest import CapabilityManifest

# The version every runtime-host-v1 wire model carries. Bound compatibility on
# this value as well as on the zeocore release.
RUNTIME_HOST_PROTOCOL_VERSION: Final = 1

Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
State = Literal[
    "succeeded",
    "invalid_request",
    "failed",
    "refused",
    "unavailable",
    "cancelled",
    "timed_out",
    "queued",
    "running",
    "waiting_approval",
    "needs_reconciliation",
    "protocol_error",
]


class WireModel(BaseModel):
    """Strict wire input: unknown fields and coercions are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("protocol_version", mode="before", check_fields=False)
    @classmethod
    def explicit_version(cls, value: object) -> int:
        # Literal[1] alone accepts True and 1.0 even in strict mode.
        if type(value) is not int or value != RUNTIME_HOST_PROTOCOL_VERSION:
            raise ValueError("protocol_version must be integer 1")
        return RUNTIME_HOST_PROTOCOL_VERSION


class ProviderBinding(WireModel):
    """Static admitted inventory; factory is chosen by the trusted launcher."""

    protocol_version: Literal[1]
    distribution: Identifier
    version: Identifier
    python_version: Identifier
    environment_digest: Digest
    factory: Annotated[
        str, Field(pattern=r"^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*$")
    ]
    manifests: tuple[CapabilityManifest, ...]
    manifest_digest: Digest
    generation: Annotated[int, Field(ge=1)]


class AttemptBinding(WireModel):
    """Stable identity and exact inputs; a new attempt preserves operation ID."""

    organization_id: Identifier
    project_id: Identifier
    seat_id: Identifier
    runtime_binding_id: Identifier
    packet_id: Identifier
    operation_id: Identifier
    attempt_id: Identifier
    fencing_generation: Annotated[int, Field(ge=1)]
    capability_id: Identifier
    request_digest: Digest
    manifest_digest: Digest


class LaunchContext(WireModel):
    """Delivered on a private inherited FD, not supplied in request JSON."""

    protocol_version: Literal[1]
    provider: ProviderBinding
    attempt: AttemptBinding
    bootstrap_id: Identifier
    admitted_capabilities: tuple[Identifier, ...]
    deadline_unix_ms: Annotated[int, Field(gt=0)]
    # Scope requirements are asserted by Runtime, not discovered from the host env.
    services: tuple[Identifier, ...] = ()
    credentials: tuple[Identifier, ...] = ()
    binaries: tuple[Identifier, ...] = ()
    network_hosts: tuple[Identifier, ...] = ()
    network_allowed: bool = False
    filesystem_read: bool = False
    filesystem_write: bool = False
    workspace: Identifier
    max_message_bytes: Annotated[int, Field(ge=1024, le=1048576)] = 1048576
    rpc_timeout_ms: Annotated[int, Field(ge=1, le=30000)] = 5000
    total_dispatch_budget: Annotated[int, Field(ge=0)] = 0


class InvocationRequest(WireModel):
    protocol_version: Literal[1]
    capability_id: Identifier
    arguments: dict[str, JsonValue]


class EffectRequest(WireModel):
    """A request to Runtime, not a connector dispatch authorization."""

    logical_effect_id: Identifier
    connection_ref: Identifier
    connector_revision: Digest
    operation: Identifier
    arguments: dict[str, JsonValue]


class HostResult(WireModel):
    protocol_version: Literal[1]
    binding: AttemptBinding | None
    state: State
    effect_disposition: Literal["none", "confirmed", "unknown"]
    data: JsonValue = None
    error_code: Identifier | None = None
    # These are Runtime-issued references, never arbitrary local paths.
    artifact_refs: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> HostResult:
        if self.state == "succeeded" and (
            self.error_code or self.effect_disposition == "unknown"
        ):
            raise ValueError("success cannot contain an error or unknown effect")
        if (
            self.state
            in {
                "failed",
                "refused",
                "protocol_error",
                "unavailable",
                "timed_out",
                "invalid_request",
            }
            and not self.error_code
        ):
            raise ValueError("unsuccessful state requires an error code")
        if self.effect_disposition == "unknown" and self.state not in {
            "needs_reconciliation",
            "protocol_error",
        }:
            raise ValueError("unknown effect requires reconciliation")
        return self


EXIT_CODES: dict[str, int] = {
    "succeeded": 0,
    "invalid_request": 2,
    "failed": 5,
    "refused": 3,
    "unavailable": 4,
    "cancelled": 6,
    "timed_out": 7,
    "protocol_error": 8,
    "queued": 10,
    "running": 10,
    "waiting_approval": 10,
    "needs_reconciliation": 10,
}


class RuntimeReply(WireModel):
    protocol_version: Literal[1]
    sequence: Annotated[int, Field(ge=1)]
    host_nonce: Identifier
    binding: AttemptBinding
    request_digest: Digest
    state: Literal[
        "allowed", "refused", "cancelled", "waiting_approval", "needs_reconciliation"
    ]
    data: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "EXIT_CODES",
    "RUNTIME_HOST_PROTOCOL_VERSION",
    "AttemptBinding",
    "EffectRequest",
    "HostResult",
    "InvocationRequest",
    "LaunchContext",
    "ProviderBinding",
    "RuntimeReply",
]
