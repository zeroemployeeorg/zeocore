"""The hosted registry is explicit and closed: nothing is discovered or imported."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from zeo_core.integrations.hosted import (
    REVIEWED_HOSTED_SERVICES,
    DeviceSession,
    ExecutionProfile,
    HostedConnectionClient,
    HostedConnectionStatus,
    HostedConnectionSummary,
    HostedGoogleDriveService,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedServiceBinding,
    HostedServiceRegistration,
    HostedServiceRegistry,
    InMemorySecureSessionStore,
    OpaqueConnectionHandle,
    Ready,
    ResourceSelectionRequired,
    ServiceRequirement,
    ServiceResolver,
    Unavailable,
    UnavailableCode,
)

LEDGER = "example.ledger"
READ = "example.ledger.entries.list"
EXPORT = "example.ledger.entries.export"
NOW = datetime(2026, 9, 20, tzinfo=UTC)


class _NeverInvoked:
    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        raise AssertionError("resolution must not reach the network")

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
        raise AssertionError("resolution must not reach the network")


class _LedgerProxy:
    def __init__(
        self, *, client: HostedConnectionClient, binding: HostedServiceBinding
    ) -> None:
        self.client = client
        self.binding = binding


def _registration(**changes: object) -> HostedServiceRegistration:
    values: dict[str, object] = {
        "service": LEDGER,
        "operations": frozenset({READ, EXPORT}),
        "factory": _LedgerProxy,
        "resource_bound_operations": frozenset({EXPORT}),
    }
    return HostedServiceRegistration(**{**values, **changes})  # type: ignore[arg-type]


def _resolver(registry: HostedServiceRegistry, *operations: str) -> ServiceResolver:
    store = InMemorySecureSessionStore()
    store.save(
        DeviceSession(
            device_id="dev_12345678-1234-4234-9234-123456789012",
            access_token=SecretStr("access-authority-canary"),
            refresh_token=SecretStr("refresh-authority-canary"),
            access_expires_at=NOW + timedelta(minutes=15),
            refresh_expires_at=NOW + timedelta(days=30),
        )
    )
    return ServiceResolver(
        profile=ExecutionProfile.HOSTED,
        hosted_client=HostedConnectionClient(transport=_NeverInvoked()),
        session_store=store,
        hosted_connections=(
            HostedConnectionSummary(
                handle=OpaqueConnectionHandle(value="con_ledger_12345678"),
                service=LEDGER,
                external_identity="Example Ltd",
                status=HostedConnectionStatus.ACTIVE,
                operations=operations,
            ),
        ),
        hosted_registry=registry,
    )


def test_reviewed_default_is_the_previous_service_plus_the_image_services() -> None:
    assert REVIEWED_HOSTED_SERVICES.services == frozenset(
        {"google.drive", "gemini.image", "recraft"}
    )
    download = ServiceRequirement(
        service="google.drive", operations=("google.drive.file.download",)
    )
    assert REVIEWED_HOSTED_SERVICES.requires_selected_resource(download)
    client = HostedConnectionClient(transport=_NeverInvoked())
    binding = HostedServiceBinding(connection_id="con_google_12345678")
    built = REVIEWED_HOSTED_SERVICES.build(download, client=client, binding=binding)
    assert isinstance(built, HostedGoogleDriveService)
    for service, operation in (
        ("google.drive", "google.drive.file.upload"),
        ("google.docs", "google.docs.document.read"),
        ("revolut.business", "revolut.business.accounts.list"),
    ):
        unknown = ServiceRequirement(service=service, operations=(operation,))
        assert (
            REVIEWED_HOSTED_SERVICES.build(unknown, client=client, binding=binding)
            is None
        )
        assert not REVIEWED_HOSTED_SERVICES.requires_selected_resource(unknown)


def test_registration_refuses_foreign_unbound_or_incompatible_declarations() -> None:
    invalid_changes: tuple[dict[str, object], ...] = (
        {"service": "Example Ledger"},
        {"service": "https://evil.invalid"},
        {"operations": frozenset()},
        {"operations": frozenset({"other.service.read"})},
        {"resource_bound_operations": frozenset({"example.ledger.unregistered"})},
        {"protocol_version": "2"},
    )
    for invalid in invalid_changes:
        with pytest.raises(ValueError):
            _registration(**invalid)


def test_registry_refuses_duplicate_service_identities() -> None:
    with pytest.raises(ValueError, match="duplicate hosted service registration"):
        HostedServiceRegistry((_registration(), _registration()))
    assert not hasattr(HostedServiceRegistry, "register")


def test_resolver_builds_only_registered_operations_without_network() -> None:
    registry = HostedServiceRegistry((_registration(),))
    read = ServiceRequirement(service=LEDGER, operations=(READ,))

    ready = _resolver(registry, READ, EXPORT).resolve(read)
    assert isinstance(ready, Ready) and isinstance(ready.service, _LedgerProxy)
    assert ready.service.binding.connection_id == "con_ledger_12345678"

    export = ServiceRequirement(service=LEDGER, operations=(EXPORT,))
    needs_resource = _resolver(registry, READ, EXPORT).resolve(export)
    assert isinstance(needs_resource, ResourceSelectionRequired)

    offered_not_registered = "example.ledger.entries.delete"
    refused = _resolver(registry, offered_not_registered).resolve(
        ServiceRequirement(service=LEDGER, operations=(offered_not_registered,))
    )
    assert isinstance(refused, Unavailable)
    assert refused.code is UnavailableCode.SERVICE_NOT_AVAILABLE

    default = _resolver(REVIEWED_HOSTED_SERVICES, READ).resolve(read)
    assert isinstance(default, Unavailable)
