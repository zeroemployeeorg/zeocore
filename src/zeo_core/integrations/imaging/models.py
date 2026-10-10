"""One request and result vocabulary for Nano Banana and Recraft images.

The same models drive the hosted profile (ZEOconnect holds the provider key)
and the local profile (the caller's own key). The wire shape follows the
proposed Broker contract 1.3.0 (ZEOCORE-SOW-12 §3, as changed by ZEOconnect).
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The hosted artifact bound, both ways (contract §3 and the 1.3.0 upload).
MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
#: The Broker refuses larger inputs as a decompression-bomb guard; zeocore
#: checks first so a doomed upload is never sent.
MAX_INPUT_PIXELS: Final = 40_000_000
#: Recraft's own bound for a background-removal input.
MAX_REMOVE_BACKGROUND_BYTES: Final = 5_000_000
MAX_GEMINI_INPUTS: Final = 6

InputMediaType = Literal["image/png", "image/jpeg", "image/webp"]
OutputMediaType = Literal["image/png", "image/jpeg", "image/webp", "image/svg+xml"]
Provider = Literal["gemini", "recraft"]
#: Broker contract 1.3.0 draft 6 §3: only models verified live.
GeminiModel = Literal["gemini-3.1-flash-image", "gemini-3-pro-image"]
RecraftModel = Literal["recraftv3"]
#: Generation also allows recraftv4_1, with its own arguments (1.3.0 draft 7).
RecraftGenerateModel = Literal["recraftv3", "recraftv4_1"]
AspectRatio = Literal["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"]
Occurrence = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]
StyleToken = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_/]{0,63}$")]


class ImagingError(RuntimeError):
    """Why an image was not produced, in the terms a caller can act on.

    ``outcome`` is one of:

    - ``refused``: the provider or Broker said no; nothing was produced.
    - ``budget_exhausted``: the connection's budget can't cover this call.
    - ``input_unavailable``: an input id expired or belongs elsewhere.
    - ``stopped``: an operational stop is in force.
    - ``unavailable``: the service could not be reached or reported an outage.
    - ``ambiguous``: the call may have run and may have been billed. Sending
      the same request again is an exact replay of the stored outcome on the
      hosted profile; it never bills twice. On the local profile there is no
      replay, so a repeat may bill again.
    - ``approval_required``: a person must approve in ZEOconnect first;
      ``approval_url`` says where.
    - ``in_flight``: the first call for this request is still running.
    - ``not_paired``: this device has no ZEOconnect session.
    - ``invalid_response``: the answer did not match the reviewed contract.
    - ``artifact_expired``: the call succeeded earlier, but the Broker no
      longer holds the image bytes (after 30 days). It never regenerates;
      ``content_sha256`` names what was produced.

    ``retry`` says what another attempt needs, and ``request_key`` names the
    request, so a caller can record both.
    """

    def __init__(
        self,
        outcome: str,
        message: str,
        *,
        approval_url: str | None = None,
        retry: str | None = None,
        request_key: str | None = None,
        content_sha256: str | None = None,
    ) -> None:
        self.outcome = outcome
        self.approval_url = approval_url
        self.content_sha256 = content_sha256
        #: What another attempt needs:
        #:
        #: - ``same_request``: asking again with the same request is safe. On
        #:   the hosted profile it replays a stored outcome or runs a call that
        #:   never started; it never bills twice. Fix a budget, a stop or an
        #:   approval first where the outcome says so.
        #: - ``new_occurrence``: the failure is recorded against this request,
        #:   or (local profile) the call may have run, so another attempt is a
        #:   new call under a new ``occurrence`` and may bill.
        #: - ``none``: the request itself has to change.
        self.retry = retry or _RETRY.get(outcome, "none")
        self.request_key = request_key
        super().__init__(message)


_RETRY = dict.fromkeys(
    (
        "ambiguous",
        "approval_required",
        "in_flight",
        "not_paired",
        "unavailable",
        "stopped",
        "budget_exhausted",
        "input_unavailable",
    ),
    "same_request",
)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sniff_media_type(content: bytes) -> InputMediaType | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_dimensions(content: bytes, media_type: InputMediaType) -> tuple[int, int]:
    """Width and height from the header alone; nothing is decoded."""
    try:
        if media_type == "image/png":
            if content[12:16] != b"IHDR":
                raise ValueError
            width, height = struct.unpack(">II", content[16:24])
            return width, height
        if media_type == "image/jpeg":
            return _jpeg_dimensions(content)
        return _webp_dimensions(content)
    except ValueError, struct.error, IndexError:
        raise ValueError("image header is unreadable") from None


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    index = 2
    while index + 4 <= len(content):
        if content[index] != 0xFF:
            raise ValueError
        marker = content[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        (length,) = struct.unpack(">H", content[index + 2 : index + 4])
        # Start-of-frame markers, excluding DHT, JPG and DAC.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", content[index + 5 : index + 9])
            return width, height
        index += 2 + length
    raise ValueError


def _webp_dimensions(content: bytes) -> tuple[int, int]:
    chunk = content[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
        return width, height
    if chunk == b"VP8L":
        bits = int.from_bytes(content[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if chunk == b"VP8 ":
        width, height = struct.unpack("<HH", content[26:30])
        return width & 0x3FFF, height & 0x3FFF
    raise ValueError


class ImageInput(BaseModel):
    """Bytes a caller supplies, checked before any of them leave the machine.

    ``role`` is the caller's own label for what this input is (for example
    ``identity`` or ``camera_guide``). It is provenance only: it is never sent
    to a provider and does not change the request identity. The order of
    inputs is kept everywhere.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: bytes = Field(repr=False, min_length=1, max_length=MAX_IMAGE_BYTES)
    media_type: InputMediaType
    role: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$"
    )

    @model_validator(mode="after")
    def _is_the_declared_image(self) -> ImageInput:
        if sniff_media_type(self.content) != self.media_type:
            raise ValueError("image bytes do not match the declared media type")
        width, height = image_dimensions(self.content, self.media_type)
        if not 0 < width * height <= MAX_INPUT_PIXELS:
            raise ValueError("image exceeds the 40 megapixel input bound")
        return self

    @property
    def sha256(self) -> str:
        return sha256_hex(self.content)

    @classmethod
    def from_path(cls, path: str | Path, *, role: str | None = None) -> ImageInput:
        content = Path(path).read_bytes()
        media_type = sniff_media_type(content)
        if media_type is None:
            raise ValueError("file is not a png, jpeg or webp image")
        return cls(content=content, media_type=media_type, role=role)

    def provenance(self) -> InputProvenance:
        return InputProvenance(
            sha256=self.sha256, media_type=self.media_type, role=self.role
        )


class InputProvenance(BaseModel):
    """What went in, in order: never the bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: InputMediaType
    role: str | None = None


class BaseImageRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    #: Names this request among identical ones. The same request with the same
    #: occurrence is the same image (an exact replay on the hosted profile); a
    #: new variant needs a new occurrence.
    occurrence: Occurrence = "1"

    def input_images(self) -> tuple[ImageInput, ...]:
        raise NotImplementedError

    def arguments(self) -> dict[str, object]:
        """Wire arguments without inputs, which each profile adds its own way."""
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            exclude={"kind", "occurrence", *_INPUTS},
        )

    def idempotency_key(self) -> str:
        """Stable across processes: the operation, arguments, inputs and occurrence."""
        material = json.dumps(
            {
                "operation": OPERATIONS[self.kind],
                "arguments": self.arguments(),
                "inputs": [item.sha256 for item in self.input_images()],
                "occurrence": self.occurrence,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "zi-" + sha256_hex(material.encode())


_INPUTS = {"inputs", "input"}


class GeminiGenerate(BaseImageRequest):
    """Nano Banana: text to image, or edit from up to six references."""

    kind: Literal["gemini.generate"] = "gemini.generate"
    model: GeminiModel = "gemini-3.1-flash-image"
    prompt: str = Field(min_length=1, max_length=16_000)
    inputs: tuple[ImageInput, ...] = Field(default=(), max_length=MAX_GEMINI_INPUTS)
    aspect_ratio: AspectRatio = "1:1"
    #: Only sizes verified live (Broker contract 1.3.0 draft 7 §3): 1K for
    #: both models, and 4K for gemini-3.1-flash-image (ZBS's thumbnails).
    image_size: Literal["1K", "4K"] = "1K"
    #: JPEG only: gemini-3.1-flash-image refuses png output (HTTP 400, seen
    #: live by DuckTyper on 2026-10-10). Another type is added only once a
    #: live run shows it.
    output_media_type: Literal["image/jpeg"] = Field(
        default="image/jpeg", serialization_alias="output_mime_type"
    )

    def input_images(self) -> tuple[ImageInput, ...]:
        return self.inputs

    @model_validator(mode="after")
    def _size_is_verified_for_the_model(self) -> GeminiGenerate:
        if self.image_size == "4K" and self.model != "gemini-3.1-flash-image":
            raise ValueError("4K is verified only for gemini-3.1-flash-image")
        return self


class RecraftColor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rgb: tuple[
        Annotated[int, Field(ge=0, le=255)],
        Annotated[int, Field(ge=0, le=255)],
        Annotated[int, Field(ge=0, le=255)],
    ]
    weight: float | None = Field(default=None, ge=0.0, le=1.0)


class RecraftControls(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    colors: tuple[RecraftColor, ...] = Field(min_length=1, max_length=5)


class RecraftGenerate(BaseImageRequest):
    """Recraft generation. Each model takes only its own arguments (draft 7 §3).

    ``recraftv3``: ``style``, ``negative_prompt``, ``size``, ``random_seed``.
    ``recraftv4_1``: ``size`` 1344x768 and ``image_format`` png (both
    required), ``random_seed`` and ``controls``; never ``style``.
    """

    kind: Literal["recraft.generate"] = "recraft.generate"
    model: RecraftGenerateModel = "recraftv3"
    prompt: str = Field(min_length=1)
    style: StyleToken | None = None
    negative_prompt: str | None = Field(default=None, max_length=1000)
    size: str | None = Field(default=None, pattern=r"^[1-9][0-9]{2,3}x[1-9][0-9]{2,3}$")
    random_seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    image_format: Literal["png"] | None = None
    controls: RecraftControls | None = None

    def input_images(self) -> tuple[ImageInput, ...]:
        return ()

    @model_validator(mode="after")
    def _arguments_fit_the_model(self) -> RecraftGenerate:
        _check_recraft_prompt(self.prompt)
        if self.model == "recraftv3":
            if self.image_format is not None or self.controls is not None:
                raise ValueError("recraftv3 takes no image_format or controls")
            return self
        if self.style is not None or self.negative_prompt is not None:
            raise ValueError("recraftv4_1 takes no style or negative_prompt")
        if self.size != "1344x768" or self.image_format != "png":
            raise ValueError("recraftv4_1 needs size 1344x768 and image_format png")
        if self.random_seed is not None and self.random_seed > 2_147_483_647:
            raise ValueError("recraftv4_1 seeds run from 0 to 2147483647")
        return self


class RecraftImageToImage(BaseImageRequest):
    kind: Literal["recraft.image_to_image"] = "recraft.image_to_image"
    model: RecraftModel = "recraftv3"
    input: ImageInput
    prompt: str = Field(min_length=1)
    strength: float = Field(ge=0.0, le=1.0)
    style: StyleToken | None = None
    negative_prompt: str | None = Field(default=None, max_length=1000)
    random_seed: int | None = Field(default=None, ge=0, le=2**32 - 1)

    def input_images(self) -> tuple[ImageInput, ...]:
        return (self.input,)

    @model_validator(mode="after")
    def _prompt_fits(self) -> RecraftImageToImage:
        _check_recraft_prompt(self.prompt)
        return self


class RecraftRemoveBackground(BaseImageRequest):
    kind: Literal["recraft.remove_background"] = "recraft.remove_background"
    input: ImageInput

    def input_images(self) -> tuple[ImageInput, ...]:
        return (self.input,)

    @model_validator(mode="after")
    def _recraft_accepts_it(self) -> RecraftRemoveBackground:
        if self.input.media_type not in ("image/png", "image/webp"):
            raise ValueError("Recraft removes backgrounds from png or webp only")
        if len(self.input.content) > MAX_REMOVE_BACKGROUND_BYTES:
            raise ValueError("Recraft removes backgrounds from at most 5,000,000 bytes")
        return self


class RecraftCrispUpscale(BaseImageRequest):
    """Recraft crisp upscale: one image in, png out (1.3.0 draft 7)."""

    kind: Literal["recraft.crisp_upscale"] = "recraft.crisp_upscale"
    input: ImageInput

    def input_images(self) -> tuple[ImageInput, ...]:
        return (self.input,)

    @model_validator(mode="after")
    def _recraft_accepts_it(self) -> RecraftCrispUpscale:
        if len(self.input.content) > MAX_REMOVE_BACKGROUND_BYTES:
            raise ValueError("Recraft upscales at most 5,000,000 bytes")
        return self


class RecraftVectorize(BaseImageRequest):
    kind: Literal["recraft.vectorize"] = "recraft.vectorize"
    input: ImageInput

    def input_images(self) -> tuple[ImageInput, ...]:
        return (self.input,)


def _check_recraft_prompt(prompt: str) -> None:
    if len(prompt.encode()) > 1000:
        raise ValueError("Recraft prompts are at most 1000 bytes")


ImageRequest = Annotated[
    GeminiGenerate
    | RecraftGenerate
    | RecraftImageToImage
    | RecraftRemoveBackground
    | RecraftCrispUpscale
    | RecraftVectorize,
    Field(discriminator="kind"),
]

#: Each request kind's Broker operation (proposed contract 1.3.0).
OPERATIONS: Final[dict[str, str]] = {
    "gemini.generate": "gemini.image.generate",
    "recraft.generate": "recraft.image.generate",
    "recraft.image_to_image": "recraft.image.image_to_image",
    "recraft.remove_background": "recraft.image.remove_background",
    "recraft.crisp_upscale": "recraft.image.crisp_upscale",
    "recraft.vectorize": "recraft.image.vectorize",
}
CREDITS_OPERATION: Final = "recraft.account.read"


def provider_of(request: BaseImageRequest) -> Provider:
    return "gemini" if request.kind.startswith("gemini.") else "recraft"


class Cost(BaseModel):
    """What the call cost, in the provider's unit, as the Broker settled it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    unit: str = Field(min_length=1, max_length=64)
    reserved: float | None = None
    settled: float | None = None


class GeneratedImage(BaseModel):
    """One produced image and the facts about the call that made it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: bytes = Field(repr=False)
    media_type: OutputMediaType
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Provider
    operation: str
    profile: Literal["local", "hosted"]
    model: str | None = None
    provider_image_id: str | None = None
    cost: Cost | None = None
    execution_id: str | None = None
    #: True when the hosted Broker served a stored outcome, not a fresh call.
    replayed: bool = False
    #: The request's stable identity (its idempotency key): the same request
    #: and occurrence always have the same key.
    request_key: str | None = None
    #: The inputs, in the order they were sent, with their roles.
    inputs: tuple[InputProvenance, ...] = ()

    @model_validator(mode="after")
    def _digest_matches(self) -> GeneratedImage:
        if sha256_hex(self.content) != self.sha256:
            raise ValueError("image digest does not match its bytes")
        return self

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.write_bytes(self.content)
        return destination


class CreditBalance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["recraft"] = "recraft"
    credits: float


_SVG_FORBIDDEN = re.compile(
    rb"<\s*(image|script|foreignObject|iframe|object|embed)\b|\bon[a-z]+\s*=|javascript:",
    re.IGNORECASE,
)
_SVG_SHAPES = re.compile(rb"<\s*(path|polygon|polyline|rect|circle|ellipse)\b")


def check_vector(content: bytes) -> None:
    """An SVG is active content: accept only a genuine, inert vector.

    It must be an ``<svg>`` with at least one shape, and carry no embedded
    raster, script, event handler or foreign content.
    """
    head = content.lstrip()[:512].lower()
    if (
        not (head.startswith(b"<svg") or head.startswith(b"<?xml"))
        or b"<svg" not in head
    ):
        raise ImagingError("invalid_response", "vectorize did not return an SVG")
    if _SVG_FORBIDDEN.search(content):
        raise ImagingError(
            "invalid_response", "the SVG embeds a raster or active content"
        )
    if not _SVG_SHAPES.search(content):
        raise ImagingError("invalid_response", "the SVG has no vector shapes")


def check_output(content: bytes, media_type: str) -> OutputMediaType:
    """The produced bytes are what they claim, within the hosted bound."""
    if not 0 < len(content) <= MAX_IMAGE_BYTES:
        raise ImagingError("invalid_response", "the image is empty or too large")
    if media_type == "image/svg+xml":
        check_vector(content)
        return "image/svg+xml"
    sniffed = sniff_media_type(content)
    if sniffed is None or sniffed != media_type:
        raise ImagingError(
            "invalid_response", "the image bytes do not match their media type"
        )
    return sniffed
