import json
import shutil

import pytest

from app.ontology import DEFAULT_SCHEMA, DOCUMENTS_DIR
from app.ontology.extract_graph import (
    _find_evidence_span,
    extract_for_document,
    extract_graph,
    extract_graph_from_chunks,
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
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        content = self.responses[self.calls]
        self.calls += 1
        return type("FakeResponse", (), {"content": content})()


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


# --- extract_graph ------------------------------------------------------


def test_extract_graph_preserves_minimal_shape_when_no_structured_metadata(monkeypatch):
    # An LLM response with none of the new optional fields must round-trip
    # with exactly the old node/edge shape -- no properties/confidence/
    # evidence/source_section key should appear out of nowhere.
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph("Alice works here.", schema)

    assert result == graph


def test_extract_graph_raises_when_nodes_edges_missing(monkeypatch):
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps({})))

    with pytest.raises(ValueError):
        extract_graph("some text", schema)


def test_extract_graph_drops_edges_with_unknown_node_ids(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [{"source": "n1", "target": "does_not_exist", "type": "KNOWS"}],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph(document_text, schema)

    assert result["nodes"][0]["source_section"] == "주계약 > 제17조(보험금의 지급)"
    assert "source_section" not in result["nodes"][1]


# --- extract_graph_from_chunks -----------------------------------------------


def test_extract_graph_from_chunks_single_group_skips_merge(monkeypatch):
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    fake_model = RecordingChatModel(json.dumps(graph))
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}

    result = extract_graph_from_chunks([{"path": "p1", "text": "hello"}], schema, max_group_chars=1000)

    assert result == graph
    assert len(fake_model.prompts) == 1


def test_extract_graph_from_chunks_merges_coreferent_nodes_across_groups(monkeypatch):
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
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = extract_graph_from_chunks(
        [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}], schema, max_group_chars=30
    )

    labels = {(n["type"], n["label"]) for n in result["nodes"]}
    assert labels == {("Person", "Alice"), ("Org", "Acme"), ("Person", "Bob")}
    assert len(result["nodes"]) == 3  # Alice/Acme deduped, not double-counted
    assert len(result["edges"]) == 2  # duplicate Alice->Acme edge collapses; Bob->Acme survives


def test_extract_graph_from_chunks_writes_progress_files_per_group(monkeypatch, caplog):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph1 = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    graph2 = {"nodes": [{"id": "n2", "label": "Bob", "type": "Person"}], "edges": []}
    fake_model = SequencedChatModel([json.dumps(graph1), json.dumps(graph2)])
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)
    write_document()
    stem = "doc_raw"

    with caplog.at_level("INFO"):
        extract_graph_from_chunks(
            [{"path": "p1", "text": "a" * 30}, {"path": "p2", "text": "b" * 30}],
            schema,
            max_group_chars=30,
            stem=stem,
        )

    progress_dir = document_dir_for(stem) / "extraction_progress"
    assert json.loads((progress_dir / "node_proc_1.json").read_text()) == graph1["nodes"]
    assert json.loads((progress_dir / "node_proc_2.json").read_text()) == graph2["nodes"]
    assert "1/2" in caplog.text
    assert "2/2" in caplog.text


def test_extract_graph_from_chunks_clears_stale_progress_files_from_previous_run(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    write_document()
    stem = "doc_raw"
    progress_dir = document_dir_for(stem) / "extraction_progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "node_proc_5.json").write_text("[]")

    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    extract_graph_from_chunks([{"path": "p1", "text": "hello"}], schema, max_group_chars=1000, stem=stem)

    assert not (progress_dir / "node_proc_5.json").exists()
    assert json.loads((progress_dir / "node_proc_1.json").read_text()) == graph["nodes"]


def test_extract_graph_from_chunks_without_stem_writes_no_progress_files(monkeypatch):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Person"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
    )

    result = extract_graph_from_chunks([{"path": "p1", "text": "hello"}], schema, max_group_chars=1000)

    assert result == graph
    assert not DOCUMENTS_DIR.exists() or not any(DOCUMENTS_DIR.iterdir())


# --- extract_for_document seam -----------------------------------------------


def test_extract_for_document_raises_file_not_found_when_document_missing():
    with pytest.raises(FileNotFoundError):
        extract_for_document("missing_raw")


def test_extract_for_document_creates_default_schema_when_none_saved(monkeypatch):
    write_document()
    graph = {"nodes": [{"id": "n1", "label": "Alice", "type": "Entity"}], "edges": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(graph))
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
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    schema, result_graph, version = extract_for_document("doc_raw")

    assert schema == DEFAULT_SCHEMA
    assert result_graph == graph
    assert version == 1
    assert len(fake_model.prompts) == 1  # single chunk group -> no merge needed
