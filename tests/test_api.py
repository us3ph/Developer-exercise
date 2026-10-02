from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3
from threading import Barrier

import pytest

EXPECTED = {
    "A": ("D-1234", "single_dossier", "attached"), "B": ("D-2041", "context", "attached"),
    "C": (None, "needs_choice", "needs_choice"), "D": ("D-2077", "reference", "attached"),
    "E": (None, "triage", "triage"), "F": (None, "triage", "triage"),
    "G": ("D-1234", "dossier_address", "attached"), "H": ("D-1234", "thread", "attached"),
    "I": ("D-2041", "subject_reference", "attached"), "J": (None, "triage", "triage"),
    "K": ("D-1234", "single_dossier", "attached"), "L": ("D-1234", "single_dossier", "attached"),
}


def endpoint(payload):
    return "/webhooks/whatsapp" if "id" in payload else "/webhooks/email"


def choices(caplog):
    return [record for record in caplog.records if hasattr(record, "interactive_payload")]


@pytest.mark.parametrize("case", list(EXPECTED))
def test_required_cases_over_http(case, client, database, load_fixture, caplog):
    payload = load_fixture(case)
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        response = client.post(endpoint(payload), json=payload)
        assert response.status_code == 201, response.text
        result = response.json()
        message = result["message"]
        assert result["duplicate"] is False
        assert (message["dossier_reference"], message["routing_method"], message["routing_state"]) == EXPECTED[case]
        assert message["direction"] == "incoming"
        assert message["routing_reason"]
        if case == "K":
            replay = client.post(endpoint(payload), json=payload)
            assert replay.status_code == 200
            assert replay.json() == {"message": message, "duplicate": True}
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM message WHERE channel = ? AND external_id = ?",
            (message["channel"], message["external_id"]),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["routing_method"] == EXPECTED[case][1]
        assert rows[0]["dossier_id"] == message["dossier_id"]
        assert rows[0]["content"] == message["content"]
    if case == "C":
        assert message["dossier_id"] is None
        assert message["candidates"] == ["D-2041", "D-2077"]
        assert len(choices(caplog)) == 1
        rows = choices(caplog)[0].interactive_payload["interactive"]["action"]["sections"][0]["rows"]
        assert [row["id"] for row in rows] == message["candidates"]
    else:
        assert choices(caplog) == []
    if case == "F":
        assert message["sender"] == "+33612345678"
    if case == "L":
        assert message["sender"] == "+212661234567"


def test_read_endpoints_include_seeds_and_all_persisted_routing_results(client, database, load_fixture):
    for case in EXPECTED:
        payload = load_fixture(case)
        assert client.post(endpoint(payload), json=payload).status_code == 201
    expected_ids = {
        "D-1234": {"<out-1234@dossiers.courtee.ai>", "wamid.IN-A", "wamid.IN-K", "wamid.IN-L",
                   "<IN-G@cihbank.ma>", "<IN-H@cihbank.ma>"},
        "D-2041": {"wamid.OUT-2041", "wamid.IN-B", "<IN-I@cihbank.ma>"},
        "D-2077": {"wamid.IN-D"},
    }
    for reference, external_ids in expected_ids.items():
        response = client.get(f"/dossiers/{reference}/messages")
        assert response.status_code == 200
        thread = response.json()
        assert thread["dossier"]["reference"] == reference
        assert thread["dossier"]["active"] is True
        assert {message["external_id"] for message in thread["messages"]} == external_ids
        assert all(message["dossier_reference"] == reference for message in thread["messages"])
        dates = [message["date"] for message in thread["messages"]]
        assert dates == sorted(dates, reverse=True)
    triage = client.get("/triage")
    assert triage.status_code == 200
    messages = triage.json()["messages"]
    assert {message["external_id"] for message in messages} == {
        "wamid.IN-C", "wamid.IN-E", "wamid.IN-F", "<IN-J@example.test>",
    }
    assert all(message["dossier_id"] is None for message in messages)
    states = {message["external_id"]: message["routing_state"] for message in messages}
    assert states["wamid.IN-C"] == "needs_choice"
    assert states["wamid.IN-E"] == states["wamid.IN-F"] == states["<IN-J@example.test>"] == "triage"
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message").fetchone()[0] == 14


def test_thread_sorts_by_event_time_and_breaks_ties_consistently(client, load_fixture):
    for external_id, timestamp in [("newer", "1760000000"), ("older", "1759000000"), ("same-date", "1760000000")]:
        payload = load_fixture("A")
        payload.update({"id": external_id, "timestamp": timestamp})
        assert client.post(endpoint(payload), json=payload).status_code == 201
    thread = client.get("/dossiers/D-1234/messages").json()["messages"]
    assert [message["external_id"] for message in thread] == [
        "same-date", "newer", "<out-1234@dossiers.courtee.ai>", "older",
    ]


def test_fresh_seeded_read_endpoints(client):
    assert client.get("/triage").json() == {"messages": []}
    assert len(client.get("/dossiers/D-1234/messages").json()["messages"]) == 1
    assert len(client.get("/dossiers/D-2041/messages").json()["messages"]) == 1
    assert client.get("/dossiers/D-2077/messages").json()["messages"] == []
    assert client.get("/dossiers/D-9999/messages").status_code == 404


def test_replaying_every_fixture_keeps_all_messages_and_choice_log_once(client, database, load_fixture, caplog):
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        for case in EXPECTED:
            payload = load_fixture(case)
            first = client.post(endpoint(payload), json=payload)
            replay = client.post(endpoint(payload), json=payload)
            assert first.status_code == 201 and replay.status_code == 200
            assert replay.json() == {"message": first.json()["message"], "duplicate": True}
    assert len(choices(caplog)) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message").fetchone()[0] == 14


def test_duplicate_returns_original_even_when_payload_and_lookup_facts_change(client, database, load_fixture, monkeypatch):
    payload = load_fixture("A")
    first = client.post(endpoint(payload), json=payload).json()["message"]
    with database.connect() as connection:
        connection.execute("UPDATE dossier SET active = 0 WHERE reference = 'D-1234'")
    payload["text"]["body"] = "Pour D-2041"
    def unexpected_routing(*args):
        raise AssertionError("A replay must not run routing again")
    monkeypatch.setattr("courtee.message_service.route", unexpected_routing)
    replay = client.post(endpoint(payload), json=payload)
    assert replay.status_code == 200
    assert replay.json() == {"message": first, "duplicate": True}


def test_database_insert_conflict_returns_existing_without_second_choice_log(client, database, load_fixture, monkeypatch, caplog):
    payload = load_fixture("C")
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        first = client.post(endpoint(payload), json=payload).json()["message"]
        service = client.app.state.message_service
        original_existing = service._existing
        reads = 0
        def miss_advisory_read(connection, channel, external_id):
            nonlocal reads
            reads += 1
            return None if reads == 1 else original_existing(connection, channel, external_id)
        monkeypatch.setattr(service, "_existing", miss_advisory_read)
        replay = client.post(endpoint(payload), json=payload)
    assert replay.status_code == 200
    assert replay.json() == {"message": first, "duplicate": True}
    assert reads == 2
    assert len(choices(caplog)) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message WHERE external_id = 'wamid.IN-C'").fetchone()[0] == 1


def test_concurrent_replays_save_one_message_and_log_one_choice(client, database, load_fixture, caplog):
    payload = load_fixture("C")
    barrier = Barrier(4)
    def submit():
        barrier.wait(timeout=10)
        return client.post(endpoint(payload), json=payload)
    with caplog.at_level("INFO", logger="courtee.routing_service"), ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: submit(), range(4)))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.json()["message"]["id"] for response in responses}) == 1
    assert sum(not response.json()["duplicate"] for response in responses) == 1
    assert len(choices(caplog)) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message WHERE external_id = 'wamid.IN-C'").fetchone()[0] == 1


def test_failed_insert_rolls_back_and_does_not_log_choice(client, database, load_fixture, caplog):
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER fail_incoming BEFORE INSERT ON message "
                           "WHEN NEW.direction = 'incoming' BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
            client.post("/webhooks/whatsapp", json=load_fixture("C"))
    assert choices(caplog) == []
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message").fetchone()[0] == 2
        connection.execute("DROP TRIGGER fail_incoming")
    assert client.post("/webhooks/whatsapp", json=load_fixture("C")).status_code == 201


def test_external_ids_are_scoped_per_channel(client, database, load_fixture):
    whatsapp = load_fixture("K")
    email = load_fixture("G")
    email["headers"]["Message-ID"] = whatsapp["id"]
    for payload in (whatsapp, email):
        assert client.post(endpoint(payload), json=payload).status_code == 201
        assert client.post(endpoint(payload), json=payload).status_code == 200
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message WHERE external_id = ?", (whatsapp["id"],)).fetchone()[0] == 2


@pytest.mark.parametrize("sender", ["0661234567", "212661234567", "+212 6 61 23 45 67"])
def test_required_phone_formats_over_http(sender, client, load_fixture):
    payload = load_fixture("A")
    payload["from"] = sender
    message = client.post(endpoint(payload), json=payload).json()["message"]
    assert message["sender"] == "+212661234567"
    assert message["dossier_reference"] == "D-1234"


def test_person_with_second_contact_uses_same_dossier_relationship(client, database, load_fixture):
    with database.connect() as connection:
        connection.execute("INSERT INTO contact(id, person_id, channel, normalized_value) "
                           "VALUES (4, 1, 'whatsapp', '+212666000000')")
    payload = load_fixture("A")
    payload["from"] = "0666000000"
    message = client.post(endpoint(payload), json=payload).json()["message"]
    assert (message["dossier_reference"], message["routing_method"]) == ("D-1234", "single_dossier")


def test_dossier_with_another_borrower_routes_through_participants(client, database, load_fixture):
    with database.connect() as connection:
        connection.execute("INSERT INTO person VALUES (4, 'Imane', 'client')")
        connection.execute("INSERT INTO contact VALUES (4, 4, 'whatsapp', '+212663000000')")
        connection.execute("INSERT INTO dossier_participant VALUES (4, 1, 'co_borrower')")
    payload = load_fixture("A")
    payload["from"] = "+212663000000"
    assert client.post(endpoint(payload), json=payload).json()["message"]["dossier_reference"] == "D-1234"


def test_database_active_flag_controls_fallback(client, database, load_fixture, caplog):
    with database.connect() as connection:
        connection.execute("UPDATE dossier SET active = 0 WHERE reference = 'D-2077'")
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        message = client.post("/webhooks/whatsapp", json=load_fixture("C")).json()["message"]
    assert (message["dossier_reference"], message["routing_method"]) == ("D-2041", "single_dossier")
    assert choices(caplog) == []


def test_references_and_case_insensitive_headers_and_recipients_over_http(client, load_fixture):
    payload = load_fixture("H")
    payload["from"] = "Bennani <K.BENNANI@CIHBANK.MA>"
    payload["headers"] = {"mEsSaGe-Id": "<Opaque-ID@CIHBANK.MA>",
                          "rEfErEnCeS": "<unknown@bank.test> <out-1234@dossiers.courtee.ai>"}
    result = client.post(endpoint(payload), json=payload)
    assert result.status_code == 201
    message = result.json()["message"]
    assert (message["dossier_reference"], message["routing_method"]) == ("D-1234", "thread")
    assert message["external_id"] == "<Opaque-ID@CIHBANK.MA>"
    payload = load_fixture("G")
    payload["to"] = ["Courtee <D-1234@DOSSIERS.COURTEE.AI>"]
    assert client.post(endpoint(payload), json=payload).json()["message"]["routing_method"] == "dossier_address"


@pytest.mark.parametrize("channel", ["whatsapp", "email"])
def test_incoming_message_never_becomes_outgoing_context_or_thread(channel, client, load_fixture):
    if channel == "whatsapp":
        first, reply = load_fixture("A"), load_fixture("C")
        reply["context"] = {"id": first["id"]}
        expected_method = "needs_choice"
    else:
        first, reply = load_fixture("G"), load_fixture("H")
        reply["headers"]["In-Reply-To"] = first["headers"]["Message-ID"]
        expected_method = "triage"
    assert client.post(endpoint(first), json=first).status_code == 201
    message = client.post(endpoint(reply), json=reply).json()["message"]
    assert message["dossier_id"] is None
    assert message["routing_method"] == expected_method


def test_event_date_and_payload_and_attachment_metadata_are_persisted(client, database, load_fixture):
    payload = load_fixture("A")
    payload.pop("text")
    payload.update({"type": "document", "document": {
        "id": "salary-123", "filename": "attestation_salaire.pdf", "mime_type": "application/pdf",
        "caption": "Pour D-1234", "sha256": "simulated-digest",
    }})
    response = client.post(endpoint(payload), json=payload)
    assert response.status_code == 201
    message = response.json()["message"]
    assert message["date"] == datetime.fromtimestamp(int(payload["timestamp"]), timezone.utc).isoformat()
    assert message["attachments"] == [{"external_id": "salary-123", "filename": "attestation_salaire.pdf", "mime_type": "application/pdf"}]
    with database.connect() as connection:
        saved = connection.execute("SELECT payload_json, attachments_json FROM message WHERE id = ?", (message["id"],)).fetchone()
        assert json.loads(saved["payload_json"])["document"] == payload["document"]
        assert json.loads(saved["attachments_json"]) == message["attachments"]


@pytest.mark.parametrize("case, field, value", [
    ("A", "id", None), ("A", "from", "bad phone"), ("A", "timestamp", "not-a-date"),
    ("A", "text", None), ("A", "type", "unsupported"), ("G", "headers", {}),
    ("G", "from", "no-address"), ("G", "to", []),
])
def test_invalid_payloads_return_422_and_save_nothing(case, field, value, client, database, load_fixture, caplog):
    payload = load_fixture(case)
    path = endpoint(payload)
    payload[field] = value
    with caplog.at_level("INFO", logger="courtee.routing_service"):
        assert client.post(path, json=payload).status_code == 422
    assert choices(caplog) == []
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message").fetchone()[0] == 2
