# Ontology Extraction Pipeline

## Goal

Replace the dummy ontology graph with a real, LLM-driven pipeline: from
a selected parsed document, first generate an ontology schema, then
extract nodes/edges according to that schema, persist both, and render
the result in `OntologyGraph.vue`.

## Flow

```
select document -> [Generate schema] -> LLM proposes schema -> data/graph/{stem}/schema.json
                                              |
                        [Extract graph] -> LLM extracts nodes/edges using schema
                                              |
                data/graph/{stem}/nodes.json, edges.json -> rendered in OntologyGraph.vue
```

`{stem}` = the parsed filename without its extension, e.g.
`report_raw.md` -> `report_raw` -> `backend/data/graph/report_raw/`.

Two explicit user-triggered steps (not one auto-chained action), so the
schema can be inspected/reused before extraction runs.

## Backend (`backend/app/ontology.py`, new)

Reuses `chat.get_chat_model()` — same LLM/config as the chat feature.

### Data shapes

- Schema: `{"node_types": [{"name": str, "description": str}], "edge_types": [{"name": str, "description": str, "source": str, "target": str}]}`
  (`source`/`target` reference a `node_types` name.)
- Node: `{"id": str, "label": str, "type": str}`
- Edge: `{"source": str, "target": str, "type": str}` (`source`/`target` reference a node `id`)

### Endpoints

- `POST /api/ontology/{filename}/schema` — reads `backend/data/{filename}`,
  prompts the LLM to propose a schema for that document, parses the
  response as JSON (stripping markdown code fences if present), saves
  to `data/graph/{stem}/schema.json`, returns it. Parse failure -> 400.
- `POST /api/ontology/{filename}/extract` — loads `schema.json` (400 if
  missing, with a message telling the user to generate it first),
  prompts the LLM to extract nodes/edges from the document per that
  schema, parses JSON, saves `nodes.json` + `edges.json` under
  `data/graph/{stem}/`, returns `{"nodes": [...], "edges": [...]}`.
  Parse failure -> 400.
- `GET /api/ontology/{filename}` — reads back saved `nodes.json`/
  `edges.json`; 404 if extraction hasn't run yet.

No retry-on-parse-failure — a failed parse just returns 400 and the
user re-clicks the button. No schema/graph validation beyond structural
JSON shape (list of dicts with the expected keys) — trust the LLM
output otherwise; malformed shape -> 400.

### Tests

TDD, pytest. LLM calls are mocked (`get_chat_model`) the same way
`test_chat.py` does. Cover: happy path for each endpoint, missing
schema on extract (404), malformed LLM JSON (400) for both schema and
extract.

## Frontend

- **`OntologyGraph.vue`** — dummy dataset removed. Takes `file` prop;
  on file change, `GET /api/ontology/{filename}`:
  - 404 -> show "아직 추출된 온톨로지가 없습니다" plus "스키마 생성" /
    "그래프 추출" buttons.
  - 200 -> render real nodes/edges (same SVG circular-layout approach
    as before, now with real IDs/types).
  - "스키마 생성" -> `POST .../schema`. "그래프 추출" -> `POST .../extract`
    (shows an error inline, e.g. "스키마를 먼저 생성하세요", if the
    backend 400s/404s because schema is missing).
  - On successful extract, emits `types-available` with the sorted
    unique list of node types found, and re-renders the graph.
- **`SettingsPanel.vue`** — the hardcoded `Person/Organization/Concept`
  filter checkboxes are removed. Takes an `availableTypes: string[]`
  prop instead and renders one checkbox per type; empty list shows a
  placeholder. Filter state (which types are enabled) still lives here
  and is emitted via `filters-changed`, as before.
- **`App.vue`** — new pass-through: listens for `types-available` from
  `OntologyGraph`, stores it, passes it down to `SettingsPanel` as
  `available-types`. Resets `graphFilters` to "all enabled" whenever
  the type list changes.

## Out of scope

- No validation that extracted node/edge types actually match the
  saved schema (trust the LLM for now).
- No retry/backoff on LLM JSON parse failure.
- No editing of the generated schema through the UI — regenerate
  (overwriting `schema.json`) if it's wrong.
- No token/length limits on document content sent to the LLM.
