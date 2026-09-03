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
