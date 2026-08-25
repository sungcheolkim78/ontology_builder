<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { VEdgeLabel, VNetworkGraph } from 'v-network-graph'
import 'v-network-graph/lib/style.css'
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force'

const props = defineProps({
  file: { type: Object, default: null },
  enabledTypes: { type: Set, default: () => new Set() },
  enabledEdgeTypes: { type: Set, default: () => new Set() },
  schemaVersion: { type: Number, default: 0 },
  highlightedNodeIds: { type: Array, default: () => [] },
})
const emit = defineEmits(['types-available', 'edge-types-available', 'schema-updated'])

const nodes = ref([])
const edges = ref([])
const schema = ref(null)
const status = ref('empty') // empty | loading | no-graph | ready | error
const error = ref('')
const message = ref('')
const isGeneratingSchema = ref(false)
const isExtracting = ref(false)
const isEmbedding = ref(false)
const elapsedSeconds = ref(0)
let elapsedTimer = null

function startElapsedTimer() {
  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value += 1
  }, 1000)
}

function stopElapsedTimer() {
  clearInterval(elapsedTimer)
  elapsedTimer = null
}

const progressMessage = computed(() => {
  if (isGeneratingSchema.value) return `문서를 읽어 스키마 생성 중... ${elapsedSeconds.value}초`
  if (isExtracting.value) return `문서를 읽고 주어진 스키마로 노드와 에지를 생성 중... ${elapsedSeconds.value}초`
  if (isEmbedding.value) return `노드 임베딩 생성 중... ${elapsedSeconds.value}초`
  return ''
})

const TYPE_COLORS = ['#4f8ef7', '#f7a24f', '#4fbf7a', '#c96fd6', '#e0555a', '#5ac8d8']
const EDGE_TYPE_COLORS = ['#8a6d3b', '#2f9e8f', '#a05195', '#d45087', '#665191', '#2c7fb8']

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

const visibleNodes = computed(() => displayNodes.value.filter((n) => props.enabledTypes.has(n.type)))

const visibleEdges = computed(() => {
  const visibleIds = new Set(visibleNodes.value.map((n) => n.id))
  return displayEdges.value.filter(
    (e) => props.enabledEdgeTypes.has(e.type) && visibleIds.has(e.source) && visibleIds.has(e.target)
  )
})

function colorFor(type) {
  const types = [...new Set(displayNodes.value.map((n) => n.type))].sort()
  const index = types.indexOf(type)
  return TYPE_COLORS[index % TYPE_COLORS.length]
}

function edgeColorFor(type) {
  const types = [...new Set(displayEdges.value.map((e) => e.type))].sort()
  const index = types.indexOf(type)
  return EDGE_TYPE_COLORS[index % EDGE_TYPE_COLORS.length]
}

// --- v-network-graph data shapes ---
const vngNodes = computed(() => {
  const result = {}
  for (const n of visibleNodes.value) {
    result[n.id] = { name: n.label, type: n.type }
  }
  return result
})

const vngEdges = computed(() => {
  const result = {}
  visibleEdges.value.forEach((e, i) => {
    result[`e${i}`] = { source: e.source, target: e.target, label: e.type }
  })
  return result
})

const layouts = ref({ nodes: {} })
const selectedNodes = ref([])

watch(
  () => props.highlightedNodeIds,
  (ids) => {
    selectedNodes.value = ids ?? []
    focusOnNodes(selectedNodes.value)
  }
)

watch(
  displayNodes,
  (list) => {
    emit('types-available', [...new Set(list.map((n) => n.type))].sort())
  },
  { immediate: true }
)

watch(
  displayEdges,
  (list) => {
    emit('edge-types-available', [...new Set(list.map((e) => e.type))].sort())
  },
  { immediate: true }
)

// --- d3-force layout ---
const CENTER = 200
let simulation = null

watch(
  [visibleNodes, visibleEdges],
  ([nodeList, edgeList]) => {
    const simNodes = nodeList.map((node) => {
      const existing = layouts.value.nodes[node.id]
      return existing ? { id: node.id, x: existing.x, y: existing.y } : { id: node.id }
    })
    const simLinks = edgeList.map((e) => ({ source: e.source, target: e.target }))

    simulation?.stop()
    simulation = forceSimulation(simNodes)
      .force('charge', forceManyBody().strength(-300))
      .force('link', forceLink(simLinks).id((d) => d.id).distance(120))
      .force('center', forceCenter(CENTER, CENTER))
      .force('collide', forceCollide(30))
      .on('tick', () => {
        const positions = {}
        simNodes.forEach((n) => {
          positions[n.id] = { x: n.x, y: n.y }
        })
        layouts.value = { nodes: positions }
      })
      .on('end', fitSoon)
  },
  { immediate: true }
)

onUnmounted(() => {
  simulation?.stop()
  stopElapsedTimer()
})

const configs = computed(() => ({
  view: {
    autoPanAndZoomOnLoad: 'fit-content',
    fitContentMargin: '10%',
  },
  node: {
    normal: {
      radius: 18,
      color: (node) => colorFor(node.type),
    },
    selected: {
      type: 'circle',
      radius: 24,
      color: (node) => colorFor(node.type),
      strokeWidth: 4,
      strokeColor: '#ffcc00',
    },
    selectable: true,
    label: {
      visible: true,
      text: 'name',
      fontSize: 10,
    },
  },
  edge: {
    normal: {
      color: (edge) => edgeColorFor(edge.label),
      width: 1.5,
    },
    label: {
      fontSize: 9,
      color: '#555',
    },
  },
}))

const graphRef = ref(null)

function resetView() {
  graphRef.value?.fitToContents()
}

function focusOnNodes(ids) {
  if (!graphRef.value || !ids || ids.length === 0) return
  const positions = ids.map((id) => layouts.value.nodes[id]).filter(Boolean)
  if (positions.length === 0) return

  const centerX = positions.reduce((sum, p) => sum + p.x, 0) / positions.length
  const centerY = positions.reduce((sum, p) => sum + p.y, 0) / positions.length

  graphRef.value.transitionWhile(() => {
    const box = graphRef.value.getViewBox()
    const halfWidth = (box.right - box.left) / 2
    const halfHeight = (box.bottom - box.top) / 2
    graphRef.value.setViewBox({
      left: centerX - halfWidth,
      right: centerX + halfWidth,
      top: centerY - halfHeight,
      bottom: centerY + halfHeight,
    })
  })
}

async function fitSoon() {
  await nextTick()
  graphRef.value?.fitToContents()
}

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
  layouts.value = { nodes: {} }
  selectedNodes.value = []
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
  fitSoon()
}

watch(() => props.file, loadGraph, { immediate: true })
watch(() => props.schemaVersion, () => loadSchemaStatus(props.file))

async function generateSchema() {
  if (!props.file) return
  isGeneratingSchema.value = true
  error.value = ''
  message.value = ''
  startElapsedTimer()
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/schema`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    schema.value = await res.json()
    layouts.value = { nodes: {} }
    message.value = `스키마 생성 완료 (노드 타입 ${schema.value.node_types.length}개, 엣지 타입 ${schema.value.edge_types.length}개)`
    emit('schema-updated')
    fitSoon()
  } catch (err) {
    error.value = '스키마 생성 실패: ' + err.message
  } finally {
    isGeneratingSchema.value = false
    stopElapsedTimer()
  }
}

async function extract() {
  if (!props.file) return
  isExtracting.value = true
  error.value = ''
  message.value = ''
  startElapsedTimer()
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/extract`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadGraph(props.file)
    message.value = `그래프 추출 완료 (노드 ${nodes.value.length}개, 엣지 ${edges.value.length}개)`
    emit('schema-updated')
  } catch (err) {
    error.value = '그래프 추출 실패: ' + err.message
  } finally {
    isExtracting.value = false
    stopElapsedTimer()
  }
}

async function embed() {
  if (!props.file) return
  isEmbedding.value = true
  error.value = ''
  message.value = ''
  startElapsedTimer()
  try {
    const res = await fetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/embed`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const result = await res.json()
    message.value = `임베딩 생성 완료 (노드 ${result.embedded}개)`
  } catch (err) {
    error.value = '임베딩 생성 실패: ' + err.message
  } finally {
    isEmbedding.value = false
    stopElapsedTimer()
  }
}
</script>

<template>
  <section class="graph">
    <h2 class="panel-title">온톨로지 그래프</h2>

    <div class="panel-body">
      <p v-if="status === 'empty'" class="placeholder">문서를 선택하세요</p>

      <template v-else-if="status === 'no-graph' || status === 'ready'">
        <div class="actions">
          <button :disabled="isGeneratingSchema" @click="generateSchema">
            {{ isGeneratingSchema ? '생성 중...' : '스키마 생성' }}
          </button>
          <button :disabled="isExtracting" @click="extract">
            {{ isExtracting ? '추출 중...' : '그래프 추출' }}
          </button>
          <button :disabled="isEmbedding || status !== 'ready'" @click="embed">
            {{ isEmbedding ? '임베딩 생성 중...' : '임베딩 생성' }}
          </button>
          <button v-if="displayMode !== 'none'" @click="resetView">리셋</button>
        </div>
        <p class="schema-status">
          <template v-if="schemaTypeCount === 0">활성 스키마 없음 (추출 시 기본 스키마 사용)</template>
          <template v-else>활성 스키마: 노드 타입 {{ schemaTypeCount }}개</template>
        </p>
        <p v-if="progressMessage" class="progress">{{ progressMessage }}</p>
        <p v-if="message" class="success">{{ message }}</p>
        <p v-if="error" class="error">{{ error }}</p>
        <p v-if="displayMode === 'schema'" class="placeholder">스키마 미리보기 (아직 추출 전)</p>
        <p v-if="displayMode === 'none' && !error" class="placeholder">
          스키마를 생성하거나 라이브러리에서 선택하세요
        </p>
      </template>

      <p v-else-if="status === 'error'" class="error">{{ error }}</p>

      <div v-if="displayMode !== 'none'" class="graph-viewport">
        <v-network-graph
          ref="graphRef"
          v-model:selected-nodes="selectedNodes"
          :nodes="vngNodes"
          :edges="vngEdges"
          :layouts="layouts"
          :configs="configs"
        >
          <template #edge-label="{ edge, ...slotProps }">
            <v-edge-label :text="edge.label" align="center" vertical-align="above" v-bind="slotProps" />
          </template>
        </v-network-graph>
      </div>
    </div>
  </section>
</template>

<style scoped>
.graph {
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
  background: #7c3aed;
}
.panel-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 1rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-shrink: 0;
}
.placeholder {
  color: #888;
  flex-shrink: 0;
}
.schema-status {
  color: #555;
  font-size: 0.85rem;
  flex-shrink: 0;
}
.progress {
  color: #555;
  font-style: italic;
  flex-shrink: 0;
}
.success {
  color: #1a7f37;
  flex-shrink: 0;
}
.error {
  color: red;
  flex-shrink: 0;
}
.graph-viewport {
  flex: 1;
  min-height: 0;
  width: 100%;
}
</style>
