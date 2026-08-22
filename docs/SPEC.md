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
`app/parser.py` (document → markdown conversion), and `app/ontology.py`
(schema generation + node/edge extraction).

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
| POST | `/api/ontology/{filename}/schema` | LLM proposes a node/edge type schema for the document |
| POST | `/api/ontology/{filename}/extract` | LLM extracts nodes/edges per the saved schema |
| GET | `/api/ontology/{filename}` | Read back the saved nodes/edges |

**`POST /api/chat`** — body `{"messages": [{"role": "user"|"assistant"|"system", "content": "..."}]}`.
Converts to langchain messages, calls `ChatOpenAI` pointed at
`https://openrouter.ai/api/v1` (model from `OPENROUTER_MODEL` env var,
default `openai/gpt-4o-mini`), returns
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

**`POST /api/ontology/{filename}/extract`** — loads
`graph/{stem}/schema.json` (400 if it doesn't exist yet — generate the
schema first), prompts the LLM to extract nodes/edges from the
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
`firecrawl-anydoc`, `python-multipart`.
`requirements-dev.txt` adds `pytest`, `httpx` for testing.

### Tests

`backend/tests/` (pytest, run via `python -m pytest`): `test_chat.py`,
`test_config.py`, `test_files.py`, `test_ontology.py`, `test_parse.py`.
Chat/parse/ontology tests mock the external calls (`get_chat_model`,
`anydoc.to_markdown_bytes`); file tests use the real filesystem.

## Frontend (`frontend/`)

Vue 3 + Vite. Dashboard layout in `src/App.vue`, split into four
components under `src/components/`.

```
┌──────────┬─────────────────────┬──────────────────┐
│          │                     │  Document Preview │
│ Settings │       Chat          ├──────────────────┤
│ (280px)  │     (flexible)      │  Ontology Graph   │
└──────────┴─────────────────────┴──────────────────┘
```

- **`SettingsPanel.vue`** — reads `/api/config` for the active model
  name (read-only) and `/api/files` for the list of previously parsed
  documents on mount. A file input posts to `/api/parse`, adds the
  result to the top of the list, and selects it. Clicking any list
  item emits `file-selected` (`{filename, path}`), highlighting it.
  Renders one filter checkbox per entry in the `availableTypes` prop
  (the real node types of whatever graph is currently loaded — nothing
  hardcoded); toggling emits `filters-changed`.
- **`ChatPanel.vue`** — self-contained message list + input, calls
  `/api/chat` with the full local history on each send.
- **`DocumentPreview.vue`** — takes the `file` prop (`{filename, path}`),
  fetches `/api/files/{filename}`, renders it as HTML via `marked`.
  Uses an always-visible (non-overlay) scrollbar — see Known
  Limitations history for why.
- **`OntologyGraph.vue`** — takes `file` and `enabledTypes` props. On
  file change, `GET /api/ontology/{filename}`: 404 shows "스키마 생성"/
  "그래프 추출" buttons (two explicit steps — schema first, then
  extraction — so the schema can be inspected/reused); 200 renders the
  real nodes/edges as SVG (circular layout, colored by type). Emits
  `types-available` with the sorted unique node types whenever graph
  data (re)loads, so the filter checkboxes in `SettingsPanel` can be
  built from real data instead of a fixed list.

State lives in `App.vue`: `parsedFile` (selected/uploaded document),
`graphFilters` (enabled node types, a `Set`), `availableTypes` (from
`OntologyGraph`'s `types-available`, passed down to `SettingsPanel`).
A draggable `.resizer` between the chat column and the right column
(document preview + ontology graph) adjusts `rightColumnWidth` via
plain mousedown/mousemove/mouseup, clamped to 260–800px. Chat messages
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
