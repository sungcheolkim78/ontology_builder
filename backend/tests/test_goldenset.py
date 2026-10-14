import json

import pytest
from fastapi.testclient import TestClient

from app.graph import graphdb
from app.main import app
from app.utils.paths import document_dir_for
from fakes import FakeChatModel, SequencedChatModel


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
    from app.preprocess.goldenset import compact_document_for_questions

    document = "# A\n" + ("x" * 100) + "\n# B\n" + ("y" * 100)

    compacted = compact_document_for_questions(document, max_chars=50)

    assert "# A" in compacted
    assert "# B" in compacted
    assert len(compacted) <= 50


def test_compact_document_for_questions_returns_unchanged_when_under_budget():
    from app.preprocess.goldenset import compact_document_for_questions

    document = "# A\nshort"
    assert compact_document_for_questions(document, max_chars=1000) == document


def test_generate_goldenset_single_group_two_llm_calls(monkeypatch):
    from app.preprocess.goldenset import generate_goldenset

    questions = _question_payload(1)
    answers = _answer_payload(["q001"])
    fake_model = SequencedChatModel([json.dumps(questions), json.dumps(answers)])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

    result = generate_goldenset("Alice works at Acme.", "doc_raw.md", question_count=1)

    assert fake_model.calls == 2
    assert result["source_file"] == "doc_raw.md"
    assert len(result["questions"]) == 1
    assert result["questions"][0]["answerable"] is True
    assert result["questions"][0]["evidence"][0]["quote"] == "Alice works at Acme."
    assert result["warnings"] == []


def test_generate_goldenset_downgrades_unverifiable_evidence(monkeypatch):
    from app.preprocess.goldenset import generate_goldenset

    questions = _question_payload(1)
    answers = _answer_payload(["q001"], quote="this text is not in the document")
    fake_model = SequencedChatModel([json.dumps(questions), json.dumps(answers)])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

    result = generate_goldenset("Alice works at Acme.", "doc_raw.md", question_count=1)

    assert result["questions"][0]["answerable"] is False
    assert result["questions"][0]["evidence"] == []
    assert len(result["warnings"]) == 2


def test_generate_goldenset_rejects_empty_document():
    from app.preprocess.goldenset import generate_goldenset

    with pytest.raises(ValueError):
        generate_goldenset("   ", "doc_raw.md")


def test_goldenset_endpoint_saves_and_returns_report(monkeypatch):
    write_document()
    questions = _question_payload(1)
    answers = _answer_payload(["q001"])
    fake_model = SequencedChatModel([json.dumps(questions), json.dumps(answers)])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel("not json"))
    client = TestClient(app)

    response = client.post("/api/documents/doc_raw.md/goldenset")

    assert response.status_code == 400


def test_get_goldenset_returns_404_when_none_saved():
    client = TestClient(app)

    response = client.get("/api/documents/doc_raw.md/goldenset")

    assert response.status_code == 404


def test_record_and_latest_goldenset_answers_filters_by_schema_version():
    from app.preprocess.goldenset import latest_goldenset_answers, record_goldenset_answer

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
    from app.preprocess.goldenset import latest_goldenset_answers, record_goldenset_answer

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
    from app.preprocess.goldenset import latest_goldenset_answers

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
    from app.preprocess.goldenset import save_goldenset

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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
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
    from app.preprocess.goldenset import save_goldenset

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


# --- many questions: batches over different windows of the document ----------


class PromptAwareModel:
    """A chat model for concurrent batches, where call order is not fixed: it
    reads what each prompt asks for (a question count, or the ids to answer)
    and answers that. Question texts come from `texts`, a per-call function of
    (batch_call_number, index) so a test can make batches distinct or identical."""

    def __init__(self, texts):
        import re
        import threading

        self._re = re
        self.texts = texts
        self.question_prompts = []
        self.answer_prompts = []
        self._lock = threading.Lock()

    def invoke(self, prompt):
        text = prompt if isinstance(prompt, str) else "\n".join(m.content for m in prompt)
        count = self._re.search(r"exactly (\d+) important", text)
        if count:
            with self._lock:
                self.question_prompts.append(text)
                call = len(self.question_prompts)
            questions = [
                {"id": f"q{i:03d}", "question": self.texts(call, i), "question_type": "entity",
                 "importance": "high", "rationale": "r"}
                for i in range(1, int(count.group(1)) + 1)
            ]
            return type("R", (), {"content": json.dumps({"questions": questions})})()
        with self._lock:
            self.answer_prompts.append(text)
        ids = self._re.findall(r'"id": "(q\d+)"', text.split("Questions:")[1])
        return type("R", (), {"content": json.dumps(_answer_payload(ids))})()


LONG_DOCUMENT = "".join(f"# S{n}\nAlice works at Acme. " + ("filler words " * 30) + "\n" for n in range(3))


def test_a_later_window_of_a_long_document_shows_other_text_but_every_heading():
    from app.preprocess.goldenset import compact_document_for_questions

    document = "# A\n" + "".join(f"a{n:03d} " for n in range(100)) + "\n# B\n" + "".join(f"b{n:03d} " for n in range(100))

    first = compact_document_for_questions(document, max_chars=200)
    second = compact_document_for_questions(document, max_chars=200, window=1)

    assert first != second
    assert "a000" in first and "a000" not in second
    assert "# A" in second and "# B" in second
    assert len(second) <= 200


def test_asking_for_many_questions_makes_batches_of_at_most_ten_each_over_its_own_window(monkeypatch):
    from app.preprocess.goldenset import generate_goldenset

    model = PromptAwareModel(lambda call, i: f"batch {call} question {i}?")
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    result = generate_goldenset(LONG_DOCUMENT, "doc_raw.md", question_count=25, question_context_chars=150)

    assert len(model.question_prompts) == 3 and len(model.answer_prompts) == 3
    views = [prompt.split("<document>")[1] for prompt in model.question_prompts]
    assert len(set(views)) == 3, "each batch must be shown a different window of the document"
    assert [q["id"] for q in result["questions"]] == [f"q{i:03d}" for i in range(1, 26)]
    assert all(q["answerable"] for q in result["questions"])


def test_a_question_repeated_by_another_batch_is_dropped_with_a_warning_and_ids_stay_contiguous(monkeypatch):
    from app.preprocess.goldenset import generate_goldenset

    model = PromptAwareModel(lambda call, i: f"same question {i}?")  # every batch asks the same ones
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    result = generate_goldenset(LONG_DOCUMENT, "doc_raw.md", question_count=12, question_context_chars=150)

    assert len(result["questions"]) == 6
    assert [q["id"] for q in result["questions"]] == [f"q{i:03d}" for i in range(1, 7)]
    assert sum("duplicate" in w for w in result["warnings"]) == 6


def test_a_small_request_is_still_one_question_call_and_one_answer_call(monkeypatch):
    from app.preprocess.goldenset import generate_goldenset

    model = PromptAwareModel(lambda call, i: f"question {i}?")
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    generate_goldenset(LONG_DOCUMENT, "doc_raw.md", question_count=10, question_context_chars=150)

    assert (len(model.question_prompts), len(model.answer_prompts)) == (1, 1)


def test_saving_a_golden_set_over_an_existing_one_keeps_the_old_one(monkeypatch):
    from app.preprocess.goldenset import load_goldenset, save_goldenset
    from app.utils.paths import document_dir_for

    write_document()
    save_goldenset("doc_raw", {"questions": [{"id": "q001"}], "warnings": []})
    save_goldenset("doc_raw", {"questions": [{"id": "q001"}, {"id": "q002"}], "warnings": []})

    assert len(load_goldenset("doc_raw")["questions"]) == 2
    archived = sorted(document_dir_for("doc_raw").glob("goldenset.*.json"))
    assert len(archived) == 1
    assert len(json.loads(archived[0].read_text())["questions"]) == 1


def test_saving_the_same_golden_set_again_archives_nothing():
    from app.preprocess.goldenset import save_goldenset
    from app.utils.paths import document_dir_for

    write_document()
    report = {"questions": [{"id": "q001"}], "warnings": []}
    save_goldenset("doc_raw", report)
    save_goldenset("doc_raw", report)

    assert list(document_dir_for("doc_raw").glob("goldenset.*.json")) == []
