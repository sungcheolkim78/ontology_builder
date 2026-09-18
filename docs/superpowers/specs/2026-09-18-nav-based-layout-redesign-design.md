# Navigation-Based Layout Redesign

## Goal

Replace the current fixed 2×2 grid layout (`ChatPanel` / `DocumentPreview` /
`OntologyGraph` / `SchemaGraphPreview`, all visible at once, resizable via
drag handles) with a left navigation rail plus a single full-bleed main
content pane that swaps by selected nav item. The 2×2 grid was cramped —
every one of the four panels (chat, document reading, graph exploration)
benefits from more screen space than a quarter of the viewport allows.

New nav items: **File Explorer**, **Preview**, **Ontology**, **Chat**,
**Settings**. Selecting one shows that item's content full-width/height in
the main pane; only one is visible at a time.

This is a **structural** redesign (how panels relate to each other and to
`App.vue`'s state), not a visual-language redesign — it reuses the
Tailwind classes/tokens established in the prior UI redesign
(`2026-08-28-tailwind-css-ui-redesign-design.md` /
`...-phase2-design.md`) as-is.

## Current state (what's being replaced)

`App.vue` renders `SettingsPanel` as a fixed left column, and a 2×2 CSS
grid to its right holding `ChatPanel` (top-left), `DocumentPreview`
(top-right), `OntologyGraph` (bottom-left), `SchemaGraphPreview`
(bottom-right), with two drag handles to resize the column/row split.

`SettingsPanel.vue` (2129 lines) is the single largest component and
currently owns, all at once:
- File upload + document list + a `showFileExplorer`-gated floating
  window for document management/pipeline-stage badges
- Global model config (model catalog, per-operation model overrides)
- GraphRAG hop count, markdown-render toggle, node/edge type filter
  checkboxes
- Every ontology workflow action and its state: discover, schema
  generation, extraction, embedding, validation, evolution proposal/apply,
  domain-schema convergence, domain pending-review, goldenset generation

`DocumentPreview.vue` already has a raw/chunk/pdf **toggle** (one visible
at a time) with a "jump to PDF" button that computes a page number from
`raw.md`'s `<!-- page: N -->` markers (`utils/pdfHighlight.js`'s
`pageForLine`) and passes `{ page, text, id }` to `PdfViewer.vue`, which
does its own on-page text search/highlight.

`ChatPanel.vue` has no PDF-citation feature today — related-node/edge
chips only emit `highlight-nodes` (consumed by `OntologyGraph` to
highlight+pan) and `toggle-type` (consumed by `App.vue`'s
`graphFilters`/`edgeGraphFilters`).

Backend note (no backend changes needed for this spec): extracted
nodes/edges already carry an optional `evidence_text` field — an exact,
backend-verified-verbatim quote from the document
(`app.ontology.extraction._find_evidence_span`) — persisted in LadybugDB
and returned as part of `related_nodes`/`related_edges` in `/api/chat`'s
response (`graphdb.expand_hops` → `graphrag.search_graph` →
`graphrag.answer_question`). This is the text Chat's new PDF-citation
panel searches for.

## Navigation & overall layout

`App.vue` adds one new piece of state, `activeView` (`'files' | 'preview'
| 'ontology' | 'chat' | 'settings'`), defaulting to `'files'`. The header
(title + selected filename) is unchanged. Below it, the current
`flex`-row-of-(`SettingsPanel`, 2×2-grid) is replaced with a
`flex`-row-of-(`NavSidebar`, main-content-pane):

- **`NavSidebar.vue`** (new) — presentation-only. Renders the five nav
  items (icon + label), highlights whichever matches `activeView`, emits
  `nav-select` with the chosen view id. No business logic, no knowledge of
  what each view contains.
- **Main content pane** — a `<component :is>`-style switch (or five
  `v-if` blocks) rendering exactly one of `FileExplorerView`,
  `PreviewView`, `OntologyWorkflowView`, `ChatView`, `SettingsView` based
  on `activeView`. Each view is full-bleed within the pane (no more
  resizable drag handles — those, and the `colPercent`/`rowPercent`/
  `gridRef`/`startColResize`/etc. code in `App.vue`, are deleted).

All of `App.vue`'s existing shared state (`parsedFile`, `graphFilters`,
`edgeGraphFilters`, `availableTypes`, `availableEdgeTypes`,
`schemaVersion`, `schemaRefreshRequest`, `graphRagHops`, `renderMarkdown`,
`highlightedNodeIds`, `toggleTypeRequest`, `toggleEdgeTypeRequest`) is
kept as-is and threaded down into whichever view needs it — the
prop/emit contracts of `OntologyGraph.vue` and `SchemaGraphPreview.vue`
do not change, only what wraps them does.

Selecting a document in File Explorer does not force-navigate to another
view — the user picks the document, then separately picks where to look
at it. (`parsedFile` becoming non-null is what other views key off to
stop showing their "no document selected" empty state.)

## `SettingsPanel.vue` split

`SettingsPanel.vue`'s ~2129 lines map onto three of the new views, so it
is split into three new files (old file deleted):

1. **`FileExplorerView.vue`** — upload form, document list, per-document
   pipeline-stage badges, delete/rename if present. This is today's
   `showFileExplorer`-gated floating content, promoted to a real view
   (the floating-window state/markup goes away entirely).
2. **`OntologyWorkflowView.vue`** — see below.
3. **`SettingsView.vue`** — model catalog + selected model, per-operation
   model override controls, GraphRAG hop count control, markdown-render
   toggle. Emits the same `hops-changed`/`markdown-changed` events
   `App.vue` already listens for today.

Each new file keeps the relevant slice of `SettingsPanel.vue`'s existing
`ref`/`reactive` state and API calls verbatim — this is a mechanical
split by responsibility, not a rewrite of the underlying logic.

## Preview view

**`PreviewView.vue`** (new), replacing `DocumentPreview.vue`'s
toggle-based UI:

- **When the selected document has a PDF** (`file.has_pdf`): a left/right
  split. Left = `PdfViewer.vue` (unchanged component). Right = a tab strip
  with **원문 (MD)** and **청크 (Chunk)** tabs (`ChunkView.vue` unchanged;
  MD tab reuses `DocumentPreview.vue`'s existing marked-rendering +
  scroll-position tracking code). Selecting a chunk, or scrolling the MD
  tab, computes a jump request via the existing `pageForLine` and pushes
  it to the left `PdfViewer` — same mechanism as today's "이 줄을 PDF에서
  보기" button, just always-visible instead of behind a manual toggle.
- **When there is no PDF**: no split — just today's MD/Chunk toggle
  behavior, unchanged. (Showing a markdown fallback pane *and* an MD tab
  side by side would duplicate the same content, so the split only
  appears when there's an actual second source — the PDF — to show
  alongside the markdown/chunk view.)

## Ontology view

**`OntologyWorkflowView.vue`** (new) — left step-sidebar + main area:

- **Step sidebar**: the core pipeline as an ordered list — discover →
  generate schema → extract → validate/evolve — each step's existing
  trigger button/status/error display from `SettingsPanel.vue`, carried
  over as-is. Below the core steps, a collapsed-by-default **"Advanced"**
  section holds domain-schema convergence (calibration file selection,
  converge, pending-review apply) and goldenset generation — actions used
  far less often than the core per-document workflow.
- **Main area**: `OntologyGraph.vue` and `SchemaGraphPreview.vue`
  (unchanged components) plus a node/edge **detail inspector** — when a
  node or edge is selected/clicked in the graph, its full properties
  (label, type, detail, confidence, evidence_text, source_section, etc.)
  show in a side panel. `OntologyGraph.vue` already tracks node selection
  via v-network-graph's own `v-model:selected-nodes` (`selectedNodes` ref)
  but has no equivalent for edges, and doesn't surface either selection to
  its parent today. This adds a `v-model:selected-edges` binding (mirroring
  the existing node one) plus a `defineEmits` (e.g.
  `node-selected`/`edge-selected`, firing on the corresponding `watch`)
  surfacing the clicked item's full data up to `OntologyWorkflowView.vue`
  to render in the inspector, rather than a new fetch.
- Node/edge type filter checkboxes (today in `SettingsPanel.vue`, driving
  `graphFilters`/`edgeGraphFilters`) move here, next to the graph they
  filter.

## Chat view

**`ChatView.vue`** (new, thin layout wrapper) — left/right split:

- Left: `ChatPanel.vue` (unchanged core chat UI/logic), plus one addition:
  clicking a related-node/edge chip now emits a new `cite-evidence` event
  carrying `{ text }`, where `text` is that item's `evidence_text` if
  present, else its `label` (same fallback precedence
  `DocumentPreview.vue`'s existing jump-to-PDF uses when a line has no
  richer text to search for). The existing `highlight-nodes`/`toggle-type`
  emits are unchanged and still update `App.vue`'s
  `highlightedNodeIds`/`graphFilters` — so the corresponding node/edge is
  already pre-highlighted whenever the user later switches to the
  Ontology view (no forced auto-navigation).
- Right: `PdfViewer.vue` when the document has a PDF, else a new small
  **`MarkdownEvidenceViewer.vue`** showing the raw markdown with the
  cited quote wrapped in `<mark>` and scrolled into view.
- `ChatView.vue` fetches the document's raw text once per selected file
  (same `/api/files/{filename}` call `DocumentPreview.vue` already makes
  independently — no shared cache introduced, consistent with how each
  panel fetches its own data today) so it can resolve `evidence_text`
  into a page number.

**New util**: `pdfHighlight.js` gains `pageForQuote(rawText, lines,
quote)` — finds `rawText.indexOf(quote)` (evidence_text is guaranteed
verbatim per the backend's own verification), converts that character
offset to a line number by counting newlines up to it, then delegates to
the existing `pageForLine(lines, lineNumber)`. Returns `1` (and lets
`PdfViewer`'s own search silently no-op) if the quote isn't found — same
graceful-miss behavior `PdfViewer.vue` already has for any jump request
whose text doesn't match on the target page.

## Data flow summary

No backend/API changes. No changes to `OntologyGraph.vue`'s or
`SchemaGraphPreview.vue`'s existing props/emits (only new ones added to
`OntologyGraph.vue` for selection, additive). `ChatPanel.vue` gains one
new emit (`cite-evidence`); all its existing props/emits are unchanged.
`App.vue` adds `activeView` and a handler that sets it from
`NavSidebar`'s `nav-select`; every other existing ref/handler in
`App.vue` is unchanged, just re-wired into the new view components
instead of the old grid cells.

## Error handling / edge cases

- No document selected: each view shows its own empty state, matching
  today's per-panel "업로드된 문서가 없습니다" / "선택된 문서 없음"
  pattern.
- Chat citation when `evidence_text` is absent (older extractions, or a
  node/edge type that never carries it): fall back to `label`, same as
  above; if even that fails to match, the citation panel just doesn't
  highlight anything (page 1 / top of document), it never errors.
- Switching `activeView` away from and back to Ontology preserves
  `highlightedNodeIds`/filters (they live in `App.vue`, not inside
  `OntologyWorkflowView.vue`), so a chat-triggered highlight survives
  until the user clears it, same lifetime as today.
- Views are mounted/unmounted on switch (`v-if`, not `v-show`) — matches
  every existing panel's own current behavior of refetching on becoming
  visible/on file change, so no new caching/staleness concerns are
  introduced.

## Testing

- `NavSidebar.vue`: new Vitest spec — renders five items, emits
  `nav-select` with the right id, highlights the active one.
- `PreviewView.vue`: new specs covering the has-PDF split (chunk
  selection jumps the PDF) and the no-PDF fallback (behaves like today's
  `DocumentPreview` toggle). Existing `DocumentPreview.test.js` cases move
  here (renamed) to the extent their assertions still apply.
- `pdfHighlight.test.js`: add cases for `pageForQuote` (found on page 1,
  found on a later page via a page marker, not-found fallback to page 1).
- `ChatPanel.vue` / `ChatView.vue`: new spec asserting a chip click emits
  `cite-evidence` with `evidence_text` when present and falls back to
  `label` when absent.
- `FileExplorerView.vue`, `OntologyWorkflowView.vue`, `SettingsView.vue`:
  carry over whatever existing `SettingsPanel` test coverage applies to
  their slice (split the same way the implementation is split); no new
  behavior is being introduced by the split itself, so no new tests are
  required purely for the move.
- Manual verification against the running podman-compose stack for the
  full nav flow (per `CLAUDE.md`), since cross-view layout/visual
  behavior isn't covered by Vitest.
