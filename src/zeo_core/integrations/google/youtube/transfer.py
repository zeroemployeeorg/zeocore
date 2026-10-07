"""Send a file's bytes to a YouTube resumable session link: no credential, resumable.

The device that holds a multi-GB file runs this. It knows only the session link that
the custody boundary opened (``GoogleYouTubeService.create_*_upload_session``); it
never sees the channel's token.

The protocol (YouTube Data API, resumable uploads):

- ``PUT link`` with ``Content-Range: bytes */SIZE`` and no body asks how much YouTube
  has. ``308`` with ``Range: bytes=0-N`` means N+1 bytes are stored (no ``Range``
  means none); ``200``/``201`` means the upload is complete and carries the resource.
- Each later ``PUT`` sends ``bytes START-END/SIZE``. Every chunk but the last is a
  multiple of 256 KiB.
- ``404``/``410``: the session expired. ``401``/``403``: the link alone is not enough.

Every attempt starts with that probe, so a dropped connection, a killed process or a
reboot resumes at the exact byte YouTube holds, and sending a range again is harmless.
The video exists only when the last byte lands, so no retry here can create a second
one. Transient failures (connection errors, timeouts, 429, 5xx) back off and probe
again for as long as the session lives; nothing gives up on a timer.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from zeo_core.integrations.google.youtube.service import UPLOAD_PREFIX

#: Resumable chunks must be multiples of this (except the last).
CHUNK_UNIT = 256 * 1024
#: Default chunk: 16 MiB keeps a 4K upload to a few hundred requests.
DEFAULT_CHUNK_BYTES = 64 * CHUNK_UNIT
MAX_BACKOFF_SECONDS = 60.0
#: Consecutive failures without progress before a run gives up (about 10 minutes);
#: the next run probes again and resumes, so nothing is lost.
MAX_FAILURES = 20


class TransferError(RuntimeError):
    """A transfer that cannot start (bad link, bad chunk size)."""


@runtime_checkable
class ByteResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def text(self) -> str: ...


@runtime_checkable
class ByteHttp(Protocol):
    """Unauthenticated HTTP PUT to a session link. Redirects are never followed."""

    def put(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout: float
    ) -> ByteResponse: ...


class HttpxByteHttp:
    """Default byte transport: httpx, no redirects, no ambient proxies or auth."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(300.0, connect=15.0),
        )

    def put(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout: float
    ) -> ByteResponse:
        return self._client.put(
            url, headers=dict(headers), content=content, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()


class TransferState(StrEnum):
    COMPLETED = "completed"
    #: The session is gone (404/410). Nothing exists unless the last byte landed.
    EXPIRED = "expired"
    #: 401/403: the link alone is refused; the device would need a credential.
    REFUSED = "refused"
    #: Another 4xx: YouTube rejected the request itself.
    REJECTED = "rejected"
    #: The file no longer matches the job (size or modification time).
    FILE_CHANGED = "file_changed"
    #: The caller asked to stop; resume later from ``received``.
    PAUSED = "paused"


@dataclass(frozen=True)
class TransferOutcome:
    state: TransferState
    received: int
    resource: dict[str, Any] | None = None
    status_code: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class FileIdentity:
    """What the job recorded about the file; checked before every chunk."""

    path: Path
    size_bytes: int
    mtime_ms: int

    def matches(self) -> bool:
        try:
            stat = os.stat(self.path)
        except OSError:
            return False
        return (
            stat.st_size == self.size_bytes
            and stat.st_mtime_ns // 1_000_000 == self.mtime_ms
        )


class _TransientError(Exception):
    pass


@dataclass
class _Step:
    received: int
    resource: dict[str, Any] | None = None
    terminal: TransferOutcome | None = None


class ResumableTransfer:
    """Drive one session link from whatever YouTube holds to completion."""

    def __init__(
        self,
        *,
        url: str,
        file: FileIdentity,
        mime_type: str,
        http: ByteHttp | None = None,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        on_progress: Callable[[int, int], None] | None = None,
        before_final_chunk: Callable[[], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        request_timeout: float = 300.0,
        max_failures: int = MAX_FAILURES,
    ) -> None:
        if not url.startswith(UPLOAD_PREFIX):
            raise TransferError(f"refusing an upload link outside {UPLOAD_PREFIX}")
        if chunk_bytes <= 0 or chunk_bytes % CHUNK_UNIT:
            raise TransferError("chunk_bytes must be a positive multiple of 256 KiB")
        self._url = url
        self._file = file
        self._mime = mime_type
        self._http = http or HttpxByteHttp()
        self._chunk = chunk_bytes
        self._on_progress = on_progress
        self._before_final = before_final_chunk
        self._should_stop = should_stop or (lambda: False)
        self._sleep = sleep
        self._jitter = jitter
        self._timeout = request_timeout
        self._max_failures = max_failures
        self._failures = 0
        self._received = 0

    def run(self) -> TransferOutcome:
        """Probe, then send until complete, expired, refused, changed or stopped."""
        self._failures = 0
        self._received = 0
        while True:
            try:
                return self._attempt()
            except _TransientError as error:
                self._failures += 1
                if self._failures >= self._max_failures:
                    return TransferOutcome(
                        TransferState.PAUSED,
                        self._received,
                        detail=f"unreachable: {error}",
                    )
                delay = min(MAX_BACKOFF_SECONDS, 2.0 ** min(self._failures - 1, 6))
                self._sleep(delay * (0.5 + self._jitter() / 2))

    def _interrupted(self) -> TransferOutcome | None:
        if self._should_stop():
            return TransferOutcome(TransferState.PAUSED, self._received)
        if not self._file.matches():
            return TransferOutcome(TransferState.FILE_CHANGED, self._received)
        return None

    def _attempt(self) -> TransferOutcome:
        """One probe, then chunks until done; a transient failure raises."""
        if stopped := self._interrupted():
            return stopped
        step = self._probe()
        while step.terminal is None and step.resource is None:
            self._received = step.received
            self._progress(self._received)
            if stopped := self._interrupted():
                return stopped
            previous = self._received
            step = self._send(previous)
            if step.terminal is None and step.resource is None:
                if step.received <= previous:
                    raise _TransientError("no progress")
                self._failures = 0
        if step.terminal is not None:
            return step.terminal
        self._progress(self._file.size_bytes)
        return TransferOutcome(
            TransferState.COMPLETED,
            self._file.size_bytes,
            resource=step.resource,
            status_code=200,
        )

    def probe(self) -> TransferOutcome | int:
        """One status check: the stored byte count, or a terminal outcome."""
        step = self._probe()
        if step.terminal is not None:
            return step.terminal
        if step.resource is not None:
            return TransferOutcome(
                TransferState.COMPLETED, self._file.size_bytes, resource=step.resource
            )
        return step.received

    def _probe(self) -> _Step:
        response = self._put({"Content-Range": f"bytes */{self._file.size_bytes}"}, b"")
        return self._interpret(response, received_before=0)

    def _send(self, offset: int) -> _Step:
        size = self._file.size_bytes
        length = min(self._chunk, size - offset)
        with open(self._file.path, "rb") as handle:
            handle.seek(offset)
            data = handle.read(length)
        if len(data) != length:
            return _Step(
                offset, terminal=TransferOutcome(TransferState.FILE_CHANGED, offset)
            )
        if offset + length == size and self._before_final is not None:
            self._before_final()
        response = self._put(
            {
                "Content-Type": self._mime,
                "Content-Range": f"bytes {offset}-{offset + length - 1}/{size}",
            },
            data,
        )
        return self._interpret(response, received_before=offset)

    def _put(self, headers: dict[str, str], content: bytes) -> ByteResponse:
        try:
            return self._http.put(
                self._url, headers=headers, content=content, timeout=self._timeout
            )
        except (httpx.TransportError, OSError, TimeoutError) as error:
            raise _TransientError(type(error).__name__) from None

    def _interpret(self, response: ByteResponse, *, received_before: int) -> _Step:
        status = response.status_code
        if status in (200, 201):
            return _Step(self._file.size_bytes, resource=_json(response.text))
        if status == 308:
            return _Step(_stored_bytes(response.headers))
        if status == 429 or status >= 500:
            raise _TransientError(f"HTTP {status}")
        state = {
            404: TransferState.EXPIRED,
            410: TransferState.EXPIRED,
            401: TransferState.REFUSED,
            403: TransferState.REFUSED,
        }.get(status, TransferState.REJECTED)
        return _Step(
            received_before,
            terminal=TransferOutcome(
                state,
                received_before,
                status_code=status,
                detail=_reason(response.text),
            ),
        )

    def _progress(self, received: int) -> None:
        if self._on_progress is not None:
            self._on_progress(received, self._file.size_bytes)


def _stored_bytes(headers: Mapping[str, str]) -> int:
    for key, value in headers.items():
        if key.lower() == "range":
            # "bytes=0-N": N+1 bytes are stored.
            _, _, span = value.partition("=")
            _, _, last = span.partition("-")
            try:
                return int(last) + 1
            except ValueError:
                raise _TransientError("unreadable Range header") from None
    return 0


def _json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _reason(text: str) -> str:
    errors = _json(text).get("error", {})
    items = errors.get("errors") if isinstance(errors, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return str(items[0].get("reason") or "")
    return ""


__all__ = [
    "CHUNK_UNIT",
    "DEFAULT_CHUNK_BYTES",
    "MAX_BACKOFF_SECONDS",
    "MAX_FAILURES",
    "ByteHttp",
    "ByteResponse",
    "FileIdentity",
    "HttpxByteHttp",
    "ResumableTransfer",
    "TransferError",
    "TransferOutcome",
    "TransferState",
]
