from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from courtee.normalization import (
    EmailPayload, WhatsAppPayload, normalize_email, normalize_mailbox,
    normalize_phone, normalize_whatsapp,
)

RECEIPT = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("value, expected", [
    ("0661234567", "+212661234567"),
    ("212661234567", "+212661234567"),
    ("+212 6 61 23 45 67", "+212661234567"),
    ("+33 6 12 34 56 78", "+33612345678"),
    ("0033612345678", "+33612345678"),
])
def test_phone_formats(value, expected):
    assert normalize_phone(value) == expected


@pytest.mark.parametrize("value", ["", "123", "+00012345678", "call 0661234567", "++212661234567"])
def test_invalid_phone_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_phone(value)


def test_whatsapp_uses_supplied_timestamp_and_preserves_content(load_fixture):
    payload = load_fixture("A")
    payload["provider_metadata"] = {"test": True}
    message = normalize_whatsapp(WhatsAppPayload.model_validate(payload), RECEIPT)
    assert message.channel == "whatsapp"
    assert message.sender == "+212661234567"
    assert message.external_id == "wamid.IN-A"
    assert message.date == datetime.fromtimestamp(1759395600, timezone.utc).isoformat()
    assert message.received_at == RECEIPT.isoformat()
    assert message.content == "Des nouvelles ?"
    assert message.payload["provider_metadata"] == {"test": True}
    assert message.payload["from"] == "212661234567"


def test_whatsapp_uses_inbound_context_id(load_fixture):
    payload = load_fixture("B")
    message = normalize_whatsapp(WhatsAppPayload.model_validate(payload), RECEIPT)
    assert message.context_id == "wamid.OUT-2041"
    payload["context"] = {"message_id": "wamid.OUT-2041"}
    message = normalize_whatsapp(WhatsAppPayload.model_validate(payload), RECEIPT)
    assert message.context_id is None


@pytest.mark.parametrize("kind", ["document", "image"])
def test_whatsapp_media_preserves_metadata_and_caption(kind, load_fixture):
    payload = load_fixture("A")
    payload.pop("text")
    payload.update({"type": kind, kind: {
        "id": "media-123", "mime_type": "application/pdf", "filename": "attestation_salaire.pdf",
        "caption": "Pour D-1234", "sha256": "simulated-digest",
    }})
    message = normalize_whatsapp(WhatsAppPayload.model_validate(payload), RECEIPT)
    assert message.content == "Pour D-1234"
    assert message.attachments[0].external_id == "media-123"
    assert message.attachments[0].filename == "attestation_salaire.pdf"
    assert message.payload[kind]["sha256"] == "simulated-digest"


@pytest.mark.parametrize("kind", ["text", "document", "image"])
def test_whatsapp_requires_content_object_for_its_type(kind, load_fixture):
    payload = load_fixture("A")
    payload.pop("text")
    payload["type"] = kind
    with pytest.raises(ValueError):
        normalize_whatsapp(WhatsAppPayload.model_validate(payload), RECEIPT)


@pytest.mark.parametrize("field, value", [
    ("timestamp", "not-a-date"), ("timestamp", "9999999999999999999999"),
    ("timestamp", True), ("id", " "), ("type", "unsupported"),
])
def test_invalid_whatsapp_fields_are_rejected(field, value, load_fixture):
    payload = load_fixture("A")
    payload[field] = value
    with pytest.raises((ValueError, ValidationError)):
        normalize_whatsapp(WhatsAppPayload.model_validate(payload), RECEIPT)


def test_email_normalizes_mailboxes_headers_and_receipt_time(load_fixture):
    payload = load_fixture("H")
    payload.update({"from": "Conseiller <K.BENNANI@CIHBANK.MA>",
                    "to": ["Courtee <D-1234@DOSSIERS.COURTEE.AI>"],
                    "cc": ["Contact <CONTACT@COURTEE.AI>"]})
    payload["headers"] = {"mEsSaGe-Id": "<CaseSensitive@CIH.MA>",
                          "iN-rEpLy-To": "<out-1234@dossiers.courtee.ai>"}
    message = normalize_email(EmailPayload.model_validate(payload), RECEIPT)
    assert message.sender == "k.bennani@cihbank.ma"
    assert message.recipients == ("d-1234@dossiers.courtee.ai", "contact@courtee.ai")
    assert message.external_id == "<CaseSensitive@CIH.MA>"
    assert message.headers["in-reply-to"] == "<out-1234@dossiers.courtee.ai>"
    assert message.date == RECEIPT.isoformat()
    assert message.received_at == RECEIPT.isoformat()
    assert message.payload["headers"]["mEsSaGe-Id"] == "<CaseSensitive@CIH.MA>"


@pytest.mark.parametrize("timestamp, expected", [
    ("1759395600", "2025-10-02T09:00:00+00:00"),
    (1759395600, "2025-10-02T09:00:00+00:00"),
    ("2026-10-02T13:00:00+01:00", "2026-10-02T12:00:00+00:00"),
    ("2026-10-02T12:00:00Z", "2026-10-02T12:00:00+00:00"),
    ("2026-10-02T12:00:00", "2026-10-02T12:00:00+00:00"),
])
def test_email_event_time(timestamp, expected, load_fixture):
    payload = load_fixture("G")
    payload["timestamp"] = timestamp
    message = normalize_email(EmailPayload.model_validate(payload), RECEIPT)
    assert message.date == expected


def test_email_retains_attachment_metadata_and_original_text(load_fixture):
    payload = load_fixture("G")
    payload["text"] = "Bonjour\n\n--\nSignature\n> Quoted text"
    payload["attachments"] = [{"filename": "salaire.pdf", "mime_type": "application/pdf"},
                              {"id": "bank-attachment", "filename": "accord.pdf"}]
    message = normalize_email(EmailPayload.model_validate(payload), RECEIPT)
    assert message.content == payload["text"]
    assert message.attachments[0].external_id == "attachment-1"
    assert message.attachments[0].mime_type == "application/pdf"
    assert message.attachments[1].external_id == "bank-attachment"


@pytest.mark.parametrize("headers", [{}, {"Message-ID": " "},
                                        {"Message-ID": "<one@test>", "message-id": "<two@test>"}])
def test_missing_or_duplicate_message_id_is_rejected(headers, load_fixture):
    payload = load_fixture("G")
    payload["headers"] = headers
    with pytest.raises(ValueError):
        normalize_email(EmailPayload.model_validate(payload), RECEIPT)


@pytest.mark.parametrize("value", ["", "no-address", "a@example.test, b@example.test", "a@"])
def test_invalid_or_multiple_mailboxes_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_mailbox(value)
