import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from app import graphdb
from app.embeddings import EMBEDDING_DIM
from app.main import app
from app.ontology import DOCUMENTS_DIR
from app.parser import DATA_DIR
from app.paths import document_dir_for


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


def write_raw(stem, content="content"):
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.md").write_text(content)
    return d


def test_list_files_returns_saved_filenames_newest_first():
    write_raw("older_raw", "old")
    os.utime(document_dir_for("older_raw") / "raw.md", (1000, 1000))
    write_raw("newer_raw", "new")
    os.utime(document_dir_for("newer_raw") / "raw.md", (2000, 2000))
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {
        "files": [{"filename": "newer_raw.md"}, {"filename": "older_raw.md"}]
    }


def test_list_files_excludes_hidden_entries():
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCUMENTS_DIR / ".DS_Store").write_text("")
    write_raw("report_raw")
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"files": [{"filename": "report_raw.md"}]}


def test_list_files_returns_empty_list_when_no_data_dir():
    client = TestClient(app)

    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"files": []}


def test_list_documents_reports_original_filename_and_schema_and_graph_status():
    from app.ontology import save_document_manifest

    write_raw("report_raw")
    save_document_manifest("report_raw", "report.docx")
    d = document_dir_for("report_raw")
    (d / "schema_v1.json").write_text(json.dumps({"node_types": [], "edge_types": []}))
    (d / "versions.json").write_text(
        json.dumps(
            {"active_version": 1, "versions": [{"version": 1, "document_type": "general", "created_at": None}]}
        )
    )
    client = TestClient(app)

    response = client.get("/api/documents")

    assert response.status_code == 200
    body = response.json()
    assert len(body["documents"]) == 1
    doc = body["documents"][0]
    assert doc["size_bytes"] > 0
    assert isinstance(doc["modified_at"], float)
    doc.pop("size_bytes")
    doc.pop("modified_at")
    assert doc == {
        "filename": "report_raw.md",
        "original_filename": "report.docx",
        "converter": "anydoc",
        "summary": None,
        "has_chunks": False,
        "has_goldenset": False,
        "has_schema": True,
        "has_graph": False,
        "graphdb_name": graphdb.DB_PATH.name,
    }


def test_list_documents_falls_back_to_derived_filename_without_manifest():
    write_raw("report_raw")
    client = TestClient(app)

    response = client.get("/api/documents")

    assert response.status_code == 200
    doc = response.json()["documents"][0]
    assert doc["original_filename"] == "report_raw.md"
    assert doc["has_schema"] is False
    assert doc["has_graph"] is False


def test_list_documents_reports_goldenset_status():
    write_raw("report_raw")
    from app.goldenset import save_goldenset

    save_goldenset("report_raw", {"source_file": "report_raw.md", "source_sha256": "x", "questions": []})
    client = TestClient(app)

    response = client.get("/api/documents")

    assert response.status_code == 200
    assert response.json()["documents"][0]["has_goldenset"] is True


def test_list_documents_returns_empty_list_when_no_data_dir():
    client = TestClient(app)

    response = client.get("/api/documents")

    assert response.status_code == 200
    assert response.json() == {"documents": []}


def test_get_file_returns_saved_markdown_content():
    write_raw("report_raw", "# hello")
    client = TestClient(app)

    response = client.get("/api/files/report_raw.md")

    assert response.status_code == 200
    assert response.text == "# hello"


def test_get_file_returns_404_for_missing_file():
    client = TestClient(app)

    response = client.get("/api/files/does_not_exist.md")

    assert response.status_code == 404


def test_list_files_excludes_ladybugdb_files(monkeypatch):
    # Regression test: graphdb.DB_PATH lives under data/graph/, a sibling of
    # data/documents/ -- GET /api/files only ever lists document folders, so
    # the ladybug DB file (and its .wal sidecar) must never show up here.
    write_raw("doc_raw", "# Doc\nAlice works at Acme.")
    d = document_dir_for("doc_raw")
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (d / "schema_v1.json").write_text(json.dumps(schema))
    (d / "versions.json").write_text(
        json.dumps(
            {"active_version": 1, "versions": [{"version": 1, "document_type": "general", "created_at": None}]}
        )
    )
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
