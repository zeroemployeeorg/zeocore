"""The one image vocabulary: inputs are checked before any byte leaves."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.test_integrations.imaging.images import (
    SVG,
    jpeg,
    png,
    webp_vp8,
    webp_vp8l,
    webp_vp8x,
)
from zeo_core.integrations.imaging import (
    OPERATIONS,
    GeminiGenerate,
    ImageInput,
    ImagingError,
    RecraftGenerate,
    RecraftImageToImage,
    RecraftRemoveBackground,
    RecraftVectorize,
    check_vector,
)
from zeo_core.integrations.imaging.models import check_output, image_dimensions


def _png(width: int = 4, height: int = 4) -> ImageInput:
    return ImageInput(content=png(width, height), media_type="image/png")


@pytest.mark.parametrize(
    ("content", "media_type", "size"),
    [
        (png(640, 480), "image/png", (640, 480)),
        (jpeg(1024, 768), "image/jpeg", (1024, 768)),
        (webp_vp8x(2000, 1000), "image/webp", (2000, 1000)),
        (webp_vp8l(300, 200), "image/webp", (300, 200)),
        (webp_vp8(320, 240), "image/webp", (320, 240)),
    ],
)
def test_dimensions_come_from_the_header(
    content: bytes, media_type: str, size: tuple[int, int]
) -> None:
    assert image_dimensions(content, media_type) == size  # type: ignore[arg-type]
    ImageInput(content=content, media_type=media_type)


def test_an_input_must_be_the_image_it_claims() -> None:
    with pytest.raises(ValidationError, match="do not match"):
        ImageInput(content=png(), media_type="image/jpeg")
    with pytest.raises(ValidationError, match="do not match"):
        ImageInput(content=b"GIF89a....", media_type="image/png")


def test_an_input_over_40_megapixels_is_refused_before_upload() -> None:
    ImageInput(content=png(8000, 5000), media_type="image/png")
    with pytest.raises(ValidationError, match="40 megapixel"):
        ImageInput(content=png(8000, 5001), media_type="image/png")
    with pytest.raises(ValidationError, match="40 megapixel"):
        ImageInput(content=png(0, 10), media_type="image/png")


def test_an_unreadable_header_is_refused() -> None:
    with pytest.raises(ValidationError, match="unreadable"):
        ImageInput(content=b"\xff\xd8\xff\xd9", media_type="image/jpeg")


def test_from_path_sniffs_the_type(tmp_path: Path) -> None:
    path = tmp_path / "duck.bin"
    path.write_bytes(jpeg())
    assert ImageInput.from_path(path).media_type == "image/jpeg"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="not a png"):
        ImageInput.from_path(path)


def test_the_key_is_the_request_so_asking_again_is_a_replay() -> None:
    first = GeminiGenerate(prompt="a duck", inputs=(_png(),))
    assert (
        first.idempotency_key()
        == GeminiGenerate(prompt="a duck", inputs=(_png(),)).idempotency_key()
    )
    assert first.idempotency_key().startswith("zi-")
    others = [
        GeminiGenerate(prompt="a duck", inputs=(_png(),), occurrence="2"),
        GeminiGenerate(prompt="a goose", inputs=(_png(),)),
        GeminiGenerate(prompt="a duck", inputs=(_png(5, 5),)),
        GeminiGenerate(prompt="a duck", inputs=(_png(),), aspect_ratio="16:9"),
        GeminiGenerate(prompt="a duck"),
    ]
    keys = {item.idempotency_key() for item in others}
    assert first.idempotency_key() not in keys
    assert len(keys) == len(others)


def test_wire_arguments_leave_out_inputs_kind_and_occurrence() -> None:
    request = GeminiGenerate(
        prompt="a duck",
        inputs=(_png(),),
        occurrence="v2",
        output_media_type="image/jpeg",
    )
    assert request.arguments() == {
        "model": "gemini-3.1-flash-image",
        "prompt": "a duck",
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "output_mime_type": "image/jpeg",
    }
    assert RecraftVectorize(input=_png()).arguments() == {}
    assert RecraftImageToImage(
        input=_png(), prompt="sticker", strength=0.2
    ).arguments() == {"model": "recraftv3", "prompt": "sticker", "strength": 0.2}


def test_every_kind_has_one_broker_operation() -> None:
    assert OPERATIONS == {
        "gemini.generate": "gemini.image.generate",
        "recraft.generate": "recraft.image.generate",
        "recraft.image_to_image": "recraft.image.image_to_image",
        "recraft.remove_background": "recraft.image.remove_background",
        "recraft.crisp_upscale": "recraft.image.crisp_upscale",
        "recraft.vectorize": "recraft.image.vectorize",
    }


def test_recraft_bounds_hold_before_any_call() -> None:
    with pytest.raises(ValidationError, match="1000 bytes"):
        RecraftGenerate(prompt="é" * 501)
    RecraftGenerate(prompt="é" * 500)
    with pytest.raises(ValidationError, match="png or webp"):
        RecraftRemoveBackground(
            input=ImageInput(content=jpeg(), media_type="image/jpeg")
        )
    big = ImageInput(content=png(tail=b"\0" * 5_000_000), media_type="image/png")
    with pytest.raises(ValidationError, match="5,000,000"):
        RecraftRemoveBackground(input=big)
    with pytest.raises(ValidationError):
        RecraftImageToImage(input=_png(), prompt="x", strength=1.5)
    with pytest.raises(ValidationError):
        GeminiGenerate(prompt="x", inputs=tuple(_png(i + 1, 1) for i in range(7)))
    with pytest.raises(ValidationError):
        GeminiGenerate(prompt="x", model="gemini-2.0-flash")


def test_a_genuine_vector_passes() -> None:
    check_vector(SVG)
    check_vector(b'<?xml version="1.0"?>\n' + SVG)


@pytest.mark.parametrize(
    "content",
    [
        SVG.replace(b"<path", b'<image href="data:image/png;base64,AA"/><path'),
        SVG.replace(b"</svg>", b"<script>alert(1)</script></svg>"),
        SVG.replace(b"<svg ", b'<svg onload="x()" '),
        SVG.replace(b"<path", b"<foreignObject/><path"),
        b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        png(),
    ],
)
def test_anything_else_is_not_a_vector(content: bytes) -> None:
    with pytest.raises(ImagingError) as caught:
        check_vector(content)
    assert caught.value.outcome == "invalid_response"


def test_an_output_must_be_what_its_media_type_says() -> None:
    assert check_output(png(), "image/png") == "image/png"
    assert check_output(SVG, "image/svg+xml") == "image/svg+xml"
    for content, media_type in [(png(), "image/jpeg"), (b"", "image/png")]:
        with pytest.raises(ImagingError):
            check_output(content, media_type)


def test_only_models_verified_live_are_allowed() -> None:
    # Broker contract 1.3.0 draft 6 §3.
    for model in ("gemini-3.1-flash-image", "gemini-3-pro-image"):
        GeminiGenerate(prompt="x", model=model)
    with pytest.raises(ValidationError):
        GeminiGenerate(prompt="x", model="gemini-3-pro-image-preview")


def test_4k_is_allowed_only_where_it_was_verified_live() -> None:
    # 1.3.0 draft 7 §3: ZBS's thumbnails, gemini-3.1-flash-image only.
    GeminiGenerate(prompt="x", image_size="4K", aspect_ratio="16:9")
    with pytest.raises(ValidationError, match="4K"):
        GeminiGenerate(prompt="x", model="gemini-3-pro-image", image_size="4K")
    with pytest.raises(ValidationError):
        GeminiGenerate(prompt="x", image_size="2K")


def test_recraftv4_1_takes_only_its_own_arguments() -> None:
    from zeo_core.integrations.imaging.models import RecraftControls

    request = RecraftGenerate(
        model="recraftv4_1",
        prompt="thumbnail",
        size="1344x768",
        image_format="png",
        random_seed=2_147_483_647,
        controls=RecraftControls.model_validate(
            {"colors": [{"rgb": [255, 0, 10], "weight": 0.5}, {"rgb": [0, 0, 0]}]}
        ),
    )
    assert request.arguments() == {
        "model": "recraftv4_1",
        "prompt": "thumbnail",
        "size": "1344x768",
        "random_seed": 2_147_483_647,
        "image_format": "png",
        "controls": {
            "colors": [{"rgb": [255, 0, 10], "weight": 0.5}, {"rgb": [0, 0, 0]}]
        },
    }
    base = {
        "model": "recraftv4_1",
        "prompt": "x",
        "size": "1344x768",
        "image_format": "png",
    }
    cases: list[tuple[dict[str, object], str]] = [
        ({"style": "digital_illustration"}, "style"),
        ({"negative_prompt": "no"}, "negative_prompt"),
        ({"random_seed": 2_147_483_648}, "2147483647"),
        ({"size": "1024x1024"}, "1344x768"),
        ({"image_format": None}, "1344x768"),
    ]
    for extra, match in cases:
        with pytest.raises(ValidationError, match=match):
            RecraftGenerate.model_validate({**base, **extra})
    for colors in (
        [],
        [{"rgb": [0, 0, 0]}] * 6,
        [{"rgb": [256, 0, 0]}],
        [{"rgb": [0, 0, 0], "weight": 1.5}],
    ):
        with pytest.raises(ValidationError):
            RecraftGenerate.model_validate({**base, "controls": {"colors": colors}})
    with pytest.raises(ValidationError, match="recraftv3"):
        RecraftGenerate(prompt="x", image_format="png")


def test_crisp_upscale_takes_any_input_type_within_recrafts_bound() -> None:
    from zeo_core.integrations.imaging import RecraftCrispUpscale

    for content, media in (
        (png(), "image/png"),
        (jpeg(), "image/jpeg"),
        (webp_vp8x(8, 8), "image/webp"),
    ):
        RecraftCrispUpscale(input=ImageInput(content=content, media_type=media))
    big = ImageInput(content=png(tail=b"\0" * 5_000_000), media_type="image/png")
    with pytest.raises(ValidationError, match="5,000,000"):
        RecraftCrispUpscale(input=big)


def test_crisp_upscale_refuses_more_than_1344x768_pixels() -> None:
    from zeo_core.integrations.imaging import RecraftCrispUpscale

    RecraftCrispUpscale(
        input=ImageInput(content=png(1344, 768), media_type="image/png")
    )
    with pytest.raises(ValidationError, match="1,032,192"):
        RecraftCrispUpscale(
            input=ImageInput(content=png(1344, 769), media_type="image/png")
        )
