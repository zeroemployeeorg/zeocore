# Images: Nano Banana and Recraft

`zeo_core.integrations.imaging` gives one API for image generation and asset
creation over Google's Gemini image models (Nano Banana) and Recraft. The
requests and the result are the same whether you use your own keys (the local
profile) or let ZEOconnect hold them (the hosted profile).

!!! warning "Draft"
    The hosted profile follows the proposed ZEOconnect Broker contract 1.3.0,
    which isn't frozen yet, and billed calls without per-call approval await a
    council ruling. The local profile is an offline contract, not yet run here
    against live accounts.

## What you can ask for

| Request | Provider call | Output |
|---|---|---|
| `GeminiGenerate` | Nano Banana, text to image or edit from up to 6 ordered references (`gemini-3.1-flash-image`, `gemini-3-pro-image`, `gemini-3-pro-image-preview`), 1K | JPEG |
| `RecraftGenerate` | Recraft generation (`recraftv3`), with `style`, `negative_prompt`, `size`, `random_seed` | PNG, or SVG for vector styles |
| `RecraftImageToImage` | Restyle one seed image, `strength` 0–1 | PNG |
| `RecraftRemoveBackground` | Cut out, from png or webp up to 5,000,000 bytes | PNG |
| `RecraftVectorize` | Raster to vector | SVG |

`ImagingService.credits()` reads the Recraft balance.

- **One image per call.** A variant is a new call with a new `occurrence`.
- **Inputs are checked before anything is sent:** the bytes must be the declared
  png, jpeg or webp, at most 10 MiB and 40 megapixels, within the provider's own
  limits. An unsupported parameter or combination is refused, never dropped.
- **Gemini output is JPEG at 1K only:** `gemini-3.1-flash-image` refuses png
  output, and the Broker allows only values verified live.
- **An SVG must be a genuine, inert vector.** It needs at least one shape, and
  must carry no `<image>`, embedded raster, script, event handler or foreign
  content. This is a structural check, not a visual approval.

## In Python

```python
from zeo_core.integrations.imaging import (
    GeminiGenerate, ImageInput, ImagingError, RecraftVectorize, build_imaging,
)

images = build_imaging()  # ZEOCORE_CONNECTION_PROFILE: local (default) or hosted
duck = images.run(GeminiGenerate(
    prompt="the duck, facing left, full figure",
    inputs=(ImageInput.from_path("duck.png"),),
))
duck.save("duck-left.jpg")
print(duck.sha256, duck.model, duck.cost, duck.request_key)
```

`GeneratedImage` carries the bytes, `media_type`, `sha256`, `provider`,
`operation`, `profile`, `model`, `provider_image_id`, `cost`, `execution_id`,
`replayed` and `request_key`.

## From any language: `zeocore image`

One JSON object on stdin, one on stdout. The image bytes are written to `output`
and never go to stdout.

```console
$ echo '{"request": {"kind": "recraft.vectorize", "input": {"path": "duck.png"}},
         "output": "duck.svg"}' | zeocore image
{"ok": true, "path": "duck.svg", "media_type": "image/svg+xml", "sha256": "…",
 "provider": "recraft", "operation": "recraft.image.vectorize", "profile": "hosted",
 "model": null, "provider_image_id": "…", "cost": {"unit": "recraft_credits", …},
 "execution_id": "…", "replayed": false, "request_key": "zi-…"}
$ echo '{"credits": true}' | zeocore image
{"ok": true, "provider": "recraft", "credits": 980.0}
```

Input images are given as `{"path": …, "role": …}`, where `role` is optional:
`input` for Recraft, and `inputs` (an ordered list) for Gemini. The answer's
`inputs` lists each input's sha256, media type and role, in order. A role is
never sent to the provider and doesn't change `request_key`.

The result is the last JSON line on stdout. The exit status is the `zeocore`
family ([the zeocore command](../reference/cli.md)):

| Exit | Outcomes |
|---|---|
| 0 | done |
| 2 | invalid request; nothing was sent |
| 10 | `approval_required` |
| 11 | `in_flight`, `unavailable`, `input_unavailable`: ask again later |
| 12 | `not_paired`: run `zeocore login` |
| 13 | `ambiguous` |
| 20 | `refused`, `budget_exhausted`, `stopped`, `artifact_expired`, `invalid_response` |

A failed answer carries `outcome`, `message`, `retry`, `request_key`,
`approval_url` and, for `artifact_expired`, `content_sha256`.

## Failures and retries

`ImagingError.outcome` says what happened, and `retry` what another attempt
needs:

| `retry` | Meaning |
|---|---|
| `same_request` | Asking again with the same request is safe. On the hosted profile it replays the stored outcome, or runs a call that never started, and never bills twice. Fix the budget, stop or approval first where the outcome names one. |
| `new_occurrence` | The failure is recorded against this request, or (local profile) the call may have run. Another attempt is a new call with a new `occurrence`, and may bill. |
| `none` | The request itself has to change. |

The outcomes are listed in the exit table above. Nothing
is retried automatically, and there is never a fallback to another provider or
model.

## The request identity

`request.idempotency_key()` (`request_key` in answers) is derived from the
operation, the arguments, the sha256 of each input and `occurrence`. The same
request is the same image: on the hosted profile, asking for it again is an
exact replay of the stored outcome. An interrupted call can be resolved by
asking again, without a second bill. To get a different image from the same
inputs, change `occurrence`.

## Hosted profile (ZEOconnect)

ZEOconnect holds each provider's API key as a connection, enrolled in WEB, with
a budget set there. zeocore never sees the key.

- `ZEOCORE_CONNECTION_PROFILE=hosted` selects the hosted profile.
- `ZEOCORE_IMAGING_GEMINI_CONNECTION` and `ZEOCORE_IMAGING_RECRAFT_CONNECTION`
  name the connections.
- The device must be paired with ZEOconnect.

How a call runs:

- **Inputs go up first,** through `POST /v1/artifacts:upload`.
- **The output can feed the next call directly.** An image the same connection
  produced is used without uploading it again, so remove background followed by
  vectorize uploads once. Moving an image to the other provider uploads it.
- **Billed calls wait up to 180 s.** Giving up after the request was sent is
  `ambiguous`, and the same request then replays.
- **The connection's budget is reserved first,** at worst-case cost, before the
  provider call. A call with no budget, or one that would pass it, is
  `budget_exhausted`.

## Local profile

The local profile uses your own keys, `GEMINI_API_KEY` and `RECRAFT_API_KEY`.
It calls Gemini's Interactions API and Recraft's v1 API directly, with no
Broker, so there is no replay: a repeated call is a new call and may bill again.
