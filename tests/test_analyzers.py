import json

from fastapi.testclient import TestClient
import httpx
import pytest

from courtee.analysis_contract import AnalysisContext, validate_analysis
from courtee.analyzers import AnalyzerError, FakeAnalyzer, LlmAnalyzer, analyzer_from_environment
from courtee.api import create_app

CONTEXT = AnalysisContext(3, 1, "D-1234", "submitted", "email", "Accord de principe pour 850 000 MAD", "Accord")
VALID_RESULT = {"intents": ["accord_principe"], "entities": {"montant": 850000}, "confidence": 0.91,
                "actions": [{"type": "set_status", "value": "accord_principe", "risk": "medium"}],
                "reply_draft": "Bonjour, nous avons reçu l'accord de principe."}


def provider_response(content=None, finish_reason="stop"):
    return {"choices": [{"finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content or json.dumps(VALID_RESULT)}}]}


def test_live_adapter_uses_openrouter_free_model_and_json_only_contract():
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer fictional-api-key"
        body = json.loads(request.content)
        assert body["model"] == "openrouter/free"
        assert body["response_format"] == {"type": "json_object"}
        assert body["provider"] == {"require_parameters": True}
        assert body["stream"] is False
        assert body["messages"][0]["role"] == "system"
        assert "ONLY one JSON object" in body["messages"][0]["content"]
        assert "reply_draft" in body["messages"][0]["content"]
        assert json.loads(body["messages"][1]["content"])["dossier_reference"] == "D-1234"
        return httpx.Response(200, json=provider_response())
    analyzer = LlmAnalyzer("fictional-api-key", transport=httpx.MockTransport(handler))
    result = validate_analysis(analyzer.analyze(CONTEXT))
    assert result.actions[0].risk == "medium"
    assert len(requests) == 1


def test_live_model_is_configurable():
    def handler(request):
        assert json.loads(request.content)["model"] == "provider/fictional-model:free"
        return httpx.Response(200, json=provider_response())
    analyzer = LlmAnalyzer("fictional-api-key", "provider/fictional-model:free", transport=httpx.MockTransport(handler))
    assert validate_analysis(analyzer.analyze(CONTEXT)).confidence == 0.91


@pytest.mark.parametrize("response", [
    {"error": {"message": "simulated error"}}, {"choices": []}, {"choices": [{}]},
    provider_response(finish_reason="length"), provider_response(finish_reason="error"),
    {"choices": [{"finish_reason": "stop", "message": {"content": None, "refusal": "refused"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": []}}]},
])
def test_live_adapter_rejects_error_or_incomplete_responses(response):
    analyzer = LlmAnalyzer("fictional-api-key", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
    with pytest.raises(AnalyzerError):
        analyzer.analyze(CONTEXT)


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_provider_http_errors_keep_message_attached_and_manual(database, load_fixture, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "provider failure"}})
    analyzer = LlmAnalyzer("fictional-api-key", transport=httpx.MockTransport(handler))
    with TestClient(create_app(database, analyzer=analyzer)) as client:
        response = client.post("/webhooks/whatsapp", json=load_fixture("A"))
        assert response.status_code == 201
        message = response.json()["message"]
        assert message["dossier_reference"] == "D-1234"
        assert message["analysis"]["status"] == "manual_review"
        assert message["analysis"]["error"] == f"Analyzer provider returned HTTP {status}"
        assert "fictional-api-key" not in response.text
        assert client.post("/webhooks/whatsapp", json=load_fixture("A")).status_code == 200
        assert len(calls) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM dossier_history").fetchone()[0] == 0


def test_provider_timeout_is_handled_as_manual_review(database, load_fixture):
    def handler(request):
        raise httpx.ReadTimeout("simulated timeout", request=request)
    analyzer = LlmAnalyzer("fictional-api-key", transport=httpx.MockTransport(handler))
    with TestClient(create_app(database, analyzer=analyzer)) as client:
        response = client.post("/webhooks/whatsapp", json=load_fixture("A"))
        assert response.status_code == 201
        assert response.json()["message"]["analysis"]["status"] == "manual_review"


def test_valid_live_response_enters_same_proposal_flow(database, load_fixture):
    analyzer = LlmAnalyzer("fictional-api-key", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_response())))
    with TestClient(create_app(database, analyzer=analyzer)) as client:
        message = client.post("/webhooks/whatsapp", json=load_fixture("A")).json()["message"]
        assert message["analysis"]["status"] == "completed"
        proposal = message["proposals"][0]
        assert proposal["status"] == "pending"
        assert client.post(f"/proposals/{proposal['id']}/validate", json={"reviewer": "tutor-1"}).status_code == 200
        assert client.get("/dossiers/D-1234/messages").json()["dossier"]["status"] == "accord_principe"


def test_fake_analyzer_is_default_even_when_key_exists(monkeypatch):
    monkeypatch.delenv("COURTEE_ANALYZER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fictional-api-key")
    assert isinstance(analyzer_from_environment(), FakeAnalyzer)


def test_live_mode_uses_configured_key_and_model(monkeypatch):
    monkeypatch.setenv("COURTEE_ANALYZER", "llm")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fictional-api-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/fictional-model:free")
    analyzer = analyzer_from_environment()
    assert isinstance(analyzer, LlmAnalyzer)
    assert analyzer.model == "provider/fictional-model:free"


def test_live_mode_requires_key(monkeypatch):
    monkeypatch.setenv("COURTEE_ANALYZER", "llm")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        analyzer_from_environment()


def test_unknown_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("COURTEE_ANALYZER", "unknown")
    with pytest.raises(ValueError, match="COURTEE_ANALYZER"):
        analyzer_from_environment()


def test_dotenv_config_is_loaded_without_overriding_exported_values(database, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COURTEE_ANALYZER", "fake")
    monkeypatch.setenv("OPENROUTER_MODEL", "temporary-test-model")
    monkeypatch.delenv("COURTEE_ANALYZER", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "exported-fictional-key")
    (tmp_path / ".env").write_text("COURTEE_ANALYZER=llm\nOPENROUTER_MODEL=openrouter/free\nOPENROUTER_API_KEY=file-fictional-key\n")
    with TestClient(create_app(database)) as client:
        analyzer = client.app.state.message_service.analysis.analyzer
        assert isinstance(analyzer, LlmAnalyzer)
        assert analyzer.api_key == "exported-fictional-key"
        assert analyzer.model == "openrouter/free"
