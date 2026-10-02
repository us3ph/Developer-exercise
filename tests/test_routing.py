from copy import deepcopy
from dataclasses import replace

import pytest

from courtee.domain import Dossier, KnownMessage, LookupFacts
from courtee.normalization import EmailPayload, WhatsAppPayload, normalize_email, normalize_whatsapp
from courtee.routing import route

DOSSIERS = (Dossier(1, "D-1234", True), Dossier(2, "D-2041", True), Dossier(3, "D-2077", True))
OUTGOING = (KnownMessage("whatsapp", "outgoing", "wamid.OUT-2041", 2),
            KnownMessage("email", "outgoing", "<out-1234@dossiers.courtee.ai>", 1))
EXPECTED = {
    "A": (1, "single_dossier", "attached"), "B": (2, "context", "attached"),
    "C": (None, "needs_choice", "needs_choice"), "D": (3, "reference", "attached"),
    "E": (None, "triage", "triage"), "F": (None, "triage", "triage"),
    "G": (1, "dossier_address", "attached"), "H": (1, "thread", "attached"),
    "I": (2, "subject_reference", "attached"), "J": (None, "triage", "triage"),
    "K": (1, "single_dossier", "attached"), "L": (1, "single_dossier", "attached"),
}


@pytest.fixture
def message_for(load_fixture):
    def load(case):
        payload = load_fixture(case)
        if "id" in payload:
            return normalize_whatsapp(WhatsAppPayload.model_validate(payload))
        return normalize_email(EmailPayload.model_validate(payload))
    return load


def facts_for(message):
    owned = {"+212661234567": frozenset({1}), "+212662000111": frozenset({2, 3}),
             "k.bennani@cihbank.ma": frozenset({1, 2})}
    return LookupFacts(DOSSIERS, message.sender in owned, owned.get(message.sender, frozenset()), OUTGOING)


@pytest.mark.parametrize("case", list(EXPECTED))
def test_required_fixture_routing_decisions(case, message_for):
    message = message_for(case)
    decision = route(message, facts_for(message))
    assert (decision.dossier_id, decision.method, decision.state) == EXPECTED[case]
    assert decision.reason
    if case == "C":
        assert decision.candidates == ("D-2041", "D-2077")


def test_router_is_repeatable_has_no_logs_and_leaves_inputs_unchanged(message_for, caplog):
    message = message_for("C")
    facts = facts_for(message)
    before = deepcopy((message, facts))
    with caplog.at_level("DEBUG"):
        first = route(message, facts)
        assert route(message, facts) == first
    assert (message, facts) == before
    assert caplog.records == []


def test_known_context_wins_over_unrelated_reference(message_for):
    message = replace(message_for("B"), content="pour D-1234")
    assert route(message, facts_for(message)).method == "context"


@pytest.mark.parametrize("known", [
    KnownMessage("whatsapp", "incoming", "wamid.OUT-2041", 2),
    KnownMessage("email", "outgoing", "wamid.OUT-2041", 2),
    KnownMessage("whatsapp", "outgoing", "wamid.OUT-2041", None),
    KnownMessage("whatsapp", "outgoing", "wamid.OUT-2041", 999),
])
def test_context_requires_outgoing_whatsapp_with_existing_dossier(known, message_for):
    message = replace(message_for("B"), content="pour D-2077")
    assert route(message, replace(facts_for(message), messages=(known,))).method == "reference"


@pytest.mark.parametrize("context_id", [None, "wamid.UNKNOWN"])
def test_foreign_reference_never_falls_back_to_single_active_dossier(context_id, message_for):
    message = replace(message_for("E"), context_id=context_id)
    decision = route(message, facts_for(message))
    assert decision.dossier_id is None
    assert decision.method == "triage"


@pytest.mark.parametrize("content", ["D-20411", "XD-2041", "D-2041x", "D-2041-other", "d-2041"])
def test_only_complete_case_sensitive_whatsapp_references_match(content, message_for):
    message = replace(message_for("A"), content=content)
    assert route(message, facts_for(message)).method == "single_dossier"


@pytest.mark.parametrize("content", ["D-2077, D-2077", "D-2077 et D-1234"])
def test_unique_owned_reference_wins(content, message_for):
    message = replace(message_for("D"), content=content)
    decision = route(message, facts_for(message))
    assert (decision.dossier_id, decision.method) == (3, "reference")


def test_multiple_owned_references_need_choice_and_candidates_are_stable(message_for):
    message = replace(message_for("D"), content="D-2041 et D-2077")
    facts = replace(facts_for(message), dossiers=tuple(reversed(DOSSIERS)))
    decision = route(message, facts)
    assert decision.method == "needs_choice"
    assert decision.candidates == ("D-2041", "D-2077")


def test_unknown_sender_with_reference_goes_to_triage(message_for):
    message = replace(message_for("F"), content="pour D-1234")
    assert route(message, facts_for(message)).method == "triage"


def test_inactive_dossier_excluded_from_fallback_but_explicit_reference_is_allowed(message_for):
    message = message_for("A")
    facts = replace(facts_for(message), dossiers=(Dossier(1, "D-1234", False),))
    assert route(message, facts).method == "triage"
    assert route(replace(message, content="pour D-1234"), facts).method == "reference"


def test_choice_uses_only_active_dossiers(message_for):
    message = message_for("C")
    facts = replace(facts_for(message), dossiers=(*DOSSIERS[:2], Dossier(3, "D-2077", False)))
    decision = route(message, facts)
    assert (decision.dossier_id, decision.method) == (2, "single_dossier")


def test_email_address_precedes_thread_and_subject(message_for):
    message = replace(message_for("H"), recipients=("d-2041@dossiers.courtee.ai",), subject="[D-2077]")
    assert route(message, facts_for(message)).dossier_id == 2
    assert route(message, facts_for(message)).method == "dossier_address"


def test_ambiguous_recipient_rule_uses_thread(message_for):
    message = replace(message_for("H"), recipients=("d-1234@dossiers.courtee.ai", "d-2041@dossiers.courtee.ai"))
    assert route(message, facts_for(message)).method == "thread"


def test_duplicate_recipients_are_one_unique_dossier(message_for):
    message = replace(message_for("G"), recipients=("d-1234@dossiers.courtee.ai",) * 2)
    assert route(message, facts_for(message)).method == "dossier_address"


@pytest.mark.parametrize("recipient", [
    "other-d-1234@dossiers.courtee.ai", "d-12345@dossiers.courtee.ai", "d-1234@dossiers.courtee.ai.other",
])
def test_email_dossier_mailbox_match_is_exact(recipient, message_for):
    message = replace(message_for("J"), recipients=(recipient,))
    assert route(message, facts_for(message)).method == "triage"


def test_references_resolves_known_outgoing_email(message_for):
    message = replace(message_for("H"), headers={
        "references": "<unknown@cihbank.ma>\r\n <out-1234@dossiers.courtee.ai>",
    })
    assert route(message, facts_for(message)).method == "thread"


@pytest.mark.parametrize("known", [
    KnownMessage("email", "incoming", "<out-1234@dossiers.courtee.ai>", 1),
    KnownMessage("whatsapp", "outgoing", "<out-1234@dossiers.courtee.ai>", 1),
    KnownMessage("email", "outgoing", "<out-1234@dossiers.courtee.ai>", None),
])
def test_thread_requires_outgoing_email(known, message_for):
    message = message_for("H")
    assert route(message, replace(facts_for(message), messages=(known,))).method == "triage"


def test_thread_ids_are_case_sensitive(message_for):
    message = replace(message_for("H"), headers={"in-reply-to": "<OUT-1234@dossiers.courtee.ai>"})
    assert route(message, facts_for(message)).method == "triage"


def test_multiple_thread_messages_for_same_dossier_are_unique(message_for):
    message = replace(message_for("H"), headers={
        "in-reply-to": "<out-1234@dossiers.courtee.ai>", "references": "<second@courtee.ai>",
    })
    facts = replace(facts_for(message), messages=(*OUTGOING, KnownMessage("email", "outgoing", "<second@courtee.ai>", 1)))
    assert route(message, facts).method == "thread"


def test_conflicting_reply_and_references_use_subject_instead(message_for):
    message = replace(message_for("H"), headers={
        "in-reply-to": "<out-1234@dossiers.courtee.ai>", "references": "<other@courtee.ai>",
    }, subject="[D-2077]")
    facts = replace(facts_for(message), messages=(*OUTGOING, KnownMessage("email", "outgoing", "<other@courtee.ai>", 2)))
    decision = route(message, facts)
    assert (decision.dossier_id, decision.method) == (3, "subject_reference")
    assert route(replace(message, subject=""), facts).method == "triage"


def test_ambiguous_subject_goes_to_triage_and_repeated_subject_is_unique(message_for):
    message = replace(message_for("I"), subject="[D-1234] [D-2041]")
    assert route(message, facts_for(message)).method == "triage"
    assert route(replace(message, subject="[D-2041] [D-2041]"), facts_for(message)).dossier_id == 2


def test_email_unknown_sender_can_route_by_explicit_existing_dossier(message_for):
    message = replace(message_for("J"), recipients=("d-1234@dossiers.courtee.ai",))
    assert route(message, facts_for(message)).method == "dossier_address"


def test_unknown_dossier_clues_continue_to_next_email_rule(message_for):
    message = replace(message_for("I"), recipients=("d-9999@dossiers.courtee.ai",))
    assert route(message, facts_for(message)).method == "subject_reference"
    assert route(replace(message, subject="[D-9999]"), facts_for(message)).method == "triage"
