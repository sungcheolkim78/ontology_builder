import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import graphdb
from app.embeddings import EMBEDDING_DIM
from app.main import app
from app.ontology import GRAPH_DIR
from app.parser import DATA_DIR


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class FakeEmbeddingModel:
    def embed_documents(self, texts):
        return [[0.0] * EMBEDDING_DIM for _ in texts]


@pytest.fixture(autouse=True)
def stub_embedding_model(monkeypatch):
    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: FakeEmbeddingModel())


@pytest.fixture(autouse=True)
def clean_data_dir():
    graphdb.reset_connection()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    graphdb.reset_connection()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def test_list_files_returns_saved_filenames_newest_first():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "older_raw.md").write_text("old")
    (DATA_DIR / "older_raw.md").touch()
    os.utime(DATA_DIR / "older_raw.md", (1000, 1000))
    (DATA_DIR / "newer_raw.md").write_text("new")
    os.utime(DATA_DIR / "newer_raw.md", (2000, 2000))
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {
        "files": [{"filename": "newer_raw.md"}, {"filename": "older_raw.md"}]
    }


def test_list_files_excludes_hidden_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ".gitkeep").write_text("")
    (DATA_DIR / "report_raw.md").write_text("content")
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"files": [{"filename": "report_raw.md"}]}


def test_list_files_returns_empty_list_when_no_data_dir():
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"files": []}


def test_get_file_returns_saved_markdown_content():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "report_raw.md").write_text("# hello")
    client = TestClient(app)

    response = client.get("/api/files/report_raw.md")

    assert response.status_code == 200
    assert response.text == "# hello"


def test_get_file_returns_404_for_missing_file():
    client = TestClient(app)

    response = client.get("/api/files/does_not_exist.md")

    assert response.status_code == 404


def test_list_files_excludes_ladybugdb_files(monkeypatch):
    # Regression test: graphdb.DB_PATH must live outside DATA_DIR's top
    # level, since GET /api/files lists everything directly in DATA_DIR.
    # Extracting a graph creates the ladybug DB file (and a .wal sidecar);
    # neither should ever show up as a "document" here.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "doc_raw.md").write_text("# Doc\nAlice works at Acme.")
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    extract_response = client.post("/api/ontology/doc_raw.md/extract")
    assert extract_response.status_code == 200

    response = client.get("/api/files")

    assert response.status_code == 200
    filenames = [f["filename"] for f in response.json()["files"]]
    assert "graph.ladybugdb" not in filenames
    assert not any(".ladybugdb" in name for name in filenames)


def test_get_file_blocks_path_traversal():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    outside = DATA_DIR.parent / "secret.txt"
    outside.write_text("top secret")
    client = TestClient(app)

    try:
        response = client.get("/api/files/..%2Fsecret.txt")
        assert response.status_code == 404
    finally:
        outside.unlink()
