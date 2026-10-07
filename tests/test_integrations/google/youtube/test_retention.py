"""The 30-day rule: refresh YouTube data, delete it on failure, keep the job."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zeo_core.integrations.google.youtube.job import JobDirectory, ProviderRecord
from zeo_core.integrations.google.youtube.publish import main
from zeo_core.integrations.google.youtube.retention import RetentionSweep
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedOperationResponse,
    HostedOperationStatus,
)

from .helpers import make_job

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class _Broker:
    def __init__(self, answer: HostedOperationResponse | Exception) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def invoke(
        self, operation_id: str, arguments: dict[str, object], idempotency_key: str
    ) -> HostedOperationResponse:
        self.calls.append(operation_id)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _ok(privacy: str = "public") -> HostedOperationResponse:
    return HostedOperationResponse(
        status=HostedOperationStatus.CONFIRMED,
        execution_id="obs-1",
        result={"video_id": "vid1", "privacy": privacy, "publish_at": None},
    )


def _published(tmp_path: Path, days_old: int, number: int = 1) -> JobDirectory:
    jd = JobDirectory(make_job(tmp_path, job_number=number))
    jd.write_provider_record(
        ProviderRecord(
            video_id="vid1",
            privacy="private",
            video_url="https://youtu.be/vid1",
            fetched_at=NOW - timedelta(days=days_old),
        )
    )
    jd.append(actor="executor", type="uploaded", provider_record="youtube.json")
    return jd


def _sweep(tmp_path: Path, broker: _Broker) -> RetentionSweep:
    return RetentionSweep(tmp_path, broker_for=lambda _c: broker, clock=lambda: NOW)


def test_young_records_are_left_alone(tmp_path: Path) -> None:
    jd = _published(tmp_path, days_old=5)
    broker = _Broker(_ok())
    report = _sweep(tmp_path, broker).run()
    assert (report.checked, report.refreshed, broker.calls) == (1, 0, [])
    assert jd.provider_record() is not None


def test_old_records_are_refreshed_with_one_read(tmp_path: Path) -> None:
    jd = _published(tmp_path, days_old=21)
    broker = _Broker(_ok("public"))
    report = _sweep(tmp_path, broker).run()
    assert report.refreshed == 1 and broker.calls == ["youtube.video.get"]
    record = jd.provider_record()
    assert (
        record is not None and record.fetched_at == NOW and record.privacy == "public"
    )


@pytest.mark.parametrize(
    "answer",
    [
        HostedOperationResponse(
            status=HostedOperationStatus.FAILED_SAFE, execution_id="x"
        ),
        HostedClientError("hosted request was refused"),
    ],
)
def test_a_gone_video_or_lapsed_access_deletes_the_youtube_fields_only(
    tmp_path: Path, answer: HostedOperationResponse | Exception
) -> None:
    jd = _published(tmp_path, days_old=21)
    report = _sweep(tmp_path, _Broker(answer)).run()
    assert len(report.dropped) == 1
    assert jd.provider_record() is None and not (jd.path / "youtube.json").exists()
    state = jd.state()
    assert state.uploaded and state.video_id is None and state.provider_dropped
    assert (jd.path / "job.json").exists(), "the job's own record stays"


def test_unreachable_defers_until_the_limit_then_deletes(tmp_path: Path) -> None:
    young = _published(tmp_path, days_old=22, number=1)
    old = _published(tmp_path, days_old=29, number=2)
    report = _sweep(
        tmp_path, _Broker(HostedClientError("hosted transport is unavailable"))
    ).run()
    assert report.deferred == 1 and young.provider_record() is not None
    assert old.provider_record() is None
    assert "not refreshed within 29 days" in report.dropped[0]["reason"]


def test_a_running_job_is_skipped(tmp_path: Path) -> None:
    jd = _published(tmp_path, days_old=25)
    descriptor = os.open(jd.path / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        report = _sweep(tmp_path, _Broker(_ok())).run()
    finally:
        os.close(descriptor)
    assert report.skipped_busy == 1 and jd.provider_record() is not None


def test_retain_cli_reports_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from zeo_core.integrations.google.youtube import publish

    _published(tmp_path, days_old=1)

    class _Transport:
        def close(self) -> None:
            pass

    monkeypatch.setattr(publish, "_transport", lambda: (_Transport(), None))
    assert main(["retain", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 1
