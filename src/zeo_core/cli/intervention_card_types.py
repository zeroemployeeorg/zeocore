"""TypeScript types for a published card payload schema.

The types are rendered from the published JSON Schema, so a front end that only reads
``contracts/zeocore-v1/ui.intervention-card.d.ts`` sees exactly what the registry
accepts. A key that is optional on the wire is optional in the type and never
``null``: the contract says an absent key is absent, not null.
"""

from __future__ import annotations

import json
from typing import Any, Final

from zeo_core.cli.schemas import render_schema

SCHEMA_NAME: Final = "ui.intervention-card"

_PRIMITIVES: Final = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
}


def _type(node: dict[str, Any]) -> str:
    if "$ref" in node:
        return str(node["$ref"]).rsplit("/", 1)[-1]
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " | ".join(json.dumps(value) for value in node["enum"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            parts = [_type(part) for part in node[key] if part.get("type") != "null"]
            return " | ".join(parts)
    if node.get("type") == "array":
        item = _type(node["items"])
        if node.get("minItems") == node.get("maxItems") is not None:
            return "[" + ", ".join([item] * node["minItems"]) + "]"
        return f"{item}[]" if "|" not in item else f"({item})[]"
    return _PRIMITIVES[node["type"]]


def _interface(name: str, node: dict[str, Any]) -> str:
    required = set(node.get("required", ()))
    lines = [f"export interface {name} {{"]
    for key, prop in node["properties"].items():
        mark = "" if key in required else "?"
        lines.append(f"  {key}{mark}: {_type(prop)};")
    lines.append("}")
    return "\n".join(lines)


def render_typescript(name: str = SCHEMA_NAME) -> str:
    """The exact committed bytes of ``<name>.d.ts``."""
    schema = render_schema(name)
    header = "\n".join(
        [
            f"// Generated from {name}.schema.json. Do not edit by hand.",
            f"// Schema: {schema['$id']}",
            "// Rules that tie fields together (for example intervention_ref equal to",
            "// work_ref.work_id) are enforced by the validator, not by these types.",
        ]
    )
    blocks = [header]
    for def_name, node in sorted(schema["$defs"].items()):
        blocks.append(_interface(def_name, node))
    if "oneOf" in schema:
        cards = " | ".join(part["$ref"].rsplit("/", 1)[-1] for part in schema["oneOf"])
        blocks.append(f"export type InterventionCard = {cards};")
    else:
        blocks.append(_interface(schema["title"], schema))
    return "\n\n".join(blocks) + "\n"
