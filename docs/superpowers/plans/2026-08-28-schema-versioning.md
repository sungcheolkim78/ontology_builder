# Per-Document Schema Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one document hold multiple schema versions simultaneously, each with its own extracted NODE/REL graph, while chat/GraphRAG stays scoped to a single active version per document.

**Architecture:** Add a `version INT64` column (plus an `original_id STRING` column, see Global Constraints) to graphdb's existing shared per-type-name NODE/REL tables; store schemas as per-version flat files (`schema_v{n}.json`) with a `versions.json` active-version pointer; thread `version` through every graphdb/ontology/graphrag function and the affected `main.py` routes; add version list/activate/delete endpoints; add a version-management section to the frontend's File Explorer modal; migrate the 4 already-extracted real documents with a one-time script.

**Tech Stack:** FastAPI (Python 3.14), LadybugDB (Cypher-native embedded graph DB, kuzu-compatible), pytest, Vue 3 (`<script setup>`), podman-compose.

**Spec:** `docs/superpowers/specs/2026-08-28-schema-versioning-design.md` (includes a mid-planning correction — read the "Node/edge ID scheme" section before touching any lookup query).

## Global Constraints

- Every `graphdb.py` public function takes `version: int = 1` as a trailing parameter (not inserted mid-signature) — this default means **none of the ~60 existing tests in `tests/test_graphdb.py` need to change**; only new version-specific tests are added. `ontology.py`'s thin wrappers (`save_graph`, `load_graph`, `embed_graph`) follow the same `version: int = 1` default pattern for the same reason. `ontology.py`'s schema functions (`save_schema`, `load_schema`, `create_schema_version`, `activate_version`, `delete_version`) do **not** get a default — callers (always `main.py`, after resolving the active version) must be explicit, since there's no safe implicit version once a document can have zero, one, or several.
- **Node PRIMARY KEY `id` is never parsed or reconstructed by application code.** Every node row has a separate `original_id STRING` column holding the bare LLM-assigned id untouched. All lookups (`_node_from_row`, `_edge_from_row`, `find_matching_edges`, `expand_hops`) go through `original_id`, `source_document`, and `version` as plain column filters — never by rebuilding an `f"{stem}::v{version}::{id}"` string and matching it against `id`. This is the mid-planning correction; see the spec's "Node/edge ID scheme" section for why the naive approach is an actual bug against migrated data.
- New node ids are written as `f"{stem}::v{version}::{original_id}"` (including version 1). Existing rows written before this change keep their legacy 2-part id (`f"{stem}::{original_id}"`) forever unless re-extracted — this is fine precisely because nothing reads `id` for anything other than internal PRIMARY KEY uniqueness/joins.
- `ALTER TABLE t ADD colName TYPE [DEFAULT val]` is LadybugDB's real syntax — no `COLUMN` keyword. Verified experimentally against the running `ontology_builder_backend_1` container before this plan was written; do not second-guess this in Task 7.
- Every new backend test that touches the database resets `graphdb` state exactly like the existing tests in the same file do (autouse `clean_graphdb`/`clean_dirs` fixtures — check for an existing one in the file before adding a new one).
- Run backend tests with `OPENROUTER_API_KEY=dummy python -m pytest tests/ -v` from `backend/`, inside the project's `.venv` (see `CLAUDE.md`).

---

### Task 1: `graphdb.py` — version + `original_id` DDL, `write_graph`, `has_graph`, `_ExtractedDocument`

**Files:**
- Modify: `backend/app/graphdb.py:55-64` (`_get_connection`), `:101-107` (`has_graph`), `:176-318` (`write_graph`)
- Test: `backend/tests/test_graphdb.py` (append near the existing `write_graph`/`has_graph` tests)

**Interfaces:**
- Produces: `graphdb.write_graph(stem, nodes, edges, version=1)`, `graphdb.has_graph(stem, version=1)` — both now version-aware; every other task in this plan calls these with an explicit `version=`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graphdb.py`:

```python
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
```

(These reference `load_graph(stem, version=...)`, which Task 2 implements — that's fine, both tasks land before the suite needs to pass end to end; run only these two tests for now.)

- [ ] **Step 2: Run the new tests to verify they fail**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -k "scopes_by_version or scoped_by_version" -v
```

Expected: FAIL (`write_graph`/`has_graph` don't accept `version=` yet, and `load_graph` doesn't either).

- [ ] **Step 3: Update `_get_connection`**

In `backend/app/graphdb.py`, change:

```python
        _connection.execute(
            "CREATE NODE TABLE IF NOT EXISTS _ExtractedDocument(stem STRING PRIMARY KEY)"
        )
```

to:

```python
        _connection.execute(
            "CREATE NODE TABLE IF NOT EXISTS _ExtractedDocument("
            "id STRING PRIMARY KEY, stem STRING, version INT64)"
        )
```

- [ ] **Step 4: Update `has_graph`**

Change:

```python
@_synchronized
def has_graph(stem: str) -> bool:
    conn = _get_connection()
    result = conn.execute(
        "MATCH (d:_ExtractedDocument {stem: $stem}) RETURN d.stem AS stem", {"stem": stem}
    )
    return len(list(result.rows_as_dict())) > 0
```

to:

```python
@_synchronized
def has_graph(stem: str, version: int = 1) -> bool:
    conn = _get_connection()
    result = conn.execute(
        "MATCH (d:_ExtractedDocument {id: $id}) RETURN d.stem AS stem",
        {"id": f"{stem}::v{version}"},
    )
    return len(list(result.rows_as_dict())) > 0
```

- [ ] **Step 5: Rewrite `write_graph`**

Replace the entire function body (`backend/app/graphdb.py:176-318`) with:

```python
@_synchronized
def write_graph(stem: str, nodes: list, edges: list, version: int = 1) -> None:
    conn = _get_connection()
    nodes_by_id = {n["id"]: n for n in nodes}

    for edge in edges:
        if edge["source"] not in nodes_by_id:
            raise ValueError(f"edge references unknown node id: {edge['source']!r}")
        if edge["target"] not in nodes_by_id:
            raise ValueError(f"edge references unknown node id: {edge['target']!r}")

    node_types = _validate_identifier_set(n["type"] for n in nodes)
    edge_specs = list(
        dict.fromkeys(
            (
                _validate_identifier(e["type"]),
                _validate_identifier(nodes_by_id[e["source"]]["type"]),
                _validate_identifier(nodes_by_id[e["target"]]["type"]),
            )
            for e in edges
        )
    )

    existing = _existing_tables(conn)
    for t in node_types:
        if t not in existing:
            conn.execute(
                f"CREATE NODE TABLE {t}(id STRING PRIMARY KEY, original_id STRING, "
                f"label STRING, detail STRING, source_document STRING, version INT64, "
                f"embedding FLOAT[{EMBEDDING_DIM}])"
            )
            existing[t] = "NODE"

    for etype, src, dst in edge_specs:
        if etype not in existing:
            conn.execute(
                f"CREATE REL TABLE GROUP {etype}(FROM {src} TO {dst}, "
                f"type STRING, detail STRING, source_document STRING, version INT64)"
            )
            existing[etype] = "REL"
        elif (src, dst) not in _existing_pairs(conn, etype):
            conn.execute(f"ALTER TABLE {etype} ADD FROM {src} TO {dst}")

    conn.execute("BEGIN TRANSACTION")
    try:
        # DETACH DELETE removes each node's own edges too -- edges never
        # cross documents (see Global Constraints), so this alone clears
        # this document *version*'s entire previous graph.
        for name, kind in existing.items():
            if kind == "NODE":
                conn.execute(
                    f"MATCH (n:{name}) WHERE n.source_document = $stem "
                    f"AND n.version = $version DETACH DELETE n",
                    {"stem": stem, "version": version},
                )

        nodes_by_type = {}
        for node in nodes:
            nodes_by_type.setdefault(node["type"], []).append(node)
        for node_type, type_nodes in nodes_by_type.items():
            conn.execute(
                f"UNWIND $rows AS row "
                f"CREATE (:{node_type} {{id: row.id, original_id: row.original_id, "
                f"label: row.label, detail: row.detail, source_document: row.stem, "
                f"version: row.version, embedding: row.embedding}})",
                {
                    "rows": [
                        {
                            "id": f"{stem}::v{version}::{node['id']}",
                            "original_id": node["id"],
                            "label": node["label"],
                            "detail": node.get("detail") or "",
                            "stem": stem,
                            "version": version,
                            "embedding": node.get("embedding"),
                        }
                        for node in type_nodes
                    ]
                },
            )

        edges_by_spec = {}
        for edge in edges:
            src_type = nodes_by_id[edge["source"]]["type"]
            dst_type = nodes_by_id[edge["target"]]["type"]
            edges_by_spec.setdefault((edge["type"], src_type, dst_type), []).append(edge)
        for (etype, src_type, dst_type), spec_edges in edges_by_spec.items():
            conn.execute(
                f"UNWIND $rows AS row "
                f"MATCH (a:{src_type} {{id: row.src}}), (b:{dst_type} {{id: row.dst}}) "
                f"CREATE (a)-[:{etype} {{type: row.type, detail: row.detail, "
                f"source_document: row.stem, version: row.version}}]->(b)",
                {
                    "rows": [
                        {
                            "src": f"{stem}::v{version}::{edge['source']}",
                            "dst": f"{stem}::v{version}::{edge['target']}",
                            "type": edge["type"],
                            "detail": edge.get("detail") or "",
                            "stem": stem,
                            "version": version,
                        }
                        for edge in spec_edges
                    ]
                },
            )

        conn.execute(
            "MERGE (d:_ExtractedDocument {id: $id}) SET d.stem = $stem, d.version = $version",
            {"id": f"{stem}::v{version}", "stem": stem, "version": version},
        )
        conn.execute("COMMIT")
        reset_connection()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except RuntimeError as rollback_error:
            if "No active transaction" not in str(rollback_error):
                raise
        raise
```

- [ ] **Step 6: Run the new tests — expect them still failing on `load_graph`**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -k "scopes_by_version or scoped_by_version" -v
```

Expected: still FAIL — `load_graph` doesn't accept `version=` yet (Task 2). This is expected; don't chase it further in this task.

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/graphdb.py tests/test_graphdb.py
git commit -m "Add version/original_id columns and thread version through write_graph/has_graph"
```

---

### Task 2: `graphdb.py` — version-aware reads, `original_id`-based lookups

**Files:**
- Modify: `backend/app/graphdb.py:157-172` (`_node_from_row`/`_edge_from_row`), `:321-352` (`update_node_embeddings`), `:355-396` (`load_graph`), `:399-571` (all remaining read functions)
- Test: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: `write_graph`/`has_graph` from Task 1.
- Produces: `graphdb.load_graph(stem, version=1)`, `find_relevant_nodes(stem, type_keywords, allowed_types, version=1)`, `find_similar_nodes(stem, node_type, query_embedding, top_k, min_score=0.0, version=1)`, `all_nodes_of_types(stem, allowed_types, version=1)`, `find_matching_edges(stem, allowed_types, matched_node_ids, version=1)`, `all_edges_of_types(stem, allowed_types, version=1)`, `expand_hops(stem, seed_ids, hops, version=1)`, `update_node_embeddings(stem, nodes, version=1)` — every later task in this plan calls these.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_graphdb.py`:

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -k "original_id or as_is or legacy_two_part" -v
```

Expected: FAIL.

- [ ] **Step 3: Rewrite `_node_from_row`/`_edge_from_row`**

```python
def _node_from_row(row: dict) -> dict:
    node = {"id": row["original_id"], "label": row["label"], "type": row["type"]}
    if row.get("detail"):
        node["detail"] = row["detail"]
    return node


def _edge_from_row(row: dict) -> dict:
    edge = {
        "source": row["source"],
        "target": row["target"],
        "type": row["type"],
    }
    if row.get("detail"):
        edge["detail"] = row["detail"]
    return edge
```

- [ ] **Step 4: Rewrite `update_node_embeddings`**

```python
@_synchronized
def update_node_embeddings(stem: str, nodes: list, version: int = 1) -> None:
    if not nodes:
        return
    conn = _get_connection()
    conn.execute("BEGIN TRANSACTION")
    try:
        for node in nodes:
            node_type = _validate_identifier(node["type"])
            conn.execute(
                f"MATCH (n:{node_type} {{id: $id}}) SET n.embedding = $embedding",
                {"id": f"{stem}::v{version}::{node['id']}", "embedding": node.get("embedding")},
            )
        conn.execute("COMMIT")
        reset_connection()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except RuntimeError as rollback_error:
            if "No active transaction" not in str(rollback_error):
                raise
        raise
```

- [ ] **Step 5: Rewrite `load_graph`**

```python
@_synchronized
def load_graph(stem: str, version: int = 1) -> dict | None:
    if not has_graph(stem, version):
        return None
    conn = _get_connection()

    if _has_table_of_kind(conn, "NODE"):
        node_rows = conn.execute(
            "MATCH (n) WHERE n.source_document = $stem AND n.version = $version "
            "RETURN label(n) AS type, n.original_id AS original_id, n.label AS label, "
            "n.detail AS detail ORDER BY n.id",
            {"stem": stem, "version": version},
        ).rows_as_dict()
        nodes = [_node_from_row(row) for row in node_rows]
    else:
        nodes = []

    if _has_table_of_kind(conn, "REL"):
        edge_rows = conn.execute(
            "MATCH (a)-[r]->(b) WHERE r.source_document = $stem AND r.version = $version "
            "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
            "b.original_id AS target ORDER BY r.type, a.id, b.id",
            {"stem": stem, "version": version},
        ).rows_as_dict()
        edges = [_edge_from_row(row) for row in edge_rows]
    else:
        edges = []

    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 6: Rewrite `find_relevant_nodes`**

```python
@_synchronized
def find_relevant_nodes(stem: str, type_keywords: dict, allowed_types: list, version: int = 1) -> list:
    if not allowed_types or not type_keywords:
        return []
    pairs = [
        {"type": t, "keywords": kws}
        for t, kws in type_keywords.items()
        if t in allowed_types and kws
    ]
    if not pairs:
        return []
    conn = _get_connection()
    if not _has_table_of_kind(conn, "NODE"):
        return []
    result = conn.execute(
        "UNWIND $pairs AS tk "
        "MATCH (n) WHERE label(n) = tk.type AND n.source_document = $stem "
        "AND n.version = $version "
        "AND ANY(kw IN tk.keywords WHERE toLower(n.label) CONTAINS toLower(kw) "
        "OR toLower(kw) CONTAINS toLower(n.label)) "
        "RETURN DISTINCT n.original_id AS id",
        {"pairs": pairs, "stem": stem, "version": version},
    )
    return [row["id"] for row in result.rows_as_dict()]
```

- [ ] **Step 7: Rewrite `find_similar_nodes`**

```python
@_synchronized
def find_similar_nodes(
    stem: str, node_type: str, query_embedding: list, top_k: int, min_score: float = 0.0,
    version: int = 1,
) -> list:
    _validate_identifier(node_type)
    conn = _get_connection()
    if not _has_table_of_kind(conn, "NODE"):
        return []
    if node_type not in _existing_tables(conn):
        return []
    result = conn.execute(
        f"MATCH (n:{node_type}) WHERE n.source_document = $stem AND n.version = $version "
        f"AND n.embedding IS NOT NULL "
        f"RETURN n.original_id AS id, array_cosine_similarity(n.embedding, $query_embedding) AS score "
        f"ORDER BY score DESC LIMIT $top_k",
        {"stem": stem, "version": version, "query_embedding": query_embedding, "top_k": top_k},
    )
    return [
        row["id"]
        for row in result.rows_as_dict()
        if row["score"] is not None and row["score"] >= min_score
    ]
```

- [ ] **Step 8: Rewrite `all_nodes_of_types`**

```python
@_synchronized
def all_nodes_of_types(stem: str, allowed_types: list, version: int = 1) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    if not _has_table_of_kind(conn, "NODE"):
        return []
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem "
        "AND n.version = $version RETURN n.original_id AS id",
        {"types": allowed_types, "stem": stem, "version": version},
    )
    return [row["id"] for row in result.rows_as_dict()]
```

- [ ] **Step 9: Rewrite `find_matching_edges`**

```python
@_synchronized
def find_matching_edges(stem: str, allowed_types: list, matched_node_ids: set, version: int = 1) -> list:
    if not allowed_types or not matched_node_ids:
        return []
    conn = _get_connection()
    if not _has_table_of_kind(conn, "REL"):
        return []
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "AND r.version = $version AND (a.original_id IN $ids OR b.original_id IN $ids) "
        "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
        "b.original_id AS target",
        {"types": allowed_types, "stem": stem, "version": version, "ids": list(matched_node_ids)},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]
```

- [ ] **Step 10: Rewrite `all_edges_of_types`**

```python
@_synchronized
def all_edges_of_types(stem: str, allowed_types: list, version: int = 1) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    if not _has_table_of_kind(conn, "REL"):
        return []
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "AND r.version = $version "
        "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
        "b.original_id AS target",
        {"types": allowed_types, "stem": stem, "version": version},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]
```

- [ ] **Step 11: Rewrite `expand_hops`**

```python
@_synchronized
def expand_hops(stem: str, seed_ids: set, hops: int, version: int = 1) -> tuple:
    if not seed_ids:
        return [], []
    conn = _get_connection()
    hops = max(min(hops, MAX_EXPAND_HOPS), 0)

    if not _has_table_of_kind(conn, "NODE"):
        return [], []

    has_rel_table = _has_table_of_kind(conn, "REL")

    if has_rel_table:
        node_rows = conn.execute(
            f"MATCH (n)-[*0..{hops}]-(m) WHERE n.original_id IN $seeds "
            f"AND n.source_document = $stem AND n.version = $version "
            f"AND m.source_document = $stem AND m.version = $version "
            f"RETURN DISTINCT label(m) AS type, m.original_id AS original_id, "
            f"m.label AS label, m.detail AS detail",
            {"seeds": list(seed_ids), "stem": stem, "version": version},
        )
    else:
        node_rows = conn.execute(
            "MATCH (n) WHERE n.original_id IN $seeds AND n.source_document = $stem "
            "AND n.version = $version "
            "RETURN label(n) AS type, n.original_id AS original_id, n.label AS label, "
            "n.detail AS detail",
            {"seeds": list(seed_ids), "stem": stem, "version": version},
        )
    nodes = [_node_from_row(row) for row in node_rows.rows_as_dict()]

    if not has_rel_table:
        return nodes, []

    expanded_ids = [n["id"] for n in nodes]
    edge_rows = conn.execute(
        "MATCH (a)-[r]->(b) WHERE a.original_id IN $ids AND b.original_id IN $ids "
        "AND r.source_document = $stem AND r.version = $version "
        "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
        "b.original_id AS target",
        {"ids": expanded_ids, "stem": stem, "version": version},
    )
    edges = [_edge_from_row(row) for row in edge_rows.rows_as_dict()]

    return nodes, edges
```

- [ ] **Step 12: Run the full graphdb test file**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v
```

Expected: PASS — every pre-existing test in this file (none of which pass `version=`) plus every new test from Task 1 and this task.

- [ ] **Step 13: Commit**

```bash
cd backend && git add app/graphdb.py tests/test_graphdb.py
git commit -m "Thread version through graphdb reads; resolve node ids via original_id"
```

---

### Task 3: `graphdb.py` — `delete_version_data`

**Files:**
- Modify: `backend/app/graphdb.py` (add new function near `write_graph`)
- Test: `backend/tests/test_graphdb.py`

**Interfaces:**
- Consumes: `_existing_tables`, `_synchronized`, `_get_connection`, `reset_connection` (all already in this module).
- Produces: `graphdb.delete_version_data(stem, version=1)` — Task 5's `ontology.delete_version` calls this.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -k delete_version_data -v
```

Expected: FAIL (`AttributeError: module 'app.graphdb' has no attribute 'delete_version_data'`).

- [ ] **Step 3: Implement it**

Add to `backend/app/graphdb.py`, after `write_graph`:

```python
@_synchronized
def delete_version_data(stem: str, version: int = 1) -> None:
    """Removes every row belonging to this (stem, version) across all known
    NODE tables (DETACH DELETE also removes their edges) plus the matching
    _ExtractedDocument marker row. Used by ontology.delete_version when a
    schema version is deleted -- the schema file itself is removed by that
    caller, this only clears the graph data side."""
    conn = _get_connection()
    existing = _existing_tables(conn)
    conn.execute("BEGIN TRANSACTION")
    try:
        for name, kind in existing.items():
            if kind == "NODE":
                conn.execute(
                    f"MATCH (n:{name}) WHERE n.source_document = $stem "
                    f"AND n.version = $version DETACH DELETE n",
                    {"stem": stem, "version": version},
                )
        conn.execute(
            "MATCH (d:_ExtractedDocument {id: $id}) DETACH DELETE d",
            {"id": f"{stem}::v{version}"},
        )
        conn.execute("COMMIT")
        reset_connection()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except RuntimeError as rollback_error:
            if "No active transaction" not in str(rollback_error):
                raise
        raise
```

- [ ] **Step 4: Run test to verify it passes**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -k delete_version_data -v
```

Expected: PASS.

- [ ] **Step 5: Run the full graphdb suite**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_graphdb.py -v
```

Expected: PASS (all tests, old and new).

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/graphdb.py tests/test_graphdb.py
git commit -m "Add graphdb.delete_version_data"
```

---

### Task 4: `ontology.py` — version file storage and management functions

**Files:**
- Modify: `backend/app/ontology.py:1-13` (imports), `:298-379` (schema/graph persistence functions)
- Test: `backend/tests/test_ontology.py` (new tests, appended)

**Interfaces:**
- Consumes: `graphdb.write_graph(..., version=)`, `graphdb.load_graph(..., version=)`, `graphdb.update_node_embeddings(..., version=)`, `graphdb.delete_version_data(stem, version=)` (all from Tasks 1-3).
- Produces: `graph_dir_for(stem)`, `schema_path_for_version(stem, version)`, `versions_path(stem)`, `list_versions(stem) -> list[dict]`, `get_active_version(stem) -> int | None`, `save_schema(stem, version, schema)`, `load_schema(stem, version) -> dict | None`, `create_schema_version(stem, schema, document_type="general") -> int`, `activate_version(stem, version)` (raises `ValueError` if unknown), `delete_version(stem, version)` (raises `ValueError` if unknown), `list_schema_stems() -> list[str]`, `save_graph(stem, graph, version=1)`, `load_graph(stem, version=1)`, `embed_graph(stem, version=1) -> int` — Task 5's `main.py` calls all of these.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ontology.py`:

```python
def test_create_schema_version_increments_and_activates():
    from app.ontology import create_schema_version, get_active_version, list_versions

    v1 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []}, "general")
    v2 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []}, "legal")

    assert v1 == 1
    assert v2 == 2
    assert get_active_version("doc_raw") == 2
    assert [v["version"] for v in list_versions("doc_raw")] == [1, 2]


def test_activate_version_switches_active_pointer():
    from app.ontology import activate_version, create_schema_version, get_active_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    activate_version("doc_raw", 1)

    assert get_active_version("doc_raw") == 1


def test_activate_version_raises_for_unknown_version():
    from app.ontology import activate_version, create_schema_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    with pytest.raises(ValueError):
        activate_version("doc_raw", 99)


def test_delete_version_removes_schema_file_and_graph_rows():
    from app import graphdb
    from app.ontology import create_schema_version, delete_version, list_versions

    v1 = create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    graphdb.write_graph(
        "doc_raw", [{"id": "n1", "label": "Alice", "type": "Person"}], [], version=v1
    )

    delete_version("doc_raw", v1)

    assert list_versions("doc_raw") == []
    assert not (GRAPH_DIR / "doc_raw" / "schema_v1.json").is_file()
    assert graphdb.has_graph("doc_raw", version=1) is False


def test_delete_active_version_reactivates_most_recent_remaining():
    from app.ontology import create_schema_version, delete_version, get_active_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})
    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    delete_version("doc_raw", 2)

    assert get_active_version("doc_raw") == 1


def test_delete_version_raises_for_unknown_version():
    from app.ontology import create_schema_version, delete_version

    create_schema_version("doc_raw", {"node_types": [], "edge_types": []})

    with pytest.raises(ValueError):
        delete_version("doc_raw", 99)


def test_get_active_version_returns_none_when_no_versions_exist():
    from app.ontology import get_active_version

    assert get_active_version("never_seen") is None
```

Note: `pytest` is already imported at the top of `tests/test_ontology.py`; `graphdb` is imported inside the test bodies that need it, matching this file's existing style (see `test_embed_graph_computes_and_stores_embeddings`).

- [ ] **Step 2: Run the new tests to verify they fail**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_ontology.py -k "version" -v
```

Expected: FAIL (none of these functions exist yet).

- [ ] **Step 3: Add the `datetime` import**

In `backend/app/ontology.py`, add near the top (after the existing `import re`):

```python
from datetime import datetime
```

- [ ] **Step 4: Replace the schema/graph persistence functions**

Replace `backend/app/ontology.py:298-379` (from `def graph_dir_for` through the end of `def load_graph`) with:

```python
def graph_dir_for(stem: str) -> Path:
    return GRAPH_DIR / stem


def versions_path(stem: str) -> Path:
    return graph_dir_for(stem) / "versions.json"


def _load_versions_manifest(stem: str) -> dict:
    path = versions_path(stem)
    if not path.is_file():
        return {"active_version": None, "versions": []}
    return json.loads(path.read_text())


def _save_versions_manifest(stem: str, manifest: dict) -> None:
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    versions_path(stem).write_text(json.dumps(manifest))


def list_versions(stem: str) -> list[dict]:
    return _load_versions_manifest(stem)["versions"]


def get_active_version(stem: str) -> int | None:
    return _load_versions_manifest(stem)["active_version"]


def schema_path_for_version(stem: str, version: int) -> Path:
    return graph_dir_for(stem) / f"schema_v{version}.json"


def save_schema(stem: str, version: int, schema: dict) -> None:
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    schema_path_for_version(stem, version).write_text(json.dumps(schema))


def load_schema(stem: str, version: int) -> dict | None:
    path = schema_path_for_version(stem, version)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def create_schema_version(stem: str, schema: dict, document_type: str = "general") -> int:
    manifest = _load_versions_manifest(stem)
    next_version = max((v["version"] for v in manifest["versions"]), default=0) + 1
    save_schema(stem, next_version, schema)
    manifest["versions"].append(
        {
            "version": next_version,
            "document_type": document_type,
            "created_at": datetime.now().isoformat(),
        }
    )
    manifest["active_version"] = next_version
    _save_versions_manifest(stem, manifest)
    return next_version


def activate_version(stem: str, version: int) -> None:
    manifest = _load_versions_manifest(stem)
    if not any(v["version"] == version for v in manifest["versions"]):
        raise ValueError(f"version {version} not found for {stem!r}")
    manifest["active_version"] = version
    _save_versions_manifest(stem, manifest)


def delete_version(stem: str, version: int) -> None:
    manifest = _load_versions_manifest(stem)
    remaining = [v for v in manifest["versions"] if v["version"] != version]
    if len(remaining) == len(manifest["versions"]):
        raise ValueError(f"version {version} not found for {stem!r}")
    schema_path_for_version(stem, version).unlink(missing_ok=True)
    graphdb.delete_version_data(stem, version)
    manifest["versions"] = remaining
    if manifest["active_version"] == version:
        manifest["active_version"] = max((v["version"] for v in remaining), default=None)
    _save_versions_manifest(stem, manifest)


def save_document_manifest(stem: str, original_filename: str) -> None:
    """Records the one piece of per-document info the rest of this module's
    stem-based file layout loses: the filename as originally uploaded (e.g.
    "report.docx"), before parser.py renames it to "{stem}_raw.md". Schema
    and graph presence are deliberately NOT duplicated here -- get_active_version
    and graphdb.has_graph already answer those live, so there's nothing to
    keep in sync."""
    d = graph_dir_for(stem)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"original_filename": original_filename}))


def load_document_manifest(stem: str) -> dict | None:
    path = graph_dir_for(stem) / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def embed_nodes(nodes: list) -> list:
    """Attaches an "embedding" vector to each node (label + detail text),
    so graphdb.find_similar_nodes has something to rank against later when
    a question's keywords don't literally match any node's label. Returns
    new dicts rather than mutating the input."""
    if not nodes:
        return []
    model = get_embedding_model()
    texts = [node_embedding_text(n) for n in nodes]
    vectors = embed_with_telemetry("ontology.embed_nodes", model, texts)
    return [{**node, "embedding": vector} for node, vector in zip(nodes, vectors)]


def save_graph(stem: str, graph: dict, version: int = 1) -> None:
    graphdb.write_graph(stem, graph["nodes"], graph["edges"], version=version)


def embed_graph(stem: str, version: int = 1) -> int:
    """Embeds this document version's already-extracted nodes in a separate
    pass from extraction, so a large document's LLM extraction call doesn't
    also pay for the embedding call before anything is visible. Reads the
    nodes graphdb already has (written by save_graph with no embedding),
    computes vectors, and updates them in place via graphdb.update_node_embeddings
    -- rerunning this is safe and simply recomputes/overwrites every node's
    embedding."""
    graph = graphdb.load_graph(stem, version=version)
    if graph is None or not graph["nodes"]:
        return 0
    nodes = embed_nodes(graph["nodes"])
    graphdb.update_node_embeddings(stem, nodes, version=version)
    return len(nodes)


def list_schema_stems() -> list[str]:
    if not GRAPH_DIR.is_dir():
        return []
    return [
        d.name
        for d in GRAPH_DIR.iterdir()
        if d.is_dir() and (d / "versions.json").is_file()
    ]


def load_graph(stem: str, version: int = 1) -> dict | None:
    return graphdb.load_graph(stem, version=version)
```

Note this drops the old module-level `save_schema(stem, schema)`/`load_schema(stem)` (single-file, no version) entirely — every caller becomes explicit about which version it means, per this task's Interfaces block. `list_schema_stems` switches its existence check from `schema.json` to `versions.json`.

- [ ] **Step 5: Run the new tests to verify they pass**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_ontology.py -k "version" -v
```

Expected: PASS for the 7 new tests. (Do **not** run the whole file yet — many pre-existing tests still call the old `save_schema(stem, schema)`/`load_schema(stem)` shapes and will fail until Task 6.)

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/ontology.py tests/test_ontology.py
git commit -m "Add per-version schema file storage and version management to ontology.py"
```

---

### Task 5: `main.py` + `graphrag.py` — routes and search_graph version threading

**Files:**
- Modify: `backend/app/main.py:13-25` (imports), `:77-116` (`chat`), `:154-174` (`list_documents`, `list_schemas`), `:196-271` (schema/extract/embed/get_ontology routes; add new version routes)
- Modify: `backend/app/graphrag.py:106-199` (`search_graph`)
- Test: `backend/tests/test_ontology.py` (new endpoint tests), `backend/tests/test_graphrag.py` (one new test)

**Interfaces:**
- Consumes: everything from Task 4 (`get_active_version`, `create_schema_version`, `activate_version`, `delete_version`, `list_versions`, `load_schema`, `save_graph`, `load_graph`, `embed_graph`) and `graphdb.has_graph(stem, version=)`.
- Produces: the new/changed HTTP routes below; no other task depends on this one's internals.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ontology.py`:

```python
def test_generate_schema_response_includes_version(monkeypatch):
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.status_code == 200
    assert response.json() == {**schema, "version": 1}
    saved = json.loads((GRAPH_DIR / "doc_raw" / "schema_v1.json").read_text())
    assert saved == schema
    versions = json.loads((GRAPH_DIR / "doc_raw" / "versions.json").read_text())
    assert versions["active_version"] == 1


def test_generate_schema_second_call_creates_second_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr(
        "app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema))
    )
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    response = client.post("/api/ontology/doc_raw.md/schema")

    assert response.json()["version"] == 2
    assert (GRAPH_DIR / "doc_raw" / "schema_v1.json").is_file()
    assert (GRAPH_DIR / "doc_raw" / "schema_v2.json").is_file()


def test_list_schema_versions_endpoint_reports_active_and_graph_status(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)

    client.post("/api/ontology/doc_raw.md/schema")
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.get("/api/ontology/doc_raw.md/schema/versions")

    assert response.status_code == 200
    versions = response.json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert [v["is_active"] for v in versions] == [False, True]
    assert [v["has_graph"] for v in versions] == [False, False]


def test_activate_schema_version_endpoint_switches_active_version(monkeypatch):
    write_document()
    schema_v1 = {"node_types": [{"name": "Person", "description": "v1"}], "edge_types": []}
    schema_v2 = {"node_types": [{"name": "Organization", "description": "v2"}], "edge_types": []}
    client = TestClient(app)

    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema_v1)))
    client.post("/api/ontology/doc_raw.md/schema")
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema_v2)))
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.post("/api/ontology/doc_raw.md/schema/versions/1/activate")

    assert response.status_code == 200
    assert client.get("/api/ontology/doc_raw.md/schema").json() == schema_v1


def test_activate_schema_version_endpoint_returns_404_for_unknown_version():
    write_document()
    client = TestClient(app)

    response = client.post("/api/ontology/doc_raw.md/schema/versions/99/activate")

    assert response.status_code == 404


def test_delete_schema_version_endpoint_removes_version(monkeypatch):
    write_document()
    schema = {"node_types": [], "edge_types": []}
    monkeypatch.setattr("app.ontology.get_chat_model", lambda: FakeChatModel(json.dumps(schema)))
    client = TestClient(app)
    client.post("/api/ontology/doc_raw.md/schema")
    client.post("/api/ontology/doc_raw.md/schema")

    response = client.delete("/api/ontology/doc_raw.md/schema/versions/2")

    assert response.status_code == 200
    versions = client.get("/api/ontology/doc_raw.md/schema/versions").json()["versions"]
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["is_active"] is True


def test_delete_schema_version_endpoint_returns_404_for_unknown_version():
    write_document()
    client = TestClient(app)

    response = client.delete("/api/ontology/doc_raw.md/schema/versions/99")

    assert response.status_code == 404
```

Append to `backend/tests/test_graphrag.py`:

```python
def test_search_graph_scoped_to_active_version(monkeypatch):
    graphdb.write_graph(STEM, NODES, EDGES, version=1)
    graphdb.write_graph(
        STEM,
        [{"id": "n9", "label": "Grace Hopper", "type": "Person"}],
        [],
        version=2,
    )
    model = SequencedChatModel(
        [
            json.dumps(
                {
                    "node_types": ["Person"],
                    "edge_types": [],
                    "keywords": {"Person": ["Grace Hopper"]},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.graphrag.get_chat_model", lambda: model)

    result = search_graph("Who is Grace Hopper?", SCHEMA, STEM, version=2, hops=0)

    assert {n["label"] for n in result["related_nodes"]} == {"Grace Hopper"}
```

- [ ] **Step 2: Run the new tests to verify they fail**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_ontology.py -k "version" tests/test_graphrag.py -k "scoped_to_active_version" -v
```

Expected: FAIL (routes don't exist yet; `search_graph` doesn't accept `version=`; existing `test_generate_schema_saves_and_returns_schema` will also now fail since it asserts against the old response shape — that's expected and gets fixed in Task 6, ignore it here).

- [ ] **Step 3: Update `graphrag.py`'s `search_graph`**

In `backend/app/graphrag.py`, change the signature and every `graphdb.*` call inside it:

```python
def search_graph(question: str, schema: dict, stem: str, version: int = 1, hops: int = 1) -> dict:
```

and update each call site inside the function body (lines as read earlier in this file):

```python
            type_ids = graphdb.find_relevant_nodes(
                stem, {node_type: keywords.get(node_type, [])}, [node_type], version=version
            )
```
```python
                type_ids = graphdb.find_similar_nodes(
                    stem, node_type, query_embedding, top_k=EMBEDDING_FALLBACK_TOP_K,
                    version=version,
                )
```
```python
                type_ids = graphdb.all_nodes_of_types(stem, [node_type], version=version)
```
```python
        matched_edges = graphdb.find_matching_edges(
            stem, edge_types, matched_node_id_set, version=version
        )
```
```python
            for edge in graphdb.all_edges_of_types(stem, edge_types, version=version):
```
```python
    related_nodes, related_edges = graphdb.expand_hops(
        stem, matched_node_id_set, hops, version=version
    )
```

- [ ] **Step 4: Update `main.py` imports**

Change:

```python
from app.ontology import (
    DEFAULT_SCHEMA,
    embed_graph,
    extract_graph,
    generate_schema,
    list_schema_stems,
    load_document_manifest,
    load_graph,
    load_schema,
    save_document_manifest,
    save_graph,
    save_schema,
)
```

to:

```python
from app.ontology import (
    DEFAULT_SCHEMA,
    activate_version,
    create_schema_version,
    delete_version,
    embed_graph,
    extract_graph,
    generate_schema,
    get_active_version,
    list_schema_stems,
    list_versions,
    load_document_manifest,
    load_graph,
    load_schema,
    save_document_manifest,
    save_graph,
)
```

(`save_schema` is dropped — nothing outside `ontology.py` calls it directly anymore, `create_schema_version`/`activate_version` own that now.)

- [ ] **Step 5: Update `list_documents`**

In `backend/app/main.py`, change the body of `list_documents` (around line 158-174):

```python
    for p in paths:
        if not p.is_file() or p.name.startswith("."):
            continue
        stem = p.stem
        manifest = load_document_manifest(stem)
        documents.append(
            {
                "filename": p.name,
                "original_filename": (manifest or {}).get("original_filename", p.name),
                "has_schema": load_schema(stem) is not None,
                "has_graph": graphdb.has_graph(stem),
                "graphdb_name": graphdb.DB_PATH.name,
            }
        )
```

to:

```python
    for p in paths:
        if not p.is_file() or p.name.startswith("."):
            continue
        stem = p.stem
        manifest = load_document_manifest(stem)
        active_version = get_active_version(stem)
        documents.append(
            {
                "filename": p.name,
                "original_filename": (manifest or {}).get("original_filename", p.name),
                "has_schema": active_version is not None,
                "has_graph": active_version is not None
                and graphdb.has_graph(stem, version=active_version),
                "graphdb_name": graphdb.DB_PATH.name,
            }
        )
```

- [ ] **Step 6: Update the schema/extract/embed/get_ontology/get_schema routes**

Replace each of these in `backend/app/main.py`:

```python
@app.post("/api/ontology/{filename}/schema")
def create_schema(filename: str, request: CreateSchemaRequest | None = None):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    document_type = request.document_type if request else "general"
    max_chars = request.max_chars if request else None
    try:
        schema = generate_schema(
            doc_path.read_text(), document_type=document_type, max_chars=max_chars
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    version = create_schema_version(_stem(filename), schema, document_type=document_type)
    return {**schema, "version": version}


class UseSchemaRequest(BaseModel):
    source_stem: str


@app.post("/api/ontology/{filename}/schema/use")
def use_schema(filename: str, request: UseSchemaRequest):
    source_version = get_active_version(request.source_stem)
    if source_version is None:
        raise HTTPException(status_code=404, detail="source schema not found")
    schema = load_schema(request.source_stem, source_version)
    source_document_type = next(
        (
            v["document_type"]
            for v in list_versions(request.source_stem)
            if v["version"] == source_version
        ),
        "general",
    )
    version = create_schema_version(_stem(filename), schema, document_type=source_document_type)
    return {**schema, "version": version}


@app.get("/api/ontology/{filename}/schema")
def get_schema(filename: str):
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        raise HTTPException(status_code=404, detail="schema not found")
    return load_schema(stem, version)


@app.post("/api/ontology/{filename}/extract")
def create_extraction(filename: str):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        version = create_schema_version(stem, DEFAULT_SCHEMA, document_type="default")
    schema = load_schema(stem, version)
    try:
        graph = extract_graph(doc_path.read_text(), schema)
        save_graph(stem, graph, version=version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return graph


@app.post("/api/ontology/{filename}/embed")
def create_embeddings(filename: str):
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None or not graphdb.has_graph(stem, version=version):
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    embedded = embed_graph(stem, version=version)
    return {"embedded": embedded}


@app.get("/api/ontology/{filename}")
def get_ontology(filename: str):
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    graph = load_graph(stem, version=version)
    if graph is None:
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    return graph
```

(This is a straight replacement of the whole existing block from `class CreateSchemaRequest` through the end of `get_ontology`, i.e. `backend/app/main.py:196-271` in the pre-Task-5 file.)

- [ ] **Step 7: Add the new version-management routes**

Append immediately after `get_ontology`:

```python
@app.get("/api/ontology/{filename}/schema/versions")
def get_schema_versions(filename: str):
    stem = _stem(filename)
    active = get_active_version(stem)
    return {
        "versions": [
            {
                **v,
                "is_active": v["version"] == active,
                "has_graph": graphdb.has_graph(stem, version=v["version"]),
            }
            for v in list_versions(stem)
        ]
    }


@app.post("/api/ontology/{filename}/schema/versions/{version}/activate")
def activate_schema_version(filename: str, version: int):
    try:
        activate_version(_stem(filename), version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}


@app.delete("/api/ontology/{filename}/schema/versions/{version}")
def delete_schema_version(filename: str, version: int):
    try:
        delete_version(_stem(filename), version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}
```

- [ ] **Step 8: Update the `/api/chat` handler**

In `backend/app/main.py`, change:

```python
    if request.filename and messages:
        stem = _stem(request.filename)
        schema = load_schema(stem)
        if schema and graphdb.has_graph(stem):
            hops = max(1, min(5, request.hops))
            try:
                result = search_graph(messages[-1]["content"], schema, stem, hops)
            except ValueError:
                result = None
```

to:

```python
    if request.filename and messages:
        stem = _stem(request.filename)
        version = get_active_version(stem)
        schema = load_schema(stem, version) if version is not None else None
        if schema and graphdb.has_graph(stem, version=version):
            hops = max(1, min(5, request.hops))
            try:
                result = search_graph(
                    messages[-1]["content"], schema, stem, version=version, hops=hops
                )
            except ValueError:
                result = None
```

- [ ] **Step 9: Run the new tests to verify they pass**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/test_ontology.py -k "version" tests/test_graphrag.py -k "scoped_to_active_version" -v
```

Expected: PASS for every test written in Step 1 of this task. (Many *other* pre-existing tests in `test_ontology.py`, `test_files.py`, and `test_chat.py` are still red at this point — Task 6 fixes those. Do not run the full suite yet.)

- [ ] **Step 10: Commit**

```bash
cd backend && git add app/main.py app/graphrag.py tests/test_ontology.py tests/test_graphrag.py
git commit -m "Add schema version endpoints; scope extract/embed/chat/search_graph to active version"
```

---

### Task 6: Fix pre-existing tests broken by the file-layout and response-shape changes

**Files:**
- Modify: `backend/tests/test_ontology.py`, `backend/tests/test_files.py`, `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5. Produces nothing new — this task only makes previously-passing tests pass again under the new storage format.

- [ ] **Step 1: Run the full suite to see the current breakage**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/ -v
```

Expected: several FAILures in `test_ontology.py`, `test_files.py`, `test_chat.py` — all from tests that either (a) write `schema.json` directly to disk to simulate "a schema already exists," or (b) assert a schema-creation response equals the raw schema dict with no `version` key. Every other file should already be green from Tasks 1-5.

- [ ] **Step 2: Add a `seed_schema_version` helper to `test_ontology.py`**

Add directly after the existing `write_document` helper (`backend/tests/test_ontology.py`, right after its `def write_document(...)` function):

```python
def seed_schema_version(stem, schema, version=1, document_type="general"):
    d = GRAPH_DIR / stem
    d.mkdir(parents=True, exist_ok=True)
    (d / f"schema_v{version}.json").write_text(json.dumps(schema))
    (d / "versions.json").write_text(
        json.dumps(
            {
                "active_version": version,
                "versions": [
                    {"version": version, "document_type": document_type, "created_at": None}
                ],
            }
        )
    )
```

- [ ] **Step 3: Fix `test_generate_schema_saves_and_returns_schema`**

Delete this test entirely — it's now fully superseded by `test_generate_schema_response_includes_version` (added in Task 5), which asserts the same behavior against the correct new response/file shape.

- [ ] **Step 4: Fix `test_extract_uses_and_saves_default_schema_when_none_saved`**

Change the assertion line from:

```python
    saved_schema = json.loads((GRAPH_DIR / "doc_raw" / "schema.json").read_text())
```

to:

```python
    saved_schema = json.loads((GRAPH_DIR / "doc_raw" / "schema_v1.json").read_text())
```

- [ ] **Step 5: Fix `test_extract_saves_and_returns_graph`**

Change:

```python
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))
```

to:

```python
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)
```

and change the final assertion from `graphdb.load_graph("doc_raw") == graph` to `graphdb.load_graph("doc_raw", version=1) == graph`.

- [ ] **Step 6: Fix `test_extract_returns_400_on_invalid_json`**

Change:

```python
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    (schema_dir / "schema.json").write_text(json.dumps({"node_types": [], "edge_types": []}))
```

to:

```python
    write_document()
    seed_schema_version("doc_raw", {"node_types": [], "edge_types": []})
```

- [ ] **Step 7: Fix `test_extract_drops_edges_with_unknown_node_ids`**

Change:

```python
    write_document()
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))
```

to:

```python
    write_document()
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)
```

- [ ] **Step 8: Fix `test_get_ontology_returns_saved_graph`**

Change:

```python
def test_get_ontology_returns_saved_graph():
    from app import graphdb
    nodes = [{"id": "n1", "label": "Alice", "type": "Person"}]
    edges = []
    graphdb.write_graph("doc_raw", nodes, edges)
    client = TestClient(app)
```

to:

```python
def test_get_ontology_returns_saved_graph():
    from app import graphdb
    seed_schema_version("doc_raw", DEFAULT_SCHEMA)
    nodes = [{"id": "n1", "label": "Alice", "type": "Person"}]
    edges = []
    graphdb.write_graph("doc_raw", nodes, edges)
    client = TestClient(app)
```

(`DEFAULT_SCHEMA` is already imported at the top of this file.)

- [ ] **Step 9: Fix `test_list_schemas_returns_stems_with_a_saved_schema`**

Change:

```python
def test_list_schemas_returns_stems_with_a_saved_schema():
    schema = {"node_types": [], "edge_types": []}
    for stem in ("doc_raw", "other_raw"):
        d = GRAPH_DIR / stem
        d.mkdir(parents=True)
        (d / "schema.json").write_text(json.dumps(schema))
    # a graph dir with no schema.json shouldn't be listed
    (GRAPH_DIR / "no_schema_raw").mkdir(parents=True)
```

to:

```python
def test_list_schemas_returns_stems_with_a_saved_schema():
    schema = {"node_types": [], "edge_types": []}
    for stem in ("doc_raw", "other_raw"):
        seed_schema_version(stem, schema)
    # a graph dir with no versions.json shouldn't be listed
    (GRAPH_DIR / "no_schema_raw").mkdir(parents=True)
```

- [ ] **Step 10: Fix `test_get_schema_returns_saved_schema`**

Change:

```python
def test_get_schema_returns_saved_schema():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    d = GRAPH_DIR / "doc_raw"
    d.mkdir(parents=True)
    (d / "schema.json").write_text(json.dumps(schema))
    client = TestClient(app)
```

to:

```python
def test_get_schema_returns_saved_schema():
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    seed_schema_version("doc_raw", schema)
    client = TestClient(app)
```

- [ ] **Step 11: Fix `test_use_schema_copies_source_schema_to_target`**

Change:

```python
def test_use_schema_copies_source_schema_to_target():
    write_document("target_raw.md")
    source_schema = {
        "node_types": [{"name": "Organization", "description": "an org"}],
        "edge_types": [],
    }
    source_dir = GRAPH_DIR / "source_raw"
    source_dir.mkdir(parents=True)
    (source_dir / "schema.json").write_text(json.dumps(source_schema))
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "source_raw"}
    )

    assert response.status_code == 200
    assert response.json() == source_schema
    saved = json.loads((GRAPH_DIR / "target_raw" / "schema.json").read_text())
    assert saved == source_schema
```

to:

```python
def test_use_schema_copies_source_schema_to_target():
    write_document("target_raw.md")
    source_schema = {
        "node_types": [{"name": "Organization", "description": "an org"}],
        "edge_types": [],
    }
    seed_schema_version("source_raw", source_schema)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/target_raw.md/schema/use", json={"source_stem": "source_raw"}
    )

    assert response.status_code == 200
    assert response.json() == {**source_schema, "version": 1}
    saved = json.loads((GRAPH_DIR / "target_raw" / "schema_v1.json").read_text())
    assert saved == source_schema
```

- [ ] **Step 12: Fix `test_files.py`'s two direct `schema.json` writes**

In `backend/tests/test_files.py`, change `test_list_documents_reports_original_filename_and_schema_and_graph_status`'s setup from:

```python
    schema_dir = GRAPH_DIR / "report_raw"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "schema.json").write_text(json.dumps({"node_types": [], "edge_types": []}))
```

to:

```python
    d = GRAPH_DIR / "report_raw"
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema_v1.json").write_text(json.dumps({"node_types": [], "edge_types": []}))
    (d / "versions.json").write_text(
        json.dumps(
            {"active_version": 1, "versions": [{"version": 1, "document_type": "general", "created_at": None}]}
        )
    )
```

And change `test_list_files_excludes_ladybugdb_files`'s setup from:

```python
    schema_dir = GRAPH_DIR / "doc_raw"
    schema_dir.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (schema_dir / "schema.json").write_text(json.dumps(schema))
```

to:

```python
    d = GRAPH_DIR / "doc_raw"
    d.mkdir(parents=True)
    schema = {"node_types": [{"name": "Person", "description": "a person"}], "edge_types": []}
    (d / "schema_v1.json").write_text(json.dumps(schema))
    (d / "versions.json").write_text(
        json.dumps(
            {"active_version": 1, "versions": [{"version": 1, "document_type": "general", "created_at": None}]}
        )
    )
```

- [ ] **Step 13: Fix `test_chat.py`'s single `write_graph_dir` helper**

In `backend/tests/test_chat.py`, change:

```python
def write_graph_dir(stem="doc_raw", schema=SCHEMA, nodes=NODES, edges=EDGES):
    graph_dir = GRAPH_DIR / stem
    graph_dir.mkdir(parents=True)
    (graph_dir / "schema.json").write_text(json.dumps(schema))
    graphdb.write_graph(stem, nodes, edges)
    return graph_dir
```

to:

```python
def write_graph_dir(stem="doc_raw", schema=SCHEMA, nodes=NODES, edges=EDGES):
    graph_dir = GRAPH_DIR / stem
    graph_dir.mkdir(parents=True)
    (graph_dir / "schema_v1.json").write_text(json.dumps(schema))
    (graph_dir / "versions.json").write_text(
        json.dumps(
            {"active_version": 1, "versions": [{"version": 1, "document_type": "general", "created_at": None}]}
        )
    )
    graphdb.write_graph(stem, nodes, edges)
    return graph_dir
```

This single change fixes all 4 tests that call `write_graph_dir()` (`test_chat_with_filename_injects_graph_context_and_returns_type_analysis`, `test_chat_reports_not_found_when_no_types_relevant`, `test_chat_falls_back_to_all_instances_when_no_keyword_match`, `test_chat_reports_not_found_when_determined_type_has_no_instances`) — no other edits needed in this file.

- [ ] **Step 14: Run the full backend suite**

```
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/ -v
```

Expected: PASS, every test in the suite.

- [ ] **Step 15: Commit**

```bash
cd backend && git add tests/test_ontology.py tests/test_files.py tests/test_chat.py
git commit -m "Adapt pre-existing tests to per-version schema file layout"
```

---

### Task 7: One-time migration script for the 4 real extracted documents

**Files:**
- Create: `backend/migrate_schema_versions.py`

**Interfaces:**
- Consumes: `graphdb._get_connection`, `graphdb._existing_tables`, `graphdb.reset_connection` (private functions, used deliberately here — a one-off script tightly coupled to graphdb internals, not a public API consumer), `ontology.GRAPH_DIR`.
- Produces: nothing consumed by other tasks — this is a standalone operational script, not imported anywhere.

This script has no automated test (it mutates the real, non-test `backend/data`, which `tests/conftest.py` deliberately isolates test runs from) — Task 9's manual verification step is what proves it works.

- [ ] **Step 1: Write the script**

```python
"""One-time migration: adds per-document schema versioning to existing
backend/data. Run once, with podman-compose stopped (this script and the
backend container must never have graph.ladybugdb open at the same time
-- see CLAUDE.md's virtiofs/WAL notes). Run ./scripts/backup_data.sh
first. Safe to re-run -- every step is a no-op if already applied.

Usage:
    cd backend && source .venv/bin/activate && python migrate_schema_versions.py
"""
import json

from app import graphdb
from app.ontology import GRAPH_DIR


def migrate_schema_files():
    if not GRAPH_DIR.is_dir():
        print("No graph directory found -- nothing to migrate.")
        return
    for stem_dir in sorted(GRAPH_DIR.iterdir()):
        if not stem_dir.is_dir():
            continue
        old_schema = stem_dir / "schema.json"
        versions_path = stem_dir / "versions.json"
        if versions_path.is_file():
            print(f"{stem_dir.name}: already migrated, skipping")
            continue
        if not old_schema.is_file():
            print(f"{stem_dir.name}: no schema.json, skipping")
            continue
        old_schema.rename(stem_dir / "schema_v1.json")
        versions_path.write_text(
            json.dumps(
                {
                    "active_version": 1,
                    "versions": [
                        {"version": 1, "document_type": "unknown", "created_at": None}
                    ],
                }
            )
        )
        print(f"{stem_dir.name}: schema.json -> schema_v1.json, wrote versions.json")


def migrate_graphdb():
    conn = graphdb._get_connection()
    tables = graphdb._existing_tables(conn)

    for name, kind in tables.items():
        columns = {
            row["name"]
            for row in conn.execute(f"CALL table_info('{name}') RETURN *").rows_as_dict()
        }
        if "version" not in columns:
            conn.execute(f"ALTER TABLE {name} ADD version INT64 DEFAULT 1")
            print(f"{name}: added version column (default 1)")
        else:
            print(f"{name}: already has version column, skipping")

        if kind == "NODE" and "original_id" not in columns:
            conn.execute(f"ALTER TABLE {name} ADD original_id STRING")
            rows = conn.execute(f"MATCH (n:{name}) RETURN n.id AS id").rows_as_dict()
            for row in rows:
                original_id = row["id"].rsplit("::", 1)[1]
                conn.execute(
                    f"MATCH (n:{name} {{id: $id}}) SET n.original_id = $original_id",
                    {"id": row["id"], "original_id": original_id},
                )
            print(f"{name}: added original_id column, backfilled {len(rows)} row(s)")
        elif kind == "NODE":
            print(f"{name}: already has original_id column, skipping")

    # _ExtractedDocument's primary key changes shape (stem -> a composite
    # "{stem}::v{version}" id), which ALTER TABLE can't do -- rebuild it
    # from its current rows.
    doc_columns = {
        row["name"]
        for row in conn.execute("CALL table_info('_ExtractedDocument') RETURN *").rows_as_dict()
    }
    if "id" in doc_columns:
        print("_ExtractedDocument: already migrated, skipping")
    else:
        existing_stems = [
            row["stem"]
            for row in conn.execute(
                "MATCH (d:_ExtractedDocument) RETURN d.stem AS stem"
            ).rows_as_dict()
        ]
        conn.execute("DROP TABLE _ExtractedDocument")
        conn.execute(
            "CREATE NODE TABLE _ExtractedDocument(id STRING PRIMARY KEY, stem STRING, version INT64)"
        )
        for stem in existing_stems:
            conn.execute(
                "CREATE (:_ExtractedDocument {id: $id, stem: $stem, version: 1})",
                {"id": f"{stem}::v1", "stem": stem},
            )
        print(
            f"_ExtractedDocument: rebuilt with composite key "
            f"({len(existing_stems)} document(s) preserved)"
        )

    graphdb.reset_connection()


if __name__ == "__main__":
    migrate_schema_files()
    migrate_graphdb()
    print("Migration complete.")
```

- [ ] **Step 2: Commit**

```bash
cd backend && git add migrate_schema_versions.py
git commit -m "Add one-time migration script for existing extracted documents"
```

(Running it against the real `backend/data` happens in Task 9, not here — this step only commits the script.)

---

### Task 8: Frontend — version management in the File Explorer modal

**Files:**
- Modify: `frontend/src/components/SettingsPanel.vue`

**Interfaces:**
- Consumes: `GET /api/ontology/{filename}/schema/versions`, `POST /api/ontology/{filename}/schema/versions/{version}/activate`, `DELETE /api/ontology/{filename}/schema/versions/{version}` (all from Task 5).
- No other component is touched — `OntologyGraph.vue`, `DocumentPreview.vue`, `ChatPanel.vue`, `SchemaGraphPreview.vue`, `App.vue` need no changes (they only ever call `GET /api/ontology/{filename}`, which already transparently resolves to the active version server-side).

There is no automated frontend test suite in this repo (per `CLAUDE.md`); this task's "test" is a manual browser check, done in Task 9.

- [ ] **Step 1: Add version-tracking state**

In `frontend/src/components/SettingsPanel.vue`'s `<script setup>`, add after the existing `const showConfigurations = ref(false)` line:

```js
const schemaVersions = ref([])
const versionActionError = ref('')
```

- [ ] **Step 2: Add `loadSchemaVersions` and the active-version label**

Add after the existing `loadSchemas`/`loadDocuments` functions:

```js
async function loadSchemaVersions() {
  if (!props.selectedFilename) {
    schemaVersions.value = []
    return
  }
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/versions`
    )
    const data = await res.json()
    schemaVersions.value = data.versions
  } catch (err) {
    // version list is best-effort; leave as-is on failure
  }
}

const activeVersionLabel = computed(() => {
  const active = schemaVersions.value.find((v) => v.is_active)
  return active ? `v${active.version} 활성` : ''
})
```

- [ ] **Step 3: Add `activateVersion`/`deleteVersion`**

Add after `useSchema`:

```js
async function activateVersion(version) {
  versionActionError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/versions/${version}/activate`,
      { method: 'POST' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadSchemaVersions()
    emit('graph-extracted')
  } catch (err) {
    versionActionError.value = '버전 활성화 실패: ' + err.message
  }
}

async function deleteVersion(version) {
  const confirmed = window.confirm(`v${version} 스키마와 그 그래프 데이터를 삭제하시겠습니까?`)
  if (!confirmed) return

  versionActionError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/versions/${version}`,
      { method: 'DELETE' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadSchemaVersions()
    emit('graph-extracted')
  } catch (err) {
    versionActionError.value = '버전 삭제 실패: ' + err.message
  }
}
```

`emit('graph-extracted')` reuses the existing event `App.vue` already listens to for "the active graph may have changed, refresh" — both activating and deleting a version can change what the active graph is, and this repo has no more specific event for that, so this is the correct existing hook rather than a new one.

- [ ] **Step 4: Wire up loading**

Add a `watch` near the existing ones (after the `toggleEdgeTypeRequest` watcher):

```js
watch(() => props.selectedFilename, loadSchemaVersions)
watch(() => props.schemaVersion, loadSchemaVersions)
```

And in `onMounted`, after the existing `await loadSchemas()`:

```js
  await loadSchemaVersions()
```

- [ ] **Step 5: Add the sidebar active-version indicator**

In the template, inside the `워크플로우` group's schema-generation `<section>`, change:

```html
      <section>
        <div class="workflow-row">
          <button
            type="button"
            class="workflow-button"
            :disabled="!selectedFilename || isGeneratingSchema"
            @click="generateSchema"
          >
            {{ isGeneratingSchema ? '생성 중...' : '2 스키마 생성' }}
          </button>
          <select v-model="schemaDocumentType" :disabled="isGeneratingSchema" class="schema-type-select">
            <option value="general">일반 문서</option>
            <option value="legal">법률·보험 문서</option>
          </select>
        </div>
      </section>
```

to:

```html
      <section>
        <div class="workflow-row">
          <button
            type="button"
            class="workflow-button"
            :disabled="!selectedFilename || isGeneratingSchema"
            @click="generateSchema"
          >
            {{ isGeneratingSchema ? '생성 중...' : '2 스키마 생성' }}
          </button>
          <select v-model="schemaDocumentType" :disabled="isGeneratingSchema" class="schema-type-select">
            <option value="general">일반 문서</option>
            <option value="legal">법률·보험 문서</option>
          </select>
        </div>
        <p v-if="activeVersionLabel" class="version-indicator">{{ activeVersionLabel }}</p>
      </section>
```

- [ ] **Step 6: Add the version-management section to the File Explorer modal**

In the `v-if="showFileExplorer"` overlay's body, insert a new `<section>` between the existing "업로드된 문서" section and the "스키마 라이브러리" section:

```html
          <section>
            <h3>선택된 문서의 스키마 버전</h3>
            <p v-if="!selectedFilename" class="placeholder">문서를 먼저 선택하세요</p>
            <p v-else-if="schemaVersions.length === 0" class="placeholder">생성된 버전이 없습니다</p>
            <ul v-else class="file-list">
              <li
                v-for="v in schemaVersions"
                :key="v.version"
                class="version-item"
                :class="{ active: v.is_active }"
              >
                <div class="version-item-main">
                  <span class="version-label">v{{ v.version }} · {{ v.document_type }}</span>
                  <span v-if="v.is_active" class="status-badge on">활성</span>
                  <span class="status-badge" :class="{ on: v.has_graph }">그래프</span>
                </div>
                <div class="version-item-actions">
                  <button
                    v-if="!v.is_active"
                    type="button"
                    class="version-action-button"
                    @click="activateVersion(v.version)"
                  >활성화</button>
                  <button
                    type="button"
                    class="version-action-button danger"
                    @click="deleteVersion(v.version)"
                  >삭제</button>
                </div>
              </li>
            </ul>
            <p v-if="versionActionError" class="error">{{ versionActionError }}</p>
          </section>
```

- [ ] **Step 7: Add the new CSS classes**

Add to the `<style scoped>` block, near the existing `.file-list`/`.status-badge` rules:

```css
.version-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
  font-size: 0.9rem;
}
.version-item.active {
  background: #dbe9ff;
}
.version-item-main {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  overflow-wrap: anywhere;
}
.version-label {
  font-weight: 600;
}
.version-item-actions {
  display: flex;
  gap: 0.3rem;
  flex-shrink: 0;
}
.version-action-button {
  padding: 0.2rem 0.5rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #f0f0f0;
  color: #333;
  font-size: 0.75rem;
  cursor: pointer;
}
.version-action-button:hover {
  background: #e4e4e4;
}
.version-action-button.danger {
  border-color: #c0392b;
  color: #c0392b;
  background: #fff;
}
.version-action-button.danger:hover {
  background: #fdecea;
}
.version-indicator {
  color: #555;
  font-size: 0.8rem;
  margin: 0.2rem 0 0;
}
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/SettingsPanel.vue
git commit -m "Add schema version management to the File Explorer modal"
```

---

### Task 9: Run the real migration, rebuild the stack, and verify end to end

**Files:** none (operational verification only)

**Interfaces:** none — this is the final integration check tying every prior task together against the real `backend/data` and a running browser.

- [ ] **Step 1: Back up real data**

```bash
./scripts/backup_data.sh
```

Expected: a new `backups/backend-data_<timestamp>.tar.gz` is created.

- [ ] **Step 2: Stop the stack before touching the real database file**

```bash
podman-compose down
```

- [ ] **Step 3: Run the migration script against real data**

```bash
cd backend && source .venv/bin/activate && python migrate_schema_versions.py
```

Expected output: one line per existing `backend/data/graph/{stem}` directory reporting the `schema.json` → `schema_v1.json` rename, one line per existing NODE/REL table reporting the `version`/`original_id` column additions, and a final `_ExtractedDocument: rebuilt with composite key (4 document(s) preserved)` line (or however many documents currently exist).

- [ ] **Step 4: Spot-check the migrated data**

```bash
cd backend && source .venv/bin/activate && python -c "
from app import graphdb
conn = graphdb._get_connection()
for row in conn.execute('MATCH (d:_ExtractedDocument) RETURN d.id, d.stem, d.version').rows_as_dict():
    print(row)
"
```

Expected: one row per document, each `id` shaped like `{stem}::v1`, `version` = 1.

- [ ] **Step 5: Rebuild and start the stack**

```bash
podman-compose up --build -d
```

- [ ] **Step 6: Verify a pre-existing document's chat/graph still works post-migration**

Open `http://localhost:5173`, select one of the 4 pre-existing documents, confirm its graph still renders in `OntologyGraph.vue` (proves `load_graph`/`get_active_version` correctly resolve migrated data) and ask it a question in chat that should hit an edge match or hop expansion (proves the `original_id`-based lookup fix from Task 2 actually works against real migrated rows, not just the synthetic regression test).

- [ ] **Step 7: Verify the new version UI**

Click "1 파일 선택", select a document, confirm the new "선택된 문서의 스키마 버전" section shows its (migrated) v1. Click "2 스키마 생성" to create a v2, reopen the file explorer, confirm both v1 and v2 appear with v2 marked active. Click "활성화" on v1, confirm the label flips. Click 삭제 on v2, confirm it disappears and v1 becomes active again.

- [ ] **Step 8: Run the full backend suite one more time as a final regression check**

```bash
cd backend && OPENROUTER_API_KEY=dummy python -m pytest tests/ -v
```

Expected: PASS, full suite (this only re-confirms Task 6's work — included here as the final gate before calling the feature done).
