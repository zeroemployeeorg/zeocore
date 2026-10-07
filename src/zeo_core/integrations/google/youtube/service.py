"""YouTube Data API v3: publish a finished video and everything that goes with it.

``GoogleYouTubeService`` uploads a video (resumable, in chunks), sets its schedule and
metadata, sets its thumbnail, adds a caption track, adds it to a playlist, and reads the
authorised channel and a video's state. It is the provider client that a hosted
connection (ZEOconnect) wraps with custody, approval and idempotency; it does none of
that itself.

CREDENTIALS: injected only. The service is built from a ``GoogleCredentialSource`` and a
``GoogleApiClientFactory`` (``google/ports.py``), as ``GoogleDocsService``'s injected
path is; it reads no credential or config file, so a channel's tokens can live only
inside the custody boundary that injects them. Without a credential source,
``initialize()`` fails and every operation reports it.

HONEST OUTCOMES: YouTube keeps uploads from an unverified API project private and still
answers success. When the requested privacy differs from the privacy YouTube reports
back, the result says so (``privacy_requested`` against ``privacy``) instead of
reporting the request as done.

ERROR SHAPE: as the other Google services, each public method returns
``IntegrationResult``; an SDK failure becomes ``error_result`` with the provider's
message, and nothing is retried here (an upload whose outcome is unknown must be
reconciled by the caller, not repeated blindly).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from zeo_core.integrations.core.base import BaseIntegrationService
from zeo_core.integrations.core.results import IntegrationResult
from zeo_core.integrations.google.ports import (
    DiscoveryGoogleApiClientFactory,
    GoogleApiClientFactory,
    GoogleCredentialSource,
)
from zeo_core.integrations.google.youtube.models import (
    CaptionUpload,
    VideoMetadata,
    VideoStatus,
    VideoUpload,
)

if TYPE_CHECKING:
    from googleapiclient.http import MediaFileUpload

NoneType = type(None)

#: Upload chunk size: a multiple of 256 KiB, as resumable uploads require.
CHUNK_BYTES = 8 * 1024 * 1024

ProgressCallback = Callable[[float], None]


class GoogleYouTubeService(BaseIntegrationService):
    """Integration service for publishing on YouTube (Data API v3)."""

    #: upload (videos.insert); youtube (videos.update, thumbnails.set,
    #: playlistItems.insert, channels.list); force-ssl (captions.insert).
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
        self.youtube: Any = None

    @property
    def name(self) -> str:
        return "GoogleYouTube"

    @property
    def version(self) -> str:
        return "1.0.0"

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
            credentials = self._credential_source.get_credentials()
            self.youtube = self._client_factory.build(
                "youtube", "v3", credentials=credentials
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
            self.logger.error(f"YouTube {what} failed: {api_error}")
            return IntegrationResult.error_result(f"YouTube {what} failed: {api_error}")
        return IntegrationResult.success_result(
            content=cast(dict[str, Any], response or {}), message=f"YouTube {what}"
        )

    @staticmethod
    def _media(
        path: str, mimetype: str, *, resumable: bool, chunksize: int = -1
    ) -> MediaFileUpload:
        from googleapiclient.http import MediaFileUpload

        return MediaFileUpload(
            path, mimetype=mimetype, resumable=resumable, chunksize=chunksize
        )

    # ------------------------------------------------------------------
    # channels.list (mine)
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
                "id": item.get("id"),
                "title": snippet.get("title"),
                "handle": snippet.get("customUrl"),
            },
            message="Read the authorised channel",
        )

    # ------------------------------------------------------------------
    # videos.insert (resumable)
    # ------------------------------------------------------------------

    def upload_video(
        self,
        upload: VideoUpload,
        *,
        chunk_bytes: int = CHUNK_BYTES,
        on_progress: ProgressCallback | None = None,
    ) -> IntegrationResult[dict[str, Any]]:
        """Upload a video in resumable chunks with its snippet and status.

        Returns the video id, the privacy YouTube reports, and the privacy requested.
        When they differ (an unverified API project keeps uploads private),
        ``privacy_overridden`` is true and the message says so.
        """
        if chunk_bytes % (256 * 1024):
            return IntegrationResult.error_result(
                "chunk_bytes must be a multiple of 256 KiB"
            )
        if not_ready := self._ready():
            return not_ready
        body = {"snippet": upload.metadata.snippet(), "status": upload.status.status()}
        try:
            media = self._media(
                str(upload.file), "video/*", resumable=True, chunksize=chunk_bytes
            )
            request = self.youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
                notifySubscribers=upload.notify_subscribers,
            )
            response: dict[str, Any] | None = None
            while response is None:
                progress, response = request.next_chunk()
                if progress is not None and on_progress is not None:
                    on_progress(float(progress.progress()))
        except Exception as api_error:
            # Unknown outcome: the video may exist. The caller reconciles against
            # the channel's uploads before trying again.
            self.logger.error(f"YouTube videos.insert failed: {api_error}")
            return IntegrationResult.error_result(
                f"YouTube videos.insert failed (the video may exist): {api_error}"
            )
        if on_progress is not None:
            on_progress(1.0)
        requested = upload.status.privacy
        privacy = (response.get("status") or {}).get("privacyStatus")
        overridden = privacy is not None and privacy != requested
        return IntegrationResult.success_result(
            content={
                "id": response.get("id"),
                "privacy": privacy,
                "privacy_requested": requested,
                "privacy_overridden": overridden,
                "publish_at": (response.get("status") or {}).get("publishAt"),
            },
            message=(
                f"Uploaded, but YouTube kept it {privacy} (asked {requested}):"
                " the API project may be unverified"
                if overridden
                else f"Uploaded as {privacy}"
            ),
        )

    # ------------------------------------------------------------------
    # videos.update
    # ------------------------------------------------------------------

    def set_status(
        self, video_id: str, status: VideoStatus
    ) -> IntegrationResult[dict[str, Any]]:
        """Change a video's privacy or schedule (``publish_at`` keeps it private)."""
        return self._call(
            "videos.update(status)",
            lambda: (
                self.youtube.videos()
                .update(part="status", body={"id": video_id, "status": status.status()})
                .execute()
            ),
        )

    def set_metadata(
        self, video_id: str, metadata: VideoMetadata
    ) -> IntegrationResult[dict[str, Any]]:
        """Replace a video's title, description, tags, category and language."""
        snippet = metadata.snippet()
        if "categoryId" not in snippet:
            return IntegrationResult.error_result(
                "set_metadata needs category_id (YouTube requires it on update)"
            )
        return self._call(
            "videos.update(snippet)",
            lambda: (
                self.youtube.videos()
                .update(part="snippet", body={"id": video_id, "snippet": snippet})
                .execute()
            ),
        )

    # ------------------------------------------------------------------
    # thumbnails.set, captions.insert, playlistItems.insert
    # ------------------------------------------------------------------

    def set_thumbnail(
        self, video_id: str, image_path: str
    ) -> IntegrationResult[dict[str, Any]]:
        """Set a custom thumbnail: JPEG or PNG, at most 2 MB.

        The channel must be allowed custom thumbnails (a verified channel).
        """
        mimetype = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        return self._call(
            "thumbnails.set",
            lambda: (
                self.youtube.thumbnails()
                .set(
                    videoId=video_id,
                    media_body=self._media(image_path, mimetype, resumable=False),
                )
                .execute()
            ),
        )

    def add_caption(
        self, video_id: str, caption: CaptionUpload
    ) -> IntegrationResult[dict[str, Any]]:
        """Add a caption track (as a published track, not a draft)."""
        mimetype = (
            "text/vtt"
            if caption.file.suffix.lower() == ".vtt"
            else "application/x-subrip"
        )
        body = {
            "snippet": {
                "videoId": video_id,
                "language": caption.language,
                "name": caption.name,
                "isDraft": False,
            }
        }
        return self._call(
            "captions.insert",
            lambda: (
                self.youtube.captions()
                .insert(
                    part="snippet",
                    body=body,
                    media_body=self._media(
                        str(caption.file), mimetype, resumable=False
                    ),
                )
                .execute()
            ),
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
        return self._call(
            "playlistItems.insert",
            lambda: (
                self.youtube.playlistItems().insert(part="snippet", body=body).execute()
            ),
        )

    # ------------------------------------------------------------------
    # videos.list
    # ------------------------------------------------------------------

    def get_video(self, video_id: str) -> IntegrationResult[dict[str, Any]]:
        """A video's status and processing state (has YouTube finished processing?)."""
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
            content=cast(dict[str, Any], items[0]), message=f"Read video {video_id}"
        )


__all__ = ["CHUNK_BYTES", "GoogleYouTubeService", "ProgressCallback"]
