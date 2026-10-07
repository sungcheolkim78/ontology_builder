import json
import shutil
import threading

import pytest

from app.graph import graphdb
from app.llm.chat import set_model_name
from app.ontology import DEFAULT_SCHEMA, DOCUMENTS_DIR, create_schema_version
from app.ontology.extract_graph import (
    _find_evidence_span,
    extract_for_document,
    extract_graph,
)
from app.preprocess.parser import DATA_DIR
from app.utils.paths import document_dir_for


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class RecordingChatModel:
    def __init__(self, content):
        self.content = content
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return type("FakeResponse", (), {"content": self.content})()


class SequencedChatModel:
    """Returns each response in order, one per invoke() call. Groups now run
    concurrently, so the read-index-then-increment below is lock-protected --
    otherwise two threads could read the same index."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            content = self.responses[self.calls]
            self.calls += 1
        return type("FakeResponse", (), {"content": content})()


def _prompt_text(prompt):
    if isinstance(prompt, str):
        return prompt
    return "\n".join(getattr(m, "content", str(m)) for m in prompt)


class KeyedChatModel:
    """Returns a response based on matching a substring in the prompt --
    used for the resume tests below, so a test can fail loudly (invalid
    content, breaking json parsing) if a group that should have been
    resumed from cache is incorrectly regenerated instead."""

    def __init__(self, responses_by_marker):
        self.responses_by_marker = responses_by_marker

    def invoke(self, prompt):
        text = _prompt_text(prompt)
        for marker, content in self.responses_by_marker.items():
            if marker in text:
                return type("FakeResponse", (), {"content": content})()
        raise AssertionError(f"no matching response for prompt: {prompt!r}")


@pytest.fixture(autouse=True)
def clean_data_dir():
    graphdb.reset_database()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    yield
    graphdb.reset_database()
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


# --- extract_graph ------------------------------------------------------


def test_extract_graph_preserves_minimal_shape_when_no_structured_metadata(monkeypatch):
    # An LLM response with none of the new optional fields must round-trip
    # with exactly the old node/edge shape -- no properties/confidence/
    # evidence/source_section key should appear out of nowhere.
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works here.", schema)

    assert result == graph


def test_extract_graph_raises_when_nodes_edges_missing(monkeypatch):
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps({})))

    with pytest.raises(ValueError):
        extract_graph("some text", schema)


def test_extract_graph_drops_edges_with_unknown_node_ids(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [{"source": "n1", "target": "does_not_exist", "type": "KNOWS"}],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice.", schema)

    assert result["edges"] == []


def test_extract_graph_verifies_evidence_against_document_text(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person", "evidence": "Alice works at Acme."}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works at Acme.", schema)

    node = result["nodes"][0]
    assert node["evidence_text"] == "Alice works at Acme."
    assert node["start_offset"] == 0
    assert node["end_offset"] == len("Alice works at Acme.")


def test_extract_graph_drops_evidence_not_found_verbatim_in_document():
    assert _find_evidence_span("hallucinated quote", "Alice works at Acme.") is None
    assert _find_evidence_span(None, "Alice works at Acme.") is None
    assert _find_evidence_span("", "Alice works at Acme.") is None


def test_extract_graph_drops_evidence_offsets_for_hallucinated_quote(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person", "evidence": "not in the document"}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works at Acme.", schema)

    node = result["nodes"][0]
    assert "evidence" not in node
    assert "evidence_text" not in node
    assert "start_offset" not in node
    assert "end_offset" not in node


def test_extract_graph_keeps_only_schema_declared_properties(monkeypatch):
    schema = {
        "node_types": [
            {"name": "Coverage", "description": "a coverage", "properties": {"amount": {"datatype": "string"}}}
        ],
        "edge_types": [],
    }
    graph = {
        "nodes": [
            {
                "id": "n1",
                "label": "암보장",
                "type": "Coverage",
                "properties": {"amount": "50%", "made_up": "should not survive"},
            }
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("document text", schema)

    assert result["nodes"][0]["properties"] == {"amount": "50%"}


def test_extract_graph_ignores_malformed_property_map(monkeypatch):
    schema = {
        "node_types": [
            {"name": "Coverage", "description": "d", "properties": {"amount": {"datatype": "string"}}}
        ],
        "edge_types": [],
    }
    graph = {"nodes": [{"id": "n1", "label": "x", "type": "Coverage", "properties": "not-a-dict"}], "edges": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("document text", schema)

    assert "properties" not in result["nodes"][0]


def test_extract_graph_normalizes_confidence_and_drops_invalid_values(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [
            {"id": "n1", "label": "Alice", "type": "Person", "confidence": "HIGH"},
            {"id": "n2", "label": "Bob", "type": "Person", "confidence": "MAYBE"},
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice and Bob.", schema)

    assert result["nodes"][0]["confidence"] == "HIGH"
    assert "confidence" not in result["nodes"][1]


def test_extract_graph_keeps_source_section_only_when_it_matches_a_real_label(monkeypatch):
    schema = {"node_types": [{"name": "Coverage", "description": "d"}], "edge_types": []}
    document_text = "[주계약 > 제17조(보험금의 지급)]\n암 진단 확정 시 지급한다."
    graph = {
        "nodes": [
            {"id": "n1", "label": "암보장", "type": "Coverage", "source_section": "주계약 > 제17조(보험금의 지급)"},
            {"id": "n2", "label": "다른보장", "type": "Coverage", "source_section": "존재하지 않는 조항"},
        ],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph(document_text, schema)

    assert result["nodes"][0]["source_section"] == "주계약 > 제17조(보험금의 지급)"
    assert "source_section" not in result["nodes"][1]


@pytest.fixture
def thirty_char_groups(monkeypatch):
    """Makes two 30-character chunks fall into two chunk groups."""
    monkeypatch.setattr("app.ontology.chunk_groups.MAX_CHUNK_GROUP_CHARS", 30)


def _use_schema(schema, stem="doc_raw"):
    """Makes `schema` the document's active schema version."""
    return create_schema_version(stem, schema, document_type="general")


# --- extract_for_document over chunk groups ----------------------------------


def test_extract_for_document_merges_coreferent_nodes_across_chunk_groups(monkeypatch, thirty_char_groups):
    schema = {
        "node_types": [{"name": "Person", "description": "a person"}, {"name": "Org", "description": "an org"}],
        "edge_types": [{"name": "WORKS_AT", "description": "works at", "source": "Person", "target": "Org"}],
    }
    graph1 = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}, {"id": "n2", "label": "Acme", "type": "Org"}],
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
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

    write_document()
    write_chunks("doc_raw", ["a" * 30, "b" * 30])
    _use_schema(schema)

    _, result, _ = extract_for_document("doc_raw")

    labels = {(n["type"], n["label"]) for n in result["nodes"]}
    assert labels == {("Person", "Alice"), ("Org", "Acme"), ("Person", "Bob")}
    assert len(result["nodes"]) == 3  # Alice/Acme deduped, not double-counted
    assert len(result["edges"]) == 2  # duplicate Alice->Acme edge collapses; Bob->Acme survives


# --- extract_for_document seam -----------------------------------------------


def test_extract_for_document_raises_file_not_found_when_document_missing():
    with pytest.raises(FileNotFoundError):
        extract_for_document("missing_raw")


def test_extract_for_document_creates_default_schema_when_none_saved(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    schema, result_graph, version = extract_for_document("doc_raw")

    assert schema == DEFAULT_SCHEMA
    assert result_graph == graph
    assert version == 1
    saved_schema = json.loads((DOCUMENTS_DIR / "doc_raw" / "schema_v1.json").read_text())
    assert saved_schema == DEFAULT_SCHEMA


def test_extract_for_document_uses_chunks_when_present(monkeypatch):
    write_document()
    write_chunks("doc_raw", ["Alice works at Acme."])
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    fake_model = RecordingChatModel(json.dumps(graph))
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)

    schema, result_graph, version = extract_for_document("doc_raw")

    assert schema == DEFAULT_SCHEMA
    assert result_graph == graph
    assert version == 1
    assert len(fake_model.prompts) == 1  # single chunk group -> no merge needed


# --- progress reporting (stem given) -----------------------------------------


def _read_progress(stem, operation):
    return json.loads((document_dir_for(stem) / "progress" / f"{operation}.json").read_text())


def test_extract_for_document_reports_progress_with_running_node_edge_counts(monkeypatch, thirty_char_groups):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph1 = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    graph2 = {
        "nodes": [{"id": "n2", "label": "Bob", "type": "Person"}, {"id": "n3", "label": "Carol", "type": "Person"}],
        "edges": [],
    }
    fake_model = SequencedChatModel([json.dumps(graph1), json.dumps(graph2)])
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: fake_model)
    write_document()
    write_chunks("doc_raw", ["a" * 30, "b" * 30])
    _use_schema(schema)

    extract_for_document("doc_raw")

    state = _read_progress("doc_raw", "extract")
    assert state["status"] == "done"
    assert state["total"] == 2
    assert state["completed"] == 2
    assert state["nodes"] == 3  # 1 from group1 + 2 from group2, running total


def test_extract_for_document_reports_progress_for_whole_document(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}, {"id": "n2", "label": "Bob", "type": "Entity"}], "edges": []}
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    extract_for_document("doc_raw")

    state = _read_progress("doc_raw", "extract")
    assert state["status"] == "done"
    assert state["total"] == 1
    assert state["completed"] == 1
    assert state["nodes"] == 2
    assert state["edges"] == 0


# --- resume cache, observed through extract_for_document ---------------------


class ScriptedChatModel:
    """Answers by prompt substring and can be told to raise for chosen
    markers; records every prompt so tests can count a group's LLM calls."""

    def __init__(self, by_marker, fail_on=()):
        self.by_marker = by_marker
        self.fail_on = set(fail_on)
        self.prompts = []
        self._lock = threading.Lock()

    def calls_for(self, marker):
        return sum(1 for p in self.prompts if marker in _prompt_text(p))

    def invoke(self, prompt):
        text = _prompt_text(prompt)
        with self._lock:
            self.prompts.append(prompt)
        for marker in self.fail_on:
            if marker in text:
                raise RuntimeError(f"simulated failure for {marker[:3]}...")
        for marker, content in self.by_marker.items():
            if marker in text:
                return type("FakeResponse", (), {"content": content})()
        raise AssertionError(f"no matching response for prompt: {text[:80]!r}")


_PERSON_SCHEMA = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}


def test_extract_for_document_retry_reruns_only_the_failed_group(monkeypatch, thirty_char_groups):
    graph1 = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    graph2 = {"nodes": [{"id": "n2", "label": "Bob", "type": "Person"}], "edges": []}
    model = ScriptedChatModel(
        {"a" * 30: json.dumps(graph1), "b" * 30: json.dumps(graph2)}, fail_on={"b" * 30}
    )
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
    write_document()
    write_chunks("doc_raw", ["a" * 30, "b" * 30])
    _use_schema(_PERSON_SCHEMA)
    with pytest.raises(RuntimeError):
        extract_for_document("doc_raw")

    model.fail_on = set()
    _, result, _ = extract_for_document("doc_raw")

    labels = {(n["type"], n["label"]) for n in result["nodes"]}
    assert labels == {("Person", "Alice"), ("Person", "Bob")}
    assert model.calls_for("a" * 30) == 1  # group 1 resumed from the resume cache
    assert model.calls_for("b" * 30) == 2


def test_extract_for_document_does_not_reuse_results_across_different_schemas(monkeypatch):
    # Regression: the resume cache used to be keyed by group index alone, so
    # re-extracting after activating a different schema version silently
    # reused the previous schema's nodes/edges for every group index.
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    model = ScriptedChatModel({"hello": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
    write_document()
    write_chunks("doc_raw", ["hello"])
    other_schema = {
        "node_types": [{"name": "Person", "description": "a person"}, {"name": "Org", "description": "an org"}],
        "edge_types": [],
    }

    _use_schema(_PERSON_SCHEMA)
    extract_for_document("doc_raw")
    _use_schema(other_schema)
    extract_for_document("doc_raw")

    assert len(model.prompts) == 2


def test_extract_for_document_reuses_results_when_the_schema_is_unchanged(monkeypatch):
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    model = ScriptedChatModel({"hello": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
    write_document()
    write_chunks("doc_raw", ["hello"])
    _use_schema(_PERSON_SCHEMA)

    extract_for_document("doc_raw")
    extract_for_document("doc_raw")

    assert len(model.prompts) == 1


# --- extract_for_document: an unchunked document is one chunk group ----------


def test_extract_for_document_reuses_the_resume_cache_for_an_unchunked_document(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    model = ScriptedChatModel({"Alice works at Acme.": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    _, first, _ = extract_for_document("doc_raw")
    _, second, _ = extract_for_document("doc_raw")

    assert first == second == graph
    assert len(model.prompts) == 1


def test_extract_for_document_does_not_reuse_results_across_a_model_change(monkeypatch):
    write_document()
    monkeypatch.setattr("app.llm.chat._selected_models", {})
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    model = ScriptedChatModel({"Alice works at Acme.": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    set_model_name("openai/gpt-5.4-mini", "extract_graph")
    extract_for_document("doc_raw")
    extract_for_document("doc_raw")  # same model: reused
    assert len(model.prompts) == 1

    set_model_name("anthropic/claude-sonnet-5", "extract_graph")
    extract_for_document("doc_raw")
    assert len(model.prompts) == 2


def test_extract_for_document_evidence_offsets_are_relative_to_raw_md_for_an_unchunked_document(monkeypatch):
    write_document(content="# Doc\nAlice works at Acme.")
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Entity", "evidence": "Alice works at Acme."}],
        "edges": [],
    }
    model = ScriptedChatModel({"Alice works at Acme.": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    _, result, _ = extract_for_document("doc_raw")

    node = result["nodes"][0]
    assert (node["start_offset"], node["end_offset"]) == (6, 26)


# --- extract_for_document persists the graph it returns -----------------------


def test_extract_for_document_saves_the_graph_for_the_version_it_returns(monkeypatch):
    write_document()
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}, {"id": "n2", "label": "Acme", "type": "Entity"}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    _, result, version = extract_for_document("doc_raw")

    saved = graphdb.load_graph("doc_raw", version=version)
    assert {n["label"] for n in saved["nodes"]} == {"Alice", "Acme"}
    assert len(saved["nodes"]) == len(result["nodes"])


# --- evidence offsets are always relative to raw.md ---------------------------


def test_extract_for_document_evidence_offsets_are_relative_to_raw_md_for_a_chunked_document(monkeypatch):
    # The chunk group text the LLM sees is "[c0]\n..." prefixed, so an offset
    # into *that* is not an offset into raw.md.
    write_document(content="# Doc\nAlice works at Acme.")
    write_chunks("doc_raw", ["Alice works at Acme."])
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Entity", "evidence": "Alice works at Acme."}],
        "edges": [],
    }
    model = ScriptedChatModel({"Alice works at Acme.": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    _, result, _ = extract_for_document("doc_raw")

    node = result["nodes"][0]
    assert (node["start_offset"], node["end_offset"]) == (6, 26)


def test_extract_for_document_edge_evidence_offsets_are_relative_to_raw_md_too(monkeypatch):
    write_document(content="# Doc\nAlice works at Acme.")
    write_chunks("doc_raw", ["Alice works at Acme."])
    schema = {
        "node_types": [{"name": "Person", "description": "p"}, {"name": "Org", "description": "o"}],
        "edge_types": [{"name": "WORKS_AT", "description": "w", "source": "Person", "target": "Org"}],
    }
    _use_schema(schema)
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}, {"id": "n2", "label": "Acme", "type": "Org"}],
        "edges": [
            {"source": "n1", "target": "n2", "type": "WORKS_AT", "evidence": "Alice works at Acme."}
        ],
    }
    model = ScriptedChatModel({"Alice works at Acme.": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    _, result, _ = extract_for_document("doc_raw")

    edge = result["edges"][0]
    assert (edge["start_offset"], edge["end_offset"]) == (6, 26)


def test_extract_for_document_keeps_evidence_but_drops_offsets_when_the_quote_is_not_in_raw_md(monkeypatch):
    # chunking drops "---" page-break lines from a chunk's text, so a quote
    # that spans one is verbatim in the text the LLM saw but not in raw.md.
    write_document(content="# Doc\nAlice works\n---\nat Acme.")
    write_chunks("doc_raw", ["Alice works\nat Acme."])
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Entity", "evidence": "Alice works\nat Acme."}],
        "edges": [],
    }
    model = ScriptedChatModel({"Alice works": json.dumps(graph)})
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)

    _, result, _ = extract_for_document("doc_raw")

    node = result["nodes"][0]
    assert node["evidence_text"] == "Alice works\nat Acme."
    assert "start_offset" not in node
    assert "end_offset" not in node
