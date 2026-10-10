"""Image generation and asset creation: Nano Banana (Gemini) and Recraft.

One request vocabulary and one result type over both providers, in the local
profile (your own keys) or the hosted profile (ZEOconnect holds the keys).
The hosted wire follows the proposed Broker contract 1.3.0 and is a draft
until that contract is pinned.
"""

from .hosted import HostedGeminiImages, HostedRecraftImages
from .local import LocalGeminiImages, LocalRecraftImages
from .models import (
    MAX_IMAGE_BYTES,
    MAX_INPUT_PIXELS,
    OPERATIONS,
    BaseImageRequest,
    Cost,
    CreditBalance,
    GeminiGenerate,
    GeneratedImage,
    ImageInput,
    ImageRequest,
    ImagingError,
    InputProvenance,
    RecraftGenerate,
    RecraftImageToImage,
    RecraftRemoveBackground,
    RecraftVectorize,
    check_vector,
)
from .service import ImageBackend, ImagingService, RecraftBackend, build_imaging

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_INPUT_PIXELS",
    "OPERATIONS",
    "BaseImageRequest",
    "Cost",
    "CreditBalance",
    "GeminiGenerate",
    "GeneratedImage",
    "HostedGeminiImages",
    "HostedRecraftImages",
    "ImageBackend",
    "ImageInput",
    "ImageRequest",
    "InputProvenance",
    "ImagingError",
    "ImagingService",
    "LocalGeminiImages",
    "LocalRecraftImages",
    "RecraftBackend",
    "RecraftGenerate",
    "RecraftImageToImage",
    "RecraftRemoveBackground",
    "RecraftVectorize",
    "build_imaging",
    "check_vector",
]
