<script setup>
import { ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import DocumentPreview from './components/DocumentPreview.vue'
import OntologyGraph from './components/OntologyGraph.vue'
import SettingsPanel from './components/SettingsPanel.vue'

const parsedFile = ref(null)
const graphFilters = ref(new Set(['Person', 'Organization', 'Concept']))

function onFileParsed(file) {
  parsedFile.value = file
}

function onFiltersChanged(filters) {
  graphFilters.value = filters
}
</script>

<template>
  <div class="dashboard">
    <SettingsPanel @file-parsed="onFileParsed" @filters-changed="onFiltersChanged" />
    <main class="chat-column">
      <ChatPanel />
    </main>
    <div class="right-column">
      <DocumentPreview :file="parsedFile" />
      <OntologyGraph :enabled-types="graphFilters" />
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
.right-column {
  width: 360px;
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
