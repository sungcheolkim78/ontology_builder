import json
import os

import pytest
from fastapi.testclient import TestClient

from app.preprocess.embeddings import EMBEDDING_DIM
from app.main import app
from app.ontology import DEFAULT_SCHEMA, embed_graph, embed_nodes
from app.utils.paths import document_dir_for
from fakes import FakeChatModel, FakeEmbeddingModel, RecordingChatModel, SequencedChatModel, prompt_text
from app.utils.paths import documents_dir


def write_document(filename="doc_raw.md", content="# Doc\nAlice works at Acme."):
    stem = filename.removesuffix(".md")
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.md").write_text(content)


def seed_schema_version(stem, schema, version=1, document_type="general"):
    d = documents_dir() / stem
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


def test_progress_endpoint_returns_404_when_nothing_recorded():
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md/progress", params={"operation": "schema"})

    assert response.status_code == 404


def test_progress_endpoint_returns_404_for_unknown_operation():
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md/progress", params={"operation": "bogus"})

    assert response.status_code == 404


def test_progress_endpoint_returns_state_after_schema_generation_completes(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    response = client.get("/api/ontology/doc_raw.md/progress", params={"operation": "schema"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["total"] == 1
    assert body["completed"] == 1


def test_progress_endpoint_reports_running_totals_for_extract(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/extract")
    response = client.get("/api/ontology/doc_raw.md/progress", params={"operation": "extract"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["nodes"] == 1
    assert body["edges"] == 0


def test_generate_schema_returns_400_on_invalid_json(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("not json at all")
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 400


def test_generate_schema_returns_404_when_document_missing(monkeypatch):
    client = TestClient(app)

    response = client.post("/api/ontology/missing_raw.md/schema")

    assert response.status_code == 404


def test_generate_schema_uses_legal_prompt_for_legal_document_type(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/schema", json={"document_type": "legal"}
    )

    assert response.status_code == 200
    assert "defined terms" in prompt_text(fake_model.prompts[0])


def test_generate_schema_returns_400_on_unknown_document_type(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("{}")
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report))
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("not json at all")
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/discover")

    assert response.status_code == 200
    assert response.json() == report


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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["node_types"] == schema["node_types"]
    assert body["version"] == 1


def test_schema_endpoint_ignores_discovery_by_default(monkeypatch):
    write_document()
    (documents_dir() / "doc_raw").mkdir(parents=True, exist_ok=True)
    (documents_dir() / "doc_raw" / "discovery.json").write_text(json.dumps({"classes": [{"name": "Policy"}]}))
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")

    assert "Reference --" not in prompt_text(fake_model.prompts[0])


def test_generate_schema_includes_discovery_hint_when_requested(monkeypatch):
    write_document()
    (documents_dir() / "doc_raw").mkdir(parents=True, exist_ok=True)
    (documents_dir() / "doc_raw" / "discovery.json").write_text(json.dumps({"classes": [{"name": "Policy"}]}))
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema", json={"use_discovery": True})

    assert response.status_code == 200
    assert "Reference --" in prompt_text(fake_model.prompts[0])
    assert "Policy" in prompt_text(fake_model.prompts[0])


def test_embed_nodes_attaches_a_vector_per_node(monkeypatch):
    calls = []

    class FakeEmbeddingModel:
        def embed_documents(self, texts):
            calls.append(texts)
            return [[float(i)] * EMBEDDING_DIM for i in range(len(texts))]

    monkeypatch.setattr("app.llm.calls.get_embedding_model", lambda: FakeEmbeddingModel())
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

    monkeypatch.setattr("app.llm.calls.get_embedding_model", fail)

    assert embed_nodes([]) == []


def test_embed_graph_computes_and_stores_embeddings():
    from app.graph import graphdb

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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    saved_schema = json.loads((documents_dir() / "doc_raw" / "schema_v1.json").read_text())
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    from app.graph import graphdb
    assert graphdb.load_graph("doc_raw", version=1) == graph


def test_extract_returns_400_on_invalid_json(monkeypatch):
    write_document()
    seed_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("nope")
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json()["edges"] == []


def _load_legal_fixture():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "legal_policy_expected.json")
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def _load_legal_sample_text():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "legal_policy_sample.md")
    with open(fixture_path, encoding="utf-8") as f:
        return f.read()


LEGAL_FIXTURE_SCHEMA = {
    "node_types": [
        {"name": "Article", "description": "a structural provision"},
        {
            "name": "Norm",
            "description": "a rule stated by the policy",
            "properties": {"modality": {"datatype": "string"}},
        },
        {"name": "Party", "description": "a party to the policy"},
        {"name": "Benefit", "description": "a payment obligation"},
        {"name": "Condition", "description": "a condition that must hold"},
        {
            "name": "PaymentAmount",
            "description": "an amount expression",
            "properties": {"expression": {"datatype": "string"}},
        },
        {
            "name": "Exclusion",
            "description": "an exception that bars payment",
            "properties": {"duration": {"datatype": "string"}},
        },
    ],
    "edge_types": [
        {"name": "STATES", "description": "d", "source": "Article", "target": "Norm"},
        {"name": "HAS_BEARER", "description": "d", "source": "Norm", "target": "Party"},
        {"name": "HAS_ACTION", "description": "d", "source": "Norm", "target": "Benefit"},
        {"name": "HAS_CONDITION", "description": "d", "source": "Norm", "target": "Condition"},
        {"name": "HAS_AMOUNT", "description": "d", "source": "Norm", "target": "PaymentAmount"},
        {"name": "HAS_EXCEPTION", "description": "d", "source": "Norm", "target": "Exclusion"},
    ],
}

# A hand-authored extraction-shaped LLM response for legal_policy_sample.md
# -- "evidence" values are verbatim substrings of that file (verified
# separately), so app.ontology._find_evidence_span actually accepts them
# rather than the test relying on a value that merely looks plausible.
LEGAL_FIXTURE_EXTRACTION_RESPONSE = {
    "nodes": [
        {"id": "article17", "label": "제17조", "type": "Article"},
        {
            "id": "norm1",
            "label": "암 진단 보험금 지급 규정",
            "type": "Norm",
            "properties": {"modality": "OBLIGATION"},
            "evidence": "회사는 피보험자가 이 계약의 보험기간 중 암 진단 확정을 받은 경우, 제1조에서 정한",
            "confidence": "HIGH",
        },
        {"id": "insurer", "label": "회사", "type": "Party", "confidence": "HIGH"},
        {
            "id": "benefit1",
            "label": "보험금 지급",
            "type": "Benefit",
            "evidence": "가입금액의 50%를 보험금으로 지급한다.",
            "confidence": "HIGH",
        },
        {
            "id": "condition1",
            "label": "암 진단 확정",
            "type": "Condition",
            "evidence": "암 진단 확정을 받은 경우",
            "confidence": "HIGH",
        },
        {
            "id": "amount1",
            "label": "가입금액의 50%",
            "type": "PaymentAmount",
            "properties": {"expression": "가입금액의 50%"},
            "confidence": "HIGH",
        },
        {
            "id": "exclusion1",
            "label": "계약일로부터 90일 이내 면책",
            "type": "Exclusion",
            "properties": {"duration": "90일"},
            "evidence": "계약일로부터 90일 이내에 암 진단 확정을 받은 경우",
            "confidence": "HIGH",
        },
    ],
    "edges": [
        {"source": "article17", "target": "norm1", "type": "STATES"},
        {"source": "norm1", "target": "insurer", "type": "HAS_BEARER"},
        {"source": "norm1", "target": "benefit1", "type": "HAS_ACTION"},
        {"source": "norm1", "target": "condition1", "type": "HAS_CONDITION"},
        {"source": "norm1", "target": "amount1", "type": "HAS_AMOUNT"},
        {"source": "norm1", "target": "exclusion1", "type": "HAS_EXCEPTION"},
    ],
}


def test_legal_fixture_extraction_produces_full_rule_chain_with_evidence(monkeypatch):
    from app.ontology import extract_graph

    monkeypatch.setattr(
        "app.llm.calls.get_chat_model",
        lambda operation=None: FakeChatModel(json.dumps(LEGAL_FIXTURE_EXTRACTION_RESPONSE)),
    )

    graph = extract_graph(_load_legal_sample_text(), LEGAL_FIXTURE_SCHEMA)

    node_types = {n["type"] for n in graph["nodes"]}
    assert node_types == {
        "Article", "Norm", "Party", "Benefit", "Condition", "PaymentAmount", "Exclusion",
    }
    edge_types = {e["type"] for e in graph["edges"]}
    assert edge_types == {
        "STATES", "HAS_BEARER", "HAS_ACTION", "HAS_CONDITION", "HAS_AMOUNT", "HAS_EXCEPTION",
    }

    by_id = {n["id"]: n for n in graph["nodes"]}
    # Evidence was verified against the real document text, not just echoed.
    assert by_id["condition1"]["evidence_text"] == "암 진단 확정을 받은 경우"
    assert by_id["exclusion1"]["evidence_text"] == "계약일로부터 90일 이내에 암 진단 확정을 받은 경우"
    assert by_id["exclusion1"]["properties"] == {"duration": "90일"}
    assert by_id["norm1"]["properties"] == {"modality": "OBLIGATION"}
    # Not a chunked call (no bracketed section labels in this document text),
    # so source_section must be absent, never fabricated.
    assert all("source_section" not in n for n in graph["nodes"])


def test_legal_fixture_reextraction_is_idempotent(monkeypatch):
    from app.graph import graphdb
    from app.ontology import extract_graph

    monkeypatch.setattr(
        "app.llm.calls.get_chat_model",
        lambda operation=None: FakeChatModel(json.dumps(LEGAL_FIXTURE_EXTRACTION_RESPONSE)),
    )
    document_text = _load_legal_sample_text()

    first = extract_graph(document_text, LEGAL_FIXTURE_SCHEMA)
    graphdb.write_graph("legal_fixture", first["nodes"], first["edges"], version=1)
    first_loaded = graphdb.load_graph("legal_fixture", version=1)

    second = extract_graph(document_text, LEGAL_FIXTURE_SCHEMA)
    graphdb.write_graph("legal_fixture", second["nodes"], second["edges"], version=1)
    second_loaded = graphdb.load_graph("legal_fixture", version=1)

    assert sorted(first_loaded["nodes"], key=lambda n: n["id"]) == sorted(
        second_loaded["nodes"], key=lambda n: n["id"]
    )
    assert sorted(first_loaded["edges"], key=lambda e: (e["source"], e["target"], e["type"])) == sorted(
        second_loaded["edges"], key=lambda e: (e["source"], e["target"], e["type"])
    )


def test_legal_fixture_competency_questions_answered_via_graphrag(monkeypatch):
    from app.graph import graphdb
    from app.graph.graphrag import answer_question
    from app.ontology import extract_graph

    # Extract through the real pipeline first (not the raw fixture response
    # directly) so evidence is actually verified/renamed to `evidence_text`
    # by extract_graph's own normalization -- exactly what graphrag reads.
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model",
        lambda operation=None: FakeChatModel(json.dumps(LEGAL_FIXTURE_EXTRACTION_RESPONSE)),
    )
    extracted = extract_graph(_load_legal_sample_text(), LEGAL_FIXTURE_SCHEMA)
    graphdb.write_graph("legal_fixture", extracted["nodes"], extracted["edges"], version=1)
    fixture = _load_legal_fixture()
    cq1 = next(q for q in fixture["competency_questions"] if q["id"] == "cq1")
    cq3 = next(q for q in fixture["competency_questions"] if q["id"] == "cq3")

    # cq1: "암 진단 확정 시 보험금으로 얼마를 지급하는가?" -- graph-shaped,
    # answerable from condition1/amount1 and their verified evidence.
    analysis_cq1 = {
        "node_types": ["Condition", "PaymentAmount"],
        "edge_types": [],
        "keywords": {"Condition": ["암 진단 확정"], "PaymentAmount": ["가입금액의 50%"]},
    }
    model_cq1 = SequencedChatModel([json.dumps(analysis_cq1), "가입금액의 50%를 지급합니다."])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model_cq1)

    result_cq1 = answer_question(
        [{"role": "user", "content": cq1["question"]}], LEGAL_FIXTURE_SCHEMA, "legal_fixture", hops=0
    )

    assert "50%" in result_cq1["content"]
    assert cq1["expected_answer_contains"][0] in result_cq1["content"] or any(
        cq1["expected_answer_contains"][0] in (n.get("evidence_text") or "")
        for n in result_cq1["related_nodes"]
    )
    assert any(n.get("evidence_text") for n in result_cq1["related_nodes"])

    # cq3: "어떤 기간 동안 지급하지 않는가?" -- graph-shaped, from exclusion1.
    analysis_cq3 = {
        "node_types": ["Exclusion"],
        "edge_types": [],
        "keywords": {"Exclusion": ["계약일로부터 90일 이내 면책"]},
    }
    model_cq3 = SequencedChatModel([json.dumps(analysis_cq3), "계약일로부터 90일 이내에는 지급하지 않습니다."])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model_cq3)

    result_cq3 = answer_question(
        [{"role": "user", "content": cq3["question"]}], LEGAL_FIXTURE_SCHEMA, "legal_fixture", hops=0
    )

    assert "90일" in result_cq3["content"]
    assert any(
        n.get("evidence_text") == "계약일로부터 90일 이내에 암 진단 확정을 받은 경우"
        for n in result_cq3["related_nodes"]
    )


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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph)))
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph


def test_get_ontology_returns_saved_graph():
    from app.graph import graphdb
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
    from app.graph import graphdb
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
    (documents_dir() / "no_schema_raw").mkdir(parents=True)
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
    from app.ontology.schema_validation import normalize_schema

    # The stored file itself stays exactly as saved (see
    # test_use_domain_schema_creates_new_version_for_document and friends,
    # which assert load_schema()'s raw round-trip) -- normalization happens
    # only at this API boundary, so a legacy schema.json with no typed
    # properties still returns with additive defaults filled in for any
    # client that relies on them being present.
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == normalize_schema(schema)


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
    saved = json.loads((documents_dir() / "target_raw" / "schema_v1.json").read_text())
    assert saved == source_schema


def test_use_schema_returns_404_when_source_missing():
    write_document("target_raw.md")
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "missing_raw"}
    )

    assert response.status_code == 404


def _seed_schema_and_graph(stem="doc_raw"):
    from app.graph import graphdb
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report))
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("not json at all")
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
    from app.graph import graphdb
    from app.ontology import create_schema_version, delete_version, list_versions

    v1 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    graphdb.write_graph(
        "doc_raw", [{"id": "n1", "label": "Alice", "type": "Person"}], [], version=v1
    )

    delete_version("doc_raw", v1)

    assert list_versions("doc_raw") == []
    assert not (documents_dir() / "doc_raw" / "schema_v1.json").is_file()
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == {**schema, "version": 1}
    saved = json.loads((documents_dir() / "doc_raw" / "schema_v1.json").read_text())
    assert saved == schema
    versions = json.loads((documents_dir() / "doc_raw" / "versions.json").read_text())
    assert versions["active_version"] == 1


def test_generate_schema_second_call_creates_second_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.json()["version"] == 2
    assert (documents_dir() / "doc_raw" / "schema_v1.json").is_file()
    assert (documents_dir() / "doc_raw" / "schema_v2.json").is_file()


def test_list_schema_versions_endpoint_reports_active_and_graph_status(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
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

    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema_v1)))
    client.post("/api/ontology/doc_raw.md/schema")
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema_v2)))
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.post("/api/ontology/doc_raw.md/schema/versions/1/activate")

    assert response.status_code == 200
    from app.ontology.schema_validation import normalize_schema

    assert client.get("/api/ontology/doc_raw.md/schema").json() == normalize_schema(schema_v1)


def test_activate_schema_version_endpoint_returns_404_for_unknown_version():
    write_document()
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema/versions/99/activate")

    assert response.status_code == 404


def test_delete_schema_version_endpoint_removes_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(proposal))
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
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("not json")
    )
    client = TestClient(app)

    response = client.post(
        "/api/ontology/doc_raw.md/evolve", json={"validation_report": {}}
    )

    assert response.status_code == 400


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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
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


def test_converge_endpoint_includes_evaluation(monkeypatch):
    write_document("doc2_raw.md", "Bob works at Acme too.")
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    no_changes = {"changes": []}
    fake_model = SequencedChatModel(
        [json.dumps(empty_graph), json.dumps(validate_response), json.dumps(no_changes)]
    )
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
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

    monkeypatch.setattr("app.llm.calls.get_embedding_model", lambda: FakeEmbeddingModel())
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

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


def test_run_domain_convergence_records_schema_contract_version_and_validation_summary(monkeypatch):
    from app.ontology import domain_convergence_history, run_domain_convergence
    from app.ontology.schema_validation import SCHEMA_CONTRACT_VERSION

    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    fake_model = SequencedChatModel([json.dumps(seed_schema)])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

    run_domain_convergence("insurance_policy", [{"stem": "doc1_raw", "text": "doc1"}])

    entry = domain_convergence_history("insurance_policy")[0]
    assert entry["schema_contract_version"] == SCHEMA_CONTRACT_VERSION
    # Distinct from iterations[i]["validation_summary"] (per-document
    # extraction/graph quality, from validate_ontology's own LLM report) --
    # this is the converged schema's own structural validity, from
    # app.ontology.schema_validation.validate_schema.
    assert entry["schema_validation_summary"] == {"error_count": 0, "warning_count": 0}


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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

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
    from app.ontology.schema_validation import normalize_schema

    assert body["node_types"] == normalize_schema(schema)["node_types"]
    assert body["calibration_stems"] == []
    assert body["history"] == []
    assert body["pending_review"] == []


def test_get_domain_schema_endpoint_returns_legacy_schema_with_additive_defaults():
    from app.ontology import save_domain_schema

    legacy_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    save_domain_schema("legacy_domain", legacy_schema)
    client = TestClient(app)

    body = client.get("/api/ontology/domain-schema/legacy_domain").json()

    assert body["node_types"][0]["properties"] == {}
    assert body["node_types"][0]["category"] is None
    assert body["validation"] == {"required_provenance": False, "closed_world_types": False}


def test_get_domain_schema_endpoint_returns_404_when_missing():
    client = TestClient(app)

    response = client.get("/api/ontology/domain-schema/missing_domain")

    assert response.status_code == 404


def test_converge_domain_persisted_endpoint(monkeypatch):
    write_document("doc_raw.md", "Alice works at Acme.")
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(seed_schema)))
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


def test_converge_domain_persisted_endpoint_seeds_a_new_domain_with_the_requested_document_type(monkeypatch):
    from app.llm.prompts import SCHEMA_PROMPTS
    from fakes import prompt_text

    write_document("doc_raw.md", "Alice works at Acme.")
    seed_schema = {"node_types": [{"name": "Norm", "description": "a rule"}], "edge_types": []}
    model = RecordingChatModel(json.dumps(seed_schema))
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/domain-schema/insurance_policy/converge",
        json={"filenames": ["doc_raw.md"], "document_type": "legal"},
    )

    assert response.status_code == 200
    assert SCHEMA_PROMPTS["legal"] in prompt_text(model.prompts[0])


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


def test_create_summary_endpoint_saves_and_returns_summary(monkeypatch):
    write_document()
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("문서 요약입니다.")
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("   "))
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
    from app.preprocess.chunking import chunk_markdown_file

    save_document_summary("doc_raw", "요약입니다.")
    chunk_markdown_file("doc_raw")
    client = TestClient(app)

    response = client.get("/api/documents")

    assert response.status_code == 200
    doc = response.json()["documents"][0]
    assert doc["summary"] == "요약입니다."
    assert doc["has_chunks"] is True
