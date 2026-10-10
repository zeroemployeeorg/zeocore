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
- ``zeocore image``: one Nano Banana or Recraft call
  (``zeo_core.integrations.imaging.command``).
- ``zeocore login | logout | whoami | connections | invoke | artifact get``:
  the ZEOconnect Broker client (``zeo_core.cli.client``).

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
  doesn't match its schema). 1 is an internal error in zeocore itself, with
  ``{"ok": false, "outcome": "internal"}`` and no detail; for an operation,
  treat it like 13: its state is unknown.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from typing import Any, Final

from pydantic import ValidationError

from zeo_core.adapters.runtime_host.canonical import (
    MAX_BYTES,
    ProtocolError,
    digest,
    parse_json,
)

from . import client
from .protocol import (
    CLI_PROTOCOL,
    EXIT_AMBIGUOUS,
    EXIT_APPROVAL,
    EXIT_DONE,
    EXIT_HELD,
    EXIT_INTERNAL,
    EXIT_INVALID,
    EXIT_NOT_PAIRED,
    EXIT_WAIT,
    Answer,
    Command,
    emit,
    invalid,
)
from .schemas import SCHEMAS, adapter, render_schema

OK, INVALID = EXIT_DONE, EXIT_INVALID
__all__ = [
    "CLI_PROTOCOL",
    "COMMANDS",
    "EXIT_AMBIGUOUS",
    "EXIT_APPROVAL",
    "EXIT_DONE",
    "EXIT_HELD",
    "EXIT_INTERNAL",
    "EXIT_INVALID",
    "EXIT_NOT_PAIRED",
    "EXIT_WAIT",
    "emit",
    "main",
    "run",
]


def _invalid(message: str) -> Answer:
    return invalid(message)


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
        declared = _field_names(render_schema(args[0]))
        return EXIT_HELD, {
            "ok": False,
            "outcome": "invalid",
            # loc and type only: a message or input could quote the value. A
            # key that isn't a declared field (one inside a free-form object,
            # or an unknown extra) is the caller's data, so it shows as "*".
            "errors": [
                {
                    "loc": [
                        part if isinstance(part, int) or part in declared else "*"
                        for part in item["loc"]
                    ],
                    "type": item["type"],
                }
                for item in error.errors(include_url=False, include_input=False)
            ],
        }
    return OK, {
        "ok": True,
        "value": validator.dump_python(model, mode="json", by_alias=True),
    }


def _field_names(schema: object) -> frozenset[str]:
    """Every property name the schema declares, at any depth."""
    names: set[str] = set()
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return frozenset(names)


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
    try:
        command = parse_json(stdin)
    except ProtocolError as error:
        return _invalid(str(error))
    from zeo_core.integrations.imaging.command import run as image

    if os.getenv("ZEOCORE_CONNECTION_PROFILE") == "hosted":
        return image(command, service_factory=_hosted_images)
    return image(command)


def _hosted_images() -> Any:  # noqa: ANN401 -- ImagingService, imported lazily
    """The hosted image service on this profile's grant, bound to its origin."""
    from zeo_core.integrations.hosted.client import HostedConnectionClient
    from zeo_core.integrations.hosted.pairing import SecureStoreError
    from zeo_core.integrations.imaging import ImagingError, build_imaging

    try:
        store = client.make_store(client._profile(None))
        transport = client.make_transport(store)
    except SecureStoreError:
        raise ImagingError(
            "not_paired", "no usable secure session store on this machine"
        ) from None
    except ValueError:
        raise ImagingError(
            "refused", "ZEOCONNECT_URL is not an allowed Broker origin", retry="none"
        ) from None
    return build_imaging(
        profile="hosted", hosted_client=HostedConnectionClient(transport=transport)
    )


#: Every command, named explicitly. Nothing is discovered or loaded by name.
COMMANDS: Final[dict[str, Command]] = {
    "artifact": client.artifact,
    "connections": client.connections,
    "digest": _digest,
    "image": _image,
    "invoke": client.invoke,
    "llm": client.llm,
    "login": client.login,
    "logout": client.logout,
    "upload": client.upload,
    "whoami": client.whoami,
    "schema": _schema,
    "validate": _validate,
    "version": _version,
}
_READS_STDIN: Final = frozenset(
    {"artifact", "digest", "image", "invoke", "llm", "validate"}
)


def run(argv: Sequence[str], stdin: Callable[[], bytes]) -> Answer:
    if not argv or argv[0] not in COMMANDS:
        return _invalid("commands: " + ", ".join(sorted(COMMANDS)))
    name, args = argv[0], argv[1:]
    return COMMANDS[name](args, stdin() if name in _READS_STDIN else b"")


def main(argv: Sequence[str] | None = None) -> None:
    try:
        status, answer = run(
            sys.argv[1:] if argv is None else argv,
            # One byte past the limit is enough to refuse an oversized input.
            lambda: sys.stdin.buffer.read(MAX_BYTES + 1),
        )
    except Exception:
        # Never a traceback in place of the answer. The detail could quote
        # input, so it goes nowhere.
        status, answer = EXIT_INTERNAL, {"ok": False, "outcome": "internal"}
    emit(answer)
    raise SystemExit(status)


if __name__ == "__main__":
    main()
