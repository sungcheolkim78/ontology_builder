import threading
import time

import pytest

from app import graphdb
from app.embeddings import EMBEDDING_DIM


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


def test_reset_database_clears_all_documents_and_deletes_files_on_disk():
    graphdb.write_graph("doc_a", NODES, EDGES)
    graphdb.write_graph(
        "doc_b", [{"id": "n1", "label": "Charles Babbage", "type": "Person"}], []
    )

    graphdb.reset_database()

    assert not graphdb.DB_PATH.exists()
    wal_path = graphdb.DB_PATH.parent / (graphdb.DB_PATH.name + ".wal")
    assert not wal_path.exists()
    assert graphdb.has_graph("doc_a") is False
    assert graphdb.has_graph("doc_b") is False


def test_reset_database_leaves_a_usable_database_behind():
    graphdb.write_graph("doc_a", NODES, EDGES)
    graphdb.reset_database()

    graphdb.write_graph("doc_a", NODES, EDGES)
    loaded = graphdb.load_graph("doc_a")
    assert graphdb.has_graph("doc_a") is True
    assert sorted(loaded["nodes"], key=lambda n: n["id"]) == sorted(NODES, key=lambda n: n["id"])


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


def test_write_graph_raises_value_error_for_edge_with_unknown_source_node():
    # Regression test: an LLM extraction hallucinating an edge endpoint (a
    # node id not present in this document's own `nodes` list) used to raise
    # a raw KeyError from `nodes_by_id[edge["source"]]` deep inside
    # write_graph, propagating as an uncaught 500 on the extract endpoint.
    # It must instead raise ValueError, checked before any DDL/transaction
    # work begins, so main.py's existing `except ValueError -> 400` handling
    # covers it like every other malformed-LLM-output case.
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    bad_edges = [{"source": "n1", "target": "does_not_exist", "type": "WORKED_ON"}]

    with pytest.raises(ValueError):
        graphdb.write_graph("doc_dangling_edge", nodes, bad_edges)


def test_write_graph_raises_value_error_for_edge_with_unknown_target_node():
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    bad_edges = [{"source": "does_not_exist", "target": "n1", "type": "WORKED_ON"}]

    with pytest.raises(ValueError):
        graphdb.write_graph("doc_dangling_edge", nodes, bad_edges)


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


def test_find_relevant_nodes_matches_case_insensitively():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_relevant_nodes("doc_a", {"Person": ["ada"]}, ["Person"])

    assert matched == ["n1"]


def test_find_relevant_nodes_returns_empty_when_no_match():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_relevant_nodes("doc_a", {"Person": ["nonexistent"]}, ["Person"])

    assert matched == []


def test_find_relevant_nodes_filters_by_allowed_types():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_relevant_nodes("doc_a", {"Person": ["ada"]}, ["Concept"])

    assert matched == []


def test_find_relevant_nodes_only_matches_keyword_against_its_own_type():
    graphdb.write_graph("doc_a", NODES, EDGES)

    # "ada" is keyed under Concept, not Person, so it must not match the
    # Person node even though Person is an allowed type -- this is the whole
    # point of grouping keywords by type instead of searching them flat
    # across every allowed type.
    matched = graphdb.find_relevant_nodes(
        "doc_a", {"Concept": ["ada"]}, ["Person", "Concept"]
    )

    assert matched == []


def test_find_relevant_nodes_empty_allowed_types_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.find_relevant_nodes("doc_a", {"Person": ["ada"]}, []) == []


def test_find_relevant_nodes_scoped_to_document():
    graphdb.write_graph("doc_a", NODES, EDGES)
    graphdb.write_graph(
        "doc_b", [{"id": "n1", "label": "Ada Impersonator", "type": "Person"}], []
    )

    matched = graphdb.find_relevant_nodes("doc_a", {"Person": ["ada"]}, ["Person"])

    assert matched == ["n1"]  # doc_a's n1, not doc_b's


def _vec(dim, active_index):
    v = [0.0] * dim
    v[active_index] = 1.0
    return v


def test_find_similar_nodes_ranks_by_cosine_similarity():
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
        {"id": "n2", "label": "Charles Babbage", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 1)},
    ]
    graphdb.write_graph("doc_a", nodes, [])

    matched = graphdb.find_similar_nodes("doc_a", "Person", _vec(EMBEDDING_DIM, 0), top_k=1)

    assert matched == ["n1"]


def test_find_similar_nodes_respects_top_k():
    nodes = [
        {"id": "n1", "label": "A", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
        {"id": "n2", "label": "B", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
        {"id": "n3", "label": "C", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
    ]
    graphdb.write_graph("doc_a", nodes, [])

    matched = graphdb.find_similar_nodes("doc_a", "Person", _vec(EMBEDDING_DIM, 0), top_k=2)

    assert len(matched) == 2


def test_find_similar_nodes_excludes_nodes_without_an_embedding():
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
        {"id": "n2", "label": "Charles Babbage", "type": "Person"},  # no embedding -> NULL column
    ]
    graphdb.write_graph("doc_a", nodes, [])

    matched = graphdb.find_similar_nodes("doc_a", "Person", _vec(EMBEDDING_DIM, 0), top_k=5)

    assert matched == ["n1"]


def test_find_similar_nodes_respects_min_score():
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
        {"id": "n2", "label": "Charles Babbage", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 1)},
    ]
    graphdb.write_graph("doc_a", nodes, [])

    matched = graphdb.find_similar_nodes(
        "doc_a", "Person", _vec(EMBEDDING_DIM, 0), top_k=5, min_score=0.5
    )

    assert matched == ["n1"]  # n2's orthogonal embedding scores 0.0, below the floor


def test_update_node_embeddings_sets_embedding_on_existing_nodes():
    nodes = [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person"},
        {"id": "n2", "label": "Charles Babbage", "type": "Person"},
    ]
    graphdb.write_graph("doc_a", nodes, [])

    graphdb.update_node_embeddings(
        "doc_a",
        [
            {"id": "n1", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)},
            {"id": "n2", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 1)},
        ],
    )

    matched = graphdb.find_similar_nodes("doc_a", "Person", _vec(EMBEDDING_DIM, 0), top_k=1)
    assert matched == ["n1"]


def test_update_node_embeddings_empty_list_is_a_noop():
    graphdb.write_graph("doc_a", [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}], [])

    graphdb.update_node_embeddings("doc_a", [])  # must not raise

    assert graphdb.load_graph("doc_a")["nodes"] == [
        {"id": "n1", "label": "Ada Lovelace", "type": "Person"}
    ]


def test_find_similar_nodes_scoped_to_document():
    graphdb.write_graph(
        "doc_a", [{"id": "n1", "label": "Ada Lovelace", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)}], []
    )
    graphdb.write_graph(
        "doc_b", [{"id": "n1", "label": "Ada Impersonator", "type": "Person", "embedding": _vec(EMBEDDING_DIM, 0)}], []
    )

    matched = graphdb.find_similar_nodes("doc_a", "Person", _vec(EMBEDDING_DIM, 0), top_k=5)

    assert matched == ["n1"]  # doc_a's n1, not doc_b's


def test_find_similar_nodes_unknown_type_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.find_similar_nodes("doc_a", "NoSuchType", _vec(EMBEDDING_DIM, 0), top_k=5) == []


def test_find_similar_nodes_handles_zero_node_tables_on_fresh_database():
    graphdb.write_graph("doc_empty", [], [])

    assert graphdb.find_similar_nodes("doc_empty", "Person", _vec(EMBEDDING_DIM, 0), top_k=5) == []


def test_all_nodes_of_types_returns_every_instance():
    nodes = NODES + [{"id": "n3", "label": "Charles Babbage", "type": "Person"}]
    graphdb.write_graph("doc_a", nodes, EDGES)

    matched = graphdb.all_nodes_of_types("doc_a", ["Person"])

    assert set(matched) == {"n1", "n3"}


def test_all_nodes_of_types_empty_types_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.all_nodes_of_types("doc_a", []) == []


def test_find_matching_edges_filters_by_type_and_connection():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_matching_edges("doc_a", ["WORKED_ON"], {"n1"})

    assert matched == [{"source": "n1", "target": "n2", "type": "WORKED_ON", "detail": "From 1842"}]


def test_find_matching_edges_excludes_unconnected_nodes():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_matching_edges("doc_a", ["WORKED_ON"], {"some_other_id"})

    assert matched == []


def test_find_matching_edges_empty_matched_ids_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.find_matching_edges("doc_a", ["WORKED_ON"], set()) == []


def test_all_edges_of_types_ignores_connection():
    nodes = [
        {"id": "n3", "label": "Charles Babbage", "type": "Person"},
        {"id": "n4", "label": "Royal Society", "type": "Organization"},
    ]
    edges = [{"source": "n3", "target": "n4", "type": "MEMBER_OF"}]
    graphdb.write_graph("doc_a", NODES + nodes, EDGES + edges)

    matched = graphdb.all_edges_of_types("doc_a", ["MEMBER_OF"])

    assert matched == [{"source": "n3", "target": "n4", "type": "MEMBER_OF"}]


def test_all_edges_of_types_empty_types_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.all_edges_of_types("doc_a", []) == []


def test_find_matching_edges_handles_document_with_no_edges_on_fresh_database():
    # Regression test for the same zero-REL-table failure mode fixed in
    # load_graph (see test_load_graph_handles_document_with_no_edges_on_fresh_database):
    # a naive `MATCH (a)-[r]->(b) WHERE r.type IN $types ...` raises
    # `RuntimeError: Binder exception: Cannot find property source_document
    # for r.` when no REL table exists at all anywhere in the database. The
    # clean_graphdb autouse fixture wipes DB_PATH before every test, so this
    # is guaranteed to be the very first write_graph call against a fresh
    # database, with zero edges -- no REL table exists yet.
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    graphdb.write_graph("doc_no_edges", nodes, [])

    assert graphdb.find_matching_edges("doc_no_edges", ["WORKED_ON"], {"n1"}) == []


def test_all_edges_of_types_handles_document_with_no_edges_on_fresh_database():
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    graphdb.write_graph("doc_no_edges", nodes, [])

    assert graphdb.all_edges_of_types("doc_no_edges", ["WORKED_ON"]) == []


def test_expand_hops_zero_returns_only_seed():
    graphdb.write_graph("doc_a", NODES, EDGES)

    nodes, edges = graphdb.expand_hops("doc_a", {"n1"}, hops=0)

    assert {n["id"] for n in nodes} == {"n1"}
    assert edges == []


def test_expand_hops_one_includes_neighbor_and_connecting_edge():
    graphdb.write_graph("doc_a", NODES, EDGES)

    nodes, edges = graphdb.expand_hops("doc_a", {"n1"}, hops=1)

    assert {n["id"] for n in nodes} == {"n1", "n2"}
    assert edges == [{"source": "n1", "target": "n2", "type": "WORKED_ON", "detail": "From 1842"}]


def test_expand_hops_is_undirected():
    # n2 -[WORKED_ON]-> nothing incoming to n1 structurally, but expansion
    # from n2 must still reach n1 (undirected traversal).
    graphdb.write_graph("doc_a", NODES, EDGES)

    nodes, _ = graphdb.expand_hops("doc_a", {"n2"}, hops=1)

    assert {n["id"] for n in nodes} == {"n1", "n2"}


def test_expand_hops_does_not_cross_documents():
    graphdb.write_graph("doc_a", NODES, EDGES)
    graphdb.write_graph("doc_b", [{"id": "n1", "label": "Unrelated", "type": "Person"}], [])

    nodes, _ = graphdb.expand_hops("doc_a", {"n1"}, hops=5)

    assert {n["id"] for n in nodes} == {"n1", "n2"}  # doc_a's own graph only


def test_expand_hops_empty_seeds_returns_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    nodes, edges = graphdb.expand_hops("doc_a", set(), hops=1)

    assert nodes == []
    assert edges == []


def test_expand_hops_zero_hops_on_zero_rel_table_database_returns_seed():
    # Regression test for a silent-failure variant of the zero-REL-table bug
    # seen in load_graph/find_matching_edges/all_edges_of_types. There, an
    # untyped relationship pattern raised RuntimeError when no REL table
    # existed. Here, the variable-length pattern `MATCH (n)-[*0..{hops}]-(m)`
    # does NOT raise against a database with zero REL tables -- verified
    # experimentally -- it runs and silently returns zero rows, even at
    # hops=0 where m should always include n itself. The clean_graphdb
    # autouse fixture wipes DB_PATH before every test, so a single
    # write_graph call with zero edges guarantees no REL table exists yet.
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    graphdb.write_graph("doc_no_edges", nodes, [])

    result_nodes, edges = graphdb.expand_hops("doc_no_edges", {"n1"}, hops=0)

    assert {n["id"] for n in result_nodes} == {"n1"}
    assert edges == []


def test_load_graph_handles_document_with_zero_nodes_on_fresh_database():
    # Symmetric regression test to
    # test_load_graph_handles_document_with_no_edges_on_fresh_database, but
    # for the NODE-table case: a document extraction that legitimately
    # yields zero nodes (and therefore zero edges) still calls
    # write_graph, which still marks has_graph(stem) == True via the
    # _ExtractedDocument marker row -- but on a fresh database with no NODE
    # tables at all, load_graph's untyped `MATCH (n) ...` node query used to
    # raise `RuntimeError: Binder exception: Cannot find property
    # source_document for n.` instead of returning nodes: [].
    graphdb.write_graph("doc_empty", [], [])

    loaded = graphdb.load_graph("doc_empty")
    assert loaded == {"nodes": [], "edges": []}


def test_find_relevant_nodes_handles_zero_node_tables_on_fresh_database():
    graphdb.write_graph("doc_empty", [], [])

    assert graphdb.find_relevant_nodes("doc_empty", {"Person": ["ada"]}, ["Person"]) == []


def test_all_nodes_of_types_handles_zero_node_tables_on_fresh_database():
    graphdb.write_graph("doc_empty", [], [])

    assert graphdb.all_nodes_of_types("doc_empty", ["Person"]) == []


def test_expand_hops_handles_zero_node_tables_on_fresh_database():
    graphdb.write_graph("doc_empty", [], [])

    nodes, edges = graphdb.expand_hops("doc_empty", {"n1"}, hops=1)

    assert nodes == []
    assert edges == []


def test_write_graph_is_thread_safe_under_concurrent_calls():
    # Regression test for a genuine data-loss bug: graphdb.py holds one
    # module-level Connection, but FastAPI's synchronous `def` endpoints run
    # on a real worker threadpool, so concurrent requests execute
    # write_graph on different threads simultaneously. write_graph's
    # explicit BEGIN TRANSACTION/COMMIT/ROLLBACK is per-connection state --
    # two overlapping calls interleave destructively (verified: a second
    # thread's BEGIN while the first's transaction is open raises
    # "RuntimeError: Connection already has an active transaction", and the
    # first thread's subsequent COMMIT then fails too -- after its DETACH
    # DELETE has already run, i.e. actual data loss).
    #
    # To force genuine overlap rather than relying on timing luck, this
    # patches the shared connection's `execute` so the *first* thread to
    # reach "BEGIN TRANSACTION" pauses right after starting it -- leaving
    # its transaction open for a window the second thread's write_graph
    # call (released from the barrier at the same time) is guaranteed to
    # run into.
    conn = graphdb._get_connection()
    original_execute = conn.execute
    first_transaction_started = threading.Event()

    def patched_execute(query, *args, **kwargs):
        if query == "BEGIN TRANSACTION" and not first_transaction_started.is_set():
            first_transaction_started.set()
            result = original_execute(query, *args, **kwargs)
            time.sleep(0.3)
            return result
        return original_execute(query, *args, **kwargs)

    conn.execute = patched_execute

    barrier = threading.Barrier(2)
    errors = []

    nodes_a = [{"id": f"a{i:02d}", "label": f"Node A{i}", "type": "Person"} for i in range(10)]
    edges_a = [
        {"source": f"a{i:02d}", "target": f"a{i + 1:02d}", "type": "KNOWS"}
        for i in range(len(nodes_a) - 1)
    ]
    nodes_b = [{"id": f"b{i:02d}", "label": f"Node B{i}", "type": "Person"} for i in range(10)]
    edges_b = [
        {"source": f"b{i:02d}", "target": f"b{i + 1:02d}", "type": "KNOWS"}
        for i in range(len(nodes_b) - 1)
    ]

    def run(stem, nodes, edges):
        barrier.wait()
        try:
            graphdb.write_graph(stem, nodes, edges)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=run, args=("doc_concurrent_a", nodes_a, edges_a))
    t2 = threading.Thread(target=run, args=("doc_concurrent_b", nodes_b, edges_b))
    try:
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
    finally:
        conn.execute = original_execute

    assert errors == []
    assert graphdb.load_graph("doc_concurrent_a") == {"nodes": nodes_a, "edges": edges_a}
    assert graphdb.load_graph("doc_concurrent_b") == {"nodes": nodes_b, "edges": edges_b}


def test_expand_hops_nonzero_hops_on_zero_rel_table_database_returns_seed():
    # Same underlying bug as above, exercised with hops > 0: still no
    # traversal is possible (there are no relationships in the whole
    # database), but the seed node itself must still come back rather than
    # an empty list or a raised RuntimeError.
    nodes = [{"id": "n1", "label": "Ada Lovelace", "type": "Person"}]
    graphdb.write_graph("doc_no_edges", nodes, [])

    result_nodes, edges = graphdb.expand_hops("doc_no_edges", {"n1"}, hops=3)

    assert {n["id"] for n in result_nodes} == {"n1"}
    assert edges == []


def test_write_graph_scopes_by_version():
    graphdb.write_graph("doc_a", NODES, EDGES, version=1)
    graphdb.write_graph(
        "doc_a",
        [{"id": "n1", "label": "Version 2 Node", "type": "Person"}],
        [],
        version=2,
    )

    loaded_v1 = graphdb.load_graph("doc_a", version=1)
    loaded_v2 = graphdb.load_graph("doc_a", version=2)

    assert {n["label"] for n in loaded_v1["nodes"]} == {"Ada Lovelace", "Analytical Engine"}
    assert {n["label"] for n in loaded_v2["nodes"]} == {"Version 2 Node"}


def test_has_graph_is_scoped_by_version():
    graphdb.write_graph("doc_a", NODES, EDGES, version=1)

    assert graphdb.has_graph("doc_a", version=1) is True
    assert graphdb.has_graph("doc_a", version=2) is False


def test_node_from_row_uses_original_id_column():
    row = {"id": "doc_a::v1::n1", "original_id": "n1", "label": "Ada Lovelace", "type": "Person", "detail": None}
    assert graphdb._node_from_row(row) == {"id": "n1", "label": "Ada Lovelace", "type": "Person"}


def test_edge_from_row_uses_source_target_as_is():
    row = {"source": "n1", "target": "n2", "type": "WORKED_ON", "detail": None}
    assert graphdb._edge_from_row(row) == {"source": "n1", "target": "n2", "type": "WORKED_ON"}


def test_find_matching_edges_and_expand_hops_resolve_legacy_two_part_ids_via_original_id():
    # Regression test for the bug found during implementation planning:
    # a migrated (pre-versioning) row keeps its legacy 2-part PRIMARY KEY
    # id ("doc_a::n1", no version segment) forever. find_matching_edges
    # and expand_hops must resolve bare ids via the original_id column,
    # never by reconstructing a 3-part "doc_a::v1::n1" string and
    # matching it against id -- that reconstruction would never match a
    # legacy row like this one.
    conn = graphdb._get_connection()
    conn.execute(
        f"CREATE NODE TABLE Person(id STRING PRIMARY KEY, original_id STRING, "
        f"label STRING, detail STRING, source_document STRING, version INT64, "
        f"embedding FLOAT[{EMBEDDING_DIM}])"
    )
    conn.execute(
        "CREATE REL TABLE GROUP WORKED_ON(FROM Person TO Person, type STRING, "
        "detail STRING, source_document STRING, version INT64)"
    )
    conn.execute(
        "CREATE (:Person {id: 'doc_a::n1', original_id: 'n1', label: 'Ada Lovelace', "
        "detail: '', source_document: 'doc_a', version: 1})"
    )
    conn.execute(
        "CREATE (:Person {id: 'doc_a::n2', original_id: 'n2', label: 'Charles Babbage', "
        "detail: '', source_document: 'doc_a', version: 1})"
    )
    conn.execute(
        "MATCH (a:Person {id: 'doc_a::n1'}), (b:Person {id: 'doc_a::n2'}) "
        "CREATE (a)-[:WORKED_ON {type: 'WORKED_ON', detail: '', "
        "source_document: 'doc_a', version: 1}]->(b)"
    )
    graphdb.reset_connection()

    matched_edges = graphdb.find_matching_edges("doc_a", ["WORKED_ON"], {"n1"}, version=1)
    assert matched_edges == [{"source": "n1", "target": "n2", "type": "WORKED_ON"}]

    nodes, edges = graphdb.expand_hops("doc_a", {"n1"}, hops=1, version=1)
    assert {n["id"] for n in nodes} == {"n1", "n2"}
    assert edges == [{"source": "n1", "target": "n2", "type": "WORKED_ON"}]


def test_delete_version_data_removes_only_that_version():
    graphdb.write_graph("doc_a", NODES, EDGES, version=1)
    graphdb.write_graph(
        "doc_a", [{"id": "n1", "label": "V2 Node", "type": "Person"}], [], version=2
    )

    graphdb.delete_version_data("doc_a", version=1)

    assert graphdb.has_graph("doc_a", version=1) is False
    assert graphdb.has_graph("doc_a", version=2) is True
    loaded_v2 = graphdb.load_graph("doc_a", version=2)
    assert {n["label"] for n in loaded_v2["nodes"]} == {"V2 Node"}


def test_write_graph_treats_type_names_as_case_insensitive_like_the_db_engine():
    # Regression test: LadybugDB's catalog (and Cypher label resolution)
    # treats table/type identifiers case-insensitively -- confirmed
    # experimentally against a real database (CREATE NODE TABLE FOO after
    # CREATE NODE TABLE foo raises "Binder exception: FOO already exists
    # in catalog", while MATCH (n:FOO) correctly finds rows created under
    # label foo). write_graph's own existence check (`t not in existing`)
    # compared names with plain case-sensitive Python `in`, so a second
    # document whose LLM-generated type name matched an existing one
    # except for case (e.g. "WORKED_ON" vs "worked_on") incorrectly looked
    # unregistered and triggered a duplicate CREATE, crashing the entire
    # extraction with that same RuntimeError.
    graphdb.write_graph(
        "doc_a",
        [
            {"id": "n1", "label": "Ada Lovelace", "type": "Person"},
            {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
        ],
        [{"source": "n1", "target": "n2", "type": "worked_on"}],
    )

    # Second document reuses the same conceptual node/edge types but with
    # different casing -- must not raise.
    graphdb.write_graph(
        "doc_b",
        [
            {"id": "n1", "label": "Grace Hopper", "type": "PERSON"},
            {"id": "n2", "label": "COBOL", "type": "CONCEPT"},
        ],
        [{"source": "n1", "target": "n2", "type": "WORKED_ON"}],
    )

    loaded_b = graphdb.load_graph("doc_b")
    assert {n["label"] for n in loaded_b["nodes"]} == {"Grace Hopper", "COBOL"}
    assert loaded_b["edges"] == [{"source": "n1", "target": "n2", "type": "WORKED_ON"}]
