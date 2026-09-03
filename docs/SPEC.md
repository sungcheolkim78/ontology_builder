# Ontology Builder — Spec

Chatbot + GraphRAG + custom ontology schema system. This document
describes the system as currently implemented: a FastAPI backend, a
Vue 3 dashboard frontend, and a podman-compose dev environment.

## Architecture

```
┌──────────────────────────┐      ┌───────────────────────────┐
│   frontend (Vue 3/Vite)  │◄────►│   backend (FastAPI)        │
│   :5173, dev server      │ HTTP │   :8000, uvicorn --reload  │
│   /api/* proxied →       │      │                             │
└──────────────────────────┘      └───────┬────────────────────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    ▼                      ▼                      ▼
             OpenRouter API           anydoc (Rust)         backend/data/
             (via langchain,         doc → markdown       documents/{stem}/raw.md,
          chat + schema/extract)                      documents/{stem}/schema_v{N}.json,
                                              graph/graph.ladybugdb (nodes/edges)
```

Both services run as separate containers via `podman-compose.yml`,
each with source volume-mounted for hot-reload during development.

## Development workflow

For any non-trivial feature (new endpoint, new data layout, new UI
behavior — not a one-line fix), write a short spec first, then
implement test-first (TDD): write a failing test, make it pass, repeat.
`docs/data-layout-proposal.md` and `docs/chunk-view-spec.md` are the
precedent for the spec step — a focused doc covering what's changing,
why, and the concrete before/after, written *before* touching code, not
after. The test-first step means backend changes go through
`backend/tests/` (pytest, see "Tests" below) and frontend logic/component
changes go through `frontend/src/**/__tests__/` (Vitest, see "Frontend
tests" below) — write the test against the not-yet-existing behavior,
watch it fail, then implement.

## Backend (`backend/`)

FastAPI app in `app/main.py`, split into `app/chat.py` (LLM chat),
`app/parser.py` (document → markdown conversion), `app/graphdb.py`
(LadybugDB connection + Cypher-backed node/edge storage and search),
`app/ontology.py` (schema generation + node/edge extraction),
`app/graphrag.py` (keyword extraction + graph-based retrieval for chat),
and `app/telemetry.py` (OpenTelemetry tracing for every LLM call).

`app/graphdb.py` owns the single LadybugDB connection at
`backend/data/graph/graph.ladybugdb` (opened lazily, cached at module
level) — deliberately placed inside the `graph/` subdirectory rather
than directly in `backend/data/`, since `GET /api/files` lists
everything directly in `backend/data/` as a user document, and the
`.ladybugdb` file (plus its `.wal` sidecar) would otherwise show up as
fake documents and crash `GET /api/files/{name}` (binary content read
as UTF-8 text).
Storage is one Cypher node table and one Cypher rel table per distinct
node/edge *type name*, shared across every document rather than
per-document — each row carries a `source_document` property, and
every read/write function filters on it so one document's data doesn't
leak into another's even though they may share a table. Node/edge type
names come from LLM output, so anywhere a type name is interpolated
into DDL or a Cypher label, it first passes `_validate_identifier()`
(a safe `[A-Za-z_][A-Za-z0-9_]*` check) — a defense against LLM output
that isn't a legal Cypher identifier. Node ids are stored internally as
`{stem}::{id}` (globally unique across documents sharing a type table)
and stripped back to the bare id before returning from any function.
A database with zero REL tables at all (a fresh database, or every
document written so far had zero edges) breaks an untyped relationship
pattern match in two different ways — it can raise, or silently return
nothing — so `load_graph`, `find_matching_edges`, `all_edges_of_types`,
and `expand_hops` each check table existence first and take a
REL-table-free path rather than relying on the query to fail safely.

### Telemetry

`app/telemetry.py` exports `invoke_with_telemetry(operation, model, prompt)`,
a drop-in replacement for `model.invoke(prompt)` used at all five LLM
call sites (`chat.answer` in `main.py`; `ontology.generate_schema` and
`ontology.extract_graph`; `graphrag.determine_types` and
`graphrag.extract_keywords`). Each call wraps a span named `llm.{operation}`
recording `gen_ai.request.model`, `gen_ai.prompt.length`,
`gen_ai.response.length`, `gen_ai.call.success`, and
`gen_ai.usage.{input,output}_tokens` when the provider returns them —
metadata only, never the prompt/response text itself. Span duration is
automatic (OpenTelemetry records start/end time on every span; Jaeger's
UI displays it without any manual tracking). On an exception, the span
records it and sets an error status before the exception is re-raised
unchanged — tracing never swallows or alters application errors.

`invoke_with_telemetry` also retries on
`langchain_core.exceptions.ModelConnectionError` (the provider-agnostic
base class langchain raises for connection-level failures, e.g. the
`OpenAIConnectionError` langchain-openai raises for a dropped OpenRouter
connection) up to `max_retries` times (default 2) with a fixed
`retry_delay` (default 1.0s) between attempts, recording
`gen_ai.retry.count` on the span either way. Any other exception type is
raised immediately with no retry. This exists because a transient
OpenRouter connection error was observed in practice during a real
`extract_graph` call — not a hypothetical failure mode.

`configure_telemetry()` (called once at import time in `main.py`) only
registers a real `TracerProvider` + OTLP HTTP exporter if
`OTEL_EXPORTER_OTLP_ENDPOINT` is set in the environment; otherwise the
OpenTelemetry API's built-in no-op tracer stays active, so
`invoke_with_telemetry` is always safe to call — in particular, it adds
no network calls and negligible overhead when running tests locally
(outside podman-compose, where that env var is never set).

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check → `{"status": "ok"}` |
| GET | `/api/hello` | Scaffold sample endpoint |
| GET | `/api/config` | Current LLM model name + auth status → `{"model": "...", "auth_required": bool}` |
| POST | `/api/login` | `{"password": "..."}` → `{"token": "..."}`, or 401 |
| POST | `/api/chat` | Chat with the LLM |
| POST | `/api/parse` | Upload a document, convert to markdown |
| GET | `/api/files` | List parsed documents, newest first |
| GET | `/api/files/{filename}` | Read back a saved markdown file |
| GET | `/api/documents` | List documents with converter/size/summary/chunk/schema/graph status |
| POST | `/api/documents/{filename}/chunk` | Split `raw.md` into per-article JSON chunks |
| GET | `/api/documents/{filename}/chunk` | Read back the saved chunks |
| POST | `/api/documents/{filename}/summary` | LLM generates a short document summary |
| GET | `/api/documents/{filename}/summary` | Read back the saved summary |
| GET | `/api/ontology/schemas` | List every document stem that has a saved schema |
| POST | `/api/ontology/{filename}/schema` | LLM proposes a node/edge type schema for the document |
| POST | `/api/ontology/{filename}/schema/use` | Copy another document's schema onto this one |
| GET | `/api/ontology/{filename}/schema` | Read back the saved schema |
| POST | `/api/ontology/{filename}/extract` | LLM extracts nodes/edges per the saved (or default) schema |
| GET | `/api/ontology/{filename}` | Read back the saved nodes/edges |

**`POST /api/chat`** — body
`{"messages": [{"role": "user"|"assistant"|"system", "content": "..."}], "filename": "...", "hops": 1}`.
`filename`/`hops` are optional; `hops` is clamped server-side to `1..5`
regardless of what's sent (the frontend's number input already
restricts it to that range, but the backend no longer trusts that).
Plain chat (no `filename`, or the
document has no schema/graph yet) works exactly as it always has:
messages go straight to `ChatOpenAI`. When `filename` names a document
that has *both* a schema and an extracted graph, `graphrag.search_graph()`
runs a schema-aware, three-step search before the chat call:

1. **Type analysis** — `determine_relevant_types()` sends the whole
   schema plus the question to the LLM, asking which `node_types`/
   `edge_types` (by exact name, from the schema) are relevant. Any
   name the LLM returns that isn't actually in the schema is dropped
   (defends against hallucinated types). If *both* lists come back
   empty, the search stops here — no keyword extraction, no second LLM
   call.
2. **Instance search, per node type** — `extract_keywords()` (a
   separate LLM call) returns terms grouped by which node type each
   one is an instance of (e.g. `{"Person": ["Ada Lovelace"]}`), not a
   flat list — so a term extracted for one type can never match a node
   of a different type just because the label happens to overlap.
   For each node type determined relevant in stage 1, the search tries,
   in order, until one of them produces a match:
   a. **Keyword match** — `find_relevant_nodes()` matches that type's
      own keywords against that type's node labels (case-insensitive
      substring, either direction).
   b. **Embedding similarity** — if (a) found nothing (no keyword was
      extracted for this type, or it matched no label), `find_similar_nodes()`
      ranks that type's own nodes by cosine similarity between the
      question's embedding and each node's embedding (`label` +
      `detail` text, embedded once at extraction time via
      `OPENROUTER_EMBEDDING_MODEL`, default `openai/text-embedding-3-small`,
      stored as a `FLOAT[1536]` column on each node row), keeping the
      top `EMBEDDING_FALLBACK_TOP_K` (5). This is what lets a category
      question ("what are the responsibilities?", no literal keyword to
      extract) or a cross-language question ("어떤 학위가 필요한
      잡인가요?" against an English-labeled node) find the right
      instances by meaning instead of falling through to every instance
      of the type.
   c. **All instances of the type** — if (b) also found nothing (most
      likely because this document was extracted before embeddings
      existed, so its nodes have a `NULL` embedding column), every
      instance of just this type is used, same as the original
      keyword-only fallback.
   `find_matching_edges()` separately picks up edges whose `type` is in
   the determined `edge_types` *and* that connect to an already-matched
   node (edges have no text of their own to embed or keyword-match
   against). Matched edges contribute their other endpoint back into
   the matched-node set; if edges were determined relevant but *no*
   node was matched at all (e.g. `node_types` came back empty),
   `all_edges_of_types()` pulls in every edge of the determined type
   instead. Real bug report that motivated the original (b)/(c)
   fallback: "어떤 학위가 필요한 잡인가요?" and "what responsibilies?"
   both correctly identified their relevant types in stage 1 but got
   zero keyword matches in stage 2, so every such question reported
   "not found" despite the graph clearly having the answer. Only when a
   determined type has *zero actual instances* in the graph does the
   search still end empty after all three tiers — a genuine miss.
3. **Expansion** — the resulting node set (whichever tier produced
   it) is expanded `hops` steps via `graphdb.expand_hops()`, an
   undirected, variable-length Cypher pattern match
   (`MATCH (n)-[*0..hops]-(m) ...`) run against LadybugDB, and the
   resulting subgraph is formatted as an `Entities:`/`Relations:`
   text block, injected as a `system` message ahead of the conversation.
   Each entity/relation line appends the node's or edge's `detail` text
   when present (`- label (type): detail`), so the final answer isn't
   limited to whatever a short label conveys — this is what lets a
   question like "PhD 없이도 지원 가능한가요?" surface an exception
   clause ("동등한 업계 경력이 있는 경우... 예외적으로 고려될 수
   있습니다") that a bare `Requirement` label/type could never carry.

When the GraphRAG path runs (schema + graph both exist, and
`search_graph()` doesn't raise), the response carries the determined
types as their own fields — `node_types`/`edge_types` (the same lists
`determine_relevant_types()` produced, already filtered to real schema
names) — rather than a text line baked into `content`. `content` is
just the model's answer, or the bare "관련된 내용을 찾을 수 없습니다."
when nothing was found at any stage (no relevant types, or a relevant
type with zero actual instances) — in that case the chat model is
never called for a final answer. This is a deliberate behavior change
from a bare GraphRAG setup: once a document with a graph is selected,
a miss is reported as a miss rather than silently answering from the
model's general knowledge. A technical failure (LLM returns
unparseable JSON at either stage) is different from a miss and falls
back to plain chat, same as when there's no graph at all. The response
also carries `related_nodes`/`related_edges` — the exact node/edge
objects (`{"id", "label", "type", "detail"}` / `{"source", "target",
"type", "detail"}`) that `search_graph()` matched and expanded via
`graphdb.expand_hops()`, i.e. precisely the entities backing that
answer's context text, not an LLM-guessed citation. Plain chat (no
`filename`, or no schema/graph yet) omits all four keys entirely rather
than sending empty values, so the existing plain-chat response shape
is unchanged. The frontend renders `node_types`/`edge_types` as
clickable chips that toggle that type's graph filter, and
`related_nodes` as chips that highlight the matching nodes in the
ontology graph panel — see the Frontend section.

**`POST /api/parse`** — multipart upload, field `file`, optional field
`converter` (`"anydoc"` default or `"table_aware"`, the latter only
applying to actual `.pdf` uploads — see `app.chunking`). Extracts the
extension from the filename (sanitized via `os.path.basename` to
prevent path traversal), calls `anydoc.to_markdown_bytes(data, ext)`,
saves the result to `backend/data/documents/{stem}/raw.md`, returns
`{"filename": "...", "path": "data/..."}` (content is not included in
the response — fetch it separately via `/api/files/{filename}`). The
returned `"filename"` (`{stem}_raw.md`) is a synthetic, stable
identifier — see `app.paths.document_dir_for` — decoupled from the
actual on-disk path.
`anydoc.ConvertError` and `ValueError` (e.g. unrecognized extension)
both map to HTTP 400. A `.md` upload skips `anydoc` entirely (it only
accepts formats it converts *into* markdown *from* — `md` isn't one of
them, so the call would just fail) and is registered as-is: the
uploaded bytes are UTF-8-decoded and written straight to `raw.md`,
with an invalid-UTF-8 upload also mapping to 400.

**`GET /api/files`** — lists every `backend/data/documents/{stem}/`
folder that has a `raw.md` in it, sorted by that file's modification
time, newest first: `{"files": [{"filename": "..."}]}`.

**`GET /api/files/{filename}`** — plain-text read of
`backend/data/documents/{stem}/raw.md`, 404 if missing,
`basename`-sanitized against path traversal.

**`GET /api/documents`** — like `GET /api/files` but one row per document
with everything the File Explorer's right-hand panel needs:
`original_filename`/`converter` (from `manifest.json`, defaulting to the
derived filename/`"anydoc"` when no manifest was ever written),
`size_bytes`/`modified_at` (from `raw.md`'s own `stat()`), `summary`
(`load_document_summary`, `null` if never generated), `has_chunks`
(`documents/{stem}/chunks.json` exists), `has_schema`/`has_graph`
(active schema version / `graphdb.has_graph` for that version), and
`graphdb_name`.

**`POST /api/documents/{filename}/chunk`** — reads
`documents/{stem}/raw.md`, runs `app.chunking.chunk_markdown` (Korean
`제N조` article headings; see `app.chunking`'s module docstring and
`scripts/data_prep/README.md` for the heading/section heuristics), and
saves `{"source", "preamble": {...}, "chunks": [...]}` to
`documents/{stem}/chunks.json`. 404 if the document doesn't exist.
Re-running overwrites the previous chunks (no versioning, unlike schema).

**`GET /api/documents/{filename}/chunk`** — reads back
`documents/{stem}/chunks.json`; 404 if chunking hasn't been run yet.

**`POST /api/documents/{filename}/summary`** — reads
`documents/{stem}/raw.md`, prompts the LLM for a 2-3 sentence plain-text
(not JSON) summary via `ontology.summarize_document`, saves it to
`documents/{stem}/summary.json`, and returns `{"summary": "..."}`. 404 if
the document doesn't exist; 400 if the LLM returns empty content.
Re-running overwrites the previous summary.

**`GET /api/documents/{filename}/summary`** — reads back the saved
summary; 404 if none has been generated yet.

**`POST /api/ontology/{filename}/schema`** — reads
`backend/data/documents/{stem}/raw.md`, prompts the LLM (same
`get_chat_model()` as chat) to propose an ontology schema for that
document, parses the response as JSON (stripping markdown code fences
if present), saves it to
`backend/data/documents/{stem}/schema_v{N}.json` (`stem` = filename
without extension, `N` = next version number, tracked in
`documents/{stem}/versions.json`), and returns it plus `"version"`.
Schema shape:
`{"node_types": [{"name", "description"}], "edge_types": [{"name", "description", "source", "target"}]}`.
404 if the document doesn't exist; 400 if the LLM's response isn't
parseable/well-shaped JSON.

**`GET /api/ontology/schemas`** — scans
`backend/data/documents/*/versions.json`, returns
`{"schemas": [{"stem": "..."}]}` for the "스키마 라이브러리" list
in `SettingsPanel`.

**`POST /api/ontology/{filename}/schema/use`** — body
`{"source_stem": "..."}`. Loads `documents/{source_stem}`'s active
schema version (404 if that source has no schema) and saves it as a
new schema version under `documents/{stem}`, i.e. designates it the
active schema for `filename`. Returns the copied schema.

**`GET /api/ontology/{filename}/schema`** — reads back
`documents/{stem}`'s active schema version; 404 if none has been
generated/assigned yet. Used by the frontend to show schema status and
to drive the "schema preview" graph mode before extraction has run.

**`POST /api/ontology/{filename}/extract`** — loads
`documents/{stem}`'s active schema version; if none exists, falls back to
`DEFAULT_SCHEMA` (a generic `Entity`/`RELATED_TO` schema) and persists
it as this document's schema rather than erroring, so "extract" always
produces *something*. Prompts the LLM to extract nodes/edges from the
document conforming to that schema, then saves the result via
`graphdb.write_graph()` into LadybugDB (`backend/data/graph/graph.ladybugdb`)
rather than as `nodes.json`/`edges.json` files, and returns
`{"nodes": [...], "edges": [...]}`. Node shape
`{"id", "label", "type", "detail"}`, edge shape
`{"source", "target", "type", "detail"}` (`source`/`target` are node
ids). `detail` is an LLM-written free-text field — one or two
sentences of anything from the document that the label/type alone
loses (exact conditions, exceptions, figures, dates) — optional and
often empty; it exists because label/type is a lossy summary and
GraphRAG answers were otherwise limited to whatever a short label
could convey (see the GraphRAG section below). 400 on
unparseable/malformed LLM JSON, and also 400 if a node/edge type name
in the schema isn't a valid Cypher identifier (letters/digits/
underscores, starting with a letter or underscore) — `graphdb.py`
interpolates type names directly into DDL/Cypher, so it rejects unsafe
ones via `_validate_identifier()` as a security backstop.
`SCHEMA_PROMPT` (`app/ontology.py`) instructs the LLM to only propose
identifier-safe type names to avoid hitting this in practice, but a
non-compliant schema (e.g. hand-edited, or an LLM that ignores the
instruction) still surfaces as a clean 400 rather than a crash. No
validation that node/edge types actually match the schema — the LLM
output is trusted structurally only (must have the right list/dict
shape). Graphs extracted before
this field existed simply have no `detail` on their nodes/edges;
re-running "그래프 추출" is the only way to backfill it, there's no
migration. `write_graph()` re-extracting the same document first
deletes that document's own prior nodes/edges (scoped by
`source_document`) before writing the new ones, so a re-extraction
fully replaces rather than appends.

**`GET /api/ontology/{filename}`** — reads back the saved graph via
`graphdb.load_graph()` (LadybugDB, not `nodes.json`/`edges.json`);
response shape is unchanged (`{"nodes": [...], "edges": [...]}`); 404
if extraction hasn't run yet.

### Auth (`app/auth.py`)

A single shared password gates the app when it's deployed somewhere
public (Render) — not per-user accounts, and not intended as a real
security boundary, just a deterrent against casual/uninvited access to
an app that spends OpenRouter credits. Stateless, no session store:
`POST /api/login` checks the submitted password against `APP_PASSWORD`
(constant-time compare) and, on success, returns
`token = sha256(APP_PASSWORD)`. The frontend stores that token in
`localStorage` and sends it as `Authorization: Bearer {token}` on every
subsequent request; `require_auth`, an HTTP middleware, recomputes the
same hash and rejects any `/api/*` request without a match — except
`/health`, `/api/login`, and `/api/config` itself, which must stay
reachable pre-login. **`OPTIONS` requests always bypass the gate
regardless of path**, checked first in the middleware, before even
looking at `APP_PASSWORD`: a CORS preflight request never carries the
app's own `Authorization` header (browsers don't attach custom headers
to preflight), and confirmed in production, requiring one here broke
every real cross-origin call behind it — `OPTIONS /api/parse` came
back 401, so the browser aborted the actual `POST` before ever sending
it, breaking every file upload the moment `APP_PASSWORD` was set. The
gate is a no-op whenever `APP_PASSWORD` is unset (local dev, and every
backend test — none of them set it): `/api/config`'s `auth_required`
is `false`, `require_auth` never rejects anything, and the frontend
never shows a login screen at all. Because the token is a fixed hash
with no expiry, rotating `APP_PASSWORD` on Render immediately
invalidates every previously-issued token at once.

### Configuration

- `OPENROUTER_API_KEY` (required), `OPENROUTER_MODEL` (optional,
  default `openai/gpt-4o-mini`) — read from `backend/.env`
  (git-ignored; `backend/.env.example` documents the format).
- `APP_PASSWORD` (optional) — enables the login gate described above
  when set to a non-empty value; unset by default in local dev.
- `OTEL_EXPORTER_OTLP_ENDPOINT` (optional) — set by `podman-compose.yml`
  to Jaeger's OTLP HTTP receiver; unset in any other environment
  (including local pytest runs) disables tracing entirely rather than
  erroring.

### Dependencies

`requirements.txt`: `fastapi`, `uvicorn`, `langchain-openai`,
`firecrawl-anydoc`, `python-multipart`, `networkx`, `ladybug`,
`opentelemetry-api`, `opentelemetry-sdk`,
`opentelemetry-exporter-otlp-proto-http`. `ladybug` is the embedded
Cypher-native graph database (`graphdb.py`) that stores extracted
nodes/edges; `networkx` remains a declared dependency but is no longer
imported anywhere in `app/` — graph search and hop expansion are now
Cypher queries via `graphdb.py`, not in-memory `networkx` graphs.
`requirements-dev.txt` adds `pytest`, `httpx` for testing.

### Tests

`backend/tests/` (pytest, run via `python -m pytest`): `test_auth.py`,
`test_chat.py`, `test_chunking.py`, `test_config.py`, `test_files.py`,
`test_graphdb.py`, `test_graphrag.py`, `test_ontology.py`, `test_parse.py`,
`test_paths.py`, `test_telemetry.py`.
`test_auth.py` patches both `app.auth.APP_PASSWORD` and
`app.main.APP_PASSWORD` per test (the latter is a separate name bound
by `main.py`'s `from app.auth import APP_PASSWORD`, so patching only
the former leaves the middleware's copy unpatched) — none of these
tests, nor any other test file, ever sets the real `APP_PASSWORD`
env var, so the auth gate stays inactive for the rest of the suite.
Chat/parse/ontology/graphrag tests mock the external calls
(`get_chat_model`, `get_embedding_model`, `anydoc.to_markdown_bytes`);
an autouse fixture stubs `get_embedding_model` in every test file whose
code path can reach `embed_nodes()`/`embed_query()`, even when that
particular test doesn't exercise the embedding fallback, so no test
suite run ever makes a real OpenRouter embeddings call. Telemetry tests
use a bare fake model (no OTel mocking needed — the default no-op
tracer is already the behavior under test); file tests use the real
filesystem. `test_chat.py`'s GraphRAG tests use a `SequencedChatModel`
fake that returns a different canned response per `invoke()` call (in
order) and records the messages it was called with, since one
`/api/chat` request with `filename` set makes up to three LLM calls
(type analysis, keyword extraction, then the actual answer) plus, when
a determined type's keyword match comes up empty, one embedding call
against a separately-mocked embedding model. `test_graphdb.py` runs
directly against a real LadybugDB database on disk — no mocking — via
an autouse fixture that deletes and recreates `graphdb.DB_PATH` before
and after every test; see the "Backend tests" section of `CLAUDE.md`
for the operational caveat this implies.

`frontend/src/**/__tests__/` (Vitest + `@vue/test-utils`, run via
`npm test`): `chunkFormat.test.js`, `ChunkView.test.js`,
`DocumentPreview.test.js`. Component tests mock `../utils/api.js`'s
`apiFetch` (`vi.mock`) rather than hitting a real backend, and mount
with `@vue/test-utils`'s `mount()`; `frontend/vitest.setup.js` stubs
`ResizeObserver` (unavailable in jsdom) so `DocumentPreview.vue` can be
mounted at all. No other component has test coverage yet — this is the
first frontend feature built test-first, not a retrofit.

## Frontend (`frontend/`)

Vue 3 + Vite. Dashboard layout in `src/App.vue`, split into five
components under `src/components/`.

```
┌──────────┬──────────────────┬──────────────────┐
│          │      Chat        │  Document Preview │
│ Settings │                  ├──────────────────┤
│ (280px)  │  Ontology Graph  │  Schema / Graph   │
│          │                  │  DB Preview       │
└──────────┴──────────────────┴──────────────────┘
```

The right-hand area is a CSS grid (`App.vue`'s `.main-grid`) split into
four quadrants, independently resizable: a `.resizer-v` (drag
horizontally) between the two columns and a `.resizer-h` (drag
vertically) between the two rows, both implemented the same way as
`SettingsPanel`'s old single resizer — plain
mousedown/mousemove/mouseup on `window`, computing the new split as a
percentage of the grid's `getBoundingClientRect()` and clamped to
20–80%. `colPercent`/`rowPercent` in `App.vue` drive
`grid-template-columns`/`grid-template-rows` (`{split}% 6px 1fr`)
directly.

- **`SettingsPanel.vue`** — the sidebar itself renders one filter
  checkbox per entry in the `availableTypes` prop (the real node types
  of whatever graph is currently loaded — nothing hardcoded); toggling
  emits `filters-changed`. A parallel "그래프 엣지 필터" section does the
  same for `availableEdgeTypes`/`edge-filters-changed`. Both filter sets
  are still owned locally (`enabledTypes`/`enabledEdgeTypes` reset to
  "everything on" whenever the corresponding `available*Types` prop
  changes, e.g. after a new extraction), but can also be driven
  externally: the `toggleTypeRequest`/`toggleEdgeTypeRequest` props each
  carry a fresh `{type}` object on every change (a new object each time,
  so the same type clicked twice in a row still triggers a watcher fire)
  and a watcher calls the same `toggleType()`/`toggleEdgeType()` a
  checkbox click would — this is how `ChatPanel`'s type-analysis chips
  (see below) reach the filter state that actually lives here, via
  `App.vue` as a relay. Everything else lives in two modals opened from
  sidebar buttons:
  - **File Explorer** (`showFileExplorer`) — two columns. Left: a file
    input (radio choice between `anydoc` and `table_aware` converters,
    the latter only doing anything for a `.pdf` — see `app.chunking`)
    that posts to `/api/parse`, and the document list from
    `GET /api/documents`, each row showing a 4-stage badge strip
    (MD/Chunk/Schema/Graph, `has_chunks`/`has_schema`/`has_graph`).
    Clicking a row emits `file-selected` (`{filename, path}`). Right:
    for the selected document — 메타 정보 (original filename, converter,
    size, modified time, an "요약 생성"/"재생성" button around
    `POST /api/documents/{filename}/summary`, and a "청크 생성"/"재생성"
    button around `POST /api/documents/{filename}/chunk`, both refetching
    `/api/documents` on success so the badge strip and summary text stay
    current), the document's schema versions (`GET .../schema/versions`,
    activate/delete), and the "스키마 라이브러리" list
    (`GET /api/ontology/schemas`, clicking one calls
    `POST /api/ontology/{selectedFilename}/schema/use` and emits
    `schema-used`; refetched whenever `schemaVersion` changes).
  - **실행 설정** (`showRunSettings`) — LLM model picker
    (`POST /api/config/model`), a "GraphRAG 설정" number input (1–5,
    default 1) for the retrieval hop count (`hops-changed`), a "채팅
    표시 설정" checkbox (default checked) for whether chat messages
    render as HTML markdown or plain text (`markdown-changed`), and a
    "스키마 생성 설정" max-chars input. The main sidebar's own "워크플로우"
    section (온톨로지 발견/도메인 스키마/스키마 생성/그래프 추출/임베딩
    생성/온톨로지 검증 buttons) isn't detailed in this doc yet.
- **`ChatPanel.vue`** — self-contained message list + input, calls
  `/api/chat` with the full local history on each send, plus the
  `file`/`hops` props (`filename` and `hops` in the request body) so
  the backend can run GraphRAG against the currently selected
  document's graph. The in-flight request's `AbortController` is
  aborted when the user presses Escape while `isLoading` is true (a
  `keydown` listener on `window`, added/removed in `onMounted`/
  `onUnmounted`); the backend has no cancellation API, so this only
  stops the client from waiting on the response — the server may keep
  processing the LLM call to completion regardless, its result simply
  discarded. Aborting shows "요청이 취소되었습니다." and immediately frees
  the input for the next message rather than leaving it disabled until
  the original request would have resolved. The `renderMarkdown` prop
  (from `SettingsPanel`'s
  toggle) switches each message between `marked.parse(...)` piped
  through `v-html` and a plain `<p>` with `white-space: pre-wrap` — same
  unsanitized-`v-html` approach as `DocumentPreview.vue`, consistent
  with that existing precedent rather than a new one. User and
  assistant messages get distinct bubble backgrounds (`.message.user`
  vs `.message.assistant`, keyed off the same `role` string already
  used for the "나"/"챗봇" label) so a message's origin is visually
  obvious without reading the label. Each assistant message that came
  from a GraphRAG-backed answer also stores the response's
  `node_types`/`edge_types`/`related_nodes` (see `POST /api/chat`
  above) and renders two chip rows above the answer text — "노드:" and
  "엣지:" — one chip per determined type, separated from the answer by
  a dashed border and margin (the "one line of breathing room before
  the GraphRAG result" gap). Each type chip's `inactive` class comes
  from comparing it against the `enabledTypes`/`enabledEdgeTypes` props
  (passed down from `App.vue`, the same Sets `OntologyGraph` filters
  by) so a chip visually shows whether that type is currently shown or
  hidden in the graph; clicking one emits `toggle-type` with `{kind:
  'node'|'edge', type}`, which `App.vue` turns into a fresh
  `toggleTypeRequest`/`toggleEdgeTypeRequest` object for `SettingsPanel`
  (see above) — this is a toggle on the *type*, filtering every node/edge
  of that type in the graph, not a highlight of specific instances.
  Below that, a separate "관련 노드" chip row (unrelated to the type
  toggle above it) renders one chip per related node; clicking a chip
  emits `highlight-nodes` with that single node's id in a one-element
  array. `App.vue` forwards this straight through to `OntologyGraph` as
  `highlightedNodeIds` — no sentence-level attribution, this links a
  whole answer to the graph entities it was actually retrieved from,
  not specific words within it (see the GraphRAG section above for why:
  those ids come from `search_graph()`'s own matched/expanded set, not
  a citation the LLM invented, so nothing here can point at a node that
  doesn't exist).
- **`DocumentPreview.vue`** — takes the `file` prop (`{filename, path}`),
  fetches `/api/files/{filename}`, renders it as HTML via `marked`.
  Uses an always-visible (non-overlay) scrollbar — see Known
  Limitations history for why. On the same file change it also tries
  `GET /api/documents/{filename}/chunk`; a 404 (no chunks generated yet)
  is not an error, it just means no chunk view is offered. When chunks
  exist, a "원문 | 청크" toggle appears in the panel header (`viewMode`,
  always reset to `"raw"` on file change, even if chunks exist — never
  auto-opens the chunk view) and switching to "청크" renders
  `<ChunkView :data="chunkData" />` in place of the raw markdown pane.
  `ChunkView.vue` is a pure presentational child: it shows the chunk
  JSON's `source` and the preamble's line count (`line_end - line_start
  + 1`, from `utils/chunkFormat.js` — never the preamble's own `text`),
  then one collapsible row per chunk (`path` only when collapsed, the
  chunk's `text` rendered via `marked` when expanded). Each row toggles
  independently via a local `Set` of expanded chunk ids — multiple can
  be open at once, not an accordion — and every chunk starts collapsed.
- **`OntologyGraph.vue`** — takes `file`, `enabledTypes`,
  `enabledEdgeTypes`, `schemaVersion`, and `highlightedNodeIds` props.
  On file change, checks
  `GET /api/ontology/{filename}/schema` and `GET /api/ontology/{filename}`
  to decide what to draw, in priority order: an extracted graph (real
  `nodes`/`edges`) if one exists; otherwise a **schema preview** (the
  schema's `node_types`/`edge_types` drawn as if they were the
  nodes/edges themselves) if a schema exists; otherwise a placeholder
  telling the user to generate or pick a schema. "스키마 생성" and
  "그래프 추출" buttons are always available once a file is selected.
  While either request is in flight, a `setInterval`-driven
  `elapsedSeconds` counter drives an operation-specific status line
  ("문서를 읽어 스키마 생성 중... {n}초" / "문서를 읽고 주어진 스키마로
  노드와 에지를 생성 중... {n}초") so a long LLM call doesn't look frozen;
  on success the same status line is replaced with a count summary
  ("스키마 생성 완료 (노드 타입 X개, 엣지 타입 Y개)" /
  "그래프 추출 완료 (노드 X개, 엣지 Y개)"). Emits `types-available`/
  `edge-types-available` with the sorted unique node/edge types of
  whatever is currently drawn (schema or real graph), so
  `SettingsPanel`'s filter checkboxes always match what's on screen,
  and `schema-updated` after a successful generate/extract so `App.vue`
  can bump `schemaVersion` (which also tells `SettingsPanel` to refresh
  its schema library list). Rendering is delegated to the
  [`v-network-graph`](https://dash14.github.io/v-network-graph/) library
  (`<v-network-graph :nodes :edges :layouts :configs>`), which fills
  100% of its container and provides pan/zoom/node-drag for free —
  `displayNodes` (schema-preview or real graph, filtered by
  `enabledTypes`) and `displayEdges` (additionally filtered by
  `enabledEdgeTypes`, on top of requiring both endpoints to already be
  visible) are converted into the id-keyed objects the
  library expects, node colors come from `configs.node.normal.color`
  (a function of `node.type`), edge colors likewise from
  `configs.edge.normal.color` (a function of `edge.label`, using a
  separate color palette from nodes), and node positions come from a
  `d3-force` simulation (`forceManyBody` + `forceLink` + `forceCenter` +
  `forceCollide`) restarted whenever the visible node/edge set changes;
  each `tick` writes `{x, y}` into `layouts` (a plain ref the library
  also mutates on drag), and nodes that already have a position keep it
  as the simulation's starting point rather than jumping, so toggling a
  filter doesn't reshuffle the whole layout. Edge labels need more than config —
  v-network-graph only reads a label's *text* from the edge object's
  own `label` field (set to the relation type name when building
  `vngEdges`) via the `#edge-label` slot rendering a `<v-edge-label>`;
  `configs.edge.label` only controls style (font size/color), not
  content. A small legend (two `<table>`s, node types and edge types,
  each row a color swatch + type name) sits above the graph so the
  color coding is actually readable — colors are assigned by sorted
  index into each palette, so they stay stable as long as the set of
  visible types doesn't change. `view.autoPanAndZoomOnLoad: "fit-content"`
  fits the graph on first mount; a "리셋" button re-triggers the same
  fit via the component's exposed `fitToContents()` method (accessed
  through a template ref), also called automatically after
  load/generate/extract so the view stays sensible across data changes.
  `highlightedNodeIds` (from `ChatPanel`'s "관련 노드" chips, via
  `App.vue`) is watched into a `selectedNodes` ref bound with
  `v-model:selected-nodes` on `<v-network-graph>`; `configs.node.selected`
  gives selected nodes a larger radius and a gold stroke so a
  highlighted node reads as visually distinct without a custom
  render slot. Reset to `[]` on file change so a stale id from a
  previous document's chat history can't linger. The same watcher also
  calls `focusOnNodes()`, which re-centers the view on the
  highlighted node(s) without changing zoom level: it averages the
  `layouts` position of every highlighted id (so multiple ids center
  on their centroid), reads the current `getViewBox()` box, shifts it
  by the same width/height around that centroid, and applies it via
  `setViewBox()` inside `transitionWhile()` so the pan animates instead
  of jumping. This is a manual reimplementation rather than a
  `fitToContents()` call, since that method fits the *entire* graph's
  bounding box and has no "fit to a subset of nodes" mode; positions
  outside the currently loaded `layouts` (e.g. a chip clicked in the
  schema-preview display mode, where ids are schema type names, not
  real node ids) are silently skipped rather than panning anywhere.
- **`SchemaGraphPreview.vue`** — read-only raw-data viewer, three tabs
  ("스키마"/"Nodes"/"Edges") over `GET /api/ontology/{filename}/schema`
  and `GET /api/ontology/{filename}` (nodes/edges from the same
  response), each rendered as `JSON.stringify(..., null, 2)` in a
  `<pre>`. Refetches on file change (resetting to the "스키마" tab) and
  on `schemaVersion` change, same pattern as `OntologyGraph`. Exists
  because working with the pipeline surfaced a real need to inspect the
  raw schema/node/edge JSON directly rather than only its rendered
  graph form.

State lives in `App.vue`: `parsedFile` (selected/uploaded document),
`graphFilters`/`edgeGraphFilters` (enabled node/edge types, both
`Set`s, passed to both `OntologyGraph` as `enabledTypes`/
`enabledEdgeTypes` and to `ChatPanel` under the same prop names so its
type chips can show active/inactive), `availableTypes`/
`availableEdgeTypes` (from `OntologyGraph`'s `types-available`/
`edge-types-available`, passed down to `SettingsPanel`),
`schemaVersion` (bumped by either `OntologyGraph`'s `schema-updated` or
`SettingsPanel`'s `schema-used`, and passed to `SettingsPanel` and
`SchemaGraphPreview` as a refresh signal), `graphRagHops` (from
`SettingsPanel`'s `hops-changed`, passed to `ChatPanel`), `renderMarkdown`
(from `SettingsPanel`'s `markdown-changed`, passed to `ChatPanel`),
`highlightedNodeIds` (from `ChatPanel`'s `highlight-nodes`, passed to
`OntologyGraph`), `toggleTypeRequest`/`toggleEdgeTypeRequest` (from
`ChatPanel`'s `toggle-type`, each wrapped in a fresh object and passed
to `SettingsPanel`, which is the actual owner of the filter `Set`s —
see `SettingsPanel.vue` above). Chat messages (and the
`node_types`/`edge_types`/`related_nodes` attached to each) stay local
to `ChatPanel`.

`vite.config.js` proxies `/api` and `/health` to `http://backend:8000`
(the compose service name) so the browser only ever talks to
`localhost:5173`.

## Sample data & data-prep tooling (`scripts/`, `samples/`, `Makefile`)

Outside the app itself:

- **`samples/`** — five pre-converted Samsung Life 약관 (insurance
  terms) markdown documents, checked into git specifically so a new
  user can copy them straight into `backend/data/` and try schema
  generation/extraction immediately, without running the PDF-to-
  markdown pipeline below first. See `samples/README.md`.
- **`scripts/data_prep/`** — `download_samsunglife_terms.py` (fetches
  source PDFs) and `convert_pdfs_to_markdown.py` (table-aware PDF →
  markdown, writing a `manifest.json` alongside the output recording
  source SHA-256/page/table counts per file). Their output lands under
  `data/raw/` (`pdf/`, `md/`), which is git-ignored — the full raw set
  is a local-only, regeneratable artifact; only the curated `samples/`
  subset is committed.
- **`scripts/prepare_goldenset/`** — generates a golden question/answer
  set from a directory of markdown documents, for evaluating GraphRAG
  answer quality against a fixed reference set rather than manual
  spot-checking.
- **`Makefile`** — `make samsunglife-data`, `make pdf-to-md`, `make
  goldenset` (plus `-test` targets for each) wire up the three tools
  above with sensible defaults; see `make help`.

## Deployment (dev)

`podman-compose.yml` defines three services:

- **jaeger** — `jaegertracing/all-in-one`, UI at `localhost:16686`. No
  volumes (traces are in-memory; they don't survive `down`).
- **backend** — builds `backend/Dockerfile` (`python:3.12-slim`,
  `uvicorn --reload`), port 8000, `env_file: backend/.env`,
  `OTEL_EXPORTER_OTLP_ENDPOINT` pointed at jaeger, volumes for `app/`
  and `data/` (hot-reload + host-visible parse output), `depends_on: jaeger`.
- **frontend** — builds `frontend/Dockerfile` (`node:20-slim`, `vite`
  dev server), port 5173, volumes for `src/`, `index.html`,
  `vite.config.js`, `depends_on: backend`.

Every LLM call shows up as a trace at `http://localhost:16686` (search
by service `ontology-builder-backend`) — useful for seeing which
GraphRAG stage a slow chat response actually spent time in.

Run with `podman-compose up --build`. Requires a running
`podman machine` and a `backend/.env` with a real `OPENROUTER_API_KEY`.

`backend/data/.gitkeep` keeps the bind-mount source directory present
in a fresh checkout — see the troubleshooting note below for why that
matters.

### Troubleshooting: bind mounts coming up empty

On podman machine (macOS, applehv + virtiofs), a bind-mounted
directory (`backend/data`, `frontend/src`, etc.) can occasionally come
up empty inside the container — or, in one observed case, the host
side got wiped back to empty after a `down`/`up` cycle — even though
the files are genuinely present/absent on the other side. This is
podman/virtiofs mount flakiness, not an application bug. If a volume
looks empty or stale (e.g. `/api/files` unexpectedly returns nothing,
or frontend changes don't show up after a save):

1. Make sure the host-side directory exists (`mkdir -p backend/data`).
2. `podman-compose down && podman-compose up --build -d` (a plain
   `restart` sometimes isn't enough to reattach the mount correctly).
3. Verify with `podman exec <container> ls <mount path>` against the
   host directory before trusting the app's behavior.

### Troubleshooting: frontend changes not showing up (Vite serving stale code)

A second, distinct symptom of the same underlying virtiofs flakiness:
`frontend/src/*.vue` is correctly updated on both host and inside the
container (`cat`/`grep` show the new content), but the *running Vite
dev server* keeps serving an old compiled version of the file — its
file watcher never saw the change, so it never invalidated its
transform cache or pushed an HMR update. This is sneaky because it can
look exactly like a logic bug in your own code (elements silently
missing from the DOM, old behavior persisting) with nothing wrong in
any file you can inspect. Confirm it by fetching the module straight
from the dev server and diffing against the source:

```
curl -s http://localhost:5173/src/components/Foo.vue | grep 'some-recent-change'
```

If that comes back empty while `grep` on the file itself finds it,
Vite is stale. Fix: `podman-compose down && podman-compose up --build -d`
for the frontend service (a page reload or even disabling the browser
cache does **not** help — the staleness is server-side, in Vite's own
transform cache, not the browser).

## Deployment (production, Render)

`render.yaml` defines two services, split the same way as local dev
but as independent Render services rather than podman-compose
containers:

- **`ontology-builder-backend`** — `type: web`, `runtime: docker`,
  built from `backend/Dockerfile` with `dockerCommand` overriding the
  Dockerfile's dev `CMD` to drop `--reload` and bind Render's assigned
  `$PORT`. Has a 1GB disk mounted at `/app/data` (persists
  `backend/data` across deploys — the `starter` plan, not `free`, is
  required for a disk). `healthCheckPath: /health` (must stay
  unauthenticated — see Auth above). Env vars: `OPENROUTER_API_KEY`
  and `APP_PASSWORD` (both `sync: false` — set the real values in the
  Render dashboard, never committed), `OPENROUTER_MODEL`,
  `CORS_ALLOWED_ORIGINS` (must equal the frontend service's actual
  public URL).
- **`ontology-builder-frontend`** — `type: web`, `runtime: static`,
  built via `npm install && npm run build`, published from
  `frontend/dist`. `VITE_API_BASE_URL` (must equal the backend
  service's actual public URL) is inlined into the build by Vite, so
  the same relative `/api/*` calls `utils/api.js` makes locally (via
  Vite's dev-server proxy) resolve to an absolute cross-origin URL in
  production instead — see `API_BASE` in `utils/api.js`.

Leaving `APP_PASSWORD` unset on the Render backend is a valid
deployment choice (the app is then open, same as local dev) — setting
it is a deliberate opt-in, not something `render.yaml` forces.

## Known limitations / not yet built

- **No persistence beyond `backend/data/`, and no server-based
  database.** Parsed markdown and schemas live as flat files there;
  extracted graphs live in an embedded LadybugDB database file
  (`backend/data/graph/graph.ladybugdb`) rather than JSON, but it's
  still local to the backend container/filesystem — there is no
  per-user/session separation, and chat history is not saved anywhere.
- **No migration from the pre-LadybugDB JSON storage.** Documents
  extracted before graph storage moved to LadybugDB have leftover
  `nodes.json` / `edges.json` files under the old (pre-`documents/`
  layout) `data/graph/{stem}/` location on disk; no code reads or
  deletes them, so they linger harmlessly but permanently — and since
  the `documents/{stem}/` migration didn't move them either, they're
  now doubly orphaned. Such documents behave as if never extracted
  (`GET /api/ontology/{f}` → 404) until re-extracted, which populates
  LadybugDB and leaves the stale JSON files in place, unused.
- **No DDL garbage collection.** Re-extracting a document under a
  different schema (different type names) leaves the old type's NODE/
  REL tables behind, now with zero rows for that document — and if no
  other document ever used that type, zero rows at all. Tables are
  never dropped.
- **No real auth.** The optional `APP_PASSWORD` gate (see Auth above)
  is a single shared password with a non-expiring stateless token —
  fine as a casual-access deterrent on the Render deploy, not a
  security boundary. No per-user accounts, no login rate limiting, no
  token expiry, no logout button.
- **No streaming chat.** Responses return in one shot.
- **No automated frontend tests.** Frontend changes are verified
  manually / via Playwright, not a test suite.
- **No schema/graph validation.** Ontology extraction trusts the LLM's
  JSON structurally (right keys/lists) but doesn't check that node/edge
  `type` values actually match what's in `schema.json`.
- **No retry on LLM JSON parse failure.** A malformed response is just
  a 400 — the user re-clicks the button.
- **No document length/token limits.** The full document text is sent
  to the LLM for both schema generation and extraction.
- **GraphRAG instance matching tries substring match, then embedding
  similarity, then every instance of the type** (see the three-tier
  fallback above) — it still cannot recover a question that needs a
  *specific* instance the keyword extraction mis-extracted (e.g.
  mangled a name) badly enough that neither the substring match nor
  the embedding similarity ranks the right node highly; that still
  reads as "not found" unless the type falls through to the
  all-instances tier. A GraphRAG-augmented chat turn costs up to three
  LLM calls (type analysis, keyword extraction, then the answer) plus
  one embedding call per node type whose keyword match came up empty
  (cached per question — one embedding call total no matter how many
  types need it) — versus one LLM call for plain chat. The
  type-analysis step alone is enough to short-circuit to "not found"
  without any of the others if nothing in the schema looked relevant.
  Documents extracted before this embedding fallback existed have no
  vector stored per node (`NULL` column) and fall straight through to
  the all-instances tier until re-extracted.
