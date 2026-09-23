import json
import shutil
import threading

import pytest

from app.ontology.generate_schema import (
    _map_concurrently,
    discover_for_document,
    discover_ontology,
    discover_ontology_from_chunks,
    find_redundant_type_pairs,
    generate_schema,
    generate_schema_from_chunks,
    measure_schema_stability,
    schema_for_document,
    summarize_document,
)
from app.preprocess.embeddings import EMBEDDING_DIM
from app.preprocess.parser import DATA_DIR
from app.utils.paths import document_dir_for


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class RecordingChatModel:
    """Same as FakeChatModel, but remembers every prompt it was invoked
    with -- needed for the discovery-hint tests below, which assert on the
    prompt text itself rather than just the returned content."""

    def __init__(self, content):
        self.content = content
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("FakeResponse", (), {"content": self.content})()


def _prompt_text(prompt):
    """Flattens a captured prompt (now usually a [SystemMessage, HumanMessage]
    list -- see generate_schema.py's own SystemMessage/HumanMessage calls)
    into one string for substring assertions, regardless of which shape it is."""
    if isinstance(prompt, str):
        return prompt
    return "\n".join(getattr(m, "content", str(m)) for m in prompt)


class SequencedChatModel:
    """Returns each response in order, one per invoke() call -- needed for
    the *_from_chunks consolidation tests, which make one LLM call per
    group plus one more for the consolidation pass. The map step of
    generate_schema_from_chunks/measure_schema_stability now calls invoke()
    concurrently from multiple threads, so the read-index-then-increment
    below is lock-protected -- otherwise two threads could race and read the
    same index (or skip one)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            content = self.responses[self.calls]
            self.calls += 1
        return type("FakeResponse", (), {"content": content})()


class KeyedChatModel:
    """Returns a response based on matching a substring in the prompt,
    instead of call order -- needed for the group-candidate-file tests
    below, since the map step now calls invoke() concurrently and a plain
    call-order fake (like SequencedChatModel) can't guarantee which group's
    call gets which canned response. `default`, if given, is used for any
    prompt that matches no marker (the reduce/consolidation call, which
    always happens after every group call, so it has no such ordering
    problem)."""

    def __init__(self, responses_by_marker, default=None):
        self.responses_by_marker = responses_by_marker
        self.default = default

    def invoke(self, prompt):
        text = _prompt_text(prompt)
        for marker, content in self.responses_by_marker.items():
            if marker in text:
                return type("FakeResponse", (), {"content": content})()
        if self.default is not None:
            return type("FakeResponse", (), {"content": self.default})()
        raise AssertionError(f"no matching response for prompt: {prompt!r}")


class FakeEmbeddingModel:
    def embed_documents(self, texts):
        return [[0.0] * EMBEDDING_DIM for _ in texts]


@pytest.fixture(autouse=True)
def stub_embedding_model(monkeypatch):
    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: FakeEmbeddingModel())


@pytest.fixture(autouse=True)
def clean_data_dir():
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)


def write_document(filename="doc_raw.md", content="# Doc\nAlice works at Acme."):
    stem = filename.removesuffix(".md")
    d = document_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.md").write_text(content)


def write_chunks(stem, chunk_texts):
    (document_dir_for(stem) / "chunks.json").write_text(
        json.dumps(
            {
                "source": stem,
                "preamble": {"line_start": 1, "line_end": 1, "text": ""},
                "chunks": [
                    {
                        "id": f"0::c{i}",
                        "section_index": i,
                        "section_label": "",
                        "article_no": str(i + 1),
                        "sub_no": None,
                        "title": "",
                        "path": f"c{i}",
                        "line_start": 1,
                        "line_end": 2,
                        "text": text,
                    }
                    for i, text in enumerate(chunk_texts)
                ],
            }
        )
    )


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


# --- _map_concurrently -------------------------------------------------------


def test_map_concurrently_preserves_order_and_overlaps_calls():
    import time

    def slow_double(x):
        time.sleep(0.2)
        return x * 2

    start = time.monotonic()
    result = _map_concurrently(slow_double, [1, 2, 3, 4, 5])
    elapsed = time.monotonic() - start

    assert result == [2, 4, 6, 8, 10]  # order preserved despite concurrent execution
    assert elapsed < 0.2 * 5  # overlapped, not run one after another


def test_map_concurrently_single_item_skips_thread_pool():
    calls = []

    def record_and_return(x):
        calls.append(threading.current_thread())
        return x

    result = _map_concurrently(record_and_return, ["only"])

    assert result == ["only"]
    assert calls == [threading.current_thread()]  # ran inline, no worker thread


# --- discover_ontology ------------------------------------------------------


def test_discover_ontology_returns_report_from_llm_json(monkeypatch):
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))

    result = discover_ontology("some document text")

    assert result == report


def test_discover_ontology_raises_when_classes_missing(monkeypatch):
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps({"no_classes_key": []}))
    )

    with pytest.raises(ValueError):
        discover_ontology("some document text")


def test_discover_ontology_from_chunks_single_group_skips_consolidation(monkeypatch):
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    fake_model = RecordingChatModel(json.dumps(report))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = discover_ontology_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000)

    assert result == report
    assert len(fake_model.prompts) == 1


def test_discover_ontology_from_chunks_consolidates_multiple_groups(monkeypatch):
    group1 = _discovery_report(
        domain="insurance",
        classes=[{"name": "Policy", "definition": "d1", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}],
        competency_questions=["What does this cover?"],
    )
    group2 = _discovery_report(
        domain="insurance",
        classes=[{"name": "InsurancePolicy", "definition": "d2", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}],
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
    # competency_questions deduped across groups (identical string in both)
    assert result["competency_questions"] == ["What does this cover?"]
    assert result["domain_model"]["domain"] == "insurance"
    assert fake_model.calls == 3


# --- generate_schema ---------------------------------------------------------


def test_generate_schema_returns_schema_from_llm_json(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "d"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    result = generate_schema("some document text")

    assert result == schema


def test_generate_schema_raises_for_unknown_document_type():
    with pytest.raises(ValueError):
        generate_schema("some document text", document_type="nonsense")


def test_generate_schema_raises_when_node_edge_types_missing(monkeypatch):
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps({})))

    with pytest.raises(ValueError):
        generate_schema("some document text")


def test_generate_schema_includes_discovery_hint_when_given(monkeypatch):
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    generate_schema("some document text", discovery={"classes": [{"name": "Policy"}]})

    assert "Reference --" in _prompt_text(fake_model.prompts[0])
    assert "Policy" in _prompt_text(fake_model.prompts[0])


def test_generate_schema_ignores_discovery_by_default(monkeypatch):
    schema = {"node_types": [], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    generate_schema("some document text")

    assert "Reference --" not in _prompt_text(fake_model.prompts[0])


def test_generate_schema_from_chunks_single_group_skips_consolidation(monkeypatch):
    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(schema))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = generate_schema_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000)

    assert result == schema
    assert len(fake_model.prompts) == 1


def test_generate_schema_from_chunks_consolidates_multiple_groups(monkeypatch):
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


# --- discover_for_document / schema_for_document seams -----------------------


def test_discover_for_document_raises_file_not_found_when_document_missing():
    with pytest.raises(FileNotFoundError):
        discover_for_document("missing_raw")


def test_discover_for_document_uses_whole_document_when_no_chunks(monkeypatch):
    write_document()
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))

    result = discover_for_document("doc_raw")

    assert result == report


def test_discover_for_document_uses_chunks_when_present(monkeypatch):
    write_document()
    write_chunks("doc_raw", ["Alice works at Acme."])
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    fake_model = RecordingChatModel(json.dumps(report))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = discover_for_document("doc_raw")

    assert result == report
    assert len(fake_model.prompts) == 1  # single chunk group -> no consolidation call


def test_discover_for_document_ignores_max_chars_for_group_budget(monkeypatch):
    """Regression: discover_for_document used to forward its own `max_chars`
    (the frontend's "최대 문자수" field, which defaults to 1,000,000) as
    discover_ontology_from_chunks's `max_group_chars`, silently overriding
    MAX_CHUNK_GROUP_CHARS for every real request, since group_chunks_by_budget
    only falls back to that env-configured default when max_group_chars is
    None -- see discover_for_document's own docstring. A large `max_chars`
    must not collapse chunking into a single group."""
    write_document()
    write_chunks("doc_raw", ["a" * 30, "b" * 30])
    monkeypatch.setattr("app.ontology.utils.MAX_CHUNK_GROUP_CHARS", 30)
    group1 = _discovery_report(classes=[{"name": "Policy", "definition": "d1", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    group2 = _discovery_report(classes=[{"name": "Coverage", "definition": "d2", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    consolidated = {"classes": group1["classes"] + group2["classes"], "relationships": []}
    model = KeyedChatModel({"a" * 30: json.dumps(group1), "b" * 30: json.dumps(group2)}, default=json.dumps(consolidated))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: model)

    discover_for_document("doc_raw", max_chars=1_000_000)  # the frontend's real-world default

    progress = _read_progress("doc_raw", "discover")
    assert progress["total"] == 2  # two groups, not one -- max_chars did not collapse them


def test_schema_for_document_raises_file_not_found_when_document_missing():
    with pytest.raises(FileNotFoundError):
        schema_for_document("missing_raw")


def test_schema_for_document_uses_whole_document_when_no_chunks(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    result = schema_for_document("doc_raw")

    assert result == schema


def test_schema_for_document_uses_chunks_when_present(monkeypatch):
    write_document()
    write_chunks("doc_raw", ["Alice works at Acme."])
    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    result = schema_for_document("doc_raw")

    assert result["node_types"] == schema["node_types"]


def test_schema_for_document_ignores_max_chars_for_group_budget(monkeypatch):
    """Regression: same bug as discover_for_document's -- schema_for_document
    used to forward `max_chars` as generate_schema_from_chunks's own
    `max_group_chars`, silently defeating MAX_CHUNK_GROUP_CHARS."""
    write_document()
    write_chunks("doc_raw", ["a" * 30, "b" * 30])
    monkeypatch.setattr("app.ontology.utils.MAX_CHUNK_GROUP_CHARS", 30)
    schema1 = {"node_types": [{"name": "Policy", "description": "d1"}], "edge_types": []}
    schema2 = {"node_types": [{"name": "Coverage", "description": "d2"}], "edge_types": []}
    consolidated = {"node_types": schema1["node_types"] + schema2["node_types"], "edge_types": []}
    model = KeyedChatModel({"a" * 30: json.dumps(schema1), "b" * 30: json.dumps(schema2)}, default=json.dumps(consolidated))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: model)

    schema_for_document("doc_raw", max_chars=1_000_000)  # the frontend's real-world default

    progress = _read_progress("doc_raw", "schema")
    assert progress["total"] == 2  # two groups, not one -- max_chars did not collapse them


# --- summarize_document --------------------------------------------------


def test_summarize_document_strips_and_returns_llm_text(monkeypatch):
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel("  이 문서는 보험약관을 설명합니다.  ")
    )

    assert summarize_document("some document text") == "이 문서는 보험약관을 설명합니다."


def test_summarize_document_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel("   "))

    with pytest.raises(ValueError):
        summarize_document("some document text")


# --- find_redundant_type_pairs / measure_schema_stability --------------------


def test_find_redundant_type_pairs_flags_near_duplicate_descriptions(monkeypatch):
    schema = {
        "node_types": [
            {"name": "Customer", "description": "a paying customer"},
            {"name": "Client", "description": "a paying customer"},
            {"name": "Product", "description": "something sold"},
        ],
        "edge_types": [],
    }

    class VectorFakeEmbeddingModel:
        def embed_documents(self, texts):
            # Customer/Client get identical vectors; Product gets an
            # orthogonal one, so only the first pair should pass threshold.
            return [[0.0, 1.0] if text.startswith("Product") else [1.0, 0.0] for text in texts]

    monkeypatch.setattr("app.ontology.get_embedding_model", lambda: VectorFakeEmbeddingModel())

    pairs = find_redundant_type_pairs(schema, threshold=0.9)

    assert pairs == [{"element_type": "node_type", "a": "Customer", "b": "Client", "similarity": pytest.approx(1.0)}]


def test_find_redundant_type_pairs_skips_types_with_fewer_than_two_entries():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}

    pairs = find_redundant_type_pairs(schema)

    assert pairs == []


def test_measure_schema_stability_perfect_agreement_across_runs(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    result = measure_schema_stability("some document text", runs=3)

    assert result["avg_jaccard_similarity"] == 1.0
    assert result["type_name_sets"] == [["Person"]] * 3


def test_measure_schema_stability_disagreement_lowers_similarity(monkeypatch):
    schemas = [
        {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []},
        {"node_types": [{"name": "Individual", "description": "a person"}], "edge_types": []},
    ]
    fake_model = SequencedChatModel([json.dumps(s) for s in schemas])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = measure_schema_stability("some document text", runs=2)

    assert result["avg_jaccard_similarity"] == 0.0


def test_measure_schema_stability_raises_for_fewer_than_two_runs():
    with pytest.raises(ValueError):
        measure_schema_stability("doc", runs=1)


# --- progress reporting (stem given) -----------------------------------------


def _read_progress(stem, operation):
    return json.loads((document_dir_for(stem) / "progress" / f"{operation}.json").read_text())


def test_discover_ontology_from_chunks_reports_progress_when_stem_given(monkeypatch):
    write_document()
    group1 = _discovery_report(classes=[{"name": "Policy", "definition": "d1", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    group2 = _discovery_report(classes=[{"name": "Coverage", "definition": "d2", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    consolidated = {"classes": group1["classes"] + group2["classes"], "relationships": []}
    fake_model = SequencedChatModel([json.dumps(group1), json.dumps(group2), json.dumps(consolidated)])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    discover_ontology_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    state = _read_progress("doc_raw", "discover")
    assert state["status"] == "done"
    assert state["total"] == 2
    assert state["completed"] == 2


def test_discover_ontology_from_chunks_writes_group_candidate_files(monkeypatch):
    write_document()
    group1 = _discovery_report(classes=[{"name": "Policy", "definition": "d1", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    group2 = _discovery_report(classes=[{"name": "Coverage", "definition": "d2", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    consolidated = {"classes": group1["classes"] + group2["classes"], "relationships": []}
    model = KeyedChatModel({"a" * 30: json.dumps(group1), "b" * 30: json.dumps(group2)}, default=json.dumps(consolidated))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: model)

    discover_ontology_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    progress_dir = document_dir_for("doc_raw") / "progress"
    assert json.loads((progress_dir / "discover_classes_1.json").read_text()) == group1["classes"]
    assert json.loads((progress_dir / "discover_relationships_1.json").read_text()) == group1["relationships"]
    assert json.loads((progress_dir / "discover_classes_2.json").read_text()) == group2["classes"]
    assert json.loads((progress_dir / "discover_relationships_2.json").read_text()) == group2["relationships"]


def test_discover_ontology_from_chunks_clears_stale_candidate_files_from_previous_run(monkeypatch):
    write_document()
    progress_dir = document_dir_for("doc_raw") / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "discover_classes_5.json").write_text("[]")
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))

    discover_ontology_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000, stem="doc_raw")

    assert not (progress_dir / "discover_classes_5.json").exists()
    assert json.loads((progress_dir / "discover_classes_1.json").read_text()) == report["classes"]


def test_discover_ontology_from_chunks_resumes_full_report_from_cache(monkeypatch):
    # Regression: a prior implementation only cached classes/relationships
    # per group, which would have silently dropped attributes/events/rules/
    # terminology/competency_questions/warnings for any group resumed from
    # cache instead of freshly generated.
    write_document()
    progress_dir = document_dir_for("doc_raw") / "progress"
    progress_dir.mkdir(parents=True)
    group1_fields = {
        "domain_model": {"domain": "insurance", "subdomains": [], "document_types": [], "business_processes": [], "major_actors": []},
        "classes": [{"name": "Policy", "definition": "d1", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}],
        "relationships": [],
        "attributes": [{"name": "amount", "defined_on": "Policy", "definition": "d", "datatype": "number", "unit": "", "required": True, "rationale": ""}],
        "events": [{"name": "Claim", "definition": "d", "trigger": "t", "affected_entities": []}],
        "rules": [{"name": "R1", "description": "d", "conditions": [], "consequences": [], "exceptions": []}],
        "terminology": [{"canonical_term": "보험료", "synonyms": [], "abbreviations": [], "source_terms": []}],
        "competency_questions": ["What is covered?"],
        "warnings": ["ambiguous term"],
    }
    for field, value in group1_fields.items():
        (progress_dir / f"discover_{field}_1.json").write_text(json.dumps(value, ensure_ascii=False))
    # Group 2 has no cache -- must actually be generated; "a" * 30 (group 1's
    # text) is mapped to garbage so the test fails loudly if group 1 is
    # incorrectly regenerated instead of resumed from the cache above.
    group2 = _discovery_report(classes=[{"name": "Coverage", "definition": "d2", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    consolidated = {"classes": group1_fields["classes"] + group2["classes"], "relationships": []}
    model = KeyedChatModel(
        {"a" * 30: "NOT_VALID_JSON -- group 1 should have been resumed from cache", "b" * 30: json.dumps(group2)},
        default=json.dumps(consolidated),
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: model)

    result = discover_ontology_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    assert result["attributes"] == group1_fields["attributes"]
    assert result["events"] == group1_fields["events"]
    assert result["rules"] == group1_fields["rules"]
    assert result["terminology"] == group1_fields["terminology"]
    assert "What is covered?" in result["competency_questions"]
    assert "ambiguous term" in result["warnings"]


def test_discover_ontology_from_chunks_writes_no_progress_without_stem(monkeypatch):
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))

    discover_ontology_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000)

    assert not (DATA_DIR / "documents").exists()


def test_generate_schema_from_chunks_reports_progress_when_stem_given(monkeypatch):
    write_document()
    schema1 = {"node_types": [{"name": "Policy", "description": "d1"}], "edge_types": []}
    schema2 = {"node_types": [{"name": "Coverage", "description": "d2"}], "edge_types": []}
    consolidated = {"node_types": schema1["node_types"] + schema2["node_types"], "edge_types": []}
    fake_model = SequencedChatModel([json.dumps(schema1), json.dumps(schema2), json.dumps(consolidated)])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    generate_schema_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    state = _read_progress("doc_raw", "schema")
    assert state["status"] == "done"
    assert state["total"] == 2
    assert state["completed"] == 2


def test_generate_schema_from_chunks_writes_group_candidate_files(monkeypatch):
    write_document()
    schema1 = {"node_types": [{"name": "Policy", "description": "d1"}], "edge_types": []}
    schema2 = {"node_types": [{"name": "Coverage", "description": "d2"}], "edge_types": []}
    consolidated = {"node_types": schema1["node_types"] + schema2["node_types"], "edge_types": []}
    model = KeyedChatModel({"a" * 30: json.dumps(schema1), "b" * 30: json.dumps(schema2)}, default=json.dumps(consolidated))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: model)

    generate_schema_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    progress_dir = document_dir_for("doc_raw") / "progress"
    assert json.loads((progress_dir / "schema_node_types_1.json").read_text()) == schema1["node_types"]
    assert json.loads((progress_dir / "schema_edge_types_1.json").read_text()) == schema1["edge_types"]
    assert json.loads((progress_dir / "schema_node_types_2.json").read_text()) == schema2["node_types"]
    assert json.loads((progress_dir / "schema_edge_types_2.json").read_text()) == schema2["edge_types"]


def test_generate_schema_from_chunks_resumes_from_cached_group_candidates(monkeypatch):
    # Regression: a retried generate_schema_from_chunks call used to redo
    # every group's LLM call from scratch, even ones a prior (e.g. partially
    # failed) attempt had already completed successfully.
    write_document()
    progress_dir = document_dir_for("doc_raw") / "progress"
    progress_dir.mkdir(parents=True)
    schema1 = {"node_types": [{"name": "Policy", "description": "d1"}], "edge_types": []}
    schema2 = {"node_types": [{"name": "Coverage", "description": "d2"}], "edge_types": []}
    (progress_dir / "schema_node_types_1.json").write_text(json.dumps(schema1["node_types"]))
    (progress_dir / "schema_edge_types_1.json").write_text(json.dumps(schema1["edge_types"]))
    (progress_dir / "schema_node_types_2.json").write_text(json.dumps(schema2["node_types"]))
    (progress_dir / "schema_edge_types_2.json").write_text(json.dumps(schema2["edge_types"]))
    consolidated = {"node_types": schema1["node_types"] + schema2["node_types"], "edge_types": []}
    fake_model = RecordingChatModel(json.dumps(consolidated))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = generate_schema_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    assert result == consolidated
    assert len(fake_model.prompts) == 1  # only the consolidation call -- both groups resumed from cache


def test_generate_schema_from_chunks_only_regenerates_missing_groups(monkeypatch):
    write_document()
    progress_dir = document_dir_for("doc_raw") / "progress"
    progress_dir.mkdir(parents=True)
    schema1 = {"node_types": [{"name": "Policy", "description": "d1"}], "edge_types": []}
    (progress_dir / "schema_node_types_1.json").write_text(json.dumps(schema1["node_types"]))
    (progress_dir / "schema_edge_types_1.json").write_text(json.dumps(schema1["edge_types"]))
    # Group 2 has no cache -- must actually be generated; "a" * 30 (group 1's
    # text) is mapped to garbage so the test fails loudly if group 1 is
    # incorrectly regenerated instead of resumed from the cache above.
    schema2 = {"node_types": [{"name": "Coverage", "description": "d2"}], "edge_types": []}
    consolidated = {"node_types": schema1["node_types"] + schema2["node_types"], "edge_types": []}
    model = KeyedChatModel(
        {"a" * 30: "NOT_VALID_JSON -- group 1 should have been resumed from cache", "b" * 30: json.dumps(schema2)},
        default=json.dumps(consolidated),
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: model)

    result = generate_schema_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
        max_group_chars=30,
        stem="doc_raw",
    )

    assert result == consolidated
    assert json.loads((progress_dir / "schema_node_types_2.json").read_text()) == schema2["node_types"]


def test_generate_schema_from_chunks_clears_stale_candidate_files_from_previous_run(monkeypatch):
    write_document()
    progress_dir = document_dir_for("doc_raw") / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "schema_node_types_5.json").write_text("[]")
    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    generate_schema_from_chunks([{"path": "p1", "text": "hello"}], max_group_chars=1000, stem="doc_raw")

    assert not (progress_dir / "schema_node_types_5.json").exists()
    assert json.loads((progress_dir / "schema_node_types_1.json").read_text()) == schema["node_types"]


def test_discover_for_document_reports_progress_for_whole_document(monkeypatch):
    write_document()
    report = _discovery_report(classes=[{"name": "Policy", "definition": "d", "category": "CONCEPT", "parent": "", "rationale": "", "confidence": "HIGH"}])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))

    discover_for_document("doc_raw")

    state = _read_progress("doc_raw", "discover")
    assert state == {
        "operation": "discover",
        "status": "done",
        "stage": "done",
        "total": 1,
        "completed": 1,
        "error": None,
    }


def test_schema_for_document_reports_progress_for_whole_document(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Policy", "description": "d"}], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(schema)))

    schema_for_document("doc_raw")

    state = _read_progress("doc_raw", "schema")
    assert state["status"] == "done"
    assert state["total"] == 1
    assert state["completed"] == 1
