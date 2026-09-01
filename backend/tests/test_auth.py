from fastapi.testclient import TestClient

from app.main import app


def test_login_rejects_when_app_password_unset(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "")
    monkeypatch.setattr("app.main.APP_PASSWORD", "")
    client = TestClient(app)

    response = client.post("/api/login", json={"password": "anything"})

    assert response.status_code == 401


def test_login_accepts_correct_password_and_protected_route_requires_it(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    unauthenticated = client.get("/api/hello")
    assert unauthenticated.status_code == 401

    login = client.post("/api/login", json={"password": "hunter2"})
    assert login.status_code == 200
    token = login.json()["token"]

    authenticated = client.get("/api/hello", headers={"Authorization": f"Bearer {token}"})
    assert authenticated.status_code == 200


def test_login_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    response = client.post("/api/login", json={"password": "wrong"})

    assert response.status_code == 401


def test_health_and_config_and_login_stay_open_when_app_password_set(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401


def test_options_preflight_bypasses_auth_even_on_protected_routes(monkeypatch):
    """Regression test: a CORS preflight OPTIONS request never carries the
    app's own Authorization header, so requiring one here breaks every real
    cross-origin request behind it -- observed in production as OPTIONS
    /api/parse returning 401 the moment APP_PASSWORD was set."""
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    response = client.options(
        "/api/parse",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code != 401
