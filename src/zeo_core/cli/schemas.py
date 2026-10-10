"""The public contract models, by stable name, as JSON Schema 2020-12.

A name is a promise: once published, it names the same contract until a
major version. Adding a name is additive. Each schema is also committed
under ``contracts/zeocore-v1/`` for callers that vendor files, and a test
fails if a model and its committed schema drift.

Models are imported only when their schema is asked for.
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Final

from pydantic import TypeAdapter

JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"

#: Stable name -> "module:attribute". Only shape lives here, never behaviour.
SCHEMAS: Final[dict[str, str]] = {
    "connections.normalized-error": (
        "zeo_core.contracts.connections.errors:NormalizedError"
    ),
    "connections.observation-artifact": (
        "zeo_core.contracts.connections.observation:ObservationArtifact"
    ),
    "hosted.artifact-descriptor": (
        "zeo_core.integrations.hosted.client:HostedArtifactDescriptor"
    ),
    "hosted.availability-snapshot": (
        "zeo_core.integrations.hosted.setup:AvailabilitySnapshot"
    ),
    "hosted.operation-request": (
        "zeo_core.integrations.hosted.client:HostedOperationRequest"
    ),
    "hosted.operation-response": (
        "zeo_core.integrations.hosted.client:HostedOperationResponse"
    ),
    "revolut.account": "zeo_core.integrations.revolut.models:Account",
    # TransactionQuery waits for its wire alias ("from", contract §6): its
    # schema today would name the Python field "from_".
    "revolut.transaction-page": "zeo_core.integrations.revolut.models:TransactionPage",
}


def adapter(name: str) -> TypeAdapter[Any]:
    """The validator for ``name``; ``KeyError`` for an unknown name."""
    module, attribute = SCHEMAS[name].split(":")
    return TypeAdapter(getattr(importlib.import_module(module), attribute))


def render_schema(name: str) -> dict[str, Any]:
    schema = adapter(name).json_schema(by_alias=True)
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["$id"] = f"https://zeocore.zeo.ac/contracts/zeocore-v1/{name}.schema.json"
    return schema


def rendered_file(name: str) -> str:
    """The exact committed bytes for ``name``'s schema file."""
    return (
        json.dumps(render_schema(name), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n"
    )
