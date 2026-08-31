# Tailwind CSS UI Redesign (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply phase 1's slate+indigo Tailwind visual language (already live in `SettingsPanel.vue`) to `App.vue`'s page layout and the four remaining components — `ChatPanel.vue`, `DocumentPreview.vue`, `OntologyGraph.vue`, `SchemaGraphPreview.vue` — replacing their current per-panel distinct-color scheme with the single unified accent, while preserving six deliberate exceptions that don't get unified.

**Architecture:** Same approach as phase 1: rewrite each file's `<template>` with Tailwind utility classes and delete/shrink its `<style scoped>` block, with zero changes to any `<script setup>` block. Phase 1's Tailwind tooling (`@tailwindcss/vite`, `src/style.css`) is already installed — no tooling changes in this plan.

**Tech Stack:** Vue 3 (`<script setup>`), Vite, Tailwind CSS v4 (already installed).

**Spec:** `docs/superpowers/specs/2026-08-28-tailwind-css-ui-redesign-phase2-design.md`

## Global Constraints

- No `<script setup>` changes in any of the 5 files touched by this plan — every `ref`, `computed`, `watch`, `emit`, prop, and function stays byte-for-byte identical. Only `<template>` and `<style>` change.
- No new Tailwind plugins (no `@tailwindcss/typography`, no scrollbar plugin) — every exception below is handled with plain CSS in a minimal `<style scoped>` remnant instead.
- Six documented exceptions, none of which get unified into the single indigo accent:
  1. Chat's node/edge type-analysis chips keep a node-vs-edge color distinction (`emerald` for node-type chips, `amber` for edge-type chips) instead of becoming indigo pills.
  2. Chat's per-instance related-node chips keep their `:style="{ '--chip-color': ... }"` binding (color computed at runtime to match `OntologyGraph.vue`'s own node colors) — but express fully as Tailwind arbitrary-value utilities referencing that CSS variable (`border-[var(--chip-color)]`, `hover:bg-[var(--chip-color)]`, etc.) with **no** `<style scoped>` remnant needed for this element.
  3. `DocumentPreview.vue`'s `.markdown-scroll` and `SchemaGraphPreview.vue`'s per-table scroll wrapper keep a small `<style scoped>` block containing only `scrollbar-width`/`scrollbar-color`/`::-webkit-scrollbar` rules.
  4. `OntologyGraph.vue`'s node hover tooltip stays dark (`bg-slate-800`/`text-white` via Tailwind classes, not custom CSS).
  5. `DocumentPreview.vue`'s and `ChatPanel.vue`'s markdown-legibility `:deep()` rules (added in phase 1's post-review fix wave, commit `7012b69`) **must be preserved** in whatever `<style scoped>` remnant each file keeps — this is the phase-1 fix for Tailwind's Preflight flattening `v-html`-rendered markdown, and deleting these files' entire style blocks without carrying these rules forward would silently reintroduce that regression.
  6. `v-network-graph`'s own node/edge rendering (`OntologyGraph.vue`'s `configs` computed object) is untouched — this plan only touches the chrome around the graph.
- No automated frontend test suite exists — every verification step is a manual `podman-compose` rebuild + Playwright walkthrough, not a unit test.

---

### Task 1: `App.vue` — page layout

**Files:**
- Modify: `frontend/src/App.vue:128-181` (`<template>`)
- Modify: `frontend/src/App.vue:183-248` (`<style scoped>` — deleted entirely)

**Interfaces:**
- No changes to any prop/emit contract with the 5 child components — `App.vue`'s `<script setup>` (lines 1-126: all `ref`/`computed`/drag-resize functions) is untouched.

- [ ] **Step 1: Replace the `<template>` block**

```html
<template>
  <div class="flex h-screen">
    <SettingsPanel
      :selected-filename="parsedFile?.filename"
      :available-types="availableTypes"
      :available-edge-types="availableEdgeTypes"
      :schema-version="schemaVersion"
      :toggle-type-request="toggleTypeRequest"
      :toggle-edge-type-request="toggleEdgeTypeRequest"
      @file-selected="onFileSelected"
      @filters-changed="onFiltersChanged"
      @edge-filters-changed="onEdgeFiltersChanged"
      @schema-used="onSchemaChanged"
      @schema-generated="onSchemaChanged({ previewSchema: true })"
      @graph-extracted="onSchemaChanged"
      @database-reset="onSchemaChanged"
      @hops-changed="onHopsChanged"
      @markdown-changed="onMarkdownChanged"
    />
    <div class="relative flex-1 min-w-0 grid" :style="gridStyle" ref="gridRef">
      <div class="col-start-1 row-start-1 flex min-w-0 min-h-0 flex-col overflow-hidden">
        <ChatPanel
          :file="parsedFile"
          :hops="graphRagHops"
          :render-markdown="renderMarkdown"
          :enabled-types="graphFilters"
          :enabled-edge-types="edgeGraphFilters"
          :available-types="availableTypes"
          @highlight-nodes="onHighlightNodes"
          @toggle-type="onToggleType"
        />
      </div>
      <div class="col-start-2 row-start-1 min-w-0 min-h-0 overflow-hidden border-l border-slate-200">
        <DocumentPreview :file="parsedFile" />
      </div>
      <div class="col-start-1 row-start-2 min-w-0 min-h-0 overflow-hidden border-t border-slate-200">
        <OntologyGraph
          :file="parsedFile"
          :enabled-types="graphFilters"
          :enabled-edge-types="edgeGraphFilters"
          :schema-refresh-request="schemaRefreshRequest"
          :highlighted-node-ids="highlightedNodeIds"
          @types-available="onTypesAvailable"
          @edge-types-available="onEdgeTypesAvailable"
        />
      </div>
      <div class="col-start-2 row-start-2 min-w-0 min-h-0 overflow-hidden border-l border-t border-slate-200">
        <SchemaGraphPreview :file="parsedFile" :schema-version="schemaVersion" />
      </div>
      <div
        class="absolute top-0 bottom-0 z-[2] w-2 cursor-col-resize bg-transparent hover:bg-indigo-200 active:bg-indigo-200"
        :style="colResizerStyle"
        @mousedown="startColResize"
      ></div>
      <div
        class="absolute left-0 right-0 z-[2] h-2 cursor-row-resize bg-transparent hover:bg-indigo-200 active:bg-indigo-200"
        :style="rowResizerStyle"
        @mousedown="startRowResize"
      ></div>
    </div>
  </div>
</template>
```

Note: `font-family: sans-serif` from the old `.dashboard` rule is deliberately dropped, not replaced with a `font-sans` class — Tailwind's Preflight already sets a better default sans-serif stack globally, and phase 1's final review flagged the old override as actively worse than just leaving it to Tailwind's default.

- [ ] **Step 2: Delete the entire `<style scoped>` block**

Delete everything from `<style scoped>` to `</style>` at the end of the file. Nothing replaces it.

- [ ] **Step 3: Confirm `<script setup>` is untouched**

`git diff frontend/src/App.vue` must show zero changes between `<script setup>` and `</script>` (lines 1-126 in the pre-Task-1 file).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/App.vue
git commit -m "Redesign App.vue layout with Tailwind utility classes"
```

---

### Task 2: `OntologyGraph.vue` — graph panel chrome

**Files:**
- Modify: `frontend/src/components/OntologyGraph.vue:448-509` (`<template>`)
- Modify: `frontend/src/components/OntologyGraph.vue:511-591` (`<style scoped>` — deleted entirely)

**Interfaces:**
- No changes to `<script setup>` (lines 1-446: all node/edge computeds, the d3-force simulation, `configs`, event handlers). `v-network-graph`'s own rendering (driven by the `configs` computed object) is untouched — this task only touches the surrounding chrome.

- [ ] **Step 1: Replace the `<template>` block**

```html
<template>
  <section class="flex h-full flex-col">
    <h2 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">온톨로지 그래프</h2>

    <div class="flex-1 min-h-0 flex flex-col p-4">
      <p v-if="status === 'empty'" class="shrink-0 text-sm text-slate-500">문서를 선택하세요</p>

      <template v-else-if="status === 'no-graph' || status === 'ready'">
        <div class="mb-3 flex shrink-0 items-center gap-2">
          <button
            v-if="status === 'ready' && schema && schema.node_types.length > 0"
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            @click="showSchemaPreview = !showSchemaPreview"
          >
            {{ showSchemaPreview ? '추출된 그래프 보기' : '스키마 미리보기' }}
          </button>
          <button
            v-if="displayMode !== 'none'"
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            @click="resetView"
          >리셋</button>
          <label class="ml-1 flex items-center gap-1 text-sm text-slate-600 cursor-pointer">
            <input type="checkbox" class="accent-indigo-600" v-model="showNodeLabels" />
            Node Label
          </label>
          <label class="flex items-center gap-1 text-sm text-slate-600 cursor-pointer">
            <input type="checkbox" class="accent-indigo-600" v-model="showEdgeLabels" />
            Edge Label
          </label>
        </div>
        <p v-if="error" class="shrink-0 text-sm text-red-600">{{ error }}</p>
        <p v-if="displayMode === 'none' && !error" class="shrink-0 text-sm text-slate-500">
          스키마를 생성하거나 라이브러리에서 선택하세요
        </p>
      </template>

      <p v-else-if="status === 'error'" class="shrink-0 text-sm text-red-600">{{ error }}</p>

      <div
        v-if="displayMode !== 'none'"
        class="relative flex-1 min-h-0 w-full rounded-lg shadow-[inset_0_0_0_1px_rgba(0,0,0,0.08),0_1px_4px_rgba(0,0,0,0.12)]"
        @mousemove="onNodePointerMove"
      >
        <v-network-graph
          ref="graphRef"
          v-model:selected-nodes="selectedNodes"
          v-model:zoom-level="zoomLevel"
          :nodes="vngNodes"
          :edges="vngEdges"
          :layouts="layouts"
          :configs="configs"
          :event-handlers="eventHandlers"
        >
          <template #edge-label="{ edge, ...slotProps }">
            <v-edge-label v-if="showEdgeLabels" :text="edge.label" align="center" vertical-align="above" v-bind="slotProps" />
          </template>
        </v-network-graph>

        <div
          v-if="hoveredNode"
          class="pointer-events-none fixed z-[2000] max-w-[260px] rounded-md bg-slate-800 px-[0.65rem] py-2 text-sm leading-snug text-white shadow-lg"
          :style="{ left: tooltipPos.x + 12 + 'px', top: tooltipPos.y + 12 + 'px' }"
        >
          <div class="mb-0.5 text-xs uppercase tracking-wide text-slate-400">{{ hoveredNode.type }}</div>
          <div class="mb-0.5 font-semibold">{{ hoveredNode.name }}</div>
          <div v-if="hoveredNode.detail" class="text-slate-300">{{ hoveredNode.detail }}</div>
        </div>
      </div>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Delete the entire `<style scoped>` block**

No exception applies to this file (the tooltip's dark color is now expressed as Tailwind classes, not custom CSS) — delete everything from `<style scoped>` to `</style>`. Nothing replaces it.

- [ ] **Step 3: Confirm `<script setup>` is untouched**

`git diff frontend/src/components/OntologyGraph.vue` must show zero changes between `<script setup>` and `</script>` (lines 1-446 in the pre-Task-2 file).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/OntologyGraph.vue
git commit -m "Redesign OntologyGraph.vue chrome with Tailwind utility classes"
```

---

### Task 3: `SchemaGraphPreview.vue` — tabs and data tables

**Files:**
- Modify: `frontend/src/components/SchemaGraphPreview.vue:64-178` (`<template>`)
- Modify: `frontend/src/components/SchemaGraphPreview.vue:180-287` (`<style scoped>` — replaced with a scrollbar-only remnant)

**Interfaces:**
- No changes to `<script setup>` (lines 1-62: `activeTab`, `schema`, `graph`, `load()`, `nodeRows`/`edgeRows`/`statusText` computeds).
- Produces: a new class marker `schema-scroll` applied to all 4 scrollable table wrappers, used by this task's own scrollbar CSS remnant (not consumed by any other task).

- [ ] **Step 1: Replace the `<template>` block**

```html
<template>
  <section class="flex h-full flex-col">
    <h2 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">스키마 / 그래프DB</h2>
    <div class="flex-1 min-h-0 flex flex-col p-4">
      <p v-if="!file" class="text-sm text-slate-500">문서를 선택하세요</p>
      <template v-else>
        <div class="mb-3 flex shrink-0 gap-2">
          <button
            type="button"
            class="rounded-md border px-3 py-1 text-sm"
            :class="activeTab === 'schema' ? 'border-indigo-600 bg-indigo-100 text-indigo-700' : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'schema'"
          >스키마</button>
          <button
            type="button"
            class="rounded-md border px-3 py-1 text-sm"
            :class="activeTab === 'nodes' ? 'border-indigo-600 bg-indigo-100 text-indigo-700' : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'nodes'"
          >Nodes</button>
          <button
            type="button"
            class="rounded-md border px-3 py-1 text-sm"
            :class="activeTab === 'edges' ? 'border-indigo-600 bg-indigo-100 text-indigo-700' : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'edges'"
          >Edges</button>
        </div>

        <div class="flex-1 min-h-0 flex flex-col">
          <template v-if="activeTab === 'schema'">
            <p v-if="!schema" class="text-sm text-slate-500">아직 데이터가 없습니다</p>
            <div v-else class="flex-1 min-h-0 flex flex-col gap-3">
              <div class="flex-1 min-h-0 flex flex-col">
                <h4 class="mb-1 shrink-0 text-xs font-bold uppercase tracking-wide text-slate-500">node_types</h4>
                <div class="schema-scroll flex-1 min-h-0 overflow-auto rounded-md border border-slate-200">
                  <table class="w-full border-collapse text-sm">
                    <thead>
                      <tr>
                        <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Name</th>
                        <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Description</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="nt in schema.node_types" :key="nt.name" class="hover:bg-slate-50">
                        <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ nt.name }}</td>
                        <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ nt.description }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div class="flex-1 min-h-0 flex flex-col">
                <h4 class="mb-1 shrink-0 text-xs font-bold uppercase tracking-wide text-slate-500">edge_types</h4>
                <div class="schema-scroll flex-1 min-h-0 overflow-auto rounded-md border border-slate-200">
                  <table class="w-full border-collapse text-sm">
                    <thead>
                      <tr>
                        <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Name</th>
                        <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Description</th>
                        <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Source</th>
                        <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Target</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="et in schema.edge_types" :key="et.name" class="hover:bg-slate-50">
                        <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ et.name }}</td>
                        <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ et.description }}</td>
                        <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ et.source }}</td>
                        <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ et.target }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="activeTab === 'nodes'">
            <p v-if="nodeRows.length === 0" class="text-sm text-slate-500">아직 데이터가 없습니다</p>
            <div v-else class="schema-scroll flex-1 min-h-0 overflow-auto rounded-md border border-slate-200">
              <table class="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">ID</th>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Label</th>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Type</th>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="n in nodeRows" :key="n.id" class="hover:bg-slate-50">
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ n.id }}</td>
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ n.label }}</td>
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ n.type }}</td>
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ n.detail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>

          <template v-else-if="activeTab === 'edges'">
            <p v-if="edgeRows.length === 0" class="text-sm text-slate-500">아직 데이터가 없습니다</p>
            <div v-else class="schema-scroll flex-1 min-h-0 overflow-auto rounded-md border border-slate-200">
              <table class="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Source</th>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Target</th>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Type</th>
                    <th class="sticky top-0 whitespace-nowrap border-b border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-semibold">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in edgeRows" :key="i" class="hover:bg-slate-50">
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ e.source }}</td>
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ e.target }}</td>
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ e.type }}</td>
                    <td class="whitespace-nowrap border-b border-slate-100 px-2.5 py-1.5">{{ e.detail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>

        <p class="mt-2 shrink-0 border-t border-slate-200 pt-2 text-sm text-slate-600">{{ statusText }}</p>
      </template>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Replace the `<style scoped>` block with a scrollbar-only remnant**

Replace everything from `<style scoped>` to `</style>` with:

```html
<style scoped>
.schema-scroll {
  scrollbar-width: thin;
  scrollbar-color: #b0b0b0 #f0f0f0;
}
.schema-scroll::-webkit-scrollbar {
  width: 10px;
}
.schema-scroll::-webkit-scrollbar-track {
  background: #f0f0f0;
}
.schema-scroll::-webkit-scrollbar-thumb {
  background-color: #b0b0b0;
  border-radius: 6px;
  border: 2px solid #f0f0f0;
}
</style>
```

- [ ] **Step 3: Confirm `<script setup>` is untouched**

`git diff frontend/src/components/SchemaGraphPreview.vue` must show zero changes between `<script setup>` and `</script>` (lines 1-62 in the pre-Task-3 file).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/SchemaGraphPreview.vue
git commit -m "Redesign SchemaGraphPreview.vue with Tailwind utility classes"
```

---

### Task 4: `DocumentPreview.vue` — markdown viewer

**Files:**
- Modify: `frontend/src/components/DocumentPreview.vue:95-114` (`<template>`)
- Modify: `frontend/src/components/DocumentPreview.vue:116-228` (`<style scoped>` — trimmed to the scrollbar + markdown-legibility remnant)

**Interfaces:**
- No changes to `<script setup>` (lines 1-93: scroll-position math, `apiFetch`/`marked.parse` loading logic).
- Consumes: the markdown-legibility `:deep()` rules already added to this file's `<style scoped>` block in phase 1's post-review fix wave (commit `7012b69`) — this task carries them forward into the trimmed block, it does not re-derive them.

- [ ] **Step 1: Replace the `<template>` block**

```html
<template>
  <section class="flex h-full flex-col border-b border-slate-200">
    <h2 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">문서 Preview</h2>
    <div class="flex-1 min-h-0 flex flex-col p-4">
      <p v-if="!file" class="text-sm text-slate-500">업로드된 문서가 없습니다</p>
      <p v-else-if="error" class="text-sm text-red-600">{{ error }}</p>
      <template v-else>
        <div class="flex-1 min-h-0 flex gap-2">
          <div class="markdown-scroll flex-1 min-h-0 overflow-y-scroll" ref="scrollRef" @scroll="onScroll">
            <div class="markdown" v-html="html"></div>
          </div>
          <div class="relative w-1.5 shrink-0 rounded-full bg-slate-100">
            <div class="absolute inset-x-0 min-h-4 rounded-full bg-indigo-600" :style="thumbStyle"></div>
          </div>
        </div>
        <p class="mt-1 shrink-0 border-t border-slate-100 pt-1 text-xs text-slate-500">{{ currentLine }} of {{ totalLines }} lines</p>
      </template>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Replace the `<style scoped>` block, carrying the markdown-legibility rules forward**

Read the file's CURRENT `<style scoped>` block first (it already has the phase-1 fix wave's `:deep()` additions at what were lines 191-219 in that fix — re-read the live file rather than trusting these line numbers, since Task 1-3 of this plan don't touch this file but its exact current line numbers may have drifted). Replace the entire block with:

```html
<style scoped>
.markdown-scroll {
  scrollbar-width: thin;
  scrollbar-color: #b0b0b0 #f0f0f0;
}
.markdown-scroll::-webkit-scrollbar {
  width: 10px;
}
.markdown-scroll::-webkit-scrollbar-track {
  background: #f0f0f0;
}
.markdown-scroll::-webkit-scrollbar-thumb {
  background-color: #b0b0b0;
  border-radius: 6px;
  border: 2px solid #f0f0f0;
}
.markdown :deep(h1) {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0.75rem 0 0.5rem;
}
.markdown :deep(h2) {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0.75rem 0 0.5rem;
}
.markdown :deep(h3) {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 0.5rem 0 0.35rem;
}
.markdown :deep(h4) {
  font-size: 1rem;
  font-weight: 600;
  margin: 0.5rem 0 0.35rem;
}
.markdown :deep(p) {
  margin: 0.5rem 0;
}
.markdown :deep(ul),
.markdown :deep(ol) {
  list-style: revert;
  padding-left: 1.5em;
  margin: 0.5rem 0;
}
.markdown :deep(table) {
  border-collapse: collapse;
}
.markdown :deep(td),
.markdown :deep(th) {
  border: 1px solid #e2e8f0;
  padding: 0.25rem 0.5rem;
}
</style>
```

(This is the same `:deep()` content the fix wave added, carried over verbatim, plus the scrollbar rules — with the markdown table border color updated from `#ccc` to `#e2e8f0`, slate-200's hex, for consistency with the rest of the new palette. Everything else — `.preview`, `.panel-title`, `.panel-body`, `.content-row`, `.position-track`, `.position-thumb`, `.status-line`, `.placeholder`, `.error` — is deleted; all of it is now expressed via the Tailwind classes in Step 1.)

- [ ] **Step 3: Confirm `<script setup>` is untouched**

`git diff frontend/src/components/DocumentPreview.vue` must show zero changes between `<script setup>` and `</script>`.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/DocumentPreview.vue
git commit -m "Redesign DocumentPreview.vue with Tailwind utility classes"
```

---

### Task 5: `ChatPanel.vue` — chat messages and composer

**Files:**
- Modify: `frontend/src/components/ChatPanel.vue:98-174` (`<template>`)
- Modify: `frontend/src/components/ChatPanel.vue:176-344` (`<style scoped>` — trimmed to the markdown-legibility remnant only)

**Interfaces:**
- No changes to `<script setup>` (lines 1-96: `sendMessage`, `sortRelatedNodes`, abort-controller/keydown logic, all props/emits).
- Consumes: the markdown-legibility `:deep()` rules already added to this file's `<style scoped>` block in phase 1's post-review fix wave (commit `7012b69`) — carried forward, not re-derived. Also consumes `colorForNodeType` from `../utils/nodeColors.js` (already imported, unchanged) for the per-instance node-chip color.

- [ ] **Step 1: Replace the `<template>` block**

```html
<template>
  <section class="flex h-full min-w-0 flex-col">
    <h2 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">Chat</h2>

    <div class="flex-1 min-h-0 flex flex-col p-4">
      <div class="mb-4 flex-1 overflow-y-auto rounded-lg border border-slate-200 p-4">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="message mb-3 rounded-lg px-3 py-2"
          :class="msg.role === 'user' ? 'bg-indigo-100 text-right' : 'bg-slate-100'"
        >
          <strong>{{ msg.role === 'user' ? '나' : '챗봇' }}</strong>
          <div
            v-if="(msg.nodeTypes && msg.nodeTypes.length) || (msg.edgeTypes && msg.edgeTypes.length)"
            class="mt-1 mb-4 border-b border-dashed border-slate-300 pb-2"
          >
            <div class="mb-1 flex flex-wrap items-center gap-1.5">
              <span class="text-xs text-slate-500">노드:</span>
              <template v-if="msg.nodeTypes.length">
                <button
                  v-for="type in msg.nodeTypes"
                  :key="'n-' + type"
                  type="button"
                  class="cursor-pointer rounded-full border border-emerald-600 bg-white px-2.5 py-0.5 text-xs text-emerald-700 hover:bg-emerald-600 hover:text-white"
                  :class="{ 'opacity-40 line-through': !enabledTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'node', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="text-xs text-slate-400">없음</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-xs text-slate-500">엣지:</span>
              <template v-if="msg.edgeTypes.length">
                <button
                  v-for="type in msg.edgeTypes"
                  :key="'e-' + type"
                  type="button"
                  class="cursor-pointer rounded-full border border-amber-800 bg-white px-2.5 py-0.5 text-xs text-amber-800 hover:bg-amber-800 hover:text-white"
                  :class="{ 'opacity-40 line-through': !enabledEdgeTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'edge', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="text-xs text-slate-400">없음</span>
            </div>
          </div>
          <div v-if="renderMarkdown" class="markdown mt-1" v-html="marked.parse(msg.content)"></div>
          <p v-else class="mt-1 whitespace-pre-wrap">{{ msg.content }}</p>
          <div v-if="msg.relatedNodes && msg.relatedNodes.length" class="mt-2 flex flex-wrap items-center gap-1.5">
            <span class="text-xs text-slate-500">관련 노드:</span>
            <button
              v-for="node in msg.relatedNodes"
              :key="node.id"
              type="button"
              class="cursor-pointer rounded-full border border-[var(--chip-color)] bg-white px-2.5 py-0.5 text-xs text-[var(--chip-color)] hover:bg-[var(--chip-color)] hover:text-white"
              :style="{ '--chip-color': colorForNodeType(node.type, availableTypes) }"
              @click="emit('highlight-nodes', [node.id])"
            >
              {{ node.label }}
            </button>
          </div>
        </div>
        <p v-if="isLoading" class="text-sm text-slate-500">응답 중... (ESC로 취소)</p>
        <p v-if="error" class="text-sm text-red-600">{{ error }}</p>
      </div>

      <form class="flex gap-2" @submit.prevent="sendMessage">
        <input
          v-model="input"
          type="text"
          placeholder="메시지를 입력하세요"
          class="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <button
          type="submit"
          :disabled="isLoading"
          class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
        >전송</button>
      </form>
    </div>
  </section>
</template>
```

Note the `message` class on the message-row `<div>` is kept deliberately (not a leftover) — it's required as the ancestor selector for the `.message .markdown :deep(...)` rules kept in Step 2; nothing else in `<style>` targets it. The node-type/edge-type/node-chip elements need no class markers at all — their base color, hover swap, and (for the node-chip) the runtime CSS-variable binding are fully expressed as Tailwind utility classes (including the `border-[var(--chip-color)]`/`hover:bg-[var(--chip-color)]` arbitrary-value pattern for the one genuinely dynamic color in this file).

- [ ] **Step 2: Replace the `<style scoped>` block, carrying the markdown-legibility rules forward**

Read the file's CURRENT `<style scoped>` block first (it already has the phase-1 fix wave's `:deep()` additions — re-read the live file rather than trusting line numbers, since this plan's earlier tasks don't touch this file but line numbers may have drifted from what's described here). Replace the entire block with:

```html
<style scoped>
.message .markdown :deep(p) {
  margin: 0.25rem 0;
}
.message .markdown :deep(h1) {
  font-size: 1.15rem;
  font-weight: 700;
  margin: 0.5rem 0 0.3rem;
}
.message .markdown :deep(h2) {
  font-size: 1.05rem;
  font-weight: 700;
  margin: 0.5rem 0 0.3rem;
}
.message .markdown :deep(h3) {
  font-size: 1rem;
  font-weight: 600;
  margin: 0.4rem 0 0.25rem;
}
.message .markdown :deep(h4) {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0.4rem 0 0.25rem;
}
.message .markdown :deep(ul),
.message .markdown :deep(ol) {
  list-style: revert;
  padding-left: 1.4em;
  margin: 0.25rem 0;
}
.message .markdown :deep(table) {
  border-collapse: collapse;
}
.message .markdown :deep(td),
.message .markdown :deep(th) {
  border: 1px solid #e2e8f0;
  padding: 0.25rem 0.5rem;
}
</style>
```

(Carried over verbatim from the fix wave's additions, with the markdown table border color updated from `#ccc` to `#e2e8f0` for palette consistency, same touch as `DocumentPreview.vue`. Everything else — `.chat`, `.panel-title`, `.panel-body`, `.messages`, `.message`/`.message.user`/`.message.assistant` background rules, `.message p`, `.message .markdown` margin, `.type-analysis*`, `.type-chip*`, `.related-nodes`/`.related-label`, `.node-chip*`, `.error`, `.input-row*` — is deleted; all of it is now expressed via the Tailwind classes in Step 1.)

- [ ] **Step 3: Confirm `<script setup>` is untouched**

`git diff frontend/src/components/ChatPanel.vue` must show zero changes between `<script setup>` and `</script>`.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/ChatPanel.vue
git commit -m "Redesign ChatPanel.vue with Tailwind utility classes"
```

---

### Task 6: Rebuild and verify every interaction across all five files

**Files:** none (verification only)

**Interfaces:** none — this is the integration check confirming Tasks 1-5 together produce a working, visually-unified, functionally-identical app.

- [ ] **Step 1: Rebuild the stack**

```bash
podman-compose down && podman-compose up --build -d
```

- [ ] **Step 2: Confirm the served files reflect the new templates**

```bash
curl -s http://localhost:5173/src/App.vue | grep -c "col-start-1"
curl -s http://localhost:5173/src/components/OntologyGraph.vue | grep -c "bg-slate-800"
curl -s http://localhost:5173/src/components/SchemaGraphPreview.vue | grep -c "schema-scroll"
curl -s http://localhost:5173/src/components/DocumentPreview.vue | grep -c "bg-indigo-600"
curl -s http://localhost:5173/src/components/ChatPanel.vue | grep -c "border-\[var(--chip-color)\]"
```

Expected: every command returns a non-zero count. (If any returns 0, check whether Vite is serving the `.vue` file's compiled JS instead of raw source for that particular request — phase 1's fix wave hit this same false alarm; confirm visually in the browser before concluding something is stale.)

- [ ] **Step 3: Visually verify every interaction in the browser**

Using Playwright (or equivalent browser automation), against `http://localhost:5173`, with a document that has an extracted graph selected:

1. All four panel headers (Chat, 문서 Preview, 온톨로지 그래프, 스키마/그래프DB) now look identical in style to `SettingsPanel`'s header — white background, slate border, no per-panel color.
2. **Chat**: send a message whose answer includes markdown headings/bullet lists — confirm they render with visible hierarchy (not flattened). Confirm the "나" bubble is `indigo-100` and the assistant bubble is `slate-100`. Confirm the 노드/엣지 type-analysis chips show emerald (node) and amber (edge) respectively, still toggle the corresponding filter on click (check `enabledTypes`/`enabledEdgeTypes` visibly change via the `온톨로지 설정` filters in the sidebar), and that clicking an inactive chip re-activates it. Confirm the 관련 노드 chips show the correct per-type color (matching that node's color in the graph view below) and clicking one calls `highlight-nodes` (the corresponding node highlights/pans in `OntologyGraph`).
3. **문서 Preview**: select a document, confirm headings/bullet lists in the raw document render with visible hierarchy, confirm the scroll-position thumb (now indigo) tracks scrolling correctly, and confirm the custom thin scrollbar still renders (not a native browser scrollbar).
4. **온톨로지 그래프**: click "스키마 미리보기"/"추출된 그래프 보기" toggle, click "리셋", toggle both Node Label and Edge Label checkboxes (now indigo-tinted), and hover a node to confirm the tooltip appears (dark background, white text, showing type/label/detail).
5. **스키마 / 그래프DB**: click through all three tabs (스키마/Nodes/Edges), confirm the active tab is highlighted (indigo pill) and inactive tabs use the secondary-button style, confirm all data tables render with the new header/hover styling, and confirm their scrollbar is still the custom thin one.
6. **App.vue layout**: drag both the vertical and horizontal resizer handles, confirm the four panels resize correctly and each resizer highlights indigo on hover.
7. **Re-verify `SettingsPanel.vue`'s own workflow** (unaffected by this plan, but wired to the same `App.vue` this plan changed): select a file, generate a schema, extract a graph, embed it, and confirm the graph/document/chat panels around it all still update correctly — this confirms `App.vue`'s prop/emit wiring survived Task 1's rewrite.

- [ ] **Step 4: Report findings**

Note any visual bugs or regressions found. If something is broken (not just "still uses the old per-panel color," which would mean a task was missed — check for that specifically), fix it in the relevant task (1-5) before considering this plan complete.
