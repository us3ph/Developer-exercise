"""Strict validation of the analyzer contract before any action is applied."""
from dataclasses import dataclass
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from courtee.domain import Channel

Risk = Literal["low", "medium", "high"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Entities(ContractModel):
    montant: float | None = Field(default=None, ge=0)
    duree_ans: StrictInt | None = Field(default=None, gt=0)
    taux: float | None = Field(default=None, ge=0)
    pieces: list[StrictStr] = Field(default_factory=list)


class StatusAction(ContractModel):
    type: Literal["set_status"]
    value: StrictStr = Field(min_length=1, pattern=r"\S")
    risk: Risk


class DocumentAction(ContractModel):
    type: Literal["request_document"]
    value: StrictStr = Field(min_length=1, pattern=r"\S")
    risk: Risk


class AttachmentAction(ContractModel):
    type: Literal["classify_attachment"]
    value: StrictStr = Field(min_length=1, pattern=r"\S")
    risk: Risk
    attachment_id: StrictInt = Field(gt=0)


Action = Annotated[StatusAction | DocumentAction | AttachmentAction, Field(discriminator="type")]


class AnalysisResult(ContractModel):
    intents: list[StrictStr]
    entities: Entities
    confidence: float = Field(ge=0, le=1)
    actions: list[Action]
    reply_draft: StrictStr


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON number")


def validate_analysis(raw: str) -> AnalysisResult:
    if not isinstance(raw, str):
        raise ValueError("Analyzer output must be a JSON string")
    decoded = json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    return AnalysisResult.model_validate(decoded)


@dataclass(frozen=True)
class AnalysisAttachment:
    id: int
    external_id: str
    filename: str
    mime_type: str


@dataclass(frozen=True)
class AnalysisContext:
    message_id: int
    dossier_id: int
    dossier_reference: str
    dossier_status: str
    channel: Channel
    content: str
    subject: str
    attachments: tuple[AnalysisAttachment, ...] = ()
