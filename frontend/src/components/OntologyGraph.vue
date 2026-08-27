<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { VEdgeLabel, VNetworkGraph } from 'v-network-graph'
import 'v-network-graph/lib/style.css'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import { colorForNodeType } from '../utils/nodeColors.js'
import { apiFetch } from '../utils/api.js'

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
const schemaDocumentType = ref('general')
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

const EDGE_TYPE_COLORS = ['#8a6d3b', '#2f9e8f', '#a05195', '#d45087', '#665191', '#2c7fb8']

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

// Memoized once per displayNodes/displayEdges change instead of being
// recomputed from scratch on every colorFor/edgeColorFor call -- v-network-
// graph calls these once per node/edge on every render (including every
// force-simulation tick), so recomputing the sorted type list inline made
// rendering O(n^2)/O(n*m) instead of O(n).
const nodeTypeOrder = computed(() => [...new Set(displayNodes.value.map((n) => n.type))].sort())
const edgeTypeOrder = computed(() => [...new Set(displayEdges.value.map((e) => e.type))].sort())

function colorFor(type) {
  return colorForNodeType(type, nodeTypeOrder.value)
}

function edgeColorFor(type) {
  const index = edgeTypeOrder.value.indexOf(type)
  return EDGE_TYPE_COLORS[index % EDGE_TYPE_COLORS.length]
}

// --- v-network-graph data shapes ---
const vngNodes = computed(() => {
  const result = {}
  for (const n of visibleNodes.value) {
    result[n.id] = { name: n.label, type: n.type, detail: n.detail }
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

// --- node hover tooltip ---
const hoveredNode = ref(null)
const tooltipPos = ref({ x: 0, y: 0 })

function onNodePointerOver({ node, event }) {
  hoveredNode.value = vngNodes.value[node] ?? null
  tooltipPos.value = { x: event.clientX, y: event.clientY }
}

function onNodePointerMove(event) {
  if (hoveredNode.value) {
    tooltipPos.value = { x: event.clientX, y: event.clientY }
  }
}

function onNodePointerOut() {
  hoveredNode.value = null
}

const eventHandlers = {
  'node:pointerover': onNodePointerOver,
  'node:pointerout': onNodePointerOut,
  'node:dragstart': onNodeDragStart,
  'node:pointermove': onNodeDragMove,
  'node:dragend': onNodeDragEnd,
}

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
// Force priority (strongest to weakest pull): linked nodes > same-type nodes >
// gravity toward the center. Charge is a separate, independent repulsion that
// pushes every node away from every other node as hard as tolerable given the
// above -- it is not "weaker" than the others, it opposes them.
const CENTER = 200
const LINK_DISTANCE = 140
const LINK_STRENGTH = 1
const CLUSTER_STRENGTH = 0.2
const CHARGE_STRENGTH = -900
// Much weaker than link/cluster, so it only matters for nodes those forces
// don't already pull somewhere -- i.e. it reels in isolated nodes that would
// otherwise drift away under pure charge repulsion, without fighting the
// clustering/linking of connected nodes.
const GRAVITY_STRENGTH = 0.03
let simulation = null
// Keyed by node id, same object instances as the live simulation's nodes --
// used by the drag handlers below to pin/release a node's fixed position
// (fx/fy) without waiting for the next visibleNodes/visibleEdges watcher run.
let simNodesById = new Map()
// The 'end' event fires both after the initial layout settles and after any
// later alphaTarget reheat (e.g. from a drag) cools back down. Only the
// initial settle should trigger fitToContents -- re-fitting after a manual
// drag would yank back the zoom/pan the user just set up.
let suppressNextSimulationEnd = false

// Pulls nodes of the same `type` toward that type's current centroid each
// tick, so same-type nodes visually cluster together instead of spreading
// out purely by link/charge forces.
function forceCluster(strength) {
  let nodes = []
  function force(alpha) {
    const centers = new Map()
    for (const n of nodes) {
      let c = centers.get(n.type)
      if (!c) {
        c = { x: 0, y: 0, count: 0 }
        centers.set(n.type, c)
      }
      c.x += n.x
      c.y += n.y
      c.count += 1
    }
    for (const c of centers.values()) {
      c.x /= c.count
      c.y /= c.count
    }
    for (const n of nodes) {
      const c = centers.get(n.type)
      n.vx -= (n.x - c.x) * strength * alpha
      n.vy -= (n.y - c.y) * strength * alpha
    }
  }
  force.initialize = (_nodes) => {
    nodes = _nodes
  }
  return force
}

watch(
  [visibleNodes, visibleEdges],
  ([nodeList, edgeList]) => {
    const simNodes = nodeList.map((node) => {
      const existing = layouts.value.nodes[node.id]
      return existing
        ? { id: node.id, type: node.type, x: existing.x, y: existing.y }
        : { id: node.id, type: node.type }
    })
    const simLinks = edgeList.map((e) => ({ source: e.source, target: e.target }))
    simNodesById = new Map(simNodes.map((n) => [n.id, n]))

    simulation?.stop()
    suppressNextSimulationEnd = false
    simulation = forceSimulation(simNodes)
      .force('link', forceLink(simLinks).id((d) => d.id).distance(LINK_DISTANCE).strength(LINK_STRENGTH))
      .force('cluster', forceCluster(CLUSTER_STRENGTH))
      .force('charge', forceManyBody().strength(CHARGE_STRENGTH))
      .force('x', forceX(CENTER).strength(GRAVITY_STRENGTH))
      .force('y', forceY(CENTER).strength(GRAVITY_STRENGTH))
      .force('collide', forceCollide(40))
      .on('tick', () => {
        const positions = {}
        simNodes.forEach((n) => {
          positions[n.id] = { x: n.x, y: n.y }
        })
        layouts.value = { nodes: positions }
      })
      .on('end', () => {
        if (suppressNextSimulationEnd) {
          suppressNextSimulationEnd = false
          return
        }
        fitSoon()
      })
  },
  { immediate: true }
)

// Pins the dragged node(s) at the pointer position (d3-force convention: an
// fx/fy on a node overrides the simulation for that axis) and reheats the
// simulation so the 'link'/'cluster'/'charge' forces above keep recomputing
// every other node's position in real time -- this is what makes edge-linked
// neighbors follow the dragged node instead of staying put.
function onNodeDragStart(positions) {
  if (!simulation) return
  for (const [id, pos] of Object.entries(positions)) {
    const n = simNodesById.get(id)
    if (!n) continue
    n.fx = pos.x
    n.fy = pos.y
  }
  simulation.alphaTarget(0.3).restart()
}

function onNodeDragMove(positions) {
  if (!simulation) return
  for (const [id, pos] of Object.entries(positions)) {
    const n = simNodesById.get(id)
    if (!n) continue
    n.fx = pos.x
    n.fy = pos.y
  }
}

function onNodeDragEnd(positions) {
  if (!simulation) return
  for (const id of Object.keys(positions)) {
    const n = simNodesById.get(id)
    if (!n) continue
    n.fx = null
    n.fy = null
  }
  suppressNextSimulationEnd = true
  simulation.alphaTarget(0)
}

onUnmounted(() => {
  simulation?.stop()
  stopElapsedTimer()
})

const configs = computed(() => ({
  view: {
    autoPanAndZoomOnLoad: 'fit-content',
    fitContentMargin: '10%',
    scalingObjects: true,
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
      visible: showNodeLabels.value,
      text: 'name',
      fontSize: () => 10 / zoomLevel.value,
    },
  },
  edge: {
    normal: {
      color: (edge) => edgeColorFor(edge.label),
      width: 1.5,
    },
    type: 'curve',
    gap: 12,
    label: {
      fontSize: () => 9 / zoomLevel.value,
      color: '#555',
    },
  },
}))

const graphRef = ref(null)
const zoomLevel = ref(1)
const showNodeLabels = ref(true)
const showEdgeLabels = ref(true)

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
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(file.filename)}/schema`)
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
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(file.filename)}`)
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
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/schema`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_type: schemaDocumentType.value }),
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
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/extract`, {
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
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/embed`, {
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
          <select v-model="schemaDocumentType" :disabled="isGeneratingSchema" class="schema-type-select">
            <option value="general">일반 문서</option>
            <option value="legal">법률·보험 문서</option>
          </select>
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
          <label class="label-toggle">
            <input type="checkbox" v-model="showNodeLabels" />
            Node Label
          </label>
          <label class="label-toggle">
            <input type="checkbox" v-model="showEdgeLabels" />
            Edge Label
          </label>
        </div>
        <p v-if="progressMessage" class="progress">{{ progressMessage }}</p>
        <p v-if="message" class="success">{{ message }}</p>
        <p v-if="error" class="error">{{ error }}</p>
        <p v-if="displayMode === 'none' && !error" class="placeholder">
          스키마를 생성하거나 라이브러리에서 선택하세요
        </p>
      </template>

      <p v-else-if="status === 'error'" class="error">{{ error }}</p>

      <div v-if="displayMode !== 'none'" class="graph-viewport" @mousemove="onNodePointerMove">
        <v-network-graph
          ref="graphRef"
          v-model:selected-nodes="selectedNodes"
          v-model:zoom-level="zoomLevel"
          :nodes="vngNodes"
          :edges="vngEdges"
          :layouts="layouts"
          :configs="configs"
          :event-handlers="eventHandlers"
        >
          <template #edge-label="{ edge, ...slotProps }">
            <v-edge-label v-if="showEdgeLabels" :text="edge.label" align="center" vertical-align="above" v-bind="slotProps" />
          </template>
        </v-network-graph>

        <div
          v-if="hoveredNode"
          class="node-tooltip"
          :style="{ left: tooltipPos.x + 12 + 'px', top: tooltipPos.y + 12 + 'px' }"
        >
          <div class="node-tooltip-type">{{ hoveredNode.type }}</div>
          <div class="node-tooltip-label">{{ hoveredNode.name }}</div>
          <div v-if="hoveredNode.detail" class="node-tooltip-detail">{{ hoveredNode.detail }}</div>
        </div>
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
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  flex-shrink: 0;
}
.label-toggle {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.85rem;
  color: #555;
  margin-left: 0.25rem;
  cursor: pointer;
}
.placeholder {
  color: #888;
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
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.08), 0 1px 4px rgba(0, 0, 0, 0.12);
  position: relative;
}
.node-tooltip {
  position: fixed;
  z-index: 2000;
  pointer-events: none;
  max-width: 260px;
  background: #1f2937;
  color: #fff;
  padding: 0.5rem 0.65rem;
  border-radius: 6px;
  font-size: 0.8rem;
  line-height: 1.4;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
}
.node-tooltip-type {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #9ca3af;
  margin-bottom: 0.15rem;
}
.node-tooltip-label {
  font-weight: 600;
  margin-bottom: 0.15rem;
}
.node-tooltip-detail {
  color: #d1d5db;
}
</style>
