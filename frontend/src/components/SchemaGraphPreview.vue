<script setup>
import { computed, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'

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
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(file.filename)}/schema`)
    if (res.ok) schema.value = await res.json()
  } catch (err) {
    // best-effort; tab just shows "no data" on failure
  }

  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(file.filename)}`)
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
  <section class="flex h-full flex-col">
    <div class="panel-header">
      <span>스키마 / 그래프DB</span>
    </div>
    <div class="flex min-h-0 flex-1 flex-col p-3">
      <p v-if="!file" class="text-xs text-ink-faint">문서를 선택하세요</p>
      <template v-else>
        <div class="mb-2.5 flex flex-shrink-0 gap-1.5">
          <button
            class="rounded-md border px-2.5 py-1 text-xs transition-colors"
            :class="activeTab === 'schema'
              ? 'border-accent/50 bg-accent/20 text-ink'
              : 'border-border bg-surface-raised text-ink-muted hover:bg-white/5'"
            @click="activeTab = 'schema'"
          >스키마</button>
          <button
            class="rounded-md border px-2.5 py-1 text-xs transition-colors"
            :class="activeTab === 'nodes'
              ? 'border-accent/50 bg-accent/20 text-ink'
              : 'border-border bg-surface-raised text-ink-muted hover:bg-white/5'"
            @click="activeTab = 'nodes'"
          >Nodes</button>
          <button
            class="rounded-md border px-2.5 py-1 text-xs transition-colors"
            :class="activeTab === 'edges'
              ? 'border-accent/50 bg-accent/20 text-ink'
              : 'border-border bg-surface-raised text-ink-muted hover:bg-white/5'"
            @click="activeTab = 'edges'"
          >Edges</button>
        </div>

        <div class="flex min-h-0 flex-1 flex-col">
          <template v-if="activeTab === 'schema'">
            <p v-if="!schema" class="text-xs text-ink-faint">아직 데이터가 없습니다</p>
            <div v-else class="flex min-h-0 flex-1 flex-col gap-3">
              <div class="flex min-h-0 flex-1 flex-col">
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
              <div class="flex min-h-0 flex-1 flex-col">
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
            <p v-if="nodeRows.length === 0" class="text-xs text-ink-faint">아직 데이터가 없습니다</p>
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
            <p v-if="edgeRows.length === 0" class="text-xs text-ink-faint">아직 데이터가 없습니다</p>
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

        <p class="mt-2 flex-shrink-0 border-t border-border pt-2 text-[11px] text-ink-muted">{{ statusText }}</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.table-label {
  flex-shrink: 0;
  margin: 0 0 0.25rem;
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: theme('colors.ink.faint');
}
.table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid theme('colors.border.DEFAULT');
  border-radius: 6px;
}
.data-table {
  border-collapse: collapse;
  font-size: 0.75rem;
  width: 100%;
}
.data-table th,
.data-table td {
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid theme('colors.border.subtle');
  text-align: left;
  white-space: nowrap;
  color: theme('colors.ink.DEFAULT');
}
.data-table th {
  position: sticky;
  top: 0;
  background: theme('colors.surface.raised');
  color: theme('colors.ink.muted');
  font-weight: 600;
}
.data-table tbody tr:hover {
  background: rgba(255, 255, 255, 0.03);
}
</style>
