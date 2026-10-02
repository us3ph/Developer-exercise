from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
from threading import Barrier, Event

from fastapi.testclient import TestClient
import pytest

from courtee.analysis_contract import validate_analysis
from courtee.analyzers import FakeAnalyzer
from courtee.api import create_app


@pytest.fixture
def analysis_fixture():
    def load(name):
        return json.loads((Path(__file__).parents[1] / "fixtures" / "analysis" / f"{name}.json").read_text())
    return load


@pytest.fixture
def client_with_analyzer(database):
    with ExitStack() as stack:
        def create(analyzer):
            return stack.enter_context(TestClient(create_app(database, analyzer=analyzer)))
        yield create


def output(actions, confidence=0.95):
    return json.dumps({"intents": [], "entities": {}, "confidence": confidence,
                       "actions": actions, "reply_draft": "Bonjour, bien reçu."})


def test_attachment_is_classified_automatically_with_metadata_and_history(client, database, analysis_fixture):
    response = client.post("/webhooks/whatsapp", json=analysis_fixture("attachment"))
    assert response.status_code == 201
    message = response.json()["message"]
    assert message["dossier_reference"] == "D-1234"
    assert message["analysis"]["status"] == "completed"
    assert message["analysis"]["result"]["reply_draft"]
    assert message["proposals"] == []
    classification = message["attachment_classifications"][0]
    assert classification["classification"] == "attestation_salaire"
    assert classification["filename"] == "attestation_salaire.pdf"
    assert classification["mime_type"] == "application/pdf"
    with database.connect() as connection:
        attachment = connection.execute("SELECT * FROM attachment WHERE message_id = ?", (message["id"],)).fetchone()
        assert attachment["classification"] == "attestation_salaire"
        history = connection.execute("SELECT * FROM dossier_history WHERE message_id = ?", (message["id"],)).fetchone()
        assert history["applied_by"] == "auto"
        assert json.loads(history["changes_json"])["classification"] == {"before": None, "after": "attestation_salaire"}
    thread = client.get("/dossiers/D-1234/messages").json()
    assert thread["history"][0]["message_id"] == message["id"]


def test_agreement_in_principle_stays_pending_without_changing_status(client, database, analysis_fixture):
    response = client.post("/webhooks/email", json=analysis_fixture("agreement"))
    assert response.status_code == 201
    message = response.json()["message"]
    assert message["analysis"]["status"] == "completed"
    result = message["analysis"]["result"]
    assert result["entities"] == {"montant": 850000.0, "duree_ans": 25, "taux": 4.6, "pieces": []}
    proposal = message["proposals"][0]
    assert proposal["status"] == "pending"
    assert proposal["action"] == {"type": "set_status", "value": "accord_principe", "risk": "medium"}
    assert proposal["message_id"] == message["id"]
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM dossier WHERE id = 1").fetchone()[0] == "submitted"
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 0


def test_invalid_fake_analyzer_json_leads_to_attached_manual_review(client_with_analyzer, database, load_fixture):
    analyzer = FakeAnalyzer(output_override="{invalid JSON")
    client = client_with_analyzer(analyzer)
    response = client.post("/webhooks/whatsapp", json=load_fixture("A"))
    assert response.status_code == 201
    message = response.json()["message"]
    assert message["dossier_reference"] == "D-1234"
    assert message["routing_state"] == "attached"
    assert message["analysis"]["status"] == "manual_review"
    assert message["analysis"]["result"] is None
    assert message["proposals"] == []
    assert client.get("/triage").json()["messages"] == []
    assert client.get("/dossiers/D-1234/messages").json()["messages"][0]["analysis"]["status"] == "manual_review"
    replay = client.post("/webhooks/whatsapp", json=load_fixture("A"))
    assert replay.status_code == 200 and analyzer.calls == 1
    with database.connect() as connection:
        assert connection.execute("SELECT raw_output FROM message_analysis").fetchone()[0] == "{invalid JSON"
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM proposal").fetchone()[0] == 0


def test_validating_proposal_updates_status_and_records_reviewer_and_source(client, database, analysis_fixture):
    payload = analysis_fixture("agreement")
    message = client.post("/webhooks/email", json=payload).json()["message"]
    proposal_id = message["proposals"][0]["id"]
    response = client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": "tutor-1"})
    assert response.status_code == 200
    reviewed = response.json()
    assert reviewed["proposal"]["status"] == "applied"
    assert reviewed["proposal"]["reviewed_by"] == "tutor-1"
    assert reviewed["history"]["message_id"] == message["id"]
    assert reviewed["history"]["proposal_id"] == proposal_id
    assert reviewed["history"]["applied_by"] == "tutor-1"
    assert reviewed["history"]["changes"] == {"status": {"before": "submitted", "after": "accord_principe"}}
    thread = client.get("/dossiers/D-1234/messages").json()
    assert thread["dossier"]["status"] == "accord_principe"
    assert len(thread["history"]) == 1
    assert client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": "tutor-2"}).status_code == 409
    assert client.post(f"/proposals/{proposal_id}/reject", json={"reviewer": "tutor-2"}).status_code == 409
    assert client.post("/webhooks/email", json=payload).status_code == 200
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 1


def test_rejection_records_review_without_applying_action(client, analysis_fixture):
    message = client.post("/webhooks/email", json=analysis_fixture("agreement")).json()["message"]
    proposal_id = message["proposals"][0]["id"]
    response = client.post(f"/proposals/{proposal_id}/reject", json={"reviewer": "tutor-1"})
    assert response.status_code == 200
    assert response.json()["proposal"]["status"] == "rejected"
    assert response.json()["proposal"]["reviewed_by"] == "tutor-1"
    assert response.json()["history"] is None
    thread = client.get("/dossiers/D-1234/messages").json()
    assert thread["dossier"]["status"] == "submitted" and thread["history"] == []
    assert client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": "tutor-1"}).status_code == 409


@pytest.mark.parametrize("risk, confidence, automatic", [
    ("low", 0.85, True), ("low", 0.849, False), ("low", 1, True), ("low", 0, False),
    ("medium", 0.95, False), ("medium", 1, False), ("high", 1, False),
])
def test_risk_and_confidence_gate_each_action(risk, confidence, automatic, client_with_analyzer, database, load_fixture):
    analyzer = FakeAnalyzer(output([{"type": "set_status", "value": "test_status", "risk": risk}], confidence))
    client = client_with_analyzer(analyzer)
    message = client.post("/webhooks/whatsapp", json=load_fixture("A")).json()["message"]
    assert message["analysis"]["status"] == "completed"
    assert len(message["proposals"]) == (0 if automatic else 1)
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM dossier WHERE id = 1").fetchone()[0] == ("test_status" if automatic else "submitted")
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == int(automatic)


def test_low_risk_document_request_is_persisted_once_on_replay(client_with_analyzer, database, analysis_fixture):
    analyzer = FakeAnalyzer()
    client = client_with_analyzer(analyzer)
    payload = analysis_fixture("document_request")
    first = client.post("/webhooks/email", json=payload)
    replay = client.post("/webhooks/email", json=payload)
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json()["message"] == first.json()["message"]
    assert analyzer.calls == 1
    thread = client.get("/dossiers/D-1234/messages").json()
    assert thread["document_requests"][0]["document"] == "attestation_salaire"
    assert thread["history"][0]["applied_by"] == "auto"
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM document_request").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM message_analysis").fetchone()[0] == 1


def test_mixed_risks_apply_low_action_and_keep_medium_action_pending(client, analysis_fixture):
    payload = analysis_fixture("attachment")
    payload["document"]["caption"] = "Pour D-1234, accord de principe reçu."
    message = client.post("/webhooks/whatsapp", json=payload).json()["message"]
    assert message["attachment_classifications"][0]["classification"] == "attestation_salaire"
    assert message["proposals"][0]["action"]["risk"] == "medium"
    thread = client.get("/dossiers/D-1234/messages").json()
    assert thread["dossier"]["status"] == "submitted"
    assert len(thread["history"]) == 1


@pytest.mark.parametrize("bad_raw", [
    "not JSON", "[]", "{}", '{"confidence": NaN}',
    output([], True), output([], "0.95"), output([], 1.01), output([], -0.1),
    output([{"type": "set_status", "value": "accepted", "risk": "unknown"}]),
    output([{"type": "delete_dossier", "value": "D-1234", "risk": "low"}]),
    output([{"type": "request_document", "value": "", "risk": "low"}]),
    output([{"type": "set_status", "value": "accepted", "risk": "low"},
            {"type": "unsupported", "value": "x", "risk": "low"}]),
    output([]).replace('"confidence": 0.95', '"confidence": 0.95, "confidence": 1'),
])
def test_invalid_contract_applies_nothing(bad_raw, client_with_analyzer, database, load_fixture):
    client = client_with_analyzer(FakeAnalyzer(bad_raw))
    response = client.post("/webhooks/whatsapp", json=load_fixture("A"))
    assert response.status_code == 201
    assert response.json()["message"]["analysis"]["status"] == "manual_review"
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM dossier WHERE id = 1").fetchone()[0] == "submitted"
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM proposal").fetchone()[0] == 0


def test_classification_cannot_target_another_messages_attachment(client_with_analyzer, database, analysis_fixture):
    first_client = client_with_analyzer(FakeAnalyzer())
    first = first_client.post("/webhooks/whatsapp", json=analysis_fixture("attachment")).json()["message"]
    attachment_id = first["attachment_classifications"][0]["attachment_id"]
    analyzer = FakeAnalyzer(output([
        {"type": "set_status", "value": "wrong", "risk": "low"},
        {"type": "classify_attachment", "value": "wrong", "risk": "low", "attachment_id": attachment_id},
    ]))
    second_client = client_with_analyzer(analyzer)
    payload = analysis_fixture("attachment")
    payload["id"] = "wamid.SECOND-ATTACHMENT"
    message = second_client.post("/webhooks/whatsapp", json=payload).json()["message"]
    assert message["analysis"]["status"] == "manual_review"
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM dossier WHERE id = 1").fetchone()[0] == "submitted"
        assert connection.execute("SELECT classification FROM attachment WHERE id = ?", (attachment_id,)).fetchone()[0] == "attestation_salaire"
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 1


def test_message_is_committed_before_analyzer_and_no_write_lock_spans_analysis(client_with_analyzer, database, load_fixture):
    class InspectingFake(FakeAnalyzer):
        def analyze(self, context):
            with database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT dossier_id FROM message WHERE id = ?", (context.message_id,)).fetchone()
                assert row[0] == context.dossier_id
                assert connection.execute("SELECT status FROM message_analysis WHERE message_id = ?", (context.message_id,)).fetchone()[0] == "processing"
            return super().analyze(context)
    client = client_with_analyzer(InspectingFake())
    assert client.post("/webhooks/whatsapp", json=load_fixture("A")).json()["message"]["analysis"]["status"] == "completed"


def test_unattached_messages_are_not_analyzed(client_with_analyzer, database, load_fixture):
    analyzer = FakeAnalyzer()
    client = client_with_analyzer(analyzer)
    for case in "CEFJ":
        payload = load_fixture(case)
        path = "/webhooks/email" if case == "J" else "/webhooks/whatsapp"
        message = client.post(path, json=payload).json()["message"]
        assert message["analysis"] is None
    assert analyzer.calls == 0
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message_analysis").fetchone()[0] == 0


def test_proposal_status_change_and_history_roll_back_together(client, database, analysis_fixture):
    message = client.post("/webhooks/email", json=analysis_fixture("agreement")).json()["message"]
    proposal_id = message["proposals"][0]["id"]
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER fail_history BEFORE INSERT ON dossier_history "
                           "BEGIN SELECT RAISE(ABORT, 'history unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match="history unavailable"):
        client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": "tutor-1"})
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM dossier WHERE id = 1").fetchone()[0] == "submitted"
        assert connection.execute("SELECT status, reviewed_by FROM proposal WHERE id = ?", (proposal_id,)).fetchone()[0] == "pending"
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_history")
    assert client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": "tutor-1"}).status_code == 200


def test_auto_action_failure_rolls_back_entire_batch_and_preserves_message(client_with_analyzer, database, load_fixture):
    analyzer = FakeAnalyzer(output([
        {"type": "set_status", "value": "temporary", "risk": "low"},
        {"type": "request_document", "value": "attestation_salaire", "risk": "low"},
    ]))
    client = client_with_analyzer(analyzer)
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER fail_second_history BEFORE INSERT ON dossier_history "
                           "WHEN NEW.action_index = 1 BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    response = client.post("/webhooks/whatsapp", json=load_fixture("A"))
    assert response.status_code == 201
    assert response.json()["message"]["analysis"]["status"] == "manual_review"
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM dossier WHERE id = 1").fetchone()[0] == "submitted"
        assert connection.execute("SELECT count(*) FROM document_request").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 0


def test_concurrent_review_applies_proposal_once(client, database, analysis_fixture):
    message = client.post("/webhooks/email", json=analysis_fixture("agreement")).json()["message"]
    proposal_id = message["proposals"][0]["id"]
    barrier = Barrier(2)
    def submit(reviewer):
        barrier.wait(timeout=10)
        return client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": reviewer})
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, ["tutor-1", "tutor-2"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 1


def test_replay_during_analysis_does_not_call_analyzer_again(client_with_analyzer, database, analysis_fixture):
    started, release = Event(), Event()
    class WaitingFake(FakeAnalyzer):
        def analyze(self, context):
            started.set()
            assert release.wait(timeout=10)
            return super().analyze(context)
    analyzer = WaitingFake()
    client = client_with_analyzer(analyzer)
    payload = analysis_fixture("document_request")
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(client.post, "/webhooks/email", json=payload)
        try:
            assert started.wait(timeout=5)
            replay = client.post("/webhooks/email", json=payload)
            assert replay.status_code == 200
            assert replay.json()["message"]["analysis"]["status"] == "processing"
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 201
    assert analyzer.calls == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 1


@pytest.mark.parametrize("reviewer", [None, "", "   ", "auto", " AUTO ", 123])
def test_review_requires_human_identifier(reviewer, client, analysis_fixture):
    message = client.post("/webhooks/email", json=analysis_fixture("agreement")).json()["message"]
    proposal_id = message["proposals"][0]["id"]
    assert client.post(f"/proposals/{proposal_id}/validate", json={"reviewer": reviewer}).status_code == 422


@pytest.mark.parametrize("operation", ["validate", "reject"])
def test_unknown_proposal_returns_404(operation, client):
    assert client.post(f"/proposals/999/{operation}", json={"reviewer": "tutor-1"}).status_code == 404


def test_original_exercise_contract_is_valid():
    result = validate_analysis(json.dumps({
        "intents": ["accord_principe", "demande_piece"],
        "entities": {"montant": 850000, "duree_ans": 25, "taux": 4.6, "pieces": ["attestation_salaire"]},
        "confidence": 0.91,
        "actions": [{"type": "set_status", "value": "accord_principe", "risk": "medium"},
                    {"type": "request_document", "value": "attestation_salaire", "risk": "low"}],
        "reply_draft": "Bonjour Youssef, bonne nouvelle...",
    }))
    assert result.confidence == 0.91
