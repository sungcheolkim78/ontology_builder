from fastapi.testclient import TestClient

from app.main import app


class FakeChatModel:
    def invoke(self, messages):
        last = messages[-1]
        return type("FakeResponse", (), {"content": f"echo: {last.content}"})()


def test_chat_returns_assistant_reply(monkeypatch):
    monkeypatch.setattr("app.main.get_chat_model", lambda: FakeChatModel())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.json() == {"role": "assistant", "content": "echo: hello"}
