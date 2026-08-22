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
const schema = ref(null)
const status = ref('empty') // empty | loading | no-graph | ready | error
const error = ref('')
const message = ref('')
const isGeneratingSchema = ref(false)
const isExtracting = ref(false)

const TYPE_COLORS = ['#4f8ef7', '#f7a24f', '#4fbf7a', '#c96fd6', '#e0555a', '#5ac8d8']
const RADIUS = 100
const CENTER = 130
const MIN_ZOOM = 0.3
const MAX_ZOOM = 3

const schemaTypeCount = computed(() => (schema.value ? schema.value.node_types.length : 0))

const displayMode = computed(() => {
  if (status.value === 'ready') return 'graph'
  if (schema.value && schema.value.node_types.length > 0) return 'schema'
  return 'none'
})

const displayNodes = computed(() => {
  if (displayMode.value === 'graph') return nodes.value
  if (displayMode.value === 'schema') {
    return schema.value.node_types.map((nt) => ({ id: nt.name, label: nt.name, type: nt.name }))
  }
  return []
})

const displayEdges = computed(() => {
  if (displayMode.value === 'graph') return edges.value
  if (displayMode.value === 'schema') {
    return schema.value.edge_types.map((et) => ({
      source: et.source,
      target: et.target,
      type: et.name,
    }))
  }
  return []
})

function colorFor(type) {
  const types = [...new Set(displayNodes.value.map((n) => n.type))].sort()
  const index = types.indexOf(type)
  return TYPE_COLORS[index % TYPE_COLORS.length]
}

const positions = computed(() => {
  const map = {}
  displayNodes.value.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(displayNodes.value.length, 1)
    map[node.id] = {
      x: CENTER + RADIUS * Math.cos(angle),
      y: CENTER + RADIUS * Math.sin(angle),
    }
  })
  return map
})

const visibleNodes = computed(() => displayNodes.value.filter((n) => props.enabledTypes.has(n.type)))

const visibleEdges = computed(() => {
  const visibleIds = new Set(visibleNodes.value.map((n) => n.id))
  return displayEdges.value.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
})

watch(
  displayNodes,
  (list) => {
    emit('types-available', [...new Set(list.map((n) => n.type))].sort())
  },
  { immediate: true }
)

// --- zoom / pan ---
const zoomScale = ref(1)
const panX = ref(0)
const panY = ref(0)
let panStartX = 0
let panStartY = 0
let panOriginX = 0
let panOriginY = 0

function onWheel(event) {
  const delta = event.deltaY > 0 ? -0.1 : 0.1
  zoomScale.value = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoomScale.value + delta))
}

function startPan(event) {
  panStartX = event.clientX
  panStartY = event.clientY
  panOriginX = panX.value
  panOriginY = panY.value
  window.addEventListener('mousemove', onPan)
  window.addEventListener('mouseup', stopPan)
}

function onPan(event) {
  panX.value = panOriginX + (event.clientX - panStartX)
  panY.value = panOriginY + (event.clientY - panStartY)
}

function stopPan() {
  window.removeEventListener('mousemove', onPan)
  window.removeEventListener('mouseup', stopPan)
}

function resetView() {
  zoomScale.value = 1
  panX.value = 0
  panY.value = 0
}

const groupTransform = computed(
  () => `translate(${panX.value}px, ${panY.value}px) scale(${zoomScale.value})`
)

// --- data loading ---
async function loadSchemaStatus(file) {
  if (!file) {
    schema.value = null
    return
  }
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(file.filename)}/schema`)
    if (res.status === 404) {
      schema.value = null
      return
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    schema.value = await res.json()
  } catch (err) {
    schema.value = null
  }
}

async function loadGraph(file) {
  nodes.value = []
  edges.value = []
  error.value = ''
  message.value = ''
  resetView()
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
    schema.value = await res.json()
    resetView()
    message.value = `스키마 생성 완료 (노드 타입 ${schema.value.node_types.length}개)`
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
        <button v-if="displayMode !== 'none'" @click="resetView">리셋</button>
      </div>
      <p class="schema-status">
        <template v-if="schemaTypeCount === 0">활성 스키마 없음 (추출 시 기본 스키마 사용)</template>
        <template v-else>활성 스키마: 노드 타입 {{ schemaTypeCount }}개</template>
      </p>
      <p v-if="message" class="success">{{ message }}</p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="displayMode === 'schema'" class="placeholder">스키마 미리보기 (아직 추출 전)</p>
      <p v-if="displayMode === 'none' && !error" class="placeholder">
        스키마를 생성하거나 라이브러리에서 선택하세요
      </p>
    </template>

    <p v-else-if="status === 'error'" class="error">{{ error }}</p>

    <div
      v-if="displayMode !== 'none'"
      class="graph-viewport"
      @wheel.prevent="onWheel"
      @mousedown="startPan"
    >
      <svg viewBox="0 0 260 260" class="graph-svg" :style="{ transform: groupTransform }">
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
    </div>
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
.graph-viewport {
  width: 100%;
  max-width: 320px;
  overflow: hidden;
  cursor: grab;
  touch-action: none;
}
.graph-viewport:active {
  cursor: grabbing;
}
.graph-svg {
  width: 100%;
  display: block;
  transform-origin: center center;
}
.node-label {
  font-size: 9px;
  fill: #333;
}
</style>
