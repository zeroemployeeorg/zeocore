"""Typed requests for the YouTube Data API v3 operations ``GoogleYouTubeService``
exposes.

Each model validates what YouTube itself would reject, before any call is made: title
and description lengths, the total tag length, characters YouTube refuses in titles and
descriptions, and ``publishAt``, which YouTube accepts only on a private video and only
in the future. A request that validates here can still be refused by YouTube (quota,
channel state, an unverified API project); the service reports that, it never guesses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Privacy = Literal["private", "unlisted", "public"]

#: YouTube's documented limits (Data API v3, ``videos`` resource).
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_TOTAL_MAX = 500


def _no_angle_brackets(value: str, field: str) -> str:
    if "<" in value or ">" in value:
        raise ValueError(f"{field} may not contain '<' or '>' (YouTube rejects them)")
    return value


class VideoMetadata(BaseModel):
    """The snippet of a video: what viewers read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str = ""
    tags: tuple[str, ...] = ()
    category_id: str | None = Field(default=None, pattern=r"^\d+$")
    default_language: str | None = Field(
        default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]+)*$"
    )

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _no_angle_brackets(value, "title")

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        if len(value.encode("utf-8")) > DESCRIPTION_MAX_BYTES:
            raise ValueError(f"description exceeds {DESCRIPTION_MAX_BYTES} bytes")
        return _no_angle_brackets(value, "description")

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # YouTube counts a tag with a space as if quoted (two extra characters),
        # plus one separator between tags.
        total = sum(len(t) + (2 if " " in t else 0) for t in value) + max(
            0, len(value) - 1
        )
        if total > TAGS_TOTAL_MAX:
            raise ValueError(
                f"tags total {total} characters; YouTube allows {TAGS_TOTAL_MAX}"
            )
        return value

    def snippet(self) -> dict[str, object]:
        """The Data API ``snippet`` body."""
        out: dict[str, object] = {"title": self.title, "description": self.description}
        if self.tags:
            out["tags"] = list(self.tags)
        if self.category_id:
            out["categoryId"] = self.category_id
        if self.default_language:
            out["defaultLanguage"] = self.default_language
            out["defaultAudioLanguage"] = self.default_language
        return out


class VideoStatus(BaseModel):
    """Who can see the video, and when it goes public."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    privacy: Privacy
    publish_at: datetime | None = None
    made_for_kids: bool = False

    @model_validator(mode="after")
    def _schedule(self) -> VideoStatus:
        if self.publish_at is None:
            return self
        if self.privacy != "private":
            raise ValueError(
                "publish_at requires privacy 'private'"
                " (YouTube makes it public at that time)"
            )
        if self.publish_at.tzinfo is None:
            raise ValueError("publish_at must carry a time zone")
        if self.publish_at <= datetime.now(UTC):
            raise ValueError("publish_at must be in the future")
        return self

    def status(self) -> dict[str, object]:
        """The Data API ``status`` body."""
        out: dict[str, object] = {
            "privacyStatus": self.privacy,
            "selfDeclaredMadeForKids": self.made_for_kids,
        }
        if self.publish_at is not None:
            out["publishAt"] = self.publish_at.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
        return out


#: Media types YouTube accepts for each upload kind (the device sends the bytes).
VIDEO_MIME_TYPES = frozenset({"video/mp4", "video/quicktime", "video/webm", "video/*"})
THUMBNAIL_MIME_TYPES = frozenset({"image/jpeg", "image/png"})
CAPTION_MIME_TYPES = frozenset({"application/x-subrip", "text/vtt"})
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
#: YouTube's maximum upload: 256 GB.
VIDEO_MAX_BYTES = 256 * 1000**3

LanguageCode = Field(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]+)*$")


class VideoSessionRequest(BaseModel):
    """Open a resumable session for one video; the bytes are sent elsewhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    size_bytes: int = Field(gt=0, le=VIDEO_MAX_BYTES)
    mime_type: str
    metadata: VideoMetadata
    status: VideoStatus
    notify_subscribers: bool = True

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if value not in VIDEO_MIME_TYPES:
            raise ValueError(f"unsupported video media type: {value}")
        return value


class ThumbnailSessionRequest(BaseModel):
    """Open a resumable session for a custom thumbnail (JPEG or PNG, at most 2 MB)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    size_bytes: int = Field(gt=0, le=THUMBNAIL_MAX_BYTES)
    mime_type: str

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if value not in THUMBNAIL_MIME_TYPES:
            raise ValueError("thumbnails must be image/jpeg or image/png")
        return value


class CaptionSessionRequest(BaseModel):
    """Open a resumable session for a published (not draft) caption track."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    language: str = LanguageCode
    name: str = Field(default="", max_length=150)
    size_bytes: int = Field(gt=0, le=100 * 1024 * 1024)
    mime_type: str

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if value not in CAPTION_MIME_TYPES:
            raise ValueError("captions must be application/x-subrip or text/vtt")
        return value


__all__ = [
    "CAPTION_MIME_TYPES",
    "DESCRIPTION_MAX_BYTES",
    "TAGS_TOTAL_MAX",
    "THUMBNAIL_MAX_BYTES",
    "THUMBNAIL_MIME_TYPES",
    "TITLE_MAX",
    "VIDEO_MAX_BYTES",
    "VIDEO_MIME_TYPES",
    "CaptionSessionRequest",
    "Privacy",
    "ThumbnailSessionRequest",
    "VideoMetadata",
    "VideoSessionRequest",
    "VideoStatus",
]
