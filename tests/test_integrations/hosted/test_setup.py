"""Setup coverage, inertness, stale facts and exact resource-selection semantics."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from zeo_core.integrations.environments.catalog import CATALOG
from zeo_core.integrations.hosted.profile import ExecutionProfile, ServiceRequirement
from zeo_core.integrations.hosted.setup import (
    AvailabilityDimension,
    AvailabilityFact,
    AvailabilitySnapshot,
    BlockerCode,
    HostedResourceSelection,
    ProfileSetup,
    SetupManifest,
)
from zeo_core.integrations.hosted.setup_catalog import SETUP_CATALOGUE, setup_manifest

NOW = datetime(2026, 9, 11, tzinfo=UTC)
REQUIREMENT = ServiceRequirement(
    service="hubspot.marketing", operations=("hubspot.marketing.email.publish",)
)
ROOT = Path(__file__).resolve().parents[3]


def snapshot(**changes: str) -> AvailabilitySnapshot:
    return AvailabilitySnapshot(
        requirement=REQUIREMENT,
        profile=ExecutionProfile.GOVERNED,
        connection={"value": "con_example123"},
        revision="connection:4/access:2",
        facts=tuple(
            AvailabilityFact(
                dimension=dimension,
                state="blocked" if dimension.value in changes else "satisfied",
                revision="observed:1",
                observed_at=NOW,
                fresh_until=NOW + timedelta(seconds=10),
                blockers=(BlockerCode(changes[dimension.value]),)
                if dimension.value in changes
                else (),
            )
            for dimension in AvailabilityDimension
        ),
    )


def test_catalogue_covers_entrypoints_and_nonentry_setup_tracks() -> None:
    entries = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "entry-points"
    ]["zeo_core.integrations"]
    assert {item.integration_id for item in SETUP_CATALOGUE} == set(CATALOG)
    assert len(SETUP_CATALOGUE) == len(CATALOG)
    assert {key for key, value in CATALOG.items() if value.entry_point} == set(entries)
    for item in SETUP_CATALOGUE:
        assert (ROOT / "docs/integrations" / item.guide).is_file()
        assert item.live_validation == "not_run"
        assert {profile.profile for profile in item.profiles} == {
            ExecutionProfile.LOCAL,
            ExecutionProfile.HOSTED,
            ExecutionProfile.GOVERNED,
        }
        assert all(
            not profile.actions
            for profile in item.profiles
            if profile.profile != ExecutionProfile.LOCAL
        )
    assert setup_manifest("missing") is None
    onboarding = setup_manifest("zeoconnect")
    assert onboarding is not None and onboarding.surface == "onboarding"


@pytest.mark.parametrize("provider", ["hubspot", "kit"])
def test_marketing_mappings_match_actual_declared_capabilities(provider: str) -> None:
    tree = ast.parse((ROOT / f"src/zeo_core/tools/builtin/{provider}.py").read_text())
    declared = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "id"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    manifest = setup_manifest(provider + ".marketing")
    assert manifest is not None
    assert {operation.capability for operation in manifest.operations} == declared


def test_root_service_identity_reuses_actual_github_requirement() -> None:
    requirement = ServiceRequirement(
        service="github", operations=("github.repository.file.read",)
    )
    assert requirement.service == "github"
    with pytest.raises(ValidationError):
        ServiceRequirement(
            service="github", operations=("githubelse.repository.file.read",)
        )


def test_import_lookup_and_evaluation_are_inert_in_fresh_process() -> None:
    source = """
import socket, webbrowser

def forbidden(*args, **kwargs):
    raise AssertionError('setup attempted a network/browser action')
socket.socket.connect = forbidden
socket.create_connection = forbidden
webbrowser.open = forbidden
from zeo_core.integrations.hosted.setup_catalog import SETUP_CATALOGUE, setup_manifest
from zeo_core.integrations.hosted import (
    ServiceResolver, ServiceRequirement, ExecutionProfile)
assert len(SETUP_CATALOGUE) == 19
assert setup_manifest('hubspot.marketing').profiles
result = ServiceResolver(profile=ExecutionProfile.HOSTED).resolve(
    ServiceRequirement(
        service='hubspot.marketing', operations=('hubspot.marketing.read',)))
assert result.status == 'unavailable'
"""
    subprocess.run([sys.executable, "-c", source], check=True, timeout=10)  # noqa: S603


def test_all_blockers_survive_and_primary_is_independent_of_input_order() -> None:
    original = snapshot(
        runtime_authority="identity_conflict",
        implementation="unsupported",
        provider_consent="consent_required",
        account_health="feature_unavailable",
    )
    expected = [
        BlockerCode.IDENTITY_CONFLICT,
        BlockerCode.UNSUPPORTED,
        BlockerCode.CONSENT_REQUIRED,
        BlockerCode.FEATURE_UNAVAILABLE,
    ]
    for facts in (original.facts, tuple(reversed(original.facts))):
        view = original.model_copy(update={"facts": facts}).evaluate(now=NOW)
        assert [blocker.code for blocker in view.blockers] == expected
        assert view.primary is not None
        assert view.primary.code == BlockerCode.IDENTITY_CONFLICT
        assert view.dispatch_recheck_required is True


def test_connected_but_plan_unavailable_is_not_an_auth_failure() -> None:
    view = snapshot(account_health="feature_unavailable").evaluate(now=NOW)
    assert [blocker.code for blocker in view.blockers] == [
        BlockerCode.FEATURE_UNAVAILABLE
    ]
    assert view.primary is not None
    assert view.primary.dimension == AvailabilityDimension.ACCOUNT
    assert "connect" not in view.primary.actions


def test_all_clear_never_becomes_dispatch_authority_and_expires_at_boundary() -> None:
    value = snapshot()
    assert value.evaluate(now=NOW).primary is None
    assert value.evaluate(now=NOW).dispatch_recheck_required
    assert value.evaluate(now=NOW + timedelta(seconds=9)).blockers == ()
    expired = value.evaluate(now=NOW + timedelta(seconds=10))
    assert len(expired.blockers) == 7
    assert {blocker.code for blocker in expired.blockers} == {BlockerCode.STALE}
    future = value.evaluate(now=NOW - timedelta(seconds=1))
    assert {blocker.code for blocker in future.blockers} == {BlockerCode.UNKNOWN}
    with pytest.raises(ValueError, match="aware"):
        value.evaluate(now=NOW.replace(tzinfo=None))


def test_staleness_cannot_erase_known_revocation() -> None:
    view = snapshot(provider_consent="revoked").evaluate(
        now=NOW + timedelta(seconds=10)
    )
    assert view.primary is not None
    assert view.primary.code == BlockerCode.REVOKED
    assert len(view.blockers) == 8


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("facts", []),
        ("authorization", "invented-grant"),
    ],
)
def test_unknown_version_missing_dimensions_and_invented_authority_refuse(
    field: str, value: object
) -> None:
    data = snapshot().model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        AvailabilitySnapshot.model_validate(data)


def test_duplicate_dimension_cannot_hide_a_missing_check() -> None:
    data = snapshot().model_dump(mode="json")
    data["facts"][1] = data["facts"][0]
    with pytest.raises(ValidationError, match="every availability"):
        AvailabilitySnapshot.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "unknown"),
        ("fresh_until", NOW),
        ("observed_at", NOW.replace(tzinfo=None)),
        ("blockers", ["revoked", "revoked"]),
    ],
)
def test_incoherent_or_unbounded_facts_refuse(field: str, value: object) -> None:
    data = snapshot(provider_consent="revoked").facts[2].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        AvailabilityFact.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("authorization_url", "https://attacker.invalid"),
        ("secret_ref", "opaque-private-reference"),
        ("guide", "../../private"),
        ("live_validation", "passed"),
    ],
)
def test_manifest_refuses_destinations_secrets_and_unproved_live_claims(
    field: str, value: object
) -> None:
    data = SETUP_CATALOGUE[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        SetupManifest.model_validate(data)


def test_unadmitted_profile_cannot_offer_connect() -> None:
    data = SETUP_CATALOGUE[0].profiles[1].model_dump()
    data["actions"] = ["connect"]
    with pytest.raises(ValidationError, match="cannot offer enrollment"):
        ProfileSetup.model_validate(data)


def resource(**changes: object) -> HostedResourceSelection:
    data = {
        "external_id": "folder-123",
        "display_name": "Newsletter assets",
        "media_type": "application/folder",
        "operations": ["google.drive.file.download"],
        "provider": "google",
        "connection": {"value": "con_example123"},
        "resource_type": "folder",
        "observed_at": NOW,
        "grant_revision": 3,
        "semantics": "exact_object",
        "includes_future_members": False,
    }
    data.update(changes)
    return HostedResourceSelection.model_validate(data)


def test_folder_selection_never_implies_future_or_recursive_membership() -> None:
    exact = resource()
    assert not exact.includes_future_members and not exact.members
    with pytest.raises(ValidationError, match="dynamic"):
        resource(includes_future_members=True)
    fixed = resource(semantics="fixed_collection", members=["file-a", "file-b"])
    assert fixed.members == ("file-a", "file-b")
    with pytest.raises(ValidationError, match="enumerate"):
        resource(semantics="fixed_collection")
    with pytest.raises(ValidationError, match="unique"):
        resource(semantics="fixed_collection", members=["file-a", "file-a"])
    dynamic = resource(semantics="dynamic_container", includes_future_members=True)
    assert dynamic.includes_future_members
    renamed = resource(display_name="Renamed assets")
    assert renamed.external_id == exact.external_id
    assert renamed.connection == exact.connection
    assert HostedResourceSelection.model_validate_json(exact.model_dump_json()) == exact
    assert json.loads(exact.model_dump_json())["grant_revision"] == 3
