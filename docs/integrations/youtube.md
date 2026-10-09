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
`receipt.json` once, at the end. State is the fold of the events. The one exception is
`youtube.json`, which holds YouTube's data and is replaced or deleted (see the 30-day rule
below).

The identifiers use ZEO Runtime's occurrence derivation, so Runtime can adopt the same
files (`job.runtime_identity`; the tests carry vectors computed by Go). The executor
refuses a job whose `authorization.json` doesn't bind the exact bytes of `job.json`.

Upload links are kept in the macOS Keychain (`--link-store keychain`, the default),
written through stdin. An owner-only file (`--link-store file`) is used only when you
choose it explicitly.

## YouTube data is kept for at most 30 days

The YouTube API Services Developer Policies (§III.E.4) limit stored authorized data to
30 calendar days, unless it is refreshed. The adviser's note r14 settled the rule:

- **Only `youtube.json` holds YouTube data:** the video's id, privacy, schedule and
  link, plus when they were fetched. The write-once files (`job.json`, the events,
  `receipt.json`) never contain anything YouTube returned.
- **`retain` refreshes, or deletes.** Once a record is 20 days old, `retain` refreshes
  it with one `youtube.video.get` read (1 quota unit).
  - **The video is gone, or access has lapsed:** the record is deleted, and a
    `provider_data_dropped` event says when and why.
  - **ZEOconnect is unreachable:** the refresh waits. But a record that reaches 29 days
    is deleted anyway.
- **What stays:** the job's own record, meaning what was sent, the operator's
  authorization, and that a publish happened.

Run `retain` once a day; the studio's runner does.

## Command line

```bash
python -m zeo_core.integrations.google.youtube.publish pair          # once: pair this device with ZEOconnect
python -m zeo_core.integrations.google.youtube.publish connections   # the YouTube connection IDs
python -m zeo_core.integrations.google.youtube.publish run JOB_DIR   # advance a job; safe to repeat
python -m zeo_core.integrations.google.youtube.publish status JOB_DIR
python -m zeo_core.integrations.google.youtube.publish close JOB_DIR --step STEP  # close a held job; sends nothing
python -m zeo_core.integrations.google.youtube.publish retain PUBLISH_ROOT  # daily: the 30-day rule
```

`run` prints one JSON status line. Its exit code says what to do next:

| Exit | Meaning | What to do |
|---|---|---|
| `0` | done or cancelled | nothing |
| `10` | waiting for your approval (`approval_url`) | approve in ZEOconnect, then run again |
| `11` | waiting: not due yet, YouTube processing, paused, or busy | run again later |
| `20` | held (`reason`) | if zeocore raised the hold itself (`ambiguous_upload`, `read_failed`), resolve it and append a `released` event; if ZEOconnect recorded the outcome, see below |
| `2` | invalid or unauthorized job | fix the job |

A run holds a lock on the job directory, so two runners never work on the same job. It
stops cleanly at the next chunk boundary on SIGTERM.

### A job held on an outcome ZEOconnect recorded

Holds such as `refused_in_zeoconnect` and `provider_refused` come from an answer
ZEOconnect recorded against the step's idempotency key. A `released` event
doesn't change the key, so the next run gets the same recorded answer and holds
again. Release can't retry these holds.

`close JOB_DIR --step STEP` ends such a job on a person's decision:
- **It sends nothing.** There is no new key and no new attempt.
- **It takes the run's lock** and exits `11` (`busy`) if a run holds it.
- **It closes only a held job**, and `STEP` must be one of the job's steps: `video`,
  `thumbnail`, `caption:<language>:<name>` or `playlist`.
- **It records the close** as the studio's `cancelled` event. The event names the
  exact hold it ends: `closed_on` (the hold's reason), `held_seq` (the hold's
  event), `step` and `attempt`. The REFUSED receipt keeps the reason `cancelled`,
  which every reader already understands. The original hold stays in the journal.
- **Whether a video exists** is read from the journal: an `uploaded` event means
  yes. It is never read from `youtube.json` (`provider_record`), which retention
  may delete.
- **Repeating it, or running the job afterwards, gives the same result.**

A new job for the same video is a separate request with a new key. It is not a
retry of the closed one, and it is not something zeocore starts.

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
