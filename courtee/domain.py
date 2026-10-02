"""Values shared by normalization, routing, and the future ingestion service."""
from dataclasses import dataclass, field
from typing import Any, Literal

Channel = Literal["whatsapp", "email"]
RoutingState = Literal["attached", "needs_choice", "triage"]
RoutingMethod = Literal[
    "context", "reference", "single_dossier", "needs_choice", "triage",
    "dossier_address", "thread", "subject_reference",
]


@dataclass(frozen=True)
class Attachment:
    external_id: str
    filename: str
    mime_type: str


@dataclass(frozen=True)
class NormalizedMessage:
    channel: Channel
    external_id: str
    sender: str
    content: str
    date: str
    received_at: str
    recipients: tuple[str, ...] = ()
    subject: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    context_id: str | None = None
    attachments: tuple[Attachment, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Dossier:
    id: int
    reference: str
    active: bool


@dataclass(frozen=True)
class KnownMessage:
    channel: Channel
    direction: Literal["incoming", "outgoing"]
    external_id: str
    dossier_id: int | None


@dataclass(frozen=True)
class LookupFacts:
    dossiers: tuple[Dossier, ...]
    sender_known: bool = False
    sender_dossier_ids: frozenset[int] = frozenset()
    messages: tuple[KnownMessage, ...] = ()


@dataclass(frozen=True)
class RoutingDecision:
    dossier_id: int | None
    method: RoutingMethod
    state: RoutingState
    reason: str
    candidates: tuple[str, ...] = ()
