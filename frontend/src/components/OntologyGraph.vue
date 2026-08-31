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
  highlightedNodeIds: { type: Array, default: () => [] },
  // Bumped (a new object) by App.vue whenever SettingsPanel's workflow
  // actions (schema generation, extraction, schema-library apply, DB reset)
  // change what's in the backend for the current file -- this view has no
  // buttons of its own anymore, so it only ever learns to refresh this way.
  schemaRefreshRequest: { type: Object, default: null },
})
const emit = defineEmits(['types-available', 'edge-types-available'])

const nodes = ref([])
const edges = ref([])
const schema = ref(null)
const status = ref('empty') // empty | loading | no-graph | ready | error
const error = ref('')
// True right after a fresh schema generation, so the graph view shows the
// schema's own node/edge types instead of the previously extracted graph --
// otherwise a re-generated schema would be invisible until re-extraction.
// Reset on file change (defaults back to the extracted LadybugDB graph) and
// after a successful extraction (which produces a graph worth showing again).
const showSchemaPreview = ref(false)

const EDGE_TYPE_COLORS = ['#8a6d3b', '#2f9e8f', '#a05195', '#d45087', '#665191', '#2c7fb8']

const displayMode = computed(() => {
  if (showSchemaPreview.value && schema.value && schema.value.node_types.length > 0) return 'schema'
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
      color: '#c7ccd6',
      fontSize: () => 10 / zoomLevel.value,
    },
  },
  edge: {
    normal: {
      color: (edge) => edgeColorFor(edge.label),
      width: 1.5,
    },
    marker: {
      target: {
        type: 'arrow',
        width: 4,
        height: 4,
      },
    },
    type: 'curve',
    gap: 12,
    label: {
      fontSize: () => 11 / zoomLevel.value,
      color: '#9aa1b2',
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
  layouts.value = { nodes: {} }
  selectedNodes.value = []
  showSchemaPreview.value = false
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

// Explicit refresh for the "그래프 보기" button -- re-reads this document's
// nodes/edges straight from LadybugDB (via GET /api/ontology/{filename}, the
// same endpoint loadGraph uses) rather than trusting whatever this component
// already has in memory, since schema/graph changes made elsewhere (schema
// version activation, ontology evolution apply) don't otherwise trigger a
// reload here unless they happen to also bump schemaRefreshRequest. Leaves
// layouts/selectedNodes alone (unlike loadGraph's full reset for a brand new
// file) so nodes that still exist keep their on-screen position across a
// refresh; the [visibleNodes, visibleEdges] watch below already knows how to
// carry over existing positions and settle new nodes into place.
async function viewGraph() {
  if (!props.file) return
  showSchemaPreview.value = false
  error.value = ''
  status.value = 'loading'
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}`)
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

// A single consolidated trigger for every backend-side change SettingsPanel's
// workflow actions can make (schema generation, extraction, schema-library
// apply, DB reset) -- always reloads the full graph+schema, and additionally
// forces schema-preview mode when the action was specifically a fresh schema
// generation (see showSchemaPreview above).
watch(
  () => props.schemaRefreshRequest,
  async (req) => {
    if (!req) return
    await loadGraph(props.file)
    if (req.previewSchema) showSchemaPreview.value = true
  }
)
</script>

<template>
  <section class="flex h-full flex-col">
    <div class="panel-header">
      <span>온톨로지 그래프</span>
    </div>

    <div class="flex min-h-0 flex-1 flex-col p-3">
      <p v-if="status === 'empty'" class="flex-shrink-0 text-xs text-ink-faint">문서를 선택하세요</p>

      <template v-else-if="status === 'no-graph' || status === 'ready'">
        <div class="mb-2.5 flex flex-shrink-0 items-center gap-2">
          <button
            v-if="schema && schema.node_types.length > 0"
            type="button"
            class="btn"
            :class="{ 'ring-1 ring-accent': showSchemaPreview }"
            @click="showSchemaPreview = true"
          >
            스키마 미리보기
          </button>
          <button
            type="button"
            class="btn"
            :class="{ 'ring-1 ring-accent': !showSchemaPreview }"
            @click="viewGraph"
          >
            그래프 보기
          </button>
          <button v-if="displayMode !== 'none'" class="btn" @click="resetView">리셋</button>
          <label class="ml-1 flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
            <input type="checkbox" v-model="showNodeLabels" class="h-3.5 w-3.5 rounded border-border bg-surface-sunken accent-accent" />
            Node Label
          </label>
          <label class="flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
            <input type="checkbox" v-model="showEdgeLabels" class="h-3.5 w-3.5 rounded border-border bg-surface-sunken accent-accent" />
            Edge Label
          </label>
        </div>
        <p v-if="error" class="flex-shrink-0 text-xs text-red-400">{{ error }}</p>
        <p v-if="displayMode === 'none' && !error" class="flex-shrink-0 text-xs text-ink-faint">
          스키마를 생성하거나 라이브러리에서 선택하세요
        </p>
      </template>

      <p v-else-if="status === 'error'" class="flex-shrink-0 text-xs text-red-400">{{ error }}</p>

      <div
        v-if="displayMode !== 'none'"
        class="relative min-h-0 w-full flex-1 rounded-lg ring-1 ring-inset ring-border"
        @mousemove="onNodePointerMove"
      >
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
          class="pointer-events-none fixed z-[2000] max-w-[260px] rounded-md border border-border bg-surface-raised px-2.5 py-2 text-xs leading-relaxed text-ink shadow-2xl"
          :style="{ left: tooltipPos.x + 12 + 'px', top: tooltipPos.y + 12 + 'px' }"
        >
          <div class="mb-0.5 text-[10px] uppercase tracking-wide text-ink-faint">{{ hoveredNode.type }}</div>
          <div class="font-semibold text-ink">{{ hoveredNode.name }}</div>
          <div v-if="hoveredNode.detail" class="mt-0.5 text-ink-muted">{{ hoveredNode.detail }}</div>
        </div>
      </div>
    </div>
  </section>
</template>
