"""``zeocore <command>``: JSON on stdin, one JSON object on stdout.

Commands:

- ``zeocore version``: the release, this command's protocol and the
  ZEOconnect protocol it speaks.
- ``zeocore schema list``: the stable schema names.
- ``zeocore schema <name>``: that contract as JSON Schema 2020-12.
- ``zeocore validate <name>``: validate stdin against ``<name>``. Answers
  ``{"ok": true, "value": ...}`` with the normalized value, or
  ``{"ok": false, "errors": [{"loc": [...], "type": ...}]}``. Errors never
  echo input values.
- ``zeocore digest``: the sha256 of stdin's RFC 8785 canonical bytes.
- ``zeocore image``: one Nano Banana or Recraft call
  (``zeo_core.integrations.imaging.command``).

Rules every command keeps: stdout carries exactly one JSON object and
nothing else; diagnostics go to stderr. Input JSON is strict (no duplicate
keys, no NaN, at most 1 MiB). Exit status:

- 0: done;
- 2: the command or its input can't be used (unknown command or schema,
  input that isn't strict JSON);
- 3: the command ran and said no: a value that doesn't match its schema, or
  an operation that was refused or failed.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from typing import Any, Final

from pydantic import ValidationError

from zeo_core.adapters.runtime_host.canonical import ProtocolError, digest, parse_json

from .schemas import SCHEMAS, adapter, render_schema

#: This command's own protocol: the rules above. A change to them is a new
#: major version of the command, announced like a contract change.
CLI_PROTOCOL: Final = "1"
OK, INVALID, FAILED = 0, 2, 3

Answer = tuple[int, dict[str, Any]]
Command = Callable[[Sequence[str], bytes], Answer]


def _invalid(message: str) -> Answer:
    return INVALID, {"ok": False, "outcome": "invalid_request", "message": message}


def _version(args: Sequence[str], _stdin: bytes) -> Answer:
    if args:
        return _invalid("version takes no arguments")
    from zeo_core import __version__
    from zeo_core.integrations.hosted.transport import ZEOCONNECT_PROTOCOL_VERSION

    return OK, {
        "ok": True,
        "zeocore": __version__,
        "cli_protocol": CLI_PROTOCOL,
        "zeoconnect_protocol": ZEOCONNECT_PROTOCOL_VERSION,
        "commands": sorted(COMMANDS),
    }


def _schema(args: Sequence[str], _stdin: bytes) -> Answer:
    if list(args) == ["list"]:
        return OK, {"ok": True, "schemas": sorted(SCHEMAS)}
    if len(args) != 1 or args[0] not in SCHEMAS:
        return _invalid("schema takes 'list' or one schema name")
    return OK, render_schema(args[0])


def _validate(args: Sequence[str], stdin: bytes) -> Answer:
    if len(args) != 1 or args[0] not in SCHEMAS:
        return _invalid("validate takes one schema name")
    try:
        value = parse_json(stdin)
    except ProtocolError as error:
        return _invalid(str(error))
    validator = adapter(args[0])
    try:
        model = validator.validate_python(value)
    except ValidationError as error:
        return FAILED, {
            "ok": False,
            "outcome": "invalid",
            # loc and type only: a message or input could quote the value.
            "errors": [
                {"loc": list(item["loc"]), "type": item["type"]}
                for item in error.errors(include_url=False, include_input=False)
            ],
        }
    return OK, {
        "ok": True,
        "value": validator.dump_python(model, mode="json", by_alias=True),
    }


def _digest(args: Sequence[str], stdin: bytes) -> Answer:
    if args:
        return _invalid("digest takes no arguments")
    try:
        return OK, {"ok": True, "sha256": digest(parse_json(stdin))}
    except ProtocolError as error:
        return _invalid(str(error))


def _image(args: Sequence[str], stdin: bytes) -> Answer:
    if args:
        return _invalid("image takes its request on stdin")
    from zeo_core.integrations.imaging.command import run as image

    return image(stdin)


#: Every command, named explicitly. Nothing is discovered or loaded by name.
COMMANDS: Final[dict[str, Command]] = {
    "digest": _digest,
    "image": _image,
    "schema": _schema,
    "validate": _validate,
    "version": _version,
}
_READS_STDIN: Final = frozenset({"digest", "image", "validate"})


def run(argv: Sequence[str], stdin: Callable[[], bytes]) -> Answer:
    if not argv or argv[0] not in COMMANDS:
        return _invalid("commands: " + ", ".join(sorted(COMMANDS)))
    name, args = argv[0], argv[1:]
    return COMMANDS[name](args, stdin() if name in _READS_STDIN else b"")


def main(argv: Sequence[str] | None = None) -> None:
    status, answer = run(
        sys.argv[1:] if argv is None else argv, lambda: sys.stdin.buffer.read()
    )
    sys.stdout.write(json.dumps(answer, ensure_ascii=False, sort_keys=True) + "\n")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
