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
const graphRagHops = ref(1)
const maxSchemaChars = ref(300000)
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

function onSchemaChanged() {
  schemaVersion.value++
}

function onHopsChanged(hops) {
  graphRagHops.value = hops
}

function onMaxSchemaCharsChanged(value) {
  maxSchemaChars.value = value
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
  <div class="dashboard">
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
      @database-reset="onSchemaChanged"
      @hops-changed="onHopsChanged"
      @markdown-changed="onMarkdownChanged"
      @max-schema-chars-changed="onMaxSchemaCharsChanged"
    />
    <div class="main-grid" :style="gridStyle" ref="gridRef">
      <div class="panel top-left">
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
      <div class="panel top-right">
        <DocumentPreview :file="parsedFile" />
      </div>
      <div class="panel bottom-left">
        <OntologyGraph
          :file="parsedFile"
          :enabled-types="graphFilters"
          :enabled-edge-types="edgeGraphFilters"
          :schema-version="schemaVersion"
          :highlighted-node-ids="highlightedNodeIds"
          :max-schema-chars="maxSchemaChars"
          @types-available="onTypesAvailable"
          @edge-types-available="onEdgeTypesAvailable"
          @schema-updated="onSchemaChanged"
        />
      </div>
      <div class="panel bottom-right">
        <SchemaGraphPreview :file="parsedFile" :schema-version="schemaVersion" />
      </div>
      <div class="resizer-v" :style="colResizerStyle" @mousedown="startColResize"></div>
      <div class="resizer-h" :style="rowResizerStyle" @mousedown="startRowResize"></div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  height: 100vh;
  font-family: sans-serif;
}
.main-grid {
  position: relative;
  flex: 1;
  min-width: 0;
  display: grid;
}
.panel {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.top-left {
  grid-column: 1;
  grid-row: 1;
  display: flex;
  flex-direction: column;
}
.top-right {
  grid-column: 2;
  grid-row: 1;
  border-left: 1px solid #ccc;
}
.bottom-left {
  grid-column: 1;
  grid-row: 2;
  border-top: 1px solid #ccc;
}
.bottom-right {
  grid-column: 2;
  grid-row: 2;
  border-left: 1px solid #ccc;
  border-top: 1px solid #ccc;
}
.resizer-v {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 8px;
  z-index: 2;
  cursor: col-resize;
  background: transparent;
}
.resizer-v:hover,
.resizer-v:active {
  background: #b8d0ff;
}
.resizer-h {
  position: absolute;
  left: 0;
  right: 0;
  height: 8px;
  z-index: 2;
  cursor: row-resize;
  background: transparent;
}
.resizer-h:hover,
.resizer-h:active {
  background: #b8d0ff;
}
</style>
