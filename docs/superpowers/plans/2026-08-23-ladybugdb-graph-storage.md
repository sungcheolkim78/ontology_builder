# LadybugDB Graph Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `nodes.json`/`edges.json` with LadybugDB (an embedded, Cypher-native graph database) as the storage and query layer for extracted ontology graphs, and rewrite GraphRAG's instance search and hop expansion as real Cypher queries instead of an in-memory `networkx` graph.

**Architecture:** A new `backend/app/graphdb.py` module owns a single shared LadybugDB database (`backend/data/graph.ladybugdb/`) holding all documents' nodes/edges, with one real table per node/edge type (not a generic catch-all), scoped per document via a `source_document` property and globally-unique `{stem}::{id}` node IDs. `ontology.py`'s `load_graph`/`save_graph` and `graphrag.py`'s `search_graph` keep (or nearly keep) their existing call signatures but delegate to `graphdb.py` instead of JSON files / `networkx`.

**Tech Stack:** `ladybug` (PyPI package name; embedded Cypher graph DB, Kùzu-compatible API — verified hands-on: `pip install ladybug`, `from ladybug import Database, Connection`).

**Spec:** `docs/superpowers/specs/2026-08-23-ladybugdb-graph-storage-design.md`

## Global Constraints

- Package: `pip install ladybug` — verified working; imports as `from ladybug import Database, Connection`. Do not use `kuzu` or any other package name.
- No new podman-compose service — LadybugDB is embedded (in-process), just a Python dependency and a directory on disk.
- `GET /api/ontology/{filename}` and `POST /api/chat` response shapes are unchanged — no frontend changes in this plan.
- Every node table: exactly `id STRING PRIMARY KEY, label STRING, detail STRING, source_document STRING`. Every edge table: exactly `type STRING, detail STRING, source_document STRING` plus its FROM/TO pair(s).
- Node IDs are stored as `f"{stem}::{original_id}"` and this prefix is stripped back off at every point data leaves `graphdb.py` — no other module ever sees a prefixed ID.
- Any node/edge type name used as a Cypher table/label identifier (never as a bound parameter value) MUST first pass `_validate_identifier` (pattern `^[A-Za-z_][A-Za-z0-9_]*$`), since Cypher labels can't be parameterized and these names originate from LLM output.
- Tests use a real LadybugDB against the project's existing `backend/data/graph.ladybugdb/` path, reset between tests — never mock the database itself (matches this repo's existing convention: `CLAUDE.md` notes file-backed tests use the real filesystem).
- Structural invariant relied on throughout: an edge only ever connects two nodes from the *same* document (extraction is per-document, and IDs are prefixed per-document) — no edge ever crosses `source_document` values. This is why deleting a document's nodes via `DETACH DELETE` is sufficient to also remove that document's edges, with no separate edge-deletion pass needed.
- `ChatRequest.hops` must be clamped to `1..5` server-side before being interpolated into Cypher query text (Cypher variable-length range bounds can't be bound parameters).

---

## File Structure

- Create: `backend/app/graphdb.py` — all LadybugDB connection management, DDL sync, and read/write queries. No other module talks to `ladybug` directly.
- Create: `backend/tests/test_graphdb.py` — TDD tests for `graphdb.py`, using the real database.
- Modify: `backend/requirements.txt` — add `ladybug`.
- Modify: `backend/app/ontology.py` — `load_graph`/`save_graph` delegate to `graphdb.py` instead of reading/writing `nodes.json`/`edges.json`.
- Modify: `backend/tests/test_ontology.py` — extraction tests currently assert against `nodes.json`/`edges.json` files on disk; update to assert against `graphdb.load_graph()` instead.
- Modify: `backend/app/graphrag.py` — `search_graph` takes a `stem` instead of a preloaded `graph_data` dict; instance search, fallback, and hop expansion go through `graphdb.py` instead of `networkx`.
- Modify: `backend/tests/test_graphrag.py` — rewrite to seed a real LadybugDB via `graphdb.write_graph()` instead of passing an in-memory dict.
- Modify: `backend/app/main.py` — chat handler passes `stem` (not `graph_data`) into `search_graph`, clamps `hops` to `1..5`, uses `graphdb.has_graph(stem)` instead of a truthy `load_graph` check, and widens its `ValueError` catch to cover `save_graph` (identifier-validation failures surface as 400, not 500).
- Modify: `backend/tests/test_chat.py` — `write_graph_dir()` helper writes through `ontology.save_schema`/`save_graph` instead of raw JSON files; cleanup extended to reset/remove the LadybugDB.
- Modify: `CLAUDE.md`, `docs/SPEC.md` — describe the new `graphdb.py` module, remove stale `nodes.json`/`edges.json` references.

---

### Task 1: `graphdb.py` foundation — connection lifecycle, identifier validation, `has_graph`

**Files:**
- Create: `backend/app/graphdb.py`
- Create: `backend/tests/test_graphdb.py`

**Interfaces:**
- Produces: `graphdb.DB_PATH: Path`, `graphdb.reset_connection() -> None`, `graphdb._validate_identifier(name: str) -> str` (raises `ValueError` on an unsafe name), `graphdb.has_graph(stem: str) -> bool`

- [ ] **Step 1: Add the dependency**

Add to `backend/requirements.txt` (after `networkx==3.6.1`, before the `opentelemetry-*` lines — order doesn't matter, just keep one package per line):

```
ladybug==0.19.1
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_graphdb.py`:

```python
import pytest

from app import graphdb


@pytest.fixture(autouse=True)
def clean_graphdb():
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil
        shutil.rmtree(graphdb.DB_PATH)
    yield
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil
        shutil.rmtree(graphdb.DB_PATH)


def test_has_graph_is_false_for_unknown_stem():
    assert graphdb.has_graph("nonexistent_stem") is False


def test_validate_identifier_accepts_safe_names():
    assert graphdb._validate_identifier("Person") == "Person"
    assert graphdb._validate_identifier("WORKED_ON") == "WORKED_ON"
    assert graphdb._validate_identifier("_leading_underscore") == "_leading_underscore"


def test_validate_identifier_rejects_unsafe_names():
    for bad in ["Person; DROP TABLE Person", "has space", "has-dash", "1StartsWithDigit", ""]:
        with pytest.raises(ValueError):
            graphdb._validate_identifier(bad)
```

- [ ] **Step 3: Run test to verify it fails**

Run (from `backend/`, with `.venv` activated): `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.graphdb'` (or `ImportError`).

- [ ] **Step 4: Write minimal implementation**

Create `backend/app/graphdb.py`:

```python
import re
from pathlib import Path

from ladybug import Connection, Database

DB_PATH = Path(__file__).parent.parent / "data" / "graph.ladybugdb"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_database = None
_connection = None


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"unsafe type name for graph DB identifier: {name!r}")
    return name


def _get_connection() -> Connection:
    global _database, _connection
    if _connection is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _database = Database(str(DB_PATH))
        _connection = Connection(_database)
        _connection.execute(
            "CREATE NODE TABLE IF NOT EXISTS _ExtractedDocument(stem STRING PRIMARY KEY)"
        )
    return _connection


def reset_connection() -> None:
    """Test-only: drop cached connection/database handles so a fresh one
    opens next time. Needed because tests delete DB_PATH on disk between
    runs -- the cached native handles would otherwise point at a
    now-missing directory."""
    global _database, _connection
    if _connection is not None:
        _connection.close()
        _connection = None
    if _database is not None:
        _database.close()
        _database = None


def has_graph(stem: str) -> bool:
    conn = _get_connection()
    result = conn.execute(
        "MATCH (d:_ExtractedDocument {stem: $stem}) RETURN d.stem AS stem", {"stem": stem}
    )
    return len(list(result.rows_as_dict())) > 0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/graphdb.py backend/tests/test_graphdb.py
git commit -m "Add graphdb.py foundation: LadybugDB connection lifecycle and identifier validation"
```

---

### Task 2: `graphdb.write_graph()` and `graphdb.load_graph()`

**Files:**
- Modify: `backend/app/graphdb.py`
- Modify: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: `_get_connection()`, `_validate_identifier(name)`, `has_graph(stem)` (Task 1)
- Produces: `graphdb.write_graph(stem: str, nodes: list[dict], edges: list[dict]) -> None`, `graphdb.load_graph(stem: str) -> dict | None` (returns `{"nodes": [...], "edges": [...]}` or `None`)

Node dict shape in/out: `{"id": str, "label": str, "type": str, "detail": str (optional)}`.
Edge dict shape in/out: `{"source": str, "target": str, "type": str, "detail": str (optional)}`. `source`/`target` are node `id`s, unprefixed on both sides of this boundary.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_graphdb.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: FAIL — `AttributeError: module 'app.graphdb' has no attribute 'write_graph'` (and `load_graph`).

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/graphdb.py`:

```python
def _validate_identifier_set(names: set) -> set:
    for name in names:
        _validate_identifier(name)
    return names


def _existing_tables(conn) -> dict:
    # Excludes _ExtractedDocument: it's an internal marker table with no
    # source_document property, so treating it as a regular data table in
    # write_graph's per-table DETACH DELETE loop would raise "Cannot find
    # property source_document for n." (verified against a real database --
    # a single-label MATCH errors on a missing property, unlike a generic
    # MATCH (n) across heterogeneous tables, which silently skips it).
    return {
        row["name"]: row["type"]
        for row in conn.execute("CALL show_tables() RETURN *").rows_as_dict()
        if row["name"] != "_ExtractedDocument"
    }


def _existing_pairs(conn, rel_type: str) -> set:
    rows = conn.execute(f'CALL show_connection("{rel_type}") RETURN *').rows_as_dict()
    return {(row["source table name"], row["destination table name"]) for row in rows}


def _node_from_row(row: dict) -> dict:
    node = {"id": row["id"].split("::", 1)[1], "label": row["label"], "type": row["type"]}
    if row.get("detail"):
        node["detail"] = row["detail"]
    return node


def _edge_from_row(row: dict) -> dict:
    edge = {
        "source": row["source"].split("::", 1)[1],
        "target": row["target"].split("::", 1)[1],
        "type": row["type"],
    }
    if row.get("detail"):
        edge["detail"] = row["detail"]
    return edge


def write_graph(stem: str, nodes: list, edges: list) -> None:
    conn = _get_connection()
    nodes_by_id = {n["id"]: n for n in nodes}

    node_types = _validate_identifier_set({n["type"] for n in nodes})
    edge_specs = {
        (
            _validate_identifier(e["type"]),
            _validate_identifier(nodes_by_id[e["source"]]["type"]),
            _validate_identifier(nodes_by_id[e["target"]]["type"]),
        )
        for e in edges
    }

    existing = _existing_tables(conn)
    for t in node_types:
        if t not in existing:
            conn.execute(
                f"CREATE NODE TABLE {t}(id STRING PRIMARY KEY, label STRING, "
                f"detail STRING, source_document STRING)"
            )
            existing[t] = "NODE"

    for etype, src, dst in edge_specs:
        if etype not in existing:
            conn.execute(
                f"CREATE REL TABLE GROUP {etype}(FROM {src} TO {dst}, "
                f"type STRING, detail STRING, source_document STRING)"
            )
            existing[etype] = "REL"
        elif (src, dst) not in _existing_pairs(conn, etype):
            conn.execute(f"ALTER TABLE {etype} ADD FROM {src} TO {dst}")

    conn.execute("BEGIN TRANSACTION")
    try:
        # DETACH DELETE removes each node's own edges too -- edges never
        # cross documents (see Global Constraints), so this alone clears
        # this document's entire previous graph.
        for name, kind in existing.items():
            if kind == "NODE":
                conn.execute(
                    f"MATCH (n:{name}) WHERE n.source_document = $stem DETACH DELETE n",
                    {"stem": stem},
                )

        for node in nodes:
            conn.execute(
                f"CREATE (:{node['type']} {{id: $id, label: $label, "
                f"detail: $detail, source_document: $stem}})",
                {
                    "id": f"{stem}::{node['id']}",
                    "label": node["label"],
                    "detail": node.get("detail") or "",
                    "stem": stem,
                },
            )

        for edge in edges:
            src_type = nodes_by_id[edge["source"]]["type"]
            dst_type = nodes_by_id[edge["target"]]["type"]
            conn.execute(
                f"MATCH (a:{src_type} {{id: $src}}), (b:{dst_type} {{id: $dst}}) "
                f"CREATE (a)-[:{edge['type']} {{type: $type, detail: $detail, "
                f"source_document: $stem}}]->(b)",
                {
                    "src": f"{stem}::{edge['source']}",
                    "dst": f"{stem}::{edge['target']}",
                    "type": edge["type"],
                    "detail": edge.get("detail") or "",
                    "stem": stem,
                },
            )

        conn.execute("MERGE (d:_ExtractedDocument {stem: $stem})", {"stem": stem})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def load_graph(stem: str) -> dict | None:
    if not has_graph(stem):
        return None
    conn = _get_connection()

    node_rows = conn.execute(
        "MATCH (n) WHERE n.source_document = $stem "
        "RETURN label(n) AS type, n.id AS id, n.label AS label, n.detail AS detail",
        {"stem": stem},
    ).rows_as_dict()
    nodes = [_node_from_row(row) for row in node_rows]

    edge_rows = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.source_document = $stem "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"stem": stem},
    ).rows_as_dict()
    edges = [_edge_from_row(row) for row in edge_rows]

    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphdb.py backend/tests/test_graphdb.py
git commit -m "Add graphdb.write_graph/load_graph with per-type table DDL sync"
```

---

### Task 3: `graphdb.find_relevant_nodes()` and `graphdb.all_nodes_of_types()`

**Files:**
- Modify: `backend/app/graphdb.py`
- Modify: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: `_get_connection()` (Task 1), the `NODES`/`EDGES` write path (Task 2)
- Produces: `graphdb.find_relevant_nodes(stem: str, keywords: list[str], allowed_types: list[str]) -> list[str]`, `graphdb.all_nodes_of_types(stem: str, allowed_types: list[str]) -> list[str]` — both return unprefixed node IDs.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_graphdb.py`:

```python
def test_find_relevant_nodes_matches_case_insensitively():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_relevant_nodes("doc_a", ["ada"], ["Person"])

    assert matched == ["n1"]


def test_find_relevant_nodes_returns_empty_when_no_match():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_relevant_nodes("doc_a", ["nonexistent"], ["Person"])

    assert matched == []


def test_find_relevant_nodes_filters_by_allowed_types():
    graphdb.write_graph("doc_a", NODES, EDGES)

    matched = graphdb.find_relevant_nodes("doc_a", ["ada"], ["Concept"])

    assert matched == []


def test_find_relevant_nodes_empty_allowed_types_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.find_relevant_nodes("doc_a", ["ada"], []) == []


def test_find_relevant_nodes_scoped_to_document():
    graphdb.write_graph("doc_a", NODES, EDGES)
    graphdb.write_graph(
        "doc_b", [{"id": "n1", "label": "Ada Impersonator", "type": "Person"}], []
    )

    matched = graphdb.find_relevant_nodes("doc_a", ["ada"], ["Person"])

    assert matched == ["n1"]  # doc_a's n1, not doc_b's


def test_all_nodes_of_types_returns_every_instance():
    nodes = NODES + [{"id": "n3", "label": "Charles Babbage", "type": "Person"}]
    graphdb.write_graph("doc_a", nodes, EDGES)

    matched = graphdb.all_nodes_of_types("doc_a", ["Person"])

    assert set(matched) == {"n1", "n3"}


def test_all_nodes_of_types_empty_types_matches_nothing():
    graphdb.write_graph("doc_a", NODES, EDGES)

    assert graphdb.all_nodes_of_types("doc_a", []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: FAIL — `AttributeError: module 'app.graphdb' has no attribute 'find_relevant_nodes'`.

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/graphdb.py`:

```python
def find_relevant_nodes(stem: str, keywords: list, allowed_types: list) -> list:
    if not allowed_types or not keywords:
        return []
    conn = _get_connection()
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem "
        "AND ANY(kw IN $keywords WHERE toLower(n.label) CONTAINS toLower(kw) "
        "OR toLower(kw) CONTAINS toLower(n.label)) "
        "RETURN n.id AS id",
        {"types": allowed_types, "stem": stem, "keywords": keywords},
    )
    return [row["id"].split("::", 1)[1] for row in result.rows_as_dict()]


def all_nodes_of_types(stem: str, allowed_types: list) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem RETURN n.id AS id",
        {"types": allowed_types, "stem": stem},
    )
    return [row["id"].split("::", 1)[1] for row in result.rows_as_dict()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: PASS (17 tests total).

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphdb.py backend/tests/test_graphdb.py
git commit -m "Add graphdb.find_relevant_nodes/all_nodes_of_types"
```

---

### Task 4: `graphdb.find_matching_edges()` and `graphdb.all_edges_of_types()`

**Files:**
- Modify: `backend/app/graphdb.py`
- Modify: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: `_get_connection()`, `_edge_from_row()` (Tasks 1-2)
- Produces: `graphdb.find_matching_edges(stem: str, allowed_types: list[str], matched_node_ids: set[str]) -> list[dict]`, `graphdb.all_edges_of_types(stem: str, allowed_types: list[str]) -> list[dict]` — both return edge dicts shaped `{"source": str, "target": str, "type": str, "detail": str (optional)}` with unprefixed IDs.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_graphdb.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: FAIL — `AttributeError: module 'app.graphdb' has no attribute 'find_matching_edges'`.

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/graphdb.py`:

```python
def find_matching_edges(stem: str, allowed_types: list, matched_node_ids: set) -> list:
    if not allowed_types or not matched_node_ids:
        return []
    conn = _get_connection()
    prefixed_ids = [f"{stem}::{nid}" for nid in matched_node_ids]
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "AND (a.id IN $ids OR b.id IN $ids) "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"types": allowed_types, "stem": stem, "ids": prefixed_ids},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]


def all_edges_of_types(stem: str, allowed_types: list) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"types": allowed_types, "stem": stem},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: PASS (22 tests total).

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphdb.py backend/tests/test_graphdb.py
git commit -m "Add graphdb.find_matching_edges/all_edges_of_types"
```

---

### Task 5: `graphdb.expand_hops()`

**Files:**
- Modify: `backend/app/graphdb.py`
- Modify: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: `_get_connection()`, `_node_from_row()`, `_edge_from_row()` (Tasks 1-2)
- Produces: `graphdb.expand_hops(stem: str, seed_ids: set[str], hops: int) -> tuple[list[dict], list[dict]]` — returns `(nodes, edges)` within `hops` of any seed, undirected, including the seeds themselves (hop 0).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_graphdb.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: FAIL — `AttributeError: module 'app.graphdb' has no attribute 'expand_hops'`.

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/graphdb.py`:

```python
def expand_hops(stem: str, seed_ids: set, hops: int) -> tuple:
    if not seed_ids:
        return [], []
    conn = _get_connection()
    prefixed_seeds = [f"{stem}::{sid}" for sid in seed_ids]
    hops = max(hops, 0)

    node_rows = conn.execute(
        f"MATCH (n)-[*0..{hops}]-(m) WHERE n.id IN $seeds AND m.source_document = $stem "
        f"RETURN DISTINCT label(m) AS type, m.id AS id, m.label AS label, m.detail AS detail",
        {"seeds": prefixed_seeds, "stem": stem},
    )
    nodes = [_node_from_row(row) for row in node_rows.rows_as_dict()]
    expanded_ids = [f"{stem}::{n['id']}" for n in nodes]

    edge_rows = conn.execute(
        "MATCH (a)-[r]->(b) WHERE a.id IN $ids AND b.id IN $ids AND r.source_document = $stem "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"ids": expanded_ids, "stem": stem},
    )
    edges = [_edge_from_row(row) for row in edge_rows.rows_as_dict()]

    return nodes, edges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v`
Expected: PASS (27 tests total). `graphdb.py` is now feature-complete for this plan.

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphdb.py backend/tests/test_graphdb.py
git commit -m "Add graphdb.expand_hops for undirected multi-hop context expansion"
```

---

### Task 6: Wire `ontology.py`'s `load_graph`/`save_graph` to `graphdb.py`

**Files:**
- Modify: `backend/app/ontology.py:113-139` (the `save_graph` and `load_graph` functions)
- Modify: `backend/tests/test_ontology.py`

**Interfaces:**
- Consumes: `graphdb.write_graph(stem, nodes, edges)`, `graphdb.load_graph(stem)` (Tasks 2)
- Produces: `ontology.save_graph(stem: str, graph: dict) -> None` (signature unchanged), `ontology.load_graph(stem: str) -> dict | None` (signature unchanged) — both now backed by LadybugDB, no `nodes.json`/`edges.json` files written or read.

This task changes existing, already-tested behavior, so existing tests in `test_ontology.py` that assert against `nodes.json`/`edges.json` files must be updated in the same commit — there's no separate "write a new failing test first" step here since the tests already exist and currently pass against the old (JSON) behavior; the TDD cycle is: change the assertions to the new expected behavior, watch them fail against the *old* implementation, then swap the implementation.

- [ ] **Step 1: Update the existing tests' assertions**

In `backend/tests/test_ontology.py`, replace the two tests that assert against `nodes.json`/`edges.json` files:

Replace `test_extract_saves_and_returns_graph` (currently reads `schema_dir / "nodes.json"` and `schema_dir / "edges.json"`):

```python
def test_extract_saves_and_returns_graph(monkeypatch):
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))

    graph = {
        "nodes": [{"id": "n1", "label": "Alice", "type": "Person"}],
        "edges": [],
    }
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(graph))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/extract")

    assert response.status_code == 200
    assert response.json() == graph
    from app import graphdb
    assert graphdb.load_graph("doc_raw") == graph
```

Replace `test_get_ontology_returns_saved_graph` (view its current body with `Read backend/tests/test_ontology.py` around line 130 before editing — it currently writes `nodes.json`/`edges.json` directly):

```python
def test_get_ontology_returns_saved_graph():
    from app import graphdb
    nodes = [{"id": "n1", "label": "Alice", "type": "Person"}]
    edges = []
    graphdb.write_graph("doc_raw", nodes, edges)
    client = TestClient(app)

    response = client.get("/api/ontology/doc_raw.md")

    assert response.status_code == 200
    assert response.json() == {"nodes": nodes, "edges": edges}
```

Add `graphdb` to the `clean_dirs` fixture's cleanup so LadybugDB state doesn't leak between tests in this file:

```python
@pytest.fixture(autouse=True)
def clean_dirs():
    from app import graphdb
    graphdb.reset_connection()
    for d in (DATA_DIR, GRAPH_DIR, graphdb.DB_PATH):
        if d.exists():
            shutil.rmtree(d)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    graphdb.reset_connection()
    for d in (DATA_DIR, GRAPH_DIR, graphdb.DB_PATH):
        if d.exists():
            shutil.rmtree(d)
```

- [ ] **Step 2: Run tests to verify the changed ones fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_ontology.py -v`
Expected: `test_extract_saves_and_returns_graph` and `test_get_ontology_returns_saved_graph` FAIL (the old `save_graph`/`load_graph` still write/read JSON files, so `graphdb.load_graph()` returns `None`/nothing was written there). Other tests still pass unchanged.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/ontology.py`, add the import:

```python
from app import graphdb
```

Replace the `save_graph` and `load_graph` functions (currently `backend/app/ontology.py:113-117` and `:130-139`):

```python
def save_graph(stem: str, graph: dict) -> None:
    graphdb.write_graph(stem, graph["nodes"], graph["edges"])


def load_graph(stem: str) -> dict | None:
    return graphdb.load_graph(stem)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_ontology.py -v`
Expected: PASS (all tests in this file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ontology.py backend/tests/test_ontology.py
git commit -m "Back ontology.py's load_graph/save_graph with LadybugDB instead of JSON files"
```

---

### Task 7: Rewrite `graphrag.py`'s `search_graph()` to use `graphdb.py`

**Files:**
- Modify: `backend/app/graphrag.py` (remove `find_relevant_nodes`, `find_matching_edges`, `all_nodes_of_types`, `all_edges_of_types`, `_build_graph`, `_format_node_line`, `_format_edge_line`, `_build_context_text`, `search_graph`; add new versions of the last three)
- Modify: `backend/tests/test_graphrag.py` (rewrite entirely — the node/edge-list-based tests move to `test_graphdb.py`, which already covers this logic from Tasks 3-5)

**Interfaces:**
- Consumes: `graphdb.find_relevant_nodes`, `graphdb.find_matching_edges`, `graphdb.all_nodes_of_types`, `graphdb.all_edges_of_types`, `graphdb.expand_hops` (Tasks 3-5)
- Produces: `graphrag.search_graph(question: str, schema: dict, stem: str, hops: int = 1) -> dict` (third parameter changes from `graph_data: dict` to `stem: str`; return shape unchanged: `{"node_types": [...], "edge_types": [...], "context": str | None}`)

- [ ] **Step 1: Rewrite the test file**

Replace `backend/tests/test_graphrag.py` entirely:

```python
import json

from app import graphdb
from app.graphrag import (
    determine_relevant_types,
    extract_keywords,
    format_type_preview,
    search_graph,
)

NODES = [
    {"id": "n1", "label": "Ada Lovelace", "type": "Person"},
    {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    {"id": "n3", "label": "Charles Babbage", "type": "Person"},
    {"id": "n4", "label": "Royal Society", "type": "Organization"},
]
EDGES = [
    {"source": "n1", "target": "n2", "type": "WORKED_ON"},
    {"source": "n3", "target": "n2", "type": "WORKED_ON"},
    {"source": "n3", "target": "n4", "type": "MEMBER_OF"},
]

SCHEMA = {
    "node_types": [
        {"name": "Person", "description": "a person"},
        {"name": "Concept", "description": "a concept"},
        {"name": "Organization", "description": "an organization"},
    ],
    "edge_types": [
        {"name": "WORKED_ON", "description": "worked on", "source": "Person", "target": "Concept"},
        {"name": "MEMBER_OF", "description": "member of", "source": "Person", "target": "Organization"},
    ],
}

STEM = "doc_raw"


class FakeChatModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages):
        return type("FakeResponse", (), {"content": self.content})()


class SequencedChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        content = self.responses[len(self.calls) - 1]
        return type("FakeResponse", (), {"content": content})()


def setup_function():
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil

        shutil.rmtree(graphdb.DB_PATH)


def teardown_function():
    graphdb.reset_connection()
    if graphdb.DB_PATH.exists():
        import shutil

        shutil.rmtree(graphdb.DB_PATH)


def test_determine_relevant_types_parses_and_filters_hallucinated_types(monkeypatch):
    monkeypatch.setattr(
        "app.graphrag.get_chat_model",
        lambda: FakeChatModel(
            json.dumps({"node_types": ["Person", "NotARealType"], "edge_types": ["WORKED_ON"]})
        ),
    )

    result = determine_relevant_types("What did Ada Lovelace work on?", SCHEMA)

    assert result == {"node_types": ["Person"], "edge_types": ["WORKED_ON"]}


def test_determine_relevant_types_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: FakeChatModel("not json"))

    try:
        determine_relevant_types("some question", SCHEMA)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_format_type_preview_lists_types():
    text = format_type_preview(["Person"], ["WORKED_ON"])
    assert "Person" in text
    assert "WORKED_ON" in text


def test_format_type_preview_shows_none_when_empty():
    text = format_type_preview([], [])
    assert "없음" in text


def test_search_graph_finds_context_when_types_and_keywords_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["edge_types"] == ["WORKED_ON"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Analytical Engine" in result["context"]
    assert len(model.calls) == 2  # type analysis, then keyword extraction


def test_search_graph_skips_keyword_extraction_when_no_types_relevant(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel([json.dumps({"node_types": [], "edge_types": []})])
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("completely unrelated question", SCHEMA, STEM, hops=1)

    assert result == {"node_types": [], "edge_types": [], "context": None}
    assert len(model.calls) == 1


def test_search_graph_falls_back_to_all_instances_when_no_keyword_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": []}),
            json.dumps(["someone who does not exist in the graph"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("who are the people?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Person"]
    assert result["context"] is not None
    assert "Ada Lovelace" in result["context"]
    assert "Charles Babbage" in result["context"]


def test_search_graph_falls_back_to_all_edges_of_type_when_no_keyword_match(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": [], "edge_types": ["MEMBER_OF"]}),
            json.dumps(["nonexistent keyword"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("what memberships exist?", SCHEMA, STEM, hops=1)

    assert result["edge_types"] == ["MEMBER_OF"]
    assert result["context"] is not None
    assert "Charles Babbage" in result["context"]
    assert "Royal Society" in result["context"]


def test_search_graph_returns_none_when_determined_type_has_no_instances(monkeypatch):
    nodes_without_organizations = [n for n in NODES if n["type"] != "Organization"]
    edges_without_organizations = [e for e in EDGES if e["type"] != "MEMBER_OF"]
    graphdb.write_graph(STEM, nodes_without_organizations, edges_without_organizations)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Organization"], "edge_types": []}),
            json.dumps(["nonexistent keyword"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("what organizations?", SCHEMA, STEM, hops=1)

    assert result["node_types"] == ["Organization"]
    assert result["context"] is None


def test_search_graph_includes_node_and_edge_detail_in_context(monkeypatch):
    nodes_with_detail = [
        {
            "id": "n1",
            "label": "Ada Lovelace",
            "type": "Person",
            "detail": "Corresponded with Babbage from 1833 onward.",
        },
        {"id": "n2", "label": "Analytical Engine", "type": "Concept"},
    ]
    edges_with_detail = [
        {
            "source": "n1",
            "target": "n2",
            "type": "WORKED_ON",
            "detail": "Wrote the first published algorithm for the machine in 1843.",
        },
    ]
    graphdb.write_graph(STEM, nodes_with_detail, edges_with_detail)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert "Corresponded with Babbage from 1833 onward." in result["context"]
    assert "Wrote the first published algorithm for the machine in 1843." in result["context"]


def test_search_graph_context_omits_missing_detail_gracefully(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES)
    model = SequencedChatModel(
        [
            json.dumps({"node_types": ["Person"], "edge_types": ["WORKED_ON"]}),
            json.dumps(["Ada Lovelace"]),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("What did Ada Lovelace work on?", SCHEMA, STEM, hops=1)

    assert result["context"] is not None
    assert "None" not in result["context"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphrag.py -v`
Expected: FAIL — `TypeError: search_graph() takes from 3 to 4 positional arguments but ...` or similar, since `search_graph` still expects `graph_data` and the old node/edge-list functions are being imported by name in the old test file (now replaced, so this specific error won't occur, but the calls to `search_graph(..., STEM, ...)` will fail against the *old* implementation that treats the third arg as a `graph_data` dict).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/graphrag.py`:

1. Replace the imports at the top (currently `import json`, `import networkx as nx`, `from app.chat import get_chat_model`, `from app.ontology import parse_json_response`, `from app.telemetry import invoke_with_telemetry`):

```python
import json

from app import graphdb
from app.chat import get_chat_model
from app.ontology import parse_json_response
from app.telemetry import invoke_with_telemetry
```

(drops `import networkx as nx`, adds `from app import graphdb`)

2. Delete these functions entirely: `find_relevant_nodes`, `find_matching_edges`, `all_nodes_of_types`, `all_edges_of_types`, `_build_graph`.

3. Replace `_format_edge_line` (keep `_format_node_line` as-is) and `_build_context_text`:

```python
def _format_edge_line(nodes_by_id: dict, edge: dict) -> str:
    line = f"- {nodes_by_id[edge['source']]['label']} --{edge['type']}--> {nodes_by_id[edge['target']]['label']}"
    if edge.get("detail"):
        line += f": {edge['detail']}"
    return line


def _build_context_text(stem: str, seed_ids: set, hops: int) -> str | None:
    if not seed_ids:
        return None

    nodes, edges = graphdb.expand_hops(stem, seed_ids, hops)
    if not nodes:
        return None

    nodes_by_id = {n["id"]: n for n in nodes}
    node_lines = [_format_node_line(n) for n in nodes]
    edge_lines = [_format_edge_line(nodes_by_id, e) for e in edges]

    parts = ["Entities:", *node_lines]
    if edge_lines:
        parts += ["", "Relations:", *edge_lines]
    return "\n".join(parts)
```

4. Replace `search_graph`:

```python
def search_graph(question: str, schema: dict, stem: str, hops: int = 1) -> dict:
    """Schema-aware graph search: determine which node/edge types (from the
    document's own schema) are relevant to the question, then search actual
    node/edge instances of those types via LadybugDB, then expand `hops`
    from whatever matched. Returns the determined types (for a "here's what
    I looked for" preview) alongside the resulting context text, or None if
    nothing was found at any stage."""
    types = determine_relevant_types(question, schema)
    node_types = types["node_types"]
    edge_types = types["edge_types"]

    if not node_types and not edge_types:
        return {"node_types": [], "edge_types": [], "context": None}

    keywords = extract_keywords(question)
    matched_node_ids = set(graphdb.find_relevant_nodes(stem, keywords, node_types))

    if edge_types:
        matched_edges = graphdb.find_matching_edges(stem, edge_types, matched_node_ids)
        for edge in matched_edges:
            matched_node_ids.add(edge["source"])
            matched_node_ids.add(edge["target"])

    if not matched_node_ids:
        matched_node_ids = set(graphdb.all_nodes_of_types(stem, node_types))
        if edge_types:
            for edge in graphdb.all_edges_of_types(stem, edge_types):
                matched_node_ids.add(edge["source"])
                matched_node_ids.add(edge["target"])

    context = _build_context_text(stem, matched_node_ids, hops)
    return {"node_types": node_types, "edge_types": edge_types, "context": context}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphrag.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphrag.py backend/tests/test_graphrag.py
git commit -m "Rewrite GraphRAG instance search and hop expansion as LadybugDB Cypher queries"
```

---

### Task 8: Wire `main.py`'s chat handler to the new `search_graph` signature

**Files:**
- Modify: `backend/app/main.py:63-98` (the `chat` handler) and `:179-194` (`create_extraction`)
- Modify: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: `graphdb.has_graph(stem)` (Task 1), `search_graph(question, schema, stem, hops)` (Task 7), `ontology.save_graph` (Task 6, now raises `ValueError` on an unsafe type name)
- Produces: no new public interface — this task is the final call-site wiring.

- [ ] **Step 1: Update the test helper and assertions**

In `backend/tests/test_chat.py`, replace `write_graph_dir` (currently writes `nodes.json`/`edges.json` directly) so it goes through the real save path:

```python
from app import graphdb
from app.ontology import save_graph, save_schema


def write_graph_dir(stem="doc_raw", schema=SCHEMA, nodes=NODES, edges=EDGES):
    save_schema(stem, schema)
    save_graph(stem, {"nodes": nodes, "edges": edges})
```

Replace every `shutil.rmtree(GRAPH_DIR)` cleanup call (there are 4, in `finally` blocks) with:

```python
    finally:
        graphdb.reset_connection()
        shutil.rmtree(GRAPH_DIR, ignore_errors=True)
        shutil.rmtree(graphdb.DB_PATH, ignore_errors=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_chat.py -v`
Expected: FAIL — `TypeError: search_graph() takes from 3 to 4 positional arguments but ...` from inside `app/main.py`'s `chat()` handler, since it still calls `search_graph(question, schema, graph_data, hops)` with a dict where `stem` is now expected, and `graph_data` (from the old `load_graph`, now delegating to `graphdb.load_graph`) is a full nodes/edges dict, not a stem string.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/main.py`, add the import:

```python
from app import graphdb
```

Replace the `chat` handler body (currently lines 63-98):

```python
@app.post("/api/chat")
def chat(request: ChatRequest):
    messages = [m.model_dump() for m in request.messages]

    if request.filename and messages:
        stem = _stem(request.filename)
        schema = load_schema(stem)
        if schema and graphdb.has_graph(stem):
            hops = max(1, min(5, request.hops))
            try:
                result = search_graph(messages[-1]["content"], schema, stem, hops)
            except ValueError:
                result = None

            if result is not None:
                preview = format_type_preview(result["node_types"], result["edge_types"])
                if result["context"]:
                    augmented = [
                        {
                            "role": "system",
                            "content": f"다음은 문서에서 추출된 관련 정보입니다:\n{result['context']}",
                        }
                    ] + messages
                    model = get_chat_model()
                    response = invoke_with_telemetry(
                        "chat.answer", model, to_langchain_messages(augmented)
                    )
                    content = f"{preview}\n\n{response.content}"
                else:
                    content = f"{preview}\n\n관련된 내용을 찾을 수 없습니다."
                return {"role": "assistant", "content": content}

    model = get_chat_model()
    lc_messages = to_langchain_messages(messages)
    response = invoke_with_telemetry("chat.answer", model, lc_messages)
    return {"role": "assistant", "content": response.content}
```

Replace `create_extraction` (currently lines 179-194) so a `save_graph` identifier-validation failure surfaces as 400, not an unhandled 500:

```python
@app.post("/api/ontology/{filename}/extract")
def create_extraction(filename: str):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    stem = _stem(filename)
    schema = load_schema(stem)
    if schema is None:
        schema = DEFAULT_SCHEMA
        save_schema(stem, schema)
    try:
        graph = extract_graph(doc_path.read_text(), schema)
        save_graph(stem, graph)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return graph
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/test_chat.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full backend suite**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/ -v`
Expected: PASS, all files (`test_chat.py`, `test_config.py`, `test_files.py`, `test_graphdb.py`, `test_graphrag.py`, `test_ontology.py`, `test_parse.py`, `test_telemetry.py`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_chat.py
git commit -m "Wire chat/extract endpoints to LadybugDB-backed search_graph and save_graph"
```

---

### Task 9: Docs sync and live verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/SPEC.md`

**Interfaces:** None — documentation and manual verification only.

- [ ] **Step 1: Update `CLAUDE.md`**

In the backend module bullet list, add a `graphdb.py` bullet (after the `chat.py` bullet, before `ontology.py`, matching the existing alphabetical-ish ordering by dependency) describing: owns the LadybugDB connection; one real table per node/edge type shared across all documents, scoped by a `source_document` property; node IDs prefixed `{stem}::{id}` internally, stripped at the module boundary; type names validated against a safe-identifier pattern before use in DDL since they originate from LLM output. Update the `ontology.py` bullet's "Persisted under `backend/data/graph/{stem}/{schema,nodes,edges}.json`" sentence to say only `schema.json` is still a JSON file; nodes/edges are persisted in LadybugDB via `graphdb.py`. Update the `graphrag.py` bullet's description of hop expansion from `nx.ego_graph` to `graphdb.expand_hops` (Cypher variable-length pattern).

- [ ] **Step 2: Update `docs/SPEC.md`**

Update the ontology/GraphRAG persistence description to match (same substance as the `CLAUDE.md` update, at whatever level of detail the surrounding section already uses — check the current wording with `grep -n "nodes.json\|edges.json\|ego_graph\|networkx" docs/SPEC.md` before editing, since this file has more sections referencing the old JSON files than `CLAUDE.md` does).

- [ ] **Step 3: Run the full backend suite one more time**

Run: `OPENROUTER_API_KEY=dummy python -m pytest tests/ -v`
Expected: PASS, no regressions from the docs-only changes.

- [ ] **Step 4: Live verification against the running stack**

```bash
mkdir -p backend/data && touch backend/data/.gitkeep
podman-compose down && podman-compose up --build -d
```

Wait for the stack to come up, then through the UI (or `curl`): upload a document, generate a schema, extract a graph, and ask a chat question that should trigger GraphRAG. Confirm: the graph renders in `OntologyGraph.vue` exactly as before (API contract unchanged), the chat response includes the `[관련 타입 분석]` preview line and a real answer, and `podman exec <backend container> ls /app/data/graph.ladybugdb` shows the LadybugDB directory was created (proving nodes/edges are no longer being written as `nodes.json`/`edges.json` under `/app/data/graph/{stem}/`).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/SPEC.md
git commit -m "Update docs for LadybugDB-backed graph storage"
```
