"""YouTube request models: what YouTube would reject is rejected before any call."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from zeo_core.integrations.google.youtube.models import (
    CaptionSessionRequest,
    ThumbnailSessionRequest,
    VideoMetadata,
    VideoSessionRequest,
    VideoStatus,
)


def test_snippet_carries_only_what_is_set() -> None:
    m = VideoMetadata(
        title="Agent skills",
        description="One job each.",
        tags=("ai agents", "rasa"),
        category_id="27",
        default_language="en",
    )
    assert m.snippet() == {
        "title": "Agent skills",
        "description": "One job each.",
        "tags": ["ai agents", "rasa"],
        "categoryId": "27",
        "defaultLanguage": "en",
        "defaultAudioLanguage": "en",
    }
    assert VideoMetadata(title="x").snippet() == {"title": "x", "description": ""}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": ""},
        {"title": "x" * 101},
        {"title": "a <b> title"},
        {"title": "x", "description": "é" * 2501},
        {"title": "x", "description": "use <script>"},
        {"title": "x", "tags": ("word " * 120,)},
        {"title": "x", "category_id": "education"},
    ],
)
def test_metadata_rejects_what_youtube_rejects(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VideoMetadata(**kwargs)


def test_tags_count_quotes_and_separators() -> None:
    # 500 characters in one tag is fine; tags with spaces cost two quotes each,
    # and every tag after the first costs a separator.
    VideoMetadata(title="x", tags=("a" * 500,))
    with pytest.raises(ValidationError):
        VideoMetadata(title="x", tags=("a b" * 100, "c d" * 67))


def test_status_and_schedule() -> None:
    later = datetime.now(UTC) + timedelta(days=1)
    s = VideoStatus(privacy="private", publish_at=later)
    assert s.status()["privacyStatus"] == "private"
    assert str(s.status()["publishAt"]).endswith(".000Z")
    assert VideoStatus(privacy="unlisted").status() == {
        "privacyStatus": "unlisted",
        "selfDeclaredMadeForKids": False,
    }
    with pytest.raises(ValidationError, match="requires privacy 'private'"):
        VideoStatus(privacy="public", publish_at=later)
    with pytest.raises(ValidationError, match="time zone"):
        VideoStatus(privacy="private", publish_at=datetime(2030, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValidationError, match="future"):
        VideoStatus(
            privacy="private", publish_at=datetime.now(UTC) - timedelta(minutes=1)
        )


def test_session_requests_refuse_what_youtube_refuses() -> None:
    meta, status = VideoMetadata(title="x"), VideoStatus(privacy="private")
    VideoSessionRequest(
        size_bytes=1, mime_type="video/mp4", metadata=meta, status=status
    )
    with pytest.raises(ValidationError):
        VideoSessionRequest(
            size_bytes=1, mime_type="text/plain", metadata=meta, status=status
        )
    with pytest.raises(ValidationError):
        VideoSessionRequest(
            size_bytes=0, mime_type="video/mp4", metadata=meta, status=status
        )
    with pytest.raises(ValidationError):
        ThumbnailSessionRequest(
            video_id="v", size_bytes=3 * 1024 * 1024, mime_type="image/png"
        )
    with pytest.raises(ValidationError):
        ThumbnailSessionRequest(video_id="a&b", size_bytes=10, mime_type="image/png")
    with pytest.raises(ValidationError):
        CaptionSessionRequest(
            video_id="v", language="en", size_bytes=10, mime_type="text/plain"
        )
