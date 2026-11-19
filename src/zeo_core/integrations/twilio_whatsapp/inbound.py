"""Verify and parse one Twilio WhatsApp webhook into a `ChannelInbound`."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import SecretStr

from .models import ChannelInbound, from_whatsapp_address
from .signature import verify_signature


class WebhookRejected(ValueError):
    """The webhook is not admitted. The message never echoes the body."""


def parse_inbound(
    token: SecretStr,
    *,
    url: str,
    params: Mapping[str, str],
    signature: str | None,
) -> ChannelInbound:
    if not verify_signature(token, url, params, signature):
        raise WebhookRejected("Twilio signature did not match")
    sid = params.get("MessageSid") or params.get("SmsSid")
    if not sid:
        raise WebhookRejected("Webhook has no MessageSid")
    try:
        sender = from_whatsapp_address(params.get("From", ""))
        recipient = from_whatsapp_address(params.get("To", ""))
    except ValueError:
        raise WebhookRejected("Webhook is not a WhatsApp message") from None
    try:
        count = int(params.get("NumMedia", "0") or "0")
    except ValueError:
        raise WebhookRejected("NumMedia is not a number") from None
    media = tuple(
        params[f"MediaUrl{i}"] for i in range(count) if f"MediaUrl{i}" in params
    )
    return ChannelInbound(
        event_id=sid,
        sender_id=sender,
        recipient_id=recipient,
        conversation_id=sender,
        text=params.get("Body", ""),
        profile_name=params.get("ProfileName") or None,
        media_count=count,
        media_urls=media,
        button_payload=params.get("ButtonPayload") or None,
        button_text=params.get("ButtonText") or None,
    )
