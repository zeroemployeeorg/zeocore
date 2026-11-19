"""Twilio WhatsApp channel tests: fake transport only, no network, no real token."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from zeo_core.integrations.twilio_whatsapp import (
    SendWhatsAppRequest,
    TwilioAPIError,
    TwilioCredentials,
    TwilioWhatsAppClient,
    WebhookRejected,
    parse_inbound,
    parse_status_callback,
    to_whatsapp_address,
    verify_signature,
)
from zeo_core.integrations.twilio_whatsapp.testing import (
    FAKE_AUTH_TOKEN,
    FakeTwilio,
    fake_credentials,
    signed_webhook,
)

URL = "https://router.example.test/hooks/twilio/whatsapp"
TOKEN = SecretStr(FAKE_AUTH_TOKEN)
SENDER = "whatsapp:+14155550100"
BOT = "whatsapp:+14155238886"
PARAMS = {
    "MessageSid": "SM" + "a" * 32,
    "From": SENDER,
    "To": BOT,
    "Body": "catalog",
    "NumMedia": "0",
    "ProfileName": "Ana",
}


def _request(**over: object) -> SendWhatsAppRequest:
    base: dict[str, object] = {
        "to": "+14155550100",
        "body": "hello",
        "from_number": "+14155238886",
    }
    base.update(over)
    return SendWhatsAppRequest.model_validate(base)


def test_signed_webhook_parses_to_neutral_event() -> None:
    params, sig = signed_webhook(URL, PARAMS)
    event = parse_inbound(TOKEN, url=URL, params=params, signature=sig)
    assert event.event_id == PARAMS["MessageSid"]
    assert event.sender_id == "+14155550100"
    assert event.conversation_id == "+14155550100"
    assert event.text == "catalog"
    assert event.profile_name == "Ana"


def test_bad_or_missing_signature_is_rejected() -> None:
    params, sig = signed_webhook(URL, PARAMS)
    for bad in (None, "", "AAAA", sig[:-2] + "xx"):
        with pytest.raises(WebhookRejected):
            parse_inbound(TOKEN, url=URL, params=params, signature=bad)


def test_signature_binds_url_and_params() -> None:
    params, sig = signed_webhook(URL, PARAMS)
    assert verify_signature(TOKEN, URL, params, sig)
    assert not verify_signature(TOKEN, URL + "x", params, sig)
    assert not verify_signature(TOKEN, URL, {**params, "Body": "other"}, sig)
    assert not verify_signature(SecretStr("another"), URL, params, sig)


def test_non_whatsapp_and_missing_sid_rejected() -> None:
    sms = {**PARAMS, "From": "+14155550100"}
    params, sig = signed_webhook(URL, sms)
    with pytest.raises(WebhookRejected):
        parse_inbound(TOKEN, url=URL, params=params, signature=sig)
    nosid = {k: v for k, v in PARAMS.items() if k != "MessageSid"}
    params, sig = signed_webhook(URL, nosid)
    with pytest.raises(WebhookRejected):
        parse_inbound(TOKEN, url=URL, params=params, signature=sig)


def test_button_and_media_fields() -> None:
    p = {
        **PARAMS,
        "NumMedia": "1",
        "MediaUrl0": "https://m.example.test/a.jpg",
        "ButtonPayload": "confirm",
        "ButtonText": "Confirm",
    }
    params, sig = signed_webhook(URL, p)
    event = parse_inbound(TOKEN, url=URL, params=params, signature=sig)
    assert event.media_urls == ("https://m.example.test/a.jpg",)
    assert event.button_payload == "confirm"


def test_send_posts_once_with_whatsapp_addresses_and_basic_auth() -> None:
    fake = FakeTwilio()
    client = TwilioWhatsAppClient(fake_credentials(), transport=fake.transport)
    receipt = client.send(_request())
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req.url.path.endswith("/Messages.json")
    body = req.content.decode()
    assert "To=whatsapp%3A%2B14155550100" in body
    assert "From=whatsapp%3A%2B14155238886" in body
    assert "Body=hello" in body
    assert req.headers["authorization"].startswith("Basic ")
    assert receipt.provider_message_id.startswith("SM")
    assert receipt.status == "queued"


def test_timeout_is_unknown_effect_and_not_retried() -> None:
    fake = FakeTwilio()
    fake.raise_timeout = True
    client = TwilioWhatsAppClient(fake_credentials(), transport=fake.transport)
    with pytest.raises(TwilioAPIError) as info:
        client.send(_request())
    assert info.value.effect_unknown is True
    assert len(fake.requests) == 1


def test_api_error_is_safe_and_not_unknown() -> None:
    fake = FakeTwilio(status=400, body={"code": 63016, "message": "secret detail"})
    client = TwilioWhatsAppClient(fake_credentials(), transport=fake.transport)
    with pytest.raises(TwilioAPIError) as info:
        client.send(_request())
    err = info.value
    assert err.code == "bad_request"
    assert err.twilio_code == 63016
    assert err.effect_unknown is False
    assert FAKE_AUTH_TOKEN not in str(err)
    assert "secret detail" not in str(err)


def test_request_validation() -> None:
    with pytest.raises(ValidationError):
        _request(body=None)
    with pytest.raises(ValidationError):
        _request(content_sid="HX" + "0" * 32)  # body and content_sid both set
    with pytest.raises(ValidationError):
        _request(to="14155550100")
    with pytest.raises(ValidationError):
        _request(from_number=None)
    with pytest.raises(ValidationError):
        _request(media_url=("http://insecure.example.test/a.jpg",))
    assert _request(body=None, content_sid="HX" + "0" * 32).content_sid
    assert to_whatsapp_address("whatsapp:+14155550100") == SENDER


def test_credentials_validation_and_no_secret_in_repr() -> None:
    with pytest.raises(ValidationError):
        TwilioCredentials(account_sid="bad", auth_token=SecretStr("x"))
    creds = fake_credentials()
    assert FAKE_AUTH_TOKEN not in repr(creds)
    assert FAKE_AUTH_TOKEN not in creds.model_dump_json()


def test_reply_binding_and_receipt_time() -> None:
    p = {**PARAMS, "OriginalRepliedMessageSid": "SM" + "b" * 32}
    params, sig = signed_webhook(URL, p)
    event = parse_inbound(TOKEN, url=URL, params=params, signature=sig)
    assert event.reply_to_message_id == "SM" + "b" * 32
    plain, sig2 = signed_webhook(URL, PARAMS)
    assert parse_inbound(TOKEN, url=URL, params=plain, signature=sig2).reply_to_message_id is None

    fake = FakeTwilio(
        body={
            "sid": "SM" + "c" * 32,
            "status": "queued",
            "date_created": "Thu, 19 Nov 2026 05:30:00 +0000",
        }
    )
    receipt = TwilioWhatsAppClient(fake_credentials(), transport=fake.transport).send(
        _request()
    )
    assert receipt.created_at is not None
    assert receipt.created_at.isoformat() == "2026-11-19T05:30:00+00:00"
    bare = FakeTwilio()
    assert (
        TwilioWhatsAppClient(fake_credentials(), transport=bare.transport)
        .send(_request())
        .created_at
        is None
    )


def test_body_over_twilio_limit_is_refused_not_truncated() -> None:
    with pytest.raises(ValidationError):
        _request(body="x" * 1601)


def test_status_callback_verified_and_parsed() -> None:
    cb = {
        "MessageSid": "SM" + "d" * 32,
        "MessageStatus": "delivered",
        "To": SENDER,
    }
    params, sig = signed_webhook(URL, cb)
    receipt = parse_status_callback(TOKEN, url=URL, params=params, signature=sig)
    assert receipt.status == "delivered"
    assert receipt.to == "+14155550100"
    with pytest.raises(WebhookRejected):
        parse_status_callback(TOKEN, url=URL, params=params, signature="bad")
