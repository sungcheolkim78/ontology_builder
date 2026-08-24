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

const nodeRows = computed(() => graph.value?.nodes ?? [])
const edgeRows = computed(() => graph.value?.edges ?? [])

const statusText = computed(() => {
  if (activeTab.value === 'schema') {
    if (!schema.value) return ''
    return `노드 타입 ${schema.value.node_types.length}개, 에지 타입 ${schema.value.edge_types.length}개`
  }
  if (activeTab.value === 'nodes') {
    if (!graph.value) return ''
    return `전체 노드 ${nodeRows.value.length}개`
  }
  if (activeTab.value === 'edges') {
    if (!graph.value) return ''
    return `전체 에지 ${edgeRows.value.length}개`
  }
  return ''
})
</script>

<template>
  <section class="schema-preview">
    <h2 class="panel-title">스키마 / 그래프DB</h2>
    <div class="panel-body">
      <p v-if="!file" class="placeholder">문서를 선택하세요</p>
      <template v-else>
        <div class="tabs">
          <button :class="{ active: activeTab === 'schema' }" @click="activeTab = 'schema'">스키마</button>
          <button :class="{ active: activeTab === 'nodes' }" @click="activeTab = 'nodes'">Nodes</button>
          <button :class="{ active: activeTab === 'edges' }" @click="activeTab = 'edges'">Edges</button>
        </div>

        <div class="content-area">
          <template v-if="activeTab === 'schema'">
            <p v-if="!schema" class="placeholder">아직 데이터가 없습니다</p>
            <div v-else class="schema-tables">
              <div class="schema-table-block">
                <h4 class="table-label">node_types</h4>
                <div class="table-wrap">
                  <table class="data-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Description</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="nt in schema.node_types" :key="nt.name">
                        <td>{{ nt.name }}</td>
                        <td>{{ nt.description }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div class="schema-table-block">
                <h4 class="table-label">edge_types</h4>
                <div class="table-wrap">
                  <table class="data-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Description</th>
                        <th>Source</th>
                        <th>Target</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="et in schema.edge_types" :key="et.name">
                        <td>{{ et.name }}</td>
                        <td>{{ et.description }}</td>
                        <td>{{ et.source }}</td>
                        <td>{{ et.target }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="activeTab === 'nodes'">
            <p v-if="nodeRows.length === 0" class="placeholder">아직 데이터가 없습니다</p>
            <div v-else class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Label</th>
                    <th>Type</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="n in nodeRows" :key="n.id">
                    <td>{{ n.id }}</td>
                    <td>{{ n.label }}</td>
                    <td>{{ n.type }}</td>
                    <td>{{ n.detail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>

          <template v-else-if="activeTab === 'edges'">
            <p v-if="edgeRows.length === 0" class="placeholder">아직 데이터가 없습니다</p>
            <div v-else class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Target</th>
                    <th>Type</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(e, i) in edgeRows" :key="i">
                    <td>{{ e.source }}</td>
                    <td>{{ e.target }}</td>
                    <td>{{ e.type }}</td>
                    <td>{{ e.detail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>

        <p class="status-line">{{ statusText }}</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.schema-preview {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.panel-title {
  flex-shrink: 0;
  margin: 0;
  padding: 0.6rem 1rem;
  font-size: 1rem;
  color: #fff;
  background: #b45309;
}
.panel-body {
  flex: 1;
  min-height: 0;
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
.content-area {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.schema-tables {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.schema-table-block {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.table-label {
  flex-shrink: 0;
  margin: 0 0 0.25rem;
  font-size: 0.7rem;
  font-weight: bold;
  text-transform: uppercase;
  color: #666;
}
.table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid #ddd;
  border-radius: 4px;
  scrollbar-width: thin;
  scrollbar-color: #b0b0b0 #f0f0f0;
}
.data-table {
  border-collapse: collapse;
  font-size: 0.8rem;
  width: 100%;
}
.data-table th,
.data-table td {
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid #eee;
  text-align: left;
  white-space: nowrap;
}
.data-table th {
  position: sticky;
  top: 0;
  background: #f5f5f5;
  font-weight: 600;
}
.data-table tbody tr:hover {
  background: #f8f8f8;
}
.status-line {
  flex-shrink: 0;
  margin: 0.5rem 0 0;
  padding-top: 0.5rem;
  border-top: 1px solid #ccc;
  font-size: 0.85rem;
  color: #555;
}
</style>
