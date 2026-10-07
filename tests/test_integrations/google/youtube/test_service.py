"""GoogleYouTubeService against a mocked Data API v3 client (the SDK boundary, never the
service's own methods).

No network and no token: the credential source and the client factory are injected, as a
custody boundary injects them in production.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zeo_core.integrations.google.youtube import (
    CaptionUpload,
    GoogleYouTubeService,
    VideoMetadata,
    VideoStatus,
    VideoUpload,
)


class _Source:
    def get_credentials(self) -> object:
        return object()


class _Factory:
    def __init__(self, client: MagicMock) -> None:
        self.client = client
        self.calls: list[tuple[str, str]] = []

    def build(self, service: str, version: str, *, credentials: object) -> object:
        self.calls.append((service, version))
        return self.client


@pytest.fixture
def api() -> MagicMock:
    return MagicMock()


@pytest.fixture
def svc(api: MagicMock, monkeypatch: pytest.MonkeyPatch) -> GoogleYouTubeService:
    monkeypatch.setattr(
        GoogleYouTubeService,
        "_media",
        staticmethod(lambda path, mimetype, **kw: ("media", path, mimetype, kw)),
    )
    return GoogleYouTubeService(
        credential_source=_Source(), client_factory=_Factory(api)
    )


def _upload(tmp_path: Path, privacy: str = "public") -> VideoUpload:
    f = tmp_path / "take.mp4"
    f.write_bytes(b"\0" * 10)
    return VideoUpload(
        file=f,
        metadata=VideoMetadata(title="Agent skills", tags=("rasa",)),
        status=VideoStatus(privacy=privacy),
    )


def test_needs_injected_credentials() -> None:
    result = GoogleYouTubeService().get_my_channel()
    assert result.success is False
    assert "injected credentials" in (result.error or "")


def test_builds_youtube_v3_from_the_injected_source(
    svc: GoogleYouTubeService, api: MagicMock
) -> None:
    api.channels().list().execute.return_value = {
        "items": [{"id": "UC1", "snippet": {"title": "Rasa", "customUrl": "@rasahq"}}]
    }
    result = svc.get_my_channel()
    assert result.success and result.content == {
        "id": "UC1",
        "title": "Rasa",
        "handle": "@rasahq",
    }
    assert svc._client_factory.calls == [("youtube", "v3")]  # type: ignore[attr-defined]


def test_no_channel_is_an_error(svc: GoogleYouTubeService, api: MagicMock) -> None:
    api.channels().list().execute.return_value = {"items": []}
    assert svc.get_my_channel().success is False


def test_upload_resumes_in_chunks_and_reports_progress(
    svc: GoogleYouTubeService, api: MagicMock, tmp_path: Path
) -> None:
    progress = MagicMock()
    progress.progress.return_value = 0.5
    request = MagicMock()
    request.next_chunk.side_effect = [
        (progress, None),
        (None, {"id": "vid1", "status": {"privacyStatus": "public"}}),
    ]
    api.videos().insert.return_value = request
    seen: list[float] = []
    result = svc.upload_video(_upload(tmp_path), on_progress=seen.append)
    assert result.success and result.content is not None
    assert (
        result.content["id"] == "vid1" and result.content["privacy_overridden"] is False
    )
    assert seen == [0.5, 1.0]
    kwargs = api.videos().insert.call_args.kwargs
    assert kwargs["part"] == "snippet,status"
    assert kwargs["body"]["status"] == {
        "privacyStatus": "public",
        "selfDeclaredMadeForKids": False,
    }
    assert kwargs["media_body"][3] == {"resumable": True, "chunksize": 8 * 1024 * 1024}


def test_upload_says_when_youtube_kept_it_private(
    svc: GoogleYouTubeService, api: MagicMock, tmp_path: Path
) -> None:
    request = MagicMock()
    request.next_chunk.return_value = (
        None,
        {"id": "vid2", "status": {"privacyStatus": "private"}},
    )
    api.videos().insert.return_value = request
    result = svc.upload_video(_upload(tmp_path, "public"))
    assert result.success and result.content is not None
    assert result.content["privacy_overridden"] is True
    assert "kept it private" in (result.message or "")


def test_upload_failure_is_reported_never_retried(
    svc: GoogleYouTubeService, api: MagicMock, tmp_path: Path
) -> None:
    request = MagicMock()
    request.next_chunk.side_effect = RuntimeError("connection reset")
    api.videos().insert.return_value = request
    result = svc.upload_video(_upload(tmp_path))
    assert result.success is False and "may exist" in (result.error or "")
    assert request.next_chunk.call_count == 1


def test_chunk_size_must_be_a_256k_multiple(
    svc: GoogleYouTubeService, tmp_path: Path
) -> None:
    assert svc.upload_video(_upload(tmp_path), chunk_bytes=1000).success is False


def test_schedule_metadata_thumbnail_caption_playlist(
    svc: GoogleYouTubeService, api: MagicMock, tmp_path: Path
) -> None:
    when = datetime.now(UTC) + timedelta(days=2)
    assert svc.set_status(
        "vid", VideoStatus(privacy="private", publish_at=when)
    ).success
    body = api.videos().update.call_args.kwargs["body"]
    assert (
        body["id"] == "vid"
        and body["status"]["privacyStatus"] == "private"
        and "publishAt" in body["status"]
    )

    assert svc.set_metadata("vid", VideoMetadata(title="x")).success is False, (
        "YouTube requires categoryId on update"
    )
    assert svc.set_metadata("vid", VideoMetadata(title="x", category_id="27")).success

    assert svc.set_thumbnail("vid", "thumb.png").success
    assert api.thumbnails().set.call_args.kwargs["media_body"][2] == "image/png"

    srt = tmp_path / "en.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    assert svc.add_caption(
        "vid", CaptionUpload(file=srt, language="en", name="English")
    ).success
    cap = api.captions().insert.call_args.kwargs
    assert cap["body"]["snippet"] == {
        "videoId": "vid",
        "language": "en",
        "name": "English",
        "isDraft": False,
    }

    assert svc.add_to_playlist("PL1", "vid").success
    assert api.playlistItems().insert.call_args.kwargs["body"]["snippet"][
        "resourceId"
    ] == {"kind": "youtube#video", "videoId": "vid"}


def test_provider_errors_become_error_results(
    svc: GoogleYouTubeService, api: MagicMock
) -> None:
    api.playlistItems().insert().execute.side_effect = RuntimeError("quotaExceeded")
    result = svc.add_to_playlist("PL1", "vid")
    assert result.success is False and "quotaExceeded" in (result.error or "")


def test_get_video(svc: GoogleYouTubeService, api: MagicMock) -> None:
    api.videos().list().execute.return_value = {
        "items": [{"id": "vid", "status": {"uploadStatus": "processed"}}]
    }
    assert svc.get_video("vid").content == {
        "id": "vid",
        "status": {"uploadStatus": "processed"},
    }
    api.videos().list().execute.return_value = {"items": []}
    assert svc.get_video("nope").success is False
