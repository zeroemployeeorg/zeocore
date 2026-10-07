"""YouTube request models: what YouTube would reject is rejected before any call."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from zeo_core.integrations.google.youtube.models import (
    CaptionUpload,
    VideoMetadata,
    VideoStatus,
    VideoUpload,
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


def test_files_must_exist_and_captions_be_srt_or_vtt(tmp_path: Path) -> None:
    video = tmp_path / "take.mp4"
    video.write_bytes(b"\0")
    VideoUpload(
        file=video,
        metadata=VideoMetadata(title="x"),
        status=VideoStatus(privacy="private"),
    )
    with pytest.raises(ValidationError, match="not found"):
        VideoUpload(
            file=tmp_path / "missing.mp4",
            metadata=VideoMetadata(title="x"),
            status=VideoStatus(privacy="private"),
        )
    srt = tmp_path / "en.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    CaptionUpload(file=srt, language="en")
    txt = tmp_path / "en.txt"
    txt.write_text("hi")
    with pytest.raises(ValidationError, match=r"\.srt or \.vtt"):
        CaptionUpload(file=txt, language="en")
