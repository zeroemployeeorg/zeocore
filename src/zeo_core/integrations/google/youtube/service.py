"""YouTube Data API v3: the provider side of publishing a finished video.

``GoogleYouTubeService`` runs inside a custody boundary (ZEOconnect) that holds the
channel's OAuth token. It never sends a video's bytes. For each upload it opens a
resumable session and returns the session link; the device that has the file sends the
bytes to that link (``transfer.py``). A multi-GB 4K upload therefore never passes
through the custody service, and the token never reaches the device.

It also reads a channel and its videos, finds an upload after a lost answer
(``find_upload``), lists caption tracks, checks a playlist, and changes a video's
privacy or schedule.

CREDENTIALS: injected only (``GoogleCredentialSource``). The service reads no
credential or config file. Without a credential source every operation reports an
error.

FIXED ORIGIN: every request goes to ``https://www.googleapis.com`` with redirects off,
and a session link is returned only when it is on YouTube's upload path there.

ERROR SHAPE: each public method returns ``IntegrationResult``. A provider failure
becomes ``error_result`` and nothing is retried here; retry and reconciliation belong
to the caller's orchestrator.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

from zeo_core.integrations.core.base import BaseIntegrationService
from zeo_core.integrations.core.results import IntegrationResult
from zeo_core.integrations.google.ports import (
    DiscoveryGoogleApiClientFactory,
    GoogleApiClientFactory,
    GoogleCredentialSource,
)
from zeo_core.integrations.google.youtube.models import (
    CaptionSessionRequest,
    ThumbnailSessionRequest,
    VideoSessionRequest,
    VideoStatus,
)

NoneType = type(None)

ORIGIN = "https://www.googleapis.com"
#: Where YouTube's resumable session links live; anything else is refused.
UPLOAD_PREFIX = ORIGIN + "/upload/youtube/v3/"
SESSION_TIMEOUT_SECONDS = 30.0
#: How many of the channel's newest uploads ``find_upload`` inspects.
FIND_WINDOW = 50


@runtime_checkable
class HttpResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def text(self) -> str: ...


@runtime_checkable
class AuthorizedHttp(Protocol):
    """An HTTP session that adds the channel's bearer token to each request."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        data: bytes,
        timeout: float,
        allow_redirects: bool,
    ) -> HttpResponse: ...


@runtime_checkable
class AuthorizedHttpFactory(Protocol):
    def session(self, credentials: object) -> AuthorizedHttp: ...


class GoogleAuthorizedHttpFactory:
    """Default: ``google.auth`` ``AuthorizedSession``, ambient proxies disabled."""

    def session(self, credentials: object) -> AuthorizedHttp:
        from google.auth.transport.requests import AuthorizedSession

        session = AuthorizedSession(credentials)
        session.trust_env = False
        return cast(AuthorizedHttp, session)


class GoogleYouTubeService(BaseIntegrationService):
    """Integration service for publishing on YouTube (Data API v3)."""

    #: upload (sessions for videos.insert); youtube (videos.update,
    #: thumbnails.set, playlistItems, channels.list); force-ssl (captions).
    #: Nothing broader.
    SCOPES: list[str] = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ]

    def __init__(
        self,
        *,
        credential_source: GoogleCredentialSource | None = None,
        client_factory: GoogleApiClientFactory | None = None,
        http_factory: AuthorizedHttpFactory | None = None,
        log_level: int = logging.INFO,
    ) -> None:
        super().__init__(
            config_provider=None,
            auth_provider=None,
            config=None,
            config_path=None,
            log_level=log_level,
        )
        self._credential_source = credential_source
        self._client_factory = client_factory or DiscoveryGoogleApiClientFactory()
        self._http_factory = http_factory or GoogleAuthorizedHttpFactory()
        self._credentials: object | None = None
        self.youtube: Any = None

    @property
    def name(self) -> str:
        return "GoogleYouTube"

    @property
    def version(self) -> str:
        return "2.0.0"

    def initialize(self) -> IntegrationResult[NoneType]:
        """Build the YouTube client from the injected credential source."""
        if self._initialized:
            return IntegrationResult.success_result(
                message="Google YouTube service already initialized"
            )
        if self._credential_source is None:
            return IntegrationResult.error_result(
                "Google YouTube needs injected credentials"
                " (a credential source from a custody boundary)"
            )
        try:
            self._credentials = self._credential_source.get_credentials()
            self.youtube = self._client_factory.build(
                "youtube", "v3", credentials=self._credentials
            )
        except Exception:
            self._initialized = False
            self.logger.error("Injected Google YouTube construction failed")
            return IntegrationResult.error_result(
                "Failed to initialize injected Google YouTube service"
            )
        self._initialized = True
        return IntegrationResult.success_result(
            message="Google YouTube service initialized successfully"
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _ready(self) -> IntegrationResult[Any] | None:
        if init_error := self._ensure_initialized():
            return init_error
        if self.youtube is None:
            return IntegrationResult.error_result(
                "Google YouTube service is not initialized"
            )
        return None

    def _call(
        self, what: str, run: Callable[[], Any]
    ) -> IntegrationResult[dict[str, Any]]:
        """Run one Data API call; a provider failure is an error result, not a retry."""
        if not_ready := self._ready():
            return not_ready
        try:
            response = run()
        except Exception as api_error:
            self.logger.error(f"YouTube {what} failed: {type(api_error).__name__}")
            return IntegrationResult.error_result(f"YouTube {what} failed: {api_error}")
        return IntegrationResult.success_result(
            content=cast(dict[str, Any], response or {}), message=f"YouTube {what}"
        )

    def _open_session(
        self,
        what: str,
        path_and_query: str,
        *,
        size_bytes: int,
        mime_type: str,
        body: dict[str, object] | None,
    ) -> IntegrationResult[dict[str, Any]]:
        """POST a resumable-session request and return the session link only."""
        if not_ready := self._ready():
            return not_ready
        headers = {
            "X-Upload-Content-Length": str(size_bytes),
            "X-Upload-Content-Type": mime_type,
        }
        data = b""
        if body is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            http = self._http_factory.session(self._credentials)
            response = http.request(
                "POST",
                ORIGIN + path_and_query,
                headers=headers,
                data=data,
                timeout=SESSION_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except Exception as error:
            self.logger.error(f"YouTube {what} failed: {type(error).__name__}")
            return IntegrationResult.error_result(
                f"YouTube {what} failed: {type(error).__name__}"
            )
        if response.status_code != 200:
            return IntegrationResult.error_result(
                f"YouTube {what} refused: HTTP {response.status_code}"
                f" {_reason(response.text)}".rstrip()
            )
        location = _header(response.headers, "Location")
        if location is None or not location.startswith(UPLOAD_PREFIX):
            return IntegrationResult.error_result(
                f"YouTube {what} returned no upload link on {UPLOAD_PREFIX}"
            )
        return IntegrationResult.success_result(
            content={"upload_url": location}, message=f"YouTube {what}"
        )

    # ------------------------------------------------------------------
    # channel
    # ------------------------------------------------------------------

    def get_my_channel(self) -> IntegrationResult[dict[str, Any]]:
        """The channel these credentials publish to: id, title and handle.

        This is the identity check to make before any upload.
        """
        result = self._call(
            "channels.list",
            lambda: (
                self.youtube.channels()
                .list(part="id,snippet", mine=True, maxResults=1)
                .execute()
            ),
        )
        if not result.success or result.content is None:
            return result
        items = result.content.get("items") or []
        if not items:
            return IntegrationResult.error_result(
                "These credentials have no YouTube channel"
            )
        item = items[0]
        snippet = item.get("snippet", {})
        return IntegrationResult.success_result(
            content={
                "channel_id": item.get("id"),
                "title": snippet.get("title"),
                "handle": snippet.get("customUrl"),
            },
            message="Read the authorised channel",
        )

    # ------------------------------------------------------------------
    # upload sessions (the device sends the bytes)
    # ------------------------------------------------------------------

    def create_video_upload_session(
        self, request: VideoSessionRequest
    ) -> IntegrationResult[dict[str, Any]]:
        """Open a resumable videos.insert session with the snippet and status.

        The video exists only once the last byte reaches the returned link; an
        unfinished session creates nothing on the channel.
        """
        notify = "true" if request.notify_subscribers else "false"
        return self._open_session(
            "videos.insert session",
            "/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
            f"&notifySubscribers={notify}",
            size_bytes=request.size_bytes,
            mime_type=request.mime_type,
            body={
                "snippet": request.metadata.snippet(),
                "status": request.status.status(),
            },
        )

    def create_thumbnail_upload_session(
        self, request: ThumbnailSessionRequest
    ) -> IntegrationResult[dict[str, Any]]:
        """Open a resumable thumbnails.set session for an existing video."""
        return self._open_session(
            "thumbnails.set session",
            "/upload/youtube/v3/thumbnails/set?uploadType=resumable"
            f"&videoId={request.video_id}",
            size_bytes=request.size_bytes,
            mime_type=request.mime_type,
            body=None,
        )

    def create_caption_upload_session(
        self, request: CaptionSessionRequest
    ) -> IntegrationResult[dict[str, Any]]:
        """Open a resumable captions.insert session (a published track, not a draft)."""
        return self._open_session(
            "captions.insert session",
            "/upload/youtube/v3/captions?uploadType=resumable&part=snippet",
            size_bytes=request.size_bytes,
            mime_type=request.mime_type,
            body={
                "snippet": {
                    "videoId": request.video_id,
                    "language": request.language,
                    "name": request.name,
                    "isDraft": False,
                }
            },
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def get_video(self, video_id: str) -> IntegrationResult[dict[str, Any]]:
        """A video's privacy, schedule, upload and processing state."""
        result = self._call(
            "videos.list",
            lambda: (
                self.youtube.videos()
                .list(part="status,processingDetails,snippet", id=video_id)
                .execute()
            ),
        )
        if not result.success or result.content is None:
            return result
        items = result.content.get("items") or []
        if not items:
            return IntegrationResult.error_result(
                f"No video {video_id} on this channel"
            )
        return IntegrationResult.success_result(
            content=_video_state(items[0]), message=f"Read video {video_id}"
        )

    def find_upload(
        self, *, title: str, size_bytes: int, uploaded_after: datetime
    ) -> IntegrationResult[dict[str, Any]]:
        """Find an upload whose answer was lost: same title, same file size.

        Inspects the channel's newest uploads. ``fileDetails`` is visible only to the
        channel owner, so the size match is private to these credentials.
        """
        channel = self._call(
            "channels.list(uploads)",
            lambda: (
                self.youtube.channels()
                .list(part="contentDetails", mine=True, maxResults=1)
                .execute()
            ),
        )
        if not channel.success or channel.content is None:
            return channel
        items = channel.content.get("items") or []
        uploads = (
            items[0]
            .get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
            if items
            else None
        )
        if not uploads:
            return IntegrationResult.error_result("The channel has no uploads list")
        listed = self._call(
            "playlistItems.list(uploads)",
            lambda: (
                self.youtube.playlistItems()
                .list(part="contentDetails", playlistId=uploads, maxResults=FIND_WINDOW)
                .execute()
            ),
        )
        if not listed.success or listed.content is None:
            return listed
        ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in listed.content.get("items") or []
        ]
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            return IntegrationResult.success_result(
                content={"matches": []}, message="No uploads"
            )
        videos = self._call(
            "videos.list(fileDetails)",
            lambda: (
                self.youtube.videos()
                .list(part="snippet,status,fileDetails", id=",".join(ids))
                .execute()
            ),
        )
        if not videos.success or videos.content is None:
            return videos
        matches = []
        for item in videos.content.get("items") or []:
            snippet = item.get("snippet", {})
            size = item.get("fileDetails", {}).get("fileSize")
            published = _parse_time(snippet.get("publishedAt"))
            if (
                snippet.get("title") == title
                and size is not None
                and int(size) == size_bytes
                and (published is None or published >= uploaded_after)
            ):
                matches.append(
                    {
                        "video_id": item.get("id"),
                        "privacy": item.get("status", {}).get("privacyStatus"),
                        "upload_status": item.get("status", {}).get("uploadStatus"),
                        "published_at": snippet.get("publishedAt"),
                    }
                )
        return IntegrationResult.success_result(
            content={"matches": matches}, message=f"{len(matches)} matching upload(s)"
        )

    def list_captions(self, video_id: str) -> IntegrationResult[dict[str, Any]]:
        """The caption tracks a video already has."""
        result = self._call(
            "captions.list",
            lambda: (
                self.youtube.captions().list(part="snippet", videoId=video_id).execute()
            ),
        )
        if not result.success or result.content is None:
            return result
        tracks = [
            {
                "caption_id": item.get("id"),
                "language": item.get("snippet", {}).get("language"),
                "name": item.get("snippet", {}).get("name"),
            }
            for item in result.content.get("items") or []
        ]
        return IntegrationResult.success_result(
            content={"tracks": tracks}, message=f"{len(tracks)} caption track(s)"
        )

    def playlist_contains(
        self, playlist_id: str, video_id: str
    ) -> IntegrationResult[dict[str, Any]]:
        """Whether a playlist already holds a video (checked before adding it)."""
        result = self._call(
            "playlistItems.list",
            lambda: (
                self.youtube.playlistItems()
                .list(part="id", playlistId=playlist_id, videoId=video_id, maxResults=1)
                .execute()
            ),
        )
        if not result.success or result.content is None:
            return result
        items = result.content.get("items") or []
        return IntegrationResult.success_result(
            content={
                "present": bool(items),
                "playlist_item_id": items[0].get("id") if items else None,
            },
            message="Checked the playlist",
        )

    # ------------------------------------------------------------------
    # effects
    # ------------------------------------------------------------------

    def set_status(
        self, video_id: str, status: VideoStatus
    ) -> IntegrationResult[dict[str, Any]]:
        """Change a video's privacy or schedule (``publish_at`` keeps it private)."""
        result = self._call(
            "videos.update(status)",
            lambda: (
                self.youtube.videos()
                .update(part="status", body={"id": video_id, "status": status.status()})
                .execute()
            ),
        )
        if not result.success or result.content is None:
            return result
        state = result.content.get("status", {})
        return IntegrationResult.success_result(
            content={
                "video_id": video_id,
                "privacy": state.get("privacyStatus"),
                "publish_at": state.get("publishAt"),
            },
            message="Updated the video's status",
        )

    def add_to_playlist(
        self, playlist_id: str, video_id: str
    ) -> IntegrationResult[dict[str, Any]]:
        """Append a video to a playlist the channel owns."""
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        result = self._call(
            "playlistItems.insert",
            lambda: (
                self.youtube.playlistItems().insert(part="snippet", body=body).execute()
            ),
        )
        if not result.success or result.content is None:
            return result
        return IntegrationResult.success_result(
            content={"playlist_item_id": result.content.get("id")},
            message="Added to the playlist",
        )


def _video_state(item: Mapping[str, Any]) -> dict[str, Any]:
    status = item.get("status", {})
    return {
        "video_id": item.get("id"),
        "title": item.get("snippet", {}).get("title"),
        "privacy": status.get("privacyStatus"),
        "publish_at": status.get("publishAt"),
        "upload_status": status.get("uploadStatus"),
        "processing_status": item.get("processingDetails", {}).get("processingStatus"),
        "failure_reason": status.get("failureReason"),
        "rejection_reason": status.get("rejectionReason"),
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _reason(text: str) -> str:
    """YouTube's error reason, never the whole provider body."""
    try:
        errors = json.loads(text).get("error", {}).get("errors") or []
        reason = errors[0].get("reason") if errors else None
    except ValueError, AttributeError, TypeError:
        reason = None
    return str(reason) if reason else ""


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "FIND_WINDOW",
    "ORIGIN",
    "UPLOAD_PREFIX",
    "AuthorizedHttp",
    "AuthorizedHttpFactory",
    "GoogleAuthorizedHttpFactory",
    "GoogleYouTubeService",
    "HttpResponse",
]
