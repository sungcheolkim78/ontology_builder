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
