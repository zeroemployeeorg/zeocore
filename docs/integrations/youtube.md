# YouTube publishing

<!-- Offline contract only; verified against the YouTube Data API v3 resumable-upload guide 2026-10-07. -->

Publishing a finished video involves three pieces. The channel's token never reaches the
machine that has the file, and multi-GB files never pass through the hosted service.

| Piece | Module | Runs where | Holds |
|---|---|---|---|
| Provider client | `zeo_core.integrations.google.youtube.service` | inside ZEOconnect (custody) | the channel's OAuth token, injected |
| Byte transfer | `zeo_core.integrations.google.youtube.transfer` | the device with the file | one upload session link |
| Publish executor | `zeo_core.integrations.google.youtube.publish` | the device | a job directory |

Install with `uv pip install -e ".[youtube]"`.

## How one upload works

1. The device asks ZEOconnect for `youtube.video.upload_session.create`, sending the
   size, the sha256, the title, the privacy and any `publish_at`.
2. ZEOconnect shows you the exact request and asks for approval in the browser.
3. Once you approve, ZEOconnect opens a resumable session with the token and returns only
   the session link.
4. The device sends the bytes to that link in 16 MiB chunks (`ResumableTransfer`).
   - **Every attempt starts by asking YouTube how many bytes it has**
     (`Content-Range: bytes */SIZE`). A dropped connection, a killed process or a reboot
     therefore continues at the exact byte.
   - Connection errors, timeouts, 429 and 5xx answers back off (up to 60 s) and try again
     for as long as the session lives.
   - The video exists only once the last byte lands, so no resend can create a second one.
5. The thumbnail, captions and playlist follow. Each is its own approval. Captions and
   playlists are checked first, so nothing is added twice.
6. The executor waits for YouTube's processing, then checks that the privacy and schedule
   are the ones you asked for. When they aren't, the job is held instead of being reported
   done: an unverified Google Cloud project keeps uploads private.

With `publish_at`, the video is uploaded early as private and YouTube itself makes it
public at that time. A large upload therefore has hours or days to recover from problems.

## A lost answer never becomes a duplicate

- The job records `final_chunk_sent` before sending the request that carries the last
  byte.
- If that answer is lost and the session has expired, the executor looks for the upload
  by title and file size. File size is visible only to the channel owner.
  - **One match** is adopted as the upload.
  - **Several matches** hold the job as `ambiguous_upload` until you release it.
  - **No match** means a new session (and a new approval).
- Without `final_chunk_sent`, a lost session is always safe to replace: an unfinished
  session creates nothing.

## The job directory

`job.json` and `authorization.json` are written once, by the studio. After that the
directory only grows: events are appended to `events/%010d.json`, and the executor writes
`receipt.json` once, at the end. State is the fold of the events.

The identifiers use ZEO Runtime's occurrence derivation, so Runtime can adopt the same
files (`job.runtime_identity`; the tests carry vectors computed by Go). The executor
refuses a job whose `authorization.json` doesn't bind the exact bytes of `job.json`.

Upload links are kept in the macOS Keychain (`--link-store keychain`, the default),
written through stdin. An owner-only file (`--link-store file`) is used only when you
choose it explicitly.

## Command line

```bash
python -m zeo_core.integrations.google.youtube.publish pair          # once: pair this device with ZEOconnect
python -m zeo_core.integrations.google.youtube.publish connections   # the YouTube connection IDs
python -m zeo_core.integrations.google.youtube.publish run JOB_DIR   # advance a job; safe to repeat
python -m zeo_core.integrations.google.youtube.publish status JOB_DIR
```

`run` prints one JSON status line. Its exit code says what to do next:

| Exit | Meaning | What to do |
|---|---|---|
| `0` | done or cancelled | nothing |
| `10` | waiting for your approval (`approval_url`) | approve in ZEOconnect, then run again |
| `11` | waiting: not due yet, YouTube processing, paused, or busy | run again later |
| `20` | held (`reason`) | resolve the reason; append a `released` event |
| `2` | invalid or unauthorized job | fix the job |

A run holds a lock on the job directory, so two runners never work on the same job. It
stops cleanly at the next chunk boundary on SIGTERM.

## When YouTube wants a token on every chunk

Google's guide shows the token on every upload request. The design first sends chunks
straight to the session link. If YouTube refuses the link alone (401/403), the executor
switches that job to the **custody relay** (`relay.RelayByteHttp`), records
`relay_engaged`, and continues the same session from the byte YouTube holds:

- Each chunk, at most 4 MiB (the hosting platform's request limit), goes to ZEOconnect's
  `POST /v1/youtube/uploads:relay` with the link and the `relay_seal` that ZEOconnect
  issued with the session.
- ZEOconnect checks the seal, adds the channel's token inside custody, forwards the
  chunk, and answers with only YouTube's status, `Range`, and on completion the video's
  id and privacy.
- The device still never holds the token, and ZEOconnect still stores no link.

Later steps and runs of that job go straight to the relay. If the relay is unavailable
too, the job is held as `session_link_refused`. The first trial on a test channel shows
which path YouTube accepts.

## Test track

Use a dedicated test channel and upload as `private`. The tests in
`tests/test_integrations/google/youtube/` run against a fake upload server that keeps real
byte offsets: dropped connections, lost answers, expired sessions and refused links. No
live request has been made by this package.
