import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from app.embeddings import EMBEDDING_DIM
from app.main import app
from app.ontology import DEFAULT_SCHEMA, DOCUMENTS_DIR, DOMAIN_SCHEMA_DIR, embed_graph, embed_nodes
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
def clean_dirs():
    from app import graphdb
    graphdb.reset_connection()
    for d in (DATA_DIR, DOCUMENTS_DIR, DOMAIN_SCHEMA_DIR):
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
    for d in (DATA_DIR, DOCUMENTS_DIR, DOMAIN_SCHEMA_DIR):
        if d.exists():
            shutil.rmtree(d)
    if graphdb.DB_PATH.exists():
        if graphdb.DB_PATH.is_file():
            os.remove(graphdb.DB_PATH)
        else:
            shutil.rmtree(graphdb.DB_PATH)


def write_document(filename="doc_raw.md", content="# Doc\nAlice works at Acme."):
    stem = filename.removesuffix(".md")
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.md").write_text(content)


def seed_schema_version(stem, schema, version=1, document_type="general"):
    d = DOCUMENTS_DIR / stem
    d.mkdir(parents=True, exist_ok=True)
    (d / f"schema_v{version}.json").write_text(json.dumps(schema))
    (d / "versions.json").write_text(
        json.dumps(
            {
                "active_version": version,
                "versions": [
                    {"version": version, "document_type": document_type, "created_at": None}
                ],
            }
        )
    )


def test_generate_schema_returns_400_on_invalid_json(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("not json at all")
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
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema", json={"document_type": "legal"}
    )

    assert response.status_code == 200
    assert "defined terms" in fake_model.prompts[0]


def test_generate_schema_returns_400_on_unknown_document_type(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("{}")
    )
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema", json={"document_type": "nonsense"}
    )

    assert response.status_code == 400


def test_discover_endpoint_saves_and_returns_report(monkeypatch):
    write_document()
    report = {
        "domain_model": {"domain": "insurance", "subdomains": [], "document_types": [], "business_processes": [], "major_actors": []},
        "classes": [{"name": "Policy", "definition": "a policy", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}],
        "relationships": [],
        "attributes": [],
        "events": [],
        "rules": [],
        "terminology": [],
        "competency_questions": ["What does this cover?"],
        "warnings": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/discover")

    assert response.status_code == 200
    assert response.json() == report

    get_response = client.get("/api/ontology/doc_raw.md/discover")
    assert get_response.status_code == 200
    assert get_response.json() == report


def test_discover_returns_404_when_document_missing():
    client = TestClient(app)

    response = client.post("/api/ontology/missing_raw.md/discover")

    assert response.status_code == 404


def test_discover_returns_400_on_invalid_json(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("not json at all")
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/discover")

    assert response.status_code == 400


def test_get_discovery_returns_404_when_none_saved():
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md/discover")

    assert response.status_code == 404


def _discovery_report(domain="d", classes=None, relationships=None, competency_questions=None):
    return {
        "domain_model": {"domain": domain, "subdomains": [], "document_types": [], "business_processes": [], "major_actors": []},
        "classes": classes or [],
        "relationships": relationships or [],
        "attributes": [],
        "events": [],
        "rules": [],
        "terminology": [],
        "competency_questions": competency_questions or [],
        "warnings": [],
    }


def test_group_chunks_by_budget_packs_by_budget():
    from app.ontology import group_chunks_by_budget

    chunks = [{"text": "a" * 30}, {"text": "b" * 30}, {"text": "c" * 30}, {"text": "d" * 30}]

    groups = group_chunks_by_budget(chunks, max_group_chars=50)

    assert [len(g) for g in groups] == [1, 1, 1, 1]
    groups = group_chunks_by_budget(chunks, max_group_chars=65)
    assert [len(g) for g in groups] == [2, 2]


def test_group_chunks_by_budget_keeps_oversized_chunk_alone():
    from app.ontology import group_chunks_by_budget

    chunks = [{"text": "x" * 10}, {"text": "y" * 200}, {"text": "z" * 10}]

    groups = group_chunks_by_budget(chunks, max_group_chars=50)

    assert [len(g) for g in groups] == [1, 1, 1]


def test_discover_ontology_from_chunks_single_group_skips_consolidation(monkeypatch):
    from app.ontology import discover_ontology_from_chunks

    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    fake_model = RecordingChatModel(json.dumps(report))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = discover_ontology_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000)

    assert result == report
    assert len(fake_model.prompts) == 1


def test_discover_ontology_from_chunks_consolidates_multiple_groups(monkeypatch):
    from app.ontology import discover_ontology_from_chunks

    group1 = _discovery_report(
        domain="insurance",
        classes=[{"name": "Policy", "definition": "d1", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}],
        relationships=[],
        competency_questions=["What does this cover?"],
    )
    group2 = _discovery_report(
        domain="insurance",
        classes=[{"name": "InsurancePolicy", "definition": "d2", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}],
        relationships=[],
        competency_questions=["What does this cover?"],
    )
    consolidated = {
        "classes": [{"name": "Policy", "definition": "merged", "category": "CONCEPT", "parent": "", "rationale": "merged d1/d2", "confidence": "HIGH"}],
        "relationships": [],
    }
    fake_model = SequencedChatModel([json.dumps(group1), json.dumps(group2), json.dumps(consolidated)])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = discover_ontology_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}], max_group_chars=30
    )

    assert result["classes"] == consolidated["classes"]
    assert result["relationships"] == []
    # competency_questions deduped across groups (identical string in both)
    assert result["competency_questions"] == ["What does this cover?"]
    assert result["domain_model"]["domain"] == "insurance"
    assert fake_model.calls == 3


def test_discover_endpoint_uses_chunks_when_present(monkeypatch):
    write_document()
    stem = "doc_raw"
    (document_dir_for(stem) / "chunks.json").write_text(
        json.dumps(
            {
                "source": stem,
                "preamble": {"line_start": 1, "line_end": 1, "text": ""},
                "chunks": [
                    {"id": "0::제1조", "section_index": 0, "section_label": "주계약", "article_no": "1", "sub_no": None, "title": "목적", "path": "주계약 > 제1조(목적)", "line_start": 1, "line_end": 2, "text": "Alice works at Acme."},
                ],
            }
        )
    )
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/discover")

    assert response.status_code == 200
    assert response.json() == report


def test_generate_schema_from_chunks_single_group_skips_consolidation(monkeypatch):
    from app.ontology import generate_schema_from_chunks

    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = generate_schema_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000)

    assert result == schema
    assert len(fake_model.prompts) == 1


def test_generate_schema_from_chunks_consolidates_multiple_groups(monkeypatch):
    from app.ontology import generate_schema_from_chunks

    schema1 = {"node_types": [{"name": "Policy", "description": "d1"}], "edge_types": []}
    schema2 = {"node_types": [{"name": "InsurancePolicy", "description": "d2"}], "edge_types": []}
    consolidated = {"node_types": [{"name": "Policy", "description": "merged"}], "edge_types": []}
    fake_model = SequencedChatModel([json.dumps(schema1), json.dumps(schema2), json.dumps(consolidated)])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = generate_schema_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}], max_group_chars=30
    )

    assert result == consolidated
    assert fake_model.calls == 3


def test_schema_endpoint_uses_chunks_when_present(monkeypatch):
    write_document()
    stem = "doc_raw"
    (document_dir_for(stem) / "chunks.json").write_text(
        json.dumps(
            {
                "source": stem,
                "preamble": {"line_start": 1, "line_end": 1, "text": ""},
                "chunks": [
                    {"id": "0::제1조", "section_index": 0, "section_label": "주계약", "article_no": "1", "sub_no": None, "title": "목적", "path": "주계약 > 제1조(목적)", "line_start": 1, "line_end": 2, "text": "Alice works at Acme."},
                ],
            }
        )
    )
    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["node_types"] == schema["node_types"]
    assert body["version"] == 1


def test_generate_schema_ignores_discovery_by_default(monkeypatch):
    write_document()
    (DOCUMENTS_DIR / "doc_raw").mkdir(parents=True, exist_ok=True)
    (DOCUMENTS_DIR / "doc_raw" / "discovery.json").write_text(json.dumps({"classes": [{"name": "Policy"}]}))
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")

    assert "Reference --" not in fake_model.prompts[0]


def test_generate_schema_includes_discovery_hint_when_requested(monkeypatch):
    write_document()
    (DOCUMENTS_DIR / "doc_raw").mkdir(parents=True, exist_ok=True)
    (DOCUMENTS_DIR / "doc_raw" / "discovery.json").write_text(json.dumps({"classes": [{"name": "Policy"}]}))
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema", json={"use_discovery": True})

    assert response.status_code == 200
    assert "Reference --" in fake_model.prompts[0]
    assert "Policy" in fake_model.prompts[0]


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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    saved_schema = json.loads((DOCUMENTS_DIR / "doc_raw" / "schema_v1.json").read_text())
    assert saved_schema == DEFAULT_SCHEMA


def test_extract_saves_and_returns_graph(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)

    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    from app import graphdb
    assert graphdb.load_graph("doc_raw", version=1) == graph


def test_extract_returns_400_on_invalid_json(monkeypatch):
    write_document()
    seed_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("nope")
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 400


def test_extract_drops_edges_with_unknown_node_ids(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)

    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [
            {"source": "n1", "target": "does_not_exist", "type": "KNOWS"},
        ],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json()["edges"] == []


def test_extract_graph_preserves_minimal_shape_when_no_structured_metadata(monkeypatch):
    # An LLM response with none of the new optional fields must round-trip
    # with exactly the old node/edge shape -- no properties/confidence/
    # evidence/source_section key should appear out of nowhere.
    from app.ontology import extract_graph

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works here.", schema)

    assert result == graph


def test_extract_graph_verifies_evidence_against_document_text(monkeypatch):
    from app.ontology import extract_graph

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [
            {
                "id": "n1",
                "label": "Alice",
                "type": "Person",
                "evidence": "Alice works at Acme.",
            }
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works at Acme.", schema)

    node = result["nodes"][0]
    assert node["evidence_text"] == "Alice works at Acme."
    assert node["start_offset"] == 0
    assert node["end_offset"] == len("Alice works at Acme.")


def test_extract_graph_drops_evidence_not_found_verbatim_in_document():
    from app.ontology import extract_graph as _  # noqa: F401 -- import sanity only
    from app.ontology import _find_evidence_span

    assert _find_evidence_span("hallucinated quote", "Alice works at Acme.") is None
    assert _find_evidence_span(None, "Alice works at Acme.") is None
    assert _find_evidence_span("", "Alice works at Acme.") is None


def test_extract_graph_drops_evidence_offsets_for_hallucinated_quote(monkeypatch):
    from app.ontology import extract_graph

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [
            {"id": "n1", "label": "Alice", "type": "Person", "evidence": "not in the document"}
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works at Acme.", schema)

    node = result["nodes"][0]
    assert "evidence" not in node
    assert "evidence_text" not in node
    assert "start_offset" not in node
    assert "end_offset" not in node


def test_extract_graph_keeps_only_schema_declared_properties(monkeypatch):
    from app.ontology import extract_graph

    schema = {
        "node_types": [
            {
                "name": "Coverage",
                "description": "a coverage",
                "properties": {"amount": {"datatype": "string"}},
            }
        ],
        "edge_types": [],
    }
    graph = {
        "nodes": [
            {
                "id": "n1",
                "label": "암보장",
                "type": "Coverage",
                # "amount" is declared; "made_up" is not and must be dropped.
                "properties": {"amount": "50%", "made_up": "should not survive"},
            }
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("document text", schema)

    assert result["nodes"][0]["properties"] == {"amount": "50%"}


def test_extract_graph_ignores_malformed_property_map(monkeypatch):
    from app.ontology import extract_graph

    schema = {
        "node_types": [
            {"name": "Coverage", "description": "d", "properties": {"amount": {"datatype": "string"}}}
        ],
        "edge_types": [],
    }
    graph = {
        "nodes": [{"id": "n1", "label": "x", "type": "Coverage", "properties": "not-a-dict"}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("document text", schema)

    assert "properties" not in result["nodes"][0]


def test_extract_graph_normalizes_confidence_and_drops_invalid_values(monkeypatch):
    from app.ontology import extract_graph

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [
            {"id": "n1", "label": "Alice", "type": "Person", "confidence": "HIGH"},
            {"id": "n2", "label": "Bob", "type": "Person", "confidence": "MAYBE"},
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice and Bob.", schema)

    assert result["nodes"][0]["confidence"] == "HIGH"
    assert "confidence" not in result["nodes"][1]


def test_extract_graph_keeps_source_section_only_when_it_matches_a_real_label(monkeypatch):
    from app.ontology import extract_graph

    schema = {"node_types": [{"name": "Coverage", "description": "d"}], "edge_types": []}
    document_text = "[주계약 > 제17조(보험금의 지급)]\n암 진단 확정 시 지급한다."
    graph = {
        "nodes": [
            {
                "id": "n1",
                "label": "암보장",
                "type": "Coverage",
                "source_section": "주계약 > 제17조(보험금의 지급)",
            },
            {
                "id": "n2",
                "label": "다른보장",
                "type": "Coverage",
                "source_section": "존재하지 않는 조항",
            },
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph(document_text, schema)

    assert result["nodes"][0]["source_section"] == "주계약 > 제17조(보험금의 지급)"
    assert "source_section" not in result["nodes"][1]


def _load_legal_fixture():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "legal_policy_expected.json")
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def test_flag_structural_catchall_nodes_accepts_reified_legal_graph():
    from app.ontology import flag_structural_catchall_nodes

    fixture = _load_legal_fixture()
    assert flag_structural_catchall_nodes(fixture["reference_graph"]) == []


def test_flag_structural_catchall_nodes_flags_detail_only_article():
    from app.ontology import flag_structural_catchall_nodes

    fixture = _load_legal_fixture()
    issues = flag_structural_catchall_nodes(fixture["reference_graph_with_catchall_violation"])

    assert len(issues) == 1
    assert issues[0]["code"] == "structural_catchall"
    assert issues[0]["node_id"] == "article17"


def test_flag_structural_catchall_nodes_ignores_structural_node_with_no_detail():
    from app.ontology import flag_structural_catchall_nodes

    graph = {"nodes": [{"id": "a1", "type": "Article", "label": "제1조", "detail": ""}], "edges": []}
    assert flag_structural_catchall_nodes(graph) == []


def test_validate_legal_edge_shapes_accepts_reified_legal_graph():
    from app.ontology import validate_legal_edge_shapes

    fixture = _load_legal_fixture()
    assert validate_legal_edge_shapes(fixture["reference_graph"]) == []


def test_validate_legal_edge_shapes_flags_has_condition_pointed_at_a_benefit():
    from app.ontology import validate_legal_edge_shapes

    fixture = _load_legal_fixture()
    issues = validate_legal_edge_shapes(fixture["reference_graph_with_bad_edge_shape"])

    assert len(issues) == 1
    assert issues[0]["code"] == "unexpected_endpoint_type"


def test_validate_legal_edge_shapes_ignores_edge_types_it_has_no_hint_for():
    from app.ontology import validate_legal_edge_shapes

    graph = {
        "nodes": [{"id": "a", "type": "Person", "label": "x"}, {"id": "b", "type": "Person", "label": "y"}],
        "edges": [{"source": "a", "target": "b", "type": "KNOWS"}],
    }
    assert validate_legal_edge_shapes(graph) == []


def test_run_graph_validation_combines_schema_and_legal_guards():
    from app.ontology import run_graph_validation

    schema = {
        "node_types": [
            {"name": "Article", "description": "d"},
            {"name": "Norm", "description": "d"},
        ],
        "edge_types": [{"name": "STATES", "description": "d"}],
    }
    # Two independent problems at once: a schema_validation.validate_graph
    # issue (missing evidence on a Norm) and an app.ontology legal-guard
    # issue (a structural Article carrying catch-all detail).
    graph = {
        "nodes": [
            {"id": "a1", "type": "Article", "label": "제1조", "detail": "some substantive content"},
            {"id": "n1", "type": "Norm", "label": "규정"},
        ],
        "edges": [],
    }

    issues = run_graph_validation(schema, graph)
    codes = {i["code"] for i in issues}

    assert "structural_catchall" in codes
    assert "missing_evidence" in codes


def test_run_graph_validation_returns_empty_for_clean_graph():
    from app.ontology import run_graph_validation

    schema = {"node_types": [{"name": "Person", "description": "d"}], "edge_types": []}
    graph = {"nodes": [{"id": "p1", "type": "Person", "label": "Alice"}], "edges": []}

    assert run_graph_validation(schema, graph) == []


def test_extract_graph_from_chunks_single_group_skips_merge(monkeypatch):
    from app.ontology import extract_graph_from_chunks

    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    fake_model = RecordingChatModel(json.dumps(graph))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}

    result = extract_graph_from_chunks([{"path": "p1", "text": "hello"}], schema, max_group_chars=1000)

    assert result == graph
    assert len(fake_model.prompts) == 1


def test_extract_graph_from_chunks_merges_coreferent_nodes_across_groups(monkeypatch):
    from app.ontology import extract_graph_from_chunks

    schema = {
        "node_types": [{"name": "Person", "description": "a person"}, {"name": "Org", "description": "an org"}],
        "edge_types": [{"name": "WORKS_AT", "description": "works at", "source": "Person", "target": "Org"}],
    }
    graph1 = {
        "nodes": [
            {"id": "n1", "label": "Alice", "type": "Person"},
            {"id": "n2", "label": "Acme", "type": "Org"},
        ],
        "edges": [{"source": "n1", "target": "n2", "type": "WORKS_AT"}],
    }
    graph2 = {
        # Same real-world entities under the same exact labels (as
        # EXTRACT_PROMPT's "canonical surface form" instruction expects) but
        # different, group-local ids -- must merge into graph1's nodes.
        "nodes": [
            {"id": "a", "label": "Alice", "type": "Person"},
            {"id": "b", "label": "Acme", "type": "Org"},
            {"id": "c", "label": "Bob", "type": "Person"},
        ],
        "edges": [
            {"source": "a", "target": "b", "type": "WORKS_AT"},
            {"source": "c", "target": "b", "type": "WORKS_AT"},
        ],
    }
    fake_model = SequencedChatModel([json.dumps(graph1), json.dumps(graph2)])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = extract_graph_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}], schema, max_group_chars=30
    )

    labels = {(n["type"], n["label"]) for n in result["nodes"]}
    assert labels == {("Person", "Alice"), ("Org", "Acme"), ("Person", "Bob")}
    assert len(result["nodes"]) == 3  # Alice/Acme deduped, not double-counted
    # The duplicate Alice->Acme edge from graph2 collapses into graph1's;
    # Bob->Acme survives as its own edge.
    assert len(result["edges"]) == 2
    alice_id = next(n["id"] for n in result["nodes"] if n["label"] == "Alice")
    acme_id = next(n["id"] for n in result["nodes"] if n["label"] == "Acme")
    bob_id = next(n["id"] for n in result["nodes"] if n["label"] == "Bob")
    edge_pairs = {(e["source"], e["target"]) for e in result["edges"]}
    assert edge_pairs == {(alice_id, acme_id), (bob_id, acme_id)}


def test_extract_endpoint_uses_chunks_when_present(monkeypatch):
    write_document()
    stem = "doc_raw"
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version(stem, schema)
    (document_dir_for(stem) / "chunks.json").write_text(
        json.dumps(
            {
                "source": stem,
                "preamble": {"line_start": 1, "line_end": 1, "text": ""},
                "chunks": [
                    {"id": "0::제1조", "section_index": 0, "section_label": "주계약", "article_no": "1", "sub_no": None, "title": "목적", "path": "주계약 > 제1조(목적)", "line_start": 1, "line_end": 2, "text": "Alice works here."},
                ],
            }
        )
    )
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph


def test_get_ontology_returns_saved_graph():
    from app import graphdb
    seed_schema_version("doc_raw", DEFAULT_SCHEMA)
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
        seed_schema_version(stem, schema)
    # a graph dir with no versions.json shouldn't be listed
    (DOCUMENTS_DIR / "no_schema_raw").mkdir(parents=True)
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

    assert load_document_manifest("doc_raw") == {
        "original_filename": "report.docx",
        "converter": "anydoc",
    }


def test_save_document_manifest_records_given_converter():
    from app.ontology import load_document_manifest, save_document_manifest

    save_document_manifest("doc_raw", "report.pdf", converter="table_aware")

    assert load_document_manifest("doc_raw") == {
        "original_filename": "report.pdf",
        "converter": "table_aware",
    }


def test_load_document_manifest_returns_none_when_missing():
    from app.ontology import load_document_manifest

    assert load_document_manifest("never_uploaded") is None


def test_get_schema_returns_saved_schema():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)
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
    seed_schema_version("source_raw", source_schema)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "source_raw"}
    )

    assert response.status_code == 200
    assert response.json() == {**source_schema, "version": 1}
    saved = json.loads((DOCUMENTS_DIR / "target_raw" / "schema_v1.json").read_text())
    assert saved == source_schema


def test_use_schema_returns_404_when_source_missing():
    write_document("target_raw.md")
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "missing_raw"}
    )

    assert response.status_code == 404


def _seed_schema_and_graph(stem="doc_raw"):
    from app import graphdb
    from app.ontology import create_schema_version

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    version = create_schema_version(stem, schema)
    graphdb.write_graph(stem, [{"id": "n1", "label": "Alice", "type": "Person"}], [], version=version)
    return schema


def test_validate_endpoint_returns_report(monkeypatch):
    write_document()
    _seed_schema_and_graph()
    report = {
        "validation_summary": {
            "ontology_valid": True,
            "extraction_valid": True,
            "provenance_valid": True,
            "competency_questions_answerable": True,
            "overall_quality": "good",
        },
        "issues": [],
        "missing_elements": {"classes": [], "relationships": [], "attributes": [], "events": [], "rules": []},
        "contradictions": [],
        "ambiguities": [],
        "competency_questions": [],
        "recommended_changes": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/validate")

    assert response.status_code == 200
    assert response.json() == report


def test_validate_returns_404_when_document_missing():
    client = TestClient(app)

    response = client.post("/api/ontology/missing_raw.md/validate")

    assert response.status_code == 404


def test_validate_returns_404_when_schema_missing():
    write_document()
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/validate")

    assert response.status_code == 404


def test_validate_returns_404_when_graph_not_extracted():
    from app.ontology import create_schema_version

    write_document()
    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/validate")

    assert response.status_code == 404


def test_validate_returns_400_on_invalid_json(monkeypatch):
    write_document()
    _seed_schema_and_graph()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("not json at all")
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/validate")

    assert response.status_code == 400


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
    assert not (DOCUMENTS_DIR / "doc_raw" / "schema_v1.json").is_file()
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == {**schema, "version": 1}
    saved = json.loads((DOCUMENTS_DIR / "doc_raw" / "schema_v1.json").read_text())
    assert saved == schema
    versions = json.loads((DOCUMENTS_DIR / "doc_raw" / "versions.json").read_text())
    assert versions["active_version"] == 1


def test_generate_schema_second_call_creates_second_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.json()["version"] == 2
    assert (DOCUMENTS_DIR / "doc_raw" / "schema_v1.json").is_file()
    assert (DOCUMENTS_DIR / "doc_raw" / "schema_v2.json").is_file()


def test_list_schema_versions_endpoint_reports_active_and_graph_status(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
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

    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema_v1)))
    client.post("/api/ontology/doc_raw.md/schema")
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema_v2)))
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
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
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


def test_evolve_endpoint_returns_proposal(monkeypatch):
    write_document()
    _seed_schema_and_graph()
    proposal = {
        "evolution_summary": {"changes_proposed": 1, "human_review_required": False},
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
                "reason": "missing from schema",
                "evidence": "Acme Corp",
                "confidence": "HIGH",
            }
        ],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(proposal))
    )
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/evolve",
        json={"validation_report": {"issues": []}},
    )

    assert response.status_code == 200
    assert response.json() == proposal


def test_evolve_returns_404_when_graph_not_extracted():
    from app.ontology import create_schema_version

    write_document()
    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/evolve", json={"validation_report": {}}
    )

    assert response.status_code == 404


def test_evolve_returns_400_on_invalid_json(monkeypatch):
    write_document()
    _seed_schema_and_graph()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("not json")
    )
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/evolve", json={"validation_report": {}}
    )

    assert response.status_code == 400


def test_apply_evolution_adds_node_type_and_node_creates_new_version():
    from app.ontology import apply_evolution, get_active_version, load_schema

    _seed_schema_and_graph()

    changes = [
        {
            "change_id": "c1",
            "decision": "ADD",
            "element_type": "node_type",
            "element": {"name": "Organization", "description": "an org"},
        },
        {
            "change_id": "c2",
            "decision": "ADD",
            "element_type": "node",
            "element": {"id": "n2", "label": "Acme", "type": "Organization", "detail": ""},
        },
    ]

    result = apply_evolution("doc_raw", changes)

    assert result["version"] == 2
    assert get_active_version("doc_raw") == 2
    schema_v2 = load_schema("doc_raw", 2)
    assert {"name": "Organization", "description": "an org"} in schema_v2["node_types"]
    assert result["node_count"] == 2  # original Alice node preserved + new one

    from app import graphdb
    graph_v2 = graphdb.load_graph("doc_raw", version=2)
    ids = {n["id"] for n in graph_v2["nodes"]}
    assert ids == {"n1", "n2"}


def test_apply_evolution_deprecates_node_type_without_removing_it():
    from app.ontology import apply_evolution, load_schema

    _seed_schema_and_graph()

    result = apply_evolution(
        "doc_raw",
        [
            {
                "change_id": "c1",
                "decision": "DEPRECATE",
                "element_type": "node_type",
                "element": {"name": "Person", "description": "a person"},
            }
        ],
    )

    schema_v2 = load_schema("doc_raw", result["version"])
    assert schema_v2["node_types"][0]["description"].startswith("[DEPRECATED]")


def test_evolve_apply_endpoint_returns_404_when_no_schema():
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/evolve/apply", json={"changes": []})

    assert response.status_code == 404


def test_evolve_apply_endpoint_bumps_version():
    _seed_schema_and_graph()
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/evolve/apply",
        json={
            "changes": [
                {
                    "change_id": "c1",
                    "decision": "ADD",
                    "element_type": "edge_type",
                    "element": {
                        "name": "WORKS_AT",
                        "description": "works at",
                        "source": "Person",
                        "target": "Person",
                    },
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2


class SequencedChatModel:
    """Returns each response in order, one per invoke() call -- needed here
    because converge_domain_schema makes multiple sequential LLM calls
    (extract/validate/propose_evolution, per document) within one function
    call, unlike the single-call tests above that get away with a fixed
    FakeChatModel response."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        content = self.responses[self.calls]
        self.calls += 1
        return type("FakeResponse", (), {"content": content})()


def _minimal_validation_report(issue_count=0):
    return {
        "validation_summary": {
            "ontology_valid": True,
            "extraction_valid": True,
            "provenance_valid": True,
            "competency_questions_answerable": True,
            "overall_quality": "ok",
        },
        "issues": [{"severity": "LOW", "category": "x"} for _ in range(issue_count)],
    }


def test_converge_domain_schema_applies_auto_decisions_and_queues_review(monkeypatch):
    from app.ontology import converge_domain_schema

    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    extract_response = {"nodes": [{"id": "n1", "label": "Bob", "type": "Person"}], "edges": []}
    validate_response = _minimal_validation_report(issue_count=1)
    propose_response = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
                "reason": "missing from schema",
                "evidence": "Acme",
                "confidence": "HIGH",
            },
            {
                "change_id": "c2",
                "decision": "NEEDS_HUMAN_REVIEW",
                "element_type": "edge_type",
                "element": {
                    "name": "WORKS_AT",
                    "description": "works at",
                    "source": "Person",
                    "target": "Organization",
                },
                "reason": "ambiguous",
                "evidence": "Bob works at Acme",
                "confidence": "LOW",
            },
            {
                "change_id": "c3",
                "decision": "ADD",
                "element_type": "node",
                "element": {"id": "n2", "label": "Acme", "type": "Organization"},
                "reason": "instance-level, should be ignored by schema convergence",
                "evidence": "Acme",
                "confidence": "HIGH",
            },
        ]
    }
    fake_model = SequencedChatModel(
        [json.dumps(extract_response), json.dumps(validate_response), json.dumps(propose_response)]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = converge_domain_schema(
        [{"stem": "doc2_raw", "text": "Bob works at Acme."}], seed_schema
    )

    node_type_names = {t["name"] for t in result["schema"]["node_types"]}
    assert node_type_names == {"Person", "Organization"}
    assert result["schema"]["edge_types"] == []  # NEEDS_HUMAN_REVIEW change not applied
    assert len(result["iterations"]) == 1
    assert result["iterations"][0]["stem"] == "doc2_raw"
    assert result["iterations"][0]["issue_count"] == 1
    assert [c["change_id"] for c in result["iterations"][0]["changes_applied"]] == ["c1"]
    assert [c["change_id"] for c in result["pending_review"]] == ["c2"]
    assert result["pending_review"][0]["stem"] == "doc2_raw"


def test_converge_domain_schema_folds_multiple_documents_in_order(monkeypatch):
    from app.ontology import converge_domain_schema

    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    add_org = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
                "reason": "r",
                "evidence": "e",
                "confidence": "HIGH",
            }
        ]
    }
    add_edge = {
        "changes": [
            {
                "change_id": "c2",
                "decision": "MERGE",
                "element_type": "edge_type",
                "element": {
                    "name": "WORKS_AT",
                    "description": "works at",
                    "source": "Person",
                    "target": "Organization",
                },
                "reason": "r",
                "evidence": "e",
                "confidence": "HIGH",
            }
        ]
    }
    fake_model = SequencedChatModel(
        [
            json.dumps(empty_graph), json.dumps(validate_response), json.dumps(add_org),
            json.dumps(empty_graph), json.dumps(validate_response), json.dumps(add_edge),
        ]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = converge_domain_schema(
        [
            {"stem": "doc2_raw", "text": "doc2"},
            {"stem": "doc3_raw", "text": "doc3"},
        ],
        seed_schema,
    )

    assert {t["name"] for t in result["schema"]["node_types"]} == {"Person", "Organization"}
    assert {t["name"] for t in result["schema"]["edge_types"]} == {"WORKS_AT"}
    assert [it["stem"] for it in result["iterations"]] == ["doc2_raw", "doc3_raw"]
    assert result["pending_review"] == []


def test_converge_domain_endpoint_returns_seed_schema_and_final_schema(monkeypatch):
    write_document("doc_raw.md", "Alice works at Acme.")
    write_document("doc2_raw.md", "Bob works at Acme too.")
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    add_org = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
                "reason": "r",
                "evidence": "e",
                "confidence": "HIGH",
            }
        ]
    }
    fake_model = SequencedChatModel(
        [json.dumps(empty_graph), json.dumps(validate_response), json.dumps(add_org)]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/converge",
        json={"filenames": ["doc2_raw.md"], "seed_schema": seed_schema},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["seed_schema"] == seed_schema
    assert {t["name"] for t in body["schema"]["node_types"]} == {"Person", "Organization"}
    assert len(body["iterations"]) == 1


def test_converge_domain_endpoint_returns_404_for_missing_document():
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/converge",
        json={"filenames": ["missing_raw.md"], "seed_schema": {"node_types": [], "edge_types": []}},
    )

    assert response.status_code == 404


def test_converge_domain_endpoint_returns_400_for_empty_filenames():
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/converge",
        json={"filenames": [], "seed_schema": {"node_types": [], "edge_types": []}},
    )

    assert response.status_code == 400


def test_converge_domain_endpoint_generates_seed_schema_when_none_given(monkeypatch):
    write_document("doc_raw.md", "Alice works at Acme.")
    write_document("doc2_raw.md", "Bob works at Acme too.")
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    no_changes = {"changes": []}
    fake_model = SequencedChatModel(
        [
            json.dumps(seed_schema),  # generate_schema on doc_raw.md
            json.dumps(empty_graph), json.dumps(validate_response), json.dumps(no_changes),
        ]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/converge",
        json={"filenames": ["doc_raw.md", "doc2_raw.md"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["seed_schema"] == seed_schema
    assert len(body["iterations"]) == 1
    assert body["iterations"][0]["stem"] == "doc2_raw"


def _iteration(
    stem,
    doc_chars,
    node_type_counts,
    edge_type_counts=None,
    issue_count=0,
    missing_element_count=0,
    competency_questions=None,
):
    return {
        "stem": stem,
        "changes_applied": [],
        "changes_pending_review": [],
        "validation_summary": {},
        "issue_count": issue_count,
        "doc_chars": doc_chars,
        "missing_element_count": missing_element_count,
        "node_type_counts": node_type_counts,
        "edge_type_counts": edge_type_counts or {},
        "competency_questions": competency_questions or [],
    }


def test_evaluate_domain_schema_computes_coverage_and_utilization():
    from app.ontology import evaluate_domain_schema

    schema = {
        "node_types": [
            {"name": "Person", "description": "a person"},
            {"name": "Organization", "description": "an org"},
        ],
        "edge_types": [{"name": "WORKS_AT", "description": "works at"}],
    }
    iterations = [
        _iteration("doc1", 1000, {"Person": 2}, {}, issue_count=1, missing_element_count=2),
        _iteration("doc2", 1000, {"Person": 1, "Organization": 1}, {"WORKS_AT": 1}, issue_count=3),
    ]

    result = evaluate_domain_schema(schema, iterations)

    assert result["coverage"] == {"avg_issue_count": 2.0, "avg_missing_element_count": 1.0}
    assert result["type_utilization"] == {"Person": 1.0, "Organization": 0.5, "WORKS_AT": 0.5}
    assert result["qa_success_rate"] is None


def test_evaluate_domain_schema_consistency_is_zero_for_identical_density():
    from app.ontology import evaluate_domain_schema

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    iterations = [
        _iteration("doc1", 1000, {"Person": 1}),
        _iteration("doc2", 2000, {"Person": 2}),  # same density: 1 per 1000 chars
    ]

    result = evaluate_domain_schema(schema, iterations)

    assert result["consistency"]["Person"] == 0.0


def test_evaluate_domain_schema_qa_success_rate_from_competency_questions():
    from app.ontology import evaluate_domain_schema

    schema = {"node_types": [], "edge_types": []}
    iterations = [
        _iteration(
            "doc1", 1000, {},
            competency_questions=[{"question": "q1", "answerable": True}, {"question": "q2", "answerable": False}],
        ),
        _iteration("doc2", 1000, {}, competency_questions=[{"question": "q3", "answerable": True}]),
    ]

    result = evaluate_domain_schema(schema, iterations)

    assert result["qa_success_rate"] == pytest.approx(2 / 3)


def test_evaluate_domain_schema_handles_empty_iterations():
    from app.ontology import evaluate_domain_schema

    result = evaluate_domain_schema({"node_types": [], "edge_types": []}, [])

    assert result["type_utilization"] == {}
    assert result["qa_success_rate"] is None


def test_find_redundant_type_pairs_flags_near_duplicate_descriptions(monkeypatch):
    from app.ontology import find_redundant_type_pairs

    schema = {
        "node_types": [
            {"name": "Customer", "description": "a paying customer"},
            {"name": "Client", "description": "a paying customer"},
            {"name": "Product", "description": "something sold"},
        ],
        "edge_types": [],
    }

    class FakeEmbeddingModel:
        def embed_documents(self, texts):
            # Customer/Client get identical vectors; Product gets an
            # orthogonal one, so only the first pair should pass threshold.
            vectors = []
            for text in texts:
                if text.startswith("Product"):
                    vectors.append([0.0, 1.0])
                else:
                    vectors.append([1.0, 0.0])
            return vectors

    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: FakeEmbeddingModel())

    pairs = find_redundant_type_pairs(schema, threshold=0.9)

    assert pairs == [{"element_type": "node_type", "a": "Customer", "b": "Client", "similarity": pytest.approx(1.0)}]


def test_find_redundant_type_pairs_skips_types_with_fewer_than_two_entries():
    from app.ontology import find_redundant_type_pairs

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}

    pairs = find_redundant_type_pairs(schema)

    assert pairs == []


def test_measure_schema_stability_perfect_agreement_across_runs(monkeypatch):
    from app.ontology import measure_schema_stability

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    result = measure_schema_stability("some document text", runs=3)

    assert result["avg_jaccard_similarity"] == 1.0
    assert result["type_name_sets"] == [["Person"]] * 3


def test_measure_schema_stability_disagreement_lowers_similarity(monkeypatch):
    from app.ontology import measure_schema_stability

    schemas = [
        {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []},
        {"node_types": [{"name": "Individual", "description": "a person"}], "edge_types": []},
    ]
    fake_model = SequencedChatModel([json.dumps(s) for s in schemas])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = measure_schema_stability("some document text", runs=2)

    assert result["avg_jaccard_similarity"] == 0.0


def test_measure_schema_stability_raises_for_fewer_than_two_runs():
    from app.ontology import measure_schema_stability

    with pytest.raises(ValueError):
        measure_schema_stability("doc", runs=1)


def test_converge_endpoint_includes_evaluation(monkeypatch):
    write_document("doc2_raw.md", "Bob works at Acme too.")
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    no_changes = {"changes": []}
    fake_model = SequencedChatModel(
        [json.dumps(empty_graph), json.dumps(validate_response), json.dumps(no_changes)]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/converge",
        json={"filenames": ["doc2_raw.md"], "seed_schema": seed_schema},
    )

    assert response.status_code == 200
    body = response.json()
    assert "evaluation" in body
    assert body["evaluation"]["type_utilization"] == {"Person": 0.0}


def test_redundant_types_endpoint(monkeypatch):
    class FakeEmbeddingModel:
        def embed_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: FakeEmbeddingModel())
    client = TestClient(app)
    schema = {
        "node_types": [
            {"name": "Customer", "description": "a customer"},
            {"name": "Client", "description": "a customer"},
        ],
        "edge_types": [],
    }

    response = client.post("/api/ontology/domain-schema/redundant-types", json=schema)

    assert response.status_code == 200
    assert response.json()["pairs"][0]["a"] == "Customer"


def test_schema_stability_endpoint(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema/stability", json={"runs": 2})

    assert response.status_code == 200
    assert response.json()["avg_jaccard_similarity"] == 1.0


def test_schema_stability_endpoint_returns_404_when_document_missing():
    client = TestClient(app)

    response = client.post("/api/ontology/missing_raw.md/schema/stability")

    assert response.status_code == 404


def test_run_domain_convergence_seeds_from_first_document_when_domain_is_new(monkeypatch):
    from app.ontology import domain_calibration_stems, domain_convergence_history, load_domain_schema, run_domain_convergence

    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    no_changes = {"changes": []}
    fake_model = SequencedChatModel(
        [
            json.dumps(seed_schema),  # generate_schema seeds from doc1
            json.dumps(empty_graph), json.dumps(validate_response), json.dumps(no_changes),  # doc2
        ]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = run_domain_convergence(
        "insurance_policy",
        [{"stem": "doc1_raw", "text": "doc1"}, {"stem": "doc2_raw", "text": "doc2"}],
    )

    assert result["domain"] == "insurance_policy"
    assert result["seed_schema"] == seed_schema
    assert load_domain_schema("insurance_policy") == seed_schema
    assert domain_calibration_stems("insurance_policy") == ["doc1_raw", "doc2_raw"]
    history = domain_convergence_history("insurance_policy")
    assert len(history) == 1
    assert history[0]["stems"] == ["doc1_raw", "doc2_raw"]


def test_run_domain_convergence_reuses_existing_domain_schema_as_seed(monkeypatch):
    from app.ontology import domain_calibration_stems, run_domain_convergence, save_domain_schema

    existing_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    save_domain_schema("insurance_policy", existing_schema)
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    no_changes = {"changes": []}
    fake_model = SequencedChatModel(
        [json.dumps(empty_graph), json.dumps(validate_response), json.dumps(no_changes)]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = run_domain_convergence("insurance_policy", [{"stem": "doc3_raw", "text": "doc3"}])

    # Only one document's worth of calls consumed -- existing_schema was the
    # seed, doc3 was the only one folded in (no seed-generation call spent).
    assert fake_model.calls == 3
    assert result["seed_schema"] == existing_schema
    assert domain_calibration_stems("insurance_policy") == ["doc3_raw"]


def test_run_domain_convergence_raises_when_no_schema_and_no_documents():
    from app.ontology import run_domain_convergence

    with pytest.raises(ValueError):
        run_domain_convergence("insurance_policy", [])


def test_run_domain_convergence_accumulates_pending_review_across_calls(monkeypatch):
    from app.ontology import load_domain_pending_review, run_domain_convergence, save_domain_schema

    save_domain_schema("insurance_policy", {"node_types": [], "edge_types": []})
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    review_change = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "NEEDS_HUMAN_REVIEW",
                "element_type": "node_type",
                "element": {"name": "Ambiguous", "description": "?"},
                "reason": "r",
                "evidence": "e",
                "confidence": "LOW",
            }
        ]
    }
    fake_model = SequencedChatModel(
        [json.dumps(empty_graph), json.dumps(validate_response), json.dumps(review_change)]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    run_domain_convergence("insurance_policy", [{"stem": "doc1_raw", "text": "doc1"}])

    pending = load_domain_pending_review("insurance_policy")
    assert len(pending) == 1
    assert pending[0]["change_id"] == "c1"
    assert pending[0]["stem"] == "doc1_raw"


def test_apply_domain_schema_changes_applies_and_clears_pending_review():
    from app.ontology import apply_domain_schema_changes, load_domain_schema, save_domain_schema
    from app.ontology import _save_domain_pending_review

    save_domain_schema("insurance_policy", {"node_types": [], "edge_types": []})
    _save_domain_pending_review(
        "insurance_policy",
        [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
            }
        ],
    )

    result = apply_domain_schema_changes(
        "insurance_policy",
        [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
            }
        ],
    )

    assert {t["name"] for t in result["schema"]["node_types"]} == {"Organization"}
    assert result["pending_review"] == []
    assert load_domain_schema("insurance_policy")["node_types"][0]["name"] == "Organization"


def test_apply_domain_schema_changes_raises_when_domain_missing():
    from app.ontology import apply_domain_schema_changes

    with pytest.raises(ValueError):
        apply_domain_schema_changes("missing_domain", [])


def test_use_domain_schema_creates_new_version_for_document(monkeypatch):
    from app.ontology import get_active_version, load_schema, save_domain_schema, use_domain_schema

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    save_domain_schema("insurance_policy", schema)

    version = use_domain_schema("doc_raw", "insurance_policy", document_type="insurance")

    assert version == 1
    assert get_active_version("doc_raw") == 1
    assert load_schema("doc_raw", 1) == schema


def test_use_domain_schema_raises_when_domain_missing():
    from app.ontology import use_domain_schema

    with pytest.raises(ValueError):
        use_domain_schema("doc_raw", "missing_domain")


def test_list_domains_returns_only_domains_with_a_saved_schema():
    from app.ontology import list_domains, save_domain_schema

    assert list_domains() == []
    save_domain_schema("insurance_policy", {"node_types": [], "edge_types": []})
    save_domain_schema("hr_contract", {"node_types": [], "edge_types": []})

    assert list_domains() == ["hr_contract", "insurance_policy"]


def test_list_domain_schemas_endpoint():
    from app.ontology import save_domain_schema

    save_domain_schema("insurance_policy", {"node_types": [], "edge_types": []})
    client = TestClient(app)

    response = client.get("/api/ontology/domain-schemas")

    assert response.status_code == 200
    assert response.json() == {"domains": ["insurance_policy"]}


def test_get_domain_schema_endpoint_returns_schema_and_metadata():
    from app.ontology import save_domain_schema

    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    save_domain_schema("insurance_policy", schema)
    client = TestClient(app)

    response = client.get("/api/ontology/domain-schema/insurance_policy")

    assert response.status_code == 200
    body = response.json()
    assert body["node_types"] == schema["node_types"]
    assert body["calibration_stems"] == []
    assert body["history"] == []
    assert body["pending_review"] == []


def test_get_domain_schema_endpoint_returns_404_when_missing():
    client = TestClient(app)

    response = client.get("/api/ontology/domain-schema/missing_domain")

    assert response.status_code == 404


def test_converge_domain_persisted_endpoint(monkeypatch):
    write_document("doc_raw.md", "Alice works at Acme.")
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(seed_schema)))
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/insurance_policy/converge",
        json={"filenames": ["doc_raw.md"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "insurance_policy"
    assert body["schema"] == seed_schema
    assert "evaluation" in body


def test_converge_domain_persisted_endpoint_returns_400_for_empty_filenames():
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/insurance_policy/converge", json={"filenames": []}
    )

    assert response.status_code == 400


def test_apply_domain_pending_review_endpoint(monkeypatch):
    from app.ontology import save_domain_schema
    from app.ontology import _save_domain_pending_review

    save_domain_schema("insurance_policy", {"node_types": [], "edge_types": []})
    _save_domain_pending_review(
        "insurance_policy",
        [{"change_id": "c1", "decision": "ADD", "element_type": "node_type", "element": {"name": "Organization", "description": "an org"}}],
    )
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/insurance_policy/pending-review/apply",
        json={
            "changes": [
                {
                    "change_id": "c1",
                    "decision": "ADD",
                    "element_type": "node_type",
                    "element": {"name": "Organization", "description": "an org"},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["pending_review"] == []


def test_apply_domain_pending_review_endpoint_returns_404_when_domain_missing():
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/missing_domain/pending-review/apply", json={"changes": []}
    )

    assert response.status_code == 404


def test_use_domain_schema_endpoint():
    from app.ontology import save_domain_schema

    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    save_domain_schema("insurance_policy", schema)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema/use-domain",
        json={"domain": "insurance_policy"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["node_types"] == schema["node_types"]


def test_use_domain_schema_endpoint_returns_404_when_domain_missing():
    write_document()
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema/use-domain", json={"domain": "missing_domain"}
    )

    assert response.status_code == 404


def test_summarize_document_strips_and_returns_llm_text(monkeypatch):
    from app.ontology import summarize_document

    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("  이 문서는 보험약관을 설명합니다.  ")
    )

    assert summarize_document("some document text") == "이 문서는 보험약관을 설명합니다."


def test_summarize_document_raises_on_empty_response(monkeypatch):
    from app.ontology import summarize_document

    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel("   "))

    with pytest.raises(ValueError):
        summarize_document("some document text")


def test_create_summary_endpoint_saves_and_returns_summary(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("문서 요약입니다.")
    )
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "문서 요약입니다."}

    get_response = client.get("/api/documents/doc_raw.md/summary")
    assert get_response.status_code == 200
    assert get_response.json() == {"summary": "문서 요약입니다."}


def test_create_summary_returns_404_when_document_missing():
    client = TestClient(app)

    response = client.post("/api/documents/missing.md/summary")

    assert response.status_code == 404


def test_get_summary_returns_404_when_not_generated():
    write_document()
    client = TestClient(app)

    response = client.get("/api/documents/doc_raw.md/summary")

    assert response.status_code == 404


def test_create_summary_returns_400_on_empty_llm_response(monkeypatch):
    write_document()
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel("   "))
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/summary")

    assert response.status_code == 400


def test_create_chunks_endpoint_saves_and_returns_chunks():
    write_document(
        "doc_raw.md",
        "지엄체크 항목\n### 제1조 [목적]\n\n이 계약은 성립됩니다.\n",
    )
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/chunk")

    assert response.status_code == 200
    body = response.json()
    assert [c["id"] for c in body["chunks"]] == ["0::제1조"]

    get_response = client.get("/api/documents/doc_raw.md/chunk")
    assert get_response.status_code == 200
    assert [c["id"] for c in get_response.json()["chunks"]] == ["0::제1조"]


def test_create_chunks_returns_404_when_document_missing():
    client = TestClient(app)

    response = client.post("/api/documents/missing.md/chunk")

    assert response.status_code == 404


def test_get_chunks_returns_404_when_not_chunked():
    write_document()
    client = TestClient(app)

    response = client.get("/api/documents/doc_raw.md/chunk")

    assert response.status_code == 404


def test_list_documents_reports_has_chunks_and_summary():
    write_document()
    from app.ontology import save_document_summary
    from app.chunking import chunk_markdown_file

    save_document_summary("doc_raw", "요약입니다.")
    chunk_markdown_file("doc_raw")
    client = TestClient(app)

    response = client.get("/api/documents")

    assert response.status_code == 200
    doc = response.json()["documents"][0]
    assert doc["summary"] == "요약입니다."
    assert doc["has_chunks"] is True
