"""Tests for app.ontology.refinement: applying a merge mapping (old type -> the type
that replaces it) to a schema and to the graph extracted under it. See CONTEXT.md
("Refinement", "Merge mapping", "Candidate schema")."""

import pytest

from app.graph import graphdb
from app.ontology import persistence
from app.ontology.refinement import (
    apply_mapping_to_graph,
    apply_mapping_to_schema,
    refine_version,
    validate_mapping,
)
from app.ontology.schema_validation import validate_schema

STEM = "doc_raw"


def _schema():
    return {
        "node_types": [
            {"name": "Provision", "description": "a clause"},
            {"name": "Norm", "description": "a rule"},
            {"name": "Party", "description": "a party", "properties": {"role": {"datatype": "string"}}},
            {"name": "Doctor", "description": "a physician", "properties": {"license": {"datatype": "string"}, "role": {"datatype": "int"}}},
            {"name": "Account", "description": "an account"},
        ],
        "edge_types": [
            {"name": "STATES_NORM", "description": "states", "source": "Provision", "target": "Norm"},
            {"name": "HAS_BEARER", "description": "bound", "source": "Norm", "target": "Party"},
            {"name": "WORKS_FOR", "description": "works for", "source": "Doctor", "target": "Party"},
            {"name": "ACTS_FOR", "description": "acts for", "source": "Party", "target": "Party"},
            {"name": "TARGETS_ACCOUNT", "description": "targets", "source": "Norm", "target": "Account"},
            {"name": "UNUSED", "description": "never used", "source": "Norm", "target": "Norm"},
        ],
    }


def _types(schema, kind="node_types"):
    return {t["name"]: t for t in schema[kind]}


# --- the mapping ---------------------------------------------------------------


def test_a_merged_node_type_disappears_and_its_target_keeps_the_union_of_properties():
    result = apply_mapping_to_schema(_schema(), {"node_types": {"Doctor": "Party"}})

    nodes = _types(result)
    assert "Doctor" not in nodes
    assert set(nodes["Party"]["properties"]) == {"role", "license"}
    assert nodes["Party"]["properties"]["role"]["datatype"] == "string"  # the target's own declaration wins
    assert "a party" in nodes["Party"]["description"] and "a physician" in nodes["Party"]["description"]


def test_edge_types_follow_their_merged_endpoints():
    result = apply_mapping_to_schema(_schema(), {"node_types": {"Doctor": "Party", "Account": "Norm"}})

    edges = _types(result, "edge_types")
    assert (edges["WORKS_FOR"]["source"], edges["WORKS_FOR"]["target"]) == ("Party", "Party")
    assert (edges["TARGETS_ACCOUNT"]["source"], edges["TARGETS_ACCOUNT"]["target"]) == ("Norm", "Norm")


def test_an_edge_type_mapped_to_none_is_dropped_and_one_mapped_to_another_is_merged_into_it():
    result = apply_mapping_to_schema(_schema(), {"node_types": {"Doctor": "Party"}, "edge_types": {"UNUSED": None, "WORKS_FOR": "ACTS_FOR"}})

    assert set(_types(result, "edge_types")) == {"STATES_NORM", "HAS_BEARER", "ACTS_FOR", "TARGETS_ACCOUNT"}


def test_the_candidate_schema_is_valid_and_the_original_is_left_alone():
    schema = _schema()
    result = apply_mapping_to_schema(schema, {"node_types": {"Doctor": "Party", "Account": "Norm"}, "edge_types": {"UNUSED": None}})

    assert validate_schema(result) == []
    assert "Doctor" in _types(schema) and "UNUSED" in _types(schema, "edge_types")


@pytest.mark.parametrize(
    "mapping, message",
    [
        ({"node_types": {"Nope": "Party"}}, "Nope"),                        # a type the schema does not have
        ({"node_types": {"Doctor": "Nope"}}, "Nope"),                       # a target it does not have
        ({"node_types": {"Doctor": "Account", "Account": "Party"}}, "chain"),  # a target that is itself merged away
        ({"node_types": {"Party": "Party"}}, "itself"),
        ({"edge_types": {"Nope": None}}, "Nope"),
        ({"edge_types": {"HAS_BEARER": "STATES_NORM"}}, "endpoints"),       # merged edge types must agree on endpoints
    ],
)
def test_a_mapping_that_cannot_be_applied_is_refused_with_the_reason(mapping, message):
    with pytest.raises(ValueError, match=message):
        validate_mapping(_schema(), mapping)


# --- the graph ------------------------------------------------------------------


def _graph():
    return {
        "nodes": [
            {"id": "n1", "label": "회사", "type": "Party"},
            {"id": "n2", "label": "회사", "type": "Doctor", "evidence_text": "회사는", "detail": "the insurer"},
            {"id": "n3", "label": "제1조", "type": "Provision"},
            {"id": "n4", "label": "의무", "type": "Norm"},
            {"id": "n5", "label": "연금계좌", "type": "Account"},
        ],
        "edges": [
            {"source": "n3", "target": "n4", "type": "STATES_NORM"},
            {"source": "n4", "target": "n1", "type": "HAS_BEARER"},
            {"source": "n4", "target": "n2", "type": "HAS_BEARER"},
            {"source": "n4", "target": "n5", "type": "TARGETS_ACCOUNT"},
            {"source": "n4", "target": "n4", "type": "UNUSED"},
        ],
    }


def test_nodes_take_their_new_type_and_those_that_become_the_same_node_are_one():
    result = apply_mapping_to_graph(_graph(), {"node_types": {"Doctor": "Party", "Account": "Norm"}})

    nodes = {(n["type"], n["label"]): n for n in result["nodes"]}
    assert set(nodes) == {("Party", "회사"), ("Provision", "제1조"), ("Norm", "의무"), ("Norm", "연금계좌")}
    assert nodes[("Party", "회사")]["evidence_text"] == "회사는"  # what the duplicate knew is kept
    assert nodes[("Party", "회사")]["detail"] == "the insurer"


def test_edges_are_rewritten_to_the_surviving_node_and_duplicates_collapse():
    result = apply_mapping_to_graph(_graph(), {"node_types": {"Doctor": "Party"}})

    bearers = [e for e in result["edges"] if e["type"] == "HAS_BEARER"]
    assert len(bearers) == 1  # n4->n1 and n4->n2 became the same edge
    ids = {n["id"] for n in result["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in result["edges"])


def test_edges_of_a_dropped_type_are_removed():
    result = apply_mapping_to_graph(_graph(), {"edge_types": {"UNUSED": None}})

    assert "UNUSED" not in {e["type"] for e in result["edges"]}
    assert len(result["edges"]) == 4


def test_the_original_graph_is_left_alone():
    graph = _graph()

    apply_mapping_to_graph(graph, {"node_types": {"Doctor": "Party"}})

    assert graph["nodes"][1]["type"] == "Doctor" and len(graph["nodes"]) == 5


# --- a new version --------------------------------------------------------------


def _store(version_schema=None, graph=None):
    version = persistence.create_schema_version(STEM, version_schema or _schema(), document_type="legal")
    persistence.save_graph(STEM, graph or _graph(), version=version)
    return version


def test_refining_a_version_creates_and_activates_a_new_one_and_leaves_the_old_untouched():
    source = _store()

    summary = refine_version(STEM, {"node_types": {"Doctor": "Party", "Account": "Norm"}, "edge_types": {"UNUSED": None}})

    new = summary["version"]
    assert new != source and persistence.get_active_version(STEM) == new
    assert "Doctor" not in _types(persistence.load_schema(STEM, new))
    assert "Doctor" in _types(persistence.load_schema(STEM, source))
    assert len(graphdb.load_graph(STEM, version=source)["nodes"]) == 5
    assert len(graphdb.load_graph(STEM, version=new)["nodes"]) == 4
    assert summary["from_version"] == source
    assert (summary["node_types"], summary["edge_types"]) == ({"before": 5, "after": 3}, {"before": 6, "after": 5})
    assert summary["nodes"] == {"before": 5, "after": 4}


def test_refining_a_version_that_has_no_graph_still_makes_the_candidate_schema():
    source = persistence.create_schema_version(STEM, _schema(), document_type="legal")

    summary = refine_version(STEM, {"node_types": {"Doctor": "Party"}}, version=source)

    assert summary["nodes"] is None
    assert "Doctor" not in _types(persistence.load_schema(STEM, summary["version"]))
