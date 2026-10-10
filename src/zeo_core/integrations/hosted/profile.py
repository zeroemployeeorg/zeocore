"""Inert service requirements and the managed-profile resolution state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zeo_core.integrations.hosted.client import (
    CONNECTION_REVISION_PATTERN,
    HostedExpectedBinding,
    HostedFenceUnsupportedError,
)


class ExecutionProfile(StrEnum):
    """Explicit execution placements; automatic choice is policy, not a profile."""

    FAKE = "fake"
    LOCAL = "local"
    HOSTED = "hosted"
    GOVERNED = "governed"


class ServiceResolutionStatus(StrEnum):
    """Connection lifecycle only; operation outcomes use HostedOperationStatus."""

    READY = "ready"
    CONNECTION_REQUIRED = "connection_required"
    CONNECTION_SELECTION_REQUIRED = "connection_selection_required"
    RESOURCE_SELECTION_REQUIRED = "resource_selection_required"
    REPAIR_REQUIRED = "repair_required"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"


class HostedConnectionStatus(StrEnum):
    """Sanitized connection states returned by the member API."""

    ACTIVE = "active"
    REPAIR_REQUIRED = "repair_required"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"


class UnavailableCode(StrEnum):
    """Stable local reasons for a service that cannot currently resolve."""

    SECURE_STORE_REQUIRED = "secure_store_required"
    CONNECTION_CATALOG_REQUIRED = "connection_catalog_required"
    PROFILE_NOT_CONFIGURED = "profile_not_configured"
    GOVERNED_PORT_REQUIRED = "governed_port_required"
    SERVICE_NOT_AVAILABLE = "service_not_available"


class ServiceRequirement(BaseModel):
    """One logical service and its existing public operation identities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str = Field(..., pattern=r"^[a-z][a-z0-9]*(?:\.[a-z0-9]+)*$")
    operations: tuple[str, ...] = Field(..., min_length=1, max_length=20)

    @field_validator("operations")
    @classmethod
    def _operations_are_unique(cls, operations: tuple[str, ...]) -> tuple[str, ...]:
        if len(operations) != len(set(operations)):
            raise ValueError("service operations must be unique")
        return operations

    @model_validator(mode="after")
    def _operations_belong_to_service(self) -> ServiceRequirement:
        prefix = self.service + "."
        if any(not operation.startswith(prefix) for operation in self.operations):
            raise ValueError("every operation must use the service identity prefix")
        return self


class OpaqueConnectionHandle(BaseModel):
    """Sanitized handle selected by a user; it carries no tenant or revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str = Field(..., pattern=r"^con_[A-Za-z0-9_-]{8,200}$")

    def __str__(self) -> str:
        return self.value


class HostedResourceSummary(BaseModel):
    """A provider resource the user explicitly selected in ZEOconnect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str = Field(..., min_length=1, max_length=500)
    display_name: str = Field(..., min_length=1, max_length=500)
    media_type: str = Field(..., min_length=1, max_length=200)
    operations: tuple[str, ...] = Field(..., min_length=1, max_length=20)


class HostedConnectionSummary(BaseModel):
    """Device-visible connection metadata with no custody or tenant fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handle: OpaqueConnectionHandle
    service: str
    external_identity: str = Field(..., min_length=1, max_length=500)
    status: HostedConnectionStatus
    operations: tuple[str, ...]
    resources: tuple[HostedResourceSummary, ...] = ()
    # Present when the Broker supports the binding fence (contract 1.2.0).
    connection_revision: str | None = Field(
        default=None, pattern=CONNECTION_REVISION_PATTERN
    )

    def expected_binding(self) -> HostedExpectedBinding:
        """The binding to fence an invocation on this connection with.

        Raises ``HostedFenceUnsupportedError`` when the listing carried no
        revision: a fence that cannot be checked is never sent unfenced.
        """
        if self.connection_revision is None:
            raise HostedFenceUnsupportedError("no_revision")
        return HostedExpectedBinding(
            external_identity=self.external_identity,
            connection_revision=self.connection_revision,
        )

    def satisfies(self, requirement: ServiceRequirement) -> bool:
        return self.service == requirement.service and set(
            requirement.operations
        ).issubset(self.operations)


class _Resolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    operations: tuple[str, ...]


class ConnectionRequired(_Resolution):
    status: Literal[ServiceResolutionStatus.CONNECTION_REQUIRED] = (
        ServiceResolutionStatus.CONNECTION_REQUIRED
    )


class ConnectionSelectionRequired(_Resolution):
    status: Literal[ServiceResolutionStatus.CONNECTION_SELECTION_REQUIRED] = (
        ServiceResolutionStatus.CONNECTION_SELECTION_REQUIRED
    )
    candidates: tuple[HostedConnectionSummary, ...]


class ResourceSelectionRequired(_Resolution):
    status: Literal[ServiceResolutionStatus.RESOURCE_SELECTION_REQUIRED] = (
        ServiceResolutionStatus.RESOURCE_SELECTION_REQUIRED
    )
    connection: HostedConnectionSummary


class RepairRequired(_Resolution):
    status: Literal[ServiceResolutionStatus.REPAIR_REQUIRED] = (
        ServiceResolutionStatus.REPAIR_REQUIRED
    )
    connection: HostedConnectionSummary


class Revoked(_Resolution):
    status: Literal[ServiceResolutionStatus.REVOKED] = ServiceResolutionStatus.REVOKED
    connection: HostedConnectionSummary


class Unavailable(_Resolution):
    status: Literal[ServiceResolutionStatus.UNAVAILABLE] = (
        ServiceResolutionStatus.UNAVAILABLE
    )
    code: UnavailableCode


ServiceT = TypeVar("ServiceT")


@dataclass(frozen=True)
class Ready(Generic[ServiceT]):
    """Resolved service kept outside serialization to avoid dumping its state."""

    service: ServiceT
    connection: HostedConnectionSummary | None = None
    status: ServiceResolutionStatus = ServiceResolutionStatus.READY


ResolutionResult = (
    Ready[object]
    | ConnectionRequired
    | ConnectionSelectionRequired
    | ResourceSelectionRequired
    | RepairRequired
    | Revoked
    | Unavailable
)


__all__ = [
    "ConnectionRequired",
    "ConnectionSelectionRequired",
    "ExecutionProfile",
    "HostedConnectionStatus",
    "HostedConnectionSummary",
    "HostedResourceSummary",
    "OpaqueConnectionHandle",
    "Ready",
    "RepairRequired",
    "ResolutionResult",
    "ResourceSelectionRequired",
    "Revoked",
    "ServiceRequirement",
    "ServiceResolutionStatus",
    "Unavailable",
    "UnavailableCode",
]
