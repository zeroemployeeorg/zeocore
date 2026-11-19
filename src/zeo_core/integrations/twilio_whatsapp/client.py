"""Send one WhatsApp message through Twilio."""

from __future__ import annotations

import httpx

from .models import (
    ChannelReceipt,
    SendWhatsAppRequest,
    TwilioCredentials,
    to_whatsapp_address,
)
from .transport import TwilioAPIError, TwilioTransport


class TwilioWhatsAppClient:
    def __init__(
        self,
        credentials: TwilioCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._transport = TwilioTransport(credentials, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._transport.close()

    def send(self, request: SendWhatsAppRequest) -> ChannelReceipt:
        """Send once. Never retried; see `TwilioAPIError.effect_unknown`."""
        form: dict[str, str] = {"To": to_whatsapp_address(request.to)}
        if request.from_number is not None:
            form["From"] = to_whatsapp_address(request.from_number)
        if request.messaging_service_sid is not None:
            form["MessagingServiceSid"] = request.messaging_service_sid
        if request.body is not None:
            form["Body"] = request.body
        if request.content_sid is not None:
            form["ContentSid"] = request.content_sid
        if request.content_variables is not None:
            form["ContentVariables"] = request.content_variables
        if request.media_url:
            form["MediaUrl"] = request.media_url[0]
        if request.status_callback is not None:
            form["StatusCallback"] = request.status_callback
        payload = self._transport.post_message(form)
        sid = payload.get("sid")
        if not isinstance(sid, str) or not sid:
            raise TwilioAPIError("invalid_response", "Twilio response had no message sid")
        raw_code = payload.get("error_code")
        return ChannelReceipt(
            provider_message_id=sid,
            status=str(payload.get("status") or "unknown"),
            to=request.to,
            error_code=raw_code if isinstance(raw_code, int) else None,
        )
