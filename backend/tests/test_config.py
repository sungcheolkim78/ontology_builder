from fastapi.testclient import TestClient

from app.main import app


def test_get_config_returns_configured_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"model": "openai/gpt-4o-mini"}
