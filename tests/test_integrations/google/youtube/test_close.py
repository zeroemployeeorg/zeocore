"""``publish close``: the studio's Close of a job held on a recorded outcome.

Asked for by ZBS, so the lock, the checks and the event shape stay in zeocore
(Node has no flock). It waits for the E10 ruling and sends nothing to
ZEOconnect. The close is bound to the exact hold the person saw: its
``held_seq``, and the step and attempt that hold recorded (ZEO-RT SOW-99).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_integrations.google.youtube.fakes import FakeBroker, FakeYouTube
from tests.test_integrations.google.youtube.helpers import make_job
from tests.test_integrations.google.youtube.test_publish import _executor
from zeo_core.integrations.google.youtube.job import Event, JobDirectory
from zeo_core.integrations.google.youtube.links import InMemoryUploadLinkStore
from zeo_core.integrations.google.youtube.publish import (
    EXIT_DONE,
    EXIT_HELD,
    EXIT_INVALID,
    EXIT_WAIT,
    RunResult,
    close_held_job,
    job_lock,
    main,
)

World = tuple[FakeYouTube, FakeBroker, InMemoryUploadLinkStore]
VIDEO = "youtube.video.upload_session.create"


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


def _hold(directory: Path) -> Event:
    return [e for e in JobDirectory(directory).events() if e.type == "held"][-1]


def _close(
    directory: Path, seq: int | None = None, step: str | None = None
) -> RunResult:
    hold = _hold(directory)
    extra = hold.model_extra or {}
    return close_held_job(
        directory,
        hold.seq if seq is None else seq,
        str(extra.get("step")) if step is None else step,
    )


def _close_event(directory: Path) -> Event:
    (event,) = [e for e in JobDirectory(directory).events() if e.type == "cancelled"]
    return event


def test_a_recorded_hold_names_its_own_step_and_attempt(
    tmp_path: Path, world: World
) -> None:
    directory = _held(tmp_path, world, VIDEO)
    extra = _hold(directory).model_extra or {}
    assert (extra["step"], extra["attempt"]) == ("video", 1)


def test_close_binds_the_exact_hold_keeps_the_receipt_plain_and_sends_nothing(
    tmp_path: Path, world: World
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, VIDEO)
    hold = _hold(directory)
    sent = len(broker.calls)
    closed = _close(directory)
    assert closed.exit_code == EXIT_DONE
    assert closed.status["reason"] == "cancelled"
    assert (closed.status["held_seq"], closed.status["step"]) == (hold.seq, "video")
    # Old readers match "cancelled" exactly, so the receipt stays plain
    # (ZEO-RT SOW-93). The detail lives in the journal, bound to the hold.
    receipt = JobDirectory(directory).receipt()
    assert receipt is not None
    assert (receipt.outcome, receipt.reason) == ("REFUSED", "cancelled")
    close = _close_event(directory)
    assert close.actor == "studio"
    assert close.model_extra == {
        "closed_on": "refused_in_zeoconnect",
        "held_seq": hold.seq,
        "step": "video",
        "attempt": 1,
    }
    again = _executor(directory, yt, broker, links).run()
    assert again.status["state"] == "cancelled"
    assert _close(directory).status == closed.status
    assert len(broker.calls) == sent


@pytest.mark.parametrize(
    ("operation", "job", "step"),
    [
        ("youtube.thumbnail.upload_session.create", {"thumbnail": True}, "thumbnail"),
        (
            "youtube.caption.upload_session.create",
            {"captions": (("en", "English: full"),)},
            "caption:en:English: full",
        ),
    ],
)
def test_a_later_step_close_takes_the_step_from_the_hold(
    tmp_path: Path, world: World, operation: str, job: dict[str, object], step: str
) -> None:
    directory = _held(tmp_path, world, operation, **job)
    _close(directory)
    assert (_close_event(directory).model_extra or {})["step"] == step
    # Whether a video exists is read from the durable journal (uploaded),
    # never from youtube.json, which retention may delete.
    assert "uploaded" in [e.type for e in JobDirectory(directory).events()]


def test_a_video_youtube_rejected_is_closed_on_the_video_step_sending_nothing(
    tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Release only rereads the same rejected video and holds again (studio
    # #120), so close is the way out. The upload happened; nothing is resent.
    yt, broker, links = world
    complete = yt._complete

    def rejected(session: object) -> dict[str, object]:
        resource = complete(session)  # type: ignore[arg-type]
        for video in yt.videos.values():
            video.upload = "rejected"
        return resource

    monkeypatch.setattr(yt, "_complete", rejected)
    directory = make_job(tmp_path)
    held = _executor(directory, yt, broker, links).run()
    assert (held.exit_code, held.status["reason"]) == (EXIT_HELD, "youtube_rejected")
    extra = _hold(directory).model_extra or {}
    assert (extra["step"], extra["attempt"]) == ("video", 1)
    sent = len(broker.calls)
    closed = _close(directory)
    assert closed.exit_code == EXIT_DONE
    assert (_close_event(directory).model_extra or {})[
        "closed_on"
    ] == "youtube_rejected"
    assert "uploaded" in [e.type for e in JobDirectory(directory).events()]
    assert len(broker.calls) == sent


def test_a_stale_close_after_release_and_a_new_hold_is_refused(
    tmp_path: Path, world: World
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, VIDEO)
    seen = _hold(directory).seq
    # Another actor releases it, the next run holds again: a different hold.
    JobDirectory(directory).append(actor="studio", type="released")
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_HELD
    assert _hold(directory).seq != seen
    stale = _close(directory, seq=seen)
    assert stale.exit_code == EXIT_INVALID
    assert stale.status["reason"] == "hold_changed"
    assert JobDirectory(directory).receipt() is None
    assert _close(directory).exit_code == EXIT_DONE


@pytest.mark.parametrize(
    "setup", ["not_held", "released", "legacy", "local_hold", "ambiguous_upload"]
)
def test_close_refuses_what_it_must_not_close(
    tmp_path: Path, world: World, setup: str
) -> None:
    if setup == "not_held":
        directory, seq, reason = make_job(tmp_path), 1, "not_held"
    elif setup == "released":
        directory = _held(tmp_path, world, VIDEO)
        seq = _hold(directory).seq
        JobDirectory(directory).append(actor="studio", type="released")
        reason = "not_held"
    elif setup == "legacy":
        # A recorded-outcome hold from an older journal, with no typed step.
        # Close never guesses its step.
        directory = make_job(tmp_path)
        held = JobDirectory(directory).append(
            actor="executor",
            type="held",
            reason="refused_in_zeoconnect",
            detail="video: something",
        )
        seq, reason = held.seq, "hold_step_unknown"
    else:
        # A hold zeocore raised itself is never closed, even with a typed step:
        # an ambiguous upload or a changed file is released after a check.
        directory = make_job(tmp_path)
        held = JobDirectory(directory).append(
            actor="executor",
            type="held",
            reason="file_changed" if setup == "local_hold" else "ambiguous_upload",
            detail="2 uploads match; check the channel, then release",
            step="video",
            attempt=1,
        )
        seq, reason = held.seq, "hold_not_closeable"
    refused = close_held_job(directory, seq, "video")
    assert refused.exit_code == EXIT_INVALID
    assert refused.status["reason"] == reason
    assert JobDirectory(directory).receipt() is None
    assert not JobDirectory(directory).state().cancelled


def test_close_is_busy_while_a_run_holds_the_lock(tmp_path: Path, world: World) -> None:
    directory = _held(tmp_path, world, VIDEO)
    with job_lock(directory):
        busy = _close(directory)
    assert busy.exit_code == EXIT_WAIT and busy.status["state"] == "busy"
    assert JobDirectory(directory).receipt() is None
    assert _close(directory).exit_code == EXIT_DONE


def test_a_run_is_busy_while_a_close_holds_the_lock(
    tmp_path: Path, world: World
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, VIDEO)
    sent = len(broker.calls)
    with job_lock(directory):
        busy = _executor(directory, yt, broker, links).run()
    assert busy.exit_code == EXIT_WAIT and busy.status["state"] == "busy"
    assert len(broker.calls) == sent


def test_a_kill_between_the_event_and_the_receipt_completes_the_same(
    tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, VIDEO)
    seq = _hold(directory).seq
    sent = len(broker.calls)

    def killed(_self: object, _receipt: object) -> None:
        raise KeyboardInterrupt("killed before the receipt")

    monkeypatch.setattr(JobDirectory, "write_receipt", killed)
    with pytest.raises(KeyboardInterrupt):
        _close(directory)
    monkeypatch.undo()
    assert JobDirectory(directory).state().cancelled
    # Either path finishes it the same way: a run, or the close again.
    finished = _executor(directory, yt, broker, links).run()
    assert finished.status["reason"] == "cancelled"
    assert _close(directory, seq=seq, step="video").exit_code == EXIT_DONE
    assert len(broker.calls) == sent


def test_the_close_command_prints_one_json_line(
    tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _held(tmp_path, world, VIDEO)
    seq = _hold(directory).seq
    code = main(
        ["close", str(directory), "--expect-held-seq", str(seq), "--step", "video"]
    )
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == EXIT_DONE
    assert (printed["state"], printed["reason"]) == ("cancelled", "cancelled")


def test_an_existing_but_wrong_step_is_refused(tmp_path: Path, world: World) -> None:
    # A video + thumbnail job held on video: naming thumbnail must not close it.
    directory = _held(tmp_path, world, VIDEO, thumbnail=True)
    refused = _close(directory, step="thumbnail")
    assert refused.status["reason"] == "step_mismatch"
    assert not JobDirectory(directory).state().cancelled
    assert JobDirectory(directory).receipt() is None


@pytest.mark.parametrize("interrupted", [False, True])
def test_a_repeat_replays_only_the_exact_original_close(
    tmp_path: Path,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    interrupted: bool,
) -> None:
    yt, broker, links = world
    directory = _held(tmp_path, world, VIDEO)
    seq = _hold(directory).seq
    if interrupted:

        def killed(_self: object, _receipt: object) -> None:
            raise KeyboardInterrupt("killed before the receipt")

        monkeypatch.setattr(JobDirectory, "write_receipt", killed)
        with pytest.raises(KeyboardInterrupt):
            _close(directory)
        monkeypatch.undo()
    else:
        assert _close(directory).exit_code == EXIT_DONE
    events = len(JobDirectory(directory).events())
    for wrong in ((seq + 1, "video"), (seq, "thumbnail")):
        other = close_held_job(directory, *wrong)
        assert other.exit_code == EXIT_INVALID
        assert other.status["reason"] == "already_closed"
        assert (other.status["closed_held_seq"], other.status["closed_step"]) == (
            seq,
            "video",
        )
    same = close_held_job(directory, seq, "video")
    assert same.exit_code == EXIT_DONE
    assert (same.status["held_seq"], same.status["step"]) == (seq, "video")
    # Nothing was appended by the repeats, and nothing was sent.
    assert len(JobDirectory(directory).events()) == events
    receipt = JobDirectory(directory).receipt()
    assert receipt is not None and receipt.reason == "cancelled"
