import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ontology import GRAPH_DIR
from app.parser import DATA_DIR


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


@pytest.fixture(autouse=True)
def clean_dirs():
    for d in (DATA_DIR, GRAPH_DIR):
        if d.exists():
            shutil.rmtree(d)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    for d in (DATA_DIR, GRAPH_DIR):
        if d.exists():
            shutil.rmtree(d)


def write_document(filename="doc_raw.md", content="# Doc\nAlice works at Acme."):
    (DATA_DIR / filename).write_text(content)


def test_generate_schema_saves_and_returns_schema(monkeypatch):
    write_document()
    schema = {
        "node_types": [{"name": "Person", "description": "a person"}],
        "edge_types": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == schema
    saved = json.loads((GRAPH_DIR / "doc_raw" / "schema.json").read_text())
    assert saved == schema


def test_generate_schema_returns_400_on_invalid_json(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel("not json at all")
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 400


def test_generate_schema_returns_404_when_document_missing(monkeypatch):
    client = TestClient(app)

    response = client.post("/api/ontology/missing_raw.md/schema")

    assert response.status_code == 404


def test_extract_returns_400_when_schema_missing(monkeypatch):
    write_document()
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 400


def test_extract_saves_and_returns_graph(monkeypatch):
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))

    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    assert json.loads((schema_dir / "nodes.json").read_text()) == graph["nodes"]
    assert json.loads((schema_dir / "edges.json").read_text()) == graph["edges"]


def test_extract_returns_400_on_invalid_json(monkeypatch):
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    (schema_dir / "schema.json").write_text(json.dumps({"node_types": [], "edge_types": []}))
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel("nope")
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 400


def test_get_ontology_returns_saved_graph():
    graph_dir = GRAPH_DIR / "doc_raw"
    graph_dir.mkdir(parents=True)
    nodes = [{"id": "n1", "label": "Alice", "type": "Person"}]
    edges = []
    (graph_dir / "nodes.json").write_text(json.dumps(nodes))
    (graph_dir / "edges.json").write_text(json.dumps(edges))
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md")

    assert response.status_code == 200
    assert response.json() == {"nodes": nodes, "edges": edges}


def test_get_ontology_returns_404_when_not_extracted():
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md")

    assert response.status_code == 404
