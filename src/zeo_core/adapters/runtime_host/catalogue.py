"""Private candidate activation and immutable generation publication."""

from __future__ import annotations

import importlib
import importlib.metadata
import platform
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from zeo_core.adapters.runtime_host.canonical import (
    ProtocolError,
    digest,
    manifest_inventory,
)
from zeo_core.contracts.runtime import ProviderBinding
from zeo_core.tools.invoke import BoundCapability
from zeo_core.tools.registry import CapabilityRegistry


def validate_schema(schema: dict[str, Any]) -> Draft202012Validator:
    """No remote resolution or malformed-schema compatibility fallback."""
    Draft202012Validator.check_schema(schema)

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ProtocolError("external schema reference refused")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    return Draft202012Validator(schema)


def validate_inventory(binding: ProviderBinding) -> None:
    identities: set[str] = set()
    projections: dict[str, str] = {}
    for manifest in binding.manifests:
        identity = manifest.id.canonical()
        if identity in identities:
            raise ProtocolError(f"duplicate capability: {identity}")
        identities.add(identity)
        if manifest.projection_name:
            prior = projections.get(manifest.projection_name)
            if prior:
                raise ProtocolError(f"projection collision: {prior} and {identity}")
            projections[manifest.projection_name] = identity
        request = validate_schema(manifest.request_schema)
        response = validate_schema(manifest.response_schema)
        for example in manifest.examples:
            request.validate(example.request)
            if example.response is not None:
                response.validate(example.response)
    if digest(manifest_inventory(binding.manifests)) != binding.manifest_digest:
        raise ProtocolError("manifest inventory digest mismatch")


@dataclass(frozen=True)
class Generation:
    number: int
    capabilities: MappingProxyType[str, BoundCapability]
    manifest_digest: str


class CandidateCatalogue:
    """Factory callbacks never see or mutate the published registry.

    Cleanup is compare-by-generation; an old handle cannot remove a replacement.
    The host executes one admitted generation per process. This is not a sandbox
    for arbitrary provider Python or mutable objects retained by that Python.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Generation | None = None
        self._highest = 0

    def snapshot(self) -> Generation | None:
        with self._lock:
            return self._active

    def activate(
        self, binding: ProviderBinding, factory: Callable[[], CapabilityRegistry]
    ) -> Generation:
        validate_inventory(binding)
        candidate = factory()
        if not isinstance(candidate, CapabilityRegistry):
            raise ProtocolError("provider factory did not return CapabilityRegistry")
        if digest(manifest_inventory(candidate.manifests())) != binding.manifest_digest:
            raise ProtocolError(
                "provider contributions disagree with admitted inventory"
            )
        capabilities = {
            cap.definition.id.canonical(): cap for cap in candidate.list_all()
        }
        for cap in capabilities.values():
            if cap.request_model.model_json_schema() != cap.definition.request_schema:
                raise ProtocolError("runtime request model disagrees with manifest")
            for example in cap.definition.examples:
                cap.request_model.model_validate(example.request)
        generation = Generation(
            binding.generation, MappingProxyType(capabilities), binding.manifest_digest
        )
        with self._lock:
            if binding.generation <= self._highest:
                raise ProtocolError("stale generation publication")
            self._active = generation
            self._highest = binding.generation
        return generation

    def dispose(self, generation: Generation) -> None:
        with self._lock:
            if self._active is generation:
                self._active = None


def installed_factory(binding: ProviderBinding) -> Callable[[], CapabilityRegistry]:
    """Only called after Runtime redeems and validates the trusted binding."""
    if platform.python_version() != binding.python_version:
        raise ProtocolError("interpreter version mismatch")
    if importlib.metadata.version(binding.distribution) != binding.version:
        raise ProtocolError("provider distribution version mismatch")
    module, attribute = binding.factory.split(":")
    factory = getattr(importlib.import_module(module), attribute)
    if not callable(factory):
        raise ProtocolError("provider factory is not callable")
    return cast(Callable[[], CapabilityRegistry], factory)


__all__ = [
    "CandidateCatalogue",
    "validate_inventory",
]
