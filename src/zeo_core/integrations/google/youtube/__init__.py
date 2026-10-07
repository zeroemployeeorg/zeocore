"""YouTube publishing: provider client, credential-free transfer, publish executor.

- ``service``: the provider client a custody boundary (ZEOconnect) runs with the
  channel's token. It opens upload sessions and never sends a video's bytes.
- ``transfer``: the device sends the bytes to a session link, resuming at the exact
  byte after any interruption. No credential.
- ``job`` and ``publish``: the job directory and the executor that advances it.
"""

from zeo_core.integrations.google.youtube.models import (
    CaptionSessionRequest,
    Privacy,
    ThumbnailSessionRequest,
    VideoMetadata,
    VideoSessionRequest,
    VideoStatus,
)
from zeo_core.integrations.google.youtube.service import GoogleYouTubeService

__all__ = [
    "CaptionSessionRequest",
    "GoogleYouTubeService",
    "Privacy",
    "ThumbnailSessionRequest",
    "VideoMetadata",
    "VideoSessionRequest",
    "VideoStatus",
]
