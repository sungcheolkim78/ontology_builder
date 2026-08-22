<script setup>
import { ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import DocumentPreview from './components/DocumentPreview.vue'
import OntologyGraph from './components/OntologyGraph.vue'
import SettingsPanel from './components/SettingsPanel.vue'

const MIN_RIGHT_WIDTH = 260
const MAX_RIGHT_WIDTH = 800

const parsedFile = ref(null)
const graphFilters = ref(new Set())
const availableTypes = ref([])
const schemaVersion = ref(0)
const rightColumnWidth = ref(360)

let dragStartX = 0
let dragStartWidth = 0

function onFileSelected(file) {
  parsedFile.value = file
}

function onFiltersChanged(filters) {
  graphFilters.value = filters
}

function onTypesAvailable(types) {
  availableTypes.value = types
}

function onSchemaChanged() {
  schemaVersion.value++
}

function startResize(event) {
  dragStartX = event.clientX
  dragStartWidth = rightColumnWidth.value
  window.addEventListener('mousemove', onResize)
  window.addEventListener('mouseup', stopResize)
}

function onResize(event) {
  const delta = dragStartX - event.clientX
  const next = dragStartWidth + delta
  rightColumnWidth.value = Math.min(MAX_RIGHT_WIDTH, Math.max(MIN_RIGHT_WIDTH, next))
}

function stopResize() {
  window.removeEventListener('mousemove', onResize)
  window.removeEventListener('mouseup', stopResize)
}
</script>

<template>
  <div class="dashboard">
    <SettingsPanel
      :selected-filename="parsedFile?.filename"
      :available-types="availableTypes"
      :schema-version="schemaVersion"
      @file-selected="onFileSelected"
      @filters-changed="onFiltersChanged"
      @schema-used="onSchemaChanged"
    />
    <main class="chat-column">
      <ChatPanel />
    </main>
    <div class="resizer" @mousedown="startResize"></div>
    <div class="right-column" :style="{ width: rightColumnWidth + 'px' }">
      <DocumentPreview :file="parsedFile" />
      <OntologyGraph
        :file="parsedFile"
        :enabled-types="graphFilters"
        :schema-version="schemaVersion"
        @types-available="onTypesAvailable"
        @schema-updated="onSchemaChanged"
      />
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  height: 100vh;
  font-family: sans-serif;
}
.chat-column {
  flex: 1;
  min-width: 0;
  padding: 1rem;
  display: flex;
  flex-direction: column;
}
.resizer {
  width: 6px;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
}
.resizer:hover,
.resizer:active {
  background: #b8d0ff;
}
.right-column {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid #ccc;
}
.right-column > * {
  flex: 1;
  min-height: 0;
}
</style>
