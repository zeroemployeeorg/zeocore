"""Behavioural proofs for durable effect orchestration and reconciliation."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.connections.adapters.fake_subprocess_runner import FakeSubprocessRunner
from zeo_core.connections import (
    DispatchDisposition,
    EffectDispatchRequest,
    EffectDispatchResult,
    EffectExecutionResult,
    EffectOrchestrator,
    EffectPreflightError,
    ExactAuthorizationVerifier,
    KeychainEffectDispatcher,
    KeychainSecretStore,
    ReconciliationDisposition,
    ReconciliationResult,
    SQLiteConnectionStore,
)
from zeo_core.contracts.common.enums import EffectKind
from zeo_core.contracts.connections import (
    AuthorizationId,
    AuthorizationRefusalReason,
    BusinessOperation,
    Connection,
    ConnectionId,
    ConnectionStatus,
    ConnectorId,
    ConnectorRevision,
    ConnectorRevisionId,
    EffectAuthorization,
    ExecutionId,
    ExecutionState,
    IdempotencyKey,
    IdempotencyMode,
    NormalizedError,
    NormalizedErrorCode,
    OperationId,
    OrganizationId,
    RiskClass,
    SecretRef,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
CONFIRMATION = "a" * 64
RECONCILIATION = "b" * 64


class AcceptingSignatureVerifier:
    def verify_signature(self, authorization: EffectAuthorization) -> bool:
        del authorization
        return True


class RejectingSignatureVerifier:
    def verify_signature(self, authorization: EffectAuthorization) -> bool:
        del authorization
        return False


class RecordingDispatcher:
    def __init__(self, result: EffectDispatchResult | Exception) -> None:
        self.result = result
        self.calls: list[EffectDispatchRequest] = []

    def dispatch(self, request: EffectDispatchRequest) -> EffectDispatchResult:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RecordingReconciler:
    def __init__(self, result: ReconciliationResult | Exception) -> None:
        self.result = result
        self.calls: list[EffectDispatchRequest] = []

    def reconcile(self, request: EffectDispatchRequest) -> ReconciliationResult:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StateInspectingDispatcher:
    def __init__(self, store: SQLiteConnectionStore) -> None:
        self.store = store
        self.observed_state: ExecutionState | None = None
        self.calls = 0

    def dispatch(self, request: EffectDispatchRequest) -> EffectDispatchResult:
        self.calls += 1
        execution = self.store.get_execution(
            organization_id=request.organization_id,
            execution_id=request.execution_id,
        )
        self.observed_state = None if execution is None else execution.state
        if self.observed_state is not ExecutionState.DISPATCH_STARTED:
            raise AssertionError("provider entered before durable dispatch marker")
        return EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest=CONFIRMATION,
        )


class BlockingDispatcher:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def dispatch(self, _request: EffectDispatchRequest) -> EffectDispatchResult:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test dispatcher was not released")
        return EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest=CONFIRMATION,
        )


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _surface(
    tmp_path: Path,
) -> tuple[SQLiteConnectionStore, OrganizationId, EffectAuthorization, bytes]:
    store = SQLiteConnectionStore(path=tmp_path / "connections.sqlite3")
    organization_id = OrganizationId(value="org-course")
    operation_id = OperationId(value="bluesky.publish_approved_post")
    revision_id = ConnectorRevisionId(value="bluesky@1")
    connector_id = ConnectorId(value="bluesky")
    revision = ConnectorRevision(
        connector_id=connector_id,
        revision_id=revision_id,
        provider="bluesky",
        authentication_profile="app-password",
        permitted_upstream_origins=("https://bsky.social",),
        external_account_identity_probe="profile.get",
        health_probe="session.get",
        operations=(
            BusinessOperation(
                operation_id=operation_id,
                effect=EffectKind.EXTERNAL_COMMUNICATION,
                request_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "additionalProperties": False,
                },
                response_schema={"type": "object"},
                allowed_origin="https://bsky.social",
                method="POST",
                path_template="/xrpc/com.atproto.repo.createRecord",
                secret_bindings=("app-password",),
                redaction_paths=("authorization",),
                idempotency_mode=IdempotencyMode.NOT_IDEMPOTENT,
                reconciliation_strategy="lookup_record_by_content_digest",
            ),
        ),
        request_size_limit_bytes=4096,
        response_size_limit_bytes=4096,
        timeout_seconds=10.0,
        credential_injection_point="authorization-header",
        redaction_policy="drop-provider-payload",
        risk_class=RiskClass.HIGH,
        reconciliation_method="lookup_record_by_content_digest",
        provider_error_mapping_version="1",
    )
    connection = Connection(
        connection_id=ConnectionId(value="conn-course"),
        organization_id=organization_id,
        connector_id=connector_id,
        connector_revision=revision_id,
        provider_application_profile="class-demo",
        verified_external_identity="did:plc:course",
        exposed_business_operations=(operation_id,),
        secret_handle=SecretRef(handle="zc0-kc:org-course:opaque"),
        status=ConnectionStatus.ACTIVE,
        created_at=NOW,
    )
    store.save_connector_revision(organization_id=organization_id, revision=revision)
    store.save_connection(organization_id=organization_id, connection=connection)
    request_body = b'{"text":"governed hello"}'
    authorization = EffectAuthorization(
        authorization_id=AuthorizationId(value="auth-course"),
        organization_id=organization_id,
        seat_id="principal",
        runtime_binding_id="runtime-course",
        packet_id="packet-course",
        attempt_id="attempt-course",
        connection_id=connection.connection_id,
        connector_revision=revision_id,
        operation_id=operation_id,
        argument_digest=_digest(request_body),
        idempotency_key=IdempotencyKey(value="idem-course"),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        nonce="nonce-course",
        audience="zeocore",
        issuer="zeo-go",
        signature="opaque-signature",
    )
    return store, organization_id, authorization, request_body


def _orchestrator(store: SQLiteConnectionStore) -> EffectOrchestrator:
    return EffectOrchestrator(
        store=store,
        verifier=ExactAuthorizationVerifier(
            signature_verifier=AcceptingSignatureVerifier(),
            expected_audience="zeocore",
            trusted_issuers=frozenset({"zeo-go"}),
        ),
        clock=lambda: NOW,
    )


def _never_reconcile() -> RecordingReconciler:
    return RecordingReconciler(
        ReconciliationResult(
            disposition=ReconciliationDisposition.UNRESOLVED,
            evidence_digest=RECONCILIATION,
        )
    )


def test_dispatch_marker_is_durable_before_provider_call(tmp_path: Path) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = StateInspectingDispatcher(store)

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=ExecutionId(value="exec-marker"),
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    assert result.state is ExecutionState.SUCCEEDED
    assert dispatcher.calls == 1
    assert dispatcher.observed_state is ExecutionState.DISPATCH_STARTED
    states = [
        item.state
        for item in store.get_execution_history(
            organization_id=organization_id,
            execution_id=ExecutionId(value="exec-marker"),
        )
    ]
    assert states == [
        ExecutionState.CREATED,
        ExecutionState.AUTHORIZATION_VERIFIED,
        ExecutionState.PREPARED,
        ExecutionState.DISPATCH_STARTED,
        ExecutionState.SUCCEEDED,
    ]


def test_direct_success_has_durable_positive_confirmation(tmp_path: Path) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(
        EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest=CONFIRMATION,
        )
    )
    execution_id = ExecutionId(value="exec-success")

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=execution_id,
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    receipts = store.get_receipts_for_execution(
        organization_id=organization_id, execution_id=execution_id
    )
    assert result.state is ExecutionState.SUCCEEDED
    assert len(dispatcher.calls) == 1
    assert len(receipts) == 1
    assert receipts[0].confirmation_evidence_ref is not None
    evidence = store.get_confirmation_evidence(
        organization_id=organization_id,
        evidence_ref=receipts[0].confirmation_evidence_ref,
    )
    assert evidence is not None
    assert evidence.confirmation_digest == CONFIRMATION


def test_timeout_reconciles_without_redispatch_and_preserves_ambiguity(
    tmp_path: Path,
) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(TimeoutError("provider timed out"))
    reconciler = RecordingReconciler(
        ReconciliationResult(
            disposition=ReconciliationDisposition.CONFIRMED,
            evidence_digest=RECONCILIATION,
            confirmation_digest=CONFIRMATION,
        )
    )
    execution_id = ExecutionId(value="exec-reconciled")

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=execution_id,
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=reconciler,
    )

    receipts = store.get_receipts_for_execution(
        organization_id=organization_id, execution_id=execution_id
    )
    assert result.state is ExecutionState.SUCCEEDED
    assert len(dispatcher.calls) == 1
    assert len(reconciler.calls) == 1
    assert [item.final_state for item in receipts] == [
        ExecutionState.AMBIGUOUS,
        ExecutionState.SUCCEEDED,
    ]
    assert receipts[1].resolves_ambiguous_recorded_at == receipts[0].recorded_at
    assert receipts[1].reconciliation_evidence == RECONCILIATION


def test_unresolved_reconciliation_stays_ambiguous_and_appends_attempt(
    tmp_path: Path,
) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    canary = "RAW_PROVIDER_SECRET_CANARY"
    dispatcher = RecordingDispatcher(RuntimeError(canary))
    reconciler = _never_reconcile()
    execution_id = ExecutionId(value="exec-unresolved")

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=execution_id,
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=reconciler,
    )

    execution = store.get_execution(
        organization_id=organization_id, execution_id=execution_id
    )
    receipts = store.get_receipts_for_execution(
        organization_id=organization_id, execution_id=execution_id
    )
    assert result.state is ExecutionState.AMBIGUOUS
    assert execution is not None and execution.state is ExecutionState.AMBIGUOUS
    assert [item.final_state for item in receipts] == [
        ExecutionState.AMBIGUOUS,
        ExecutionState.AMBIGUOUS,
    ]
    assert receipts[1].reconciliation_attempt_evidence == RECONCILIATION
    assert canary.encode() not in (tmp_path / "connections.sqlite3").read_bytes()


def test_proved_no_effect_is_failed_safe_and_never_reconciled(tmp_path: Path) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(
        EffectDispatchResult(
            disposition=DispatchDisposition.FAILED_SAFE,
            normalized_error=NormalizedError(
                code=NormalizedErrorCode.FAILED_SAFE,
                message="provider proved no effect was applied",
            ),
        )
    )
    reconciler = _never_reconcile()

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=ExecutionId(value="exec-safe"),
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=reconciler,
    )

    assert result.state is ExecutionState.FAILED_SAFE
    assert len(dispatcher.calls) == 1
    assert reconciler.calls == []


def test_stopped_effect_is_dispatched_once_and_a_replay_never_redispatches(
    tmp_path: Path,
) -> None:
    """A Broker stop (ZEOconnect contract v1 §9) cannot become a retry loop.

    The stop arrives as a returned failed-safe result, so it is recorded once,
    never reconciled, and a replay of the same authorization returns the
    recorded outcome without reaching the dispatcher again.
    """

    store, organization_id, authorization, request_body = _surface(tmp_path)
    orchestrator = _orchestrator(store)
    dispatcher = RecordingDispatcher(
        EffectDispatchResult(
            disposition=DispatchDisposition.FAILED_SAFE,
            normalized_error=NormalizedError(
                code=NormalizedErrorCode.REQUEST_REFUSED,
                message="stopped:connections:org-course",
            ),
        )
    )
    reconciler = _never_reconcile()

    def invoke(execution_id: str) -> EffectExecutionResult:
        return orchestrator.execute(
            organization_id=organization_id,
            connection_id=authorization.connection_id,
            connector_revision=authorization.connector_revision,
            operation_id=authorization.operation_id,
            authorization=authorization,
            execution_id=ExecutionId(value=execution_id),
            request_body=request_body,
            dispatcher=dispatcher,
            reconciler=reconciler,
        )

    first = invoke("exec-stopped")
    replays = [invoke(f"exec-stopped-replay-{n}") for n in range(3)]

    assert first.state is ExecutionState.FAILED_SAFE
    assert all(replay.state is ExecutionState.FAILED_SAFE for replay in replays)
    assert {replay.execution_id for replay in replays} == {first.execution_id}
    assert len(dispatcher.calls) == 1
    assert reconciler.calls == []


def test_request_mismatch_refuses_before_provider_call_and_is_durable(
    tmp_path: Path,
) -> None:
    store, organization_id, authorization, _ = _surface(tmp_path)
    dispatcher = RecordingDispatcher(RuntimeError("must not run"))
    execution_id = ExecutionId(value="exec-refused")

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=execution_id,
        request_body=b"different request",
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    assert result.state is ExecutionState.REFUSED
    assert result.refusal_reason is AuthorizationRefusalReason.REQUEST_DIGEST_MISMATCH
    assert dispatcher.calls == []
    receipts = store.get_receipts_for_execution(
        organization_id=organization_id, execution_id=execution_id
    )
    assert len(receipts) == 1
    assert receipts[0].final_state is ExecutionState.REFUSED


def test_authorization_is_compared_to_independent_trusted_intent(
    tmp_path: Path,
) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(RuntimeError("must not run"))

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=ConnectionId(value="a-different-connection"),
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=ExecutionId(value="exec-binding-refused"),
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    assert result.state is ExecutionState.REFUSED
    assert result.refusal_reason is AuthorizationRefusalReason.CONNECTION_MISMATCH
    assert dispatcher.calls == []


def test_unverifiable_signature_refuses_before_provider_call(tmp_path: Path) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(RuntimeError("must not run"))
    orchestrator = EffectOrchestrator(
        store=store,
        verifier=ExactAuthorizationVerifier(
            signature_verifier=RejectingSignatureVerifier(),
            expected_audience="zeocore",
            trusted_issuers=frozenset({"zeo-go"}),
        ),
        clock=lambda: NOW,
    )

    result = orchestrator.execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=ExecutionId(value="exec-bad-signature"),
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    assert result.state is ExecutionState.REFUSED
    assert result.refusal_reason is AuthorizationRefusalReason.SIGNATURE_UNVERIFIABLE
    assert dispatcher.calls == []


def test_absent_authorization_refuses_with_zero_persistence_or_provider_calls(
    tmp_path: Path,
) -> None:
    store, organization_id, _, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(RuntimeError("must not run"))

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=ConnectionId(value="conn-course"),
        connector_revision=ConnectorRevisionId(value="bluesky@1"),
        operation_id=OperationId(value="bluesky.publish_approved_post"),
        authorization=None,
        execution_id=ExecutionId(value="exec-absent"),
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    assert result == result.__class__(
        state=ExecutionState.REFUSED,
        execution_id=None,
        refusal_reason=AuthorizationRefusalReason.ABSENT,
    )
    assert dispatcher.calls == []
    assert (
        store.get_execution(
            organization_id=organization_id,
            execution_id=ExecutionId(value="exec-absent"),
        )
        is None
    )


def test_inactive_connection_fails_preflight_without_dispatch(tmp_path: Path) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    connection = store.get_connection(
        organization_id=organization_id, connection_id=authorization.connection_id
    )
    assert connection is not None
    store.save_connection(
        organization_id=organization_id,
        connection=connection.model_copy(
            update={"status": ConnectionStatus.REPAIR_REQUIRED}
        ),
    )
    dispatcher = RecordingDispatcher(RuntimeError("must not run"))

    with pytest.raises(EffectPreflightError, match="not active"):
        _orchestrator(store).execute(
            organization_id=organization_id,
            connection_id=authorization.connection_id,
            connector_revision=authorization.connector_revision,
            operation_id=authorization.operation_id,
            authorization=authorization,
            execution_id=ExecutionId(value="exec-inactive"),
            request_body=request_body,
            dispatcher=dispatcher,
            reconciler=_never_reconcile(),
        )

    assert dispatcher.calls == []


def test_provider_detail_cannot_cross_the_orchestration_boundary() -> None:
    canary = "RAW_PROVIDER_SECRET_CANARY"
    with pytest.raises(ValidationError, match="provider_detail is forbidden"):
        EffectDispatchResult(
            disposition=DispatchDisposition.FAILED_SAFE,
            normalized_error=NormalizedError(
                code=NormalizedErrorCode.FAILED_SAFE,
                message="safe failure",
                provider_detail=canary,
            ),
        )


def test_dispatch_request_repr_hides_request_bytes(tmp_path: Path) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    dispatcher = RecordingDispatcher(
        EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest=CONFIRMATION,
        )
    )
    _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=ExecutionId(value="exec-repr"),
        request_body=request_body,
        dispatcher=dispatcher,
        reconciler=_never_reconcile(),
    )

    assert request_body.decode() not in repr(dispatcher.calls[0])


def test_concurrent_idempotency_calls_share_one_execution_and_one_dispatch(
    tmp_path: Path,
) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    orchestrator = _orchestrator(store)
    dispatcher = BlockingDispatcher()
    reconciler = _never_reconcile()

    def invoke(execution_id: str) -> EffectExecutionResult:
        return orchestrator.execute(
            organization_id=organization_id,
            connection_id=authorization.connection_id,
            connector_revision=authorization.connector_revision,
            operation_id=authorization.operation_id,
            authorization=authorization,
            execution_id=ExecutionId(value=execution_id),
            request_body=request_body,
            dispatcher=dispatcher,
            reconciler=reconciler,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(invoke, "exec-concurrent-winner")
        assert dispatcher.entered.wait(timeout=2)
        duplicate = pool.submit(invoke, "exec-concurrent-duplicate")
        duplicate_result = duplicate.result(timeout=2)
        dispatcher.release.set()
        winner_result = winner.result(timeout=2)

    assert dispatcher.calls == 1
    assert duplicate_result.execution_id == winner_result.execution_id
    assert winner_result.execution_id == ExecutionId(value="exec-concurrent-winner")
    assert winner_result.state is ExecutionState.SUCCEEDED


def test_vertical_effect_uses_keychain_custody_and_persists_only_evidence(
    tmp_path: Path,
) -> None:
    store, organization_id, authorization, request_body = _surface(tmp_path)
    custody = KeychainSecretStore(
        service_prefix="zc0-vertical", runner=FakeSubprocessRunner(), clock=lambda: NOW
    )
    material = "ZC0-VERTICAL-CUSTODY-CANARY"
    ref = custody.put(organization_id=organization_id, material=material)
    connection = store.get_connection(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
    )
    assert connection is not None
    store.save_connection(
        organization_id=organization_id,
        connection=connection.model_copy(update={"secret_handle": ref}),
    )
    provider_called = False

    def provider(
        resolved_material: str, request: EffectDispatchRequest
    ) -> EffectDispatchResult:
        nonlocal provider_called
        if resolved_material != material or request.request_body != request_body:
            raise RuntimeError("vertical custody binding mismatch")
        provider_called = True
        return EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest="f" * 64,
        )

    result = _orchestrator(store).execute(
        organization_id=organization_id,
        connection_id=authorization.connection_id,
        connector_revision=authorization.connector_revision,
        operation_id=authorization.operation_id,
        authorization=authorization,
        execution_id=ExecutionId(value="exec-vertical"),
        request_body=request_body,
        dispatcher=KeychainEffectDispatcher(store=custody, invoke=provider),
        reconciler=_never_reconcile(),
    )

    assert provider_called
    assert result.state is ExecutionState.SUCCEEDED
    assert material.encode() not in (tmp_path / "connections.sqlite3").read_bytes()
    assert material not in repr(result)
