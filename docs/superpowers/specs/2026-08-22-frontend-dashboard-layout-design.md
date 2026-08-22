# Frontend Dashboard Layout

## Goal

Replace the single-page chat view with a 3-column dashboard: settings
sidebar, chat (center), document preview + ontology graph (right,
stacked). This is the workspace layout for the ontology-builder app
going forward.

## Layout

```
┌──────────┬─────────────────────┬──────────────────┐
│          │                     │  Document Preview │
│ Settings │       Chat          ├──────────────────┤
│ (280px)  │     (flexible)      │  Ontology Graph   │
└──────────┴─────────────────────┴──────────────────┘
```

## Components

- `App.vue` — 3-column layout container, owns shared state.
- `components/SettingsPanel.vue` — shows the configured LLM model name
  (read-only), a document upload button (calls `POST /api/parse`), and
  checkboxes to filter ontology graph node types.
- `components/ChatPanel.vue` — existing chat logic moved here
  unchanged (self-contained message state).
- `components/DocumentPreview.vue` — renders the markdown of the most
  recently parsed document.
- `components/OntologyGraph.vue` — renders a hardcoded set of dummy
  nodes/edges as an SVG graph, filterable by node type.

## State

Owned in `App.vue`, passed down via props/emit:

- `parsedFile: {filename, path} | null` — set when `SettingsPanel`
  emits a successful upload.
- `graphFilters: Set<string>` — selected node types, updated by
  `SettingsPanel` checkboxes.

Chat messages stay local to `ChatPanel` — no other panel needs them.

## Backend addition

`GET /api/files/{filename}` — returns the raw text of
`backend/data/{filename}`. 404 if missing. Filename is resolved via
`os.path.basename` to prevent path traversal, same pattern as the
existing parser.

## Ontology graph data

No extraction pipeline exists yet. `OntologyGraph.vue` uses a
hardcoded dummy dataset (a handful of `Person`/`Organization`/`Concept`
nodes and a few edges) rendered as plain SVG (circular layout, straight
edges) — no new graph library. Revisit once real extraction exists and
requirements are clearer.

## Markdown rendering

Add the `marked` package to render `DocumentPreview` content as HTML.

## Testing

- Backend: TDD, pytest, real filesystem (no mocks needed — it's a
  direct file read).
- Frontend: manual verification via podman-compose + Playwright
  (upload → preview renders → graph filter checkboxes toggle nodes).
  No frontend test framework exists yet in this project; not adding
  one for this change.

## Out of scope

- Real ontology extraction (graph stays dummy data).
- Temperature / other chat settings (only model name display + upload
  + graph filters, per user decision).
- Mobile/responsive layout — this is an internal desktop tool.
