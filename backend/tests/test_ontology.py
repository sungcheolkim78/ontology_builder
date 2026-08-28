import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from app.embeddings import EMBEDDING_DIM
from app.main import app
from app.ontology import DEFAULT_SCHEMA, GRAPH_DIR, embed_graph, embed_nodes
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
def clean_dirs():
    from app import graphdb
    graphdb.reset_connection()
    for d in (DATA_DIR, GRAPH_DIR):
        if d.exists():
            shutil.rmtree(d)
    if graphdb.DB_PATH.exists():
        if graphdb.DB_PATH.is_file():
            os.remove(graphdb.DB_PATH)
        else:
            shutil.rmtree(graphdb.DB_PATH)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    graphdb.reset_connection()
    for d in (DATA_DIR, GRAPH_DIR):
        if d.exists():
            shutil.rmtree(d)
    if graphdb.DB_PATH.exists():
        if graphdb.DB_PATH.is_file():
            os.remove(graphdb.DB_PATH)
        else:
            shutil.rmtree(graphdb.DB_PATH)


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


class RecordingChatModel:
    def __init__(self, content):
        self.content = content
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("FakeResponse", (), {"content": self.content})()


def test_generate_schema_uses_legal_prompt_for_legal_document_type(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: fake_model)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema", json={"document_type": "legal"}
    )

    assert response.status_code == 200
    assert "defined terms" in fake_model.prompts[0]


def test_generate_schema_returns_400_on_unknown_document_type(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel("{}")
    )
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema", json={"document_type": "nonsense"}
    )

    assert response.status_code == 400


def test_embed_nodes_attaches_a_vector_per_node(monkeypatch):
    calls = []

    class FakeEmbeddingModel:
        def embed_documents(self, texts):
            calls.append(texts)
            return [[float(i)] * EMBEDDING_DIM for i in range(len(texts))]

    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: FakeEmbeddingModel())
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person", "detail": "Mathematician"},
        {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    ]

    embedded = embed_nodes(nodes)

    assert calls == [["Ada Lovelace: Mathematician", "Analytical Engine"]]
    assert [n["embedding"] for n in embedded] == [[0.0] * EMBEDDING_DIM, [1.0] * EMBEDDING_DIM]
    # Original dicts (and the input list) must be left untouched.
    assert "embedding" not in nodes[0]


def test_embed_nodes_empty_list_skips_the_embedding_call(monkeypatch):
    def fail():
        raise AssertionError("should not be called for an empty node list")

    monkeypatch.setattr("app.ontology.get_embedding_model", fail)

    assert embed_nodes([]) == []


def test_embed_graph_computes_and_stores_embeddings():
    from app import graphdb

    graphdb.write_graph(
        "doc_raw", [{"id": "n1", "label": "Alice", "type": "Person", "detail": "engineer"}], []
    )

    count = embed_graph("doc_raw")

    assert count == 1
    matched = graphdb.find_similar_nodes("doc_raw", "Person", [0.0] * EMBEDDING_DIM, top_k=1)
    assert matched == ["n1"]


def test_embed_graph_returns_zero_when_no_graph_extracted():
    assert embed_graph("doc_raw") == 0


def test_embed_endpoint_embeds_the_extracted_graph(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)
    client.post("/api/ontology/doc_raw.md/extract")

    response = client.post("/api/ontology/doc_raw.md/embed")

    assert response.status_code == 200
    assert response.json() == {"embedded": 1}


def test_embed_endpoint_returns_404_when_not_extracted():
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/embed")

    assert response.status_code == 404


def test_extract_uses_and_saves_default_schema_when_none_saved(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    saved_schema = json.loads((GRAPH_DIR / "doc_raw" / "schema.json").read_text())
    assert saved_schema == DEFAULT_SCHEMA


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
    from app import graphdb
    assert graphdb.load_graph("doc_raw") == graph


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


def test_extract_drops_edges_with_unknown_node_ids(monkeypatch):
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))

    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [
            {"source": "n1", "target": "does_not_exist", "type": "KNOWS"},
        ],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json()["edges"] == []


def test_get_ontology_returns_saved_graph():
    from app import graphdb
    nodes = [{"id": "n1", "label": "Alice", "type": "Person"}]
    edges = []
    graphdb.write_graph("doc_raw", nodes, edges)
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md")

    assert response.status_code == 200
    assert response.json() == {"nodes": nodes, "edges": edges}


def test_get_ontology_returns_404_when_not_extracted():
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md")

    assert response.status_code == 404


def test_reset_database_endpoint_clears_extracted_graphs():
    from app import graphdb
    graphdb.write_graph("doc_raw", [{"id": "n1", "label": "Alice", "type": "Person"}], [])
    client = TestClient(app)

    response = client.post("/api/ontology/reset-database")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert graphdb.has_graph("doc_raw") is False


def test_list_schemas_returns_empty_when_none():
    client = TestClient(app)

    response = client.get("/api/ontology/schemas")

    assert response.status_code == 200
    assert response.json() == {"schemas": []}


def test_list_schemas_returns_stems_with_a_saved_schema():
    schema = {"node_types": [], "edge_types": []}
    for stem in ("doc_raw", "other_raw"):
        d = GRAPH_DIR / stem
        d.mkdir(parents=True)
        (d / "schema.json").write_text(json.dumps(schema))
    # a graph dir with no schema.json shouldn't be listed
    (GRAPH_DIR / "no_schema_raw").mkdir(parents=True)
    client = TestClient(app)

    response = client.get("/api/ontology/schemas")

    assert response.status_code == 200
    assert sorted(s["stem"] for s in response.json()["schemas"]) == [
        "doc_raw",
        "other_raw",
    ]


def test_save_and_load_document_manifest_round_trips():
    from app.ontology import load_document_manifest, save_document_manifest

    save_document_manifest("doc_raw", "report.docx")

    assert load_document_manifest("doc_raw") == {"original_filename": "report.docx"}


def test_load_document_manifest_returns_none_when_missing():
    from app.ontology import load_document_manifest

    assert load_document_manifest("never_uploaded") is None


def test_get_schema_returns_saved_schema():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    d = GRAPH_DIR / "doc_raw"
    d.mkdir(parents=True)
    (d / "schema.json").write_text(json.dumps(schema))
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == schema


def test_get_schema_returns_404_when_missing():
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 404


def test_use_schema_copies_source_schema_to_target():
    write_document("target_raw.md")
    source_schema = {
        "node_types": [{"name": "Organization", "description": "an org"}],
        "edge_types": [],
    }
    source_dir = GRAPH_DIR / "source_raw"
    source_dir.mkdir(parents=True)
    (source_dir / "schema.json").write_text(json.dumps(source_schema))
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "source_raw"}
    )

    assert response.status_code == 200
    assert response.json() == source_schema
    saved = json.loads((GRAPH_DIR / "target_raw" / "schema.json").read_text())
    assert saved == source_schema


def test_use_schema_returns_404_when_source_missing():
    write_document("target_raw.md")
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "missing_raw"}
    )

    assert response.status_code == 404


def test_create_schema_version_increments_and_activates():
    from app.ontology import create_schema_version, get_active_version, list_versions

    v1 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []}, "general")
    v2 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []}, "legal")

    assert v1 == 1
    assert v2 == 2
    assert get_active_version("doc_raw") == 2
    assert [v["version"] for v in list_versions("doc_raw")] == [1, 2]


def test_activate_version_switches_active_pointer():
    from app.ontology import activate_version, create_schema_version, get_active_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    activate_version("doc_raw", 1)

    assert get_active_version("doc_raw") == 1


def test_activate_version_raises_for_unknown_version():
    from app.ontology import activate_version, create_schema_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    with pytest.raises(ValueError):
        activate_version("doc_raw", 99)


def test_delete_version_removes_schema_file_and_graph_rows():
    from app import graphdb
    from app.ontology import create_schema_version, delete_version, list_versions

    v1 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    graphdb.write_graph(
        "doc_raw", [{"id": "n1", "label": "Alice", "type": "Person"}], [], version=v1
    )

    delete_version("doc_raw", v1)

    assert list_versions("doc_raw") == []
    assert not (GRAPH_DIR / "doc_raw" / "schema_v1.json").is_file()
    assert graphdb.has_graph("doc_raw", version=1) is False


def test_delete_active_version_reactivates_most_recent_remaining():
    from app.ontology import create_schema_version, delete_version, get_active_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    delete_version("doc_raw", 2)

    assert get_active_version("doc_raw") == 1


def test_delete_version_raises_for_unknown_version():
    from app.ontology import create_schema_version, delete_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    with pytest.raises(ValueError):
        delete_version("doc_raw", 99)


def test_get_active_version_returns_none_when_no_versions_exist():
    from app.ontology import get_active_version

    assert get_active_version("never_seen") is None


def test_generate_schema_response_includes_version(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == {**schema, "version": 1}
    saved = json.loads((GRAPH_DIR / "doc_raw" / "schema_v1.json").read_text())
    assert saved == schema
    versions = json.loads((GRAPH_DIR / "doc_raw" / "versions.json").read_text())
    assert versions["active_version"] == 1


def test_generate_schema_second_call_creates_second_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.json()["version"] == 2
    assert (GRAPH_DIR / "doc_raw" / "schema_v1.json").is_file()
    assert (GRAPH_DIR / "doc_raw" / "schema_v2.json").is_file()


def test_list_schema_versions_endpoint_reports_active_and_graph_status(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.get("/api/ontology/doc_raw.md/schema/versions")

    assert response.status_code == 200
    versions = response.json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert [v["is_active"] for v in versions] == [False, True]
    assert [v["has_graph"] for v in versions] == [False, False]


def test_activate_schema_version_endpoint_switches_active_version(monkeypatch):
    write_document()
    schema_v1 = {"node_types": [{"name": "Person", "description": "v1"}], "edge_types": []}
    schema_v2 = {"node_types": [{"name": "Organization", "description": "v2"}], "edge_types": []}
    client = TestClient(app)

    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema_v1)))
    client.post("/api/ontology/doc_raw.md/schema")
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema_v2)))
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.post("/api/ontology/doc_raw.md/schema/versions/1/activate")

    assert response.status_code == 200
    assert client.get("/api/ontology/doc_raw.md/schema").json() == schema_v1


def test_activate_schema_version_endpoint_returns_404_for_unknown_version():
    write_document()
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema/versions/99/activate")

    assert response.status_code == 404


def test_delete_schema_version_endpoint_removes_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)
    client.post("/api/ontology/doc_raw.md/schema")
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.delete("/api/ontology/doc_raw.md/schema/versions/2")

    assert response.status_code == 200
    versions = client.get("/api/ontology/doc_raw.md/schema/versions").json()["versions"]
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["is_active"] is True


def test_delete_schema_version_endpoint_returns_404_for_unknown_version():
    write_document()
    client = TestClient(app)

    response = client.delete("/api/ontology/doc_raw.md/schema/versions/99")

    assert response.status_code == 404
