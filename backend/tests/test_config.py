from fastapi.testclient import TestClient

from app.main import app


def test_get_config_returns_configured_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["max_tokens"] is None  # uncataloged model: no cap is sent
    assert body["auth_required"] is False


def test_get_config_includes_model_catalog_with_per_model_caps():
    client = TestClient(app)

    response = client.get("/api/config")

    body = response.json()
    by_id = {m["id"]: m["max_tokens"] for m in body["models"]}
    assert "google/gemini-3.7-flash" in by_id
    assert "z-ai/glm-5.3-flash" in by_id
    assert "z-ai/glm-5.3" in by_id
    assert by_id["google/gemini-3.7-flash"] == 65_536
    assert by_id["z-ai/glm-5.3-flash"] == 131_072
    assert by_id["z-ai/glm-5.3"] == 131_072


def test_get_config_includes_operation_models_defaulting_to_the_general_model():
    client = TestClient(app)

    body = client.get("/api/config").json()

    assert body["operation_models"] == {
        "discover_ontology": body["model"],
        "generate_schema": body["model"],
        "extract_graph": body["model"],
        "validate_ontology": body["model"],
    }


def test_set_model_switches_active_model():
    from app.chat import set_model_name

    client = TestClient(app)
    try:
        response = client.post("/api/config/model", json={"model": "z-ai/glm-5.3"})

        assert response.status_code == 200
        assert response.json() == {"model": "z-ai/glm-5.3", "operation": None}
        config = client.get("/api/config").json()
        assert config["model"] == "z-ai/glm-5.3"
        assert config["max_tokens"] == 131_072
        # Switching the general model also shifts every operation that has no
        # override of its own, since operation_models falls back to it.
        assert config["operation_models"]["discover_ontology"] == "z-ai/glm-5.3"
    finally:
        set_model_name(None)


def test_set_model_for_one_operation_leaves_others_on_the_general_model():
    from app.chat import set_model_name

    client = TestClient(app)
    try:
        response = client.post(
            "/api/config/model",
            json={"model": "anthropic/claude-opus-5", "operation": "extract_graph"},
        )

        assert response.status_code == 200
        assert response.json() == {"model": "anthropic/claude-opus-5", "operation": "extract_graph"}
        config = client.get("/api/config").json()
        assert config["operation_models"]["extract_graph"] == "anthropic/claude-opus-5"
        assert config["operation_models"]["generate_schema"] == config["model"]
    finally:
        set_model_name(None, "extract_graph")


def test_set_model_returns_400_for_unknown_operation():
    client = TestClient(app)

    response = client.post(
        "/api/config/model", json={"model": "z-ai/glm-5.3", "operation": "not_a_real_operation"}
    )

    assert response.status_code == 400


def test_set_model_returns_400_for_unknown_model():
    client = TestClient(app)

    response = client.post("/api/config/model", json={"model": "fake/nonexistent"})

    assert response.status_code == 400
    assert client.get("/api/config").json()["model"] == "z-ai/glm-5.3-flash"
