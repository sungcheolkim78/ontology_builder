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
             (via langchain,         doc → markdown         {name}_raw.md,
          chat + schema/extract)                       graph/{stem}/schema.json,
                                              graph/graph.ladybugdb (nodes/edges)
```

Both services run as separate containers via `podman-compose.yml`,
each with source volume-mounted for hot-reload during development.

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
`firecrawl-anydoc`, `python-multipart`, `networkx`, `ladybug`,
`opentelemetry-api`, `opentelemetry-sdk`,
`opentelemetry-exporter-otlp-proto-http`. `ladybug` is the embedded
Cypher-native graph database (`graphdb.py`) that stores extracted
nodes/edges; `networkx` remains a declared dependency but is no longer
imported anywhere in `app/` — graph search and hop expansion are now
Cypher queries via `graphdb.py`, not in-memory `networkx` graphs.
`requirements-dev.txt` adds `pytest`, `httpx` for testing.

### Tests

`backend/tests/` (pytest, run via `python -m pytest`): `test_chat.py`,
`test_config.py`, `test_files.py`, `test_graphdb.py`, `test_graphrag.py`,
`test_ontology.py`, `test_parse.py`, `test_telemetry.py`.
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
  hardcoded); toggling emits `filters-changed`. A parallel "그래프 엣지
  필터" section does the same for `availableEdgeTypes`/
  `edge-filters-changed`. Both filter sets are still owned locally
  (`enabledTypes`/`enabledEdgeTypes` reset to "everything on" whenever
  the corresponding `available*Types` prop changes, e.g. after a new
  extraction), but can also be driven externally: the `toggleTypeRequest`/
  `toggleEdgeTypeRequest` props each carry a fresh `{type}` object on
  every change (a new object each time, so the same type clicked twice
  in a row still triggers a watcher fire) and a watcher calls the same
  `toggleType()`/`toggleEdgeType()` a checkbox click would — this is how
  `ChatPanel`'s type-analysis chips (see below) reach the filter state
  that actually lives here, via `App.vue` as a relay. Also reads
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
  Limitations history for why.
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

- **No persistence beyond `backend/data/`, and no server-based
  database.** Parsed markdown and schemas live as flat files there;
  extracted graphs live in an embedded LadybugDB database file
  (`backend/data/graph/graph.ladybugdb`) rather than JSON, but it's
  still local to the backend container/filesystem — there is no
  per-user/session separation, and chat history is not saved anywhere.
- **No migration from the pre-LadybugDB JSON storage.** Documents
  extracted before graph storage moved to LadybugDB have leftover
  `graph/{stem}/nodes.json` / `edges.json` files on disk; the new code
  never reads or deletes them, so they linger harmlessly but
  permanently. Such documents behave as if never extracted
  (`GET /api/ontology/{f}` → 404) until re-extracted, which populates
  LadybugDB and leaves the stale JSON files in place, unused.
- **No DDL garbage collection.** Re-extracting a document under a
  different schema (different type names) leaves the old type's NODE/
  REL tables behind, now with zero rows for that document — and if no
  other document ever used that type, zero rows at all. Tables are
  never dropped.
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
