"""Upload-link stores: the Keychain through stdin, or an explicit owner-only file."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from zeo_core.connections.adapters.subprocess_runner import CompletedSubprocess
from zeo_core.integrations.google.youtube.links import (
    KEYCHAIN_SERVICE,
    FileUploadLinkStore,
    InMemoryUploadLinkStore,
    KeychainUploadLinkStore,
    LinkStoreError,
)

URL = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret"


class _Runner:
    def __init__(self, codes: list[int], stdout: str = "") -> None:
        self.codes = codes
        self.stdout = stdout
        self.calls: list[tuple[list[str], list[str] | None]] = []

    def run(self, args: list[str]) -> CompletedSubprocess:
        self.calls.append((args, None))
        return CompletedSubprocess(
            returncode=self.codes.pop(0), stdout=self.stdout, stderr=""
        )

    def run_with_secret_stdin(
        self, args: list[str], *, secret_lines: list[str]
    ) -> CompletedSubprocess:
        self.calls.append((args, secret_lines))
        return CompletedSubprocess(returncode=self.codes.pop(0), stdout="", stderr="")


def test_keychain_writes_through_stdin_never_argv() -> None:
    runner = _Runner([0, 0, 0], stdout=URL + "\n")
    store = KeychainUploadLinkStore(runner=runner)
    store.put("ytj_1", "video", URL)
    args, secret = runner.calls[0]
    assert URL not in " ".join(args) and secret == [URL, URL]
    assert (
        args[args.index("-s") + 1] == KEYCHAIN_SERVICE
        and args[args.index("-a") + 1] == "ytj_1/video"
    )
    assert store.get("ytj_1", "video") == URL
    store.delete("ytj_1", "video")


def test_keychain_not_found_and_failures() -> None:
    assert KeychainUploadLinkStore(runner=_Runner([44])).get("j", "s") is None
    with pytest.raises(LinkStoreError, match="unlocked"):
        KeychainUploadLinkStore(runner=_Runner([36])).get("j", "s")
    with pytest.raises(LinkStoreError):
        KeychainUploadLinkStore(runner=_Runner([1])).put("j", "s", URL)
    KeychainUploadLinkStore(runner=_Runner([44])).delete("j", "s")


def test_file_store_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "private" / "links.json"
    store = FileUploadLinkStore(path)
    assert store.get("j", "video") is None
    store.put("j", "video", URL)
    assert store.get("j", "video") == URL
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    store.delete("j", "video")
    assert store.get("j", "video") is None


def test_in_memory() -> None:
    store = InMemoryUploadLinkStore()
    store.put("j", "s", URL)
    assert store.get("j", "s") == URL
    store.delete("j", "s")
    assert store.get("j", "s") is None
