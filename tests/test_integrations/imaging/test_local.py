"""The local profile: the same requests, sent with the caller's own keys."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from tests.test_integrations.imaging.images import SVG, jpeg, png
from zeo_core.integrations.imaging import (
    GeminiGenerate,
    ImageInput,
    ImagingError,
    LocalGeminiImages,
    LocalRecraftImages,
    RecraftGenerate,
    RecraftImageToImage,
    RecraftRemoveBackground,
    RecraftVectorize,
)
from zeo_core.integrations.imaging.local import GEMINI_ORIGIN, RECRAFT_ORIGIN

KEY = "local-key-canary"


def _client(
    origin: str,
    handler: Callable[[httpx.Request], httpx.Response],
    sent: list[httpx.Request],
) -> httpx.Client:
    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    return httpx.Client(base_url=origin, transport=httpx.MockTransport(record))


def _gemini_answer(content: bytes, mime: str = "image/jpeg") -> dict[str, object]:
    return {
        "id": "int-1",
        "status": "completed",
        "steps": [
            {"type": "user_input", "content": [{"type": "text", "text": "x"}]},
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": "here"},
                    {
                        "type": "image",
                        "mime_type": mime,
                        "data": base64.b64encode(content).decode(),
                    },
                ],
            },
        ],
    }


def test_gemini_sends_the_prompt_references_and_format_with_the_key_header() -> None:
    sent: list[httpx.Request] = []
    output = jpeg(1024, 1024)
    images = LocalGeminiImages(
        KEY,
        http_client=_client(
            GEMINI_ORIGIN,
            lambda r: httpx.Response(200, json=_gemini_answer(output)),
            sent,
        ),
    )
    reference = ImageInput(content=png(), media_type="image/png")
    request = GeminiGenerate(prompt="a duck", inputs=(reference,), aspect_ratio="16:9")
    image = images.run(request)
    (http,) = sent
    assert http.url.path == "/v1beta/interactions"
    assert http.headers["x-goog-api-key"] == KEY
    assert KEY not in str(http.url)
    body = json.loads(http.content)
    assert body["model"] == "gemini-3.1-flash-image"
    assert body["input"][0] == {"type": "text", "text": "a duck"}
    assert body["input"][1]["type"] == "image"
    assert body["input"][1]["mime_type"] == "image/png"
    assert base64.b64decode(body["input"][1]["data"]) == png()
    assert body["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "aspect_ratio": "16:9",
        "image_size": "1K",
    }
    assert image.content == output
    assert image.media_type == "image/jpeg"
    assert image.request_key == request.idempotency_key()
    assert (image.profile, image.provider, image.replayed) == ("local", "gemini", False)


def test_an_image_found_outside_the_steps_is_still_found() -> None:
    nested = {
        "outputs": [
            {
                "image": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(jpeg()).decode(),
                }
            }
        ]
    }
    images = LocalGeminiImages(
        KEY,
        http_client=_client(
            GEMINI_ORIGIN, lambda r: httpx.Response(200, json=nested), []
        ),
    )
    assert images.run(GeminiGenerate(prompt="x")).content == jpeg()


def test_an_answer_in_another_media_type_is_refused_not_relabelled() -> None:
    images = LocalGeminiImages(
        KEY,
        http_client=_client(
            GEMINI_ORIGIN,
            lambda r: httpx.Response(200, json=_gemini_answer(png(), "image/png")),
            [],
        ),
    )
    with pytest.raises(ImagingError) as caught:
        images.run(GeminiGenerate(prompt="x"))
    assert caught.value.outcome == "invalid_response"


def test_png_output_is_refused_before_any_call() -> None:
    with pytest.raises(ValueError):
        GeminiGenerate(prompt="x", output_media_type="image/png")


def test_a_gemini_answer_without_an_image_is_a_refusal() -> None:
    images = LocalGeminiImages(
        KEY,
        http_client=_client(
            GEMINI_ORIGIN,
            lambda r: httpx.Response(
                200, json={"steps": [{"type": "model_output", "content": []}]}
            ),
            [],
        ),
    )
    with pytest.raises(ImagingError) as caught:
        images.run(GeminiGenerate(prompt="x"))
    assert caught.value.outcome == "refused"


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (400, "refused"),
        (403, "refused"),
        (429, "unavailable"),
        (500, "ambiguous"),
        (503, "ambiguous"),
    ],
)
def test_provider_failures_are_never_retried(status: int, outcome: str) -> None:
    sent: list[httpx.Request] = []
    images = LocalGeminiImages(
        KEY,
        http_client=_client(
            GEMINI_ORIGIN,
            lambda r: httpx.Response(
                status, json={"error": {"message": "prompt: a duck"}}
            ),
            sent,
        ),
    )
    with pytest.raises(ImagingError) as caught:
        images.run(GeminiGenerate(prompt="a duck"))
    assert caught.value.outcome == outcome
    assert "a duck" not in str(caught.value)
    assert len(sent) == 1


def test_a_timeout_is_ambiguous_and_an_unreachable_provider_is_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    for handler, outcome in [(timeout, "ambiguous"), (refused, "unavailable")]:
        images = LocalGeminiImages(KEY, http_client=_client(GEMINI_ORIGIN, handler, []))
        with pytest.raises(ImagingError) as caught:
            images.run(GeminiGenerate(prompt="x"))
        assert caught.value.outcome == outcome


def test_a_missing_key_is_refused_before_any_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RECRAFT_API_KEY", raising=False)
    with pytest.raises(ImagingError, match="GEMINI_API_KEY"):
        LocalGeminiImages()
    with pytest.raises(ImagingError, match="RECRAFT_API_KEY"):
        LocalRecraftImages()
    monkeypatch.setenv("RECRAFT_API_KEY", "  ")
    with pytest.raises(ImagingError, match="RECRAFT_API_KEY"):
        LocalRecraftImages()


def _recraft(
    answer: dict[str, object], sent: list[httpx.Request]
) -> LocalRecraftImages:
    return LocalRecraftImages(
        KEY,
        http_client=_client(
            RECRAFT_ORIGIN, lambda r: httpx.Response(200, json=answer), sent
        ),
    )


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


def test_recraft_generate_is_json_with_one_image_inline() -> None:
    sent: list[httpx.Request] = []
    image = _recraft(
        {
            "data": [{"b64_json": _b64(png(1024, 1024)), "image_id": "img-9"}],
            "credits": 40,
        },
        sent,
    ).run(
        RecraftGenerate(
            prompt="a duck sticker", style="digital_illustration", random_seed=7
        )
    )
    (request,) = sent
    assert request.url.path == "/v1/images/generations"
    assert request.headers["Authorization"] == "Bearer " + KEY
    assert json.loads(request.content) == {
        "model": "recraftv3",
        "prompt": "a duck sticker",
        "style": "digital_illustration",
        "random_seed": 7,
        "n": 1,
        "response_format": "b64_json",
    }
    assert image.provider_image_id == "img-9"
    assert image.cost is not None and image.cost.settled == 40
    assert image.media_type == "image/png"


def test_recraft_vector_styles_come_back_as_svg() -> None:
    image = _recraft({"data": [{"b64_json": _b64(SVG)}]}, []).run(
        RecraftGenerate(prompt="a duck", style="vector_illustration")
    )
    assert image.media_type == "image/svg+xml"


def test_recraft_image_to_image_is_multipart_with_the_seed_image() -> None:
    sent: list[httpx.Request] = []
    seed = ImageInput(content=png(512, 512), media_type="image/png")
    _recraft({"data": [{"b64_json": _b64(png(512, 512))}]}, sent).run(
        RecraftImageToImage(input=seed, prompt="sticker", strength=0.2)
    )
    (request,) = sent
    assert request.url.path == "/v1/images/imageToImage"
    body = request.content
    assert b'name="image"' in body and png(512, 512) in body
    assert b'name="strength"\r\n\r\n0.2' in body
    assert b'name="n"\r\n\r\n1' in body
    assert b'name="response_format"\r\n\r\nb64_json' in body


@pytest.mark.parametrize(
    ("request_", "path", "output", "media_type"),
    [
        (
            RecraftRemoveBackground,
            "/v1/images/removeBackground",
            png(64, 64),
            "image/png",
        ),
        (RecraftVectorize, "/v1/images/vectorize", SVG, "image/svg+xml"),
    ],
)
def test_recraft_asset_operations_upload_the_file(
    request_: type[RecraftRemoveBackground | RecraftVectorize],
    path: str,
    output: bytes,
    media_type: str,
) -> None:
    sent: list[httpx.Request] = []
    source = ImageInput(content=png(64, 64), media_type="image/png")
    image = _recraft(
        {"image": {"b64_json": _b64(output), "image_id": "img-2"}, "credits": 10}, sent
    ).run(request_(input=source))
    assert sent[0].url.path == path
    assert b'name="file"' in sent[0].content
    assert image.media_type == media_type
    assert image.provider_image_id == "img-2"


def test_a_vectorize_answer_that_embeds_a_raster_is_refused() -> None:
    bad = SVG.replace(b"<path", b'<image href="x.png"/><path')
    with pytest.raises(ImagingError) as caught:
        _recraft({"image": {"b64_json": _b64(bad)}}, []).run(
            RecraftVectorize(input=ImageInput(content=png(), media_type="image/png"))
        )
    assert caught.value.outcome == "invalid_response"


def test_recraft_credits_read_the_account() -> None:
    sent: list[httpx.Request] = []
    assert _recraft({"credits": 980, "email": "x"}, sent).credits().credits == 980
    assert sent[0].method == "GET" and sent[0].url.path == "/v1/users/me"
    with pytest.raises(ImagingError, match="no balance"):
        _recraft({"credits": "lots"}, []).credits()


def test_redirects_are_never_followed() -> None:
    images = LocalRecraftImages(
        KEY,
        http_client=_client(
            RECRAFT_ORIGIN,
            lambda r: httpx.Response(
                302, headers={"Location": "https://elsewhere.invalid"}
            ),
            [],
        ),
    )
    with pytest.raises(ImagingError, match="redirected"):
        images.credits()


def test_a_local_timeout_needs_a_new_occurrence_because_there_is_no_replay() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    images = LocalGeminiImages(KEY, http_client=_client(GEMINI_ORIGIN, timeout, []))
    request = GeminiGenerate(prompt="x")
    with pytest.raises(ImagingError) as caught:
        images.run(request)
    assert (caught.value.outcome, caught.value.retry) == ("ambiguous", "new_occurrence")
    assert caught.value.request_key == request.idempotency_key()
    server_error = LocalRecraftImages(
        KEY, http_client=_client(RECRAFT_ORIGIN, lambda r: httpx.Response(502), [])
    )
    with pytest.raises(ImagingError) as caught:
        server_error.run(RecraftGenerate(prompt="x"))
    assert caught.value.retry == "new_occurrence"
