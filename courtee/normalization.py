"""Validate simplified webhook objects and normalize their routing fields."""
from datetime import datetime, timezone
from email.utils import getaddresses
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from courtee.domain import Attachment, NormalizedMessage


class PayloadModel(BaseModel):
    # Preserve provider metadata while validating the fields we actually use.
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class TextPayload(PayloadModel):
    body: StrictStr


class ContextPayload(PayloadModel):
    id: StrictStr | None = Field(default=None, min_length=1)


class MediaPayload(PayloadModel):
    id: StrictStr = Field(min_length=1)
    mime_type: StrictStr
    filename: StrictStr = "attachment"
    caption: StrictStr = ""


class WhatsAppPayload(PayloadModel):
    sender: StrictStr = Field(alias="from", min_length=1)
    id: StrictStr = Field(min_length=1, pattern=r"\S")
    timestamp: StrictStr | StrictInt
    type: Literal["text", "document", "image"]
    text: TextPayload | None = None
    context: ContextPayload | None = None
    document: MediaPayload | None = None
    image: MediaPayload | None = None


class EmailAttachment(PayloadModel):
    id: StrictStr | None = Field(default=None, min_length=1)
    filename: StrictStr = Field(min_length=1)
    mime_type: StrictStr = "application/octet-stream"


class EmailPayload(PayloadModel):
    sender: StrictStr = Field(alias="from", min_length=1)
    to: list[StrictStr] = Field(min_length=1)
    cc: list[StrictStr] = Field(default_factory=list)
    subject: StrictStr = ""
    text: StrictStr = ""
    headers: dict[str, StrictStr]
    attachments: list[EmailAttachment] = Field(default_factory=list)
    timestamp: StrictStr | StrictInt | None = None


def normalize_phone(value: str) -> str:
    number = re.sub(r"[\s().-]", "", value)
    if number.startswith("00"):
        number = "+" + number[2:]
    elif number.startswith("0") and len(number) == 10:
        number = "+212" + number[1:]
    elif not number.startswith("+"):
        number = "+" + number
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
        raise ValueError("Invalid international or Moroccan local phone number")
    return number


def normalize_mailbox(value: str) -> str:
    mailboxes = getaddresses([value])
    if len(mailboxes) != 1:
        raise ValueError("Expected one email mailbox")
    address = mailboxes[0][1].strip().lower()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", address):
        raise ValueError("Invalid email mailbox")
    return address


def _receipt_time(receipt: datetime | None) -> datetime:
    value = receipt if receipt is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def event_date(value: str | int | None, receipt: datetime) -> str:
    if value is None:
        return receipt.isoformat()
    try:
        if isinstance(value, int) or re.fullmatch(r"[0-9]+", value):
            result = datetime.fromtimestamp(int(value), timezone.utc)
        else:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("Invalid message timestamp") from error


def normalize_whatsapp(payload: WhatsAppPayload, receipt: datetime | None = None) -> NormalizedMessage:
    receipt = _receipt_time(receipt)
    attachments = ()
    if payload.type == "text":
        if payload.text is None:
            raise ValueError("A text message requires text.body")
        content = payload.text.body
    else:
        media = getattr(payload, payload.type)
        if media is None:
            raise ValueError(f"A {payload.type} message requires its media object")
        content = media.caption
        attachments = (Attachment(media.id, media.filename, media.mime_type),)
    return NormalizedMessage(
        channel="whatsapp", external_id=payload.id, sender=normalize_phone(payload.sender),
        content=content, date=event_date(payload.timestamp, receipt), received_at=receipt.isoformat(),
        context_id=payload.context.id if payload.context else None, attachments=attachments,
        payload=payload.model_dump(by_alias=True, exclude_none=True),
    )


def normalize_email(payload: EmailPayload, receipt: datetime | None = None) -> NormalizedMessage:
    receipt = _receipt_time(receipt)
    headers = {}
    for name, value in payload.headers.items():
        key = name.strip().lower()
        if key in headers:
            raise ValueError("Duplicate email header names")
        headers[key] = value.strip()
    external_id = headers.get("message-id", "")
    if not external_id:
        raise ValueError("Email requires a nonempty Message-ID header")
    return NormalizedMessage(
        channel="email", external_id=external_id, sender=normalize_mailbox(payload.sender),
        content=payload.text, date=event_date(payload.timestamp, receipt), received_at=receipt.isoformat(),
        recipients=tuple(normalize_mailbox(value) for value in payload.to + payload.cc),
        subject=payload.subject, headers=headers,
        attachments=tuple(Attachment(item.id or f"attachment-{index}", item.filename, item.mime_type)
                          for index, item in enumerate(payload.attachments, start=1)),
        payload=payload.model_dump(by_alias=True, exclude_none=True),
    )
