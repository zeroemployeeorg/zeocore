"""Header-only image bytes: enough for signature and dimension checks."""

from __future__ import annotations

import struct


def png(width: int = 4, height: int = 4, tail: bytes = b"") -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + header
        + b"\0" * 4
        + tail
    )


def jpeg(width: int = 4, height: int = 4) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
    sof0 = (
        b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def webp_vp8x(width: int, height: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\0" * 4
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + bits.to_bytes(4, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def webp_vp8(width: int, height: int) -> bytes:
    frame = b"\0\0\0" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    body = b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
    b'<path d="M0 0h4v4z"/></svg>'
)
