# YouTube publishing

<!-- Offline contract only; verified against the YouTube Data API v3 reference 2026-10-07. -->

`GoogleYouTubeService` publishes a finished video to one YouTube channel:

- uploads the file in resumable chunks with its title, description, tags and privacy;
- schedules it (`publish_at`);
- sets its thumbnail;
- adds a caption track;
- adds it to a playlist;
- reads the authorised channel and the video's processing state.

Install the Google dependencies with `uv pip install -e ".[youtube]"`.

## Credentials are injected, never read from files

Unlike the Workspace services, this service has no local OAuth flow and reads no
credential or configuration file. You construct it with a `GoogleCredentialSource` and,
optionally, a `GoogleApiClientFactory` (both in `zeo_core.integrations.google.ports`).
A channel's refresh token can therefore live only inside the custody boundary that
injects it, such as a hosted connection. Without a credential source, every operation
returns an error result.

```python
from zeo_core.integrations.google.youtube import (
    GoogleYouTubeService,
    VideoMetadata,
    VideoStatus,
    VideoUpload,
)

service = GoogleYouTubeService(credential_source=my_custody_source)
channel = service.get_my_channel()  # check the identity before any upload
upload = VideoUpload(
    file="episode.mp4",
    metadata=VideoMetadata(title="Agent skills", category_id="27"),
    status=VideoStatus(privacy="private"),
)
result = service.upload_video(upload, on_progress=print)
```

The OAuth scopes are `youtube.upload`, `youtube` and `youtube.force-ssl` (captions),
and nothing broader. They are listed on `GoogleYouTubeService.SCOPES`.

## What the models refuse before any call

| Field | Rule |
|---|---|
| `title` | 1–100 characters, no `<` or `>` |
| `description` | at most 5,000 bytes, no `<` or `>` |
| `tags` | at most 500 characters in total; a tag with a space counts two extra (quotes); tags are separated by commas |
| `category_id` | numeric; required by `set_metadata`, since YouTube requires it on update |
| `publish_at` | only with privacy `private`, time-zone aware, in the future |
| captions | `.srt` or `.vtt` |
| files | must exist |

A request that validates can still be refused by YouTube (quota, channel state). The
result carries the provider's message.

## Outcomes the service reports, not hides

- **An unverified API project.** YouTube keeps every upload from an unverified API
  project private and still answers success. When the privacy YouTube reports differs
  from the privacy requested, the result sets `privacy_overridden` and says so. To
  publish publicly or on a schedule, the Google Cloud project must pass YouTube's API
  audit.
- **An upload that fails partway.** The video may or may not exist. The service never
  retries; the caller reconciles against the channel's uploads before trying again.
- **Quota.** An upload costs far more quota than a read. A quota refusal is an error
  result like any other.
- **Custom thumbnails** need a channel allowed to use them (a verified channel).

## Test track

Use a dedicated test channel, and upload as `private` until the whole flow has been
checked. The tests in `tests/test_integrations/google/youtube/` mock the Data API client
at the SDK boundary; no live request has been made by this package.
