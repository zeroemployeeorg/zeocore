"""``zeocore <command>``: JSON on stdin, JSON lines on stdout.

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

Rules every command keeps (``cli_protocol`` 1):

- stdout is JSON lines and nothing else. Events (a pairing code, an approval
  link, waiting) carry an ``event`` key; the result is always the last line,
  carries ``ok`` and never ``event``. A command with no events prints one
  line. Diagnostics go to stderr.
- Input JSON is strict: no duplicate keys, no NaN, at most 1 MiB.
- Exit status is zeocore's one family, shared with the YouTube publish
  command: 0 done; 2 invalid input, nothing sent; 10 approval required; 11
  waiting, so try the same request later; 12 not paired; 13 ambiguous,
  never retried by the command; 20 held or refused (including a value that
  doesn't match its schema).
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
#: The exit family, the same numbers as the YouTube publish command's.
EXIT_DONE: Final = 0
EXIT_INVALID: Final = 2
EXIT_APPROVAL: Final = 10
EXIT_WAIT: Final = 11
EXIT_NOT_PAIRED: Final = 12
EXIT_AMBIGUOUS: Final = 13
EXIT_HELD: Final = 20
OK, INVALID = EXIT_DONE, EXIT_INVALID

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
        return EXIT_HELD, {
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


#: Every command, named explicitly. Nothing is discovered or loaded by name.
COMMANDS: Final[dict[str, Command]] = {
    "digest": _digest,
    "schema": _schema,
    "validate": _validate,
    "version": _version,
}
_READS_STDIN: Final = frozenset({"digest", "validate"})


def run(argv: Sequence[str], stdin: Callable[[], bytes]) -> Answer:
    if not argv or argv[0] not in COMMANDS:
        return _invalid("commands: " + ", ".join(sorted(COMMANDS)))
    name, args = argv[0], argv[1:]
    return COMMANDS[name](args, stdin() if name in _READS_STDIN else b"")


def emit(line: dict[str, Any]) -> None:
    """Write one JSON line to stdout: an event now, or the result last."""
    sys.stdout.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> None:
    status, answer = run(
        sys.argv[1:] if argv is None else argv, lambda: sys.stdin.buffer.read()
    )
    emit(answer)
    raise SystemExit(status)


if __name__ == "__main__":
    main()
