"""The runtime-host names Creator depends on are a supported tier from 0.15.0."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from zeo_core.adapters.runtime_host import canonical, catalogue, channel, host
from zeo_core.contracts import runtime
from zeo_core.contracts.runtime import RUNTIME_HOST_PROTOCOL_VERSION

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "runtime-host-v1"

SURFACE = {
    canonical: {
        "MAX_BYTES",
        "InvalidRequestError",
        "ProtocolError",
        "canonical_bytes",
        "digest",
        "manifest_inventory",
        "parse_json",
    },
    catalogue: {"CandidateCatalogue", "validate_inventory"},
    channel: {"RuntimeChannel"},
    host: {"EffectPort", "ManagedHost", "parse_result", "prepare_request"},
}


@pytest.mark.parametrize("module", list(SURFACE), ids=lambda m: m.__name__)
def test_public_names_are_exactly_the_declared_tier(module: ModuleType) -> None:
    assert set(module.__all__) == SURFACE[module]
    for name in module.__all__:
        assert getattr(module, name) is not None


def test_protocol_version_constant_is_exported() -> None:
    assert "RUNTIME_HOST_PROTOCOL_VERSION" in runtime.__all__
    assert RUNTIME_HOST_PROTOCOL_VERSION == 1
    assert type(RUNTIME_HOST_PROTOCOL_VERSION) is int


def _consts(node: object) -> list[object]:
    found: list[object] = []
    if isinstance(node, dict):
        version = node.get("properties", {}).get("protocol_version")
        if isinstance(version, dict) and "const" in version:
            found.append(version["const"])
        for value in node.values():
            found.extend(_consts(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_consts(value))
    return found


def test_constant_matches_every_published_schema_and_the_vectors() -> None:
    schemas = {path.name: path for path in CONTRACT.glob("*.schema.json")}
    # An effect request travels inside a versioned reply; it has no version.
    assert "effect-request.schema.json" in schemas
    for name, path in schemas.items():
        consts = set(_consts(json.loads(path.read_text())))
        expected = (
            set()
            if name == "effect-request.schema.json"
            else {RUNTIME_HOST_PROTOCOL_VERSION}
        )
        assert consts == expected, name
    vectors = json.loads((CONTRACT / "canonical-vectors.json").read_text())
    assert vectors["protocol_version"] == RUNTIME_HOST_PROTOCOL_VERSION
