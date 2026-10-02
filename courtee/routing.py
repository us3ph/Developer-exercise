"""Pure routing: no database access, logging, network calls, or analysis."""
import re

from courtee.domain import LookupFacts, NormalizedMessage, RoutingDecision, RoutingMethod

REFERENCE = re.compile(r"(?<![\w-])D-[0-9]{4}(?![\w-])")
SUBJECT_REFERENCE = re.compile(r"\[(D-[0-9]{4})\]")
DOSSIER_MAILBOX = re.compile(r"d-([0-9]{4})@dossiers\.courtee\.ai")


def _attach(dossier_id: int, method: RoutingMethod, reason: str) -> RoutingDecision:
    return RoutingDecision(dossier_id, method, "attached", reason)


def _outgoing_dossiers(ids: set[str], channel: str, facts: LookupFacts) -> set[int]:
    existing = {d.id for d in facts.dossiers}
    return {m.dossier_id for m in facts.messages
            if m.channel == channel and m.direction == "outgoing"
            and m.external_id in ids and m.dossier_id is not None and m.dossier_id in existing}


def route(message: NormalizedMessage, facts: LookupFacts) -> RoutingDecision:
    if message.channel == "whatsapp":
        return _route_whatsapp(message, facts)
    if message.channel == "email":
        return _route_email(message, facts)
    raise ValueError("Unsupported message channel")


def _route_whatsapp(message: NormalizedMessage, facts: LookupFacts) -> RoutingDecision:
    if message.context_id:
        candidates = _outgoing_dossiers({message.context_id}, "whatsapp", facts)
        if len(candidates) == 1:
            return _attach(next(iter(candidates)), "context", "Known outgoing WhatsApp reply")

    references = set(REFERENCE.findall(message.content))
    owned = [d for d in facts.dossiers if d.id in facts.sender_dossier_ids]
    candidates = {d.id for d in owned if d.reference in references}
    if len(candidates) == 1:
        return _attach(next(iter(candidates)), "reference", "Unique reference belonging to sender")
    # Case E: an unrelated reference must never fall back to the sender's phone.
    if references and not candidates:
        return RoutingDecision(None, "triage", "triage", "Referenced dossier does not belong to sender")

    active = sorted((d for d in owned if d.active), key=lambda d: d.reference)
    if facts.sender_known and len(active) == 1:
        return _attach(active[0].id, "single_dossier", "Sender has one active dossier")
    if facts.sender_known and len(active) > 1:
        return RoutingDecision(None, "needs_choice", "needs_choice", "Sender has multiple active dossiers",
                               tuple(d.reference for d in active))
    return RoutingDecision(None, "triage", "triage", "Unknown sender or no active dossier")


def _route_email(message: NormalizedMessage, facts: LookupFacts) -> RoutingDecision:
    by_reference = {d.reference: d.id for d in facts.dossiers}
    recipients = {f"D-{match.group(1)}" for value in message.recipients
                  if (match := DOSSIER_MAILBOX.fullmatch(value))}
    candidates = {by_reference[ref] for ref in recipients if ref in by_reference}
    if len(candidates) == 1:
        return _attach(next(iter(candidates)), "dossier_address", "Unique dossier recipient mailbox")

    thread_ids = set()
    for name in ("in-reply-to", "references"):
        thread_ids.update(re.findall(r"<[^<>\s]+>|[^\s<>]+", message.headers.get(name, "")))
    candidates = _outgoing_dossiers(thread_ids, "email", facts)
    if len(candidates) == 1:
        return _attach(next(iter(candidates)), "thread", "Unique known outgoing email thread")

    references = set(SUBJECT_REFERENCE.findall(message.subject))
    candidates = {by_reference[ref] for ref in references if ref in by_reference}
    if len(candidates) == 1:
        return _attach(next(iter(candidates)), "subject_reference", "Unique bracketed subject reference")
    return RoutingDecision(None, "triage", "triage", "No unique email routing clue")
