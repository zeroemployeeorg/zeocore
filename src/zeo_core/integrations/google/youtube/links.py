"""Where a device keeps upload session links while an upload is unfinished.

A session link lets anyone who holds it send bytes into that one upload, so it is kept
like a credential: in the macOS Keychain (written through stdin, never argv), or, only
when chosen explicitly, in an owner-only file. It is deleted when its step completes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from zeo_core.connections.adapters.subprocess_runner import (
    RealSubprocessRunner,
    SubprocessRunner,
)

KEYCHAIN_SERVICE = "org.zeroemployee.zeocore.youtube-upload"
_NOT_FOUND = frozenset({44})


class LinkStoreError(RuntimeError):
    """The link store cannot be read or written."""


@runtime_checkable
class UploadLinkStore(Protocol):
    def get(self, job_id: str, step: str) -> str | None: ...

    def put(self, job_id: str, step: str, url: str) -> None: ...

    def delete(self, job_id: str, step: str) -> None: ...


class InMemoryUploadLinkStore:
    """For tests; never a production fallback."""

    def __init__(self) -> None:
        self.links: dict[tuple[str, str], str] = {}

    def get(self, job_id: str, step: str) -> str | None:
        return self.links.get((job_id, step))

    def put(self, job_id: str, step: str, url: str) -> None:
        self.links[(job_id, step)] = url

    def delete(self, job_id: str, step: str) -> None:
        self.links.pop((job_id, step), None)


class KeychainUploadLinkStore:
    """macOS Keychain, one generic password per ``<job_id>/<step>``."""

    def __init__(self, *, runner: SubprocessRunner | None = None) -> None:
        if runner is None and sys.platform != "darwin":
            raise LinkStoreError("the Keychain link store needs macOS")
        self._runner = runner or RealSubprocessRunner()

    def get(self, job_id: str, step: str) -> str | None:
        result = self._runner.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                f"{job_id}/{step}",
                "-w",
            ]
        )
        if result.returncode in _NOT_FOUND:
            return None
        if result.returncode != 0:
            raise LinkStoreError(
                f"the Keychain is unavailable (exit {result.returncode});"
                " is the login keychain unlocked for this session?"
            )
        return result.stdout.strip() or None

    def put(self, job_id: str, step: str, url: str) -> None:
        result = self._runner.run_with_secret_stdin(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                f"{job_id}/{step}",
                "-U",
                "-w",
            ],
            secret_lines=[url, url],
        )
        if result.returncode != 0:
            raise LinkStoreError(
                f"the Keychain refused the upload link (exit {result.returncode})"
            )

    def delete(self, job_id: str, step: str) -> None:
        result = self._runner.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                f"{job_id}/{step}",
            ]
        )
        if result.returncode not in {0, *_NOT_FOUND}:
            raise LinkStoreError(
                f"the Keychain could not delete the link (exit {result.returncode})"
            )


class FileUploadLinkStore:
    """Owner-only JSON file (directory 0700, file 0600). Explicit choice only."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def _read(self) -> dict[str, str]:
        try:
            raw = self._path.read_text()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise LinkStoreError(f"cannot read {self._path}: {error}") from None
        try:
            value = json.loads(raw)
        except ValueError:
            raise LinkStoreError(f"{self._path} is not valid JSON") from None
        return (
            {str(k): str(v) for k, v in value.items()}
            if isinstance(value, dict)
            else {}
        )

    def _write(self, links: dict[str, str]) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        temporary = self._path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, json.dumps(links, sort_keys=True).encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self._path)

    def get(self, job_id: str, step: str) -> str | None:
        return self._read().get(f"{job_id}/{step}")

    def put(self, job_id: str, step: str, url: str) -> None:
        links = self._read()
        links[f"{job_id}/{step}"] = url
        self._write(links)

    def delete(self, job_id: str, step: str) -> None:
        links = self._read()
        if links.pop(f"{job_id}/{step}", None) is not None:
            self._write(links)


__all__ = [
    "KEYCHAIN_SERVICE",
    "FileUploadLinkStore",
    "InMemoryUploadLinkStore",
    "KeychainUploadLinkStore",
    "LinkStoreError",
    "UploadLinkStore",
]
