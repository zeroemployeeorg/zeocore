"""The publish job directory: written once, an append-only event log, a final receipt.

A job directory is the whole durable state of publishing one video. The studio writes
``job.json`` and ``authorization.json``. The executor (``publish.py``) and the studio
append to ``events/``. The executor writes ``receipt.json`` once, at the end.

Nothing is updated in place: every file is created exclusively, and state is the fold
of the events. The identifiers follow ZEO Runtime's occurrence derivation
(``internal/autonomy/engine.go``: key = ``loop_id|due_at`` in RFC 3339 UTC with
nanoseconds; ``occurrence_id = "occ_" + hex(sha256(key)[:12])``; ``idempotency_key =
hex(sha256(key))``), so Runtime can adopt these files without a migration.

No secret is ever written here. Session links live in an ``UploadLinkStore``.

YOUTUBE DATA IS KEPT APART (YouTube API Services Developer Policies §III.E.4; the
adviser's note r14, org-zeroemployeeorg#735 6045087532): authorized data may be stored
for at most 30 days unless refreshed. So nothing YouTube returned goes into the
write-once files. The video's id, privacy, schedule and link live only in
``youtube.json``, which is replaced on refresh and deleted when a refresh fails
(``publish retain``). The job's own record (what was sent, when, the operator's
authorization, that a publish happened) stays indefinitely.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from zeo_core.integrations.google.youtube.models import (
    CAPTION_MIME_TYPES,
    THUMBNAIL_MAX_BYTES,
    THUMBNAIL_MIME_TYPES,
    VIDEO_MIME_TYPES,
    Privacy,
    VideoMetadata,
)

SCHEMA_VERSION = 1
_SHA256 = r"^[0-9a-f]{64}$"
#: The only file that holds YouTube data; replaced on refresh, deleted on failure.
PROVIDER_RECORD = "youtube.json"


class JobError(RuntimeError):
    """A job directory that is invalid, unauthorized, or written out of order."""


def rfc3339_nano(value: datetime) -> str:
    """Go's ``time.RFC3339Nano`` for a UTC time (trailing zeros trimmed)."""
    value = value.astimezone(UTC)
    base = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        base += "." + f"{value.microsecond:06d}".rstrip("0")
    return base + "Z"


def runtime_identity(loop_id: str, due_at: datetime) -> tuple[str, str]:
    """``(occurrence_id, idempotency_key)`` exactly as ZEO Runtime derives them."""
    digest = hashlib.sha256(f"{loop_id}|{rfc3339_nano(due_at)}".encode()).digest()
    return "occ_" + digest[:12].hex(), digest.hex()


def loop_id_for(job_id: str) -> str:
    """One AT-trigger loop per job."""
    return "loop_" + hashlib.sha256(job_id.encode()).digest()[:12].hex()


class MediaFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256)
    mime_type: str


class VideoFile(MediaFile):
    mtime_ms: int = Field(ge=0)

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if value not in VIDEO_MIME_TYPES:
            raise ValueError(f"unsupported video media type: {value}")
        return value


class ThumbnailFile(MediaFile):
    size_bytes: int = Field(gt=0, le=THUMBNAIL_MAX_BYTES)

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if value not in THUMBNAIL_MIME_TYPES:
            raise ValueError("thumbnails must be image/jpeg or image/png")
        return value


class CaptionFile(MediaFile):
    language: str = Field(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]+)*$")
    name: str = Field(default="", max_length=150)

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if value not in CAPTION_MIME_TYPES:
            raise ValueError("captions must be application/x-subrip or text/vtt")
        return value


class JobStatus(BaseModel):
    """Privacy and schedule as the operator chose them (no 'must be future' check:
    a job is read again long after its publish time)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    privacy: Privacy
    publish_at: datetime | None = None
    made_for_kids: bool = False

    @model_validator(mode="after")
    def _schedule(self) -> JobStatus:
        if self.publish_at is not None:
            if self.privacy != "private":
                raise ValueError("publish_at requires privacy 'private'")
            if self.publish_at.tzinfo is None:
                raise ValueError("publish_at must carry a time zone")
        return self

    def arguments(self) -> dict[str, Any]:
        return {
            "privacy": self.privacy,
            "publish_at": (
                rfc3339_nano(self.publish_at) if self.publish_at is not None else None
            ),
            "made_for_kids": self.made_for_kids,
        }


class Destination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: str = Field(min_length=1, max_length=100)
    connection_id: str = Field(pattern=r"^con_[A-Za-z0-9_-]{8,200}$")


class Job(BaseModel):
    """``job.json``: one video to publish, written once by the studio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    job_id: str = Field(pattern=r"^ytj_[0-9a-f]{24}$")
    organization_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    loop_id: str
    occurrence_id: str
    due_at: datetime
    idempotency_key: str = Field(pattern=_SHA256)
    effect_class: Literal["PUBLICATION"]
    operation: Literal["youtube.video.create"]
    destination: Destination
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    video: VideoFile
    metadata: VideoMetadata
    status: JobStatus
    notify_subscribers: bool = True
    thumbnail: ThumbnailFile | None = None
    captions: tuple[CaptionFile, ...] = ()
    playlist_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{2,64}$")
    #: Provenance for people (episode, take, what the operator typed); not interpreted.
    source: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def _identity(self) -> Job:
        if self.loop_id != loop_id_for(self.job_id):
            raise ValueError("loop_id is not derived from job_id")
        occurrence_id, key = runtime_identity(self.loop_id, self.due_at)
        if (self.occurrence_id, self.idempotency_key) != (occurrence_id, key):
            raise ValueError("occurrence identity is not Runtime's derivation")
        if self.artifact_digest != "sha256:" + self.video.sha256:
            raise ValueError("artifact_digest must be the video's sha256")
        if self.due_at.tzinfo is None or self.created_at.tzinfo is None:
            raise ValueError("times must carry a time zone")
        languages = [(c.language, c.name) for c in self.captions]
        if len(languages) != len(set(languages)):
            raise ValueError("caption tracks must differ in language or name")
        return self


class Authorization(BaseModel):
    """``authorization.json``: the operator's Publish press, bound to job.json."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    job_id: str
    trust_root: Literal["OPERATOR_DEVICE_EVENT"]
    authorized_at: datetime
    job_sha256: str = Field(pattern=_SHA256)


EventType = Literal[
    "queued",
    "session_requested",
    "approval_required",
    "session_ready",
    "session_lost",
    "relay_engaged",
    "provider_data_dropped",
    "transfer_progress",
    "final_chunk_sent",
    "uploaded",
    "step_done",
    "held",
    "released",
    "cancelled",
    "done",
]


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    seq: int = Field(ge=1)
    at: datetime
    actor: Literal["studio", "executor"]
    type: EventType


class Receipt(BaseModel):
    """``receipt.json``: the job's single outcome. It holds no YouTube data.

    The video's id and link are in ``youtube.json`` (``provider_record``), which
    retention refreshes or deletes. ``provider_object_id`` and ``video_url`` stay in
    the schema for ZEO Runtime's receipt shape and are always ``None`` here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    job_id: str
    outcome: Literal["SUCCEEDED", "REFUSED", "AMBIGUOUS"]
    provider_object_id: None = None
    response_sha256: None = None
    video_url: None = None
    provider_record: str | None = None
    observed_at: datetime
    reason: str | None = None


class ProviderRecord(BaseModel):
    """``youtube.json``: what YouTube said about the video, and when we last asked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    privacy: str | None = None
    publish_at: str | None = None
    video_url: str
    fetched_at: datetime


@dataclass
class StepState:
    """What the fold knows about one step (``video``, ``thumbnail``, ...)."""

    attempt: int = 1
    requested_at: datetime | None = None
    approval_url: str | None = None
    approval_at: datetime | None = None
    session_opened_at: datetime | None = None
    final_chunk_sent: bool = False
    received: int = 0
    done: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobState:
    """The fold of a job's events."""

    steps: dict[str, StepState] = field(default_factory=dict)
    #: From ``youtube.json`` (JobDirectory.state); None once retention dropped it.
    video_id: str | None = None
    uploaded: bool = False
    #: Why the YouTube fields were deleted, if they were.
    provider_dropped: str | None = None
    held: str | None = None
    held_detail: str = ""
    cancelled: bool = False
    done: bool = False
    #: YouTube refused a link without a token: chunks go through custody.
    relay: bool = False
    last_seq: int = 0

    def step(self, name: str) -> StepState:
        return self.steps.setdefault(name, StepState())


def _step_event(step: StepState, event: Event, extra: dict[str, Any]) -> None:
    """Apply an event that belongs to one step."""
    match event.type:
        case "session_requested":
            step.attempt = int(extra.get("attempt", step.attempt))
            step.requested_at = event.at
        case "approval_required":
            step.approval_url = str(extra.get("approval_url") or "") or None
            step.approval_at = event.at
        case "session_ready":
            step.session_opened_at = event.at
            step.approval_url = None
            step.final_chunk_sent = False
        case "session_lost":
            step.attempt = int(extra.get("next_attempt", step.attempt + 1))
            step.requested_at = step.session_opened_at = step.approval_at = None
            step.approval_url = None
            step.final_chunk_sent = bool(extra.get("final_chunk_sent", False))
        case "transfer_progress":
            step.received = int(extra.get("received", step.received))
        case "final_chunk_sent":
            step.final_chunk_sent = True
        case "step_done":
            step.done = True
            step.detail = dict(extra.get("detail") or {})


def _job_event(state: JobState, event: Event, extra: dict[str, Any]) -> None:
    """Apply an event that concerns the whole job."""
    match event.type:
        case "uploaded":
            state.uploaded = True
            state.step("video").done = True
        case "provider_data_dropped":
            state.provider_dropped = str(extra.get("reason", "dropped"))
        case "held":
            state.held = str(extra.get("reason", "held"))
            state.held_detail = str(extra.get("detail", ""))
        case "released":
            if state.held == "ambiguous_upload":
                # The operator checked the channel: open a new session.
                state.step("video").final_chunk_sent = False
            state.held = None
            state.held_detail = ""
        case "relay_engaged":
            state.relay = True
        case "cancelled":
            state.cancelled = True
        case "done":
            state.done = True


_STEP_EVENTS = frozenset(
    {
        "session_requested",
        "approval_required",
        "session_ready",
        "session_lost",
        "transfer_progress",
        "final_chunk_sent",
        "step_done",
    }
)


def fold(events: list[Event]) -> JobState:
    state = JobState()
    for event in sorted(events, key=lambda e: e.seq):
        state.last_seq = event.seq
        extra = event.model_extra or {}
        if event.type in _STEP_EVENTS:
            _step_event(state.step(str(extra.get("step", "video"))), event, extra)
        else:
            _job_event(state, event, extra)
    return state


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        view = view[os.write(descriptor, view) :]
    os.fsync(descriptor)


def _create_exclusive(path: Path, content: bytes) -> None:
    """Create ``path`` holding ``content`` whole, or not at all.

    The bytes are written and synced under a temporary name, then hard-linked
    into place. A process killed at any point therefore leaves either no file
    under the final name or the whole file, never a partial one. The link
    fails if ``path`` exists, so files stay write-once.

    A filesystem that can't hard-link is refused (``JobError``) rather than
    written directly, because a direct write would bring back the partial
    file. Power-loss durability also depends on the filesystem honouring the
    file and directory fsyncs. The directory sync is best effort.
    """
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            _write_all(descriptor, content)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise
        except OSError as error:
            raise JobError(
                "this filesystem can't publish job files atomically"
                f" (hard link failed: {error.strerror or error.errno})"
            ) from None
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class JobDirectory:
    """Read and append to one job directory; never rewrites a file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def events_dir(self) -> Path:
        return self.path / "events"

    def job(self) -> Job:
        try:
            raw = (self.path / "job.json").read_bytes()
        except OSError:
            raise JobError("job.json is missing") from None
        try:
            return Job.model_validate_json(raw)
        except ValueError as error:
            raise JobError(f"job.json is invalid: {error}") from None

    def authorized_job(self) -> Job:
        """The job, only if the operator's authorization binds these exact bytes."""
        job = self.job()
        try:
            authorization = Authorization.model_validate_json(
                (self.path / "authorization.json").read_bytes()
            )
        except OSError:
            raise JobError(
                "the job is not authorized (no authorization.json)"
            ) from None
        except ValueError as error:
            raise JobError(f"authorization.json is invalid: {error}") from None
        digest = hashlib.sha256((self.path / "job.json").read_bytes()).hexdigest()
        if authorization.job_id != job.job_id or authorization.job_sha256 != digest:
            raise JobError("authorization.json does not bind this job.json")
        return job

    def events(self) -> list[Event]:
        if not self.events_dir.is_dir():
            return []
        out = []
        for entry in sorted(self.events_dir.glob("*.json")):
            out.append(Event.model_validate_json(entry.read_bytes()))
        return out

    def state(self) -> JobState:
        state = fold(self.events())
        record = self.provider_record()
        state.video_id = record.video_id if record is not None else None
        return state

    def provider_record(self) -> ProviderRecord | None:
        path = self.path / PROVIDER_RECORD
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        return ProviderRecord.model_validate_json(raw)

    def write_provider_record(self, record: ProviderRecord) -> None:
        """Write or replace ``youtube.json`` atomically (the one replaceable file)."""
        temporary = self.path / f".{PROVIDER_RECORD}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(descriptor, _canonical(record.model_dump(mode="json")))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path / PROVIDER_RECORD)

    def drop_provider_record(self, reason: str) -> None:
        """Delete the YouTube fields and record when and why (the job record stays)."""
        try:
            (self.path / PROVIDER_RECORD).unlink()
        except FileNotFoundError:
            return
        self.append(actor="executor", type="provider_data_dropped", reason=reason)

    def append(
        self,
        *,
        actor: Literal["studio", "executor"],
        type: EventType,  # noqa: A002 - the event field is named "type" on disk
        **fields: Any,  # noqa: ANN401 - event fields are free-form JSON
    ) -> Event:
        """Append the next event; a concurrent writer loses the race and retries."""
        self.events_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(64):
            seq = (
                max((int(p.stem) for p in self.events_dir.glob("*.json")), default=0)
                + 1
            )
            event = Event.model_validate(
                {
                    "seq": seq,
                    "at": datetime.now(UTC),
                    "actor": actor,
                    "type": type,
                    **fields,
                }
            )
            try:
                _create_exclusive(
                    self.events_dir / f"{seq:010d}.json",
                    _canonical(event.model_dump(mode="json")),
                )
            except FileExistsError:
                continue
            return event
        raise JobError("could not append an event (contention)")

    def receipt(self) -> Receipt | None:
        path = self.path / "receipt.json"
        if not path.exists():
            return None
        return Receipt.model_validate_json(path.read_bytes())

    def write_receipt(self, receipt: Receipt) -> None:
        try:
            _create_exclusive(
                self.path / "receipt.json", _canonical(receipt.model_dump(mode="json"))
            )
        except FileExistsError:
            raise JobError("receipt.json already exists") from None


__all__ = [
    "PROVIDER_RECORD",
    "SCHEMA_VERSION",
    "Authorization",
    "CaptionFile",
    "Destination",
    "Event",
    "Job",
    "JobDirectory",
    "JobError",
    "JobState",
    "JobStatus",
    "ProviderRecord",
    "Receipt",
    "StepState",
    "ThumbnailFile",
    "VideoFile",
    "fold",
    "loop_id_for",
    "rfc3339_nano",
    "runtime_identity",
]
