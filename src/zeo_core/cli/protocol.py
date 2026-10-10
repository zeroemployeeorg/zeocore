"""The rules every ``zeocore`` command keeps (``cli_protocol`` 1).

See ``zeo_core.cli.__main__`` for the full statement. Kept apart so each
command module can emit events and answer without importing the dispatcher.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any, Final, NoReturn

CLI_PROTOCOL: Final = "1"
#: The exit family, the same numbers as the YouTube publish command's.
EXIT_DONE: Final = 0
EXIT_INVALID: Final = 2
EXIT_APPROVAL: Final = 10
EXIT_WAIT: Final = 11
EXIT_NOT_PAIRED: Final = 12
EXIT_AMBIGUOUS: Final = 13
EXIT_HELD: Final = 20
EXIT_INTERNAL: Final = 1

Answer = tuple[int, dict[str, Any]]
Emit = Callable[[dict[str, Any]], None]
Command = Callable[[Sequence[str], bytes], Answer]


def emit(line: dict[str, Any]) -> None:
    """Write one JSON line to stdout: an event now, or the result last."""
    sys.stdout.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def invalid(message: str) -> Answer:
    return EXIT_INVALID, {"ok": False, "outcome": "invalid_request", "message": message}


class ArgumentsError(ValueError):
    """The command line can't be used; answered as exit 2, never printed."""


class Arguments(argparse.ArgumentParser):
    """argparse that raises instead of printing usage and exiting."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog=prog, add_help=False)

    def error(self, message: str) -> NoReturn:
        raise ArgumentsError(message)
