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
(schema generation + node/edge extraction), and `app/graphrag.py`
(keyword extraction + graph-based retrieval for chat).

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
`filename`/`hops` are optional; when `filename` names a document with an
extracted graph (`GET /api/ontology/{filename}` would succeed), the
last user message is run through GraphRAG before the chat call:
1. `graphrag.extract_keywords()` asks the LLM for entities/terms in the
   question (JSON array).
2. `graphrag.retrieve_graph_context()` matches those keywords against
   node labels (case-insensitive substring), builds a `networkx.DiGraph`
   from the saved nodes/edges, and expands each matched node via
   `nx.ego_graph(..., radius=hops, undirected=True)`. The union of all
   expanded neighborhoods, plus every edge among them, is formatted as
   an `Entities:` / `Relations:` text block.
3. If any context was found, it's injected as a `system` message
   prepended to the conversation (in Korean: "다음은 문서에서 추출된
   관련 정보입니다:\n{context}") before the normal chat call.

If `filename` is omitted, the graph hasn't been extracted yet, no
keywords match, or keyword extraction fails to parse, this silently
falls back to plain chat — no error surfaces to the user. Converts the
(possibly context-prefixed) messages to langchain messages, calls
`ChatOpenAI` pointed at `https://openrouter.ai/api/v1` (model from
`OPENROUTER_MODEL` env var, default `openai/gpt-4o-mini`), returns
`{"role": "assistant", "content": "..."}`. Non-streaming. Conversation
history is not persisted server-side — the frontend resends the full
message list on every request.

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
shape `{"id", "label", "type"}`, edge shape
`{"source", "target", "type"}` (`source`/`target` are node ids). 400 on
unparseable/malformed LLM JSON. No validation that node/edge types
actually match the schema — the LLM output is trusted structurally
only (must have the right list/dict shape).

**`GET /api/ontology/{filename}`** — reads back the saved
`nodes.json`/`edges.json`; 404 if extraction hasn't run yet.

### Configuration

- `OPENROUTER_API_KEY` (required), `OPENROUTER_MODEL` (optional,
  default `openai/gpt-4o-mini`) — read from `backend/.env`
  (git-ignored; `backend/.env.example` documents the format).

### Dependencies

`requirements.txt`: `fastapi`, `uvicorn`, `langchain-openai`,
`firecrawl-anydoc`, `python-multipart`, `networkx`.
`requirements-dev.txt` adds `pytest`, `httpx` for testing.

### Tests

`backend/tests/` (pytest, run via `python -m pytest`): `test_chat.py`,
`test_config.py`, `test_files.py`, `test_graphrag.py`, `test_ontology.py`,
`test_parse.py`. Chat/parse/ontology/graphrag tests mock the external
calls (`get_chat_model`, `anydoc.to_markdown_bytes`); file tests use
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
  retrieval hop count, emitting `hops-changed` on change.
- **`ChatPanel.vue`** — self-contained message list + input, calls
  `/api/chat` with the full local history on each send, plus the
  `file`/`hops` props (`filename` and `hops` in the request body) so
  the backend can run GraphRAG against the currently selected
  document's graph.
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
  Emits `types-available` with the sorted unique node types of
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
  separate color palette from nodes), and initial node positions are a
  circular layout we compute once per node (`layouts`, a plain ref the
  library also mutates on drag). Edge labels need more than config —
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
`SettingsPanel`'s `hops-changed`, passed to `ChatPanel`). Chat messages
stay local to `ChatPanel`.

`vite.config.js` proxies `/api` and `/health` to `http://backend:8000`
(the compose service name) so the browser only ever talks to
`localhost:5173`.

## Deployment (dev)

`podman-compose.yml` defines two services:

- **backend** — builds `backend/Dockerfile` (`python:3.12-slim`,
  `uvicorn --reload`), port 8000, `env_file: backend/.env`, volumes for
  `app/` and `data/` (hot-reload + host-visible parse output).
- **frontend** — builds `frontend/Dockerfile` (`node:20-slim`, `vite`
  dev server), port 5173, volumes for `src/`, `index.html`,
  `vite.config.js`, `depends_on: backend`.

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
- **GraphRAG node matching is a naive substring match**, not embeddings
  or fuzzy matching — keywords the LLM extracts have to substantially
  overlap with a node's `label` text to hit. Every GraphRAG-augmented
  chat turn costs an extra LLM call (keyword extraction) beyond the
  answer itself.
