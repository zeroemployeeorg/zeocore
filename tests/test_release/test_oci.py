"""Adversarial offline carrier tests; no Docker daemon or actuator involved."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest
from pydantic import ValidationError

from zeo_core.release.oci import OciCarrier, OciVerificationError, verify_oci_carrier


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def fixture() -> tuple[list[tuple[str, bytes]], dict[str, object]]:
    layer = b"not decompressed or executed here"
    config = json.dumps({"os": "linux", "architecture": "arm64"}).encode()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "config": {"digest": digest(config), "size": len(config)},
            "layers": [{"digest": digest(layer), "size": len(layer)}],
        }
    ).encode()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": digest(manifest),
                    "size": len(manifest),
                    "platform": {"os": "linux", "architecture": "arm64"},
                }
            ],
        }
    ).encode()
    entries = [("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'), ("index.json", index)]
    entries += [
        ("blobs/" + digest(v).replace(":", "/"), v) for v in (manifest, config, layer)
    ]
    return entries, {
        "manifest_digest": digest(manifest),
        "config_digest": digest(config),
        "platform": "linux/arm64",
        "max_entries": 8,
    }


def archive(entries: list[tuple[str, bytes]], kind: bytes = tarfile.REGTYPE) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.type = kind
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def expectations(data: bytes, fields: dict[str, object]) -> OciCarrier:
    return OciCarrier.model_validate(
        {
            **fields,
            "archive_bytes": len(data),
            "archive_sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def test_verified_bytes_are_not_a_deployment() -> None:
    entries, fields = fixture()
    data = archive(entries)
    stream = io.BytesIO(data)
    result = verify_oci_carrier(stream, expectations(data, fields))
    assert result.manifest_digest == fields["manifest_digest"]
    assert len(result.layer_digests) == 1 and result.entries == 5
    assert not stream.closed


@pytest.mark.parametrize(
    "name",
    ["../escape", "/absolute", "blobs/sha256/not-a-digest", "config.json", "a/b"],
)
def test_unknown_and_unsafe_paths(name: str) -> None:
    entries, fields = fixture()
    data = archive([*entries, (name, b"x")])
    with pytest.raises(OciVerificationError, match="archive_unsafe"):
        verify_oci_carrier(io.BytesIO(data), expectations(data, fields))


@pytest.mark.parametrize(
    "kind",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.DIRTYPE,
        tarfile.CHRTYPE,
        tarfile.FIFOTYPE,
    ],
)
def test_no_links_devices_or_directories(kind: bytes) -> None:
    entries, fields = fixture()
    data = archive(entries, kind)
    with pytest.raises(OciVerificationError):
        verify_oci_carrier(io.BytesIO(data), expectations(data, fields))


def test_duplicates_mismatches_bounds_and_trailing_bytes() -> None:
    entries, fields = fixture()
    cases = [
        archive([*entries, entries[0]]),
        archive([*entries[:-1], (entries[-1][0], b"changed")]),
        archive(entries) + b"hidden",
        archive(entries[:-1]),
    ]
    for data in cases:
        with pytest.raises(OciVerificationError):
            verify_oci_carrier(io.BytesIO(data), expectations(data, fields))
    data = archive(entries)
    for changes in (
        {"platform": "linux/amd64"},
        {"max_entries": 3},
        {"archive_sha256": "0" * 64},
    ):
        with pytest.raises(OciVerificationError):
            verify_oci_carrier(
                io.BytesIO(data), expectations(data, fields).model_copy(update=changes)
            )
    source = io.BytesIO(data + b"extra")
    with pytest.raises(OciVerificationError, match="artifact_too_large"):
        verify_oci_carrier(source, expectations(data, fields))
    assert source.tell() == len(data) + 1


def test_strict_expectations() -> None:
    entries, fields = fixture()
    data = archive(entries)
    with pytest.raises(ValidationError):
        OciCarrier.model_validate(
            {**expectations(data, fields).model_dump(), "max_entries": True}
        )
