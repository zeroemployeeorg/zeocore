"""Twilio webhook signature check (X-Twilio-Signature).

Twilio signs the full public URL it called plus every POST parameter, sorted by
name and concatenated as name+value, with HMAC-SHA1 keyed by the account auth
token, then base64. The URL must be exactly what Twilio was configured with
(scheme, host, port, path, query), which behind a router is the router's public
URL, not the internal one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping

from pydantic import SecretStr


def compute_signature(token: SecretStr, url: str, params: Mapping[str, str]) -> str:
    data = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        token.get_secret_value().encode(), data.encode(), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    token: SecretStr,
    url: str,
    params: Mapping[str, str],
    signature: str | None,
) -> bool:
    """True only for a matching signature; a missing header is False."""
    if not signature:
        return False
    return hmac.compare_digest(compute_signature(token, url, params), signature)
