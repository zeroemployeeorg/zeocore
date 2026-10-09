"""``publish close``: the studio's Close of a job held on a recorded outcome.

Asked for by ZBS, so the lock, the check and the event shape stay in zeocore
(Node has no flock). It is held for the E10 ruling. It sends nothing to
ZEOconnect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_integrations.google.youtube.fakes import FakeBroker, FakeYouTube
from tests.test_integrations.google.youtube.helpers import make_job
from tests.test_integrations.google.youtube.test_publish import _executor
from zeo_core.integrations.google.youtube.job import JobDirectory, cancel_reason
from zeo_core.integrations.google.youtube.links import InMemoryUploadLinkStore
from zeo_core.integrations.google.youtube.publish import (
    EXIT_DONE,
    EXIT_HELD,
    EXIT_INVALID,
    EXIT_WAIT,
    close_held_job,
    job_lock,
    main,
)

World = tuple[FakeYouTube, FakeBroker, InMemoryUploadLinkStore]


@pytest.fixture
def world() -> World:
    yt = FakeYouTube()
    return yt, FakeBroker(yt), InMemoryUploadLinkStore()


def _held(tmp_path: Path, world: World, operation: str, **job: object) -> Path:
    yt, broker, links = world
    broker.refuse.add(operation)
    directory = make_job(tmp_path, **job)  # type: ignore[arg-type]
    held = _executor(directory, yt, broker, links).run()
    assert held.exit_code == EXIT_HELD
    assert held.status["reason"] == "refused_in_zeoconnect"
    return directory


def test_close_writes_a_structured_reason_and_sends_nothing(
    tmp_path: Path, world: World
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, "youtube.video.upload_session.create")
    sent = len(broker.calls)
    closed = close_held_job(directory, "video")
    assert closed.exit_code == EXIT_DONE
    assert closed.status["reason"] == "cancelled:refused_in_zeoconnect:video"
    receipt = JobDirectory(directory).receipt()
    assert receipt is not None
    assert (receipt.outcome, receipt.reason, receipt.provider_record) == (
        "REFUSED",
        "cancelled:refused_in_zeoconnect:video",
        None,
    )
    # The executor reads it as cancelled, and a second close is the same.
    again = _executor(directory, yt, broker, links).run()
    assert again.status == {
        "job_id": closed.status["job_id"],
        "state": "cancelled",
        "reason": "cancelled:refused_in_zeoconnect:video",
    }
    assert close_held_job(directory, "video").status == closed.status
    assert len(broker.calls) == sent


def test_a_later_step_close_says_where_and_the_video_stays_recorded(
    tmp_path: Path, world: World
) -> None:
    directory = _held(
        tmp_path, world, "youtube.thumbnail.upload_session.create", thumbnail=True
    )
    closed = close_held_job(directory, "thumbnail")
    assert closed.status["reason"] == "cancelled:refused_in_zeoconnect:thumbnail"
    receipt = JobDirectory(directory).receipt()
    assert receipt is not None and receipt.provider_record is not None


@pytest.mark.parametrize(
    ("setup", "reason"),
    [("not_held", "not_held"), ("released", "not_held"), ("bad_step", "unknown_step")],
)
def test_close_refuses_what_it_must_not_close(
    tmp_path: Path, world: World, setup: str, reason: str
) -> None:
    if setup == "not_held":
        directory = make_job(tmp_path)
    else:
        directory = _held(tmp_path, world, "youtube.video.upload_session.create")
        if setup == "released":
            JobDirectory(directory).append(actor="studio", type="released")
    step = "thumbnail" if setup == "bad_step" else "video"
    refused = close_held_job(directory, step)
    assert refused.exit_code == EXIT_INVALID
    assert refused.status["reason"] == reason
    assert JobDirectory(directory).receipt() is None
    assert not JobDirectory(directory).state().cancelled


def test_close_is_busy_while_a_run_holds_the_lock(tmp_path: Path, world: World) -> None:
    directory = _held(tmp_path, world, "youtube.video.upload_session.create")
    with job_lock(directory):
        busy = close_held_job(directory, "video")
    assert busy.exit_code == EXIT_WAIT and busy.status["state"] == "busy"
    assert JobDirectory(directory).receipt() is None
    assert close_held_job(directory, "video").exit_code == EXIT_DONE


def test_a_kill_between_the_event_and_the_receipt_completes_the_same(
    tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, "youtube.video.upload_session.create")
    sent = len(broker.calls)

    def killed(_self: object, _receipt: object) -> None:
        raise KeyboardInterrupt("killed before the receipt")

    monkeypatch.setattr(JobDirectory, "write_receipt", killed)
    with pytest.raises(KeyboardInterrupt):
        close_held_job(directory, "video")
    monkeypatch.undo()
    assert JobDirectory(directory).state().cancelled
    # Either path finishes it the same way: a run, or the close again.
    finished = _executor(directory, yt, broker, links).run()
    assert finished.status["reason"] == "cancelled:refused_in_zeoconnect:video"
    assert close_held_job(directory, "video").status == finished.status
    assert len(broker.calls) == sent


def test_a_studio_value_that_fails_its_check_never_reaches_the_receipt(
    tmp_path: Path,
) -> None:
    directory = JobDirectory(make_job(tmp_path))
    directory.append(
        actor="studio", type="cancelled", closed_on="Bad Reason!", step="video"
    )
    assert cancel_reason(directory.job(), directory.state()) == "cancelled"
    other = JobDirectory(make_job(tmp_path, job_number=2))
    other.append(actor="studio", type="cancelled", closed_on="held", step="nope")
    assert cancel_reason(other.job(), other.state()) == "cancelled"


def test_the_close_command_prints_one_json_line(
    tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _held(tmp_path, world, "youtube.video.upload_session.create")
    code = main(["close", str(directory), "--step", "video", "--json"])
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == EXIT_DONE
    assert printed["state"] == "cancelled"
    assert printed["reason"] == "cancelled:refused_in_zeoconnect:video"
