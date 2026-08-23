# LadybugDB Graph Storage

## Goal

Replace `nodes.json`/`edges.json` as the storage and query layer for
extracted ontology graphs with LadybugDB (an actively-maintained
community fork of Kùzu — an embedded, Cypher-native property graph
database), and rewrite GraphRAG's instance search and hop expansion as
real Cypher queries instead of an in-memory `networkx` graph.

This is explicitly a learning/testing exercise for Cypher and embedded
graph-DB integration, not a response to a scaling problem — the current
per-document graphs are small (tens to low hundreds of nodes). The
design is sized accordingly: real DDL and real Cypher queries where
that's the point of the exercise, but no speculative features (no new
cross-document chat UI, no schema garbage collection) beyond what the
stated goal requires.

## Why not just swap the storage backend transparently

The obvious minimal change is to keep `load_graph()`/`save_graph()`
returning the same `{"nodes": [...], "edges": [...]}` shape, backed by
LadybugDB instead of JSON, and leave `graphrag.py`'s `networkx`-based
search untouched. That would make LadybugDB a JSON replacement in name
only — none of the actual value (or actual practice) of writing Cypher
queries. So the design instead pushes GraphRAG's type-filtered instance
search and hop expansion down into Cypher (see "GraphRAG rewrite"
below), while keeping the "give me the whole graph" read path
(`load_graph`, used by `GET /api/ontology/{filename}`) as a thin
Cypher-backed replacement, since that operation has no interesting
query shape either way.

## Architecture

- New module `backend/app/graphdb.py`, following the existing pattern
  where each concern owns its own external dependency (`chat.py` owns
  `ChatOpenAI`, `telemetry.py` owns the OTel tracer) — `graphdb.py` owns
  the LadybugDB connection, DDL sync, and all read/write queries.
  `ontology.py` and `graphrag.py` import from it; neither talks to
  LadybugDB directly.
- LadybugDB is embedded (no server process), so no new podman-compose
  service. The database lives at `backend/data/graph.ladybugdb/` — one
  directory, shared across all documents (not one per document — see
  "Shared database" below).
- `schema.json` stays a plain JSON file per document, unchanged
  (`backend/data/graph/{stem}/schema.json`) — it's type *metadata*, not
  graph data, and doesn't benefit from a graph DB.
- API contract is unchanged: `GET /api/ontology/{filename}` still
  returns `{"nodes": [...], "edges": [...]}` in the same shape as
  today. No frontend changes.

## Shared database, per-type tables

All documents' nodes/edges live in one LadybugDB database rather than
one per document, specifically so the design has to deal with
cross-document type modeling — again, in service of the "test this
properly" goal, not because cross-document queries are a near-term
feature. Each node type and edge type becomes a **real table**, not a
generic `Entity`/`RELATION` catch-all, so the exercise involves actual
Cypher DDL rather than just parameterized inserts against a fixed
shape:

- Node tables: `CREATE NODE TABLE {type}(id STRING PRIMARY KEY, label STRING, detail STRING, source_document STRING)`
- Edge tables: `CREATE REL TABLE GROUP {type}(FROM {source} TO {target}, type STRING, detail STRING, source_document STRING)`

Every node table has the same four columns; every edge table has the
same three (plus its FROM/TO pair). This is the key move that avoids
cross-document schema conflicts: two documents can both produce a
`Person` type, or two different `WORKS_AT` edges with different
source/target pairs, without any property-shape reconciliation, because
the shape is fixed in advance.

**Node ID collisions**: node IDs are LLM-assigned per document (`n1`,
`n2`, ...) and are not globally unique. `graphdb.py` stores them as
`f"{stem}::{original_id}"` and strips the prefix back off in any
response that reaches `ontology.py`/`graphrag.py` callers, so nothing
outside `graphdb.py` ever sees the prefixed form.

### Schema sync (on every extraction)

Before writing a document's nodes/edges, `graphdb.sync_schema(schema)`:

1. For each `node_types` entry not yet a table: `CREATE NODE TABLE`.
2. For each `edge_types` entry (`name`, `source`, `target`):
   - Table doesn't exist yet: `CREATE REL TABLE GROUP` with that one
     (source, target) pair.
   - Table exists but this (source, target) pair isn't registered on
     it yet: `ALTER TABLE {name} ADD FROM {source} TO {target}`.
   - Otherwise: no-op.

No column-level conflicts are possible since every table's shape is
fixed, so sync never needs `ALTER TABLE ... ADD COLUMN`.

### Re-extraction

Clicking "그래프 추출" again for the same document must not accumulate
duplicate/stale rows. Before inserting, delete this document's existing
rows: `DELETE FROM {each known table} WHERE source_document = $stem`
(node tables) and equivalently for edge tables, then insert. Delete +
insert for one document's full graph happens inside a single
transaction — LadybugDB supports ACID transactions, so a failure
midway leaves the previous extraction intact instead of the
partially-overwritten state possible today with two independently
sequential JSON file writes. This is a natural side benefit of the
storage change, not separate scope.

### Known limitation (accepted, not implemented)

Re-extracting a document under a *different* schema (different type
names) leaves the old type's table behind, now with zero rows for that
document (and possibly zero rows total, if no other document uses that
type). No DDL garbage collection (`DROP TABLE`) is implemented — out of
scope for a learning/testing exercise. Documented here so it isn't
mistaken for a bug later.

## GraphRAG rewrite (`backend/app/graphrag.py`)

`determine_relevant_types()` and `extract_keywords()` are unchanged —
both are pure LLM calls with no graph storage involvement.

`search_graph(question, schema, stem, hops)` changes its third
parameter from a preloaded `graph_data` dict to `stem`, and queries
LadybugDB directly instead of building a `networkx.DiGraph` from JSON:

- **Type-filtered instance search** (replacing `find_relevant_nodes`/
  `find_matching_edges`): one `MATCH (n:{type}) WHERE n.source_document
  = $stem AND (...)` query per allowed type, results combined in
  Python. Kept as one query per type rather than a single query across
  heterogeneous node tables, since Kùzu-family Cypher's support for
  matching across multiple node-table labels in one pattern is a detail
  to confirm against LadybugDB specifically during implementation.
- **Fallback to all instances of a type** (replacing
  `all_nodes_of_types`/`all_edges_of_types`): same per-type query
  without the keyword filter. Unchanged behavior/semantics from today
  — only the mechanism (Cypher vs. Python list comprehension) changes.
- **Hop expansion** (replacing `nx.ego_graph`): a variable-length
  Cypher pattern, `MATCH (n)-[*1..{hops}]-(m) WHERE n.id IN $seed_ids
  AND m.source_document = $stem RETURN DISTINCT m`, followed by a
  second query to fetch the edges among the resulting node set (needed
  to build the `Relations:` context lines). `hops` is inlined into the
  query text rather than bound as a parameter, since Cypher-family
  variable-length range bounds generally can't be parameterized; this
  is safe only because `hops` is validated as an int in `1..5` — see
  "Backend hop validation" below.
- Context text formatting (`Entities:`/`Relations:` blocks, including
  the `detail` field when present) is unchanged — same output shape,
  same chat prompt contract.

`load_graph(stem)` / `save_graph(stem, graph)` in `ontology.py` keep
their current signatures and JSON-shaped return values. `load_graph`
becomes a "give me everything for this document" Cypher query across
all known tables, scoped to `source_document`; `save_graph` becomes the
schema-sync + delete + insert sequence described above. Existing
callers (`main.py`'s `GET /api/ontology/{filename}` and the extraction
endpoint) need no changes.

`main.py`'s chat handler drops its `graph_data = load_graph(stem)`
existence check in favor of a cheap `graphdb.has_graph(stem)` query,
and passes `stem` (not `graph_data`) into `search_graph()`.

### Backend hop validation

`ChatRequest.hops` is currently trusted as-is by the backend (only the
frontend clamps it to 1–5). Since `hops` now gets string-interpolated
into a Cypher query, the backend must also clamp/validate it
(`max(1, min(5, hops))`) before use — a small correctness fix that
becomes a real safety requirement once the value reaches query text,
not scope creep.

## Error handling

Unchanged philosophy: `main.py` catches only `ValueError` around
`search_graph()` (the existing LLM-JSON-parse-failure case) and falls
back to plain chat. A LadybugDB technical failure (corrupt database
file, disk error) is a different kind of failure from "no match found"
and is not caught — it propagates as an unhandled exception, consistent
with how the existing code already distinguishes "genuinely broken"
from "nothing relevant found."

## Testing

Same principle already used for file-backed tests in this codebase
(`CLAUDE.md`: "file tests use the real filesystem") — `graphdb.py`
tests use a real LadybugDB instance in a temp directory per test, not a
mock. Cover: schema sync (table creation, `ALTER TABLE ADD FROM/TO` for
a new pair on an existing edge type), write + read round-trip, deleting
and re-extracting a document, hop expansion returning the right node
set, and the node-ID-prefix stripping at the `graphdb.py` boundary.
`graphrag.py`'s existing LLM-call tests (`determine_relevant_types`,
`extract_keywords`) are unaffected; its instance-search/hop-expansion
tests move from constructing an in-memory `graph_data` dict to seeding
a real temp LadybugDB.

## Open implementation-time question

The exact PyPI package name and import surface for LadybugDB needs
confirming against `docs.ladybugdb.com` at implementation time — search
results turned up some naming ambiguity (a pre-existing unrelated
`ladybug` package on PyPI from the building-performance/AEC space).
LadybugDB's Python API is documented as intentionally
kuzu-compatible, so the fallback if the primary package name doesn't
resolve cleanly is to check for a `kuzu`-named compatibility shim.

## Out of scope

- No new user-facing cross-document query feature (no UI, no new
  endpoint) — the shared-database design *enables* cross-document
  Cypher queries but none are built here.
- No DDL garbage collection for orphaned type tables (see "Known
  limitation" above).
- No migration tool for existing `nodes.json`/`edges.json` data already
  on disk — documents need to be re-extracted after this change ships.
- No change to the schema-generation endpoint or `schema.json` format.
