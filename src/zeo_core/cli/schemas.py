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
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"

#: Stable name -> "module:attribute". Only shape lives here, never behaviour.
SCHEMAS: Final[dict[str, str]] = {
    "library.view-v2": "zeo_core.contracts.library.view:LibraryView",
    "access.receipt-v2": "zeo_core.contracts.library.receipt:AccessReceipt",
    "connections.normalized-error": (
        "zeo_core.contracts.connections.errors:NormalizedError"
    ),
    "connections.observation-artifact": (
        "zeo_core.contracts.connections.observation:ObservationArtifact"
    ),
    "hosted.artifact-descriptor": (
        "zeo_core.integrations.hosted.client:HostedArtifactDescriptor"
    ),
    "hosted.connection-summary": (
        "zeo_core.integrations.hosted.transport:HostedConnectionWire"
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
    "ui.result-card": "zeo_core.contracts.ui.card:UiCardResult",
    "ui.widget-upsert": "zeo_core.contracts.ui.widgets:WidgetUpsert",
    "ui.widget-data-ref": "zeo_core.contracts.ui.widgets:DataRef",
    "ui.intervention-card": (
        "zeo_core.contracts.ui.intervention_cards:InterventionCardPayload"
    ),
    "ui.calendar-view": "zeo_core.contracts.ui.calendar_views:CalendarView",
    "proof.report": "zeo_core.contracts.proof.report:ProofReport",
    "revolut.account": "zeo_core.integrations.revolut.models:Account",
    # TransactionQuery waits for its wire alias ("from", contract §6): its
    # schema today would name the Python field "from_".
    "revolut.transaction-page": "zeo_core.integrations.revolut.models:TransactionPage",
}


#: Names other than the registered one that select the same contract. The wire tag
#: of a UI result is ``zeo.ui-result/v1``; ``zeocore validate`` and ``zeocore schema``
#: accept it for the one-card subset (zeocore-SOW-42).
ALIASES: Final[dict[str, str]] = {
    "zeo.library-view/v2": "library.view-v2",
    "zeo.access-receipt/v2": "access.receipt-v2",
    "zeo.ui-result/v1": "ui.result-card",
    "zeo.proof-report/v1": "proof.report",
}

#: Contracts whose limits are properties of the bytes as received: ``zeocore
#: validate`` hands them the raw stdin, not a parsed value.
BYTE_CONTRACTS: Final = frozenset({"ui.result-card", "library.view-v2"})


def resolve(name: str) -> str | None:
    """The registered name for ``name`` (itself, or the alias), or None."""
    name = ALIASES.get(name, name)
    return name if name in SCHEMAS else None


def adapter(name: str) -> TypeAdapter[Any]:
    """The validator for ``name``; ``KeyError`` for an unknown name."""
    module, attribute = SCHEMAS[name].split(":")
    return TypeAdapter(getattr(importlib.import_module(module), attribute))


class _AbsentOnly(GenerateJsonSchema):
    """A key that may be absent is not a key that may be null.

    The default says a field typed ``X | None`` accepts ``null`` and defaults to it.
    A contract whose models refuse an explicit ``null`` publishes the key as ``X``,
    optional, with no ``default: null``.
    """

    def nullable_schema(self, schema: core_schema.NullableSchema) -> JsonSchemaValue:
        return self.generate_inner(schema["schema"])

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        out = super().default_schema(schema)
        if out.get("default", 0) is None:
            del out["default"]
        return out


#: Schemas whose models refuse ``null`` for every optional key (published absent-only).
ABSENT_ONLY: Final = frozenset({"ui.intervention-card", "ui.calendar-view"})


def render_schema(name: str) -> dict[str, Any]:
    generator = _AbsentOnly if name in ABSENT_ONLY else GenerateJsonSchema
    schema = adapter(name).json_schema(by_alias=True, schema_generator=generator)
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["$id"] = f"https://zeocore.zeo.ac/contracts/zeocore-v1/{name}.schema.json"
    return schema


def rendered_file(name: str) -> str:
    """The exact committed bytes for ``name``'s schema file."""
    return (
        json.dumps(render_schema(name), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n"
    )
