import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from app.embeddings import EMBEDDING_DIM
from app.main import app
from app.ontology import GRAPH_DIR
from app import graphdb

NODES = [
    {"id": "n1", "label": "Ada Lovelace", "type": "Person"},
    {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
]
EDGES = [{"source": "n1", "target": "n2", "type": "WORKED_ON"}]
SCHEMA = {
    "node_types": [
        {"name": "Person", "description": "a person"},
        {"name": "Concept", "description": "a concept"},
    ],
    "edge_types": [
        {"name": "WORKED_ON", "description": "worked on", "source": "Person", "target": "Concept"}
    ],
}


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


class FakeEmbeddingModel:
    def embed_documents(self, texts):
        return [[0.0] * EMBEDDING_DIM for _ in texts]


@pytest.fixture(autouse=True)
def stub_embedding_model(monkeypatch):
    monkeypatch.setattr("app.graphrag.get_embedding_model", lambda: FakeEmbeddingModel())


def write_graph_dir(stem="doc_raw", schema=SCHEMA, nodes=NODES, edges=EDGES):
    graph_dir = GRAPH_DIR / stem
    graph_dir.mkdir(parents=True)
    (graph_dir / "schema_v1.json").write_text(json.dumps(schema))
    (graph_dir / "versions.json").write_text(
        json.dumps(
            {"active_version": 1, "versions": [{"version": 1, "document_type": "general", "created_at": None}]}
        )
    )
    graphdb.write_graph(stem, nodes, edges)
    return graph_dir


def test_chat_returns_assistant_reply(monkeypatch):
    monkeypatch.setattr("app.main.get_chat_model", lambda: FakeChatModel())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.json() == {"role": "assistant", "content": "echo: hello"}


def test_chat_with_filename_injects_graph_context_and_returns_type_analysis(monkeypatch):
    write_graph_dir()
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": ["WORKED_ON"],
                    "keywords": {"Person": ["Ada Lovelace"]},
                }
            ),
            "Ada Lovelace worked on the Analytical Engine.",
        ]
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
        body = response.json()
        assert body["role"] == "assistant"
        assert body["content"] == "Ada Lovelace worked on the Analytical Engine."
        assert body["node_types"] == ["Person"]
        assert body["edge_types"] == ["WORKED_ON"]
        assert len(model.calls) == 2
        final_messages = model.calls[1]
        assert final_messages[0].content.startswith("다음은")
        assert "Analytical Engine" in final_messages[0].content
        assert {n["label"] for n in body["related_nodes"]} == {
            "Ada Lovelace",
            "Analytical Engine",
        }
        assert [e["type"] for e in body["related_edges"]] == ["WORKED_ON"]
    finally:
        graphdb.reset_connection()
        if GRAPH_DIR.exists():
            shutil.rmtree(GRAPH_DIR)
        if graphdb.DB_PATH.exists():
            if graphdb.DB_PATH.is_file():
                os.remove(graphdb.DB_PATH)
            else:
                shutil.rmtree(graphdb.DB_PATH)


def test_chat_reports_not_found_when_no_types_relevant(monkeypatch):
    write_graph_dir()
    model = SequencedChatModel(
        [json.dumps({"node_types": [], "edge_types": [], "keywords": {}})]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.main.get_chat_model", lambda: model)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "완전히 무관한 질문"}],
                "filename": "doc_raw.md",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["content"] == "관련된 내용을 찾을 수 없습니다."
        assert body["node_types"] == []
        assert body["edge_types"] == []
        assert len(model.calls) == 1  # only type analysis, no final answer call
    finally:
        graphdb.reset_connection()
        if GRAPH_DIR.exists():
            shutil.rmtree(GRAPH_DIR)
        if graphdb.DB_PATH.exists():
            if graphdb.DB_PATH.is_file():
                os.remove(graphdb.DB_PATH)
            else:
                shutil.rmtree(graphdb.DB_PATH)


def test_chat_falls_back_to_all_instances_when_no_keyword_match(monkeypatch):
    # A category-style question ("who are the people mentioned?") or a
    # question/document language mismatch means no keyword literally
    # matches a node label. Since the type analysis found a real, relevant
    # type, the answer should still use every instance of that type rather
    # than reporting "not found."
    write_graph_dir()
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": [],
                    "keywords": {"Person": ["a stranger not in the graph"]},
                }
            ),
            "Ada Lovelace is the person mentioned.",
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.main.get_chat_model", lambda: model)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "언급된 사람은 누구인가요?"}],
                "filename": "doc_raw.md",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["node_types"] == ["Person"]
        assert body["content"] == "Ada Lovelace is the person mentioned."
        assert len(model.calls) == 2
        final_messages = model.calls[1]
        assert "Ada Lovelace" in final_messages[0].content
    finally:
        graphdb.reset_connection()
        if GRAPH_DIR.exists():
            shutil.rmtree(GRAPH_DIR)
        if graphdb.DB_PATH.exists():
            if graphdb.DB_PATH.is_file():
                os.remove(graphdb.DB_PATH)
            else:
                shutil.rmtree(graphdb.DB_PATH)


def test_chat_reports_not_found_when_determined_type_has_no_instances(monkeypatch):
    # "Location" is a real, valid schema type (so type analysis isn't
    # filtering it out), but there happens to be zero Location nodes
    # actually extracted -- the fallback has nothing to fall back to, so
    # this should still be a genuine "not found."
    schema_with_unused_type = {
        "node_types": SCHEMA["node_types"] + [{"name": "Location", "description": "a place"}],
        "edge_types": SCHEMA["edge_types"],
    }
    write_graph_dir(schema=schema_with_unused_type)
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Location"],
                    "edge_types": [],
                    "keywords": {"Location": ["nonexistent keyword"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    monkeypatch.setattr("app.main.get_chat_model", lambda: model)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "어디에서 일했나요?"}],
                "filename": "doc_raw.md",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["node_types"] == ["Location"]
        assert body["content"] == "관련된 내용을 찾을 수 없습니다."
        assert len(model.calls) == 1
    finally:
        graphdb.reset_connection()
        if GRAPH_DIR.exists():
            shutil.rmtree(GRAPH_DIR)
        if graphdb.DB_PATH.exists():
            if graphdb.DB_PATH.is_file():
                os.remove(graphdb.DB_PATH)
            else:
                shutil.rmtree(graphdb.DB_PATH)


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
