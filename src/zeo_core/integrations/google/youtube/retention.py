"""Keep YouTube data within the 30-day limit: refresh it, or delete it.

The YouTube API Services Developer Policies (§III.E.4) let a client store authorized
data for at most 30 calendar days unless it is refreshed. The adviser's note r14
(org-zeroemployeeorg#735 6045087532) settled ZBS's rule:

- every job's ``youtube.json`` (the only file holding YouTube data) is refreshed with
  one ``youtube.video.get`` read (1 quota unit) once it is ``refresh_after`` old;
- if the video is gone or the channel's access has lapsed, the record is deleted, and
  the job's events say when and why;
- if ZEOconnect can't be reached, the refresh is deferred, but a record that reaches
  ``max_age`` is deleted anyway, so nothing outlives the limit;
- the job's own record (what was sent, the operator's authorization, the outcome) is
  the studio's data and stays.

Run it daily: ``python -m zeo_core.integrations.google.youtube.publish retain <root>``.
A job that an executor is running right now is skipped; that run refreshes it itself.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from zeo_core.integrations.google.youtube.job import (
    JobDirectory,
    JobError,
    ProviderRecord,
)
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedOperationStatus,
    HostedStoppedError,
    HostedUnavailableError,
    is_outage,
    stop_of,
)

if TYPE_CHECKING:
    from zeo_core.integrations.google.youtube.publish import YouTubeBroker

#: Refresh once a record is this old: well inside 30 days, so failed days are fine.
REFRESH_AFTER = timedelta(days=20)
#: Delete a record that couldn't be refreshed by this age.
MAX_AGE = timedelta(days=29)


@dataclass
class RetentionReport:
    checked: int = 0
    refreshed: int = 0
    deferred: int = 0
    skipped_busy: int = 0
    dropped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "state": "retained",
            "checked": self.checked,
            "refreshed": self.refreshed,
            "deferred": self.deferred,
            "skipped_busy": self.skipped_busy,
            "dropped": self.dropped,
        }


class RetentionSweep:
    """Apply the 30-day rule to every job under ``<publish-root>/jobs``."""

    def __init__(
        self,
        publish_root: Path,
        *,
        broker_for: Callable[[str], YouTubeBroker],
        clock: Callable[[], datetime] | None = None,
        refresh_after: timedelta = REFRESH_AFTER,
        max_age: timedelta = MAX_AGE,
    ) -> None:
        self._root = Path(publish_root)
        self._broker_for = broker_for
        self._clock = clock or (lambda: datetime.now(UTC))
        self._refresh_after = refresh_after
        self._max_age = max_age

    def run(self) -> RetentionReport:
        report = RetentionReport()
        jobs = self._root / "jobs"
        for path in sorted(jobs.iterdir()) if jobs.is_dir() else []:
            if path.is_dir() and path.name.startswith("ytj_"):
                self._one(JobDirectory(path), report)
        return report

    def _one(self, directory: JobDirectory, report: RetentionReport) -> None:
        try:
            record = directory.provider_record()
        except ValueError:
            directory.drop_provider_record("unreadable record")
            report.dropped.append(
                {"job_id": directory.path.name, "reason": "unreadable"}
            )
            return
        if record is None:
            return
        report.checked += 1
        age = self._clock() - record.fetched_at
        if age < self._refresh_after:
            return
        descriptor = os.open(directory.path / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report.skipped_busy += 1
                return
            self._refresh(directory, record, age, report)
        finally:
            os.close(descriptor)

    def _refresh(
        self,
        directory: JobDirectory,
        record: ProviderRecord,
        age: timedelta,
        report: RetentionReport,
    ) -> None:
        try:
            job = directory.job()
            broker = self._broker_for(job.destination.connection_id)
            response = broker.invoke(
                "youtube.video.get",
                {"video_id": record.video_id},
                f"{job.idempotency_key[:32]}:retain:{time.time_ns()}",
            )
        except (HostedClientError, JobError) as error:
            # A stop or an outage is temporary: defer. Only a refusal means
            # access has lapsed.
            refused = "refused" in str(error) and not isinstance(
                error, (HostedStoppedError, HostedUnavailableError)
            )
            if refused or age >= self._max_age:
                reason = (
                    "access lapsed (ZEOconnect refused the read)"
                    if refused
                    else f"not refreshed within {self._max_age.days} days ({error})"
                )
                self._drop(directory, reason, report)
            else:
                report.deferred += 1
            return
        if response.status is HostedOperationStatus.CONFIRMED and isinstance(
            response.result, dict
        ):
            result = response.result
            directory.write_provider_record(
                record.model_copy(
                    update={
                        "privacy": result.get("privacy"),
                        "publish_at": result.get("publish_at"),
                        "fetched_at": self._clock(),
                    }
                )
            )
            report.refreshed += 1
            return
        if stop_of(response) is not None or is_outage(response):
            # An orchestrated stop or outage is temporary, like a 503.
            if age >= self._max_age:
                reason = f"not refreshed within {self._max_age.days} days (held)"
                self._drop(directory, reason, report)
            else:
                report.deferred += 1
            return
        message = response.normalized_error.message if response.normalized_error else ""
        self._drop(
            directory,
            f"video gone or access lapsed ({response.status.value} {message})".strip(),
            report,
        )

    @staticmethod
    def _drop(directory: JobDirectory, reason: str, report: RetentionReport) -> None:
        directory.drop_provider_record(reason)
        report.dropped.append({"job_id": directory.path.name, "reason": reason})


__all__ = ["MAX_AGE", "REFRESH_AFTER", "RetentionReport", "RetentionSweep"]
