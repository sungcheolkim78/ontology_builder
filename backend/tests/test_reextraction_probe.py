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
        "answerable": True, "answer": quote, "answer_facts": [],
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
    monkeypatch.setattr("app.ontology.chunk_groups.EXTRACT_GROUP_CHARS", 25)
    model = LoggingSequencedChatModel(replies)
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: model)
    return model


def _report(**outcomes):
    return {"version": 1, "questions": [{"id": qid, "outcome": outcome} for qid, outcome in outcomes.items()]}


def test_a_missed_passage_that_a_second_extraction_cites_is_recovered(monkeypatch):
    _setup(monkeypatch, [_graph(_person("n1", "Alice", "Alice works at Acme."))])

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    assert [(q["id"], q["outcome"]) for q in result["questions"]] == [("q001", "recovered")]
    assert result["summary"] == {
        "probed": 1, "recovered": 1, "still_missed": 0, "not_locatable": 0, "semantic_recovered": 0,
        "answer_recovered": 1, "llm_calls": 1,
    }


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
    monkeypatch.setattr("app.ontology.chunk_groups.EXTRACT_GROUP_CHARS", 12_000)  # one group holds everything

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


def test_by_chunk_only_the_chunk_holding_the_passage_is_extracted_not_its_whole_group(monkeypatch):
    model = _setup(monkeypatch, [_graph(_person("n1", "Bob", "Bob works at Initech."))])
    monkeypatch.setattr("app.ontology.chunk_groups.EXTRACT_GROUP_CHARS", 12_000)  # one group holds all three chunks

    result = probe_extraction_misses(STEM, _report(q002="extraction_miss"), granularity="chunk")

    assert len(model.calls) == 1
    sent = str(model.calls[0])
    assert "Bob works at Initech." in sent and "Alice works at Acme." not in sent
    assert [(q["id"], q["outcome"]) for q in result["questions"]] == [("q002", "recovered")]
    assert result["granularity"] == "chunk"


def test_a_node_that_says_the_passage_without_quoting_it_is_a_semantic_recovery(monkeypatch):
    class Embeddings:
        def embed_documents(self, texts):
            vectors = {"Alice works at Acme.": [1.0, 0.0], "Alice: works at Acme": [0.9, 0.1]}
            return [vectors.get(text, [0.0, 1.0]) for text in texts]

    _setup(monkeypatch, [json.dumps({"nodes": [{"id": "n1", "label": "Alice", "type": "Person", "detail": "works at Acme"}], "edges": []})])
    monkeypatch.setattr("app.llm.calls.get_embedding_model", lambda: Embeddings())

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    assert result["questions"][0]["outcome"] == "still_missed"  # no quoted evidence
    assert result["questions"][0]["semantic"] is True
    assert result["summary"]["semantic_recovered"] == 1


def test_extra_instructions_reach_the_extraction_prompt_of_every_unit(monkeypatch):
    model = _setup(monkeypatch, [_graph(_person("n1", "Alice", "Alice works at Acme."))])

    probe_extraction_misses(STEM, _report(q001="extraction_miss"), extra_instructions="LIST EVERY ITEM OF A CLAUSE")

    assert "LIST EVERY ITEM OF A CLAUSE" in str(model.calls[0])


def test_a_new_node_that_states_the_facts_of_the_answer_is_an_answer_recovery(monkeypatch):
    _setup(monkeypatch, [json.dumps({"nodes": [{"id": "n1", "label": "Alice", "type": "Person", "detail": "works at Acme in Seoul"}], "edges": []})])
    golden = {
        "questions": [
            {**_question("q001", "Alice works at Acme.", 2), "answer_facts": [{"subject": "Alice", "predicate": "employer", "object": "works at Acme in Seoul"}]}
        ],
        "warnings": [],
    }
    save_goldenset(STEM, golden)

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    assert result["questions"][0]["answer"] is True
    assert result["summary"]["answer_recovered"] == 1


def test_the_probe_returns_what_it_extracted_so_a_scoring_rule_can_be_changed_without_new_calls(monkeypatch):
    _setup(monkeypatch, [_graph(_person("n1", "Alice", "Alice works at Acme."))])

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"))

    extracted = result["extracted"]
    assert list(extracted.values())[0]["nodes"][0]["label"] == "Alice"
    assert result["questions"][0]["groups"] == [int(unit) for unit in extracted]


def test_a_second_pass_asks_the_model_again_for_each_unit_and_keeps_both_passes_nodes(monkeypatch):
    first = _graph(_person("n1", "Alice"))
    second = json.dumps({"nodes": [{"id": "x", "label": "Alice employer", "type": "Person", "detail": "works at Acme in Seoul"}], "edges": []})
    model = _setup(monkeypatch, [first, second])
    golden = {
        "questions": [{**_question("q001", "Alice works at Acme.", 2), "answer_facts": [{"subject": "Alice", "predicate": "employer", "object": "works at Acme in Seoul"}]}],
        "warnings": [],
    }
    save_goldenset(STEM, golden)

    result = probe_extraction_misses(STEM, _report(q001="extraction_miss"), second_pass=True)

    assert len(model.calls) == 2
    assert result["questions"][0]["answer"] is True
    assert result["summary"]["llm_calls"] == 2
    labels = {n["label"] for g in result["extracted"].values() for n in g["nodes"]}
    assert labels == {"Alice", "Alice employer"}
