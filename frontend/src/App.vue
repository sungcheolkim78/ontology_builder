<script setup>
import { onMounted, ref } from 'vue'
import ChatView from './components/ChatView.vue'
import FileExplorerView from './components/FileExplorerView.vue'
import LoginScreen from './components/LoginScreen.vue'
import NavSidebar from './components/NavSidebar.vue'
import OntologyWorkflowView from './components/OntologyWorkflowView.vue'
import PreviewView from './components/PreviewView.vue'
import SettingsView from './components/SettingsView.vue'
import { apiFetch, authState } from './utils/api'

const authRequired = ref(false)
const configLoaded = ref(false)

onMounted(async () => {
  const response = await apiFetch('/api/config')
  const data = await response.json()
  authRequired.value = data.auth_required
  configLoaded.value = true
})

const activeView = ref('files')

const parsedFile = ref(null)
const graphFilters = ref(new Set())
const edgeGraphFilters = ref(new Set())
const availableTypes = ref([])
const availableEdgeTypes = ref([])
const schemaVersion = ref(0)
// A fresh object each time OntologyWorkflowView's or FileExplorerView's
// workflow actions (schema generation, extraction, schema-library apply, DB
// reset) change what's in the backend for the current file -- OntologyGraph
// watches this alone to know when to reload, since it no longer drives any
// of those actions itself.
const schemaRefreshRequest = ref(null)
const graphRagHops = ref(1)
const renderMarkdown = ref(true)
const highlightedNodeIds = ref([])
const toggleTypeRequest = ref(null)
const toggleEdgeTypeRequest = ref(null)

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
</script>

<template>
  <div v-if="!configLoaded"></div>
  <LoginScreen v-else-if="authRequired && !authState.token" />
  <div v-else class="flex h-screen w-screen flex-col overflow-hidden bg-canvas text-ink">
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
  </div>
</template>
