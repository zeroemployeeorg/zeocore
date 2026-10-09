"""The device-side publish executor: one job directory, run as often as you like.

``python -m zeo_core.integrations.google.youtube.publish run <job-dir> --json``

Each run takes the job as far as it can and exits with one JSON status line:

====  ===================================================================
exit  meaning
====  ===================================================================
0     done (or cancelled)
10    waiting for the operator's approval in ZEOconnect (``approval_url``)
11    waiting: due later, YouTube still processing, paused, or busy
20    held for the operator (``reason``); a ``released`` event resumes it
2     the job is invalid or not authorized
====  ===================================================================

The steps run in order: ``video`` (session, then transfer), ``thumbnail``,
``caption:<language>:<name>``, ``playlist``, ``verify``. Every provider effect goes
through ZEOconnect (exact browser approval, then its orchestrator); this process holds
no provider credential. The only secret it handles is an upload session link, kept in an
``UploadLinkStore``.

Never creating a video twice:

- An unfinished session creates nothing on YouTube, so losing one is harmless; the
  executor asks for a new one (a new idempotency key, so a new approval).
- ``final_chunk_sent`` is written to the job's events before the request carrying the
  last byte. If that answer is lost and the session can no longer be asked, the
  executor looks for the upload (title and file size, ``youtube.video.find_upload``).
  It adopts exactly one match, holds the job as ``ambiguous_upload`` when there are
  several, and opens a new session only when there are none.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from zeo_core.integrations.google.youtube.job import (
    PROVIDER_RECORD,
    CaptionFile,
    Event,
    Job,
    JobDirectory,
    JobError,
    JobState,
    MediaFile,
    ProviderRecord,
    Receipt,
    job_steps,
    rfc3339_nano,
)
from zeo_core.integrations.google.youtube.links import (
    FileUploadLinkStore,
    KeychainUploadLinkStore,
    UploadLinkStore,
)
from zeo_core.integrations.google.youtube.relay import RELAY_CHUNK_BYTES, RelayByteHttp
from zeo_core.integrations.google.youtube.service import UPLOAD_PREFIX
from zeo_core.integrations.google.youtube.transfer import (
    DEFAULT_CHUNK_BYTES,
    ByteHttp,
    FileIdentity,
    HttpxByteHttp,
    ResumableTransfer,
    TransferOutcome,
    TransferState,
)
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedOperationStatus,
)

if TYPE_CHECKING:
    from zeo_core.integrations.hosted.pairing import KeychainSecureSessionStore
    from zeo_core.integrations.hosted.profile import HostedConnectionSummary
    from zeo_core.integrations.hosted.transport import ZEOconnectHTTPTransport

EXIT_DONE = 0
EXIT_INVALID = 2
EXIT_APPROVAL = 10
EXIT_WAIT = 11
EXIT_HELD = 20

#: ZEOconnect approvals expire five minutes after they are issued.
APPROVAL_TTL = timedelta(minutes=4, seconds=30)
#: A scheduled publish time must leave YouTube this long to receive and process.
PUBLISH_MARGIN = timedelta(minutes=10)
#: Looking for a lost upload: allow this much clock difference with YouTube.
CLOCK_SLACK = timedelta(minutes=5)

OPERATIONS = (
    "youtube.channel.get",
    "youtube.video.get",
    "youtube.video.find_upload",
    "youtube.captions.list",
    "youtube.playlist.contains",
    "youtube.video.upload_session.create",
    "youtube.thumbnail.upload_session.create",
    "youtube.caption.upload_session.create",
    "youtube.video.status.update",
    "youtube.playlist.item.add",
)


@runtime_checkable
class YouTubeBroker(Protocol):
    """The ZEOconnect operations of connector revision ``google.youtube@1``."""

    def invoke(
        self, operation_id: str, arguments: dict[str, Any], idempotency_key: str
    ) -> HostedOperationResponse: ...


class HostedYouTubeBroker:
    """``YouTubeBroker`` over the paired device's ZEOconnect client."""

    def __init__(self, client: HostedConnectionClient, connection_id: str) -> None:
        self._client = client
        self._connection_id = connection_id

    def invoke(
        self, operation_id: str, arguments: dict[str, Any], idempotency_key: str
    ) -> HostedOperationResponse:
        return self._client.invoke(
            HostedOperationRequest(
                connection_id=self._connection_id,
                operation_id=operation_id,
                arguments=arguments,
                idempotency_key=idempotency_key,
            )
        )


@dataclass
class RunResult:
    exit_code: int
    status: dict[str, Any] = field(default_factory=dict)


class _EndRunError(Exception):
    """End this run with a result."""

    def __init__(self, result: RunResult) -> None:
        super().__init__(result.status.get("state", ""))
        self.result = result


def sha256_file(path: Path, *, block: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(block):
            digest.update(chunk)
    return digest.hexdigest()


def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


class JobBusyError(Exception):
    """Another process holds the job directory's lock."""


@contextmanager
def job_lock(job_dir: Path) -> Iterator[None]:
    """The job directory's exclusive lock, taken without waiting.

    A run holds it from start to finish. ``close`` takes the same lock, so a
    close can never land inside a run.
    """
    descriptor = os.open(job_dir / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JobBusyError() from None
        yield
    finally:
        os.close(descriptor)


def _close_refusal(
    job: Job, state: JobState, expect_held_seq: int, step: str
) -> str | None:
    if state.held is None:
        return "not_held"
    if state.held_seq != expect_held_seq:
        return "hold_changed"
    if state.held_step is None or state.held_attempt is None:
        return "hold_step_unknown"
    if state.held_step != step:
        return "step_mismatch"
    if state.held_step not in job_steps(job):
        return "unknown_step"
    return None


def _closing_event(directory: JobDirectory) -> Event | None:
    """The studio's close of a hold, if the job has one (the last ``cancelled``)."""
    closes = [e for e in directory.events() if e.type == "cancelled"]
    return closes[-1] if closes else None


def close_held_job(
    job_dir: Path,
    expect_held_seq: int,
    step: str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """Close hold ``expect_held_seq`` at ``step``, on a person's decision.

    It is the studio's Close for a job held on an outcome ZEOconnect
    recorded. It sends nothing to ZEOconnect: no new key, no new attempt.
    ``expect_held_seq`` and ``step`` are what the person was shown. They
    are compared with the journal under the job's lock, and never taken
    as authority:
    - ``not_held``: the job isn't held, or it was released;
    - ``hold_changed``: another hold is in force (a stale decision);
    - ``hold_step_unknown``: the hold recorded no typed step or attempt, as
      with holds zeocore raises itself and holds from older journals;
    - ``step_mismatch``: the hold is on another step.
    It then appends the studio's ``cancelled`` event, naming the hold and
    its recorded step and attempt, and writes the REFUSED receipt with
    reason ``cancelled``. The receipt itself is unchanged.

    A repeat is compared with the original close: the same hold and step
    replays unchanged, with nothing appended, and anything else is
    ``already_closed``. That also holds after an interruption between the
    event and the receipt. While a run holds the lock, it answers ``busy``
    (exit 11).
    """
    now = clock or (lambda: datetime.now(UTC))
    directory = JobDirectory(job_dir)
    try:
        job = directory.authorized_job()
    except JobError as error:
        return RunResult(EXIT_INVALID, {"state": "invalid", "reason": str(error)})

    def refused(reason: str, **fields: Any) -> RunResult:  # noqa: ANN401 - JSON
        return RunResult(
            EXIT_INVALID,
            {"job_id": job.job_id, "state": "invalid", "reason": reason, **fields},
        )

    def cancelled(closing: Event) -> RunResult:
        extra = closing.model_extra or {}
        return RunResult(
            EXIT_DONE,
            {
                "job_id": job.job_id,
                "state": "cancelled",
                "reason": "cancelled",
                "held_seq": extra.get("held_seq"),
                "step": extra.get("step"),
            },
        )

    try:
        with job_lock(directory.path):
            receipt = directory.receipt()
            if receipt is not None and not (
                receipt.outcome == "REFUSED" and receipt.reason == "cancelled"
            ):
                return refused("finished")
            state = directory.state()
            if state.cancelled:
                closing = _closing_event(directory)
                extra = (closing.model_extra if closing else None) or {}
                if closing is None or (extra.get("held_seq"), extra.get("step")) != (
                    expect_held_seq,
                    step,
                ):
                    return refused(
                        "already_closed",
                        closed_held_seq=extra.get("held_seq"),
                        closed_step=extra.get("step"),
                    )
            else:
                if (
                    refusal := _close_refusal(job, state, expect_held_seq, step)
                ) is not None:
                    return refused(refusal)
                closing = directory.append(
                    actor="studio",
                    type="cancelled",
                    closed_on=state.held,
                    held_seq=state.held_seq,
                    step=state.held_step,
                    attempt=state.held_attempt,
                )
            if receipt is None:
                directory.write_receipt(
                    Receipt.model_validate(
                        {
                            "schema_version": 1,
                            "job_id": job.job_id,
                            "outcome": "REFUSED",
                            "provider_record": (
                                PROVIDER_RECORD
                                if directory.provider_record() is not None
                                else None
                            ),
                            "observed_at": now(),
                            "reason": "cancelled",
                        }
                    )
                )
            return cancelled(closing)
    except JobBusyError:
        return RunResult(EXIT_WAIT, {"job_id": job.job_id, "state": "busy"})


class PublishExecutor:
    """Advance one authorized job; safe to run again at any point."""

    def __init__(
        self,
        job_dir: Path,
        *,
        broker: YouTubeBroker,
        links: UploadLinkStore,
        http: ByteHttp | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] | None = None,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        approval_wait: float = 270.0,
        approval_poll: float = 3.0,
        verify_hash: bool = True,
        relay: Callable[[str, str], ByteHttp] | None = None,
    ) -> None:
        self.dir = JobDirectory(job_dir)
        self._relay = relay
        self._broker = broker
        self._links = links
        self._http = http
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._should_stop = should_stop or (lambda: False)
        self._chunk = chunk_bytes
        self._approval_wait = approval_wait
        self._approval_poll = approval_poll
        self._verify_hash = verify_hash
        self._hashed = False
        self._job: Job | None = None

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def run(self) -> RunResult:
        try:
            self._job = self.dir.authorized_job()
        except JobError as error:
            return RunResult(EXIT_INVALID, {"state": "invalid", "reason": str(error)})
        try:
            with self._lock():
                return self._advance()
        except _EndRunError as stop:
            return stop.result

    @property
    def job(self) -> Job:
        if self._job is None:  # run() sets it before any step
            raise JobError("job not loaded")
        return self._job

    @contextmanager
    def _lock(self) -> Iterator[None]:
        try:
            with job_lock(self.dir.path):
                yield
        except JobBusyError:
            raise _EndRunError(self._result(EXIT_WAIT, "busy")) from None

    def _advance(self) -> RunResult:
        receipt = self.dir.receipt()
        state = self.dir.state()
        if receipt is not None:
            return self._finished(receipt)
        if state.cancelled:
            return self._close("REFUSED", reason="cancelled")
        if state.held is not None:
            return self._result(
                EXIT_HELD, "held", reason=state.held, detail=state.held_detail
            )
        if self._clock() < self.job.due_at:
            return self._result(EXIT_WAIT, "waiting", reason="not_due")
        self._video()
        if self.job.thumbnail is not None:
            self._thumbnail(self.job.thumbnail)
        for caption in self.job.captions:
            self._caption(caption)
        if self.job.playlist_id is not None:
            self._playlist(self.job.playlist_id)
        return self._verify()

    # ------------------------------------------------------------------
    # results
    # ------------------------------------------------------------------

    def _state(self) -> JobState:
        return self.dir.state()

    def _result(
        self,
        code: int,
        state: str,
        **fields: Any,  # noqa: ANN401 - status fields are free-form JSON
    ) -> RunResult:
        current = self._state() if self._job is not None else None
        video_id = current.video_id if current is not None else None
        status: dict[str, Any] = {
            "job_id": self._job.job_id if self._job is not None else None,
            "state": state,
            **{k: v for k, v in fields.items() if v is not None},
        }
        if video_id is not None:
            status["video_id"] = video_id
            status["video_url"] = video_url(video_id)
        return RunResult(code, status)

    def _hold(
        self,
        reason: str,
        detail: str = "",
        *,
        step: str | None = None,
        attempt: int | None = None,
    ) -> _EndRunError:
        # A hold on a recorded Broker outcome names its step and attempt as
        # typed fields, so a close is bound to the hold, not to a caller's
        # word (ZEO-RT SOW-99).
        bound = {"step": step, "attempt": attempt} if step is not None else {}
        self.dir.append(
            actor="executor", type="held", reason=reason, detail=detail, **bound
        )
        return _EndRunError(
            self._result(EXIT_HELD, "held", reason=reason, detail=detail)
        )

    def _finished(self, receipt: Receipt) -> RunResult:
        if receipt.outcome == "REFUSED" and receipt.reason == "cancelled":
            return self._result(EXIT_DONE, "cancelled", reason="cancelled")
        code = EXIT_DONE if receipt.outcome == "SUCCEEDED" else EXIT_HELD
        state = "done" if receipt.outcome == "SUCCEEDED" else receipt.outcome.lower()
        return self._result(code, state, reason=receipt.reason)

    def _close(self, outcome: str, *, reason: str | None = None) -> RunResult:
        receipt = Receipt.model_validate(
            {
                "schema_version": 1,
                "job_id": self.job.job_id,
                "outcome": outcome,
                "provider_record": (
                    PROVIDER_RECORD if self.dir.provider_record() is not None else None
                ),
                "observed_at": self._clock(),
                "reason": reason,
            }
        )
        self.dir.write_receipt(receipt)
        if outcome == "SUCCEEDED":
            self.dir.append(actor="executor", type="done")
        return self._finished(receipt)

    # ------------------------------------------------------------------
    # broker calls
    # ------------------------------------------------------------------

    def _invoke(
        self, operation: str, arguments: dict[str, Any], key: str
    ) -> HostedOperationResponse:
        try:
            return self._broker.invoke(operation, arguments, key)
        except HostedClientError as error:
            raise _EndRunError(
                self._result(
                    EXIT_WAIT,
                    "waiting",
                    reason="zeoconnect_unavailable",
                    detail=str(error),
                )
            ) from None

    def _read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A read (no approval). A refusal holds the job: something is misconfigured."""
        key = f"{self.job.idempotency_key[:32]}:read:{operation}:{time.time_ns()}"
        response = self._invoke(operation, arguments, key)
        if response.status is HostedOperationStatus.CONFIRMED and isinstance(
            response.result, dict
        ):
            return dict(response.result)
        error = response.normalized_error.message if response.normalized_error else ""
        raise self._hold(
            "read_failed", f"{operation}: {response.status.value} {error}".strip()
        )

    def _effect(
        self, step: str, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """An approved effect for ``step``; waits for approval, never repeats a key."""
        deadline = time.monotonic() + self._approval_wait
        while True:
            state = self._state().step(step)
            key = f"{self.job.idempotency_key[:32]}:{step}:{state.attempt}"
            if state.requested_at is None:
                self.dir.append(
                    actor="executor",
                    type="session_requested",
                    step=step,
                    attempt=state.attempt,
                )
            response = self._invoke(operation, arguments, key)
            status = response.status
            if status is HostedOperationStatus.CONFIRMED:
                result = response.result if isinstance(response.result, dict) else {}
                return dict(result)
            if status is HostedOperationStatus.APPROVAL_REQUIRED:
                url = str(response.approval_url) if response.approval_url else None
                now = self._clock()
                if (
                    state.approval_at is not None
                    and now - state.approval_at > APPROVAL_TTL
                ):
                    self._new_attempt(step, state.attempt)
                    continue
                if url != state.approval_url:
                    self.dir.append(
                        actor="executor",
                        type="approval_required",
                        step=step,
                        approval_url=url,
                    )
                if self._should_stop() or time.monotonic() >= deadline:
                    raise _EndRunError(
                        self._result(
                            EXIT_APPROVAL,
                            "waiting_approval",
                            step=step,
                            approval_url=url,
                        )
                    )
                self._sleep(self._approval_poll)
                continue
            if status is HostedOperationStatus.REFUSED:
                raise self._hold(
                    "refused_in_zeoconnect",
                    f"{step}: {operation}",
                    step=step,
                    attempt=state.attempt,
                )
            if status is HostedOperationStatus.AMBIGUOUS:
                # The broker may or may not have done it; the step's own
                # reconciliation decides on the next run.
                self._new_attempt(step, state.attempt)
                raise _EndRunError(
                    self._result(
                        EXIT_WAIT, "waiting", reason="ambiguous_retry", step=step
                    )
                )
            message = (
                response.normalized_error.message if response.normalized_error else ""
            )
            raise self._hold(
                "provider_refused",
                f"{step}: {message}".strip(),
                step=step,
                attempt=state.attempt,
            )

    def _new_attempt(
        self, step: str, attempt: int, *, final_chunk_sent: bool = False
    ) -> None:
        self.dir.append(
            actor="executor",
            type="session_lost",
            step=step,
            next_attempt=attempt + 1,
            final_chunk_sent=final_chunk_sent,
        )

    # ------------------------------------------------------------------
    # upload steps
    # ------------------------------------------------------------------

    def _stored(self, step: str) -> tuple[str, str | None] | None:
        """The step's stored upload link and relay seal, if a session is open."""
        raw = self._links.get(self.job.job_id, step)
        if raw is None:
            return None
        if raw.startswith("{"):
            value = json.loads(raw)
            return str(value["url"]), value.get("seal")
        return raw, None

    def _session(
        self, step: str, operation: str, arguments: dict[str, Any]
    ) -> tuple[str, str | None]:
        """The step's upload link and seal: stored, or a new approved session."""
        stored = self._stored(step)
        if stored is not None:
            return stored
        for _ in range(2):
            result = self._effect(step, operation, arguments)
            url = result.get("upload_url")
            seal = result.get("relay_seal")
            if isinstance(url, str) and url:
                if not url.startswith(UPLOAD_PREFIX):
                    raise self._hold("foreign_upload_link", step)
                seal = seal if isinstance(seal, str) and seal else None
                self._links.put(
                    self.job.job_id, step, json.dumps({"url": url, "seal": seal})
                )
                self.dir.append(actor="executor", type="session_ready", step=step)
                return url, seal
            # A replay: the broker never stores links, so ask under a new key.
            self._new_attempt(step, self._state().step(step).attempt)
        raise self._hold("no_upload_link", step)

    def _check_hash(self, step: str, media: MediaFile) -> None:
        """The file must still be the one the operator approved (once per run)."""
        if step == "video" and (self._hashed or not self._verify_hash):
            return
        if sha256_file(media.path) != media.sha256:
            raise self._hold(
                "file_changed", f"{step}: sha256 no longer matches the job"
            )
        if step == "video":
            self._hashed = True

    def _transfer(
        self,
        step: str,
        link: tuple[str, str | None],
        media: MediaFile,
        mtime_ms: int | None,
    ) -> dict[str, Any] | None:
        """Send the file; the completed resource, or None when the session expired."""
        if mtime_ms is None:
            mtime_ms = os.stat(media.path).st_mtime_ns // 1_000_000
        self._check_hash(step, media)
        last_percent = [-1]

        def progress(received: int, size: int) -> None:
            percent = int(received * 100 / size) if size else 100
            if percent != last_percent[0]:
                last_percent[0] = percent
                self.dir.append(
                    actor="executor",
                    type="transfer_progress",
                    step=step,
                    received=received,
                    size=size,
                )

        url, seal = link
        identity = FileIdentity(media.path, media.size_bytes, mtime_ms)

        def attempt(http: ByteHttp | None, chunk: int) -> TransferOutcome:
            return ResumableTransfer(
                url=url,
                file=identity,
                mime_type=media.mime_type,
                http=http,
                chunk_bytes=chunk,
                on_progress=progress,
                before_final_chunk=lambda: self._mark_final(step),
                should_stop=self._should_stop,
                sleep=self._sleep,
            ).run()

        relay = self._relay_for(seal, media.mime_type)
        if self._state().relay and relay is not None:
            outcome = attempt(relay, min(self._chunk, RELAY_CHUNK_BYTES))
        else:
            outcome = attempt(self._http, self._chunk)
            if outcome.state is TransferState.REFUSED and relay is not None:
                # YouTube wants a token on each chunk: same session, through custody.
                self.dir.append(
                    actor="executor",
                    type="relay_engaged",
                    step=step,
                    status_code=outcome.status_code,
                )
                outcome = attempt(relay, min(self._chunk, RELAY_CHUNK_BYTES))
        return self._settle(step, outcome, media)

    def _relay_for(self, seal: str | None, mime_type: str) -> ByteHttp | None:
        if seal is None or self._relay is None:
            return None
        return self._relay(seal, mime_type)

    def _mark_final(self, step: str) -> None:
        self.dir.append(actor="executor", type="final_chunk_sent", step=step)

    def _settle(
        self, step: str, outcome: TransferOutcome, media: MediaFile
    ) -> dict[str, Any] | None:
        """Turn a transfer outcome into the step's next move."""
        if outcome.state in (TransferState.COMPLETED, TransferState.EXPIRED):
            self._links.delete(self.job.job_id, step)
        if outcome.state is TransferState.COMPLETED:
            return outcome.resource or {}
        if outcome.state is TransferState.EXPIRED:
            state = self._state().step(step)
            self._new_attempt(
                step, state.attempt, final_chunk_sent=state.final_chunk_sent
            )
            return None
        if outcome.state is TransferState.PAUSED:
            raise _EndRunError(
                self._result(
                    EXIT_WAIT,
                    "waiting",
                    reason="unreachable" if outcome.detail else "paused",
                    detail=outcome.detail or None,
                    step=step,
                    received=outcome.received,
                    size=media.size_bytes,
                )
            )
        reasons = {
            TransferState.REFUSED: (
                "session_link_refused",
                f"{step}: YouTube answered HTTP {outcome.status_code} to the upload"
                " link and the custody relay could not take over",
            ),
            TransferState.REJECTED: (
                "upload_rejected",
                f"{step}: HTTP {outcome.status_code} {outcome.detail}".strip(),
            ),
            TransferState.FILE_CHANGED: (
                "file_changed",
                f"{step}: the file changed during the upload",
            ),
        }
        reason, detail = reasons[outcome.state]
        raise self._hold(reason, detail)

    def _video(self) -> None:
        job = self.job
        for _ in range(4):
            state = self._state()
            if state.uploaded:
                return
            step = state.step("video")
            if self._links.get(job.job_id, "video") is None and step.final_chunk_sent:
                if self._adopt_lost_upload(step.requested_at or job.created_at):
                    return
            if self._links.get(job.job_id, "video") is None:
                publish_at = job.status.publish_at
                if (
                    publish_at is not None
                    and publish_at < self._clock() + PUBLISH_MARGIN
                ):
                    raise self._hold(
                        "publish_time_passed",
                        f"publish_at {rfc3339_nano(publish_at)} is too close or past",
                    )
            link = self._session(
                "video",
                "youtube.video.upload_session.create",
                {
                    "size_bytes": job.video.size_bytes,
                    "content_sha256": job.video.sha256,
                    "mime_type": job.video.mime_type,
                    "metadata": job.metadata.model_dump(mode="json"),
                    "status": job.status.arguments(),
                    "notify_subscribers": job.notify_subscribers,
                },
            )
            resource = self._transfer("video", link, job.video, job.video.mtime_ms)
            if resource is None:
                continue
            video_id = resource.get("id")
            if not isinstance(video_id, str) or not video_id:
                raise self._hold(
                    "no_video_id", "YouTube completed the upload without an id"
                )
            status = resource.get("status")
            privacy = status.get("privacyStatus") if isinstance(status, dict) else None
            self._record_upload(video_id, privacy=privacy)
            return
        raise self._hold("session_churn", "four upload sessions in one run")

    def _record_upload(
        self, video_id: str, *, privacy: object = None, reconciled: bool = False
    ) -> None:
        """YouTube's id goes only into youtube.json; the event says that it happened."""
        self.dir.write_provider_record(
            ProviderRecord(
                video_id=video_id,
                privacy=privacy if isinstance(privacy, str) else None,
                video_url=video_url(video_id),
                fetched_at=self._clock(),
            )
        )
        extra = {"reconciled": True} if reconciled else {}
        self.dir.append(
            actor="executor", type="uploaded", provider_record=PROVIDER_RECORD, **extra
        )

    def _adopt_lost_upload(self, since: datetime) -> bool:
        found = self._read(
            "youtube.video.find_upload",
            {
                "title": self.job.metadata.title,
                "size_bytes": self.job.video.size_bytes,
                "uploaded_after": rfc3339_nano(since - CLOCK_SLACK),
            },
        )
        matches = found.get("matches")
        matches = matches if isinstance(matches, list) else []
        if (
            len(matches) == 1
            and isinstance(matches[0], dict)
            and matches[0].get("video_id")
        ):
            self._record_upload(
                str(matches[0]["video_id"]),
                privacy=matches[0].get("privacy"),
                reconciled=True,
            )
            return True
        if len(matches) > 1:
            raise self._hold(
                "ambiguous_upload",
                f"{len(matches)} uploads match; check the channel, then release",
            )
        state = self._state().step("video")
        self._new_attempt("video", state.attempt, final_chunk_sent=False)
        return False

    def _thumbnail(self, media: MediaFile) -> None:
        self._media_step(
            "thumbnail",
            "youtube.thumbnail.upload_session.create",
            {
                "video_id": self._video_id(),
                "size_bytes": media.size_bytes,
                "content_sha256": media.sha256,
                "mime_type": media.mime_type,
            },
            media,
            already_done=lambda: False,
        )

    def _caption(self, caption: CaptionFile) -> None:
        video_id = self._video_id()

        def present() -> bool:
            tracks = self._read("youtube.captions.list", {"video_id": video_id}).get(
                "tracks"
            )
            return any(
                isinstance(t, dict)
                and t.get("language") == caption.language
                and (t.get("name") or "") == caption.name
                for t in (tracks if isinstance(tracks, list) else [])
            )

        self._media_step(
            f"caption:{caption.language}:{caption.name}",
            "youtube.caption.upload_session.create",
            {
                "video_id": video_id,
                "language": caption.language,
                "name": caption.name,
                "size_bytes": caption.size_bytes,
                "content_sha256": caption.sha256,
                "mime_type": caption.mime_type,
            },
            caption,
            already_done=present,
        )

    def _media_step(
        self,
        step: str,
        operation: str,
        arguments: dict[str, Any],
        media: MediaFile,
        *,
        already_done: Callable[[], bool],
    ) -> None:
        for _ in range(4):
            state = self._state().step(step)
            if state.done:
                return
            if self._links.get(self.job.job_id, step) is None and already_done():
                self.dir.append(
                    actor="executor",
                    type="step_done",
                    step=step,
                    detail={"existing": True},
                )
                return
            link = self._session(step, operation, arguments)
            resource = self._transfer(step, link, media, None)
            if resource is None:
                continue
            self.dir.append(
                actor="executor",
                type="step_done",
                step=step,
                # no YouTube ids in the write-once events (retention, r14 §1)
                detail={},
            )
            return
        raise self._hold("session_churn", f"{step}: four sessions in one run")

    def _playlist(self, playlist_id: str) -> None:
        if self._state().step("playlist").done:
            return
        result = self._effect(
            "playlist",
            "youtube.playlist.item.add",
            {"playlist_id": playlist_id, "video_id": self._video_id()},
        )
        self.dir.append(
            actor="executor",
            type="step_done",
            step="playlist",
            detail={"already_present": bool(result.get("already_present"))},
        )

    def _video_id(self) -> str:
        state = self._state()
        if state.video_id is None:
            if state.uploaded:
                # Retention deleted the YouTube fields (no refresh within 30 days).
                raise self._hold(
                    "provider_data_dropped",
                    state.provider_dropped or "the video's YouTube record is gone",
                )
            raise JobError("no video id yet")  # the video step runs first
        return state.video_id

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------

    def _verify(self) -> RunResult:
        video = self._read("youtube.video.get", {"video_id": self._video_id()})
        processing = video.get("processing_status")
        upload = video.get("upload_status")
        if upload in {"failed", "rejected", "deleted"} or processing in {
            "failed",
            "terminated",
        }:
            reason = (
                video.get("rejection_reason") or video.get("failure_reason") or upload
            )
            raise self._hold("youtube_rejected", str(reason))
        self._refresh_record(video)
        if processing not in {"succeeded", None} or upload == "uploaded":
            return self._result(EXIT_WAIT, "waiting", reason="youtube_processing")
        expected, scheduled = self._expected_privacy()
        privacy = video.get("privacy")
        if privacy != expected:
            raise self._hold(
                "privacy_overridden",
                f"YouTube shows {privacy}, the job asked {expected}"
                " (an unverified API project keeps uploads private)",
            )
        if scheduled is not None:
            shown = _parse(video.get("publish_at"))
            if shown is None or abs((shown - scheduled).total_seconds()) > 1:
                raise self._hold(
                    "schedule_overridden",
                    f"YouTube shows publish_at {video.get('publish_at')},"
                    f" the job asked {rfc3339_nano(scheduled)}",
                )
        return self._close("SUCCEEDED")

    def _refresh_record(self, video: dict[str, Any]) -> None:
        video_id = video.get("video_id")
        if isinstance(video_id, str) and video_id:
            self.dir.write_provider_record(
                ProviderRecord(
                    video_id=video_id,
                    privacy=video.get("privacy"),
                    publish_at=video.get("publish_at"),
                    video_url=video_url(video_id),
                    fetched_at=self._clock(),
                )
            )

    def _expected_privacy(self) -> tuple[str, datetime | None]:
        publish_at = self.job.status.publish_at
        if publish_at is None:
            return self.job.status.privacy, None
        if self._clock() >= publish_at:
            return "public", None
        return "private", publish_at


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _link_store(choice: str, job_dir: Path | None) -> UploadLinkStore:
    if choice == "file":
        root = Path(os.environ.get("ZEO_YOUTUBE_LINK_FILE", ""))
        if not root.name:
            if job_dir is None:
                raise JobError("--link-store file needs ZEO_YOUTUBE_LINK_FILE")
            root = job_dir.parent.parent / "private" / "upload-links.json"
        return FileUploadLinkStore(root)
    return KeychainUploadLinkStore()


def _transport() -> tuple[ZEOconnectHTTPTransport, KeychainSecureSessionStore]:
    from zeo_core.integrations.hosted.pairing import KeychainSecureSessionStore
    from zeo_core.integrations.hosted.transport import (
        ZEOCONNECT_PRODUCTION_ORIGIN,
        ZEOconnectHTTPTransport,
    )

    origin = os.environ.get("ZEOCONNECT_URL", ZEOCONNECT_PRODUCTION_ORIGIN)
    store = KeychainSecureSessionStore()
    return (
        ZEOconnectHTTPTransport(
            session_store=store,
            base_url=origin,
            allow_development_origin=os.environ.get("ZEOCONNECT_DEVELOPMENT") == "1",
        ),
        store,
    )


def _byte_http() -> ByteHttp:
    return HttpxByteHttp()


def _print(result: RunResult) -> int:
    print(json.dumps(result.status, sort_keys=True, default=str), flush=True)
    return result.exit_code


def _run(args: argparse.Namespace) -> int:
    job_dir = Path(args.job_dir).resolve()
    try:
        job = JobDirectory(job_dir).authorized_job()
    except JobError as error:
        return _print(
            RunResult(EXIT_INVALID, {"state": "invalid", "reason": str(error)})
        )
    transport, _ = _transport()
    stopping = {"now": False}

    def stop(_signum: int, _frame: object) -> None:
        stopping["now"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    executor = PublishExecutor(
        job_dir,
        broker=HostedYouTubeBroker(
            HostedConnectionClient(transport=transport), job.destination.connection_id
        ),
        links=_link_store(args.link_store, job_dir),
        http=_byte_http(),
        relay=lambda seal, mime_type: RelayByteHttp(
            transport,
            connection_id=job.destination.connection_id,
            seal=seal,
            mime_type=mime_type,
        ),
        should_stop=lambda: stopping["now"],
        chunk_bytes=args.chunk_mib * 1024 * 1024,
    )
    try:
        return _print(executor.run())
    finally:
        transport.close()


def _status(args: argparse.Namespace) -> int:
    directory = JobDirectory(Path(args.job_dir))
    try:
        job = directory.job()
    except JobError as error:
        return _print(
            RunResult(EXIT_INVALID, {"state": "invalid", "reason": str(error)})
        )
    state = directory.state()
    receipt = directory.receipt()
    status: dict[str, Any] = {
        "job_id": job.job_id,
        "video_id": state.video_id,
        "held": state.held,
        "held_detail": state.held_detail,
        "done": receipt is not None,
        "receipt": receipt.model_dump(mode="json") if receipt else None,
        "steps": {
            name: {
                "attempt": step.attempt,
                "approval_url": step.approval_url,
                "received": step.received,
                "done": step.done,
            }
            for name, step in state.steps.items()
        },
    }
    return _print(RunResult(EXIT_DONE, status))


def _retain(args: argparse.Namespace) -> int:
    from zeo_core.integrations.google.youtube.retention import RetentionSweep

    transport, _ = _transport()
    client = HostedConnectionClient(transport=transport)
    try:
        report = RetentionSweep(
            Path(args.publish_root).resolve(),
            broker_for=lambda connection_id: HostedYouTubeBroker(client, connection_id),
        ).run()
        return _print(RunResult(EXIT_DONE, report.as_dict()))
    finally:
        transport.close()


def _pair(args: argparse.Namespace) -> int:
    from zeo_core.integrations.hosted.pairing import (
        HostedConnectionManager,
        PairingPendingError,
    )
    from zeo_core.integrations.hosted.profile import ServiceRequirement

    transport, store = _transport()
    manager = HostedConnectionManager(transport=transport, session_store=store)
    try:
        challenge = manager.begin_pairing(
            ServiceRequirement(service="youtube", operations=OPERATIONS),
            device_name=args.device_name,
        )
        print(
            json.dumps(
                {
                    "state": "pairing",
                    "verification_url": str(challenge.verification_url),
                    "user_code": challenge.user_code,
                    "expires_at": challenge.expires_at.isoformat(),
                }
            ),
            flush=True,
        )
        while datetime.now(UTC) < challenge.expires_at:
            try:
                connections = manager.complete_pairing(challenge)
            except PairingPendingError:
                time.sleep(challenge.polling_interval_seconds)
                continue
            return _print(
                RunResult(
                    EXIT_DONE, {"state": "paired", "connections": _youtube(connections)}
                )
            )
        return _print(RunResult(EXIT_HELD, {"state": "expired"}))
    finally:
        transport.close()


def _youtube(
    connections: Sequence[HostedConnectionSummary],
) -> list[dict[str, Any]]:
    return [
        {
            "connection_id": str(summary.handle),
            "channel": summary.external_identity,
            "status": str(summary.status),
            "operations": list(summary.operations),
        }
        for summary in connections
        if summary.service == "youtube"
    ]


def _close_command(args: argparse.Namespace) -> int:
    return _print(
        close_held_job(Path(args.job_dir).resolve(), args.expect_held_seq, args.step)
    )


def _connections(_args: argparse.Namespace) -> int:
    from zeo_core.integrations.hosted.pairing import HostedConnectionManager

    transport, store = _transport()
    try:
        manager = HostedConnectionManager(transport=transport, session_store=store)
        return _print(
            RunResult(
                EXIT_DONE,
                {"state": "ok", "connections": _youtube(manager.refresh_connections())},
            )
        )
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zeo_core.integrations.google.youtube.publish"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="advance one job directory")
    run.add_argument("job_dir")
    run.add_argument(
        "--json", action="store_true", help="one JSON status line (default)"
    )
    run.add_argument("--link-store", choices=("keychain", "file"), default="keychain")
    run.add_argument("--chunk-mib", type=int, default=16)
    run.set_defaults(handler=_run)
    status = commands.add_parser("status", help="print a job's folded state")
    status.add_argument("job_dir")
    status.set_defaults(handler=_status)
    retain = commands.add_parser(
        "retain", help="refresh or delete YouTube data older than 20/29 days (daily)"
    )
    retain.add_argument("publish_root")
    retain.set_defaults(handler=_retain)
    pair = commands.add_parser("pair", help="pair this device with ZEOconnect")
    pair.add_argument("--device-name", default="ZEO Broadcasting Studio")
    pair.set_defaults(handler=_pair)
    close = commands.add_parser(
        "close", help="close a job held on a recorded outcome (sends nothing)"
    )
    close.add_argument("job_dir")
    close.add_argument(
        "--expect-held-seq",
        type=int,
        required=True,
        help="the seq of the held event the person was shown",
    )
    close.add_argument(
        "--step", required=True, help="the hold's step, as the person was shown it"
    )
    close.add_argument(
        "--json", action="store_true", help="one JSON status line (default)"
    )
    close.set_defaults(handler=_close_command)
    listing = commands.add_parser("connections", help="list YouTube connections")
    listing.set_defaults(handler=_connections)
    args = parser.parse_args(argv)
    if getattr(args, "chunk_mib", 16) <= 0:
        parser.error("--chunk-mib must be positive")
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = [
    "APPROVAL_TTL",
    "EXIT_APPROVAL",
    "EXIT_DONE",
    "EXIT_HELD",
    "EXIT_INVALID",
    "EXIT_WAIT",
    "OPERATIONS",
    "HostedYouTubeBroker",
    "JobBusyError",
    "PublishExecutor",
    "RunResult",
    "YouTubeBroker",
    "close_held_job",
    "job_lock",
    "main",
    "sha256_file",
    "video_url",
]
