"""Job directory: Runtime's identity derivation, write-once files, the event fold."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from zeo_core.integrations.google.youtube.job import (
    JobDirectory,
    JobError,
    ProviderRecord,
    Receipt,
    rfc3339_nano,
    runtime_identity,
)

from .helpers import make_job

LOOP = "loop_0123456789abcdef01234567"


# Computed by a Go program that copies ZEO Runtime's internal/autonomy/engine.go:96-98
# (3c378e59) verbatim, using Go's own time.RFC3339Nano formatting.
@pytest.mark.parametrize(
    ("due_at", "key_time", "occurrence_id", "idempotency_key"),
    [
        (
            datetime(2026, 10, 9, 15, 0, tzinfo=UTC),
            "2026-10-09T15:00:00Z",
            "occ_a8236fe352643eeab7100663",
            "a8236fe352643eeab7100663b5c84d29138e744c63a7bc40021a5348d23ce1d8",
        ),
        (
            datetime(2026, 10, 9, 15, 0, 0, 500000, tzinfo=UTC),
            "2026-10-09T15:00:00.5Z",
            "occ_fb1ce02420015cf34e07c0f8",
            "fb1ce02420015cf34e07c0f8e047a19c3ccc473df6ec8b24a2ae876e0cca32b4",
        ),
        (
            datetime(
                2026, 10, 9, 16, 30, 0, 123456, tzinfo=timezone(timedelta(hours=1))
            ),
            "2026-10-09T15:30:00.123456Z",
            "occ_bf76ffd51999b26265f2fb6e",
            "bf76ffd51999b26265f2fb6efea5cab448a7beeb164921b4a081dfa15214954a",
        ),
    ],
)
def test_identity_matches_zeo_runtime(
    due_at: datetime, key_time: str, occurrence_id: str, idempotency_key: str
) -> None:
    assert rfc3339_nano(due_at) == key_time
    assert runtime_identity(LOOP, due_at) == (occurrence_id, idempotency_key)


def test_authorization_must_bind_the_exact_job_bytes(tmp_path: Path) -> None:
    directory = make_job(tmp_path)
    JobDirectory(directory).authorized_job()
    raw = (directory / "job.json").read_bytes()
    (directory / "job.json").write_bytes(raw + b"\n")
    with pytest.raises(JobError, match="does not bind"):
        JobDirectory(directory).authorized_job()


def test_unauthorized_or_inconsistent_jobs_are_refused(tmp_path: Path) -> None:
    with pytest.raises(JobError, match="not authorized"):
        JobDirectory(make_job(tmp_path, authorize=False)).authorized_job()
    directory = make_job(tmp_path, job_number=2)
    job = json.loads((directory / "job.json").read_text())
    job["occurrence_id"] = "occ_000000000000000000000000"
    (directory / "job.json").write_text(json.dumps(job))
    with pytest.raises(JobError, match="Runtime's derivation"):
        JobDirectory(directory).job()


def test_events_append_once_and_fold(tmp_path: Path) -> None:
    jd = JobDirectory(make_job(tmp_path))
    jd.append(actor="studio", type="queued")
    jd.append(actor="executor", type="session_requested", step="video", attempt=1)
    jd.append(
        actor="executor",
        type="approval_required",
        step="video",
        approval_url="https://x/a",
    )
    jd.append(actor="executor", type="session_ready", step="video")
    jd.append(actor="executor", type="final_chunk_sent", step="video")
    jd.append(
        actor="executor",
        type="session_lost",
        step="video",
        next_attempt=2,
        final_chunk_sent=True,
    )
    state = jd.state()
    video = state.steps["video"]
    assert (video.attempt, video.final_chunk_sent, video.approval_url) == (
        2,
        True,
        None,
    )
    jd.append(actor="executor", type="held", reason="ambiguous_upload")
    assert jd.state().held == "ambiguous_upload"
    jd.append(actor="studio", type="released")
    released = jd.state()
    assert released.held is None and released.steps["video"].final_chunk_sent is False
    jd.write_provider_record(
        ProviderRecord(
            video_id="vid1",
            video_url="https://youtu.be/vid1",
            fetched_at=datetime.now(UTC),
        )
    )
    jd.append(actor="executor", type="uploaded", provider_record="youtube.json")
    assert jd.state().video_id == "vid1" and jd.state().uploaded
    events_text = "".join(p.read_text() for p in jd.events_dir.iterdir())
    assert "vid1" not in events_text, "no YouTube data in the write-once events"
    assert sorted(p.name for p in jd.events_dir.iterdir())[0] == "0000000001.json"


def test_receipt_is_written_once(tmp_path: Path) -> None:
    jd = JobDirectory(make_job(tmp_path))
    receipt = Receipt(
        schema_version=1,
        job_id="ytj_x",
        outcome="SUCCEEDED",
        provider_record="youtube.json",
        observed_at=datetime.now(UTC),
    )
    jd.write_receipt(receipt)
    assert jd.receipt() == receipt
    with pytest.raises(JobError, match="already exists"):
        jd.write_receipt(receipt)


def test_publish_at_needs_private_and_a_time_zone(tmp_path: Path) -> None:
    later = datetime.now(UTC) + timedelta(days=1)
    directory = make_job(tmp_path, privacy="public", publish_at=later)
    with pytest.raises(JobError, match="requires privacy"):
        JobDirectory(directory).job()
