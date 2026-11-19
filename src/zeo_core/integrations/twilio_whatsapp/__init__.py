"""Twilio WhatsApp channel: signed inbound webhook, outbound send, test double.

Flow: Twilio POSTs a webhook to the public URL ZEOconnect routes; the host calls
`parse_inbound` (signature checked) to get a `ChannelInbound`; the bot decides;
the host calls `TwilioWhatsAppClient.send` for the reply and keeps the
`ChannelReceipt`. Credentials arrive as `TwilioCredentials` from ZEOconnect
custody; this package reads no environment variable and no .env.
"""

from __future__ import annotations

from .client import TwilioWhatsAppClient
from .inbound import WebhookRejected, parse_inbound
from .models import (
    ChannelInbound,
    ChannelReceipt,
    SendWhatsAppRequest,
    TwilioCredentials,
    from_whatsapp_address,
    to_whatsapp_address,
)
from .signature import compute_signature, verify_signature
from .transport import TwilioAPIError

__all__ = [
    "ChannelInbound",
    "ChannelReceipt",
    "SendWhatsAppRequest",
    "TwilioAPIError",
    "TwilioCredentials",
    "TwilioWhatsAppClient",
    "WebhookRejected",
    "compute_signature",
    "from_whatsapp_address",
    "parse_inbound",
    "to_whatsapp_address",
    "verify_signature",
]
