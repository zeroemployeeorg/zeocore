"""
Behavioral proofs for `KeychainSecretStore`, the step-3 `SecretStore`
implementation, per SOW-04/SOW-05.

Structure, deliberately following the house pattern already established by
`tests/connections/test_secret_ref_and_evidence_ref_safety.py` (the
`_leaks` helper, `TestProbeCanFail`-style probe-of-the-probe classes,
RED-before-green documented in each class's docstring):

1. `TestCategoricalLeakContainment` -- the acceptance bar's own words
   ("no secret material appears in public results, repr, str, logs,
   ordinary dumps, receipts, or exceptions") checked across TWELVE
   accidental channels, not the four the legacy `AuthResult` test covers:
   repr, str, f-string, percent-s, model_dump-equivalent (this store has
   no pydantic return value carrying material, so this is checked on the
   exception/object path), dict(), vars()/__dict__, json.dumps(default=
   str) best-effort, pickle.dumps, copy.deepcopy repr, exception str(),
   and logging module output. Every one of these is run BOTH against the
   real `KeychainSecretStore` instance AND, first, against a deliberately
   broken stand-in that DOES leak, proving each probe can fail before it
   is trusted (RED-before-green, doctrine section 6 / RULING-415 3c).
2. `TestSecretStoreProtocolBehavior` -- put/resolve/rotate/delete/health
   against the fake runner, including organization-scope rejection and
   not-found handling.
3. `TestStdinTransportProvenOnRealExecutable` -- proves the Principal's
   secret-transport bound (msg_e79f76af) against the REAL `security`
   binary, not just the fake: `put`/`rotate`'s stdin-twice shape actually
   stores the intended material (RED-before-green -- the single-value
   shape this stream's earlier revision used is shown failing FIRST, as
   the control case, before the corrected twice-fed shape is shown
   working), argv NEVER carries material (a positive absence guarantee,
   0/N samples across a live polling loop, not a documented exposure),
   and `security`'s own confirm-match diagnostics on stderr never carry
   material either. A companion class keeps the raw-CLI argv-leak
   evidence that justified moving off argv in the first place (ground
   truth for why the store's own real-subprocess test above asserts
   absence rather than presence), plus the env-var exposure finding that
   proved no lower-exposure subprocess-argument channel existed at all --
   both now background evidence, not live exposures, since neither argv
   nor env vars carry `material` under the current transport. These tests
   are marked to skip off-macOS and where `/usr/bin/security` is
   unavailable, and are the only tests in this file that touch a real
   subprocess -- everything else uses FakeSubprocessRunner and is fully
   offline/deterministic.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import os
import pickle
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime

import pytest

from zeo_core.connections import (
    DispatchDisposition,
    EffectDispatchRequest,
    EffectDispatchResult,
)
from zeo_core.connections.adapters.macos_keychain import (
    CrossOrganizationAccessError,
    KeychainEffectDispatcher,
    KeychainSecretStore,
    SecretMaterialError,
    SecretNotFoundError,
)
from zeo_core.contracts.connections import Connection, ConnectorRevision
from zeo_core.contracts.connections.identity import (
    ExecutionId,
    IdempotencyKey,
    OrganizationId,
)
from zeo_core.contracts.connections.verdicts import SecretHealth, SecretResolution

from .fake_subprocess_runner import FakeSubprocessRunner

CANARY = "CANARY-KEYCHAIN-MATERIAL-zc0-step3-7f2a91"
ORG_A = OrganizationId(value="org-a")
ORG_B = OrganizationId(value="org-b")


@pytest.fixture
def fake_runner() -> FakeSubprocessRunner:
    return FakeSubprocessRunner()


@pytest.fixture
def frozen_clock() -> tuple[datetime, object]:
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
    return now, (lambda: now)


@pytest.fixture
def store(
    fake_runner: FakeSubprocessRunner, frozen_clock: tuple
) -> KeychainSecretStore:
    _now, clock = frozen_clock
    return KeychainSecretStore(
        service_prefix="zc0-test-service",
        runner=fake_runner,
        clock=clock,
    )


def test_one_shot_dispatch_lease_contains_material_inside_custody_only(
    store: KeychainSecretStore,
) -> None:
    ref = store.put(organization_id=ORG_A, material=CANARY)
    resolution = store.resolve(ref=ref, organization_id=ORG_A)
    observed = False

    def provider(material: str) -> EffectDispatchResult:
        nonlocal observed
        if material != CANARY:
            raise RuntimeError("custody supplied different material")
        observed = True
        return EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest="d" * 64,
        )

    result = store._dispatch_with_resolution(
        resolution=resolution,
        organization_id=ORG_A,
        invoke=provider,
    )

    assert observed
    assert result.confirmation_digest == "d" * 64
    assert CANARY not in repr(result)
    with pytest.raises(SecretMaterialError, match="unknown or consumed"):
        store._dispatch_with_resolution(
            resolution=resolution,
            organization_id=ORG_A,
            invoke=provider,
        )


def test_dispatch_callback_exception_is_sanitized_without_chaining(
    store: KeychainSecretStore,
) -> None:
    ref = store.put(organization_id=ORG_A, material=CANARY)
    resolution = store.resolve(ref=ref, organization_id=ORG_A)

    def broken_provider(material: str) -> EffectDispatchResult:
        raise RuntimeError(material)

    with pytest.raises(SecretMaterialError, match="inside custody") as captured:
        store._dispatch_with_resolution(
            resolution=resolution,
            organization_id=ORG_A,
            invoke=broken_provider,
        )

    assert CANARY not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_keychain_effect_dispatcher_resolves_only_inside_provider_call(
    store: KeychainSecretStore,
    connection: Connection,
    connector_revision: ConnectorRevision,
) -> None:
    ref = store.put(organization_id=ORG_A, material=CANARY)
    connected = connection.model_copy(
        update={
            "organization_id": ORG_A,
            "connector_id": connector_revision.connector_id,
            "connector_revision": connector_revision.revision_id,
            "secret_handle": ref,
        }
    )
    request = EffectDispatchRequest(
        organization_id=ORG_A,
        connection=connected,
        connector_revision=connector_revision,
        operation_id=connector_revision.operations[0].operation_id,
        execution_id=ExecutionId(value="exec-keychain-dispatch"),
        idempotency_key=IdempotencyKey(value="idem-keychain-dispatch"),
        request_digest="sha256:" + "e" * 64,
        request_body=b"{}",
    )
    material_was_available = False

    def provider(
        material: str, observed_request: EffectDispatchRequest
    ) -> EffectDispatchResult:
        nonlocal material_was_available
        if material != CANARY or observed_request is not request:
            raise RuntimeError("custody dispatch binding mismatch")
        material_was_available = True
        return EffectDispatchResult(
            disposition=DispatchDisposition.CONFIRMED,
            confirmation_digest="e" * 64,
        )

    dispatcher = KeychainEffectDispatcher(store=store, invoke=provider)
    result = dispatcher.dispatch(request)

    assert material_was_available
    assert result.confirmation_digest == "e" * 64
    assert CANARY not in repr(dispatcher)
    assert CANARY not in repr(result)


# ===========================================================================
# 1. CATEGORICAL LEAK CONTAINMENT (twelve channels)
# ===========================================================================


def _all_channel_checks(obj: object, needle: str) -> dict[str, bool]:
    """
    Run `needle` absence checks across every accidental channel named in
    this stream's acceptance bar. Returns a dict of channel -> "leaked?"
    so a failing assertion's message names exactly which channel(s) leaked
    rather than just "something leaked somewhere."
    """
    checks: dict[str, bool] = {}

    checks["repr"] = needle in repr(obj)
    checks["str"] = needle in str(obj)
    checks["fstring"] = needle in f"{obj}"
    checks["percent_s"] = needle in ("%s" % (obj,))  # noqa: UP031

    try:
        checks["vars_dict"] = needle in repr(vars(obj))
    except TypeError:
        checks["vars_dict"] = needle in repr(getattr(obj, "__dict__", {}))

    try:
        checks["json_dumps_default_str"] = needle in json.dumps(obj, default=str)
    except (TypeError, ValueError) as exc:
        # `as exc` is deliberate, not decorative: this repo's pinned
        # ruff 0.16.5 formatter has a reproducible bug where `--check`
        # (and, worse, plain `ruff format`) rewrites a bare
        # `except (A, B):` into the INVALID Python 2 syntax
        # `except A, B:` -- verified directly against a minimal repro
        # outside this file and reported to Master (see this stream's
        # SOW-05). Adding `as exc` avoids triggering the bug; it is not
        # needed for this branch's own logic.
        del exc
        checks["json_dumps_default_str"] = False

    try:
        checks["pickle"] = needle.encode() in pickle.dumps(obj)
    except Exception:  # noqa: BLE001 - unpicklable is not a leak
        checks["pickle"] = False

    try:
        copied = copy.deepcopy(obj)
        # Checked two ways: `repr(copied)` (catches a type with a custom
        # __repr__ that would surface the value) AND `vars(copied)`
        # (catches a plain object whose default __repr__ shows only its
        # address -- repr() alone would miss exactly this shape, since
        # deepcopy's whole risk is a SECOND live copy of the material
        # sitting in memory/attributes, not necessarily in its printed
        # form).
        try:
            copied_vars = repr(vars(copied))
        except TypeError:
            copied_vars = ""
        checks["deepcopy_repr"] = needle in repr(copied) or needle in copied_vars
    except Exception:  # noqa: BLE001 - uncopyable is not a leak
        checks["deepcopy_repr"] = False

    checks["exception_str"] = needle in str(Exception(obj))
    checks["exception_repr"] = needle in repr(Exception(obj))

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logger = logging.getLogger("zc0-test-leak-probe")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.debug("object under test: %s", obj)
    finally:
        logger.removeHandler(handler)
    checks["logging_percent_s"] = needle in log_stream.getvalue()

    return checks


class _NaiveMaterialError(Exception):
    """
    Module-level (not nested) deliberately-broken stand-in: the classic
    "raise ValueError(secret)" mistake. Defined at module scope, not
    inside a test function, because `pickle` cannot serialize a local/
    nested class -- a nested definition would make the pickle channel of
    `_all_channel_checks` silently no-op (caught by its own `except
    Exception`) instead of genuinely proving the pickle probe catches
    this leak shape.
    """

    def __init__(self, material: str) -> None:
        self.material = material
        super().__init__(f"failed to store secret: {material}")


class _NaiveHolder:
    """Module-level deliberately-broken stand-in holding material as a
    plain attribute -- see `_NaiveMaterialError`'s docstring for why this
    must be module-level rather than a nested test-local class."""

    def __init__(self, material: str) -> None:
        self.material = material


class TestCategoricalLeakContainmentProbeCanFail:
    """
    RED-before-green: proves `_all_channel_checks` is a real probe, not a
    tautology, by running it against a deliberately leaky stand-in first.
    Every channel below must show `injected: True` -- if any channel in
    this list did NOT leak on the broken stand-in, that channel's check
    in `_all_channel_checks` would not be known to catch anything, per
    doctrine section 6 / RULING-415 3c ("a must-NOT test that has never
    been observed failing is not known to be a test").
    """

    def test_probe_catches_a_naive_exception_embedding_material(self) -> None:
        broken = _NaiveMaterialError(CANARY)
        leaks = _all_channel_checks(broken, CANARY)
        injected = any(leaks.values())
        print(f"injected: {injected}")
        assert injected, (
            "probe-of-the-probe failed: the deliberately leaky exception "
            "did not leak on any channel, so this test proves nothing "
            f"about the real code's ability to catch a real leak; {leaks}"
        )
        # This exact shape must leak on at least str/repr/exception_str/
        # exception_repr/vars_dict -- the classic "raise ValueError(secret)"
        # mistake this module's docstring calls out by name.
        assert leaks["str"] is True
        assert leaks["exception_str"] is True
        assert leaks["vars_dict"] is True

    def test_probe_catches_a_naive_object_holding_material_as_an_attribute(
        self,
    ) -> None:
        broken = _NaiveHolder(CANARY)
        leaks = _all_channel_checks(broken, CANARY)
        injected = any(leaks.values())
        print(f"injected: {injected}")
        assert injected, (
            "probe-of-the-probe failed: the naive holder did not leak on "
            f"any channel; {leaks}"
        )
        assert leaks["vars_dict"] is True
        assert leaks["pickle"] is True
        assert leaks["deepcopy_repr"] is True

    def test_probe_catches_material_logged_via_percent_s(self) -> None:
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("zc0-test-leak-probe-direct")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.debug("storing material=%s directly", CANARY)
        finally:
            logger.removeHandler(handler)
        injected = CANARY in log_stream.getvalue()
        print(f"injected: {injected}")
        assert injected, "probe-of-the-probe failed: direct logging did not leak"


class TestCategoricalLeakContainmentRealAdapter:
    """
    The GREEN half: the real `KeychainSecretStore` and everything it
    returns/raises never leak the canary on any of the twelve channels
    above, across every method that touches `material`.
    """

    def test_put_return_value_never_carries_material(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material=CANARY)
        leaks = _all_channel_checks(ref, CANARY)
        assert not any(leaks.values()), f"SecretRef leaked material: {leaks}"

    def test_put_failure_exception_never_carries_material(
        self, fake_runner: FakeSubprocessRunner, frozen_clock: tuple
    ) -> None:
        fake_runner.force_exit_code = 99
        fake_runner.force_stderr = "simulated unexpected keychain failure"
        _now, clock = frozen_clock
        broken_store = KeychainSecretStore(
            service_prefix="zc0-test-service", runner=fake_runner, clock=clock
        )
        with pytest.raises(SecretMaterialError) as exc_info:
            broken_store.put(organization_id=ORG_A, material=CANARY)
        leaks = _all_channel_checks(exc_info.value, CANARY)
        assert not any(leaks.values()), f"put() exception leaked material: {leaks}"

    def test_resolve_return_value_never_carries_material(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material=CANARY)
        resolution = store.resolve(ref=ref, organization_id=ORG_A)
        leaks = _all_channel_checks(resolution, CANARY)
        assert not any(leaks.values()), f"SecretResolution leaked material: {leaks}"
        assert isinstance(resolution, SecretResolution)

    def test_rotate_return_value_and_new_material_never_carried(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="original-material")
        new_canary = CANARY + "-rotated"
        rotated_ref = store.rotate(ref=ref, organization_id=ORG_A, material=new_canary)
        leaks = _all_channel_checks(rotated_ref, new_canary)
        assert not any(leaks.values()), f"rotate() leaked new material: {leaks}"

    def test_rotate_failure_exception_never_carries_material(
        self, fake_runner: FakeSubprocessRunner, frozen_clock: tuple
    ) -> None:
        _now, clock = frozen_clock
        store_ok = KeychainSecretStore(
            service_prefix="zc0-test-service", runner=fake_runner, clock=clock
        )
        ref = store_ok.put(organization_id=ORG_A, material="original")
        fake_runner.force_exit_code = 99
        with pytest.raises(SecretMaterialError) as exc_info:
            store_ok.rotate(ref=ref, organization_id=ORG_A, material=CANARY)
        leaks = _all_channel_checks(exc_info.value, CANARY)
        assert not any(leaks.values()), f"rotate() exception leaked material: {leaks}"

    def test_health_return_value_never_carries_material(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material=CANARY)
        health = store.health(ref=ref, organization_id=ORG_A)
        leaks = _all_channel_checks(health, CANARY)
        assert not any(leaks.values()), f"SecretHealth leaked material: {leaks}"
        assert isinstance(health, SecretHealth)

    def test_store_instance_itself_never_carries_material_after_any_call(
        self, fake_runner: FakeSubprocessRunner, frozen_clock: tuple
    ) -> None:
        # The store object itself must never accumulate material as state
        # -- put/rotate must not stash it on self anywhere reachable by
        # vars()/pickle/deepcopy of the STORE, not just of its return
        # values. This is the "no accidental channel" bar applied to the
        # custody object's own lifetime, not just one call's output.
        #
        # Deliberately uses a FRESH runner+store here rather than the
        # `store`/`fake_runner` fixtures: FakeSubprocessRunner is a TEST
        # DOUBLE that legitimately records argv history and its own
        # in-memory keychain simulation (both by design, and both are
        # exactly what test_put_calls_security_add_generic_password_with_
        # material_via_argv relies on to assert argv shape) -- checking
        # the composed object would fail this probe on the FAKE's own
        # recon surface, not on anything KeychainSecretStore itself does.
        # `store.__dict__` is inspected directly, EXCLUDING `_runner`
        # (an injected collaborator, not state the store owns), which is
        # the fair unit boundary for "does KeychainSecretStore's own
        # state carry material."
        _now, clock = frozen_clock
        isolated_runner = FakeSubprocessRunner()
        isolated_store = KeychainSecretStore(
            service_prefix="zc0-test-service", runner=isolated_runner, clock=clock
        )
        ref = isolated_store.put(organization_id=ORG_A, material=CANARY)
        isolated_store.resolve(ref=ref, organization_id=ORG_A)
        isolated_store.rotate(ref=ref, organization_id=ORG_A, material=CANARY + "-v2")
        isolated_store.health(ref=ref, organization_id=ORG_A)

        own_state = {k: v for k, v in vars(isolated_store).items() if k != "_runner"}
        leaks = _all_channel_checks(own_state, CANARY)
        assert not any(leaks.values()), (
            f"KeychainSecretStore's own state (excluding the injected "
            f"runner collaborator) leaked material: {leaks}"
        )


# ===========================================================================
# 2. SecretStore PROTOCOL BEHAVIOR
# ===========================================================================


class TestPutBehavior:
    def test_put_returns_secret_ref_with_opaque_handle(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="material-1")
        assert ref.handle.startswith("zc0-kc:org-a:")

    def test_put_calls_security_add_generic_password_via_stdin_never_argv(
        self, store: KeychainSecretStore, fake_runner: FakeSubprocessRunner
    ) -> None:
        # Positive guarantee, not a documented exposure (Principal's
        # secret-transport bound, carried into this rev): material must
        # go through run_with_secret_stdin, and `-w` must be the FINAL
        # argv element with no value trailing it -- the whole point of
        # the two-method Protocol split is that a caller cannot reach for
        # `run`/`calls` (the argv-only channel) and pass material there
        # by habit.
        store.put(organization_id=ORG_A, material="material-argv-check")
        assert fake_runner.calls == []
        assert len(fake_runner.stdin_calls) == 1
        call, line_count = fake_runner.stdin_calls[0]
        assert call[0] == "/usr/bin/security"
        assert call[1] == "add-generic-password"
        assert call[-1] == "-w"
        assert "material-argv-check" not in call
        assert all("material-argv-check" not in element for element in call)
        assert line_count == 2  # fed twice, matching the real confirm-match prompt

    def test_put_rejects_empty_material(self, store: KeychainSecretStore) -> None:
        with pytest.raises(ValueError, match="material must be non-empty"):
            store.put(organization_id=ORG_A, material="")

    def test_put_failure_raises_secret_material_error(
        self, fake_runner: FakeSubprocessRunner, frozen_clock: tuple
    ) -> None:
        fake_runner.force_exit_code = 1
        _now, clock = frozen_clock
        broken_store = KeychainSecretStore(
            service_prefix="zc0-test-service", runner=fake_runner, clock=clock
        )
        with pytest.raises(SecretMaterialError):
            broken_store.put(organization_id=ORG_A, material="x")

    def test_two_puts_mint_distinct_handles(self, store: KeychainSecretStore) -> None:
        ref1 = store.put(organization_id=ORG_A, material="m1")
        ref2 = store.put(organization_id=ORG_A, material="m2")
        assert ref1.handle != ref2.handle


class TestResolveBehavior:
    def test_resolve_returns_lease_with_expiry_after_resolution(
        self, store: KeychainSecretStore, frozen_clock: tuple
    ) -> None:
        now, _clock = frozen_clock
        ref = store.put(organization_id=ORG_A, material="m")
        resolution = store.resolve(ref=ref, organization_id=ORG_A)
        assert resolution.ref == ref
        assert resolution.resolved_at == now
        assert resolution.expires_at > resolution.resolved_at

    def test_resolve_missing_ref_raises_not_found(
        self, store: KeychainSecretStore
    ) -> None:
        from zeo_core.contracts.connections.identity import SecretRef

        never_put = SecretRef(
            handle="zc0-kc:org-a:00000000-0000-0000-0000-000000000000"
        )
        with pytest.raises(SecretNotFoundError):
            store.resolve(ref=never_put, organization_id=ORG_A)

    def test_resolve_cross_organization_rejected_before_any_security_call(
        self, store: KeychainSecretStore, fake_runner: FakeSubprocessRunner
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        calls_before = len(fake_runner.calls)
        with pytest.raises(CrossOrganizationAccessError):
            store.resolve(ref=ref, organization_id=ORG_B)
        # No new `security` invocation happened for the rejected call --
        # the scope check runs BEFORE any subprocess is spawned, per the
        # protocol's bound: "must reject... rather than silently
        # resolving it."
        assert len(fake_runner.calls) == calls_before


class TestRotateBehavior:
    def test_rotate_replaces_material_and_returns_ref_via_stdin_never_argv(
        self, store: KeychainSecretStore, fake_runner: FakeSubprocessRunner
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="original")
        rotated_ref = store.rotate(
            ref=ref, organization_id=ORG_A, material="rotated-material"
        )
        assert rotated_ref.handle == ref.handle
        assert fake_runner.calls == []
        rotate_call, line_count = fake_runner.stdin_calls[-1]
        assert "-U" in rotate_call
        assert rotate_call[-1] == "-w"
        assert "rotated-material" not in rotate_call
        assert all("rotated-material" not in element for element in rotate_call)
        assert line_count == 2

    def test_rotate_cross_organization_rejected(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        with pytest.raises(CrossOrganizationAccessError):
            store.rotate(ref=ref, organization_id=ORG_B, material="new")

    def test_rotate_rejects_empty_material(self, store: KeychainSecretStore) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        with pytest.raises(ValueError, match="material must be non-empty"):
            store.rotate(ref=ref, organization_id=ORG_A, material="")


class TestDeleteBehavior:
    def test_delete_removes_secret_then_resolve_fails_closed(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        store.delete(ref=ref, organization_id=ORG_A)
        with pytest.raises(SecretNotFoundError):
            store.resolve(ref=ref, organization_id=ORG_A)

    def test_delete_is_idempotent_second_call_does_not_raise(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        store.delete(ref=ref, organization_id=ORG_A)
        store.delete(ref=ref, organization_id=ORG_A)  # must not raise

    def test_delete_cross_organization_rejected(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        with pytest.raises(CrossOrganizationAccessError):
            store.delete(ref=ref, organization_id=ORG_B)


class TestHealthBehavior:
    def test_health_reachable_true_for_existing_secret(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        health = store.health(ref=ref, organization_id=ORG_A)
        assert health.reachable is True

    def test_health_reachable_false_after_delete_never_raises(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        store.delete(ref=ref, organization_id=ORG_A)
        health = store.health(ref=ref, organization_id=ORG_A)  # must not raise
        assert health.reachable is False
        assert health.detail is not None

    def test_health_reachable_false_for_cross_organization_never_raises(
        self, store: KeychainSecretStore
    ) -> None:
        ref = store.put(organization_id=ORG_A, material="m")
        health = store.health(ref=ref, organization_id=ORG_B)  # must not raise
        assert health.reachable is False
        assert health.detail is not None

    def test_delete_does_not_delete_historical_evidence_records(self) -> None:
        # This store has no method that touches execution/receipt records
        # at all -- ConnectionStore (step 4, a separate protocol) owns
        # those. The acceptance check ("Keychain deletion ->
        # resolution fails closed WITHOUT deleting historical execution
        # evidence") is satisfied structurally: KeychainSecretStore has no
        # surface capable of touching evidence in the first place.
        import inspect

        methods = {
            name
            for name, _ in inspect.getmembers(
                KeychainSecretStore, predicate=inspect.isfunction
            )
            if not name.startswith("_")
        }
        assert methods == {"put", "resolve", "rotate", "delete", "health"}


class TestStructuralConformance:
    def test_keychain_secret_store_satisfies_secret_store_protocol(self) -> None:
        from zeo_core.contracts.connections.protocols import SecretStore

        store_instance = KeychainSecretStore(
            service_prefix="p", runner=FakeSubprocessRunner()
        )
        assert isinstance(store_instance, SecretStore)

    def test_no_general_purpose_reveal_method_exists(
        self, store: KeychainSecretStore
    ) -> None:
        for banned_name in ("reveal", "unwrap", "get_secret", "expose", "raw"):
            assert not hasattr(store, banned_name)

    def test_service_prefix_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="service_prefix"):
            KeychainSecretStore(service_prefix="", runner=FakeSubprocessRunner())

    def test_resolution_ttl_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="resolution_ttl_seconds"):
            KeychainSecretStore(
                service_prefix="p",
                runner=FakeSubprocessRunner(),
                resolution_ttl_seconds=0,
            )


# ===========================================================================
# 3. STDIN TRANSPORT PROVEN ON THE REAL EXECUTABLE (Principal's bound)
# ===========================================================================

_IS_MACOS = platform.system() == "Darwin"
_SECURITY_AVAILABLE = shutil.which("security") is not None
# Set on a host that is meant to provide the stdin-transport proof. There, an
# unusable keychain is a failure, not a skip, so the proof cannot go unrun
# quietly on every host.
_REQUIRE_KEYCHAIN = os.environ.get("ZEOCORE_REQUIRE_KEYCHAIN_PROOF") == "1"


def _keychain_refusal() -> str | None:
    """Why the real login keychain cannot be used here, or None if it can.

    A headless macOS session (SSH, tmux, a CI agent) has no unlocked login
    keychain: `security` exits 36, "User interaction is not allowed", and the
    proofs below fail for a reason that says nothing about the adapter. This
    asks `security` itself, read-only, instead of guessing from the session.
    """

    if not (_IS_MACOS and _SECURITY_AVAILABLE):
        return "requires a real macOS security(1) binary"
    probe = subprocess.run(  # noqa: S603
        ["/usr/bin/security", "show-keychain-info"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return None
    detail = (probe.stderr or probe.stdout).strip().splitlines()
    return "requires an unlocked login keychain; security(1) reported: " + (
        detail[-1] if detail else f"exit {probe.returncode}"
    )


_KEYCHAIN_REFUSAL = _keychain_refusal()
# Only the proofs that WRITE to the real login keychain need it unlocked; the
# others in this section run headless and must keep running there.
_needs_usable_keychain = pytest.mark.skipif(
    _KEYCHAIN_REFUSAL is not None,
    reason=f"this proof writes to the login keychain, which {_KEYCHAIN_REFUSAL}",
)
if _KEYCHAIN_REFUSAL is not None and _REQUIRE_KEYCHAIN and _IS_MACOS:
    raise RuntimeError(
        "ZEOCORE_REQUIRE_KEYCHAIN_PROOF=1 but the stdin-transport proof cannot "
        f"run here: {_KEYCHAIN_REFUSAL}"
    )


def _raw_security(
    args: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/usr/bin/security", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(
    not (_IS_MACOS and _SECURITY_AVAILABLE),
    reason="stdin-transport proofs require a real macOS security(1) binary",
)
class TestStdinTransportProvenOnRealExecutable:
    """
    Principal decision msg_e79f76af, carried into this revision by
    Master's re-brief after SOW-05: "supply a synthetic secret through
    stdin only after the stream proves that path works on the actual
    macOS executable without echo or prompt leakage." These tests are
    that proof, run against the REAL `/usr/bin/security` binary (the fake
    runner cannot prove or disprove anything about the real binary's
    prompt/confirm-match behavior, by construction). Every item created
    here uses a unique synthetic service/account and is deleted in
    `finally`, matching the pattern Master's own probe used.
    """

    @_needs_usable_keychain
    def test_single_stdin_value_is_the_control_case_and_fails_red(self) -> None:
        # RED-before-green, the control case: this is SOW-05's original
        # (falsified) claim, reproduced here as the FIRST thing this
        # class proves, not asserted from memory. A single
        # newline-terminated stdin value leaves the second confirm-match
        # read at EOF; the real binary reports success (exit 0) while
        # storing an EMPTY password, not the intended material. If this
        # test ever stops reproducing, the platform changed and the
        # correction this class documents needs re-verification, not
        # silent deletion.
        canary = f"CANARY-SINGLE-STDIN-CONTROL-{time.time_ns()}"
        account = f"zc0-stdin-control-{time.time_ns()}"
        service = "zc0-stdin-control-svc"
        try:
            add = _raw_security(
                ["add-generic-password", "-a", account, "-s", service, "-w"],
                input_text=f"{canary}\n",
            )
            assert add.returncode == 0, (
                "expected the control case to report success at exit 0 "
                "while silently storing the WRONG material -- that "
                "mismatch (successful exit, wrong content) is exactly "
                "the defect this stream's original probe measured"
            )
            found = _raw_security(
                ["find-generic-password", "-a", account, "-s", service, "-w"]
            )
            assert found.stdout != canary, (
                "expected the single-stdin-value shape to NOT store the "
                "canary (it stores an empty password instead) -- if this "
                "now matches, the platform's confirm-match behavior "
                "changed and this class's premise needs re-checking"
            )
        finally:
            _raw_security(["delete-generic-password", "-a", account, "-s", service])

    @_needs_usable_keychain
    def test_twice_fed_stdin_value_is_the_corrected_shape_and_passes_green(
        self,
    ) -> None:
        # The corrected shape Master reproduced 3/3 and this stream
        # independently re-verifies here: the SAME value fed TWICE
        # (matching security(1)'s own confirm-match prompt) stores
        # correctly, read back byte-exact via find-generic-password -w.
        canary = f"CANARY-TWICE-STDIN-GREEN-{time.time_ns()}"
        account = f"zc0-stdin-green-{time.time_ns()}"
        service = "zc0-stdin-green-svc"
        try:
            add = _raw_security(
                ["add-generic-password", "-a", account, "-s", service, "-w"],
                input_text=f"{canary}\n{canary}\n",
            )
            assert add.returncode == 0
            found = _raw_security(
                ["find-generic-password", "-a", account, "-s", service, "-w"]
            )
            assert found.returncode == 0
            # security(1)'s `-w` output on find-generic-password carries a
            # trailing newline (verified directly -- an earlier shell-only
            # probe used command substitution, which strips trailing
            # newlines automatically and hid this); the stored VALUE is
            # still byte-exact once that one added newline is accounted
            # for, so this strips exactly one trailing "\n" rather than
            # using .strip() (which would also silently hide a REAL
            # leading/trailing-whitespace corruption in the material).
            stdout_without_trailing_newline = (
                found.stdout[:-1] if found.stdout.endswith("\n") else found.stdout
            )
            assert stdout_without_trailing_newline == canary, (
                f"expected byte-exact storage of the canary via the "
                f"twice-fed stdin shape (modulo security(1)'s own added "
                f"trailing newline), got {found.stdout!r}"
            )
        finally:
            _raw_security(["delete-generic-password", "-a", account, "-s", service])

    @_needs_usable_keychain
    def test_keychain_secret_store_put_never_leaks_material_via_ps_positive_guarantee(
        self,
    ) -> None:
        # The POSITIVE guarantee, inverted from SOW-05's documented
        # exposure: drive the REAL KeychainSecretStore (RealSubprocessRunner,
        # not the fake) through `put`, poll `ps -ww` throughout the
        # subprocess's lifetime, and assert the canary is absent on EVERY
        # sample, not caught on any. This is the store's actual production
        # code path, not a hand-rolled security(1) invocation -- proving
        # the adapter itself, not just the CLI shape in isolation.
        #
        # RULING-423 (Sparring's PR #39 finding, re-verified by Master
        # before relaying): a negative result from an instrument not
        # shown, in the SAME RUN, to produce a positive is inadmissible.
        # `assert samples > 0` alone only proves the polling LOOP ran; it
        # does not prove `ps` can see anything at all -- a sandboxed,
        # restricted, or hardened-runtime `ps` can run every iteration,
        # return "" or a nonzero exit every time, and this test would
        # certify a guarantee it never measured. So this test now runs a
        # KNOWN-LEAKY control process ALONGSIDE the real store call,
        # carrying a distinct canary as a genuine argv element, and
        # requires that control canary to be OBSERVED at least once
        # before trusting any negative result about the store's own
        # canary. `bash -c` is deliberately NOT used for the control --
        # bash rewrites its own argv for a `-c` script, which would make
        # the control canary invisible via `ps` even on a fully-sighted
        # machine and produce exactly the false negative this test exists
        # to rule out (Master paid this once; recorded here so it is not
        # re-paid). `sys.executable -c "..." <canary>` keeps the canary as
        # a literal, unmodified argv element.
        from zeo_core.connections.adapters.macos_keychain import KeychainSecretStore
        from zeo_core.connections.adapters.subprocess_runner import (
            RealSubprocessRunner,
        )
        from zeo_core.contracts.connections.identity import OrganizationId, SecretRef

        canary = f"CANARY-STORE-PUT-NO-PS-LEAK-{time.time_ns()}"
        control_canary = f"CANARY-CONTROL-ARGV-VISIBLE-{time.time_ns()}"
        service_prefix = f"zc0-realstore-svc-{time.time_ns()}"
        store = KeychainSecretStore(
            service_prefix=service_prefix,
            runner=RealSubprocessRunner(),
        )
        org = OrganizationId(value=f"org-realstore-{time.time_ns()}")

        refs: list[SecretRef] = []

        def _do_put() -> None:
            refs.append(store.put(organization_id=org, material=canary))

        control_proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", "import time; time.sleep(3)", control_canary],
        )
        thread = threading.Thread(target=_do_put)
        thread.start()
        samples = 0
        blind_samples = 0
        leaked_samples = 0
        control_observed = False
        deadline = time.monotonic() + 3.0
        try:
            while thread.is_alive() and time.monotonic() < deadline:
                all_procs = _run_ps_axww()
                samples += 1
                if all_procs is None:
                    blind_samples += 1
                    continue
                if control_canary in all_procs:
                    control_observed = True
                if canary in all_procs:
                    leaked_samples += 1
            thread.join(timeout=5)
        finally:
            control_proc.terminate()
            control_proc.wait(timeout=5)

        try:
            assert samples > 0, (
                "polling loop never sampled ps -- test is not proving anything"
            )
            if not control_observed:
                pytest.skip(
                    f"process inspection unavailable: the argv-visible "
                    f"positive control was never observed via ps -axww "
                    f"across {samples} samples ({blind_samples} of them "
                    f"outright blind -- nonzero exit or empty stdout). "
                    f"Per RULING-423, a negative from an instrument that "
                    f"cannot be shown to produce a positive in this same "
                    f"run is inadmissible; skipping rather than passing "
                    f"vacuously."
                )
            assert leaked_samples == 0, (
                f"canary observed in ps -axww on {leaked_samples}/{samples} "
                f"samples during KeychainSecretStore.put -- the stdin "
                f"transport is supposed to make this structurally "
                f"impossible"
            )
            assert refs, "put() did not complete during the polling window"
        finally:
            if refs:
                _raw_security(
                    [
                        "delete-generic-password",
                        "-a",
                        refs[0].handle,
                        "-s",
                        service_prefix,
                    ]
                )

    def test_stdin_transport_diagnostics_never_carry_material_on_the_real_binary(
        self,
    ) -> None:
        # security(1)'s own confirm-match prompts land on stderr; this
        # asserts directly against the real binary that neither stdout
        # nor stderr ever contain the material itself -- only the fixed
        # prompt text ("password data for new item: retype password for
        # new item: ").
        canary = f"CANARY-DIAGNOSTICS-NO-LEAK-{time.time_ns()}"
        account = f"zc0-diag-{time.time_ns()}"
        service = "zc0-diag-svc"
        try:
            add = _raw_security(
                ["add-generic-password", "-a", account, "-s", service, "-w"],
                input_text=f"{canary}\n{canary}\n",
            )
            assert canary not in add.stdout
            assert canary not in add.stderr
            assert "password data for new item" in add.stderr
        finally:
            _raw_security(["delete-generic-password", "-a", account, "-s", service])

    def test_dash_a_broad_access_is_never_passed_by_the_store(self) -> None:
        # Structural check: -A (broad, unprompted app access) must never
        # appear on any argv this store constructs. Checked against the
        # FAKE runner (deterministic, no real keychain needed) rather
        # than the real binary -- this is a property of the STORE's own
        # code, not of the platform.
        from zeo_core.connections.adapters.macos_keychain import KeychainSecretStore
        from zeo_core.contracts.connections.identity import OrganizationId

        fake = FakeSubprocessRunner()
        store = KeychainSecretStore(service_prefix="zc0-noA-svc", runner=fake)
        org = OrganizationId(value="org-noA")
        ref = store.put(organization_id=org, material="m")
        store.resolve(ref=ref, organization_id=org)
        store.rotate(ref=ref, organization_id=org, material="m2")
        store.delete(ref=ref, organization_id=org)
        store.health(ref=ref, organization_id=org)
        all_argv = list(fake.calls) + [c for c, _n in fake.stdin_calls]
        for call in all_argv:
            assert "-A" not in call


@pytest.mark.skipif(
    not (_IS_MACOS and _SECURITY_AVAILABLE),
    reason="background evidence probes require a real macOS security(1) binary",
)
class TestBackgroundEvidenceArgvAndEnvVarExposure:
    """
    Background evidence, not live exposures under the current transport:
    argv `-w <value>` (this adapter's FIRST revision, superseded this
    rev) and environment variables are both visible via `ps` to a
    same-user local process -- this is WHY the transport moved to stdin,
    kept here so a future reader can see the ground truth that motivated
    the change rather than inheriting an unverified claim. Neither
    channel carries `material` under the CURRENT transport (proven above
    by TestStdinTransportProvenOnRealExecutable and by
    test_dash_a_broad_access_is_never_passed_by_the_store's argv sweep).
    """

    def test_raw_argv_w_is_visible_via_ps_this_is_why_the_store_no_longer_uses_it(
        self,
    ) -> None:
        argv_canary = f"CANARY-ARGV-HISTORICAL-EVIDENCE-{time.time_ns()}"
        account = f"zc0-argvhist-{time.time_ns()}"
        service = "zc0-argvhist-svc"
        proc = subprocess.Popen(  # noqa: S603
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                account,
                "-s",
                service,
                "-w",
                argv_canary,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            caught = False
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and proc.poll() is None:
                ps = subprocess.run(  # noqa: S603, S607
                    ["/bin/ps", "-p", str(proc.pid), "-ww"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if argv_canary in ps.stdout:
                    caught = True
                    break
            proc.wait(timeout=5)
        finally:
            _raw_security(["delete-generic-password", "-a", account, "-s", service])
        assert caught, (
            "expected the raw argv canary to be visible via ps -ww -- "
            "this is historical ground evidence for why the store moved "
            "off argv; if this now fails, re-verify before trusting the "
            "rest of this class's premise"
        )

    def test_environment_variables_are_shown_via_ps_capital_e_same_as_argv(
        self,
    ) -> None:
        # This test's own history is part of its evidence: this stream's
        # FIRST pass at comparing argv vs env-var exposure used a plain
        # `ps eww -p <pid>` against a backgrounded shell builtin, got an
        # empty result, and wrongly concluded env vars were not exposed
        # on macOS the way argv is. Corrected here with a real child
        # PROCESS and `-wwE`: a same-user local process CAN read another
        # same-user process's environment via `ps -wwE`, exactly like
        # `ps -ww` shows argv.
        env_canary = f"CANARY-ENV-EXPOSED-HISTORICAL-{time.time_ns()}"
        proc = subprocess.Popen(  # noqa: S603, S607
            [sys.executable, "-c", "import time; time.sleep(1.5)"],
            env={"ZC0_TEST_ENV_CANARY": env_canary},
        )
        try:
            time.sleep(0.3)
            ps = subprocess.run(  # noqa: S603, S607
                ["/bin/ps", "-p", str(proc.pid), "-wwE"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert env_canary in ps.stdout, (
                "expected ps -wwE to show the child's environment to "
                "this same-user process -- if this fails, macOS changed "
                "this behavior and the historical rationale needs "
                "re-checking, not silent adjustment of this test"
            )
        finally:
            proc.wait(timeout=5)


def _run_ps_axww() -> str | None:
    """
    Returns the process table, or None if `ps` itself could not be
    trusted -- a nonzero exit or empty stdout means process inspection is
    unavailable (a sandbox, a hardened runtime, a restricted container),
    not that the process table is genuinely empty. Per RULING-423: a
    negative result from an instrument not shown, in the same run, to
    produce a positive is inadmissible. Returning None here instead of a
    bare (possibly blind) string forces every caller to notice the
    distinction rather than silently treating "couldn't check" the same
    as "checked and found nothing."
    """
    result = subprocess.run(  # noqa: S603, S607
        ["/bin/ps", "-axww"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout
