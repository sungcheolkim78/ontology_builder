# Per-Document Schema Versioning

## Goal

Today, a document has exactly one schema and one extracted graph at a
time: regenerating a schema overwrites `schema.json`, and re-extracting
overwrites the document's rows in LadybugDB (`write_graph`'s
DETACH-DELETE-then-insert). This spec lets **one document hold multiple
schema versions simultaneously**, each with its own extracted
NODE/REL data, so a user can:

1. Iterate on a schema (regenerate, re-extract, compare) without losing
   the previous attempt.
2. Keep multiple deliberately different schemas for the same document
   (e.g. a "general" pass and a "legal" pass) coexisting, not just the
   last one applied.

Chat/GraphRAG stays scoped to a single **active version** per document
— it never searches across versions. This is a deliberate scope
boundary, not a limitation to fix later: cross-version search was
explicitly ruled out during design (see brainstorming discussion) as
adding response latency and ambiguity with no requested use case.

## Storage: shared type tables + a `version` column

Two storage shapes were considered:

- **(Rejected) Version baked into the table name** (e.g.
  `Person__v1`, `Person__v2`). Rejected because the DB already shares
  one table per type *name* across all documents (see
  `2026-08-23-ladybugdb-graph-storage-design.md`) — with only 4
  documents and no versioning yet, the catalog already has 30+ node
  tables and 42 REL tables. Multiplying that by version count causes
  unbounded catalog growth, requires re-deriving `ALTER TABLE ADD
  FROM...TO` bookkeeping per version instead of per type, and turns
  version deletion into `DROP TABLE` housekeeping instead of a row
  delete.
- **(Chosen) A `version INT64` column** added to every existing
  NODE/REL table, alongside the existing `source_document` column.
  Every query that currently filters on `source_document = $stem` gains
  `AND version = $version`. This extends the existing per-document
  filtering pattern by one dimension instead of introducing a new
  storage axis.

### DDL changes (`graphdb.py`)

```sql
CREATE NODE TABLE {type}(
  id STRING PRIMARY KEY, original_id STRING, label STRING, detail STRING,
  source_document STRING, version INT64, embedding FLOAT[{EMBEDDING_DIM}]
)

CREATE REL TABLE GROUP {etype}(
  FROM {src} TO {dst},
  type STRING, detail STRING, source_document STRING, version INT64
)
```

`original_id` holds the bare LLM-assigned id (`"n1"`, `"n2"`, ...)
exactly as extracted, untouched by whatever the PRIMARY KEY `id` column
does for uniqueness. This split is what makes the ID scheme below
correct — see "Node/edge ID scheme."

`_ExtractedDocument` (today: `id=stem STRING PRIMARY KEY`, one row per
document) becomes one row **per (stem, version)**:

```sql
CREATE NODE TABLE _ExtractedDocument(
  id STRING PRIMARY KEY,   -- f"{stem}::v{version}"
  stem STRING, version INT64
)
```

`has_graph(stem, version)` matches on the composite `id`.

### Node/edge ID scheme

**Correction found during implementation planning (not in the
originally-approved storage sketch above): a plain `rsplit`-based fix
on the read side alone is insufficient and was found to be an actual
bug — see below.**

The PRIMARY KEY `id` column exists purely so LLM-assigned ids (`n1`,
`n2`, ...), which are not globally unique across documents/versions
sharing a type table, can be stored without collisions:
`f"{stem}::v{version}::{original_id}"` for every write from this
change onward (including version 1 — this isn't optional per version,
since two versions of the same document can independently extract a
node they both happen to call `n1`).

Every node row also carries a plain `original_id STRING` column
holding the bare LLM-assigned id, untouched. **All application-level
node lookups go through `original_id` (plus `source_document` and
`version`), never through parsing or reconstructing the PRIMARY KEY
`id` string.** This matters in two directions:

- **Reading a row back into `{"id": ..., "label": ..., ...}` shape**
  (`_node_from_row`): return `row["original_id"]` directly. No string
  parsing of `id` at all.
- **Looking a set of already-known bare ids back up** (`find_matching_edges`'
  `matched_node_ids` param, `expand_hops`' `seed_ids` param): filter
  with `WHERE n.original_id IN $ids AND n.source_document = $stem AND
  n.version = $version`, never by reconstructing an `f"{stem}::v{version}::{id}"`
  string and matching it against `n.id`.

The earlier draft of this spec proposed the second direction as "just
rebuild the same prefixed string and match against `id`," relying on
`rsplit` only on the read-back side. That's a real bug against
migrated data: existing rows (see Migration) keep their *legacy*
2-part id (`f"{stem}::{original_id}"`, no version segment) rather than
being rewritten, so reconstructing a *3-part* prefixed string to match
against `a.id`/`n.id` would never match those rows — `find_matching_edges`
and `expand_hops` would silently find nothing for any of today's 4
documents until each is re-extracted. Matching via `original_id`
instead sidesteps the legacy/current format distinction entirely: it's
a plain column equality check, independent of whatever shape the
PRIMARY KEY happens to have.

REL tables have never had an `id` column of their own (edges are
matched by type/endpoints, not an id) — but every query that returns
an edge also returns the node ids on either end (`a.id AS source, b.id
AS target` today). Those change to `a.original_id AS source,
b.original_id AS target`, so `_edge_from_row` also drops its
`rsplit`/parsing entirely and just uses `row["source"]`/`row["target"]`
as-is.

### Function signature changes (`graphdb.py`)

Every public function gains a `version: int` parameter next to `stem`,
and threads it into every `source_document`/`version` filter:
`write_graph`, `update_node_embeddings`, `load_graph`, `has_graph`,
`find_relevant_nodes`, `find_similar_nodes`, `all_nodes_of_types`,
`find_matching_edges`, `all_edges_of_types`, `expand_hops`.

## Schema file storage (`ontology.py`)

Flat per-version files, no subdirectories (per explicit request):

```
backend/data/graph/{stem}/
  manifest.json          -- unchanged: original_filename
  versions.json          -- new: active version + per-version metadata
  schema_v1.json
  schema_v2.json
  schema_v3.json
  ...
```

`versions.json` shape:

```json
{
  "active_version": 3,
  "versions": [
    {"version": 1, "document_type": "general", "created_at": "2026-08-20T10:00:00"},
    {"version": 2, "document_type": "legal", "created_at": "2026-08-25T09:00:00"},
    {"version": 3, "document_type": "legal", "created_at": "2026-08-28T11:00:00"}
  ]
}
```

**Version ID display**: the "file code + version" identifier the user
asked for is a *derived display value*, `f"{stem}::v{version}"` —
computed on demand for API responses/logs, not stored as its own DB
column. The DB itself keeps `source_document` (stem) and `version`
(int) as two separate columns so filters stay simple integer/string
equality rather than parsing a composite string per query.

**Version creation rule**: every `POST .../schema` call creates
`active_version + 1` and activates it immediately — it never
overwrites an existing version. `POST .../schema/use` (copy another
document's schema) follows the same rule: it copies the *source
document's active version's* schema into a new version of the target
document, then activates that new version.

**Version deletion**: removes `schema_v{n}.json` and DETACH DELETEs
every row with `source_document=stem AND version=n` across all known
tables, plus the matching `_ExtractedDocument` row. If the deleted
version was active, the most recently created remaining version
becomes active; if no versions remain, the document has no active
version (schema-dependent endpoints 404 until a new schema is
generated).

`ontology.py` functions (`save_schema`, `load_schema`,
`list_schema_stems`, `save_graph`, `load_graph`, `embed_graph`) gain a
`version` parameter (or, for creation, return the new version number)
and read/write the file names above instead of the single
`schema.json`.

## API changes (`main.py`)

**Changed:**
- `POST /api/ontology/{filename}/schema` — always creates+activates a
  new version; response includes `version`.
- `POST /api/ontology/{filename}/schema/use` — copies the source
  document's *active* schema as a new version of the target document
  (was: overwrite).
- `POST /api/ontology/{filename}/extract`, `POST
  /api/ontology/{filename}/embed`, `GET /api/ontology/{filename}`, `GET
  /api/ontology/{filename}/schema` — unchanged request shape; now
  implicitly operate on the document's current active version.

**New:**
- `GET /api/ontology/{filename}/schema/versions` — list of
  `{version, document_type, created_at, has_graph, is_active}`.
- `POST /api/ontology/{filename}/schema/versions/{version}/activate`
  — switches the active version.
- `DELETE /api/ontology/{filename}/schema/versions/{version}` — see
  deletion rule above.

`GET /api/documents`' `has_schema`/`has_graph` flags become "does an
active version exist and does it have a graph," not "does the version-1
concept exist" — i.e. still document-level booleans, now backed by
`versions.json`/`active_version`.

## Frontend changes (`SettingsPanel.vue`)

**File Explorer modal ("1 파일 선택")** gains a new section, **"선택된
문서의 스키마 버전,"** placed after "업로드된 문서" and before "스키마
라이브러리" (both existing sections are otherwise unchanged). It
reacts to `selectedFilename` (the modal stays open after selecting a
file, so this is visible immediately):

- One row per version: version number, `document_type`, created time,
  a 그래프 추출 여부 badge, and a ★/"활성" marker on the active version.
- Clicking a non-active row's "활성화" button calls the activate
  endpoint and refreshes the document's graph/schema views.
- Each row has a delete button; confirms, then calls the delete
  endpoint.

**Left sidebar workflow section** — the existing "2 스키마 생성" button
gains a small active-version indicator nearby (e.g. "v3 활성"), so the
user has some indication of state without opening the modal. No other
sidebar change.

**Configurations modal** — untouched by this change (LLM 모델/채팅
표시/GraphRAG/스키마 생성 설정/DB 관리 stay as they are).

**Everything else** (`OntologyGraph.vue`, `DocumentPreview.vue`,
`ChatPanel.vue`, `SchemaGraphPreview.vue`) is unchanged: they only ever
call `GET /api/ontology/{filename}`, which now transparently resolves
to "the active version's graph" — none of them need a version concept.

## Migration (existing 4 documents)

Run once, as a standalone script (e.g.
`backend/scripts/migrate_schema_versions.py`), against the real
`backend/data` — **not** baked into `graphdb._get_connection()`'s hot
path, since this only ever needs to run once on this developer's
machine. `./scripts/backup_data.sh` should be run immediately before,
per existing project practice for anything risky to `backend/data`.

Steps:

1. For every existing NODE/REL table except `_ExtractedDocument`:
   `ALTER TABLE {t} ADD COLUMN version INT64 DEFAULT 1`. Every existing
   row becomes version 1; the PRIMARY KEY `id` string is never
   rewritten (it keeps its legacy 2-part shape, `f"{stem}::{original_id}"`,
   indefinitely). NODE tables additionally get `ALTER TABLE {t} ADD
   COLUMN original_id STRING`, then backfilled **in Python, one row at
   a time** rather than a single Cypher update expression (safer than
   relying on an unverified Cypher string-splitting function in
   LadybugDB's dialect, and mirrors the existing per-row `SET` loop
   pattern `update_node_embeddings` already uses): read back every
   row's `id` via a plain `MATCH (n:{t}) RETURN n.id`, compute
   `original_id = id.rsplit("::", 1)[1]` in Python (safe here
   specifically because at this point in time every existing row is
   still 2-part), then `MATCH (n:{t}) WHERE n.id = $id SET
   n.original_id = $original_id` per row.
2. Rebuild `_ExtractedDocument`: read its current rows (`stem`), then
   `DROP TABLE`/`CREATE TABLE` with the new `(id, stem, version)`
   shape, and reinsert one row per existing stem with `version=1`,
   `id=f"{stem}::v1"`.
3. For each `backend/data/graph/{stem}/` directory: rename
   `schema.json` → `schema_v1.json`; write `versions.json` with
   `active_version: 1` and a single version entry
   `{"version": 1, "document_type": "unknown", "created_at": null}`
   (today's `schema.json` never recorded `document_type`, so this is
   the honest value for pre-migration data).

This migration is a one-time developer action, not a startup
auto-migration — there is no code path that detects "unmigrated data"
and repairs it automatically. If it's ever needed again (e.g. a fresh
clone with old-format data), rerun the script manually.

## Error handling

Unchanged philosophy from the existing LadybugDB design: a missing
active version (deleted all versions, or a document that never had a
schema) surfaces as a 404 from the relevant endpoint, the same way a
missing graph does today — not a new error category. Deleting a
version that doesn't exist, or activating one that doesn't exist, is
also a 404.

## Testing

- `graphdb.py`: extend existing fixtures to write/read multiple
  versions of the same stem and assert row isolation (`version=1`'s
  rows are untouched by writing `version=2`), the `_ExtractedDocument`
  composite-key behavior, and that node/edge lookups resolve correctly
  through `original_id` regardless of the underlying PRIMARY KEY `id`
  format (legacy 2-part vs. current 3-part) — this is the specific
  behavior the mid-planning correction above exists to get right.
- `ontology.py`: version file naming (`schema_v{n}.json`),
  `versions.json` read/write, active-version switching, and deletion
  (including "delete the active version, most recent remaining becomes
  active" and "delete the last version, no active version remains").
- `main.py`: new version-list/activate/delete endpoints; existing
  extract/embed/schema endpoints continue to pass against whichever
  version is active.
- No changes needed to `graphrag.py`'s own tests beyond passing the
  (now-required) `version` argument through — its search logic is
  unaffected by versioning itself.

## Out of scope

- No cross-version GraphRAG search (chat always searches exactly one
  active version — see Goal).
- No version diffing/comparison UI.
- No cap on the number of versions a document can accumulate — the
  user manages this manually via the delete button.
- No change to the Configurations modal.
- No automatic migration/repair path beyond the one-time script.
