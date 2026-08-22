<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  file: { type: Object, default: null },
  schemaVersion: { type: Number, default: 0 },
})

const activeTab = ref('schema') // schema | nodes | edges
const schema = ref(null)
const graph = ref(null)

async function load(file) {
  schema.value = null
  graph.value = null
  if (!file) return

  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(file.filename)}/schema`)
    if (res.ok) schema.value = await res.json()
  } catch (err) {
    // best-effort; tab just shows "no data" on failure
  }

  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(file.filename)}`)
    if (res.ok) graph.value = await res.json()
  } catch (err) {
    // best-effort
  }
}

watch(
  () => props.file,
  (file) => {
    activeTab.value = 'schema'
    load(file)
  },
  { immediate: true }
)
watch(() => props.schemaVersion, () => load(props.file))

const content = computed(() => {
  if (activeTab.value === 'schema') return schema.value
  if (activeTab.value === 'nodes') return graph.value?.nodes ?? null
  if (activeTab.value === 'edges') return graph.value?.edges ?? null
  return null
})

const contentText = computed(() =>
  content.value !== null ? JSON.stringify(content.value, null, 2) : null
)
</script>

<template>
  <section class="schema-preview">
    <h2>스키마 / 그래프DB</h2>
    <p v-if="!file" class="placeholder">문서를 선택하세요</p>
    <template v-else>
      <div class="tabs">
        <button :class="{ active: activeTab === 'schema' }" @click="activeTab = 'schema'">스키마</button>
        <button :class="{ active: activeTab === 'nodes' }" @click="activeTab = 'nodes'">Nodes</button>
        <button :class="{ active: activeTab === 'edges' }" @click="activeTab = 'edges'">Edges</button>
      </div>
      <p v-if="contentText === null" class="placeholder">아직 데이터가 없습니다</p>
      <pre v-else class="json-view">{{ contentText }}</pre>
    </template>
  </section>
</template>

<style scoped>
.schema-preview {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 1rem;
}
.placeholder {
  color: #888;
}
.tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-shrink: 0;
}
.tabs button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #f5f5f5;
  cursor: pointer;
}
.tabs button.active {
  background: #4f8ef7;
  color: white;
  border-color: #4f8ef7;
}
.json-view {
  flex: 1;
  min-height: 0;
  overflow-y: scroll;
  margin: 0;
  padding: 0.75rem;
  background: #f8f8f8;
  border-radius: 4px;
  font-size: 0.8rem;
  white-space: pre-wrap;
  word-break: break-word;
  scrollbar-width: thin;
  scrollbar-color: #b0b0b0 #f0f0f0;
}
.json-view::-webkit-scrollbar {
  width: 10px;
}
.json-view::-webkit-scrollbar-track {
  background: #f0f0f0;
}
.json-view::-webkit-scrollbar-thumb {
  background-color: #b0b0b0;
  border-radius: 6px;
  border: 2px solid #f0f0f0;
}
</style>
