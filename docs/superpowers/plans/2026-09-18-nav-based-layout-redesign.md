# Navigation-Based Layout Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `App.vue`'s fixed `SettingsPanel` + 2×2 resizable grid (`ChatPanel`/`DocumentPreview`/`OntologyGraph`/`SchemaGraphPreview`) with a left `NavSidebar` + single full-bleed main pane that swaps between five views (File Explorer, Preview, Ontology, Chat, Settings) based on a new `activeView` state. `SettingsPanel.vue` (2129 lines) is split by responsibility into three of those views; `DocumentPreview.vue` is replaced by a PDF-aware `PreviewView.vue`; Chat gains a PDF/markdown evidence-citation side panel.

**Architecture:** Purely structural — no backend changes, no new visual language (reuses the existing Tailwind tokens/component classes from `frontend/src/style.css` as-is). Every existing component's business logic (`ref`/`computed`/`watch`/API calls) moves verbatim into its new host file; only the surrounding layout/wiring changes. `OntologyGraph.vue`/`SchemaGraphPreview.vue`'s existing props/emits are unchanged (only additive new ones on `OntologyGraph.vue`).

**Tech Stack:** Vue 3 (`<script setup>`), Vite, Tailwind CSS v3 (existing `@apply`-based component classes), `v-network-graph`, `lucide-vue-next` (already a dependency, used for icons elsewhere — reuse for `NavSidebar.vue`'s icons), Vitest + `@vue/test-utils`.

**Spec:** `docs/superpowers/specs/2026-09-18-nav-based-layout-redesign-design.md`

## Global Constraints

- No backend/API changes anywhere in this plan.
- No changes to `OntologyGraph.vue`'s or `SchemaGraphPreview.vue`'s *existing* props/emits — `OntologyGraph.vue` only gains new, additive ones (`v-model:selected-edges`, `node-selected`, `edge-selected`).
- `ChatPanel.vue` keeps every existing prop/emit; it only gains one new emit (`cite-evidence`).
- Every `ref`/`reactive`/`computed`/`watch`/function moved out of `SettingsPanel.vue` is moved **verbatim** (same names, same logic) into its new host file — this is a mechanical split by responsibility, not a rewrite. Only the `<template>` markup each function drives moves with it; no new business logic is introduced except where a task explicitly says so (node/edge inspector, `pageForQuote`, `cite-evidence`, `ChunkView.vue`'s new `chunk-selected` emit).
- `SettingsPanel.vue` and `DocumentPreview.vue` are read-only reference material until the final task — every earlier task only *creates* new files by reading them, never edits or deletes them. This avoids three views racing to modify the same soon-to-be-deleted file. Both old files (and `DocumentPreview.test.js`) are deleted only in the final integration task, once every one of their replacements exists and passes its own tests.
- Views render via `v-if` (mount/unmount on switch), never `v-show` — matches every existing panel's current behavior of loading fresh on becoming visible/on file change.
- No automated test exists for `App.vue` itself (none exists today either) — the final task's integration/wiring is verified manually against the running `podman-compose` stack per `CLAUDE.md`, not via Vitest.
- Every new/rewritten component's props take a `file: { type: Object, default: null }` object (matching `ChatPanel.vue`/`DocumentPreview.vue`/`OntologyGraph.vue`/`SchemaGraphPreview.vue`'s existing convention) rather than a bare `selectedFilename` string, for consistency — including `FileExplorerView.vue`, which currently would only need the filename but takes the whole object like its siblings.
- Line numbers cited below refer to `frontend/src/components/SettingsPanel.vue`, `frontend/src/components/OntologyGraph.vue`, `frontend/src/components/DocumentPreview.vue`, `frontend/src/components/ChatPanel.vue`, and `frontend/src/App.vue` as they exist on the `frontend` branch at the time this plan was written (commit `cd97bff`). Re-open each file and confirm the referenced code before editing — earlier tasks in this plan never touch these files, so the line numbers should still hold when you reach each task, but always verify rather than trust blindly.

## File map

**New:**
- `frontend/src/components/NavSidebar.vue`
- `frontend/src/components/FileExplorerView.vue`
- `frontend/src/components/SettingsView.vue`
- `frontend/src/components/OntologyWorkflowView.vue`
- `frontend/src/components/PreviewView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/MarkdownEvidenceViewer.vue`
- `frontend/src/components/__tests__/NavSidebar.test.js`
- `frontend/src/components/__tests__/PreviewView.test.js`

**Modified:**
- `frontend/src/App.vue`
- `frontend/src/components/OntologyGraph.vue`
- `frontend/src/components/ChatPanel.vue`
- `frontend/src/components/ChunkView.vue`
- `frontend/src/utils/pdfHighlight.js`
- `frontend/src/utils/__tests__/pdfHighlight.test.js`
- `frontend/src/components/__tests__/ChatPanel.test.js`

**Deleted (final task only):**
- `frontend/src/components/SettingsPanel.vue`
- `frontend/src/components/DocumentPreview.vue`
- `frontend/src/components/__tests__/DocumentPreview.test.js` (superseded by `PreviewView.test.js`)

---

### Task 1: `NavSidebar.vue` — presentation-only nav rail

**Files:**
- Create: `frontend/src/components/NavSidebar.vue`
- Create: `frontend/src/components/__tests__/NavSidebar.test.js`

**Interfaces:**
- Produces: a `nav-select` emit consumed by `App.vue` in Task 8. No other task depends on this one; it can be built and tested fully in isolation.

- [ ] **Step 1: Create `NavSidebar.vue`**

```vue
<script setup>
import { FileText, MessageSquare, Network, Settings, UploadCloud } from 'lucide-vue-next'

defineProps({
  activeView: { type: String, default: 'files' },
})
const emit = defineEmits(['nav-select'])

const ITEMS = [
  { id: 'files', label: 'File Explorer', icon: UploadCloud },
  { id: 'preview', label: 'Preview', icon: FileText },
  { id: 'ontology', label: 'Ontology', icon: Network },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'settings', label: 'Settings', icon: Settings },
]
</script>

<template>
  <nav class="flex w-[76px] flex-shrink-0 flex-col items-stretch gap-1 border-r border-border bg-surface py-2">
    <button
      v-for="item in ITEMS"
      :key="item.id"
      type="button"
      :data-testid="`nav-item-${item.id}`"
      class="flex flex-col items-center gap-1 rounded-md px-1.5 py-2.5 text-[10px] font-medium transition-colors"
      :class="activeView === item.id
        ? 'bg-accent-muted/60 text-ink'
        : 'text-ink-faint hover:bg-white/5 hover:text-ink-muted'"
      @click="emit('nav-select', item.id)"
    >
      <component :is="item.icon" :size="18" />
      {{ item.label }}
    </button>
  </nav>
</template>
```

No emits besides `nav-select`; no API calls; no knowledge of what any view contains — matches the spec's "presentation-only" requirement exactly.

- [ ] **Step 2: Add `NavSidebar.test.js`**

```js
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import NavSidebar from '../NavSidebar.vue'

describe('NavSidebar', () => {
  it('renders all five nav items', () => {
    const wrapper = mount(NavSidebar, { props: { activeView: 'files' } })
    for (const id of ['files', 'preview', 'ontology', 'chat', 'settings']) {
      expect(wrapper.find(`[data-testid="nav-item-${id}"]`).exists()).toBe(true)
    }
  })

  it('highlights the active item', () => {
    const wrapper = mount(NavSidebar, { props: { activeView: 'chat' } })
    expect(wrapper.find('[data-testid="nav-item-chat"]').classes()).toContain('text-ink')
    expect(wrapper.find('[data-testid="nav-item-files"]').classes()).toContain('text-ink-faint')
  })

  it('emits nav-select with the clicked item id', async () => {
    const wrapper = mount(NavSidebar, { props: { activeView: 'files' } })
    await wrapper.find('[data-testid="nav-item-ontology"]').trigger('click')
    expect(wrapper.emitted('nav-select')).toEqual([['ontology']])
  })
})
```

- [ ] **Step 3: Run and verify**

```bash
cd frontend && npm test -- NavSidebar
```

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/NavSidebar.vue src/components/__tests__/NavSidebar.test.js
git commit -m "Add NavSidebar.vue for the nav-based layout redesign"
```

---

### Task 2: `FileExplorerView.vue` — document management, promoted from the floating window

**Files:**
- Create: `frontend/src/components/FileExplorerView.vue`
- Read only: `frontend/src/components/SettingsPanel.vue`

**Interfaces:**
- Props: `file: { type: Object, default: null }`, `schemaVersion: { type: Number, default: 0 }`.
- Emits: `file-selected`, `schema-used`, `graph-extracted` (the last one is new to this file relative to the spec's own summary — see Step 1 note below; `activateVersion`/`deleteVersion` already call `emit('graph-extracted')` in the current code, and that behavior moves here unchanged).
- Consumed by `App.vue` in Task 8.

This is today's `showFileExplorer`-gated floating dialog's *entire* content (left column: converter choice + upload + document list; right column: per-document meta info, summary, pipeline-stage badges + chunk creation, schema-version list, schema library) minus goldenset generation, which the spec explicitly moves to `OntologyWorkflowView.vue`'s Advanced section instead (Task 5) — everything else in that floating window is "document management," which stays here.

- [ ] **Step 1: Create `FileExplorerView.vue`'s `<script setup>` by moving state/logic out of `SettingsPanel.vue`**

Move these, byte-for-byte, renaming only `props.selectedFilename` → `props.file?.filename` at every use site (the rest of each function's body is unchanged):

- From lines 25–66 area: `isUploading`, `uploadError`, `uploadConverter`, `files`, `schemas`, `isUsingSchema`, `schemaUseError`, `schemaVersions`, `versionActionError` (skip everything else in that block — model/operation-model state goes to `SettingsView.vue` in Task 3, the rest goes to `OntologyWorkflowView.vue` in Task 5).
- `formatBytes` (lines 69–80), `formatModifiedAt` (82–85), `converterLabel` (87), `currentFile` computed (67, but rewritten to key off `props.file?.filename`), `fileStageBadges` (98–107), `pipelineStages` computed (109).
- `createChunks` (111–130), `isChunking`/`chunkError` refs (89–90).
- `createSummary` (132–151), `isSummarizing`/`summaryError` refs (91–92).
- `loadSchemas` (603–611), `loadDocuments` (613–621), `loadSchemaVersions` (623–637), `activeVersionLabel` computed (639–642).
- `handleFileChange` (946–969) — keep its `emit('file-selected', ...)` call as-is.
- `selectFile` (738–741) — keep its `emit('file-selected', ...)` call as-is.
- `useSchema` (743–766) — keep its `emit('schema-used')` call as-is.
- `activateVersion` (768–784) and `deleteVersion` (786–805) — keep their `emit('graph-extracted')` calls; this is why this component's `defineEmits` includes `graph-extracted` even though the spec's own one-paragraph summary of this file didn't call it out explicitly.
- The two watchers `watch(() => props.selectedFilename, loadSchemaVersions)` and `watch(() => props.schemaVersion, loadSchemaVersions)` (599–600), rewritten to `() => props.file?.filename`.
- The `watch(() => props.schemaVersion, () => { loadSchemas(); loadDocuments() })` block (668–671).
- The `onMounted` block's `loadDocuments()`/`loadSchemas()`/`loadSchemaVersions()` calls only (661–663) — drop the `/api/config` model-loading and `loadDiscovery()`/`loadDomains()` calls, which belong to the other two split targets.

Props/emits declaration:

```js
const props = defineProps({
  file: { type: Object, default: null },
  schemaVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['file-selected', 'schema-used', 'graph-extracted'])
```

Every internal reference to `props.selectedFilename` becomes `props.file?.filename`.

- [ ] **Step 2: Build `<template>`**

Take the entire `<div v-if="showFileExplorer" ...>` modal body from `SettingsPanel.vue` lines 1219–1433 and adapt it into a real, always-visible, full-bleed view:
- Drop the outer `fixed inset-0 z-[1000] ...` backdrop `<div>` and its `@click.self="showFileExplorer = false"` — this content is now the page, not an overlay.
- Drop the header row's "닫기" button and the `showFileExplorer` ref entirely (delete it — it no longer exists anywhere in this file).
- Keep the inner `flex flex-1 overflow-hidden` two-column layout (lines 1229–1431) as-is, just make its wrapping `<section>` fill the view: `<section class="flex h-full overflow-hidden">`.
- Everything inside (upload radios/input, document list with stage badges, meta `<dl>`, summary block, pipeline-stage badges + chunk button, schema-version list, schema library list) moves verbatim — same markup, same bindings, same `data-testid`s (there are none today in this section, so none to preserve).

- [ ] **Step 3: Rebuild and manually smoke-test in isolation**

Since `App.vue` isn't wired to this component until Task 8, verify it compiles and has no template errors:

```bash
cd frontend && npx vue-tsc --noEmit 2>/dev/null || true  # optional if vue-tsc isn't configured
podman-compose up --build -d
podman logs ontology_builder_frontend_1 --tail 30
```

Expected: no Vite compile error mentioning `FileExplorerView.vue`. Full interactive verification happens in Task 8 once it's actually reachable via the nav.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/FileExplorerView.vue
git commit -m "Add FileExplorerView.vue, extracted from SettingsPanel.vue's file-explorer modal"
```

---

### Task 3: `SettingsView.vue` — global model/GraphRAG/display/DB-management settings

**Files:**
- Create: `frontend/src/components/SettingsView.vue`
- Read only: `frontend/src/components/SettingsPanel.vue`

**Interfaces:**
- Props: none — this view has no per-document state at all.
- Emits: `hops-changed`, `markdown-changed`, `database-reset`. The spec's own description of this file ("model catalog + selected model, per-operation model override controls, GraphRAG hop count control, markdown-render toggle") doesn't mention the max-schema-chars input or the "LadybugDB 초기화" danger-zone button that also live in today's "실행 설정" modal — both stay global settings (they're not owned by any single ontology-pipeline step's UI), so they move here too rather than being invented as new state in `OntologyWorkflowView.vue` or `App.vue`. `maxSchemaChars` itself, however, moves to `OntologyWorkflowView.vue` in Task 5 since it's read exclusively by that view's own API calls (`discoverOntology`/`generateSchema`/`validateOntology`/`proposeEvolution`) — only the DB-reset button/state stays here.

- [ ] **Step 1: Create `SettingsView.vue`'s `<script setup>`**

Move verbatim from `SettingsPanel.vue`:
- `model`, `maxTokens`, `modelCatalog`, `selectedModel`, `isSettingModel`, `modelSetError` (lines 25–30).
- `OPERATION_MODELS`, `operationModel`, `selectedOperationModel`, `isSettingOperationModel`, `operationModelSetError` (38–47).
- `renderMarkdown` (61), `graphRagHops` (59).
- `isResettingDb`, `resetDbError` (55–56).
- `modelGroups` computed (673–686).
- `onModelChange` (688–711), `onOperationModelChange` (713–736).
- `onHopsInput` (554–558), `onMarkdownToggle` (564–567).
- `resetDatabase` (971–992) — keep its `window.confirm(...)` and `emit('database-reset')` as-is.
- The `onMounted`'s `/api/config` fetch (lines 645–659 portion only: `model.value = ...` through the `operationModel[key]`/`selectedOperationModel[key]` loop) — drop the `catch` fallback's irrelevant parts, keep `model.value = '알 수 없음'` on error.

Note: `maxSchemaChars`/`onMaxSchemaCharsInput` (60, 560–562) are **not** moved here — see Task 5.

```js
const props = undefined // no props
const emit = defineEmits(['hops-changed', 'markdown-changed', 'database-reset'])
```

- [ ] **Step 2: Build `<template>`**

Take the `<div v-if="showRunSettings" ...>` modal body from lines 1435–1546 and, like Task 2, drop the modal backdrop/close-button/`showRunSettings` ref, keeping the inner `space-y-4` content (LLM 모델 section, 채팅 표시 설정, GraphRAG 설정, 데이터베이스 관리) as the page body — but **drop the "스키마 생성 설정" (`maxSchemaChars`) block** (lines 1516–1532), since that setting moved to `OntologyWorkflowView.vue`.

- [ ] **Step 3: Rebuild and verify no compile errors, same as Task 2 Step 3.**

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/SettingsView.vue
git commit -m "Add SettingsView.vue, extracted from SettingsPanel.vue's run-settings modal"
```

---

### Task 4: `OntologyGraph.vue` — add node/edge selection emits for the new inspector

**Files:**
- Modify: `frontend/src/components/OntologyGraph.vue`

**Interfaces:**
- Adds `node-selected`/`edge-selected` emits, firing the full matched node/edge object (or `null` on deselect). Additive only — every existing prop/emit (`file`, `enabledTypes`, `enabledEdgeTypes`, `highlightedNodeIds`, `schemaRefreshRequest`, `types-available`, `edge-types-available`) is unchanged. This is a prerequisite for Task 5's node/edge inspector.

- [ ] **Step 1: Add a `selectedEdges` ref and `edge-selected`/`node-selected` emits**

In `frontend/src/components/OntologyGraph.vue`, change:

```js
const emit = defineEmits(['types-available', 'edge-types-available'])
```

to:

```js
const emit = defineEmits(['types-available', 'edge-types-available', 'node-selected', 'edge-selected'])
```

Change:

```js
const layouts = ref({ nodes: {} })
const selectedNodes = ref([])
```

to:

```js
const layouts = ref({ nodes: {} })
const selectedNodes = ref([])
const selectedEdges = ref([])
```

- [ ] **Step 2: Emit the full node/edge object on selection change**

Add, right after the existing `watch(() => props.highlightedNodeIds, ...)` block (around line 167):

```js
// v-network-graph's own click-to-select (v-model:selected-nodes/-edges below) --
// distinct from the highlightedNodeIds watcher above, which is driven by an
// external prop instead. displayNodes/displayEdges already carry every field
// the backend returned (detail, confidence, evidence_text, source_section,
// ...) in graph mode, or the schema's own name/description in schema mode --
// the inspector renders whichever of those this emits.
watch(selectedNodes, (ids) => {
  const id = ids[0]
  emit('node-selected', id ? (displayNodes.value.find((n) => n.id === id) ?? null) : null)
})

// vngEdges keys edges by a synthetic `e${index}` id (see the vngEdges computed
// above) since edges have no id of their own from the backend -- that index
// maps straight back into visibleEdges, which (unlike vngEdges) still carries
// every field the backend returned, not just what v-network-graph needs to render.
watch(selectedEdges, (ids) => {
  const id = ids[0]
  if (!id) {
    emit('edge-selected', null)
    return
  }
  const index = Number(id.slice(1))
  emit('edge-selected', visibleEdges.value[index] ?? null)
})
```

- [ ] **Step 3: Make edges selectable and bind `v-model:selected-edges`**

In the `configs` computed's `edge:` block, add `selectable: true` alongside the existing node config's `selectable: true`:

```js
  edge: {
    normal: {
      color: (edge) => edgeColorFor(edge.label),
      width: 1.5,
    },
    marker: { target: { type: 'arrow', width: 4, height: 4 } },
    type: 'curve',
    gap: 12,
    label: { fontSize: () => 11 / zoomLevel.value, color: '#9aa1b2' },
    selectable: true,
  },
```

Verify against the installed `v-network-graph` version's own docs/typings for the exact edge-config shape before relying on this — it mirrors the node config's `selectable: true` by symmetry, but hasn't been confirmed against that library's source in this repo.

In the `<template>`, change:

```html
<v-network-graph
  ref="graphRef"
  v-model:selected-nodes="selectedNodes"
  v-model:zoom-level="zoomLevel"
  ...
```

to:

```html
<v-network-graph
  ref="graphRef"
  v-model:selected-nodes="selectedNodes"
  v-model:selected-edges="selectedEdges"
  v-model:zoom-level="zoomLevel"
  ...
```

- [ ] **Step 4: Rebuild and manually verify**

Against the running stack, with an extracted graph open: click a node → check nothing visibly breaks (no consumer of `node-selected` exists yet, so this is just confirming no runtime error). Click an edge → same. Full behavior is verified once `OntologyWorkflowView.vue`'s inspector consumes these in Task 5.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/components/OntologyGraph.vue
git commit -m "Add node/edge selection emits to OntologyGraph.vue"
```

---

### Task 5: `OntologyWorkflowView.vue` — pipeline step sidebar, graph + inspector, Advanced section

**Files:**
- Create: `frontend/src/components/OntologyWorkflowView.vue`
- Read only: `frontend/src/components/SettingsPanel.vue`

**Interfaces:**
- Props: `file: { type: Object, default: null }`, `availableTypes: { type: Array, default: () => [] }`, `availableEdgeTypes: { type: Array, default: () => [] }`, `schemaVersion: { type: Number, default: 0 }`, `toggleTypeRequest: { type: Object, default: null }`, `toggleEdgeTypeRequest: { type: Object, default: null }`, `schemaRefreshRequest: { type: Object, default: null }`, `highlightedNodeIds: { type: Array, default: () => [] }` — the last four are forwarded straight through to its embedded `OntologyGraph.vue`, unchanged from what `App.vue` passes it today.
- Emits: `schema-generated`, `graph-extracted`, `filters-changed`, `edge-filters-changed`, `types-available`, `edge-types-available` (the last two are pass-throughs of `OntologyGraph.vue`'s own emits of the same name).
- `node-selected`/`edge-selected` from the embedded `OntologyGraph.vue` (Task 4) are consumed **locally** here to drive the inspector panel — they do not bubble up to `App.vue`.
- This is the largest single file in this plan; it absorbs the bulk of `SettingsPanel.vue`'s remaining logic (everything not already claimed by Tasks 2/3) plus a new node/edge inspector.

- [ ] **Step 1: Move the core-pipeline state/logic**

Move verbatim from `SettingsPanel.vue` (rewriting `props.selectedFilename` → `props.file?.filename` throughout, same as Task 2):

- `schemaDocumentType`, `isGeneratingSchema`, `isExtracting`, `isEmbedding`, `isValidating`, `validationReport`, `validationError`, `showValidationReport` (lines 190–197).
- `isProposingEvolution`, `evolutionProposal`, `evolutionError`, `showEvolutionReview`, `acceptedChangeIds`, `isApplyingEvolution`, `evolutionApplyError`, `evolutionApplyMessage` (198–205).
- `isDiscovering`, `discoveryReport`, `discoveryError`, `showDiscoveryReport`, `useDiscoveryForSchema` (206–210).
- `workflowMessage`, `workflowError`, `elapsedSeconds`, `elapsedTimer` (226–229), `startElapsedTimer`/`stopElapsedTimer` (231–241), `workflowProgress` computed (243–251), `onUnmounted(() => stopElapsedTimer())` (253).
- `maxSchemaChars` (60) and `onMaxSchemaCharsInput` (560–562) — moved here, **not** to `SettingsView.vue` (see Task 3's note).
- `discoverOntology` (255–281), `loadDiscovery` (283–295), `discoveryClasses`/`discoveryRelationships` computeds (297–298).
- `generateSchema` (300–329) — keep its `emit('schema-generated')`.
- `extractGraph` (331–354) — keep its `emit('graph-extracted')`.
- `embed` (356–378).
- `validateOntology` (380–405).
- `AUTO_ACCEPT_DECISIONS` (411), `proposeEvolution` (413–449), `toggleChangeAccepted` (451–459), `applyEvolution` (461–487) — keep its `emit('graph-extracted')`.
- `DECISION_STYLES`/`decisionClass` (489–500), `changeSummary` (502–508).
- `SEVERITY_ORDER`/`SEVERITY_STYLES`/`severityClass` (510–521), `sortedIssues` computed (523–528), `missingElementGroups` computed (530–539).
- `watch(() => props.selectedFilename, loadDiscovery)` (601) → rewritten to `props.file?.filename`.
- The `onMounted`'s `await loadDiscovery()` call (664).

- [ ] **Step 2: Move the node/edge type-filter state (feeds the graph, lives next to it now)**

Move verbatim: `enabledTypes`, `enabledEdgeTypes` (57–58), `TYPE_COLORS`/`EDGE_TYPE_COLORS`/`colorForType`/`colorForEdgeType` (541–552), `toggleType`/`toggleEdgeType` (994–1014), the two `watch(() => props.availableTypes/...availableEdgeTypes, ...)` blocks that seed `enabledTypes`/`enabledEdgeTypes` and emit `filters-changed`/`edge-filters-changed` (569–583), and the two `watch(() => props.toggleTypeRequest/...toggleEdgeTypeRequest, ...)` blocks (585–597).

- [ ] **Step 3: Move the Advanced-section state — goldenset generation**

Move verbatim: `isGeneratingGoldenset`, `goldensetError`, `goldensetReport`, `showGoldensetReport` (93–96), `createGoldenset` (153–174), `loadGoldenset` (176–187) and its `watch(() => props.selectedFilename, loadGoldenset, { immediate: true })` (188), rewritten to `props.file?.filename`.

- [ ] **Step 4: Move the Advanced-section state — domain-schema convergence**

Move verbatim: `showDomainSchema`, `domains`, `selectedDomain`, `newDomainName`, `domainSchema`, `selectedCalibrationFiles`, `isConverging`, `convergeError`, `convergeMessage`, `domainEvaluation`, `acceptedDomainReviewIds`, `isApplyingDomainReview`, `domainReviewError`, `isUsingDomainSchema`, `domainUseError` (211–225); `loadDomains` (807–815), `loadDomainSchema` (817–828), `onSelectDomain` (830–836), `toggleCalibrationFile` (838–846), `runDomainConvergence` (848–879), `toggleDomainReviewAccepted` (881–889), `applyDomainReview` (891–918), `useDomainSchemaForCurrentFile` (920–944) — keep its `emit('schema-used')`... **wait**, check this at implementation time: `useDomainSchemaForCurrentFile` emits `'schema-used'`, which today is handled by `App.vue`'s `@schema-used="onSchemaChanged"`. Since `OntologyWorkflowView.vue`'s emit list above doesn't currently include `schema-used`, **add it** to this component's `defineEmits` alongside `schema-generated`/`graph-extracted`, and wire `App.vue` to listen for it on this component too in Task 8 (in addition to `FileExplorerView.vue`'s own `schema-used`, which is a separate call site — `useSchema`).

Also move the `onMounted`'s `await loadDomains()` call (665).

- [ ] **Step 5: Add the current-file `has_graph`/`has_chunks` lookup this view needs for button-disabled states**

The `embed`/`validateOntology` buttons' `:disabled` today reads `!currentFile?.has_graph`, where `currentFile` came from `SettingsPanel.vue`'s own `files` list (now owned by `FileExplorerView.vue`, not passed down). Rather than lifting `files` into `App.vue` just for this, fetch it independently here — consistent with this app's existing per-view-fetches-its-own-data convention (see the design spec's own note that `ChatView.vue` independently re-fetches a document's raw text rather than sharing a cache with `PreviewView.vue`):

```js
const currentFileHasGraph = ref(false)

async function loadCurrentFileFlags(filename) {
  currentFileHasGraph.value = false
  if (!filename) return
  try {
    const res = await apiFetch('/api/documents')
    const data = await res.json()
    currentFileHasGraph.value = !!data.documents.find((f) => f.filename === filename)?.has_graph
  } catch (err) {
    // best-effort; buttons just stay disabled on failure
  }
}

watch(() => props.file?.filename, loadCurrentFileFlags, { immediate: true })
watch(() => props.schemaVersion, () => loadCurrentFileFlags(props.file?.filename))
```

Replace every `currentFile?.has_graph` reference in the moved template (embed/validate buttons) with `currentFileHasGraph`.

- [ ] **Step 6: Add the node/edge detail inspector**

New, local-only state (not moved from anywhere):

```js
const inspectedNode = ref(null)
const inspectedEdge = ref(null)

function onNodeSelected(node) {
  inspectedNode.value = node
  inspectedEdge.value = null
}
function onEdgeSelected(edge) {
  inspectedEdge.value = edge
  inspectedNode.value = null
}
```

- [ ] **Step 7: Build `<template>`**

Layout: a `flex h-full` row of [step sidebar, fixed width] + [main area, flex-1].

Step sidebar — carries over each button/status/error block from `SettingsPanel.vue`'s "워크플로우" section (lines 1017–1141) essentially as-is (same markup, same `:disabled`/`@click` bindings, same progress/message/error `<p>` tags), reordered into an explicit ordered list matching the spec (discover → generate schema → extract [+ embed] → validate → evolve), followed by a collapsed-by-default `<details>` (or a `showAdvanced` ref toggle) wrapping the domain-schema-convergence UI (lines 1950–2127, minus its modal wrapper) and the goldenset-generation UI (the "골든셋" block from lines 1348–1370, minus its modal wrapper, plus the `showGoldensetReport`/`showDiscoveryReport`/`showValidationReport`/`showEvolutionReview` modals themselves — those four keep their existing `fixed inset-0 z-[1000]` overlay markup **as-is**, just triggered from this view instead of `SettingsPanel.vue`; the spec's "carried over as-is" instruction applies to these report/review overlays, so don't redesign them into inline panels).

Node/edge type filter checkboxes: move the "그래프 노드 필터"/"그래프 엣지 필터" block (lines 1161–1216) into the main area, next to the graph — e.g. as a thin strip above `OntologyGraph.vue`.

Main area:

```html
<div class="flex min-h-0 flex-1 flex-col">
  <div class="flex min-h-0 flex-[2] gap-0 relative">
    <div class="min-h-0 min-w-0 flex-1">
      <OntologyGraph
        :file="file"
        :enabled-types="enabledTypes"
        :enabled-edge-types="enabledEdgeTypes"
        :highlighted-node-ids="highlightedNodeIds"
        :schema-refresh-request="schemaRefreshRequest"
        @types-available="(t) => { emit('types-available', t) }"
        @edge-types-available="(t) => { emit('edge-types-available', t) }"
        @node-selected="onNodeSelected"
        @edge-selected="onEdgeSelected"
      />
    </div>
    <div
      v-if="inspectedNode || inspectedEdge"
      class="absolute right-0 top-0 z-10 h-full w-[280px] overflow-y-auto border-l border-border bg-surface-raised p-3"
    >
      <h3 class="section-label">{{ inspectedNode ? '노드' : '엣지' }}</h3>
      <dl class="space-y-1.5 text-xs">
        <template v-if="inspectedNode">
          <dt class="text-ink-faint">Label</dt><dd class="text-ink">{{ inspectedNode.label }}</dd>
          <dt class="text-ink-faint">Type</dt><dd class="text-ink-muted">{{ inspectedNode.type }}</dd>
          <dt v-if="inspectedNode.detail" class="text-ink-faint">Detail</dt>
          <dd v-if="inspectedNode.detail" class="text-ink-muted">{{ inspectedNode.detail }}</dd>
          <dt v-if="inspectedNode.confidence" class="text-ink-faint">Confidence</dt>
          <dd v-if="inspectedNode.confidence" class="text-ink-muted">{{ inspectedNode.confidence }}</dd>
          <dt v-if="inspectedNode.evidence_text" class="text-ink-faint">Evidence</dt>
          <dd v-if="inspectedNode.evidence_text" class="italic text-ink-faint">"{{ inspectedNode.evidence_text }}"</dd>
          <dt v-if="inspectedNode.source_section" class="text-ink-faint">Section</dt>
          <dd v-if="inspectedNode.source_section" class="text-ink-muted">{{ inspectedNode.source_section }}</dd>
        </template>
        <template v-else-if="inspectedEdge">
          <dt class="text-ink-faint">Type</dt><dd class="text-ink">{{ inspectedEdge.type }}</dd>
          <dt class="text-ink-faint">Source → Target</dt>
          <dd class="text-ink-muted">{{ inspectedEdge.source }} → {{ inspectedEdge.target }}</dd>
          <dt v-if="inspectedEdge.detail" class="text-ink-faint">Detail</dt>
          <dd v-if="inspectedEdge.detail" class="text-ink-muted">{{ inspectedEdge.detail }}</dd>
          <dt v-if="inspectedEdge.confidence" class="text-ink-faint">Confidence</dt>
          <dd v-if="inspectedEdge.confidence" class="text-ink-muted">{{ inspectedEdge.confidence }}</dd>
          <dt v-if="inspectedEdge.evidence_text" class="text-ink-faint">Evidence</dt>
          <dd v-if="inspectedEdge.evidence_text" class="italic text-ink-faint">"{{ inspectedEdge.evidence_text }}"</dd>
          <dt v-if="inspectedEdge.source_section" class="text-ink-faint">Section</dt>
          <dd v-if="inspectedEdge.source_section" class="text-ink-muted">{{ inspectedEdge.source_section }}</dd>
        </template>
      </dl>
    </div>
  </div>
  <div class="min-h-0 flex-1 border-t border-border">
    <SchemaGraphPreview :file="file" :schema-version="schemaVersion" />
  </div>
</div>
```

(`OntologyGraph.vue` takes the majority of the vertical space with the inspector as a right-docked overlay that only appears when something's selected, so it doesn't permanently steal width from the graph; `SchemaGraphPreview.vue` keeps the remaining space below, full-width, in place of its old bottom-right grid cell — this exact split ratio/placement isn't dictated by the spec, so treat it as a starting point to adjust once it's actually on screen in Task 8.)

- [ ] **Step 8: Rebuild and verify no compile errors, same pattern as Task 2 Step 3.**

- [ ] **Step 9: Commit**

```bash
cd frontend && git add src/components/OntologyWorkflowView.vue
git commit -m "Add OntologyWorkflowView.vue with a node/edge inspector, extracted from SettingsPanel.vue"
```

---

### Task 6: `PreviewView.vue` — PDF/MD/Chunk split view, replacing `DocumentPreview.vue`

**Files:**
- Create: `frontend/src/components/PreviewView.vue`
- Create: `frontend/src/components/__tests__/PreviewView.test.js`
- Modify: `frontend/src/components/ChunkView.vue`
- Read only: `frontend/src/components/DocumentPreview.vue`

**Interfaces:**
- Props: `file: { type: Object, default: null }` (same as `DocumentPreview.vue` today).
- No emits.
- `ChunkView.vue` gains a new `chunk-selected` emit, fired with the full chunk object whenever a chunk row is expanded — needed so `PreviewView.vue` can compute a PDF jump target from the chunk's `line_start` the same way it already does for MD-tab scroll position. This emit isn't mentioned explicitly in the design spec's prose but is required to implement "selecting a chunk... computes a jump request via the existing `pageForLine`" — without some signal from `ChunkView.vue`, `PreviewView.vue` has no way to know which chunk the user just looked at.

- [ ] **Step 1: Add `chunk-selected` to `ChunkView.vue`**

In `frontend/src/components/ChunkView.vue`, change:

```js
const props = defineProps({
  data: { type: Object, required: true },
})

const expandedIds = ref(new Set())

function isExpanded(id) {
  return expandedIds.value.has(id)
}

function toggle(id) {
  const next = new Set(expandedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expandedIds.value = next
}
```

to:

```js
const props = defineProps({
  data: { type: Object, required: true },
})
const emit = defineEmits(['chunk-selected'])

const expandedIds = ref(new Set())

function isExpanded(id) {
  return expandedIds.value.has(id)
}

function toggle(chunk) {
  const next = new Set(expandedIds.value)
  if (next.has(chunk.id)) {
    next.delete(chunk.id)
  } else {
    next.add(chunk.id)
    emit('chunk-selected', chunk)
  }
  expandedIds.value = next
}
```

And update the template's `@click="toggle(chunk.id)"` (line 48) to `@click="toggle(chunk)"`.

- [ ] **Step 2: Create `PreviewView.vue`**

Take `DocumentPreview.vue` wholesale as the starting point (its `marked`/scroll-tracking/`chunkData`-loading logic is unchanged) and restructure it:

```vue
<script setup>
import { marked } from 'marked'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'
import { pageForLine } from '../utils/pdfHighlight.js'
import ChunkView from './ChunkView.vue'
import PdfViewer from './PdfViewer.vue'

const props = defineProps({
  file: { type: Object, default: null },
})

const chunkData = ref(null)
const viewMode = ref('raw')
const hasPdf = ref(false)
const pdfJumpRequest = ref(null)
let pdfJumpCounter = 0

const html = ref('')
const rawText = ref('')
const error = ref('')

const scrollRef = ref(null)
const scrollTop = ref(0)
const scrollHeight = ref(0)
const clientHeight = ref(0)
let resizeObserver = null

function measureScroll() {
  const el = scrollRef.value
  if (!el) return
  scrollTop.value = el.scrollTop
  scrollHeight.value = el.scrollHeight
  clientHeight.value = el.clientHeight
}

// Unlike today's manual "이 줄을 PDF에서 보기" button, the has-PDF split keeps
// the PdfViewer permanently visible, so every scroll needs to push a fresh
// jump request instead of waiting for an explicit click.
function onScroll() {
  measureScroll()
  if (hasPdf.value) jumpToPdf()
}

watch(scrollRef, (el) => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (el) {
    resizeObserver = new ResizeObserver(measureScroll)
    resizeObserver.observe(el)
    measureScroll()
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
})

const lines = computed(() => (rawText.value ? rawText.value.split('\n') : []))
const totalLines = computed(() => lines.value.length)

const thumbHeightPercent = computed(() => {
  if (scrollHeight.value === 0) return 100
  return Math.max(4, Math.min(100, (clientHeight.value / scrollHeight.value) * 100))
})

const thumbTopPercent = computed(() => {
  const scrollable = scrollHeight.value - clientHeight.value
  const fraction = scrollable > 0 ? scrollTop.value / scrollable : 0
  return (100 - thumbHeightPercent.value) * fraction
})

const thumbStyle = computed(() => ({
  height: thumbHeightPercent.value + '%',
  top: thumbTopPercent.value + '%',
}))

const currentLine = computed(() => {
  if (totalLines.value === 0) return 0
  const scrollable = scrollHeight.value - clientHeight.value
  const fraction = scrollable > 0 ? scrollTop.value / scrollable : 0
  return Math.min(totalLines.value, Math.round(fraction * (totalLines.value - 1)) + 1)
})

function jumpToPdf() {
  const line = currentLine.value || 1
  pdfJumpCounter += 1
  pdfJumpRequest.value = {
    page: pageForLine(lines.value, line),
    text: (lines.value[line - 1] || '').trim(),
    id: pdfJumpCounter,
  }
}

// Fired by ChunkView's new chunk-selected emit -- same mechanism as scrolling
// the MD tab, just keyed off the chunk's own line_start instead of the
// current scroll position.
function onChunkSelected(chunk) {
  if (!hasPdf.value) return
  pdfJumpCounter += 1
  pdfJumpRequest.value = {
    page: pageForLine(lines.value, chunk.line_start),
    text: (lines.value[chunk.line_start - 1] || '').trim(),
    id: pdfJumpCounter,
  }
}

async function loadChunkData(file) {
  chunkData.value = null
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(file.filename)}/chunk`)
    chunkData.value = res.ok ? await res.json() : null
  } catch (err) {
    chunkData.value = null
  }
}

watch(
  () => props.file,
  async (file) => {
    error.value = ''
    html.value = ''
    rawText.value = ''
    viewMode.value = 'raw'
    chunkData.value = null
    hasPdf.value = !!file?.has_pdf
    pdfJumpRequest.value = null
    if (!file) return
    loadChunkData(file)
    try {
      const res = await apiFetch(`/api/files/${encodeURIComponent(file.filename)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()
      rawText.value = text
      html.value = marked.parse(text)
      await nextTick()
      if (scrollRef.value) scrollRef.value.scrollTop = 0
      measureScroll()
      if (hasPdf.value) jumpToPdf()
    } catch (err) {
      error.value = '문서를 불러오지 못했습니다: ' + err.message
    }
  },
  { immediate: true }
)
</script>

<template>
  <section class="flex h-full flex-col">
    <div class="panel-header">
      <span>Preview</span>
    </div>
    <p v-if="!file" class="p-3 text-xs text-ink-faint">업로드된 문서가 없습니다</p>
    <p v-else-if="error" class="p-3 text-xs text-red-400">{{ error }}</p>
    <div v-else class="flex min-h-0 flex-1">
      <div v-if="hasPdf" class="min-h-0 min-w-0 flex-1 border-r border-border">
        <PdfViewer :filename="file.filename" :jump-request="pdfJumpRequest" />
      </div>
      <div class="flex min-h-0 min-w-0 flex-1 flex-col p-3">
        <div v-if="chunkData" data-testid="view-toggle" class="mb-2 flex flex-shrink-0 gap-1 text-[11px]">
          <button
            type="button"
            data-testid="view-mode-raw"
            class="rounded px-1.5 py-0.5"
            :class="viewMode === 'raw' ? 'bg-accent-muted/60 text-ink' : 'text-ink-faint hover:bg-white/5'"
            @click="viewMode = 'raw'"
          >원문</button>
          <button
            type="button"
            data-testid="view-mode-chunk"
            class="rounded px-1.5 py-0.5"
            :class="viewMode === 'chunk' ? 'bg-accent-muted/60 text-ink' : 'text-ink-faint hover:bg-white/5'"
            @click="viewMode = 'chunk'"
          >청크</button>
        </div>
        <div v-if="viewMode === 'chunk' && chunkData" class="min-h-0 flex-1 overflow-y-scroll">
          <ChunkView :data="chunkData" @chunk-selected="onChunkSelected" />
        </div>
        <template v-else>
          <div class="flex min-h-0 flex-1 gap-2">
            <div class="min-h-0 flex-1 overflow-y-scroll" ref="scrollRef" @scroll="onScroll">
              <div class="markdown text-[13px] leading-relaxed text-ink" v-html="html"></div>
            </div>
            <div class="relative w-1.5 flex-shrink-0 rounded-full bg-white/5">
              <div class="absolute left-0 right-0 min-h-[16px] rounded-full bg-accent/60" :style="thumbStyle"></div>
            </div>
          </div>
          <p class="mt-1 flex-shrink-0 border-t border-border pt-1 text-[11px] text-ink-faint">
            {{ currentLine }} of {{ totalLines }} lines
          </p>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.markdown :deep(table) { border-collapse: collapse; margin: 0.5rem 0; }
.markdown :deep(td), .markdown :deep(th) { border: 1px solid theme('colors.border.DEFAULT'); padding: 0.25rem 0.5rem; }
.markdown :deep(h1), .markdown :deep(h2), .markdown :deep(h3) { color: theme('colors.ink.DEFAULT'); font-weight: 600; margin: 0.75rem 0 0.35rem; }
.markdown :deep(code) { background: rgba(255, 255, 255, 0.08); padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.85em; }
.markdown :deep(a) { color: theme('colors.accent.DEFAULT'); }
</style>
```

Notes on what changed vs. `DocumentPreview.vue`:
- No `switchToPdf`/"PDF" tab button/"이 줄을 PDF에서 보기" button — `hasPdf` now drives a permanent left pane instead of a third toggle option.
- `viewMode` only ever toggles between `raw`/`chunk` — same two-state toggle as today's no-PDF case, now used uniformly regardless of `hasPdf`.
- `onScroll` and the new `onChunkSelected` push jump requests automatically instead of waiting for a manual click, per the spec ("just always-visible instead of behind a manual toggle").

- [ ] **Step 3: Adapt `DocumentPreview.test.js` into `PreviewView.test.js`**

Copy `frontend/src/components/__tests__/DocumentPreview.test.js` to `frontend/src/components/__tests__/PreviewView.test.js`, changing the import to `PreviewView` and adjusting the PDF-toggle describe block (the `view-mode-pdf`-button assertions no longer apply since there's no PDF *tab* anymore — replace them with assertions that the left `PdfViewer` pane renders when `has_pdf: true` and doesn't when `false`):

```js
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PreviewView from '../PreviewView.vue'
import PdfViewer from '../PdfViewer.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
  API_BASE: '',
  authState: { token: null },
}))

vi.mock('pdfjs-dist', () => ({
  getDocument: vi.fn(),
  GlobalWorkerOptions: {},
}))
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '' }))

import { apiFetch } from '../../utils/api.js'

const CHUNK_DATA = {
  source: 'doc_raw',
  preamble: { line_start: 1, line_end: 2, text: '표지' },
  chunks: [
    {
      id: '0::제1조',
      section_index: 0,
      section_label: '주계약',
      article_no: '1',
      sub_no: null,
      title: '목적',
      path: '주계약 > 제1조(목적)',
      line_start: 3,
      line_end: 5,
      text: '본문 내용',
    },
  ],
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body, text: async () => body }
}

function mockApi({ chunkStatus = 404, chunkBody = null } = {}) {
  apiFetch.mockImplementation((path) => {
    if (path.includes('/chunk')) return Promise.resolve(jsonResponse(chunkBody, chunkStatus))
    return Promise.resolve(jsonResponse('# 원문 내용', 200))
  })
}

beforeEach(() => {
  apiFetch.mockReset()
})

describe('PreviewView chunk toggle (no PDF)', () => {
  it('does not render a view toggle when the document has no chunks', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(false)
  })

  it('renders a view toggle defaulting to raw view when chunks exist', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('switches to the chunk view on click', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    expect(wrapper.text()).toContain('주계약 > 제1조(목적)')
  })

  it('switches back to the raw view on click', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    await wrapper.find('[data-testid="view-mode-raw"]').trigger('click')
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('resets to raw view when the file changes', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(true)
    await wrapper.setProps({ file: { filename: 'other_raw.md' } })
    await flushPromises()
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })
})

describe('PreviewView PDF split', () => {
  function mountWithStubbedPdfViewer(file) {
    return mount(PreviewView, { props: { file }, global: { stubs: { PdfViewer: true } } })
  }

  it('does not render the PDF pane when the document has no source pdf', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: false })
    await flushPromises()
    expect(wrapper.findComponent(PdfViewer).exists()).toBe(false)
  })

  it('renders the PDF pane alongside the MD/Chunk tabs when the document has a source pdf', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()
    expect(wrapper.findComponent(PdfViewer).exists()).toBe(true)
    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="view-mode-pdf"]').exists()).toBe(false)
  })

  it('pushes an initial jump request to the PDF pane once the document loads', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()
    const pdfViewer = wrapper.findComponent(PdfViewer)
    expect(pdfViewer.props('jumpRequest')).toMatchObject({ page: 1 })
  })

  it('removes the PDF pane when the file changes to one without a pdf', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()
    await wrapper.setProps({ file: { filename: 'other_raw.md', has_pdf: false } })
    await flushPromises()
    expect(wrapper.findComponent(PdfViewer).exists()).toBe(false)
  })
})
```

- [ ] **Step 4: Run and verify**

```bash
cd frontend && npm test -- PreviewView ChunkView
```

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/components/PreviewView.vue src/components/ChunkView.vue src/components/__tests__/PreviewView.test.js
git commit -m "Add PreviewView.vue with an always-visible PDF pane, replacing DocumentPreview's toggle"
```

---

### Task 7: Chat evidence citation — `pdfHighlight.js`, `ChatPanel.vue`, `MarkdownEvidenceViewer.vue`, `ChatView.vue`

**Files:**
- Modify: `frontend/src/utils/pdfHighlight.js`
- Modify: `frontend/src/utils/__tests__/pdfHighlight.test.js`
- Modify: `frontend/src/components/ChatPanel.vue`
- Modify: `frontend/src/components/__tests__/ChatPanel.test.js`
- Create: `frontend/src/components/MarkdownEvidenceViewer.vue`
- Create: `frontend/src/components/ChatView.vue`

**Interfaces:**
- `pdfHighlight.js` gains `pageForQuote(rawText, lines, quote)`.
- `ChatPanel.vue` gains a `cite-evidence` emit; every existing prop/emit is unchanged.
- `ChatView.vue` (new) wraps `ChatPanel.vue` + a right pane (`PdfViewer.vue` or `MarkdownEvidenceViewer.vue`), consumed by `App.vue` in Task 8.

- [ ] **Step 1: Add `pageForQuote` to `pdfHighlight.js`**

Append to `frontend/src/utils/pdfHighlight.js`:

```js
// Maps an evidence_text quote (verbatim per the backend's own verification --
// see app.ontology.extraction._find_evidence_span) straight back to a raw.md
// line number by locating it as a substring and counting newlines up to that
// point, then reuses pageForLine for the line->page step. Silently falls back
// to page 1 (letting PdfViewer's own on-page search no-op) if the quote isn't
// found verbatim -- same graceful-miss behavior PdfViewer.vue already has for
// any jump request whose text doesn't match on the target page.
export function pageForQuote(rawText, lines, quote) {
  if (!quote) return 1
  const index = rawText.indexOf(quote)
  if (index === -1) return 1
  const lineNumber = rawText.slice(0, index).split('\n').length
  return pageForLine(lines, lineNumber)
}
```

- [ ] **Step 2: Add tests to `pdfHighlight.test.js`**

Append to `frontend/src/utils/__tests__/pdfHighlight.test.js` (extend the existing import list with `pageForQuote`):

```js
describe('pageForQuote', () => {
  const rawText = '# 제목\n<!-- page: 1 -->\n첫 페이지 문장입니다.\n\n<!-- page: 2 -->\n둘째 페이지 문장입니다.\n'
  const lines = rawText.split('\n')

  it('finds a quote on page 1', () => {
    expect(pageForQuote(rawText, lines, '첫 페이지 문장')).toBe(1)
  })

  it('finds a quote past a later page marker', () => {
    expect(pageForQuote(rawText, lines, '둘째 페이지 문장')).toBe(2)
  })

  it('falls back to page 1 when the quote is not found verbatim', () => {
    expect(pageForQuote(rawText, lines, '존재하지 않는 문장')).toBe(1)
  })

  it('falls back to page 1 for an empty quote', () => {
    expect(pageForQuote(rawText, lines, '')).toBe(1)
  })
})
```

- [ ] **Step 3: Add `cite-evidence` to `ChatPanel.vue`**

In `frontend/src/components/ChatPanel.vue`, change:

```js
const emit = defineEmits(['highlight-nodes', 'toggle-type'])
```

to:

```js
const emit = defineEmits(['highlight-nodes', 'toggle-type', 'cite-evidence'])
```

Change the related-node chip's click handler (line 212):

```html
@click="emit('highlight-nodes', [node.id])"
```

to:

```html
@click="() => { emit('highlight-nodes', [node.id]); emit('cite-evidence', { text: node.evidence_text || node.label }) }"
```

- [ ] **Step 4: Add a test to `ChatPanel.test.js`**

Append a new `describe` block:

```js
describe('ChatPanel evidence citation', () => {
  function mountWithMessage(relatedNode) {
    apiFetch.mockImplementation((path) => {
      if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(null, 404))
      return Promise.resolve(
        jsonResponse({
          role: 'assistant',
          content: '답변',
          node_types: [],
          edge_types: [],
          related_nodes: [relatedNode],
        })
      )
    })
    return mount(ChatPanel, { props: { file: { filename: 'doc_raw.md' } } })
  }

  it('emits cite-evidence with evidence_text when present', async () => {
    const wrapper = mountWithMessage({ id: 'n1', label: '보험금', type: 'Benefit', evidence_text: '사망 시 보험금을 지급한다' })
    wrapper.vm.input = '질문'
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '보험금').trigger('click')
    expect(wrapper.emitted('cite-evidence')).toEqual([[{ text: '사망 시 보험금을 지급한다' }]])
  })

  it('falls back to label when evidence_text is absent', async () => {
    const wrapper = mountWithMessage({ id: 'n1', label: '보험금', type: 'Benefit' })
    wrapper.vm.input = '질문'
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '보험금').trigger('click')
    expect(wrapper.emitted('cite-evidence')).toEqual([[{ text: '보험금' }]])
  })
})
```

Verify `wrapper.vm.input` is settable this way given `<script setup>`'s default non-exposed bindings — if not, drive the input via `wrapper.find('input[type="text"]').setValue('질문')` instead before submitting.

- [ ] **Step 5: Create `MarkdownEvidenceViewer.vue`**

```vue
<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  text: { type: String, default: '' },
  quote: { type: String, default: '' },
})

const containerRef = ref(null)

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// Shows the raw markdown source (not marked.parse'd HTML) with the cited
// quote wrapped in <mark> -- rendering it through `marked` first would risk
// reformatting the text enough that the verbatim quote no longer appears as
// a substring of the rendered HTML.
const html = computed(() => {
  if (!props.quote) return `<pre class="whitespace-pre-wrap font-sans">${escapeHtml(props.text)}</pre>`
  const index = props.text.indexOf(props.quote)
  if (index === -1) return `<pre class="whitespace-pre-wrap font-sans">${escapeHtml(props.text)}</pre>`
  const before = escapeHtml(props.text.slice(0, index))
  const match = escapeHtml(props.text.slice(index, index + props.quote.length))
  const after = escapeHtml(props.text.slice(index + props.quote.length))
  return `<pre class="whitespace-pre-wrap font-sans">${before}<mark class="rounded-sm bg-yellow-300/40 px-0.5 ring-1 ring-yellow-400/80">${match}</mark>${after}</pre>`
})

watch(html, async () => {
  await nextTick()
  containerRef.value?.querySelector('mark')?.scrollIntoView({ block: 'center' })
})
</script>

<template>
  <div class="h-full overflow-y-auto p-3 text-[13px] leading-relaxed text-ink" ref="containerRef" v-html="html"></div>
</template>
```

- [ ] **Step 6: Create `ChatView.vue`**

```vue
<script setup>
import { computed, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'
import { pageForQuote } from '../utils/pdfHighlight.js'
import ChatPanel from './ChatPanel.vue'
import MarkdownEvidenceViewer from './MarkdownEvidenceViewer.vue'
import PdfViewer from './PdfViewer.vue'

const props = defineProps({
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
  renderMarkdown: { type: Boolean, default: true },
  enabledTypes: { type: Set, default: () => new Set() },
  enabledEdgeTypes: { type: Set, default: () => new Set() },
  availableTypes: { type: Array, default: () => [] },
})
const emit = defineEmits(['highlight-nodes', 'toggle-type'])

const rawText = ref('')
const citedQuote = ref('')
const pdfJumpRequest = ref(null)
let jumpCounter = 0

async function loadRawText(file) {
  rawText.value = ''
  citedQuote.value = ''
  pdfJumpRequest.value = null
  if (!file) return
  try {
    const res = await apiFetch(`/api/files/${encodeURIComponent(file.filename)}`)
    if (res.ok) rawText.value = await res.text()
  } catch (err) {
    rawText.value = ''
  }
}
watch(() => props.file, loadRawText, { immediate: true })

const lines = computed(() => (rawText.value ? rawText.value.split('\n') : []))

function onCiteEvidence({ text }) {
  citedQuote.value = text
  if (props.file?.has_pdf) {
    jumpCounter += 1
    pdfJumpRequest.value = { page: pageForQuote(rawText.value, lines.value, text), text, id: jumpCounter }
  }
}
</script>

<template>
  <div class="flex h-full min-w-0">
    <div class="min-h-0 min-w-0 flex-1 border-r border-border">
      <ChatPanel
        :file="file"
        :hops="hops"
        :render-markdown="renderMarkdown"
        :enabled-types="enabledTypes"
        :enabled-edge-types="enabledEdgeTypes"
        :available-types="availableTypes"
        @highlight-nodes="emit('highlight-nodes', $event)"
        @toggle-type="emit('toggle-type', $event)"
        @cite-evidence="onCiteEvidence"
      />
    </div>
    <div class="min-h-0 min-w-0 flex-1">
      <PdfViewer v-if="file?.has_pdf" :filename="file.filename" :jump-request="pdfJumpRequest" />
      <MarkdownEvidenceViewer v-else :text="rawText" :quote="citedQuote" />
    </div>
  </div>
</template>
```

- [ ] **Step 7: Run and verify**

```bash
cd frontend && npm test -- pdfHighlight ChatPanel
```

- [ ] **Step 8: Commit**

```bash
cd frontend && git add src/utils/pdfHighlight.js src/utils/__tests__/pdfHighlight.test.js \
  src/components/ChatPanel.vue src/components/__tests__/ChatPanel.test.js \
  src/components/MarkdownEvidenceViewer.vue src/components/ChatView.vue
git commit -m "Add PDF/markdown evidence citation to chat via a new ChatView.vue"
```

---

### Task 8: Final `App.vue` rewiring, delete superseded files, rebuild and manually verify

**Files:**
- Modify: `frontend/src/App.vue`
- Delete: `frontend/src/components/SettingsPanel.vue`
- Delete: `frontend/src/components/DocumentPreview.vue`
- Delete: `frontend/src/components/__tests__/DocumentPreview.test.js`

**Interfaces:**
- This task depends on every one of Tasks 1–7 being complete — it's the only point where all five new views, `NavSidebar.vue`, and the modified `OntologyGraph.vue`/`ChatPanel.vue` come together.

- [ ] **Step 1: Rewrite `App.vue`'s `<script setup>`**

Change:

```js
import { computed, onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import DocumentPreview from './components/DocumentPreview.vue'
import LoginScreen from './components/LoginScreen.vue'
import OntologyGraph from './components/OntologyGraph.vue'
import SchemaGraphPreview from './components/SchemaGraphPreview.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { apiFetch, authState } from './utils/api'
```

to:

```js
import { onMounted, ref } from 'vue'
import ChatView from './components/ChatView.vue'
import FileExplorerView from './components/FileExplorerView.vue'
import LoginScreen from './components/LoginScreen.vue'
import NavSidebar from './components/NavSidebar.vue'
import OntologyWorkflowView from './components/OntologyWorkflowView.vue'
import PreviewView from './components/PreviewView.vue'
import SettingsView from './components/SettingsView.vue'
import { apiFetch, authState } from './utils/api'
```

Delete entirely (no longer used): `MIN_SPLIT`, `MAX_SPLIT`, `colPercent`, `rowPercent`, `gridRef`, `dragStartX`/`dragStartColPercent`/`dragStartY`/`dragStartRowPercent`, `startColResize`/`onColResize`/`stopColResize`/`startRowResize`/`onRowResize`/`stopRowResize`, `gridStyle`/`colResizerStyle`/`rowResizerStyle` computeds.

Add:

```js
const activeView = ref('files')
```

Keep every other existing ref/handler (`parsedFile`, `graphFilters`, `edgeGraphFilters`, `availableTypes`, `availableEdgeTypes`, `schemaVersion`, `schemaRefreshRequest`, `graphRagHops`, `renderMarkdown`, `highlightedNodeIds`, `toggleTypeRequest`, `toggleEdgeTypeRequest`, and every `on*` handler function) exactly as-is — none of them change.

- [ ] **Step 2: Rewrite `App.vue`'s `<template>`**

Change the `<div class="flex min-h-0 flex-1">...</div>` block (lines 162–221) to:

```html
<div class="flex min-h-0 flex-1">
  <NavSidebar :active-view="activeView" @nav-select="activeView = $event" />
  <main class="min-h-0 min-w-0 flex-1 overflow-hidden">
    <FileExplorerView
      v-if="activeView === 'files'"
      :file="parsedFile"
      :schema-version="schemaVersion"
      @file-selected="onFileSelected"
      @schema-used="onSchemaChanged"
      @graph-extracted="onSchemaChanged"
    />
    <PreviewView v-else-if="activeView === 'preview'" :file="parsedFile" />
    <OntologyWorkflowView
      v-else-if="activeView === 'ontology'"
      :file="parsedFile"
      :available-types="availableTypes"
      :available-edge-types="availableEdgeTypes"
      :schema-version="schemaVersion"
      :toggle-type-request="toggleTypeRequest"
      :toggle-edge-type-request="toggleEdgeTypeRequest"
      :schema-refresh-request="schemaRefreshRequest"
      :highlighted-node-ids="highlightedNodeIds"
      @schema-generated="onSchemaChanged({ previewSchema: true })"
      @schema-used="onSchemaChanged"
      @graph-extracted="onSchemaChanged"
      @filters-changed="onFiltersChanged"
      @edge-filters-changed="onEdgeFiltersChanged"
      @types-available="onTypesAvailable"
      @edge-types-available="onEdgeTypesAvailable"
    />
    <ChatView
      v-else-if="activeView === 'chat'"
      :file="parsedFile"
      :hops="graphRagHops"
      :render-markdown="renderMarkdown"
      :enabled-types="graphFilters"
      :enabled-edge-types="edgeGraphFilters"
      :available-types="availableTypes"
      @highlight-nodes="onHighlightNodes"
      @toggle-type="onToggleType"
    />
    <SettingsView
      v-else-if="activeView === 'settings'"
      @hops-changed="onHopsChanged"
      @markdown-changed="onMarkdownChanged"
      @database-reset="onSchemaChanged"
    />
  </main>
</div>
```

Note the added `@schema-used="onSchemaChanged"` on `OntologyWorkflowView` — see Task 5 Step 4's note about `useDomainSchemaForCurrentFile`'s `emit('schema-used')`.

- [ ] **Step 3: Delete the superseded files**

```bash
cd frontend
git rm src/components/SettingsPanel.vue src/components/DocumentPreview.vue src/components/__tests__/DocumentPreview.test.js
```

- [ ] **Step 4: Rebuild**

```bash
podman-compose down && podman-compose up --build -d
podman logs ontology_builder_frontend_1 --tail 50
```

Expected: no Vite error referencing a missing import (double check nothing else still imports `SettingsPanel.vue`/`DocumentPreview.vue`).

- [ ] **Step 5: Run the full frontend test suite**

```bash
cd frontend && npm test
```

Expected: every existing + newly added spec passes, including the carried-over `ChatPanel.test.js` goldenset-toggle tests (unaffected by this plan) and the new `NavSidebar`/`PreviewView`/evidence-citation specs.

- [ ] **Step 6: Manual verification against the running stack**

Per `CLAUDE.md`, this is the only way to confirm cross-view layout/visual behavior — walk through each item:

- Nav renders five items; clicking each swaps the main pane; the active item is highlighted.
- Selecting a document in File Explorer does **not** force-navigate elsewhere; the header's filename still updates.
- Preview: a PDF-backed document shows the split view (PdfViewer left, MD/Chunk tabs right); scrolling MD or clicking a chunk moves the PDF pane; a non-PDF document shows the plain MD/Chunk toggle with no split.
- Ontology: discover → schema → extract → validate → evolve all work as before; the Advanced section is collapsed by default and both goldenset generation and domain-schema convergence still function once expanded; clicking a node/edge in the graph opens the inspector with its full detail (including `evidence_text` when present); type/edge filter checkboxes still filter the graph.
- Chat: sending a message and clicking a related-node chip both highlights it in the graph (switch to Ontology to confirm the highlight survived) **and** jumps/highlights the cited evidence in the right pane (PDF page for a PDF document, `<mark>`-highlighted markdown otherwise).
- Settings: model/operation-model selection, markdown toggle, hop count, and "LadybugDB 초기화" all still work and their effects are visible back in Ontology/Chat.
- No document selected: each of the five views shows its own empty state without erroring.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/App.vue
git commit -m "$(cat <<'EOF'
Replace the fixed 2x2 grid layout with a nav sidebar and single-view main pane

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
