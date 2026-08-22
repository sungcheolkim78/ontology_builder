<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  file: { type: Object, default: null },
  enabledTypes: { type: Set, default: () => new Set() },
  schemaVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['types-available', 'schema-updated'])

const nodes = ref([])
const edges = ref([])
const status = ref('empty') // empty | loading | no-graph | ready | error
const error = ref('')
const message = ref('')
const isGeneratingSchema = ref(false)
const isExtracting = ref(false)
const schemaTypeCount = ref(null) // null = unknown/no schema yet

const TYPE_COLORS = ['#4f8ef7', '#f7a24f', '#4fbf7a', '#c96fd6', '#e0555a', '#5ac8d8']
const RADIUS = 100
const CENTER = 130

function colorFor(type) {
  const types = [...new Set(nodes.value.map((n) => n.type))].sort()
  const index = types.indexOf(type)
  return TYPE_COLORS[index % TYPE_COLORS.length]
}

const positions = computed(() => {
  const map = {}
  nodes.value.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(nodes.value.length, 1)
    map[node.id] = {
      x: CENTER + RADIUS * Math.cos(angle),
      y: CENTER + RADIUS * Math.sin(angle),
    }
  })
  return map
})

const visibleNodes = computed(() => nodes.value.filter((n) => props.enabledTypes.has(n.type)))

const visibleEdges = computed(() => {
  const visibleIds = new Set(visibleNodes.value.map((n) => n.id))
  return edges.value.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
})

async function loadSchemaStatus(file) {
  if (!file) {
    schemaTypeCount.value = null
    return
  }
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(file.filename)}/schema`)
    if (res.status === 404) {
      schemaTypeCount.value = 0
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const schema = await res.json()
    schemaTypeCount.value = schema.node_types.length
  } catch (err) {
    schemaTypeCount.value = null
  }
}

async function loadGraph(file) {
  nodes.value = []
  edges.value = []
  error.value = ''
  message.value = ''
  if (!file) {
    status.value = 'empty'
    return
  }
  status.value = 'loading'
  await loadSchemaStatus(file)
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(file.filename)}`)
    if (res.status === 404) {
      status.value = 'no-graph'
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    nodes.value = data.nodes
    edges.value = data.edges
    status.value = 'ready'
    emit('types-available', [...new Set(data.nodes.map((n) => n.type))].sort())
  } catch (err) {
    status.value = 'error'
    error.value = '온톨로지를 불러오지 못했습니다: ' + err.message
  }
}

watch(() => props.file, loadGraph, { immediate: true })
watch(() => props.schemaVersion, () => loadSchemaStatus(props.file))

async function generateSchema() {
  if (!props.file) return
  isGeneratingSchema.value = true
  error.value = ''
  message.value = ''
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/schema`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const schema = await res.json()
    schemaTypeCount.value = schema.node_types.length
    message.value = `스키마 생성 완료 (노드 타입 ${schema.node_types.length}개)`
    emit('schema-updated')
  } catch (err) {
    error.value = '스키마 생성 실패: ' + err.message
  } finally {
    isGeneratingSchema.value = false
  }
}

async function extract() {
  if (!props.file) return
  isExtracting.value = true
  error.value = ''
  message.value = ''
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/extract`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadGraph(props.file)
    message.value = '그래프 추출 완료'
    emit('schema-updated')
  } catch (err) {
    error.value = '그래프 추출 실패: ' + err.message
  } finally {
    isExtracting.value = false
  }
}
</script>

<template>
  <section class="graph">
    <h2>온톨로지 그래프</h2>

    <p v-if="status === 'empty'" class="placeholder">문서를 선택하세요</p>

    <template v-else-if="status === 'no-graph' || status === 'ready'">
      <div class="actions">
        <button :disabled="isGeneratingSchema" @click="generateSchema">
          {{ isGeneratingSchema ? '생성 중...' : '스키마 생성' }}
        </button>
        <button :disabled="isExtracting" @click="extract">
          {{ isExtracting ? '추출 중...' : '그래프 추출' }}
        </button>
      </div>
      <p class="schema-status">
        <template v-if="schemaTypeCount === null">스키마 상태 확인 중...</template>
        <template v-else-if="schemaTypeCount === 0">활성 스키마 없음 (추출 시 기본 스키마 사용)</template>
        <template v-else>활성 스키마: 노드 타입 {{ schemaTypeCount }}개</template>
      </p>
      <p v-if="message" class="success">{{ message }}</p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="status === 'no-graph' && !error" class="placeholder">
        아직 추출된 온톨로지가 없습니다
      </p>
    </template>

    <p v-else-if="status === 'error'" class="error">{{ error }}</p>

    <svg v-if="status === 'ready'" viewBox="0 0 260 260" class="graph-svg">
      <line
        v-for="(edge, i) in visibleEdges"
        :key="i"
        :x1="positions[edge.source].x"
        :y1="positions[edge.source].y"
        :x2="positions[edge.target].x"
        :y2="positions[edge.target].y"
        stroke="#bbb"
      />
      <g v-for="node in visibleNodes" :key="node.id">
        <circle
          :cx="positions[node.id].x"
          :cy="positions[node.id].y"
          r="18"
          :fill="colorFor(node.type)"
        />
        <text
          :x="positions[node.id].x"
          :y="positions[node.id].y + 30"
          text-anchor="middle"
          class="node-label"
        >
          {{ node.label }}
        </text>
      </g>
    </svg>
  </section>
</template>

<style scoped>
.graph {
  height: 100%;
  overflow: auto;
  padding: 1rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.placeholder {
  color: #888;
}
.schema-status {
  color: #555;
  font-size: 0.85rem;
}
.success {
  color: #1a7f37;
}
.error {
  color: red;
}
.graph-svg {
  width: 100%;
  max-width: 320px;
}
.node-label {
  font-size: 9px;
  fill: #333;
}
</style>
