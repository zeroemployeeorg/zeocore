"""``zeocore``: zeocore for callers that don't import Python.

One command, JSON in and JSON out, so another service or language (the
ZEOconnect Broker and WEB, DuckTyper's TypeScript) can use zeocore's
contracts and operations without importing the package or pinning to its
Python models. See ``zeo_core.cli.__main__`` for the commands.
"""

from .schemas import SCHEMAS, render_schema

__all__ = ["SCHEMAS", "render_schema"]
