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

const colResizerStyle = computed(() => ({ left: `calc(${colPercent.value}% - 4px)` }))
const rowResizerStyle = computed(() => ({ top: `calc(${rowPercent.value}% - 4px)` }))
</script>

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
