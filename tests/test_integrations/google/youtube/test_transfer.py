"""ResumableTransfer against a fake upload server that keeps real byte offsets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from zeo_core.integrations.google.youtube.transfer import (
    CHUNK_UNIT,
    FileIdentity,
    ResumableTransfer,
    TransferError,
    TransferState,
)

from .fakes import FakeYouTube
from .helpers import write_file

SIZE = 5 * CHUNK_UNIT + 777


def _identity(path: Path) -> FileIdentity:
    return FileIdentity(
        path, os.stat(path).st_size, os.stat(path).st_mtime_ns // 1_000_000
    )


@pytest.fixture
def video(tmp_path: Path) -> Path:
    return write_file(tmp_path / "take.mp4", SIZE)


def _transfer(yt: FakeYouTube, url: str, path: Path, **kw: object) -> ResumableTransfer:
    return ResumableTransfer(
        url=url,
        file=_identity(path),
        mime_type="video/mp4",
        http=yt,
        chunk_bytes=2 * CHUNK_UNIT,
        sleep=kw.pop("sleep", lambda _s: None),  # type: ignore[arg-type]
        jitter=lambda: 0.0,
        **kw,  # type: ignore[arg-type]
    )


def test_completes_in_chunks_after_probing_first(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    seen: list[int] = []
    finals: list[bool] = []
    out = _transfer(
        yt,
        url,
        video,
        on_progress=lambda r, _s: seen.append(r),
        before_final_chunk=lambda: finals.append(True),
    ).run()
    assert out.state is TransferState.COMPLETED and out.resource is not None
    assert out.resource["id"] == "vid00000001"
    assert yt.sessions[url].data == bytearray(video.read_bytes())
    assert yt.puts[0][1] == f"bytes */{SIZE}", "every attempt starts with a probe"
    assert len(yt.puts) > 1 and finals == [True]
    assert seen[-1] == SIZE


def test_a_dropped_connection_resumes_at_the_stored_byte(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    yt.drop_after = (
        CHUNK_UNIT + 1000
    )  # part of the first chunk lands, then the line drops
    sleeps: list[float] = []
    out = _transfer(yt, url, video, sleep=sleeps.append).run()
    assert out.state is TransferState.COMPLETED
    assert yt.sessions[url].data == bytearray(video.read_bytes())
    assert len(yt.videos) == 1
    assert sleeps == [1.0 * 0.5], "one backoff, then probe and resume"
    resumed = [r for _u, r in yt.puts if r.startswith(f"bytes {CHUNK_UNIT + 1000}-")]
    assert resumed, "the next chunk starts exactly where YouTube's Range ended"


def test_a_new_process_resumes_the_same_session(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    calls = {"n": 0}

    def stop_after_two() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    first = _transfer(yt, url, video, should_stop=stop_after_two).run()
    assert first.state is TransferState.PAUSED and 0 < first.received < SIZE
    second = _transfer(
        yt, url, video
    ).run()  # "after a reboot": a fresh object, same link
    assert second.state is TransferState.COMPLETED
    assert len(yt.videos) == 1 and yt.sessions[url].data == bytearray(
        video.read_bytes()
    )


def test_server_errors_back_off_and_continue(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    yt.fail_statuses = [503, 429, 500, 500, 500, 500, 500, 500]
    sleeps: list[float] = []
    out = _transfer(yt, url, video, sleep=sleeps.append).run()
    assert out.state is TransferState.COMPLETED
    assert sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0], (
        "capped at 60 s, halved by zero jitter"
    )


def test_a_lost_final_answer_is_resolved_by_the_probe(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    yt.lose_final_answer = True
    out = _transfer(yt, url, video).run()
    assert out.state is TransferState.COMPLETED and out.resource == {
        "id": "vid00000001",
        "status": {"privacyStatus": "private"},
    }
    assert len(yt.videos) == 1


@pytest.mark.parametrize(
    ("setup", "state"),
    [
        (
            lambda yt, url: setattr(yt.sessions[url], "expired", True),
            TransferState.EXPIRED,
        ),
        (lambda yt, url: setattr(yt, "require_token", True), TransferState.REFUSED),
    ],
)
def test_expired_and_refused_links_end_the_transfer(
    video: Path, setup: object, state: TransferState
) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    setup(yt, url)  # type: ignore[operator]
    out = _transfer(yt, url, video).run()
    assert out.state is state and not yt.videos


def test_a_changed_file_stops_before_sending(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    transfer = _transfer(yt, url, video)
    with open(video, "ab") as handle:
        handle.write(b"more")
    assert transfer.run().state is TransferState.FILE_CHANGED
    assert not yt.puts


def test_refuses_foreign_links_and_bad_chunks(video: Path) -> None:
    yt = FakeYouTube()
    with pytest.raises(TransferError, match="outside"):
        _transfer(yt, "https://evil.example/upload", video)
    with pytest.raises(TransferError, match="256 KiB"):
        ResumableTransfer(
            url="https://www.googleapis.com/upload/youtube/v3/videos?x",
            file=_identity(video),
            mime_type="video/mp4",
            http=yt,
            chunk_bytes=1000,
        )


def test_probe_reports_stored_bytes(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    transfer = _transfer(yt, url, video)
    assert transfer.probe() == 0
    yt.sessions[url].data.extend(b"\0" * 10)
    assert transfer.probe() == 10


def test_a_long_outage_ends_the_run_without_losing_bytes(video: Path) -> None:
    yt = FakeYouTube()
    url = yt.open(
        "videos", SIZE, {"metadata": {"title": "t"}, "status": {"privacy": "private"}}
    )
    yt.fail_statuses = [503] * 25
    out = _transfer(yt, url, video, max_failures=20).run()
    assert out.state is TransferState.PAUSED and out.detail.startswith("unreachable")
    yt.fail_statuses = []
    assert _transfer(yt, url, video).run().state is TransferState.COMPLETED
    assert len(yt.videos) == 1
