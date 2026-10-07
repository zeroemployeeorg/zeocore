"""GoogleYouTubeService against a mocked Data API client and a fake authorized HTTP.

No network and no token: the credential source, client factory and authorized HTTP are
injected, as a custody boundary injects them in production.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from zeo_core.integrations.google.youtube import (
    CaptionSessionRequest,
    GoogleYouTubeService,
    ThumbnailSessionRequest,
    VideoMetadata,
    VideoSessionRequest,
    VideoStatus,
)

UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=X"


class _Source:
    def get_credentials(self) -> object:
        return "credentials"


class _Factory:
    def __init__(self, client: MagicMock) -> None:
        self.client = client

    def build(self, service: str, version: str, *, credentials: object) -> object:
        assert (service, version, credentials) == ("youtube", "v3", "credentials")
        return self.client


@dataclass
class _Response:
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    text: str = ""


class _Http:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def session(self, credentials: object) -> _Http:
        assert credentials == "credentials"
        return self

    def request(self, method: str, url: str, **kw: object) -> _Response:
        self.requests.append({"method": method, "url": url, **kw})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _svc(api: MagicMock, http: _Http | None = None) -> GoogleYouTubeService:
    return GoogleYouTubeService(
        credential_source=_Source(),
        client_factory=_Factory(api),
        http_factory=http or _Http(_Response(200, {"Location": UPLOAD})),
    )


def _video_request(**status: object) -> VideoSessionRequest:
    return VideoSessionRequest(
        size_bytes=4_210_000_000,
        mime_type="video/mp4",
        metadata=VideoMetadata(title="Agent skills", tags=("rasa",), category_id="27"),
        status=VideoStatus(**({"privacy": "private"} | status)),
        notify_subscribers=False,
    )


def test_needs_injected_credentials() -> None:
    result = GoogleYouTubeService().get_my_channel()
    assert result.success is False and "injected credentials" in (result.error or "")


def test_channel_identity() -> None:
    api = MagicMock()
    api.channels().list().execute.return_value = {
        "items": [{"id": "UC1", "snippet": {"title": "Rasa", "customUrl": "@rasahq"}}]
    }
    assert _svc(api).get_my_channel().content == {
        "channel_id": "UC1",
        "title": "Rasa",
        "handle": "@rasahq",
    }
    api.channels().list().execute.return_value = {"items": []}
    assert _svc(api).get_my_channel().success is False


def test_video_session_sends_metadata_and_returns_only_the_link() -> None:
    http = _Http(_Response(200, {"location": UPLOAD}))
    later = datetime.now(UTC) + timedelta(days=1)
    result = _svc(MagicMock(), http).create_video_upload_session(
        _video_request(publish_at=later)
    )
    assert result.success and result.content == {"upload_url": UPLOAD}
    sent = http.requests[0]
    assert sent["method"] == "POST" and sent["allow_redirects"] is False
    assert sent["url"].startswith(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable"
    )
    assert "notifySubscribers=false" in sent["url"]
    assert sent["headers"]["X-Upload-Content-Length"] == "4210000000"
    assert sent["headers"]["X-Upload-Content-Type"] == "video/mp4"
    body = json.loads(sent["data"])
    assert (
        body["snippet"]["title"] == "Agent skills"
        and body["snippet"]["categoryId"] == "27"
    )
    assert body["status"]["privacyStatus"] == "private" and body["status"][
        "publishAt"
    ].endswith(".000Z")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            _Response(
                403,
                text=json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}}),
            ),
            "HTTP 403 quotaExceeded",
        ),
        (_Response(200, {"Location": "https://evil.example/upload"}), "no upload link"),
        (_Response(200), "no upload link"),
        (ConnectionError("down"), "ConnectionError"),
    ],
)
def test_session_failures_are_error_results(
    response: _Response | Exception, message: str
) -> None:
    result = _svc(MagicMock(), _Http(response)).create_video_upload_session(
        _video_request()
    )
    assert result.success is False and message in (result.error or "")


def test_thumbnail_and_caption_sessions() -> None:
    http = _Http(_Response(200, {"Location": UPLOAD}))
    svc = _svc(MagicMock(), http)
    assert svc.create_thumbnail_upload_session(
        ThumbnailSessionRequest(
            video_id="vid_1-x", size_bytes=5000, mime_type="image/png"
        )
    ).success
    assert http.requests[0]["url"].endswith(
        "/thumbnails/set?uploadType=resumable&videoId=vid_1-x"
    )
    assert http.requests[0]["data"] == b""
    assert svc.create_caption_upload_session(
        CaptionSessionRequest(
            video_id="vid1",
            language="en",
            name="English",
            size_bytes=40,
            mime_type="application/x-subrip",
        )
    ).success
    assert json.loads(http.requests[1]["data"])["snippet"] == {
        "videoId": "vid1",
        "language": "en",
        "name": "English",
        "isDraft": False,
    }


def test_get_video_normalizes_state() -> None:
    api = MagicMock()
    api.videos().list().execute.return_value = {
        "items": [
            {
                "id": "vid1",
                "snippet": {"title": "t"},
                "status": {
                    "privacyStatus": "private",
                    "publishAt": "2026-10-09T15:00:00Z",
                    "uploadStatus": "processed",
                },
                "processingDetails": {"processingStatus": "succeeded"},
            }
        ]
    }
    assert _svc(api).get_video("vid1").content == {
        "video_id": "vid1",
        "title": "t",
        "privacy": "private",
        "publish_at": "2026-10-09T15:00:00Z",
        "upload_status": "processed",
        "processing_status": "succeeded",
        "failure_reason": None,
        "rejection_reason": None,
    }
    api.videos().list().execute.return_value = {"items": []}
    assert _svc(api).get_video("nope").success is False


def test_find_upload_matches_title_size_and_time() -> None:
    api = MagicMock()
    api.channels().list().execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]
    }
    api.playlistItems().list().execute.return_value = {
        "items": [{"contentDetails": {"videoId": v}} for v in ("a", "b", "c", "d")]
    }
    api.videos().list().execute.return_value = {
        "items": [
            {
                "id": "a",
                "snippet": {"title": "T", "publishedAt": "2026-10-07T10:00:00Z"},
                "fileDetails": {"fileSize": "100"},
                "status": {"privacyStatus": "private", "uploadStatus": "processed"},
            },
            {
                "id": "b",
                "snippet": {"title": "T", "publishedAt": "2026-10-07T10:00:00Z"},
                "fileDetails": {"fileSize": "101"},
            },
            {
                "id": "c",
                "snippet": {"title": "Other", "publishedAt": "2026-10-07T10:00:00Z"},
                "fileDetails": {"fileSize": "100"},
            },
            {
                "id": "d",
                "snippet": {"title": "T", "publishedAt": "2026-10-01T10:00:00Z"},
                "fileDetails": {"fileSize": "100"},
            },
        ]
    }
    result = _svc(api).find_upload(
        title="T", size_bytes=100, uploaded_after=datetime(2026, 10, 7, tzinfo=UTC)
    )
    assert [m["video_id"] for m in result.content["matches"]] == ["a"]  # type: ignore[index]
    api.channels().list().execute.return_value = {"items": []}
    assert (
        _svc(api)
        .find_upload(title="T", size_bytes=1, uploaded_after=datetime.now(UTC))
        .success
        is False
    )


def test_captions_playlist_status() -> None:
    api = MagicMock()
    api.captions().list().execute.return_value = {
        "items": [{"id": "c1", "snippet": {"language": "en", "name": "English"}}]
    }
    assert _svc(api).list_captions("v").content == {
        "tracks": [{"caption_id": "c1", "language": "en", "name": "English"}]
    }
    api.playlistItems().list().execute.return_value = {"items": []}
    assert _svc(api).playlist_contains("PL", "v").content == {
        "present": False,
        "playlist_item_id": None,
    }
    api.playlistItems().insert().execute.return_value = {"id": "pli"}
    assert _svc(api).add_to_playlist("PL", "v").content == {"playlist_item_id": "pli"}
    api.videos().update().execute.return_value = {"status": {"privacyStatus": "public"}}
    assert _svc(api).set_status("v", VideoStatus(privacy="public")).content == {
        "video_id": "v",
        "privacy": "public",
        "publish_at": None,
    }
    api.captions().list().execute.side_effect = RuntimeError("quotaExceeded")
    assert "quotaExceeded" in (_svc(api).list_captions("v").error or "")
