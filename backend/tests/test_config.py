from fastapi.testclient import TestClient

from app.chat import MAX_TOKENS
from app.main import app


def test_get_config_returns_configured_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["max_tokens"] == MAX_TOKENS
    assert body["auth_required"] is False


def test_get_config_includes_model_catalog():
    client = TestClient(app)

    response = client.get("/api/config")

    body = response.json()
    assert "google/gemini-3.7-flash" in body["models"]
    assert "z-ai/glm-5.3" in body["models"]


def test_set_model_switches_active_model(monkeypatch):
    from app.chat import set_model_name

    client = TestClient(app)
    try:
        response = client.post("/api/config/model", json={"model": "z-ai/glm-5.3"})

        assert response.status_code == 200
        assert response.json() == {"model": "z-ai/glm-5.3"}
        assert client.get("/api/config").json()["model"] == "z-ai/glm-5.3"
    finally:
        set_model_name(None)


def test_set_model_returns_400_for_unknown_model():
    client = TestClient(app)

    response = client.post("/api/config/model", json={"model": "fake/nonexistent"})

    assert response.status_code == 400
    assert client.get("/api/config").json()["model"] == "openai/gpt-4o-mini"
