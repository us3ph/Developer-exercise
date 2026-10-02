import pytest

from courtee.domain import Dossier, LookupFacts
from courtee.normalization import WhatsAppPayload, normalize_whatsapp
from courtee.routing import route
from courtee.routing_service import log_dossier_choice


def test_saras_interactive_dossier_choice_is_logged_outside_router(load_fixture, caplog):
    message = normalize_whatsapp(WhatsAppPayload.model_validate(load_fixture("C")))
    facts = LookupFacts((Dossier(2, "D-2041", True), Dossier(3, "D-2077", True)), True, frozenset({2, 3}))
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        decision = route(message, facts)
        assert caplog.records == []
        log_dossier_choice(message, decision)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.source_external_id == "wamid.IN-C"
    assert record.interactive_payload["to"] == "212662000111"
    rows = record.interactive_payload["interactive"]["action"]["sections"][0]["rows"]
    assert rows == [{"id": "D-2041", "title": "D-2041"}, {"id": "D-2077", "title": "D-2077"}]
    assert "D-2041" in record.getMessage() and "D-2077" in record.getMessage()


@pytest.mark.parametrize("case", ["A", "E", "F"])
def test_attached_or_triage_messages_do_not_log_choice(case, load_fixture, caplog):
    message = normalize_whatsapp(WhatsAppPayload.model_validate(load_fixture(case)))
    facts = LookupFacts((Dossier(1, "D-1234", True),), message.sender == "+212661234567", frozenset({1}))
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        log_dossier_choice(message, route(message, facts))
    assert caplog.records == []
