"""Tests for app.ontology.reextraction_probe: re-extracting only the chunk
groups that hold a missed golden passage, under the schema that was in force,
to tell an extraction that was merely sparse from a schema that cannot hold
the fact."""

import json

from app.ontology import persistence
from app.ontology.reextraction_probe import probe_extraction_misses
from app.preprocess.goldenset import save_goldenset
from app.utils.paths import chunk_path_for, raw_path_for
from app.utils.store import write_json, write_text
from fakes import LoggingSequencedChatModel

STEM = "doc_raw"
RAW = "# Title\nAlice works at Acme.\nBob works at Initech.\n"
SCHEMA = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}


def _question(qid, quote, line):
    return {
        "id": qid, "question": f"question {qid}", "question_type": "attribute",
        "answerable": True, "answer": "a", "answer_facts": [],
        "evidence": [{"quote": quote, "line_start": line, "line_end": line}],
    }


def _graph(*nodes):
    return json.dumps({"nodes": list(nodes), "edges": []})


def _person(node_id, label, evidence=None):
    node = {"id": node_id, "label": label, "type": "Person"}
    if evidence:
        node["evidence"] = evidence  # the LLM's field; extraction verifies it into evidence_text
    return node


def _setup(monkeypatch, replies):
    """A document of three chunk groups (title, Alice, Bob -- the group budget is
    set below two chunks' length), a golden set asking about both people, and a
    chat model that answers each extraction call with the next of `replies`."""
    write_text(raw_path_for(STEM), RAW)
    write_json(
        chunk_path_for(STEM),
        {
            "preamble": {"path": "preamble", "text": "# Title"},
            "chunks": [
                {"path": "alice", "text": "Alice works at Acme."},
                {"path": "bob", "text": "Bob works at Initech."},
            ],
        },
    )
    save_goldenset(
        STEM,
        {
            "questions": [
                _question("q001", "Alice works at Acme.", 2),
                _question("q002", "Bob works at Initech.", 3),
            ],
            "warnings": [],
        },
    )
    persistence.create_schema_version(STEM, SCHEMA)
    monkeypatch.setattr("app.ontology.chunk_groups.MAX_CHUNK_GROUP_CHARS", 25)
    model = LoggingSequencedChatModel(replies)
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
    return model


def _report(**outcomes):
    return {"version": 1, "questions": [{"id": qid, "outcome": outcome} for qid, outcome in outcomes.items()]}


def test_a_missed_passage_that_a_second_extraction_cites_is_recovered(monkeypatch):
    _setup(monkeypatch, [_graph(_person("n1", "Alice", "Alice works at Acme."))])

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    assert [(q["id"], q["outcome"]) for q in result["questions"]] == [("q001", "recovered")]
    assert result["summary"] == {"probed": 1, "recovered": 1, "still_missed": 0, "not_locatable": 0, "llm_calls": 1}


def test_a_missed_passage_no_node_of_the_second_extraction_cites_is_still_missed(monkeypatch):
    _setup(monkeypatch, [_graph(_person("n1", "Alice"))])  # the node exists, with no evidence

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    assert [(q["id"], q["outcome"]) for q in result["questions"]] == [("q001", "still_missed")]


def test_only_the_chunk_groups_holding_a_missed_passage_are_extracted(monkeypatch):
    model = _setup(monkeypatch, [_graph(_person("n1", "Bob", "Bob works at Initech."))])

    result = probe_extraction_misses(STEM, _report(q001="hit", q002="extraction_miss"))

    assert len(model.calls) == 1
    assert "Bob works at Initech." in str(model.calls[0]) and "Alice works at Acme." not in str(model.calls[0])
    assert [q["id"] for q in result["questions"]] == ["q002"]


def test_two_missed_passages_in_one_group_cost_one_extraction(monkeypatch):
    model = _setup(monkeypatch, [_graph(_person("n1", "Alice", "Alice works at Acme."))])
    monkeypatch.setattr("app.ontology.chunk_groups.MAX_CHUNK_GROUP_CHARS", 12_000)  # one group holds everything

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss", q002="extraction_miss"))

    assert len(model.calls) == 1
    assert [(q["id"], q["outcome"]) for q in result["questions"]] == [("q001", "recovered"), ("q002", "still_missed")]
    assert result["summary"]["llm_calls"] == 1


def test_a_passage_that_is_in_no_chunk_cannot_be_probed(monkeypatch):
    model = _setup(monkeypatch, [])
    write_json(
        chunk_path_for(STEM),
        {"preamble": {"path": "preamble", "text": "# Title"}, "chunks": [{"path": "bob", "text": "Bob works at Initech."}]},
    )

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    assert [(q["id"], q["outcome"]) for q in result["questions"]] == [("q001", "not_locatable")]
    assert model.calls == []
