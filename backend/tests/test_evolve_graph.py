import json
import os
import shutil

import pytest

from app.graph import graphdb
from app.ontology import create_schema_version, get_active_version, load_schema
from app.ontology.evolve_graph import (
    apply_evolution,
    converge_domain_schema,
    evaluate_domain_schema,
    propose_evolution,
    validate_ontology,
)
from app.preprocess.parser import DATA_DIR


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class SequencedChatModel:
    """Returns each response in order, one per invoke() call -- needed
    because converge_domain_schema makes multiple sequential LLM calls
    (extract/validate/propose_evolution, per document) within one function
    call, unlike the single-call tests above that get away with a fixed
    FakeChatModel response."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        content = self.responses[self.calls]
        self.calls += 1
        return type("FakeResponse", (), {"content": content})()


@pytest.fixture(autouse=True)
def clean_dirs():
    graphdb.reset_connection()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    if graphdb.DB_PATH.exists():
        if graphdb.DB_PATH.is_file():
            os.remove(graphdb.DB_PATH)
        else:
            shutil.rmtree(graphdb.DB_PATH)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    graphdb.reset_connection()
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    if graphdb.DB_PATH.exists():
        if graphdb.DB_PATH.is_file():
            os.remove(graphdb.DB_PATH)
        else:
            shutil.rmtree(graphdb.DB_PATH)


def _seed_schema_and_graph(stem="doc_raw"):
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    version = create_schema_version(stem, schema)
    graphdb.write_graph(stem, [{"id": "n1", "label": "Alice", "type": "Person"}], [], version=version)
    return schema


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


def _iteration(
    stem, doc_chars, node_type_counts, edge_type_counts=None,
    issue_count=0, missing_element_count=0, competency_questions=None,
):
    return {
        "stem": stem,
        "changes_applied": [],
        "changes_pending_review": [],
        "validation_summary": {},
        "issue_count": issue_count,
        "doc_chars": doc_chars,
        "missing_element_count": missing_element_count,
        "node_type_counts": node_type_counts,
        "edge_type_counts": edge_type_counts or {},
        "competency_questions": competency_questions or [],
    }


# --- validate_ontology --------------------------------------------------


def test_validate_ontology_returns_report_from_llm_json(monkeypatch):
    report = _minimal_validation_report(issue_count=2)
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(report)))

    result = validate_ontology("some document text", {"node_types": [], "edge_types": []}, {"nodes": [], "edges": []})

    assert result == report


def test_validate_ontology_raises_when_summary_or_issues_missing(monkeypatch):
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps({"issues": []})))

    with pytest.raises(ValueError):
        validate_ontology("doc", {"node_types": [], "edge_types": []}, {"nodes": [], "edges": []})


# --- propose_evolution ----------------------------------------------------


def test_propose_evolution_returns_proposal_from_llm_json(monkeypatch):
    proposal = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
            }
        ]
    }
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(proposal)))

    result = propose_evolution(
        "some document text",
        {"node_types": [], "edge_types": []},
        {"nodes": [], "edges": []},
        _minimal_validation_report(),
    )

    assert result == proposal


def test_propose_evolution_raises_when_changes_list_missing(monkeypatch):
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps({})))

    with pytest.raises(ValueError):
        propose_evolution(
            "doc", {"node_types": [], "edge_types": []}, {"nodes": [], "edges": []}, _minimal_validation_report()
        )


def test_propose_evolution_raises_when_change_missing_required_keys(monkeypatch):
    proposal = {"changes": [{"decision": "ADD"}]}  # missing element_type/element
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: FakeChatModel(json.dumps(proposal)))

    with pytest.raises(ValueError):
        propose_evolution(
            "doc", {"node_types": [], "edge_types": []}, {"nodes": [], "edges": []}, _minimal_validation_report()
        )


# --- apply_evolution --------------------------------------------------------


def test_apply_evolution_raises_when_no_active_version():
    with pytest.raises(ValueError):
        apply_evolution("doc_raw", [])


def test_apply_evolution_adds_node_type_and_node_creates_new_version():
    _seed_schema_and_graph()

    changes = [
        {
            "change_id": "c1",
            "decision": "ADD",
            "element_type": "node_type",
            "element": {"name": "Organization", "description": "an org"},
        },
        {
            "change_id": "c2",
            "decision": "ADD",
            "element_type": "node",
            "element": {"id": "n2", "label": "Acme", "type": "Organization", "detail": ""},
        },
    ]

    result = apply_evolution("doc_raw", changes)

    assert result["version"] == 2
    assert get_active_version("doc_raw") == 2
    schema_v2 = load_schema("doc_raw", 2)
    assert {"name": "Organization", "description": "an org"} in schema_v2["node_types"]
    assert result["node_count"] == 2  # original Alice node preserved + new one

    graph_v2 = graphdb.load_graph("doc_raw", version=2)
    ids = {n["id"] for n in graph_v2["nodes"]}
    assert ids == {"n1", "n2"}


def test_apply_evolution_deprecates_node_type_without_removing_it():
    _seed_schema_and_graph()

    result = apply_evolution(
        "doc_raw",
        [
            {
                "change_id": "c1",
                "decision": "DEPRECATE",
                "element_type": "node_type",
                "element": {"name": "Person", "description": "a person"},
            }
        ],
    )

    schema_v2 = load_schema("doc_raw", result["version"])
    assert schema_v2["node_types"][0]["description"].startswith("[DEPRECATED]")


def test_apply_evolution_deprecates_edge_removes_matching_edges():
    schema = {
        "node_types": [{"name": "Person", "description": "a person"}],
        "edge_types": [{"name": "KNOWS", "description": "d", "source": "Person", "target": "Person"}],
    }
    version = create_schema_version("doc_raw", schema)
    graphdb.write_graph(
        "doc_raw",
        [{"id": "n1", "label": "Alice", "type": "Person"}, {"id": "n2", "label": "Bob", "type": "Person"}],
        [{"source": "n1", "target": "n2", "type": "KNOWS"}],
        version=version,
    )

    result = apply_evolution(
        "doc_raw",
        [
            {
                "change_id": "c1",
                "decision": "DEPRECATE",
                "element_type": "edge",
                "element": {"source": "n1", "target": "n2", "type": "KNOWS"},
            }
        ],
    )

    graph_v2 = graphdb.load_graph("doc_raw", version=result["version"])
    assert graph_v2["edges"] == []


# --- converge_domain_schema / evaluate_domain_schema -------------------------


def test_converge_domain_schema_applies_auto_decisions_and_queues_review(monkeypatch):
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    extract_response = {"nodes": [{"id": "n1", "label": "Bob", "type": "Person"}], "edges": []}
    validate_response = _minimal_validation_report(issue_count=1)
    propose_response = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
                "reason": "missing from schema",
                "evidence": "Acme",
                "confidence": "HIGH",
            },
            {
                "change_id": "c2",
                "decision": "NEEDS_HUMAN_REVIEW",
                "element_type": "edge_type",
                "element": {
                    "name": "WORKS_AT", "description": "works at", "source": "Person", "target": "Organization",
                },
                "reason": "ambiguous",
                "evidence": "Bob works at Acme",
                "confidence": "LOW",
            },
            {
                "change_id": "c3",
                "decision": "ADD",
                "element_type": "node",
                "element": {"id": "n2", "label": "Acme", "type": "Organization"},
                "reason": "instance-level, should be ignored by schema convergence",
                "evidence": "Acme",
                "confidence": "HIGH",
            },
        ]
    }
    fake_model = SequencedChatModel(
        [json.dumps(extract_response), json.dumps(validate_response), json.dumps(propose_response)]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = converge_domain_schema([{"stem": "doc2_raw", "text": "Bob works at Acme."}], seed_schema)

    node_type_names = {t["name"] for t in result["schema"]["node_types"]}
    assert node_type_names == {"Person", "Organization"}
    assert result["schema"]["edge_types"] == []  # NEEDS_HUMAN_REVIEW change not applied
    assert len(result["iterations"]) == 1
    assert result["iterations"][0]["stem"] == "doc2_raw"
    assert result["iterations"][0]["issue_count"] == 1
    assert [c["change_id"] for c in result["iterations"][0]["changes_applied"]] == ["c1"]
    assert [c["change_id"] for c in result["pending_review"]] == ["c2"]
    assert result["pending_review"][0]["stem"] == "doc2_raw"


def test_converge_domain_schema_folds_multiple_documents_in_order(monkeypatch):
    seed_schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    empty_graph = {"nodes": [], "edges": []}
    validate_response = _minimal_validation_report()
    add_org = {
        "changes": [
            {
                "change_id": "c1",
                "decision": "ADD",
                "element_type": "node_type",
                "element": {"name": "Organization", "description": "an org"},
                "reason": "r", "evidence": "e", "confidence": "HIGH",
            }
        ]
    }
    add_edge = {
        "changes": [
            {
                "change_id": "c2",
                "decision": "MERGE",
                "element_type": "edge_type",
                "element": {
                    "name": "WORKS_AT", "description": "works at", "source": "Person", "target": "Organization",
                },
                "reason": "r", "evidence": "e", "confidence": "HIGH",
            }
        ]
    }
    fake_model = SequencedChatModel(
        [
            json.dumps(empty_graph), json.dumps(validate_response), json.dumps(add_org),
            json.dumps(empty_graph), json.dumps(validate_response), json.dumps(add_edge),
        ]
    )
    monkeypatch.setattr("app.ontology.get_chat_model", lambda operation=None: fake_model)

    result = converge_domain_schema(
        [{"stem": "doc2_raw", "text": "doc2"}, {"stem": "doc3_raw", "text": "doc3"}], seed_schema
    )

    assert {t["name"] for t in result["schema"]["node_types"]} == {"Person", "Organization"}
    assert {t["name"] for t in result["schema"]["edge_types"]} == {"WORKS_AT"}
    assert [it["stem"] for it in result["iterations"]] == ["doc2_raw", "doc3_raw"]
    assert result["pending_review"] == []


def test_evaluate_domain_schema_computes_coverage_and_utilization():
    schema = {
        "node_types": [
            {"name": "Person", "description": "a person"},
            {"name": "Organization", "description": "an org"},
        ],
        "edge_types": [{"name": "WORKS_AT", "description": "works at"}],
    }
    iterations = [
        _iteration("doc1", 1000, {"Person": 2}, {}, issue_count=1, missing_element_count=2),
        _iteration("doc2", 1000, {"Person": 1, "Organization": 1}, {"WORKS_AT": 1}, issue_count=3),
    ]

    result = evaluate_domain_schema(schema, iterations)

    assert result["coverage"] == {"avg_issue_count": 2.0, "avg_missing_element_count": 1.0}
    assert result["type_utilization"] == {"Person": 1.0, "Organization": 0.5, "WORKS_AT": 0.5}
    assert result["qa_success_rate"] is None


def test_evaluate_domain_schema_consistency_is_zero_for_identical_density():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    iterations = [
        _iteration("doc1", 1000, {"Person": 1}),
        _iteration("doc2", 2000, {"Person": 2}),  # same density: 1 per 1000 chars
    ]

    result = evaluate_domain_schema(schema, iterations)

    assert result["consistency"]["Person"] == 0.0


def test_evaluate_domain_schema_qa_success_rate_from_competency_questions():
    schema = {"node_types": [], "edge_types": []}
    iterations = [
        _iteration(
            "doc1", 1000, {},
            competency_questions=[{"question": "q1", "answerable": True}, {"question": "q2", "answerable": False}],
        ),
        _iteration("doc2", 1000, {}, competency_questions=[{"question": "q3", "answerable": True}]),
    ]

    result = evaluate_domain_schema(schema, iterations)

    assert result["qa_success_rate"] == pytest.approx(2 / 3)


def test_evaluate_domain_schema_handles_empty_iterations():
    result = evaluate_domain_schema({"node_types": [], "edge_types": []}, [])

    assert result["type_utilization"] == {}
    assert result["qa_success_rate"] is None
