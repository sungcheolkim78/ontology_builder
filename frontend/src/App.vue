<script setup>
import { computed, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import DocumentPreview from './components/DocumentPreview.vue'
import OntologyGraph from './components/OntologyGraph.vue'
import SchemaGraphPreview from './components/SchemaGraphPreview.vue'
import SettingsPanel from './components/SettingsPanel.vue'

const MIN_SPLIT = 20
const MAX_SPLIT = 80

const parsedFile = ref(null)
const graphFilters = ref(new Set())
const edgeGraphFilters = ref(new Set())
const availableTypes = ref([])
const availableEdgeTypes = ref([])
const schemaVersion = ref(0)
// A fresh object each time SettingsPanel's workflow actions (schema
// generation, extraction, schema-library apply, DB reset) change what's in
// the backend for the current file -- OntologyGraph watches this alone to
// know when to reload, since it no longer drives any of those actions itself.
const schemaRefreshRequest = ref(null)
const graphRagHops = ref(1)
const renderMarkdown = ref(true)
const highlightedNodeIds = ref([])
const toggleTypeRequest = ref(null)
const toggleEdgeTypeRequest = ref(null)
const colPercent = ref(50)
const rowPercent = ref(50)

const gridRef = ref(null)

function onFileSelected(file) {
  parsedFile.value = file
}

function onFiltersChanged(filters) {
  graphFilters.value = filters
}

function onEdgeFiltersChanged(filters) {
  edgeGraphFilters.value = filters
}

function onTypesAvailable(types) {
  availableTypes.value = types
}

function onEdgeTypesAvailable(types) {
  availableEdgeTypes.value = types
}

function onSchemaChanged(opts = {}) {
  schemaVersion.value++
  schemaRefreshRequest.value = { previewSchema: !!opts.previewSchema }
}

function onHopsChanged(hops) {
  graphRagHops.value = hops
}

function onMarkdownChanged(value) {
  renderMarkdown.value = value
}

function onHighlightNodes(nodeIds) {
  highlightedNodeIds.value = nodeIds
}

function onToggleType({ kind, type }) {
  if (kind === 'edge') {
    toggleEdgeTypeRequest.value = { type }
  } else {
    toggleTypeRequest.value = { type }
  }
}

let dragStartX = 0
let dragStartColPercent = 0
let dragStartY = 0
let dragStartRowPercent = 0

function startColResize(event) {
  dragStartX = event.clientX
  dragStartColPercent = colPercent.value
  window.addEventListener('mousemove', onColResize)
  window.addEventListener('mouseup', stopColResize)
}

function onColResize(event) {
  const rect = gridRef.value.getBoundingClientRect()
  const deltaPercent = ((event.clientX - dragStartX) / rect.width) * 100
  colPercent.value = Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, dragStartColPercent + deltaPercent))
}

function stopColResize() {
  window.removeEventListener('mousemove', onColResize)
  window.removeEventListener('mouseup', stopColResize)
}

function startRowResize(event) {
  dragStartY = event.clientY
  dragStartRowPercent = rowPercent.value
  window.addEventListener('mousemove', onRowResize)
  window.addEventListener('mouseup', stopRowResize)
}

function onRowResize(event) {
  const rect = gridRef.value.getBoundingClientRect()
  const deltaPercent = ((event.clientY - dragStartY) / rect.height) * 100
  rowPercent.value = Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, dragStartRowPercent + deltaPercent))
}

function stopRowResize() {
  window.removeEventListener('mousemove', onRowResize)
  window.removeEventListener('mouseup', stopRowResize)
}

const gridStyle = computed(() => ({
  gridTemplateColumns: `${colPercent.value}% 1fr`,
  gridTemplateRows: `${rowPercent.value}% 1fr`,
}))

const colResizerStyle = computed(() => ({ left: `${colPercent.value}%` }))
const rowResizerStyle = computed(() => ({ top: `${rowPercent.value}%` }))
</script>

<template>
  <div class="flex h-screen w-screen flex-col overflow-hidden bg-canvas text-ink">
    <header
      class="flex h-11 flex-shrink-0 items-center gap-3 border-b border-border bg-surface-raised px-3"
    >
      <div class="flex items-center gap-2">
        <span class="flex h-6 w-6 items-center justify-center rounded-md bg-accent/90 text-[13px] font-bold text-white">
          O
        </span>
        <span class="text-[13px] font-semibold tracking-wide text-ink">Ontology Builder</span>
      </div>
      <div class="h-4 w-px bg-border"></div>
      <div class="flex min-w-0 items-center gap-1.5 text-xs text-ink-muted">
        <span class="text-ink-faint">문서</span>
        <span class="truncate font-medium text-ink" :class="{ 'italic text-ink-faint': !parsedFile }">
          {{ parsedFile?.filename ?? '선택된 문서 없음' }}
        </span>
      </div>
    </header>

    <div class="flex min-h-0 flex-1">
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
      <div class="relative grid min-w-0 flex-1" :style="gridStyle" ref="gridRef">
        <div class="col-start-1 row-start-1 min-h-0 min-w-0 overflow-hidden">
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
        <div class="col-start-2 row-start-1 min-h-0 min-w-0 overflow-hidden border-l border-border">
          <DocumentPreview :file="parsedFile" />
        </div>
        <div class="col-start-1 row-start-2 min-h-0 min-w-0 overflow-hidden border-t border-border">
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
        <div class="col-start-2 row-start-2 min-h-0 min-w-0 overflow-hidden border-l border-t border-border">
          <SchemaGraphPreview :file="parsedFile" :schema-version="schemaVersion" />
        </div>
        <div
          class="absolute top-0 bottom-0 z-10 w-2 -translate-x-1/2 cursor-col-resize bg-transparent transition-colors hover:bg-accent/30 active:bg-accent/40"
          :style="colResizerStyle"
          @mousedown="startColResize"
        ></div>
        <div
          class="absolute left-0 right-0 z-10 h-2 -translate-y-1/2 cursor-row-resize bg-transparent transition-colors hover:bg-accent/30 active:bg-accent/40"
          :style="rowResizerStyle"
          @mousedown="startRowResize"
        ></div>
      </div>
    </div>
  </div>
</template>
