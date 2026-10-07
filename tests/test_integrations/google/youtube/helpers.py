"""Build authorized job directories for the executor tests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from zeo_core.integrations.google.youtube.job import loop_id_for, runtime_identity


def write_file(path: Path, size: int, seed: int = 7) -> Path:
    block = bytes((seed + i) % 251 for i in range(4096))
    with open(path, "wb") as handle:
        remaining = size
        while remaining:
            handle.write(block[: min(remaining, len(block))])
            remaining -= min(remaining, len(block))
    return path


def media(path: Path, mime: str, **extra: object) -> dict[str, Any]:
    data = path.read_bytes()
    out = {
        "path": str(path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime_type": mime,
        **extra,
    }
    return out


def make_job(
    root: Path,
    *,
    size: int = 3 * 256 * 1024 + 1234,
    privacy: str = "private",
    publish_at: datetime | None = None,
    due_at: datetime | None = None,
    thumbnail: bool = False,
    captions: tuple[tuple[str, str], ...] = (),
    playlist_id: str | None = None,
    authorize: bool = True,
    job_number: int = 1,
) -> Path:
    job_id = "ytj_" + f"{job_number:024x}"
    directory = root / "jobs" / job_id
    directory.mkdir(parents=True)
    files = root / "files"
    files.mkdir(exist_ok=True)
    video = write_file(files / f"take-{job_number}.mp4", size)
    stat = os.stat(video)
    now = datetime.now(UTC)
    due = due_at or now - timedelta(minutes=1)
    loop_id = loop_id_for(job_id)
    occurrence_id, key = runtime_identity(loop_id, due)
    video_media = media(video, "video/mp4", mtime_ms=stat.st_mtime_ns // 1_000_000)
    job: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "organization_id": "org_local000000000000",
        "project_id": "zbs",
        "loop_id": loop_id,
        "occurrence_id": occurrence_id,
        "due_at": due.isoformat(),
        "idempotency_key": key,
        "effect_class": "PUBLICATION",
        "operation": "youtube.video.create",
        "destination": {"channel": "@rasahq", "connection_id": "con_youtube0001"},
        "artifact_digest": "sha256:" + video_media["sha256"],
        "video": video_media,
        "metadata": {
            "title": "Agent skills",
            "description": "One job each.",
            "tags": ["rasa"],
        },
        "status": {
            "privacy": privacy,
            "publish_at": publish_at.isoformat() if publish_at else None,
            "made_for_kids": False,
        },
        "notify_subscribers": True,
        "thumbnail": None,
        "captions": [],
        "playlist_id": playlist_id,
        "source": {"episode": "agent-skills", "take": "take-1"},
        "created_at": (now - timedelta(minutes=2)).isoformat(),
    }
    if thumbnail:
        thumb = write_file(files / f"thumb-{job_number}.png", 5000, seed=3)
        job["thumbnail"] = media(thumb, "image/png")
    for language, name in captions:
        srt = files / f"{language}-{job_number}.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        job["captions"].append(
            media(srt, "application/x-subrip", language=language, name=name)
        )
    raw = json.dumps(job, sort_keys=True).encode()
    (directory / "job.json").write_bytes(raw)
    if authorize:
        (directory / "authorization.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": job_id,
                    "trust_root": "OPERATOR_DEVICE_EVENT",
                    "authorized_at": now.isoformat(),
                    "job_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        )
    return directory
