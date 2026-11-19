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
from datetime import datetime
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
    """Delivered by ZEOconnect custody; never read from a .env by this module.

    Sending needs `auth_token` OR the API key pair (`api_key_sid` +
    `api_key_secret`); an API key is preferred when both are present. Webhook
    signature checks need the account `auth_token` and nothing else: Twilio signs
    with the Auth Token, never with an API key secret.
    """

    account_sid: str
    auth_token: SecretStr | None = None
    api_key_sid: str | None = None
    api_key_secret: SecretStr | None = None

    @model_validator(mode="after")
    def _check(self) -> TwilioCredentials:
        if not _ACCOUNT_SID.match(self.account_sid):
            raise ValueError("account_sid must look like AC followed by 32 hex digits")
        if (
            self.auth_token is not None
            and not self.auth_token.get_secret_value().strip()
        ):
            raise ValueError("auth_token must not be blank")
        has_key = self.api_key_sid is not None or self.api_key_secret is not None
        if has_key:
            if self.api_key_sid is None or self.api_key_secret is None:
                raise ValueError("Set api_key_sid and api_key_secret together")
            if not re.match(r"^SK[0-9a-fA-F]{32}$", self.api_key_sid):
                raise ValueError(
                    "api_key_sid must look like SK followed by 32 hex digits"
                )
            if not self.api_key_secret.get_secret_value().strip():
                raise ValueError("api_key_secret must not be blank")
        elif self.auth_token is None:
            raise ValueError("Provide auth_token or an API key pair to send")
        return self

    def basic_auth(self) -> tuple[str, str]:
        if self.api_key_sid is not None and self.api_key_secret is not None:
            return (self.api_key_sid, self.api_key_secret.get_secret_value())
        if self.auth_token is None:  # unreachable: validated at construction
            raise ValueError("No credential to send with")
        return (self.account_sid, self.auth_token.get_secret_value())


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
    #: MessageSid of the earlier message this one quotes (Twilio
    #: `OriginalRepliedMessageSid`), when the user used WhatsApp reply. This is
    #: the only source-supported way to bind a message to a specific earlier
    #: one. Twilio's webhook carries no send time, and a Sid is not sortable, so
    #: a host must not infer "after the review" from arrival order alone.
    reply_to_message_id: str | None = None


class ChannelReceipt(_Model):
    channel: Literal["whatsapp"] = "whatsapp"
    #: Twilio's id for the message we sent; the outbound receipt key.
    provider_message_id: str
    #: queued, accepted, sending, sent, delivered, read, failed, undelivered
    status: str
    to: str
    error_code: int | None = None
    #: Twilio `date_created` for the outbound message, timezone-aware, when the
    #: response carried a parseable value; None otherwise (never invented).
    created_at: datetime | None = None


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
    messaging_service_sid: str | None = Field(
        default=None, pattern=r"^MG[0-9a-fA-F]{32}$"
    )
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
