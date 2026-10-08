"""Tests for app.ontology.retrieval_eval: scoring a document's golden set
against its extracted graph, through the same search the chatbot uses."""

import json

import pytest

from app.graph import graphdb
from app.ontology import persistence
from app.ontology.retrieval_eval import evidence_spans, score_golden_set, spans_overlap
from app.preprocess.goldenset import save_goldenset
from app.utils.paths import raw_path_for
from app.utils.store import write_text
from fakes import FakeChatModel

# 8 + 21 + 22 + 21 characters: line 1 "# Title\n", 2 "Alice works at Acme.\n",
# 3 "Bob works at Initech.\n", 4 "Alice works at Acme.\n"
RAW = "# Title\nAlice works at Acme.\nBob works at Initech.\nAlice works at Acme.\n"


def test_evidence_span_is_the_quote_inside_its_own_line_range():
    spans = evidence_spans(RAW, [{"quote": "Alice works at Acme.", "line_start": 2, "line_end": 2}])

    assert spans == [(8, 28)]
    assert RAW[8:28] == "Alice works at Acme."


def test_a_repeated_quote_is_found_at_the_line_the_golden_set_recorded_not_the_first_one():
    spans = evidence_spans(RAW, [{"quote": "Alice works at Acme.", "line_start": 4, "line_end": 4}])

    assert spans == [(51, 71)]
    assert RAW[51:71] == "Alice works at Acme."


def test_a_quote_spanning_lines_gets_one_span_over_all_of_them():
    quote = "Bob works at Initech.\nAlice works at Acme."

    spans = evidence_spans(RAW, [{"quote": quote, "line_start": 3, "line_end": 4}])

    assert spans == [(29, 71)]
    assert RAW[29:71] == quote


def test_a_quote_not_in_its_line_range_falls_back_to_its_first_occurrence_in_the_document():
    spans = evidence_spans(RAW, [{"quote": "Bob works at Initech.", "line_start": 1, "line_end": 1}])

    assert spans == [(29, 50)]


def test_a_quote_nowhere_in_the_document_has_no_span():
    assert evidence_spans(RAW, [{"quote": "Carol works at Globex.", "line_start": 2, "line_end": 2}]) == []


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ((10, 30), (10, 30), True),   # the same passage
        ((10, 30), (12, 18), True),   # a shorter quote inside a longer one
        ((12, 18), (10, 30), True),   # ... in either order
        ((10, 30), (20, 40), True),   # exactly half of each (10 of 20) overlaps
        ((10, 30), (21, 41), False),  # 9 of 20 is under half
        ((10, 30), (30, 50), False),  # touching, not overlapping
        ((10, 30), (40, 60), False),
        ((10, 50), (45, 55), True),   # 5 of the shorter span's 10: half, though the longer span barely overlaps
    ],
)
def test_two_spans_overlap_when_half_of_the_shorter_one_is_shared(a, b, expected):
    assert spans_overlap(a, b) is expected


# --- score_golden_set: each golden question against the extracted graph ------

STEM = "doc_raw"
SCHEMA = {
    "node_types": [{"name": "Person", "description": "a person"}, {"name": "Org", "description": "an org"}],
    "edge_types": [],
}


def _question(qid, quote, line, question_type="attribute", answerable=True):
    evidence = [{"quote": quote, "line_start": line, "line_end": line}] if answerable else []
    return {
        "id": qid, "question": f"question {qid}", "question_type": question_type,
        "answerable": answerable, "answer": "a" if answerable else None,
        "answer_facts": [], "evidence": evidence,
    }


def _node(node_id, label, node_type="Person", evidence=None, **extra):
    node = {"id": node_id, "label": label, "type": node_type, **extra}
    if evidence is not None:
        node["evidence_text"] = evidence
    return node


def _setup(monkeypatch, questions, nodes, search_returns=None):
    """A document, its golden set, an extracted graph under its active schema
    version, and a chat model whose question analysis asks for `search_returns`
    (default: Person nodes, keyword "Alice")."""
    write_text(raw_path_for(STEM), RAW)
    save_goldenset(STEM, {"questions": questions, "warnings": []})
    version = persistence.create_schema_version(STEM, SCHEMA)
    graphdb.write_graph(STEM, nodes, [], version=version)
    analysis = search_returns or {"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Alice"]}}
    monkeypatch.setattr("app.llm.calls.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(analysis)))
    return version


def test_a_question_whose_retrieved_node_cites_the_answering_passage_is_a_hit(monkeypatch):
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 2)],
        [_node("n1", "Alice", evidence="Alice works at Acme."), _node("n2", "Bob", evidence="Bob works at Initech.")],
    )

    report = score_golden_set(STEM, hops=0)

    question = report["questions"][0]
    assert (question["id"], question["outcome"], question["recall"]) == ("q001", "hit", 1.0)


def test_a_passage_the_graph_cites_but_the_search_did_not_return_is_a_retrieval_miss(monkeypatch):
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 2)],
        [_node("n1", "Alice", evidence="Alice works at Acme."), _node("n2", "Bob", evidence="Bob works at Initech.")],
        search_returns={"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Bob"]}},
    )

    question = score_golden_set(STEM, hops=0)["questions"][0]

    assert (question["outcome"], question["recall"]) == ("retrieval_miss", 0.0)


def test_a_passage_no_node_of_the_graph_cites_is_an_extraction_miss(monkeypatch):
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 2)],
        [_node("n1", "Alice"), _node("n2", "Bob", evidence="Bob works at Initech.")],  # Alice's node has no evidence
        search_returns={"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Alice"]}},
    )

    question = score_golden_set(STEM, hops=0)["questions"][0]

    assert (question["outcome"], question["recall"]) == ("extraction_miss", 0.0)


def _list_question(*quotes_and_lines):
    return {
        "id": "q001", "question": "list question", "question_type": "list", "answerable": True,
        "answer": "a", "answer_facts": [],
        "evidence": [{"quote": q, "line_start": line, "line_end": line} for q, line in quotes_and_lines],
    }


def test_half_of_a_questions_passages_cited_is_still_a_hit_with_a_recall_of_one_half(monkeypatch):
    _setup(
        monkeypatch,
        [_list_question(("Alice works at Acme.", 2), ("Bob works at Initech.", 3))],
        [_node("n1", "Alice", evidence="Alice works at Acme."), _node("n2", "Bob", evidence="Bob works at Initech.")],
        search_returns={"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Alice"]}},
    )

    question = score_golden_set(STEM, hops=0)["questions"][0]

    assert (question["outcome"], question["recall"]) == ("hit", 0.5)


def test_under_half_of_a_questions_passages_cited_is_a_miss_even_though_one_was_found(monkeypatch):
    # Alice's passage appears on lines 2 and 4; her node quotes it, which resolves to the
    # first occurrence, so of the three answering passages only one is cited.
    _setup(
        monkeypatch,
        [_list_question(("Alice works at Acme.", 2), ("Bob works at Initech.", 3), ("Alice works at Acme.", 4))],
        [_node("n1", "Alice", evidence="Alice works at Acme."), _node("n2", "Bob", evidence="Bob works at Initech.")],
        search_returns={"node_types": ["Person"], "edge_types": [], "keywords": {"Person": ["Alice"]}},
    )

    question = score_golden_set(STEM, hops=0)["questions"][0]

    assert question["outcome"] == "retrieval_miss"
    assert question["recall"] == pytest.approx(1 / 3)


def test_an_unanswerable_question_is_listed_but_is_not_scored(monkeypatch):
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 2), _question("q002", "", 0, answerable=False)],
        [_node("n1", "Alice", evidence="Alice works at Acme.")],
    )

    questions = score_golden_set(STEM, hops=0)["questions"]

    assert [(q["id"], q["outcome"]) for q in questions] == [("q001", "hit"), ("q002", "unanswerable")]


def test_a_question_whose_evidence_is_no_longer_in_the_document_is_unscorable_not_a_miss(monkeypatch):
    # e.g. the document was re-converted after the golden set was made
    _setup(
        monkeypatch,
        [_question("q001", "Carol works at Globex.", 2)],
        [_node("n1", "Alice", evidence="Alice works at Acme.")],
    )

    question = score_golden_set(STEM, hops=0)["questions"][0]

    assert question["outcome"] == "unscorable"


def test_the_summary_counts_outcomes_and_rates_hits_over_scorable_questions_only(monkeypatch):
    _setup(
        monkeypatch,
        [
            _question("q001", "Alice works at Acme.", 2, question_type="attribute"),
            _question("q002", "Bob works at Initech.", 3, question_type="attribute"),
            _question("q003", "Alice works at Acme.", 2, question_type="relation"),
            _question("q004", "", 0, answerable=False),
            _question("q005", "Carol works at Globex.", 2),
        ],
        [_node("n1", "Alice", evidence="Alice works at Acme."), _node("n2", "Bob")],  # Bob's node cites nothing
    )

    summary = score_golden_set(STEM, hops=0)["summary"]

    assert summary["outcomes"] == {
        "hit": 2, "retrieval_miss": 0, "extraction_miss": 1, "unanswerable": 1, "unscorable": 1,
    }
    assert summary["scored"] == 3
    assert summary["hit_rate"] == pytest.approx(2 / 3)
    assert summary["mean_recall"] == pytest.approx(2 / 3)
    assert summary["by_question_type"] == {
        "attribute": {"scored": 2, "hits": 1},
        "relation": {"scored": 1, "hits": 1},
    }


def test_the_summary_has_no_hit_rate_when_nothing_could_be_scored(monkeypatch):
    _setup(monkeypatch, [_question("q001", "", 0, answerable=False)], [_node("n1", "Alice")])

    summary = score_golden_set(STEM, hops=0)["summary"]

    assert summary["scored"] == 0
    assert summary["hit_rate"] is None and summary["mean_recall"] is None


def test_the_report_says_how_much_of_the_graph_cites_a_passage_at_all(monkeypatch):
    # An extraction miss means little if almost no node cites anything, so the
    # share of nodes and edges that carry evidence is part of the report.
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 2)],
        [_node("n1", "Alice", evidence="Alice works at Acme."), _node("n2", "Bob"), _node("n3", "Carol")],
    )

    graph = score_golden_set(STEM, hops=0)["summary"]["graph"]

    assert graph == {"nodes": 3, "nodes_with_evidence": 1, "edges": 0, "edges_with_evidence": 0}


def test_the_report_counts_answering_passages_whose_quote_appears_more_than_once(monkeypatch):
    # A node quoting a sentence that occurs twice is anchored to the first occurrence,
    # so a golden passage at a later occurrence can never be matched by it.
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 4), _question("q002", "Bob works at Initech.", 3)],
        [_node("n1", "Alice", evidence="Alice works at Acme.")],
    )

    summary = score_golden_set(STEM, hops=0)["summary"]

    assert summary["repeated_passages"] == 1  # Alice's sentence is on lines 2 and 4


def test_a_nodes_stored_offsets_are_ignored_because_they_may_be_in_the_wrong_frame(monkeypatch):
    # A graph extracted from a chunked document before offsets were anchored to raw.md
    # holds offsets into the labelled chunk-group text. The quote itself is what is trusted.
    _setup(
        monkeypatch,
        [_question("q001", "Alice works at Acme.", 2)],
        [_node("n1", "Alice", evidence="Alice works at Acme.", start_offset=900, end_offset=920)],
    )

    question = score_golden_set(STEM, hops=0)["questions"][0]

    assert question["outcome"] == "hit"
