"""PublishExecutor end to end against a fake YouTube and a fake ZEOconnect broker."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from zeo_core.integrations.google.youtube import publish
from zeo_core.integrations.google.youtube.job import JobDirectory
from zeo_core.integrations.google.youtube.links import InMemoryUploadLinkStore
from zeo_core.integrations.google.youtube.publish import (
    EXIT_APPROVAL,
    EXIT_DONE,
    EXIT_HELD,
    EXIT_INVALID,
    EXIT_WAIT,
    PublishExecutor,
    RunResult,
    main,
)
from zeo_core.integrations.google.youtube.transfer import CHUNK_UNIT
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedOperationRequest,
    HostedOperationResponse,
)
from zeo_core.integrations.hosted.pairing import (
    DeviceSession,
    InMemorySecureSessionStore,
    PairingChallenge,
    PairingPendingError,
)
from zeo_core.integrations.hosted.profile import (
    HostedConnectionStatus,
    HostedConnectionSummary,
    OpaqueConnectionHandle,
)

from .fakes import FakeBroker, FakeYouTube
from .helpers import make_job


def _executor(
    directory: Path,
    yt: FakeYouTube,
    broker: FakeBroker,
    links: InMemoryUploadLinkStore,
    **kw: object,
) -> PublishExecutor:
    return PublishExecutor(
        directory,
        broker=broker,
        links=links,
        http=yt,
        sleep=lambda _s: None,
        chunk_bytes=CHUNK_UNIT,
        approval_wait=0,
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def world() -> tuple[FakeYouTube, FakeBroker, InMemoryUploadLinkStore]:
    yt = FakeYouTube()
    return yt, FakeBroker(yt), InMemoryUploadLinkStore()


def test_happy_path_uploads_once_and_attaches_everything(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    later = datetime.now(UTC) + timedelta(days=1)
    directory = make_job(
        tmp_path,
        publish_at=later,
        thumbnail=True,
        captions=(("en", "English"),),
        playlist_id="PL123",
    )
    result = _executor(directory, yt, broker, links).run()
    assert result.exit_code == EXIT_DONE, result.status
    assert len(yt.videos) == 1
    video = next(iter(yt.videos.values()))
    job = JobDirectory(directory).job()
    assert video.content_sha256 == job.video.sha256
    assert (
        video.video_id in yt.thumbnails
        and yt.captions[video.video_id][0]["language"] == "en"
    )
    assert yt.playlists["PL123"] == [video.video_id]
    receipt = JobDirectory(directory).receipt()
    assert receipt is not None and receipt.outcome == "SUCCEEDED"
    assert receipt.video_url == f"https://youtu.be/{video.video_id}"
    assert not links.links, "upload links are deleted when their step completes"
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_DONE
    assert len(yt.videos) == 1, "running a finished job again changes nothing"


def test_waits_for_approval_and_surfaces_the_link(tmp_path: Path, world: tuple) -> None:
    yt, _broker, links = world
    broker = FakeBroker(yt, auto_approve=False)
    directory = make_job(tmp_path)
    first = _executor(directory, yt, broker, links).run()
    assert first.exit_code == EXIT_APPROVAL
    assert first.status["approval_url"].startswith("https://connect.example/approvals/")
    events = JobDirectory(directory).events()
    assert any(e.type == "approval_required" for e in events), (
        "the studio reads the link from events"
    )
    key = broker.calls[-1][1]
    broker.approve(key)
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_DONE
    assert len(yt.videos) == 1


def test_an_expired_approval_asks_again_under_a_new_key(
    tmp_path: Path, world: tuple
) -> None:
    yt, _broker, links = world
    broker = FakeBroker(yt, auto_approve=False)
    directory = make_job(tmp_path)
    clock = {"now": datetime.now(UTC)}

    def run() -> RunResult:
        return _executor(directory, yt, broker, links, clock=lambda: clock["now"]).run()

    assert run().exit_code == EXIT_APPROVAL
    first_key = broker.calls[-1][1]
    clock["now"] += timedelta(minutes=6)
    assert run().exit_code == EXIT_APPROVAL
    second_key = broker.calls[-1][1]
    assert first_key.endswith(":video:1") and second_key.endswith(":video:2")


def test_a_killed_run_resumes_without_a_second_session(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path, size=8 * CHUNK_UNIT + 5)
    stops = {"n": 0}

    def stop() -> bool:
        stops["n"] += 1
        return stops["n"] > 4

    paused = _executor(directory, yt, broker, links, should_stop=stop).run()
    assert paused.exit_code == EXIT_WAIT and paused.status["reason"] == "paused"
    assert len(yt.sessions) == 1 and not yt.videos
    yt.drop_after = 1000  # and a dropped line in the second run
    done = _executor(directory, yt, broker, links).run()
    assert done.exit_code == EXIT_DONE
    assert len(yt.sessions) == 1 and len(yt.videos) == 1


def test_a_session_that_expired_before_the_last_byte_is_replaced(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path, size=6 * CHUNK_UNIT)
    stops = {"n": 0}

    def stop() -> bool:
        stops["n"] += 1
        return stops["n"] > 3

    _executor(directory, yt, broker, links, should_stop=stop).run()
    yt.expire_all()
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_DONE
    assert len(yt.sessions) == 2 and len(yt.videos) == 1


def test_lost_final_answer_and_expired_session_adopts_the_upload(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path)
    yt.lose_final_answer = True
    original = yt.put

    def put_then_expire(url: str, **kw: object) -> object:
        try:
            return original(url, **kw)  # type: ignore[arg-type,unused-ignore]
        except Exception:
            yt.expire_all()  # the answer is lost and the session is gone
            raise

    yt.put = put_then_expire  # type: ignore[method-assign,assignment,unused-ignore]
    result = _executor(directory, yt, broker, links).run()
    assert result.exit_code == EXIT_DONE, result.status
    assert len(yt.videos) == 1 and len(yt.sessions) == 1
    uploaded = [e for e in JobDirectory(directory).events() if e.type == "uploaded"]
    assert uploaded[0].model_extra == {"video_id": "vid00000001", "reconciled": True}


def test_two_matching_uploads_hold_the_job(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path)
    jd = JobDirectory(directory)
    jd.append(actor="executor", type="session_requested", step="video", attempt=1)
    jd.append(actor="executor", type="final_chunk_sent", step="video")
    size = jd.job().video.size_bytes
    for _ in range(2):
        url = yt.open(
            "videos",
            size,
            {"metadata": {"title": "Agent skills"}, "status": {"privacy": "private"}},
        )
        yt.sessions[url].data.extend(b"\0" * size)
        yt.sessions[url].resource = yt._complete(yt.sessions[url])
    held = _executor(directory, yt, broker, links).run()
    assert held.exit_code == EXIT_HELD and held.status["reason"] == "ambiguous_upload"
    jd.append(actor="studio", type="released")
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_DONE
    assert len(yt.videos) == 3, "after the operator's release, a new session uploads"


def test_link_refused_without_a_token_holds_for_the_relay(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    yt.require_token = True
    held = _executor(make_job(tmp_path), yt, broker, links).run()
    assert (
        held.exit_code == EXIT_HELD and held.status["reason"] == "session_link_refused"
    )


def test_a_replayed_session_has_no_link_and_asks_again(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path)
    jd = JobDirectory(directory)
    key = jd.job().idempotency_key[:32] + ":video:1"
    broker.invoke(
        "youtube.video.upload_session.create", {"size_bytes": 1}, key
    )  # the lost answer
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_DONE
    assert [
        (e.model_extra or {}).get("next_attempt")
        for e in jd.events()
        if e.type == "session_lost"
    ] == [2]


def test_privacy_override_is_held_not_reported_done(
    tmp_path: Path, world: tuple
) -> None:
    yt, broker, links = world
    yt.force_privacy = "private"
    held = _executor(make_job(tmp_path, privacy="unlisted"), yt, broker, links).run()
    assert held.exit_code == EXIT_HELD and held.status["reason"] == "privacy_overridden"


def test_waits_while_youtube_processes(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path)

    def processing(_operation: str) -> None:
        for video in yt.videos.values():
            video.processing = "processing"

    broker.on_invoke = processing
    result = _executor(directory, yt, broker, links).run()
    assert (
        result.exit_code == EXIT_WAIT
        and result.status["reason"] == "youtube_processing"
    )
    broker.on_invoke = None
    for video in yt.videos.values():
        video.processing = "succeeded"
    assert _executor(directory, yt, broker, links).run().exit_code == EXIT_DONE


def test_not_due_unauthorized_held_and_offline(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    later = make_job(tmp_path, due_at=datetime.now(UTC) + timedelta(hours=1))
    assert _executor(later, yt, broker, links).run().status["reason"] == "not_due"
    assert (
        _executor(make_job(tmp_path, authorize=False, job_number=2), yt, broker, links)
        .run()
        .exit_code
        == EXIT_INVALID
    )
    held_dir = make_job(tmp_path, job_number=3)
    JobDirectory(held_dir).append(actor="executor", type="held", reason="file_changed")
    assert _executor(held_dir, yt, broker, links).run().exit_code == EXIT_HELD

    class Offline(FakeBroker):
        def invoke(self, *a: object, **k: object) -> HostedOperationResponse:  # type: ignore[override,unused-ignore]
            raise HostedClientError("hosted transport is unavailable")

    offline = _executor(make_job(tmp_path, job_number=4), yt, Offline(yt), links).run()
    assert (
        offline.exit_code == EXIT_WAIT
        and offline.status["reason"] == "zeoconnect_unavailable"
    )
    assert not yt.videos


def test_a_scheduled_time_that_passed_is_held(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    soon = datetime.now(UTC) + timedelta(minutes=3)
    held = _executor(make_job(tmp_path, publish_at=soon), yt, broker, links).run()
    assert (
        held.exit_code == EXIT_HELD and held.status["reason"] == "publish_time_passed"
    )
    assert not yt.sessions


def test_a_changed_video_is_held(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path)
    video = Path(JobDirectory(directory).job().video.path)
    data = bytearray(video.read_bytes())
    data[0] ^= 0xFF
    stat = video.stat()
    video.write_bytes(bytes(data))
    import os

    os.utime(
        video, ns=(stat.st_atime_ns, stat.st_mtime_ns)
    )  # same size and time, new bytes
    held = _executor(directory, yt, broker, links).run()
    assert held.exit_code == EXIT_HELD and held.status["reason"] == "file_changed"


def test_cancelled_jobs_close_refused(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    directory = make_job(tmp_path)
    JobDirectory(directory).append(actor="studio", type="cancelled")
    result = _executor(directory, yt, broker, links).run()
    assert result.exit_code == EXIT_HELD and result.status["state"] == "refused"
    assert not yt.sessions


def test_refused_in_zeoconnect_holds(tmp_path: Path, world: tuple) -> None:
    yt, broker, links = world
    broker.refuse.add("youtube.video.upload_session.create")
    held = _executor(make_job(tmp_path), yt, broker, links).run()
    assert held.status["reason"] == "refused_in_zeoconnect"


def test_status_cli_prints_the_fold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = make_job(tmp_path)
    JobDirectory(directory).append(actor="executor", type="uploaded", video_id="vidX")
    assert main(["status", str(directory)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["video_id"] == "vidX" and printed["done"] is False
    assert (
        main(["run", str(make_job(tmp_path, authorize=False, job_number=2))])
        == EXIT_INVALID
    )


class _FakeTransport:
    """Stands in for ZEOconnectHTTPTransport in the CLI: pairing, listing, invoke."""

    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker
        self.closed = False
        self.polls = 0

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        return self.broker.invoke(
            request.operation_id, dict(request.arguments), request.idempotency_key
        )

    def begin_pairing(self, *, device_name: str) -> PairingChallenge:
        return PairingChallenge(
            pairing_id="p1",
            device_code=SecretStr("code"),
            verification_url="https://connect.example/pair",
            user_code="ABCD-1234",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            polling_interval_seconds=1,
        )

    def poll_pairing(self, challenge: PairingChallenge) -> DeviceSession:
        self.polls += 1
        if self.polls == 1:
            raise PairingPendingError("pending")
        now = datetime.now(UTC)
        return DeviceSession(
            device_id="dev1",
            access_token=SecretStr("a"),
            refresh_token=SecretStr("r"),
            access_expires_at=now + timedelta(hours=1),
            refresh_expires_at=now + timedelta(days=30),
        )

    def list_connections(
        self, session: DeviceSession
    ) -> tuple[HostedConnectionSummary, ...]:
        del session
        return (
            HostedConnectionSummary(
                handle=OpaqueConnectionHandle(value="con_youtube0001"),
                service="youtube",
                external_identity="@rasahq",
                status=HostedConnectionStatus.ACTIVE,
                operations=("youtube.channel.get",),
            ),
            HostedConnectionSummary(
                handle=OpaqueConnectionHandle(value="con_drive000001"),
                service="google.drive",
                external_identity="me@example.org",
                status=HostedConnectionStatus.ACTIVE,
                operations=("google.drive.file.download",),
            ),
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cli(
    monkeypatch: pytest.MonkeyPatch, world: tuple
) -> tuple[FakeYouTube, _FakeTransport]:
    yt, broker, links = world
    transport = _FakeTransport(broker)
    store = InMemorySecureSessionStore()
    monkeypatch.setattr(publish, "_transport", lambda: (transport, store))
    monkeypatch.setattr(publish, "_link_store", lambda _choice, _dir: links)
    monkeypatch.setattr(publish, "_byte_http", lambda: yt)
    monkeypatch.setattr(publish.time, "sleep", lambda _s: None)
    return yt, transport


def test_cli_run_publishes_through_the_paired_transport(
    tmp_path: Path, cli: tuple, capsys: pytest.CaptureFixture[str]
) -> None:
    yt, transport = cli
    directory = make_job(tmp_path)
    assert main(["run", str(directory), "--json", "--chunk-mib", "1"]) == EXIT_DONE
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "done" and status["video_url"].startswith(
        "https://youtu.be/"
    )
    assert len(yt.videos) == 1 and transport.closed


def test_cli_pair_and_connections_list_only_youtube(
    cli: tuple, capsys: pytest.CaptureFixture[str]
) -> None:
    _yt, transport = cli
    assert main(["pair", "--device-name", "Studio"]) == EXIT_DONE
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["user_code"] == "ABCD-1234" and lines[0]["verification_url"]
    assert lines[1]["state"] == "paired"
    assert [c["connection_id"] for c in lines[1]["connections"]] == ["con_youtube0001"]
    assert main(["connections"]) == EXIT_DONE
    listed = json.loads(capsys.readouterr().out)
    assert listed["connections"][0]["channel"] == "@rasahq" and transport.closed


def test_cli_rejects_bad_chunk_size() -> None:
    with pytest.raises(SystemExit):
        main(["run", "x", "--chunk-mib", "0"])
