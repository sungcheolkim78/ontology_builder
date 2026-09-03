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


class SequencedChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        content = self.responses[self.calls]
        self.calls += 1
        return type("FakeResponse", (), {"content": content})()


class FakeEmbeddingModel:
    def embed_documents(self, texts):
        return [[0.0] * EMBEDDING_DIM for _ in texts]


@pytest.fixture(autouse=True)
def stub_embedding_model(monkeypatch):
    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: FakeEmbeddingModel())
    monkeypatch.setattr("app.graphrag.get_embedding_model", lambda: FakeEmbeddingModel())


@pytest.fixture(autouse=True)
def clean_data_dir():
    graphdb.reset_connection()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    graphdb.reset_connection()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def write_document(filename="doc_raw.md", content="# Doc\nAlice works at Acme."):
    stem = filename.removesuffix(".md")
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.md").write_text(content)


def _question_payload(n=1):
    return {
        "questions": [
            {
                "id": f"q{i:03d}",
                "question": f"Question {i}?",
                "question_type": "entity",
                "importance": "high",
                "rationale": "core fact",
            }
            for i in range(1, n + 1)
        ]
    }


def _answer_payload(ids, answerable=True, quote="Alice works at Acme."):
    return {
        "answers": [
            {
                "id": qid,
                "answerable": answerable,
                "answer": "Alice" if answerable else None,
                "answer_facts": [],
                "evidence": [{"quote": quote, "line_start": 1, "line_end": 1}] if answerable else [],
                "notes": "",
            }
            for qid in ids
        ]
    }


def test_compact_document_for_questions_keeps_every_heading_within_budget():
    from app.goldenset import compact_document_for_questions

    document = "# A\n" + ("x" * 100) + "\n# B\n" + ("y" * 100)

    compacted = compact_document_for_questions(document, max_chars=50)

    assert "# A" in compacted
    assert "# B" in compacted
    assert len(compacted) <= 50


def test_compact_document_for_questions_returns_unchanged_when_under_budget():
    from app.goldenset import compact_document_for_questions

    document = "# A\nshort"
    assert compact_document_for_questions(document, max_chars=1000) == document


def test_generate_goldenset_single_group_two_llm_calls(monkeypatch):
    from app.goldenset import generate_goldenset

    questions = _question_payload(1)
    answers = _answer_payload(["q001"])
    fake_model = SequencedChatModel([json.dumps(questions), json.dumps(answers)])
    monkeypatch.setattr("app.goldenset.get_chat_model", lambda: fake_model)

    result = generate_goldenset("Alice works at Acme.", "doc_raw.md", question_count=1)

    assert fake_model.calls == 2
    assert result["source_file"] == "doc_raw.md"
    assert len(result["questions"]) == 1
    assert result["questions"][0]["answerable"] is True
    assert result["questions"][0]["evidence"][0]["quote"] == "Alice works at Acme."
    assert result["warnings"] == []


def test_generate_goldenset_downgrades_unverifiable_evidence(monkeypatch):
    from app.goldenset import generate_goldenset

    questions = _question_payload(1)
    answers = _answer_payload(["q001"], quote="this text is not in the document")
    fake_model = SequencedChatModel([json.dumps(questions), json.dumps(answers)])
    monkeypatch.setattr("app.goldenset.get_chat_model", lambda: fake_model)

    result = generate_goldenset("Alice works at Acme.", "doc_raw.md", question_count=1)

    assert result["questions"][0]["answerable"] is False
    assert result["questions"][0]["evidence"] == []
    assert len(result["warnings"]) == 2


def test_generate_goldenset_rejects_empty_document():
    from app.goldenset import generate_goldenset

    with pytest.raises(ValueError):
        generate_goldenset("   ", "doc_raw.md")


def test_goldenset_endpoint_saves_and_returns_report(monkeypatch):
    write_document()
    questions = _question_payload(1)
    answers = _answer_payload(["q001"])
    fake_model = SequencedChatModel([json.dumps(questions), json.dumps(answers)])
    monkeypatch.setattr("app.goldenset.get_chat_model", lambda: fake_model)
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/goldenset", json={"question_count": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["questions"]) == 1

    get_response = client.get("/api/documents/doc_raw.md/goldenset")
    assert get_response.status_code == 200
    assert get_response.json() == body


def test_goldenset_endpoint_returns_404_when_document_missing():
    client = TestClient(app)

    response = client.post("/api/documents/missing_raw.md/goldenset")

    assert response.status_code == 404


def test_goldenset_endpoint_returns_400_on_invalid_json(monkeypatch):
    write_document()
    monkeypatch.setattr("app.goldenset.get_chat_model", lambda: FakeChatModel("not json"))
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/goldenset")

    assert response.status_code == 400


def test_get_goldenset_returns_404_when_none_saved():
    client = TestClient(app)

    response = client.get("/api/documents/doc_raw.md/goldenset")

    assert response.status_code == 404


def test_record_and_latest_goldenset_answers_filters_by_schema_version():
    from app.goldenset import latest_goldenset_answers, record_goldenset_answer

    write_document()
    record_goldenset_answer(
        "doc_raw", "q001", schema_version=1, hops=1, content="old answer",
        node_types=[], edge_types=[], related_nodes=[], related_edges=[],
    )
    record_goldenset_answer(
        "doc_raw", "q001", schema_version=2, hops=2, content="new answer",
        node_types=["Policy"], edge_types=[], related_nodes=[], related_edges=[],
    )

    assert latest_goldenset_answers("doc_raw", active_schema_version=1)["q001"]["content"] == "old answer"
    assert latest_goldenset_answers("doc_raw", active_schema_version=2)["q001"]["content"] == "new answer"
    assert latest_goldenset_answers("doc_raw", active_schema_version=3) == {}


def test_latest_goldenset_answers_picks_most_recent_matching_version():
    from app.goldenset import latest_goldenset_answers, record_goldenset_answer

    write_document()
    record_goldenset_answer(
        "doc_raw", "q001", schema_version=1, hops=1, content="first",
        node_types=[], edge_types=[], related_nodes=[], related_edges=[],
    )
    record_goldenset_answer(
        "doc_raw", "q001", schema_version=1, hops=1, content="second",
        node_types=[], edge_types=[], related_nodes=[], related_edges=[],
    )

    latest = latest_goldenset_answers("doc_raw", active_schema_version=1)
    assert latest["q001"]["content"] == "second"


def test_latest_goldenset_answers_returns_empty_when_no_active_schema():
    from app.goldenset import latest_goldenset_answers

    assert latest_goldenset_answers("doc_raw", active_schema_version=None) == {}


def _write_graphrag_fixture(stem="doc_raw"):
    from app.ontology import create_schema_version

    write_document(f"{stem}.md", content="Alice works at Acme.")
    schema = {
        "node_types": [{"name": "Person", "description": "a person"}],
        "edge_types": [],
    }
    version = create_schema_version(stem, schema)
    graphdb.write_graph(
        stem, [{"id": "n1", "label": "Alice", "type": "Person"}], [], version=version
    )
    from app.goldenset import save_goldenset

    save_goldenset(
        stem,
        {
            "source_file": f"{stem}.md",
            "source_sha256": "x",
            "questions": [
                {
                    "id": "q001",
                    "question": "Alice는 누구인가요?",
                    "question_type": "entity",
                    "importance": "high",
                    "answerable": True,
                    "answer": "Alice",
                    "evidence": [],
                }
            ],
            "warnings": [],
        },
    )
    return version


def test_goldenset_answer_endpoint_generates_saves_and_returns_record(monkeypatch):
    version = _write_graphrag_fixture()
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Alice"]}}),
            "Alice는 Acme에서 일합니다.",
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/goldenset/q001/answer", json={"hops": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Alice는 Acme에서 일합니다."
    assert body["schema_version"] == version
    assert body["hops"] == 2
    assert body["node_types"] == ["Person"]
    assert any(n["label"] == "Alice" for n in body["related_nodes"])
    assert "generated_at" in body

    # Persisted to disk, and surfaced back as the current answer for this schema version.
    answers_response = client.get("/api/documents/doc_raw.md/goldenset/answers")
    assert answers_response.status_code == 200
    answers_body = answers_response.json()
    assert answers_body["active_schema_version"] == version
    assert answers_body["answers"]["q001"]["content"] == "Alice는 Acme에서 일합니다."


def test_goldenset_answer_endpoint_returns_400_without_schema_or_graph():
    write_document()
    from app.goldenset import save_goldenset

    save_goldenset(
        "doc_raw",
        {
            "source_file": "doc_raw.md",
            "source_sha256": "x",
            "questions": [{"id": "q001", "question": "Q?", "question_type": "entity", "importance": "high", "answerable": True, "answer": "A", "evidence": []}],
            "warnings": [],
        },
    )
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/goldenset/q001/answer")

    assert response.status_code == 400


def test_goldenset_answer_endpoint_returns_404_for_unknown_question():
    version = _write_graphrag_fixture()
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/goldenset/does-not-exist/answer")

    assert response.status_code == 404


def test_goldenset_answers_endpoint_returns_empty_map_when_none_generated():
    _write_graphrag_fixture()
    client = TestClient(app)

    response = client.get("/api/documents/doc_raw.md/goldenset/answers")

    assert response.status_code == 200
    assert response.json()["answers"] == {}
