"""Bounded offline OCI carrier verification.

No extraction, layer decompression, network, installation or deployment authority.
The caller must separately authenticate the release manifest and build receipt.
Digest equality alone never establishes provenance or permission to load an image.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Never, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from zeo_core.adapters.runtime_host.canonical import ProtocolError, parse_json

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_BLOB = re.compile(r"blobs/sha256/[0-9a-f]{64}")
_JSON_LIMIT = 1024 * 1024


class _Reader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class OciCarrier(BaseModel):
    """Trusted expectations supplied by an independently admitted release."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_bytes: int = Field(ge=1024, le=2_000_000_000)
    max_entries: int = Field(ge=3, le=4096)
    manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    platform: str = Field(pattern=r"^linux/[a-z0-9_]+$")


class OciVerificationError(ValueError):
    """An inert carrier is invalid; nothing was loaded."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedOciCarrier:
    """Byte/structure verification only, not signed or admitted provenance."""

    manifest_digest: str
    config_digest: str
    layer_digests: tuple[str, ...]
    archive_bytes: int
    entries: int


def _refuse(code: str = "archive_unsafe") -> Never:
    raise OciVerificationError(code)


def _octal(value: bytes) -> int:
    raw = value.rstrip(b"\0 ").lstrip(b" ")
    if not raw or any(c not in b"01234567" for c in raw):
        _refuse()
    return int(raw, 8)


def _chunks(stream: _Reader, size: int) -> Iterator[bytes]:
    while size:
        block = stream.read(min(size, 65536))
        if not block:
            _refuse("input_mismatch")
        size -= len(block)
        yield block


def _object(data: bytes) -> dict[str, object]:
    try:
        value = parse_json(data)
    except (ProtocolError, ValueError, RecursionError):
        raise OciVerificationError("archive_unsafe") from None
    if not isinstance(value, dict):
        _refuse()
    return cast(dict[str, object], value)


def _header(header: bytes) -> tuple[str, int]:
    # Reject extension headers, links, sparse files and paths before reading data.
    if header[156:157] not in (b"0", b"\0") or any(header[345:500]):
        _refuse()
    if _octal(header[148:156]) != sum(header[:148] + b" " * 8 + header[156:]):
        _refuse()
    try:
        name = header[:100].split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError:
        raise OciVerificationError("archive_unsafe") from None
    if name not in ("oci-layout", "index.json") and not _BLOB.fullmatch(name):
        _refuse()
    return name, _octal(header[124:136])


def _scan(
    stream: _Reader, expected: OciCarrier
) -> tuple[dict[str, int], dict[str, bytes]]:
    sizes: dict[str, int] = {}
    metadata: dict[str, bytes] = {}
    needed = {
        "oci-layout",
        "index.json",
        "blobs/" + expected.manifest_digest.replace(":", "/"),
        "blobs/" + expected.config_digest.replace(":", "/"),
    }
    while True:
        header = stream.read(512)
        if len(header) != 512:
            _refuse()
        if header == bytes(512):
            if stream.read(512) != bytes(512):
                _refuse()
            while block := stream.read(65536):
                if any(block):
                    _refuse()
            return sizes, metadata
        name, size = _header(header)
        if name in sizes or len(sizes) >= expected.max_entries:
            _refuse()
        if size > expected.archive_bytes or (name in needed and size > _JSON_LIMIT):
            _refuse("artifact_too_large")
        sizes[name] = size
        saved = _blob(stream, name, size, keep=name in needed)
        if name in needed:
            metadata[name] = saved


def _blob(stream: _Reader, name: str, size: int, *, keep: bool) -> bytes:
    digest = hashlib.sha256()
    saved = bytearray()
    for block in _chunks(stream, size):
        digest.update(block)
        if keep:
            saved.extend(block)
    if name.startswith("blobs/") and digest.hexdigest() != name.rsplit("/", 1)[1]:
        _refuse("input_mismatch")
    padding = (-size) % 512
    if stream.read(padding) != bytes(padding):
        _refuse()
    return bytes(saved)


def _descriptor(value: object, sizes: dict[str, int]) -> str:
    if not isinstance(value, dict):
        _refuse()
    digest, size = value.get("digest"), value.get("size")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        _refuse("input_mismatch")
    if type(size) is not int or size < 0:
        _refuse("input_mismatch")
    if sizes.get("blobs/" + digest.replace(":", "/")) != size:
        _refuse("input_mismatch")
    return digest


def verify_oci_carrier(stream: _Reader, expected: OciCarrier) -> VerifiedOciCarrier:
    """Read at most the declared size plus one byte, freeze bytes, verify offline.

    Only an uncompressed regular-file OCI layout tar is accepted. Bounded spooling
    prevents a large image from residing in memory and avoids re-opening a mutable
    caller path between hashing and verification. Leaves the caller's stream open.
    """
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
        digest = hashlib.sha256()
        total = 0
        while block := stream.read(min(65536, expected.archive_bytes + 1 - total)):
            total += len(block)
            if total > expected.archive_bytes:
                _refuse("artifact_too_large")
            digest.update(block)
            spool.write(block)
        if (
            total != expected.archive_bytes
            or digest.hexdigest() != expected.archive_sha256
        ):
            _refuse("input_mismatch")
        spool.seek(0)
        sizes, metadata = _scan(spool, expected)
    return _structure(sizes, metadata, expected, total)


def _structure(
    sizes: dict[str, int], metadata: dict[str, bytes], expected: OciCarrier, total: int
) -> VerifiedOciCarrier:
    try:
        layout = _object(metadata["oci-layout"])
        index = _object(metadata["index.json"])
        manifest = _object(
            metadata["blobs/" + expected.manifest_digest.replace(":", "/")]
        )
        config = _object(metadata["blobs/" + expected.config_digest.replace(":", "/")])
    except KeyError:
        raise OciVerificationError("input_mismatch") from None
    if (
        layout != {"imageLayoutVersion": "1.0.0"}
        or index.get("schemaVersion") != 2
        or manifest.get("schemaVersion") != 2
    ):
        _refuse()
    descriptors = index.get("manifests")
    if not isinstance(descriptors, list) or len(descriptors) != 1:
        _refuse()
    descriptor = descriptors[0]
    if _descriptor(descriptor, sizes) != expected.manifest_digest:
        _refuse("input_mismatch")
    os_name, arch = expected.platform.split("/")
    platform = descriptor.get("platform")
    if (
        not isinstance(platform, dict)
        or platform.get("os") != os_name
        or platform.get("architecture") != arch
    ):
        _refuse("input_mismatch")
    if config.get("os") != os_name or config.get("architecture") != arch:
        _refuse("input_mismatch")
    if _descriptor(manifest.get("config"), sizes) != expected.config_digest:
        _refuse("input_mismatch")
    layers = manifest.get("layers")
    if not isinstance(layers, list):
        _refuse()
    digests = tuple(_descriptor(layer, sizes) for layer in layers)
    return VerifiedOciCarrier(
        expected.manifest_digest, expected.config_digest, digests, total, len(sizes)
    )
