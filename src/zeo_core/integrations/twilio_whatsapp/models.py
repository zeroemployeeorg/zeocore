"""Typed shapes for the Twilio WhatsApp channel.

The channel contract is the pair `ChannelInbound` / `ChannelReceipt`: neutral
enough for another channel to reuse, and never carrying a provider secret.
Identity rules (written down so a host does not invent them):

- `event_id` is the Twilio `MessageSid`. It is the only dedup key. Twilio may
  redeliver a webhook; a host that has seen the `event_id` must not act twice.
- `sender_id` is the WhatsApp number in E.164 form, as an opaque string. It is
  not numeric and never converted to an integer.
- `conversation_id` is `sender_id` for a one-to-one chat.
- Ordering is the host's receive sequence; Twilio gives no total order, and the
  provider timestamp is not a watermark.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
_ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
_WA_PREFIX = "whatsapp:"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def to_whatsapp_address(number: str) -> str:
    """Return `whatsapp:+E164`, accepting the number with or without the prefix."""
    bare = number[len(_WA_PREFIX) :] if number.startswith(_WA_PREFIX) else number
    if not _E164.match(bare):
        raise ValueError("WhatsApp number must be E.164, for example +14155550100")
    return f"{_WA_PREFIX}{bare}"


def from_whatsapp_address(address: str) -> str:
    if not address.startswith(_WA_PREFIX):
        raise ValueError("Not a WhatsApp address")
    bare = address[len(_WA_PREFIX) :]
    if not _E164.match(bare):
        raise ValueError("WhatsApp number must be E.164")
    return bare


class TwilioCredentials(_Model):
    """Delivered by ZEOconnect custody; never read from a .env by this module."""

    account_sid: str
    auth_token: SecretStr

    @model_validator(mode="after")
    def _check(self) -> TwilioCredentials:
        if not _ACCOUNT_SID.match(self.account_sid):
            raise ValueError("account_sid must look like AC followed by 32 hex digits")
        if not self.auth_token.get_secret_value().strip():
            raise ValueError("auth_token is required")
        return self


class ChannelInbound(_Model):
    channel: Literal["whatsapp"] = "whatsapp"
    event_id: str = Field(min_length=1)
    sender_id: str
    recipient_id: str
    conversation_id: str
    text: str = ""
    profile_name: str | None = None
    media_count: int = Field(default=0, ge=0)
    media_urls: tuple[str, ...] = ()
    #: Set when the user tapped a quick-reply/button; the button payload.
    button_payload: str | None = None
    button_text: str | None = None


class ChannelReceipt(_Model):
    channel: Literal["whatsapp"] = "whatsapp"
    #: Twilio's id for the message we sent; the outbound receipt key.
    provider_message_id: str
    #: queued, accepted, sending, sent, delivered, read, failed, undelivered
    status: str
    to: str
    error_code: int | None = None


class SendWhatsAppRequest(_Model):
    """One outbound message. Free text works inside the 24 hour user session;
    outside it WhatsApp requires an approved template (`content_sid`)."""

    to: str
    body: str | None = Field(default=None, min_length=1, max_length=1600)
    content_sid: str | None = Field(default=None, pattern=r"^HX[0-9a-fA-F]{32}$")
    #: JSON string of template variables, as Twilio expects it.
    content_variables: str | None = None
    media_url: tuple[str, ...] = ()
    #: Sender: a `whatsapp:+E164` number, or a messaging service SID (`MG...`).
    from_number: str | None = None
    messaging_service_sid: str | None = Field(default=None, pattern=r"^MG[0-9a-fA-F]{32}$")
    status_callback: str | None = None

    @model_validator(mode="after")
    def _check(self) -> SendWhatsAppRequest:
        to_whatsapp_address(self.to)
        if (self.body is None) == (self.content_sid is None):
            raise ValueError("Set exactly one of body or content_sid")
        if (self.from_number is None) == (self.messaging_service_sid is None):
            raise ValueError("Set exactly one of from_number or messaging_service_sid")
        if self.from_number is not None:
            to_whatsapp_address(self.from_number)
        if self.content_variables is not None and self.content_sid is None:
            raise ValueError("content_variables needs content_sid")
        for url in self.media_url:
            if not url.startswith("https://"):
                raise ValueError("media_url must start with https://")
        if self.status_callback is not None and not self.status_callback.startswith(
            "https://"
        ):
            raise ValueError("status_callback must start with https://")
        return self
