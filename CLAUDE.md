# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A chatbot that uses a custom-extracted ontology (nodes/edges per document)
plus GraphRAG to answer questions more accurately than plain chat. FastAPI
backend, Vue 3 frontend, run together via podman-compose for local dev. See
`docs/SPEC.md` for the full endpoint/component reference — this file covers
commands and cross-file architecture only.

## Commands

### Running the full stack (podman-compose)

```bash
mkdir -p backend/data && touch backend/data/.gitkeep  # see gotcha below
podman-compose up --build -d
```

Requires a running `podman machine` and `backend/.env` with a real
`OPENROUTER_API_KEY` (copy `backend/.env.example`). Frontend at
`localhost:5173`, backend at `localhost:8000`; the frontend dev server
proxies `/api` and `/health` to the backend container. Ladybug Explorer (a
GUI for browsing `backend/data/graph/graph.ladybugdb` directly via Cypher)
is at `localhost:8001`, running in `MODE=READ_ONLY` so it can stay up
alongside the backend without either side able to corrupt the other via a
write. Its image tag in `podman-compose.yml` must stay in sync with the
`ladybug` version pinned in `backend/requirements.txt` -- the explorer and
the embedded library have to agree on storage format to open the same file.
If graph queries in Explorer start failing oddly with both it and the
backend running, that's the same WAL-corruption failure mode as "LadybugDB
초기화" in the UI, not a new bug -- reset via that button.

Explorer only reads the database file once, when its container starts --
verified experimentally that it never picks up later writes, not on
re-query and not even via its own in-app "Apply" (reconnect to the same
path) button; only a fresh container start re-reads the file. So whenever
you want to see the latest graph, restart it: `podman restart
ontology_builder_ladybug-explorer_1`. That restart only shows everything
written so far if the main `.ladybugdb` file itself is up to date --
writes otherwise sit only in the `.wal` file until something checkpoints,
and (verified experimentally) an explicit `CHECKPOINT;` on a connection
that then stays open does *not* do this; only actually closing the
connection/database does. `graphdb.py`'s `write_graph`/`update_node_embeddings`
call `reset_connection()` right after every `COMMIT` specifically to force
that close-triggered checkpoint (the next call transparently reopens it),
so the main file is always current and an Explorer restart always shows
every write made so far.

**Known gotcha (podman on macOS, virtiofs):** bind mounts and Vite's file
watcher both go stale under this setup — a file edited on the host can
silently keep being served/read as an old version, in either the backend
(`backend/data`) or frontend (`frontend/src`) container. Symptoms: a file
you just wrote appears missing/empty, or a code change has no effect after
a browser reload. There is no code-level fix; the fix is always:

```bash
podman-compose down && podman-compose up --build -d
```

Before trusting "it's broken" or "it's not implemented," diff what's
actually being served against the source file — e.g.
`curl -s http://localhost:5173/src/components/Foo.vue | grep <recent-change>`
for frontend, `podman exec <container> cat <path>` for backend data — before
looking for a bug in the code itself. Full details and more symptoms are in
`docs/SPEC.md` under "Troubleshooting."

### Backend tests

No committed venv. First time:

```bash
cd backend
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Use a python3.14 interpreter (e.g. Homebrew's
`/opt/homebrew/opt/python@3.14/bin/python3.14`), not the macOS system
`python3` (3.9.6) — `requirements.txt` pins `langchain-openai==1.6.0`,
which has no distribution for 3.9 and fails to install.

Then:

```bash
source .venv/bin/activate
OPENROUTER_API_KEY=dummy python -m pytest tests/ -v      # all tests
OPENROUTER_API_KEY=dummy python -m pytest tests/test_chat.py::test_chat_returns_assistant_reply -v  # single test
```

`OPENROUTER_API_KEY` only needs to be set (never a real key) — every test
mocks the LLM call rather than hitting OpenRouter. Tests run directly
against the venv, not inside a container.

Tests never touch the real `backend/data` — `tests/conftest.py` points
`ONTOLOGY_DATA_DIR` at a throwaway temp directory before any `app.*` module
is imported, and `app/paths.py`'s `data_dir()` (used by `parser.DATA_DIR`,
`ontology.DOCUMENTS_DIR`, `graphdb.DB_PATH`) honors that override. This
exists because `test_graphdb.py`/`test_ontology.py`/`test_files.py`'s
fixtures delete and recreate `DATA_DIR`/`DOCUMENTS_DIR`/`graphdb.DB_PATH`
before and after every test — before this override existed, that meant
deleting every real extracted document, schema, and the graph DB on every
test run. Do not remove or bypass this isolation; if you need to inspect
what a test actually wrote, read `os.environ["ONTOLOGY_DATA_DIR"]` inside
the test process rather than pointing tests at the project path.

### Backing up analyzed data

`backend/data` (`documents/{stem}/` per-document folders holding raw
markdown, schema versions, chunks, etc., plus `graph/graph.ladybugdb` and
`domain_schemas/`) is git-ignored and lives only on the host (podman's bind
mount, not a volume), so nothing else backs it up. Snapshot it with:

```bash
./scripts/backup_data.sh          # writes backups/backend-data_<timestamp>.tar.gz
./scripts/restore_data.sh <archive>  # refuses to overwrite an existing backend/data
```

Run `backup_data.sh` after any extraction you'd be upset to lose, and
before anything risky (podman/volume changes, wiping `backend/data` to
retest from scratch). `backups/` is git-ignored too — these are local
snapshots, not committed history.

### Frontend

No lint command is wired up. `npm run dev` / `npm run build` work if you
want to run outside the container, but the normal workflow is editing
files on the host and letting the bind-mounted container's Vite dev
server hot-reload them (see the gotcha above when it doesn't).
`npm test` (Vitest + `@vue/test-utils`, jsdom environment) runs the
component/unit test suite in `frontend/src/**/__tests__/`; it needs no
container or backend and is the way to TDD new frontend logic. jsdom has
no `ResizeObserver`, which `DocumentPreview.vue` uses to measure its
scroll container — `frontend/vitest.setup.js` stubs it globally so
mounting that component in a test doesn't throw.

## Architecture

### Backend module boundaries (`backend/app/`)

`main.py` holds all routes and wires the other modules together; it has no
business logic of its own beyond request/response shaping.

- `parser.py` — `anydoc` converts an uploaded document to markdown, saved
  as `backend/data/documents/{stem}/raw.md` (`app.paths.document_dir_for`
  owns this per-document folder layout; every other per-document artifact
  -- schema versions, chunks, discovery, summary, manifest -- lives
  alongside it in that same folder). The `{stem}_raw.md`-shaped filename
  the rest of the app and the frontend pass around is a synthetic,
  stable identifier, decoupled from where the file actually sits on disk.
- `chunking.py` — a second, PDF-only ingestion path alongside `parser.py`'s
  generic `anydoc` conversion, ported from `scripts/data_prep/`'s
  Korean-insurance-policy tooling (see that directory's README for the
  heading/section heuristics and known limitations): `/api/parse`'s
  `converter=table_aware` field routes a `.pdf` upload through
  `convert_pdf_to_markdown_file` (pdfplumber-based, preserves tables as
  Markdown) instead of `anydoc`. `chunk_markdown_file` splits a document's
  `raw.md` into per-article JSON chunks at `documents/{stem}/chunks.json`
  (`제N조` headings, rider/section detection) — a separate, on-demand step
  from parsing, triggered via `POST /api/documents/{filename}/chunk`.
- `chat.py` — builds the `ChatOpenAI` client (OpenRouter) and converts
  `{role, content}` dicts to langchain messages. Every other module that
  needs an LLM call imports `get_chat_model` from here.
- `embeddings.py` — builds the `OpenAIEmbeddings` client (also OpenRouter,
  `OPENROUTER_EMBEDDING_MODEL`, default `openai/text-embedding-3-small`).
  `EMBEDDING_DIM` (1536, matching that model's output) is a hard constraint
  shared with `graphdb.py`'s node table DDL — changing embedding models to
  one with a different dimension requires re-extracting every document,
  since a Cypher `FLOAT[N]` column's width can't change after creation.
  `node_embedding_text()` is the single source of truth for what text gets
  embedded per node (`label` + `detail`), reused by both extraction
  (`ontology.embed_nodes`) and query embedding (`graphrag.embed_query`) so
  the two sides of a similarity comparison are computed consistently.
- `graphdb.py` — owns the single LadybugDB connection
  (`backend/data/graph/graph.ladybugdb`), opened lazily and cached at module
  level. There's one Cypher node table and one Cypher rel table per
  distinct node/edge *type name*, shared across every document rather
  than per-document — each row carries a `source_document` property so
  `write_graph`/`load_graph`/the search functions all filter to one
  document's own rows within tables that may hold many documents' data.
  Node/edge type names originate from LLM output (schema generation,
  then extraction), so every place that interpolates one into DDL or a
  Cypher label goes through `_validate_identifier()` first, which
  rejects anything not matching a safe `[A-Za-z_][A-Za-z0-9_]*`
  identifier pattern. Node ids are stored internally as `{stem}::{id}`
  (globally unique across documents sharing the same type tables) and
  stripped back to the bare id at every function's return boundary.
  Several functions guard against a database with zero REL tables at
  all (a fresh database, or every document written so far had zero
  edges) — an untyped relationship pattern against such a database
  either raises or silently returns nothing depending on the exact
  query shape, so `load_graph`, `find_matching_edges`,
  `all_edges_of_types`, and `expand_hops` all check table existence
  first rather than relying on the query to fail safely. Every node
  table also has an `embedding FLOAT[EMBEDDING_DIM]` column (`NULL` for
  a node no embedding was ever computed for -- e.g. a document
  extracted before this column existed); `find_similar_nodes()` ranks a
  single type's own nodes by `array_cosine_similarity()` against a
  query vector, filtering out `NULL` rows rather than sorting them
  arbitrarily.
- `prompts.py` — every LLM prompt template this app sends, as plain string
  constants (with the design-rationale comments explaining why each one asks
  for what it does), kept separate from `ontology.py`'s extraction/storage
  logic so the prompt text can be read or edited on its own. `ontology.py`
  imports each constant it needs (`SCHEMA_PROMPTS`, `EXTRACT_PROMPT`,
  `VALIDATION_PROMPT`, `DISCOVERY_PROMPT`, `SUMMARY_PROMPT`,
  `EVOLUTION_PROMPT`, `CONSOLIDATION_PROMPT`, `SCHEMA_CONSOLIDATION_PROMPT`);
  nothing else in the backend references them.
- `ontology.py` — two LLM-driven steps, run separately by design: propose a
  schema (`node_types`/`edge_types`) for a document, then extract actual
  `nodes`/`edges` conforming to a schema (the document's own, a copied one,
  or `DEFAULT_SCHEMA` as a last resort). Nodes/edges also get an optional
  `detail` field: one or two sentences of document-specific nuance (exact
  conditions, exceptions, figures) that label/type alone would lose —
  added because label/type extraction is a lossy summary, and GraphRAG
  answers were otherwise capped at whatever a short label could convey.
  Both steps parse LLM output via `parse_json_response` (strips markdown
  code fences, raises `ValueError` on bad JSON — every LLM-JSON caller in
  this codebase reuses this function rather than parsing independently).
  Only the schema is still a JSON file, at
  `backend/data/documents/{stem}/schema_v{N}.json` (one file per version,
  see `versions.json` in the same folder); nodes/edges are persisted in
  LadybugDB via `graphdb.write_graph`/`graphdb.load_graph`, not as
  `nodes.json`/`edges.json`. `save_graph()` calls `embed_nodes()` first,
  which embeds each node's `label`+`detail` text (batched into a single
  `embed_documents()` call) and attaches the resulting vector as an
  `embedding` field before handing nodes to `graphdb.write_graph` -- the
  embedding call happens here, not in `graphdb.py`, since that module
  owns storage only and never makes LLM/embedding calls itself.
  `summarize_document()` is a separate, lighter LLM call (a 2-3 sentence
  plain-text summary, not JSON) cached at `documents/{stem}/summary.json`
  via `save_document_summary`/`load_document_summary`, following the same
  regenerate-on-demand model as discovery above. `discover_ontology()` (the
  richer, exploratory "candidate ontology" pass — see its own module-level
  comment) and `generate_schema()` each send the whole document in one call
  and are bounded by `MAX_DOCUMENT_CHARS`; for a document with `chunks.json`
  (article-level JSON chunks from `app.chunking.chunk_markdown_file`),
  `main.py`'s `/api/ontology/{filename}/discover` and `.../schema` routes
  instead call `discover_ontology_from_chunks()`/`generate_schema_from_chunks()`,
  which both pack consecutive chunks into `MAX_CHUNK_GROUP_CHARS`-budgeted
  groups (`group_chunks_by_budget`), run the single-document function once
  per group (map), then fold every group's result into one unified set via a
  dedicated consolidation LLM call (reduce) — deliberately *not* trying to
  keep every group mutually consistent as it goes, since that would make
  each group's result depend on every earlier group's and prevent groups
  from being processed independently. For discovery, only
  `classes`/`relationships` (name+definition+category, no instance data) go
  through that consolidation call, since those are the only fields with a
  cross-group naming-collision problem (the same concept discovered twice
  under a different name in two groups); the other discovery fields
  (attributes/events/rules/terminology/competency_questions/warnings) are
  deduped in code by name/text instead. For schema generation, the
  consolidation call covers `node_types`/`edge_types` in full, since that's
  the entirety of a schema's shape — same merge-then-repoint-edges logic
  (`SCHEMA_CONSOLIDATION_PROMPT` in `prompts.py`), applied to a different
  output shape than discovery's `CONSOLIDATION_PROMPT`. Either way, a
  document small enough to fit in one group skips consolidation entirely and
  returns that group's result untouched, so the common case still costs
  exactly one LLM call.
- `graphrag.py` — the retrieval side of chat, a schema-aware search rather
  than plain keyword matching. Stage 1: `determine_relevant_types()`
  sends the document's schema + the question to the LLM, asking which
  node/edge *types* (by exact schema name) are relevant; empty result on
  both short-circuits immediately with no further LLM calls. Stage 2:
  `extract_keywords()` returns terms grouped by node type (e.g.
  `{"Person": ["Ada Lovelace"]}`, not a flat list), then for *each*
  relevant node type independently, three tiers are tried in order until
  one produces a match: (a) `find_relevant_nodes()` — that type's own
  keywords against that type's node labels; (b) `find_similar_nodes()` —
  if (a) found nothing, rank that type's own nodes by embedding
  similarity (`embed_query()`, computed lazily at most once per
  `search_graph()` call, reused across every type that needs it) against
  the question, keeping the top `EMBEDDING_FALLBACK_TOP_K` (5); (c)
  `all_nodes_of_types()` — if (b) also found nothing (most likely a
  document extracted before embeddings existed, so its nodes have no
  vector to rank by), every instance of just that type. This exists
  because keyword-substring matching only ever finds a *specific named*
  instance, so category questions ("what are the responsibilities?") or
  a question/document language mismatch would otherwise always miss even
  when the type is genuinely relevant and the graph clearly has matching
  data -- embedding similarity (b) catches most of these by meaning
  before falling all the way through to "every instance" (c). Edges
  follow the same shape one level up: `find_matching_edges()` picks up
  edges of the determined `edge_types` connected to an already-matched
  node, falling back to `all_edges_of_types()` only if node matching
  found nothing at all. The matched node set expands via
  `graphdb.expand_hops()` — an undirected, variable-length Cypher
  pattern match (`MATCH (n)-[*0..hops]-(m) ...`) run against LadybugDB —
  into an `Entities:`/`Relations:` context block (each line including the
  node's/edge's `detail` field when present — see above) injected into chat as a
  system message. `search_graph()` returns the determined
  `node_types`/`edge_types` and the matched/expanded `related_nodes`/
  `related_edges` alongside the context text; `main.py`'s `/api/chat`
  passes all four straight through as their own response fields rather
  than baking them into `content` as text, so the frontend can render
  them as clickable chips (type chips toggle that type's graph filter;
  node chips highlight+auto-pan to that node — see
  `OntologyGraph.vue`/`ChatPanel.vue` below) instead of parsing a
  fixed-format line. Once a document with an extracted graph is
  selected, finding nothing at either stage is reported as "관련된
  내용을 찾을 수 없습니다" rather than silently answering from the
  model's general knowledge — a deliberate behavior change from typical
  RAG fallback; a genuine technical failure (unparseable LLM JSON) is
  different from a miss and still falls back to plain chat.
- `telemetry.py` — `invoke_with_telemetry(operation, model, prompt)` wraps
  every chat-completion call site (chat answer, schema generation, graph
  extraction, type analysis, keyword extraction) and
  `embed_with_telemetry(operation, model, texts)` wraps both embedding
  call sites
  (`ontology.embed_nodes`, `graphrag.embed_query`) in an OpenTelemetry span.
  Both record model name plus the full prompt/response (or embedding input
  count/output count) as span attributes -- deliberately including the
  actual text, for debugging in Jaeger; this is fine only because this is
  local dev with no external collector. Both share a `_call_with_retry()`
  helper that retries the call up to `max_retries` (default 2) times, with
  a fixed delay, on `langchain_core.exceptions.ModelConnectionError` — the
  provider-agnostic base class langchain raises for connection-level
  failures — since transient OpenRouter connection errors are a real
  failure mode observed in this environment; any other exception is
  raised immediately, not retried. Telemetry only exports anywhere if
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set (podman-compose points it at the
  bundled Jaeger service); otherwise the OpenTelemetry API's no-op tracer
  is active, so both wrappers are always safe to call in tests.

**Testing LLM calls:** `get_chat_model`/`get_embedding_model` are imported
into each module's own namespace, so tests patch them per-module
(`app.ontology.get_chat_model`, `app.graphrag.get_chat_model`,
`app.main.get_chat_model`; `app.ontology.get_embedding_model`,
`app.graphrag.get_embedding_model`) rather than at their definitions in
`app.chat`/`app.embeddings`. Every test file whose code path can reach
`embed_nodes()`/`embed_query()` has an autouse fixture stubbing
`get_embedding_model` with a fake `embed_documents()`, so no test run ever
makes a real OpenRouter embeddings call even for tests that don't
specifically exercise the embedding fallback. A single `/api/chat` request
with `filename` set makes up to *three* chat LLM calls (type analysis,
keyword extraction, then the answer) — see `SequencedChatModel` in
`test_chat.py` for the fake used to test that (a list of canned responses,
one per `invoke()` call in order, with calls recorded for inspection) —
plus one embedding call if any determined node type's keyword match comes
up empty.

### Frontend (`frontend/src/`)

No state management library — `App.vue` owns all cross-component state
and wires five components together purely via props/emitted events:
`SettingsPanel` (model info, upload, document list, schema library, node
and edge type filters, GraphRAG hop count), `ChatPanel`, `DocumentPreview`,
`OntologyGraph`, `SchemaGraphPreview`. Reading `App.vue`'s props/emit
wiring is the fastest way to understand how a change in one panel reaches
another — e.g. selecting a document in `SettingsPanel` sets `parsedFile`
in `App.vue`, which flows down to `DocumentPreview`, `OntologyGraph`,
`SchemaGraphPreview`, and `ChatPanel` simultaneously; or a type/node chip
clicked in `ChatPanel`'s chat history flows the other way, through
`App.vue`, into `SettingsPanel`'s filter state or `OntologyGraph`'s
highlight/pan (see `graphrag.py` above and `docs/SPEC.md` for the full
wiring).

`OntologyGraph.vue` has three display modes driven by what's on the
backend for the current document, checked in this priority order:
extracted graph (`GET /api/ontology/{filename}` succeeds) → schema preview
(no extraction yet, but a schema exists — the schema's own types are drawn
as if they were nodes/edges) → placeholder. Rendering itself is delegated
to `v-network-graph`; this file converts data into that library's shape
and drives node positions with a `d3-force` simulation (charge + link +
center + collide forces), writing each tick's `{x, y}` into the
`layouts` ref that `v-network-graph` reads — layout is physics-based,
not computed once.

Component/unit logic (e.g. `ChunkView.vue`, `DocumentPreview.vue`'s view
toggle, `utils/chunkFormat.js`) has Vitest coverage — see "Frontend"
above. Full-stack behavior (a change actually working end-to-end against
the real backend) is still verified manually against the running
podman-compose stack, not via an end-to-end test suite.
