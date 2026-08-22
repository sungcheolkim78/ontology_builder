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
                         ┌─────────────────┼─────────────────┐
                         ▼                 ▼                 ▼
                  OpenRouter API      anydoc (Rust)      backend/data/
                  (via langchain)    doc → markdown      {name}_raw.md
```

Both services run as separate containers via `podman-compose.yml`,
each with source volume-mounted for hot-reload during development.

## Backend (`backend/`)

FastAPI app in `app/main.py`, split into `app/chat.py` (LLM chat) and
`app/parser.py` (document → markdown conversion).

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check → `{"status": "ok"}` |
| GET | `/api/hello` | Scaffold sample endpoint |
| GET | `/api/config` | Current LLM model name → `{"model": "..."}` |
| POST | `/api/chat` | Chat with the LLM |
| POST | `/api/parse` | Upload a document, convert to markdown |
| GET | `/api/files/{filename}` | Read back a saved markdown file |

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

**`GET /api/files/{filename}`** — plain-text read of
`backend/data/{filename}`, 404 if missing, `basename`-sanitized against
path traversal.

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
`test_config.py`, `test_files.py`, `test_parse.py`. Chat/parse tests
mock the external calls (`get_chat_model`, `anydoc.to_markdown_bytes`);
file tests use the real filesystem.

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

- **`SettingsPanel.vue`** — reads `/api/config` to show the active
  model name (read-only), a file input that posts to `/api/parse` and
  emits `file-parsed`, and checkboxes (`Person`/`Organization`/`Concept`)
  that emit `filters-changed`.
- **`ChatPanel.vue`** — self-contained message list + input, calls
  `/api/chat` with the full local history on each send.
- **`DocumentPreview.vue`** — takes the `file` prop (`{filename, path}`
  from a parse response), fetches `/api/files/{filename}`, renders it
  as HTML via `marked`.
- **`OntologyGraph.vue`** — takes an `enabledTypes` Set prop; renders a
  **hardcoded dummy dataset** (6 nodes, 6 edges) as a plain SVG
  (circular layout, straight edges), filtering nodes/edges by type. No
  real ontology extraction exists yet — see Known Limitations.

State (`parsedFile`, `graphFilters`) lives in `App.vue` and flows down
via props/emit; chat messages stay local to `ChatPanel`.

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

## Known limitations / not yet built

- **Ontology graph is dummy data.** No extraction pipeline exists that
  turns a parsed document into actual entities/relations. This is the
  next major piece of work.
- **No persistence beyond the filesystem.** Parsed markdown lives in
  `backend/data/`; there is no database, no per-user/session
  separation, and chat history is not saved anywhere.
- **No auth.** All endpoints are open; fine for local dev only.
- **No streaming chat.** Responses return in one shot.
- **No automated frontend tests.** Frontend changes are verified
  manually / via Playwright, not a test suite.
