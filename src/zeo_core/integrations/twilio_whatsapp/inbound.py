"""Verify and parse one Twilio WhatsApp webhook into a `ChannelInbound`."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import SecretStr

from .models import ChannelInbound, ChannelReceipt, from_whatsapp_address
from .signature import verify_signature


class WebhookRejectedError(ValueError):
    """The webhook is not admitted. The message never echoes the body."""


def parse_inbound(
    token: SecretStr,
    *,
    url: str,
    params: Mapping[str, str],
    signature: str | None,
) -> ChannelInbound:
    if not verify_signature(token, url, params, signature):
        raise WebhookRejectedError("Twilio signature did not match")
    sid = params.get("MessageSid") or params.get("SmsSid")
    if not sid:
        raise WebhookRejectedError("Webhook has no MessageSid")
    try:
        sender = from_whatsapp_address(params.get("From", ""))
        recipient = from_whatsapp_address(params.get("To", ""))
    except ValueError:
        raise WebhookRejectedError("Webhook is not a WhatsApp message") from None
    try:
        count = int(params.get("NumMedia", "0") or "0")
    except ValueError:
        raise WebhookRejectedError("NumMedia is not a number") from None
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
        reply_to_message_id=params.get("OriginalRepliedMessageSid") or None,
    )


def parse_status_callback(
    token: SecretStr,
    *,
    url: str,
    params: Mapping[str, str],
    signature: str | None,
) -> ChannelReceipt:
    """Verify and parse a delivery status callback (same signature scheme).

    Delivery evidence is `status` of `delivered` or `read`; `sent`/`queued` is
    not. Callbacks can arrive out of order and more than once: the host keeps
    the first terminal status per `provider_message_id`.
    """
    if not verify_signature(token, url, params, signature):
        raise WebhookRejectedError("Twilio signature did not match")
    sid = params.get("MessageSid") or params.get("SmsSid")
    status = params.get("MessageStatus") or params.get("SmsStatus")
    if not sid or not status:
        raise WebhookRejectedError("Callback has no MessageSid or status")
    try:
        to = from_whatsapp_address(params.get("To", ""))
    except ValueError:
        raise WebhookRejectedError("Callback is not for a WhatsApp message") from None
    raw = params.get("ErrorCode", "")
    return ChannelReceipt(
        provider_message_id=sid,
        status=status,
        to=to,
        error_code=int(raw) if raw.isdigit() else None,
    )
