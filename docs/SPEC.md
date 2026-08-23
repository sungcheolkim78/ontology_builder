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
             (via langchain,         doc → markdown         {name}_raw.md
          chat + schema/extract)                     graph/{stem}/{schema,nodes,edges}.json
```

Both services run as separate containers via `podman-compose.yml`,
each with source volume-mounted for hot-reload during development.

## Backend (`backend/`)

FastAPI app in `app/main.py`, split into `app/chat.py` (LLM chat),
`app/parser.py` (document → markdown conversion), `app/ontology.py`
(schema generation + node/edge extraction), `app/graphrag.py`
(keyword extraction + graph-based retrieval for chat), and
`app/telemetry.py` (OpenTelemetry tracing for every LLM call).

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
| GET | `/api/config` | Current LLM model name → `{"model": "..."}` |
| POST | `/api/chat` | Chat with the LLM |
| POST | `/api/parse` | Upload a document, convert to markdown |
| GET | `/api/files` | List parsed documents, newest first |
| GET | `/api/files/{filename}` | Read back a saved markdown file |
| GET | `/api/ontology/schemas` | List every document stem that has a saved schema |
| POST | `/api/ontology/{filename}/schema` | LLM proposes a node/edge type schema for the document |
| POST | `/api/ontology/{filename}/schema/use` | Copy another document's schema onto this one |
| GET | `/api/ontology/{filename}/schema` | Read back the saved schema |
| POST | `/api/ontology/{filename}/extract` | LLM extracts nodes/edges per the saved (or default) schema |
| GET | `/api/ontology/{filename}` | Read back the saved nodes/edges |

**`POST /api/chat`** — body
`{"messages": [{"role": "user"|"assistant"|"system", "content": "..."}], "filename": "...", "hops": 1}`.
`filename`/`hops` are optional. Plain chat (no `filename`, or the
document has no schema/graph yet) works exactly as it always has:
messages go straight to `ChatOpenAI`. When `filename` names a document
that has *both* a schema and an extracted graph, `graphrag.search_graph()`
runs a schema-aware, two-stage search before the chat call:

1. **Type analysis** — `determine_relevant_types()` sends the whole
   schema plus the question to the LLM, asking which `node_types`/
   `edge_types` (by exact name, from the schema) are relevant. Any
   name the LLM returns that isn't actually in the schema is dropped
   (defends against hallucinated types). If *both* lists come back
   empty, the search stops here — no keyword extraction, no second LLM
   call.
2. **Instance search** — `extract_keywords()` (unchanged from before:
   a separate LLM call) pulls entities/terms out of the question, and
   `find_relevant_nodes()` matches them against node labels
   (case-insensitive substring), but now pre-filtered to only nodes
   whose `type` is in the determined `node_types` — a node type the
   analysis step didn't flag can't match, even if its label happens to
   overlap a keyword. `find_matching_edges()` separately picks up
   edges whose `type` is in the determined `edge_types` *and* that
   connect to an already-matched node (edges have no text of their own
   to keyword-match against). Matched edges contribute their other
   endpoint back into the matched-node set.
3. **Fallback to "all instances of the type"** — if instance search
   comes up completely empty (no node matched, no edge matched) *but*
   at least one type was determined relevant, `all_nodes_of_types()` /
   `all_edges_of_types()` pull in every node/edge of the determined
   types, unfiltered by keyword. This exists because keyword-substring
   matching only ever finds a *specific named instance* — it has
   nothing to match against category questions like "what are the
   responsibilities?" (no keyword will ever appear inside long
   descriptive Responsibility labels), or when the question and
   document are in different languages (a Korean keyword won't
   substring-match an English label even when the concept is
   identical). Real bug report that motivated this: "어떤 학위가
   필요한 잡인가요?" and "what responsibilies?" both correctly
   identified their relevant types in stage 1 but got zero keyword
   matches in stage 2, so every such question reported "not found"
   despite the graph clearly having the answer. Only when a determined
   type has *zero actual instances* in the graph does the search still
   end empty after this fallback — a genuine miss.
4. **Expansion** — the resulting node set (whichever stage produced
   it) is expanded `hops` steps via `nx.ego_graph(..., undirected=True)`,
   and the resulting subgraph is formatted as an `Entities:`/`Relations:`
   text block, injected as a `system` message ahead of the conversation.
   Each entity/relation line appends the node's or edge's `detail` text
   when present (`- label (type): detail`), so the final answer isn't
   limited to whatever a short label conveys — this is what lets a
   question like "PhD 없이도 지원 가능한가요?" surface an exception
   clause ("동등한 업계 경력이 있는 경우... 예외적으로 고려될 수
   있습니다") that a bare `Requirement` label/type could never carry.

`format_type_preview()` renders the determined types as a fixed-format
line — `[관련 타입 분석] 노드: {...} / 엣지: {...}` (또는 "없음") — that's
**always prepended to the assistant's reply** when this path runs, so
the type-analysis step is visible, not just an internal implementation
detail. If nothing was found at any stage (no relevant types, or a
relevant type with zero actual instances), the reply is the preview
line plus "관련된 내용을 찾을 수 없습니다." and the chat model is never
called for a final answer — this is a deliberate behavior change from
a bare GraphRAG setup: once a document with a graph is selected, a
miss is reported as a miss rather than silently answering from the
model's general knowledge. A technical failure (LLM returns unparseable
JSON at either stage) is different from a miss and falls back to plain
chat, same as when there's no graph at all.

**`POST /api/parse`** — multipart upload, field `file`. Extracts the
extension from the filename (sanitized via `os.path.basename` to
prevent path traversal), calls `anydoc.to_markdown_bytes(data, ext)`,
saves the result to `backend/data/{stem}_raw.md`, returns
`{"filename": "...", "path": "data/..."}` (content is not included in
the response — fetch it separately via `/api/files/{filename}`).
`anydoc.ConvertError` and `ValueError` (e.g. unrecognized extension)
both map to HTTP 400.

**`GET /api/files`** — lists `backend/data/*` (excluding dotfiles like
`.gitkeep`), sorted by modification time, newest first:
`{"files": [{"filename": "..."}]}`.

**`GET /api/files/{filename}`** — plain-text read of
`backend/data/{filename}`, 404 if missing, `basename`-sanitized against
path traversal.

**`POST /api/ontology/{filename}/schema`** — reads
`backend/data/{filename}`, prompts the LLM (same `get_chat_model()` as
chat) to propose an ontology schema for that document, parses the
response as JSON (stripping markdown code fences if present), saves it
to `backend/data/graph/{stem}/schema.json` (`stem` = filename without
extension), and returns it. Schema shape:
`{"node_types": [{"name", "description"}], "edge_types": [{"name", "description", "source", "target"}]}`.
404 if the document doesn't exist; 400 if the LLM's response isn't
parseable/well-shaped JSON.

**`GET /api/ontology/schemas`** — scans `backend/data/graph/*/schema.json`,
returns `{"schemas": [{"stem": "..."}]}` for the "스키마 라이브러리" list
in `SettingsPanel`.

**`POST /api/ontology/{filename}/schema/use`** — body
`{"source_stem": "..."}`. Loads `graph/{source_stem}/schema.json` (404
if that source has no schema) and saves it as
`graph/{stem}/schema.json`, i.e. designates it the active schema for
`filename`. Returns the copied schema.

**`GET /api/ontology/{filename}/schema`** — reads back
`graph/{stem}/schema.json`; 404 if none has been generated/assigned
yet. Used by the frontend to show schema status and to drive the
"schema preview" graph mode before extraction has run.

**`POST /api/ontology/{filename}/extract`** — loads
`graph/{stem}/schema.json`; if none exists, falls back to
`DEFAULT_SCHEMA` (a generic `Entity`/`RELATED_TO` schema) and persists
it as this document's schema rather than erroring, so "extract" always
produces *something*. Prompts the LLM to extract nodes/edges from the
document conforming to that schema, saves `graph/{stem}/nodes.json`
and `edges.json`, returns `{"nodes": [...], "edges": [...]}`. Node
shape `{"id", "label", "type", "detail"}`, edge shape
`{"source", "target", "type", "detail"}` (`source`/`target` are node
ids). `detail` is an LLM-written free-text field — one or two
sentences of anything from the document that the label/type alone
loses (exact conditions, exceptions, figures, dates) — optional and
often empty; it exists because label/type is a lossy summary and
GraphRAG answers were otherwise limited to whatever a short label
could convey (see the GraphRAG section below). 400 on
unparseable/malformed LLM JSON. No validation that node/edge types
actually match the schema — the LLM output is trusted structurally
only (must have the right list/dict shape). Graphs extracted before
this field existed simply have no `detail` on their nodes/edges;
re-running "그래프 추출" is the only way to backfill it, there's no
migration.

**`GET /api/ontology/{filename}`** — reads back the saved
`nodes.json`/`edges.json`; 404 if extraction hasn't run yet.

### Configuration

- `OPENROUTER_API_KEY` (required), `OPENROUTER_MODEL` (optional,
  default `openai/gpt-4o-mini`) — read from `backend/.env`
  (git-ignored; `backend/.env.example` documents the format).
- `OTEL_EXPORTER_OTLP_ENDPOINT` (optional) — set by `podman-compose.yml`
  to Jaeger's OTLP HTTP receiver; unset in any other environment
  (including local pytest runs) disables tracing entirely rather than
  erroring.

### Dependencies

`requirements.txt`: `fastapi`, `uvicorn`, `langchain-openai`,
`firecrawl-anydoc`, `python-multipart`, `networkx`, `opentelemetry-api`,
`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`.
`requirements-dev.txt` adds `pytest`, `httpx` for testing.

### Tests

`backend/tests/` (pytest, run via `python -m pytest`): `test_chat.py`,
`test_config.py`, `test_files.py`, `test_graphrag.py`, `test_ontology.py`,
`test_parse.py`, `test_telemetry.py`. Chat/parse/ontology/graphrag tests
mock the external calls (`get_chat_model`, `anydoc.to_markdown_bytes`);
telemetry tests use a bare fake model (no OTel mocking needed — the
default no-op tracer is already the behavior under test); file tests use
the real filesystem. `test_chat.py`'s GraphRAG tests use a
`SequencedChatModel` fake that returns a different canned response per
`invoke()` call (in order) and records the messages it was called
with, since one `/api/chat` request with `filename` set makes two LLM
calls (keyword extraction, then the actual answer).

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

- **`SettingsPanel.vue`** — reads `/api/config` for the active model
  name (read-only) and `/api/files` for the list of previously parsed
  documents on mount. A file input posts to `/api/parse`, adds the
  result to the top of the list, and selects it. Clicking any list
  item emits `file-selected` (`{filename, path}`), highlighting it.
  Renders one filter checkbox per entry in the `availableTypes` prop
  (the real node types of whatever graph is currently loaded — nothing
  hardcoded); toggling emits `filters-changed`. Also reads
  `GET /api/ontology/schemas` for a "스키마 라이브러리" list (every schema
  generated so far, across all documents); clicking one calls
  `POST /api/ontology/{selectedFilename}/schema/use` to copy it onto
  the currently selected document, then emits `schema-used`. Refetches
  the schema list whenever its `schemaVersion` prop changes. Also
  renders a "GraphRAG 설정" number input (1–5, default 1) for the
  retrieval hop count, emitting `hops-changed` on change, and a "채팅
  표시 설정" checkbox (default checked) for whether chat messages render
  as HTML markdown or plain text, emitting `markdown-changed`.
- **`ChatPanel.vue`** — self-contained message list + input, calls
  `/api/chat` with the full local history on each send, plus the
  `file`/`hops` props (`filename` and `hops` in the request body) so
  the backend can run GraphRAG against the currently selected
  document's graph. The `renderMarkdown` prop (from `SettingsPanel`'s
  toggle) switches each message between `marked.parse(...)` piped
  through `v-html` and a plain `<p>` with `white-space: pre-wrap` — same
  unsanitized-`v-html` approach as `DocumentPreview.vue`, consistent
  with that existing precedent rather than a new one. User and
  assistant messages get distinct bubble backgrounds (`.message.user`
  vs `.message.assistant`, keyed off the same `role` string already
  used for the "나"/"챗봇" label) so a message's origin is visually
  obvious without reading the label.
- **`DocumentPreview.vue`** — takes the `file` prop (`{filename, path}`),
  fetches `/api/files/{filename}`, renders it as HTML via `marked`.
  Uses an always-visible (non-overlay) scrollbar — see Known
  Limitations history for why.
- **`OntologyGraph.vue`** — takes `file`, `enabledTypes`, and
  `schemaVersion` props. On file change, checks
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
  "그래프 추출 완료 (노드 X개, 엣지 Y개)"). Emits `types-available` with
  the sorted unique node types of
  whatever is currently drawn (schema or real graph), so
  `SettingsPanel`'s filter checkboxes always match what's on screen,
  and `schema-updated` after a successful generate/extract so `App.vue`
  can bump `schemaVersion` (which also tells `SettingsPanel` to refresh
  its schema library list). Rendering is delegated to the
  [`v-network-graph`](https://dash14.github.io/v-network-graph/) library
  (`<v-network-graph :nodes :edges :layouts :configs>`), which fills
  100% of its container and provides pan/zoom/node-drag for free —
  `displayNodes`/`displayEdges` (schema-preview or real graph, filtered
  by `enabledTypes`) are converted into the id-keyed objects the
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
`graphFilters` (enabled node types, a `Set`), `availableTypes` (from
`OntologyGraph`'s `types-available`, passed down to `SettingsPanel`),
`schemaVersion` (bumped by either `OntologyGraph`'s `schema-updated` or
`SettingsPanel`'s `schema-used`, and passed to `SettingsPanel` and
`SchemaGraphPreview` as a refresh signal), `graphRagHops` (from
`SettingsPanel`'s `hops-changed`, passed to `ChatPanel`), `renderMarkdown`
(from `SettingsPanel`'s `markdown-changed`, passed to `ChatPanel`). Chat
messages stay local to `ChatPanel`.

`vite.config.js` proxies `/api` and `/health` to `http://backend:8000`
(the compose service name) so the browser only ever talks to
`localhost:5173`.

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

## Known limitations / not yet built

- **No persistence beyond the filesystem.** Parsed markdown and
  extracted graphs live under `backend/data/`; there is no database,
  no per-user/session separation, and chat history is not saved
  anywhere.
- **No auth.** All endpoints are open; fine for local dev only.
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
- **GraphRAG instance matching is a naive substring match**, not
  embeddings or fuzzy matching — keywords the LLM extracts have to
  substantially overlap with a node's `label` text to hit. The
  all-instances-of-type fallback (see above) covers the case where
  this finds nothing but the type is genuinely relevant; it does *not*
  help when a question needs a *specific* instance the keyword
  extraction simply mis-extracted (e.g. mangled a name) — that still
  reads as "not found." A GraphRAG-augmented chat turn costs up to
  three LLM calls (type analysis, keyword extraction, then the answer)
  versus one for plain chat — the type-analysis step alone is enough
  to short-circuit to "not found" without the other two if nothing in
  the schema looked relevant.
