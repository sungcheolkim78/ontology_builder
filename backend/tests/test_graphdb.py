import pytest

from app import graphdb


@pytest.fixture(autouse=True)
def clean_graphdb():
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil
        if graphdb.DB_PATH.is_dir():
            shutil.rmtree(graphdb.DB_PATH)
        else:
            graphdb.DB_PATH.unlink()
    yield
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil
        if graphdb.DB_PATH.is_dir():
            shutil.rmtree(graphdb.DB_PATH)
        else:
            graphdb.DB_PATH.unlink()


def test_has_graph_is_false_for_unknown_stem():
    assert graphdb.has_graph("nonexistent_stem") is False


def test_validate_identifier_accepts_safe_names():
    assert graphdb._validate_identifier("Person") == "Person"
    assert graphdb._validate_identifier("WORKED_ON") == "WORKED_ON"
    assert graphdb._validate_identifier("_leading_underscore") == "_leading_underscore"


def test_validate_identifier_rejects_unsafe_names():
    for bad in ["Person; DROP TABLE Person", "has space", "has-dash", "1StartsWithDigit", "", "has`tick", "Person\n"]:
        with pytest.raises(ValueError):
            graphdb._validate_identifier(bad)


NODES = [
    {"id": "n1", "label": "Ada Lovelace", "type": "Person", "detail": "Mathematician"},
    {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
]
EDGES = [
    {"source": "n1", "target": "n2", "type": "WORKED_ON", "detail": "From 1842"},
]


def test_write_and_load_graph_round_trips():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.has_graph("doc_a") is True
    loaded = graphdb.load_graph("doc_a")

    assert loaded is not None
    assert sorted(loaded["nodes"], key=lambda n: n["id"]) == sorted(NODES, key=lambda n: n["id"])
    assert loaded["edges"] == EDGES


def test_load_graph_returns_none_for_unextracted_document():
    assert graphdb.load_graph("never_extracted") is None


def test_write_graph_scopes_by_document():
    graphdb.write_graph("doc_a", NODES, EDGES)
    graphdb.write_graph(
        "doc_b",
        [{"id": "n1", "label": "Charles Babbage", "type": "Person"}],
        [],
    )

    loaded_a = graphdb.load_graph("doc_a")
    loaded_b = graphdb.load_graph("doc_b")

    assert [n["label"] for n in loaded_a["nodes"]] == sorted(
        [n["label"] for n in loaded_a["nodes"]]
    )  # sanity: just doc_a's own data
    assert {n["label"] for n in loaded_a["nodes"]} == {"Ada Lovelace", "Analytical Engine"}
    assert {n["label"] for n in loaded_b["nodes"]} == {"Charles Babbage"}


def test_write_graph_re_extraction_replaces_previous_data():
    graphdb.write_graph("doc_a", NODES, EDGES)

    new_nodes = [{"id": "m1", "label": "Grace Hopper", "type": "Person"}]
    graphdb.write_graph("doc_a", new_nodes, [])

    loaded = graphdb.load_graph("doc_a")
    assert loaded["nodes"] == new_nodes
    assert loaded["edges"] == []


def test_write_graph_extends_edge_table_with_new_type_pair():
    # First document: WORKED_ON goes Person -> Concept.
    graphdb.write_graph("doc_a", NODES, EDGES)

    # Second document: same edge type name, different (source, target) pair --
    # must ALTER TABLE ADD FROM/TO rather than fail.
    nodes_b = [
        {"id": "p1", "label": "Person Two", "type": "Person"},
        {"id": "o1", "label": "Org One", "type": "Organization"},
    ]
    edges_b = [{"source": "p1", "target": "o1", "type": "WORKED_ON"}]

    graphdb.write_graph("doc_b", nodes_b, edges_b)

    loaded_b = graphdb.load_graph("doc_b")
    assert loaded_b["edges"] == edges_b


def test_write_graph_rejects_unsafe_type_name():
    bad_nodes = [{"id": "n1", "label": "X", "type": "Person; DROP TABLE Person"}]
    with pytest.raises(ValueError):
        graphdb.write_graph("doc_bad", bad_nodes, [])


def test_write_graph_edge_row_order_is_deterministic_across_edge_types():
    # Regression test for a determinism bug found while extending the
    # node_types ordering fix to edge_specs: with 2+ distinct edge types,
    # load_graph's `MATCH (a)-[r]->(b) ...` scan (an untyped pattern across
    # heterogeneous REL tables) was observed to return rows in an order
    # that varies from run to run -- even with a fixed PYTHONHASHSEED and
    # identical internal catalog table IDs across runs, ruling out
    # Python-level set/dict ordering (already fixed for both node_types and
    # edge_specs) as the cause. The actual fix is the `ORDER BY` added to
    # load_graph's queries; this test guards that regression.
    nodes = [
        {"id": "p1", "label": "Ada Lovelace", "type": "Person"},
        {"id": "c1", "label": "Analytical Engine", "type": "Concept"},
        {"id": "o1", "label": "Royal Society", "type": "Organization"},
    ]
    edges = [
        {"source": "p1", "target": "c1", "type": "WORKED_ON"},
        {"source": "p1", "target": "o1", "type": "MEMBER_OF"},
    ]

    graphdb.write_graph("doc_multi_edge_type", nodes, edges)

    loaded = graphdb.load_graph("doc_multi_edge_type")
    # ORDER BY r.type, a.id, b.id in load_graph -> alphabetical by type.
    assert [e["type"] for e in loaded["edges"]] == ["MEMBER_OF", "WORKED_ON"]


def test_load_graph_handles_document_with_no_edges_on_fresh_database():
    # Regression test: when this is the very first write_graph call ever
    # against a fresh database (no REL table has been created yet at all),
    # load_graph's edge query used to raise
    # `RuntimeError: Binder exception: Cannot find property source_document
    # for r.` instead of returning edges: []. The `clean_graphdb` autouse
    # fixture wipes DB_PATH before every test, so this test -- calling
    # write_graph exactly once, with zero edges -- is guaranteed to hit a
    # database with no REL tables at all.
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    graphdb.write_graph("doc_no_edges", nodes, [])

    loaded = graphdb.load_graph("doc_no_edges")
    assert loaded is not None
    assert loaded["nodes"] == nodes
    assert loaded["edges"] == []
