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
proxies `/api` and `/health` to the backend container.

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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Then:

```bash
source .venv/bin/activate
OPENROUTER_API_KEY=dummy python -m pytest tests/ -v      # all tests
OPENROUTER_API_KEY=dummy python -m pytest tests/test_chat.py::test_chat_returns_assistant_reply -v  # single test
```

`OPENROUTER_API_KEY` only needs to be set (never a real key) — every test
mocks the LLM call rather than hitting OpenRouter. Tests run directly
against the venv, not inside a container.

### Frontend

No lint/build/test commands are wired up beyond `vite`. `npm run dev` /
`npm run build` work if you want to run outside the container, but the
normal workflow is editing files on the host and letting the bind-mounted
container's Vite dev server hot-reload them (see the gotcha above when it
doesn't).

## Architecture

### Backend module boundaries (`backend/app/`)

`main.py` holds all routes and wires the other modules together; it has no
business logic of its own beyond request/response shaping.

- `parser.py` — `anydoc` converts an uploaded document to markdown, saved
  as `backend/data/{stem}_raw.md`.
- `chat.py` — builds the `ChatOpenAI` client (OpenRouter) and converts
  `{role, content}` dicts to langchain messages. Every other module that
  needs an LLM call imports `get_chat_model` from here.
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
  Persisted under `backend/data/graph/{stem}/{schema,nodes,edges}.json`.
- `graphrag.py` — the retrieval side of chat, a two-stage search rather
  than plain keyword matching. Stage 1: `determine_relevant_types()`
  sends the document's schema + the question to the LLM, asking which
  node/edge *types* (by exact schema name) are relevant; empty result on
  both short-circuits immediately with no further LLM calls. Stage 2:
  `extract_keywords()` + `find_relevant_nodes()`/`find_matching_edges()`
  search for specific instances of those types. If that finds nothing,
  `all_nodes_of_types()`/`all_edges_of_types()` fall back to *every*
  instance of the determined types rather than reporting nothing found —
  this exists because keyword-substring matching only ever finds a
  *specific named* instance, so category questions ("what are the
  responsibilities?") or a question/document language mismatch would
  otherwise always miss even when the type is genuinely relevant and the
  graph clearly has matching data. The matched node set expands via
  `nx.ego_graph(radius=hops, undirected=True)` into an `Entities:`/
  `Relations:` context block (each line including the node's/edge's
  `detail` field when present — see above) injected into chat as a
  system message, prefixed with a fixed preview line
  (`format_type_preview()`) showing the determined types. Once a
  document with an extracted graph is selected, finding nothing at
  either stage is reported as "관련된 내용을 찾을 수 없습니다" rather
  than silently answering from the model's general knowledge — a
  deliberate behavior change from typical RAG fallback; a genuine
  technical failure (unparseable LLM JSON) is different from a miss and
  still falls back to plain chat.
- `telemetry.py` — `invoke_with_telemetry(operation, model, prompt)`
  wraps every LLM call site (all five: chat answer, schema generation,
  graph extraction, type analysis, keyword extraction) in an
  OpenTelemetry span recording model/prompt-length/response-length/
  success metadata (never the prompt or response text). Only exports
  anywhere if `OTEL_EXPORTER_OTLP_ENDPOINT` is set (podman-compose points
  it at the bundled Jaeger service); otherwise the OpenTelemetry API's
  no-op tracer is active, so this is always safe to call in tests. Also
  retries up to `max_retries` (default 2) times, with a fixed delay,
  on `langchain_core.exceptions.ModelConnectionError` — the
  provider-agnostic base class langchain raises for connection-level
  failures — since transient OpenRouter connection errors are a real
  failure mode observed in this environment; any other exception is
  raised immediately, not retried.

**Testing LLM calls:** `get_chat_model` is imported into each module's own
namespace, so tests patch it per-module (`app.ontology.get_chat_model`,
`app.graphrag.get_chat_model`, `app.main.get_chat_model`) rather than at
its definition in `app.chat`. A single `/api/chat` request with `filename`
set makes *two* LLM calls (keyword extraction, then the answer) — see
`SequencedChatModel` in `test_chat.py` for the fake used to test that (a
list of canned responses, one per `invoke()` call in order, with calls
recorded for inspection).

### Frontend (`frontend/src/`)

No state management library — `App.vue` owns all cross-component state
and wires four components together purely via props/emitted events:
`SettingsPanel` (model info, upload, document list, schema library, node
filters, GraphRAG hop count), `ChatPanel`, `DocumentPreview`,
`OntologyGraph`. Reading `App.vue`'s props/emit wiring is the fastest way
to understand how a change in one panel reaches another — e.g. selecting a
document in `SettingsPanel` sets `parsedFile` in `App.vue`, which flows
down to `DocumentPreview`, `OntologyGraph`, and `ChatPanel` simultaneously.

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

No automated frontend tests exist — changes are verified manually or via
Playwright against the running podman-compose stack, not a test suite.
