import json
import shutil

from fastapi.testclient import TestClient

from app.main import app
from app.ontology import GRAPH_DIR


class FakeChatModel:
    def invoke(self, messages):
        last = messages[-1]
        return type("FakeResponse", (), {"content": f"echo: {last.content}"})()


class SequencedChatModel:
    """Returns each response in order, one per invoke() call. Records the
    messages it was called with so tests can inspect what was actually sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        content = self.responses[len(self.calls) - 1]
        return type("FakeResponse", (), {"content": content})()


def test_chat_returns_assistant_reply(monkeypatch):
    monkeypatch.setattr("app.main.get_chat_model", lambda: FakeChatModel())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.json() == {"role": "assistant", "content": "echo: hello"}


def test_chat_with_filename_injects_graph_context(monkeypatch):
    graph_dir = GRAPH_DIR / "doc_raw"
    graph_dir.mkdir(parents=True)
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person"},
        {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    ]
    edges = [{"source": "n1", "target": "n2", "type": "WORKED_ON"}]
    (graph_dir / "nodes.json").write_text(json.dumps(nodes))
    (graph_dir / "edges.json").write_text(json.dumps(edges))

    model = SequencedChatModel(
        [json.dumps(["Ada Lovelace"]), "Ada Lovelace worked on the Analytical Engine."]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.main.get_chat_model", lambda: model)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "What did Ada Lovelace work on?"}],
                "filename": "doc_raw.md",
                "hops": 1,
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "role": "assistant",
            "content": "Ada Lovelace worked on the Analytical Engine.",
        }
        assert len(model.calls) == 2
        final_messages = model.calls[1]
        assert final_messages[0].content.startswith("다음은")
        assert "Analytical Engine" in final_messages[0].content
    finally:
        shutil.rmtree(GRAPH_DIR)


def test_chat_with_filename_but_no_graph_skips_retrieval(monkeypatch):
    model = SequencedChatModel(["plain answer"])
    monkeypatch.setattr("app.main.get_chat_model", lambda: model)
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "filename": "missing_raw.md",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"role": "assistant", "content": "plain answer"}
    assert len(model.calls) == 1
