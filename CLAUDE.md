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
`OPENROUTER_API_KEY` (copy `backend/.env.example`). Give the machine at
least 4GB, ideally 6GB+ (`podman machine set --memory 6144`, machine must be
stopped first) -- podman's own default (2GiB, shared across every
container in this compose file plus podman's overhead) is not enough:
`graphdb.py`'s embedded LadybugDB creates one table per distinct node/edge
*type name* in a document's schema (see below), and committing a write
across a rich schema's many tables needs real resident memory regardless of
`graphdb.py`'s own `buffer_pool_size` cap -- confirmed via the VM's kernel
OOM-killer log (`podman machine ssh -- journalctl -k | grep -i oom`)
killing the backend process mid-COMMIT at ~1.1GB resident, for a document
whose schema had 23 node types/49 edge types. Frontend at
`localhost:5173`, backend at `localhost:8000`; the frontend dev server
proxies `/api` and `/health` to the backend container. LLM tracing
(`backend/.env`'s optional `LANGFUSE_*` vars) points at a separate,
self-hosted Langfuse server shared across projects on this machine, not
something `podman-compose.yml` itself runs -- see `docs/features/langfuse/LANGFUSE-spec.md`
before expecting traces to show up anywhere. Ladybug Explorer (a
GUI for browsing `backend/data/graph/graph.ladybugdb` directly via Cypher)
is at `localhost:8001`, running in `MODE=READ_ONLY` so it can stay up
alongside the backend without either side able to corrupt the other via a
write. Its image tag in `podman-compose.yml` must stay in sync with the
`ladybug` version pinned in `backend/requirements.txt` -- the explorer and
the embedded library have to agree on storage format to open the same file.
If graph queries in Explorer start failing oddly with both it and the
backend running, that's the same WAL-corruption failure mode as "LadybugDB
초기화" in the UI, not a new bug -- reset via that button. The most common
cause of this specific failure mode is the podman machine memory gotcha just
above: a write killed mid-COMMIT by the VM's OOM killer leaves the `.wal`
non-empty and never checkpointed, and every read against the database then
hangs forever (the module-level connection lock in `graphdb.py` is held by
the thread stuck inside the native call, so it never releases) rather than
erroring -- `/api/documents` (or any other route touching `graphdb.has_graph`)
timing out with zero CPU/disk activity in `podman stats` is the tell. Give
the machine more memory first and retry before reaching for the reset
button, which discards every document's extracted graph.

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

Tests never touch the real `backend/data` — `tests/conftest.py` sets
`ONTOLOGY_DATA_DIR` to a throwaway directory before any `app.*` module is
imported (a safety net for anything that runs at import time), and an autouse
fixture, `isolated_data_dir`, then gives *every test* an empty data directory
of its own (a `data/` folder under pytest's `tmp_path`) and drops the graph
database's cached connection on the way in and out. This works because
nothing caches the data directory: `app/utils/paths.py`'s `data_dir()` reads
`ONTOLOGY_DATA_DIR` every time a path is needed, and `graphdb.db_path()`,
`documents_dir()` and `domain_schemas_dir()` build on it (there is no
module-level `DATA_DIR`/`DOCUMENTS_DIR`/`DB_PATH` constant to go stale), so a
test never has to delete or recreate anything — what one test writes is simply
not there for the next. Do not remove or bypass this isolation. A test that
starts a worker process (e.g. the PDF converter in `test_samsunglife.py`) must
start it *after* the fixture has set the environment, since the child inherits
it — that file gives each test a pool of its own for this reason. To inspect
what a test wrote, use `data_dir()` inside the test, or the `isolated_data_dir`
fixture's value, rather than pointing at the project path.

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

`parser.py`, `chunking.py`, `embeddings.py`, and `goldenset.py` all live
under `app/preprocess/` — the document-preprocessing stages upstream of
`ontology`/`graphrag.py`, grouped into their own package (imported as
`app.preprocess.parser`, etc.) rather than by any shared code between them.
Two more single-concern packages exist the same way: `app/graph/` holds
`graphdb.py` and `graphrag.py` (the graph-DB layer and its retrieval logic),
and `app/llm/` holds `chat.py`, `prompts.py`, and `telemetry.py` (everything
about talking to an LLM that isn't itself a pipeline stage). `ontology.py`
is likewise a package, `app/ontology/`, split by concern into
`persistence.py`, `chunk_groups.py`, `generate_schema.py`, `extract_graph.py`,
`evolve_graph.py`, `utils.py`, and `domain_schema.py` --
`generate_schema.py`/`extract_graph.py`/`evolve_graph.py` used to be one
`extraction.py` module, split by pipeline stage once it grew large enough
that the three concerns (propose a schema, extract instances against it,
validate/evolve what was extracted) were easier to navigate as separate
files; `utils.py` holds what's shared across two or more of them
(the document-loading helpers, `MAX_DOCUMENT_CHARS`)
so each of the three stays scoped to its own stage. `chunk_groups.py` is
the one module behind which all three run their per-chunk-group map step:
`run_chunk_groups(stem=, stage=, operation=, group_fn=, reduce_fn=,
fingerprint_inputs=, max_chars=, reduce_stage=, summarize=)` loads the
document itself (its `chunks.json`, or with none its whole `raw.md` as a
single chunk group) and owns grouping by `MAX_CHUNK_GROUP_CHARS`, concurrent
execution (`map_concurrently`, capped by `MAX_CONCURRENT_LLM_CALLS`), the
one-group shortcut that skips `reduce_fn`, the progress file the frontend
polls (`ChunkProgress`/`load_progress`), and a *resume cache* at
`documents/{stem}/resume_cache/{stage}_{N}.json` -- one group result per
file, reused on a retry only when its *fingerprint* still matches: the
stage, the `operation`'s currently selected model and output-token ceiling
(added automatically), the caller's `fingerprint_inputs` (schema,
`document_type`, discovery hint, prompt text) and the group's own text. So
switching schema version, model or re-chunking can't silently reuse a
previous run's output. `max_chars` caps only an unchunked document's whole
text; it never changes how a chunked one is grouped. Each stage supplies
only its own `group_fn(group_text)`/`reduce_fn`; see
`CONTEXT.md` for the vocabulary (chunk group, group result, resume cache,
reduce)
(see that package's own `__init__.py` docstring for the full split; every
model call, chat or embedding, goes through `app.llm.calls`). `app.ontology.schema_validation` (normalization/validation
of a schema's `node_types`/`edge_types` shape) lives inside this same
package rather than as its own top-level module, since it's a leaf every
other `app.ontology` submodule reaches for, not a pipeline stage of its
own.

`app/utils/` holds cross-cutting helpers with no pipeline stage or route of
their own: `auth.py` (token issuing/checking), `paths.py` and `store.py`.
Together the last two are how everything under `backend/data` is located,
read and written:

- `paths.py` owns the *layout* — every file and folder name under a document
  folder (`documents/{stem}/`: `raw.md`, `raw0.md`, `source.pdf`, `chunks.json`,
  `versions.json`, `schema_v{N}.json`, `manifest.json`, `discovery.json`,
  `summary.json`, `goldenset*.json`, `progress/`, `resume_cache/`) and a domain
  folder (`domain_schemas/{domain}/`), as functions (`raw_path_for`,
  `versions_path`, `progress_path_for`, ...) built on `data_dir()`, plus the
  filename-derived helpers `stem_for`/`document_path_for` and
  `document_raw_files`/`pdf_only_document_dirs`, which know what counts as a
  document for `/api/files` and `/api/documents`. No other module spells out one of
  these names (the modules that used to define a path helper import it from
  here, so `from app.ontology import versions_path` still works).
- `store.py` owns the *I/O*: `write_text`/`write_bytes`/`write_json` write to a
  temp file beside the target and `os.replace` it, so a reader — a request
  thread, or the process converting a PDF — never sees a half-written file, and
  a failed write leaves the old file; `read_json(path, default)` returns the
  default for a missing file; `locked(key)` is a re-entrant per-key lock that
  serializes a read-modify-write (`versions.json` and `manifest.json` under the
  document's stem, goldenset answers likewise, a domain's files under
  `"domain:{name}"`). It is in-process only, which is enough: the server is one
  `uvicorn` process whose request threads are what overlap, and the one other
  writer, the PDF converter subprocess, only replaces whole files. Anything that
  reads-then-writes one of these files must hold `locked(...)` across both
  steps, as `create_schema_version` and `update_document_manifest` do.

- `parser.py` — the pdf -> markdown stage of document ingestion, all of it
  writing `backend/data/documents/{stem}/raw.md` (`app.utils.paths.document_dir_for`
  owns this per-document folder layout; every other per-document artifact
  -- schema versions, chunks, discovery, summary, manifest -- lives
  alongside it in that same folder). The `{stem}_raw.md`-shaped filename
  the rest of the app and the frontend pass around is a synthetic,
  stable identifier, decoupled from where the file actually sits on disk.
  Two independent conversion paths land here: `parse_to_markdown_file`
  (generic, via `anydoc`) and `convert_pdf_to_markdown_file` — a second,
  PDF-only path — `/api/parse`'s `converter=table_aware` field routes a
  `.pdf` upload through it instead of `anydoc`. It calls two PDF-only
  converters on the same bytes: `convert_insurance_policy_to_markdown`
  (pdfplumber-based, table/`제N조`-heading aware, ported from
  `scripts/data_prep/`'s Korean-insurance-policy tooling — see that
  directory's README for the heading/section heuristics and known
  limitations), saved as the document's actual `raw.md`; and
  `convert_general_pdf_to_markdown` (plain per-page text, no table
  detection or heading/bullet restructuring, for a PDF with no such
  structure to exploit), saved alongside as `raw0.md` — a reference copy
  for comparing the two conversions, not a document in its own right (it
  has no `document_dir_for` entry of its own and isn't picked up by
  `/api/documents`, which only looks for `raw.md`).
- `chunking.py` — the markdown -> chunked-json stage, picking up where
  `parser.py` leaves off: `chunk_markdown_file` splits a document's `raw.md`
  into per-article JSON chunks at `documents/{stem}/chunks.json` (`제N조`
  headings, rider/section detection) — a separate, on-demand step from
  parsing, triggered via `POST /api/documents/{filename}/chunk`.
- `goldenset.py` — per-document golden QA generation, adapted from
  `scripts/prepare_goldenset/prepare_goldenset.py` (the standalone CLI tool)
  so a single already-uploaded document can get one from a UI button
  (`POST /api/documents/{filename}/goldenset`) instead of only via that
  script's offline pass over a folder of Markdown files. `generate_goldenset`
  always reads the document's whole `raw.md`, never `chunks.json` — a golden
  set is the ground truth used to *validate* the chunk-grouped
  discover/schema/extract pipelines in `ontology.py` (see below), so building
  it the same chunked way would risk baking those pipelines' own blind spots
  into the ground truth meant to catch them. `compact_document_for_questions`
  still keeps the question-generation prompt inside a character budget for a
  large document, but does so by truncating *within* every section rather
  than dropping whole sections, so the LLM still sees a whole-document-shaped
  view; answer generation's evidence quotes are then re-verified in code
  against the full, uncompacted document text before being accepted, exactly
  like the standalone script. Result is cached at
  `documents/{stem}/goldenset.json` (`save_goldenset`/`load_goldenset`,
  regenerate-on-demand like `discovery.json`/`summary.json`); `list_documents`
  reports it as `has_goldenset` alongside `has_chunks`/`has_schema`/
  `has_graph`. The prompts (`QUESTION_PROMPT`/`ANSWER_PROMPT`) live in
  `prompts.py`, ported verbatim from the standalone script's own
  `prompts.py`. A second, separate concern in this module is *this app's own*
  generated answers to those golden questions -- not the golden answers
  themselves, which never change once generated. `record_goldenset_answer`
  appends one record per `POST /api/documents/{filename}/goldenset/{id}/answer`
  call (see `graphrag.py`'s `answer_question` below) to
  `documents/{stem}/goldenset_answers.json`, keyed by question id, each
  holding `schema_version`/`hops`/`generated_at` alongside the
  content/node_types/edge_types/related_nodes/related_edges the answer came
  with -- an append-only list per question, never overwritten, since an
  answer generated against schema version 3 is a real data point about how
  version 3 performed even after the schema moves to version 4.
  `latest_goldenset_answers(stem, active_schema_version)` is the read side:
  for each question, the most recent record whose `schema_version` equals
  the document's *current* active version specifically (`GET
  /api/documents/{filename}/goldenset/answers`) -- an answer generated
  against a since-changed schema is no longer shown as "the" current answer,
  though it stays in the file for later inspection.
- `chat.py` (`app/llm/chat.py`) — builds the `ChatOpenAI` client (OpenRouter) and converts
  `{role, content}` dicts to langchain messages; nothing outside
  `app.llm.calls` builds or invokes a chat model directly.
- `operations.py` + `calls.py` (`app/llm/`) — the one seam every model call
  goes through, each wrapped in `invoke_with_telemetry`/`embed_with_telemetry`:
  `call_json(operation, prompt)` for a reply that must be a JSON object
  (returns the parsed dict; `parse_json_response` lives here too: strips
  markdown code fences, handles the Responses-API content-block shape,
  raises `ValueError` on bad JSON, and raises `ValueError` if the reply isn't
  an object or lacks a field the operation declares),
  `call_text(operation, prompt)` for a prose reply (returns the text), and
  `embed(name, texts)` for embeddings (one vector per text; `name` is only the
  telemetry observation name, since an embedding call has no model choice or
  limit to register). An **operation** (see `CONTEXT.md`) is one entry in
  `operations.py`'s registry, the single place that holds its telemetry name,
  `max_tokens` ceiling (`None` = none beyond the model's own), required
  response fields, whether the settings UI offers a model picker for it
  (`selectable`, which derives `OPERATION_KEYS`), which other operation's
  model selection it follows (`model_key`), and `json`: a JSON operation
  (the default) makes `get_chat_model` add `response_format=
  {"type": "json_object"}` and low reasoning effort, a prose one
  (`answer_chat`, `summarize_document`) does not -- `call_json` and
  `call_text` each reject an operation of the other kind. Adding an LLM
  operation is adding one entry there. Nothing is retried beyond
  `invoke_with_telemetry`'s connection-error retry, so a truncated or
  malformed reply surfaces as a `ValueError`.
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
- `graphdb.py` (`app/graph/graphdb.py`) — owns the single LadybugDB connection
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
  arbitrarily. Every node and edge table also carries the optional
  *envelope* columns (`confidence`, `evidence_text`, `source_section`, the
  two offsets, `valid_from`/`valid_to`, and the open `properties` map), added
  by `ALTER TABLE ADD` so a table from before they existed ends up the same.
  They are defined once, in `_ENVELOPE_SCALAR_COLUMNS`: the DDL, the row values
  written, the CREATE field list (with the `CAST ... AS INT64` the engine needs
  for the offsets), the RETURN fields and the read-back are all built from
  that list, so adding a column is one line there --
  `test_every_envelope_column_survives_a_write_and_load_for_nodes_and_edges`
  fails if a new one isn't written or returned.
- `prompts.py` (`app/llm/prompts.py`) — every LLM prompt template this app sends, as plain string
  constants (with the design-rationale comments explaining why each one asks
  for what it does), kept separate from `ontology.py`'s extraction/storage
  logic so the prompt text can be read or edited on its own. `ontology.py`
  imports each constant it needs (`SCHEMA_PROMPTS`, `EXTRACT_PROMPT`,
  `VALIDATION_PROMPT`, `DISCOVERY_PROMPT`, `SUMMARY_PROMPT`,
  `EVOLUTION_PROMPT`, `CONSOLIDATION_PROMPT`, `SCHEMA_CONSOLIDATION_PROMPT`);
  `goldenset.py` imports `QUESTION_PROMPT`/`ANSWER_PROMPT` the same way.
- `ontology.py` (`app/ontology/`, a package — see below) — two LLM-driven steps, run separately by design: propose a
  schema (`node_types`/`edge_types`) for a document, then extract actual
  `nodes`/`edges` conforming to a schema (the document's own, a copied one,
  or `DEFAULT_SCHEMA` as a last resort). Nodes/edges also get an optional
  `detail` field: one or two sentences of document-specific nuance (exact
  conditions, exceptions, figures) that label/type alone would lose —
  added because label/type extraction is a lossy summary, and GraphRAG
  answers were otherwise capped at whatever a short label could convey.
  Both steps call the model via `call_json` (see `operations.py`/`calls.py`
  above), which owns JSON parsing and shape-checking for every LLM-JSON caller
  in this codebase.
  Only the schema is still a JSON file, at
  `backend/data/documents/{stem}/schema_v{N}.json` (one file per version,
  see `versions.json` in the same folder); nodes/edges are persisted in
  LadybugDB via `graphdb.write_graph`/`graphdb.load_graph`, not as
  `nodes.json`/`edges.json`. `save_graph()` writes them with no embedding;
  embedding is a separate pass, `embed_graph()` (`POST /api/ontology/{filename}/embed`),
  which reads the saved nodes back, has `embed_nodes()` embed each node's
  `label`+`detail` text (batched into a single `embed_documents()` call) and
  stores the vectors via `graphdb.update_node_embeddings` -- the embedding
  call happens here, not in `graphdb.py`, since that module owns storage
  only and never makes LLM/embedding calls itself.
  Each of the three pipeline stages' entry points (`discover_for_document`,
  `schema_for_document`, `extract_for_document`) owns its whole workflow, so
  `main.py`'s routes only shape the request/response, open the Langfuse
  `trace`, and map `FileNotFoundError`/`ValueError` to 404/400: discover
  saves `discovery.json`; schema loads the saved discovery hint when asked
  (`use_discovery`), saves the result as the next schema version, activates
  it, and returns `(schema, version)`; extract saves the graph for the
  active version (creating a `DEFAULT_SCHEMA` version first if there is none)
  and returns `(schema, graph, version)`. A node's/edge's `start_offset`/
  `end_offset` are always character offsets into the document's `raw.md` (the
  first occurrence of its `evidence_text`): `extract_graph()` itself reports
  offsets relative to whatever text it was handed -- for a chunk group, the
  `[path]`-labelled concatenation -- so `extract_for_document` re-anchors them
  after the merge, and drops only the offsets (keeping `evidence_text`) when
  the quote isn't in `raw.md` (chunking drops `---`/page-marker lines, so a
  quote can span one). A chunked document extracted before that re-anchoring
  stored group-frame offsets; re-running `/extract` fixes it.
  A route that needs a document's *current* schema or graph never resolves
  the active version itself: it asks `persistence.active_schema(stem)` for
  `(version, schema)` or `active_ontology(stem, load_graph=True)` for
  `(version, schema, graph)` (`load_graph=False` only checks that a graph
  exists, which is what `/api/chat`, the goldenset answer and `/embed` do --
  chat runs on every message and must not read the whole graph). They raise
  `SchemaNotFound` (no active version, or its `schema_v{N}.json` is gone) or
  `OntologyNotExtracted`, which two narrow handlers in `main.py` turn into
  404 `"schema not found"`/`"ontology not extracted yet"`; `/api/chat` catches
  them and falls back to a plain reply, and the goldenset answer turns them
  into its Korean 400. Domain convergence chooses its own seed the same way in
  one place: `converge_domain_schema(documents, seed_schema=None,
  document_type=...)` starts from `seed_schema` when given, otherwise seeds from
  `documents[0]` (`generate_schema` with that `document_type`'s prompt) and
  folds in only the rest; the stateless `/domain-schema/converge` route and
  `run_domain_convergence` (which passes the domain's stored schema as the
  seed, so `document_type` only matters for a brand-new domain) both call it.
  Domain convergence runs the whole-document functions, never the chunk-group
  runner, so a document over `MAX_DOCUMENT_CHARS` fails it with a 400.
  `summarize_document()` is a separate, lighter LLM call (a 2-3 sentence
  plain-text summary, not JSON) cached at `documents/{stem}/summary.json`
  via `save_document_summary`/`load_document_summary`, following the same
  regenerate-on-demand model as discovery above. `discover_ontology()` (the
  richer, exploratory "candidate ontology" pass — see its own module-level
  comment) and `generate_schema()` each take one chunk group's text in one
  call. `main.py`'s `/api/ontology/{filename}/discover` and `.../schema` routes
  call `discover_for_document()`/`schema_for_document()`, which run that
  function through `run_chunk_groups` -- for a document with `chunks.json`
  (article-level JSON chunks from `app.preprocess.chunking.chunk_markdown_file`)
  consecutive chunks are packed into `MAX_CHUNK_GROUP_CHARS`-budgeted
  groups (`group_chunks_by_budget`); a document with none is a single group
  holding its whole text, bounded by `MAX_DOCUMENT_CHARS`/`max_chars`, and
  gets the progress file and resume cache like any other. The single-document
  function runs once per group (map), then every group's result is folded
  into one unified set via a
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
  exactly one LLM call. `extract_graph()` (instance extraction, above) gets
  the same treatment via `extract_for_document()`, called from `main.py`'s
  `/api/ontology/{filename}/extract` route the same way --
  but its reduce step is deliberately code-only, not a second LLM call: a
  document's node/edge *count* scales with its length, unlike a schema's
  small, fixed-size type list, so folding potentially hundreds of instances
  back through an LLM wouldn't fit the same budget consolidation does for
  types. Instead, `_merge_group_graphs()` namespaces each group's node ids
  by group index (a node id is only ever unique within the group that
  produced it) and merges nodes across group boundaries by exact (type,
  label) match -- the same entity recurring in a later article is expected
  to reuse the document's own term for it verbatim, per EXTRACT_PROMPT's own
  "canonical surface form" instruction -- then rewrites every edge to point
  at the merged canonical ids and drops exact-duplicate edges.
- `graphrag.py` (`app/graph/graphrag.py`) — the retrieval side of chat, a schema-aware search rather
  than plain keyword matching. Stage 1: `determine_relevant_types()`
  sends the document's schema + the question to the LLM, asking which
  node/edge *types* (by exact schema name) are relevant; empty result on
  both short-circuits immediately with no further LLM calls. Stage 2:
  `extract_keywords()` returns terms grouped by node type (e.g.
  `{"Person": ["Ada Lovelace"]}`, not a flat list), then for *each*
  relevant node type independently, four tiers are tried in order until
  one produces a match -- declared once, as the ordered tuple
  `_NODE_MATCH_TIERS` of small functions over a per-search
  `_SearchContext`, so adding a tier is one function plus one entry:
  (a) `_by_keyword` (`find_relevant_nodes()`) — that type's own keywords
  against that type's node labels; (b) `_by_property_filter`
  (`find_nodes_by_property()`) — only when `analyze_question()` extracted a
  typed-property comparison for the type ("50% 이상인 보장"), which neither a
  label match nor similarity can answer; (c) `_by_embedding`
  (`find_similar_nodes()`) — rank that type's own nodes by embedding
  similarity to the question, keeping the top `EMBEDDING_FALLBACK_TOP_K` (5);
  the question's embedding (`embed_query()`) is computed lazily by the
  context, at most once per `search_graph()` call however many types reach
  this tier, and not at all when an earlier tier matched (both pinned by
  tests); (d) `_all_of_type` (`all_nodes_of_types()`) — if (c) also found
  nothing (most likely a document extracted before embeddings existed, so
  its nodes have no vector to rank by), every instance of just that type.
  This exists because keyword-substring matching only ever finds a
  *specific named* instance, so category questions ("what are the
  responsibilities?") or a question/document language mismatch would
  otherwise always miss even when the type is genuinely relevant and the
  graph clearly has matching data -- embedding similarity (c) catches most
  of these by meaning before falling all the way through to "every
  instance" (d). Edges
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
  `answer_question(messages, schema, stem, version, hops)` wraps
  `search_graph()` plus the augmented-context chat-answer call into one
  function returning `{content, node_types, edge_types, related_nodes,
  related_edges}` — factored out so `main.py`'s `/api/chat` (schema+graph
  path, full message history) and the goldenset per-question answer
  endpoint (`POST /api/documents/{filename}/goldenset/{id}/answer`, a
  single-message history) share exactly the same answering logic and can
  never silently drift apart.
- `telemetry.py` (`app/llm/telemetry.py`) — `invoke_with_telemetry(operation, model, prompt)` wraps
  every chat-completion call site (chat answer, schema generation, graph
  extraction, type analysis, keyword extraction) and
  `embed_with_telemetry(operation, model, texts)` wraps both embedding
  call sites (`ontology.embed_nodes`, `graphrag.embed_query`) in a
  **Langfuse** `generation`/`embedding` observation (the `langfuse`
  Python SDK — see `docs/features/langfuse/LANGFUSE-spec.md`). This replaced a raw-OpenTelemetry
  span sent to a bundled Jaeger container: Langfuse's SDK is itself
  OpenTelemetry-based (confirmed by pointing it at an unreachable host and
  observing its OTLP exporter's own retry/timeout log lines), but gives
  generation-level structure -- model name, full prompt/response text,
  and token usage as first-class fields rather than free-form span
  attributes -- plus cost accounting and per-question scoring in the UI,
  none of which a generic span gets you. Both record the actual
  prompt/response text (or embedding input count/output count) --
  deliberately including the actual text, for debugging; this is fine
  only because the Langfuse server this points at by default is
  self-hosted and not shared with anyone else (see `docs/features/langfuse/LANGFUSE-spec.md`).
  Both share a `_call_with_retry()` helper that retries the call up to
  `max_retries` (default 2) times, with a fixed delay, on
  `langchain_core.exceptions.ModelConnectionError` — the provider-agnostic
  base class langchain raises for connection-level failures — since
  transient OpenRouter connection errors are a real failure mode observed
  in this environment; any other exception is raised immediately, not
  retried, and marks the observation `level="ERROR"` with a
  `status_message` before re-raising. `configure_telemetry()` only
  constructs a real Langfuse client if `LANGFUSE_PUBLIC_KEY` is set (read
  from `backend/.env`, which podman-compose's `env_file:` line already
  wires up); otherwise both wrappers fall back to a local
  `_NoopObservation` stand-in exposing the same `update()`/context-manager
  shape, so both are always safe to call in tests. A third export,
  `trace(name, session_id=, tags=, metadata=, input=)`, opens one root
  Langfuse span per HTTP request in `main.py`'s route handlers (chat,
  discover, schema, extract, goldenset generate/answer) so the several
  `invoke_with_telemetry`/`embed_with_telemetry` calls one request can make
  (e.g. `/api/chat`'s `analyze-question` → `embed-query` → `answer-chat`)
  nest under one trace instead of each showing up as its own unrelated
  root -- see `docs/features/langfuse/LANGFUSE-spec.md`'s "Trace hierarchy" section, which
  includes a real captured example fetched via `langfuse-cli` and audited
  against Langfuse's own trace-instrumentation best practices. That
  guidance -- and the naming below -- comes from the `langfuse` Agent
  Skill vendored into this repo at `.claude/skills/langfuse/` (from
  github.com/langfuse/skills); reach for it again for any future Langfuse
  work (further instrumentation, prompt migration, dataset/eval setup) so
  the approach stays current with Langfuse's own guidance rather than
  whatever was true when this was written. Per-observation names
  (`answer-chat`, `generate-schema`, `analyze-question`, ...) are short,
  verb-first, dash-case strings per that guidance too, not the
  `ontology.generate_schema`-style dotted names this module used before.

**Testing LLM calls:** `app.llm.calls` binds `get_chat_model` and
`get_embedding_model` in its own namespace, so every test patches exactly two
names, never per caller: `app.llm.calls.get_chat_model` (for every operation,
JSON or prose -- its fake takes the operation name: `lambda operation=None:
model`) and `app.llm.calls.get_embedding_model` (`lambda: model`). The fake
models live once in `backend/tests/fakes.py` (`FakeChatModel`,
`RecordingChatModel`, `SequencedChatModel`, `LoggingSequencedChatModel`,
`FakeEmbeddingModel`, plus `prompt_text` for asserting on a captured prompt);
a file defines its own only when it needs different behaviour. `conftest.py`
has an autouse fixture installing `FakeEmbeddingModel` as the embedding model
for every test, so no test run ever makes a real OpenRouter embeddings call
even for tests that don't specifically exercise the embedding fallback; a
test that cares about the vectors patches the same name itself. A single `/api/chat` request
with `filename` set makes up to *two* chat LLM calls (question analysis, then
the answer; one fake at the one patch point serves both) — see `SequencedChatModel` in
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

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`sungcheolkim78/ontology_builder`, via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
