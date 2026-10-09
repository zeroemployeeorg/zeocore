"""Strict JSON and RFC 8785 bytes for managed wire bindings only."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, cast

import rfc8785

from zeo_core.contracts import CapabilityManifest

MAX_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    """Untrusted wire data failed validation; never an instruction to retry."""


class InvalidRequestError(ProtocolError):
    """The caller supplied an invalid command or business request."""


def canonical_bytes(value: object) -> bytes:
    try:
        return rfc8785.dumps(cast(Any, value))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProtocolError("value is outside the canonical JSON domain") from exc


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_json(raw: bytes, *, limit: int = MAX_BYTES) -> Any:  # noqa: ANN401 -- decoded JSON
    if not raw or len(raw) > limit:
        raise ProtocolError("empty or oversized JSON message")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ProtocolError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ProtocolError("non-finite JSON number")

    try:
        result = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
        canonical_bytes(result)  # also rejects unsafe integers and lone surrogates
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("invalid canonical JSON input") from exc


def manifest_inventory(manifests: Sequence[CapabilityManifest]) -> list[dict[str, Any]]:
    """Order semantic sets, not schema/request/example arrays."""
    inventory = []
    for manifest in sorted(manifests, key=lambda item: item.id.canonical()):
        data = manifest.model_dump(mode="json")
        for key in ("error_codes", "tags"):
            data[key] = sorted(data[key])
        data["effects"]["kinds"] = sorted(data["effects"]["kinds"])
        for key in ("services", "credentials", "binaries"):
            data["requirements"][key] = sorted(data["requirements"][key])
        data["requirements"]["network"]["hosts"] = sorted(
            data["requirements"]["network"]["hosts"]
        )
        inventory.append(data)
    return inventory


__all__ = [
    "MAX_BYTES",
    "InvalidRequestError",
    "ProtocolError",
    "canonical_bytes",
    "digest",
    "manifest_inventory",
    "parse_json",
]
