"""A small stdlib logging shim for the voice router (deviation D3).

The upstream router logs through structlog: ``logger.info("event", key=value)``. Core
adds no logging dependency, so ``get_logger`` returns an object with the same four
methods over the standard library. Output form: ``<event> key=value key=value`` with
each value shown by ``repr``, at the level named. Structlog processors, context binding
and renderers are NOT reproduced; callers that need them install their own logging
configuration.
"""

from __future__ import annotations

import logging


class Logger:
    """The four level methods the router uses, over one stdlib logger."""

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def _emit(self, level: int, event: str, fields: dict[str, object]) -> None:
        if self._log.isEnabledFor(level):
            rendered = " ".join(f"{key}={value!r}" for key, value in fields.items())
            self._log.log(level, "%s", f"{event} {rendered}" if rendered else event)

    def debug(self, event: str, **fields: object) -> None:
        self._emit(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: object) -> None:
        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: object) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: object) -> None:
        self._emit(logging.ERROR, event, fields)


def get_logger(name: str) -> Logger:
    """The structlog-shaped logger for a module name."""
    return Logger(name)
