"""Compatibility path for ``zeo_core.release.app_tokens`` (zeocore-SOW-36 slice 0).

The GitHub App signer serves the merger and ``zeocore release``, so it lives
with the release component and stays local (the operator's ruling, 2026-10-10:
the key custody is separate from ZEOconnect). This module only re-exports it,
so an old import path keeps working; nothing in zeocore imports it.
"""

from __future__ import annotations

from zeo_core.release import app_tokens as _moved
from zeo_core.release.app_tokens import (
    GITHUB_API,
    Access,
    AppKeySource,
    AppTokenError,
    FileAppKeySource,
    InstallationToken,
    InstallationTokens,
    KeychainAppKeySource,
    app_jwt,
)

__all__ = [
    "GITHUB_API",
    "Access",
    "AppKeySource",
    "AppTokenError",
    "FileAppKeySource",
    "InstallationToken",
    "InstallationTokens",
    "KeychainAppKeySource",
    "app_jwt",
]


def __getattr__(name: str) -> object:
    """Any other name (a private helper, a patched attribute) is the moved module's."""
    try:
        return getattr(_moved, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
