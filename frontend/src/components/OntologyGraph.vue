<script setup>
import { computed } from 'vue'

const props = defineProps({
  enabledTypes: { type: Set, default: () => new Set(['Person', 'Organization', 'Concept']) },
})

const NODES = [
  { id: 'n1', label: 'Ada Lovelace', type: 'Person' },
  { id: 'n2', label: 'Charles Babbage', type: 'Person' },
  { id: 'n3', label: 'Analytical Engine', type: 'Concept' },
  { id: 'n4', label: 'Royal Society', type: 'Organization' },
  { id: 'n5', label: 'Algorithm', type: 'Concept' },
  { id: 'n6', label: 'Computing', type: 'Concept' },
]

const EDGES = [
  { source: 'n1', target: 'n3' },
  { source: 'n2', target: 'n3' },
  { source: 'n1', target: 'n2' },
  { source: 'n2', target: 'n4' },
  { source: 'n3', target: 'n5' },
  { source: 'n5', target: 'n6' },
]

const TYPE_COLORS = {
  Person: '#4f8ef7',
  Organization: '#f7a24f',
  Concept: '#4fbf7a',
}

const RADIUS = 100
const CENTER = 130

const positions = computed(() => {
  const map = {}
  NODES.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / NODES.length
    map[node.id] = {
      x: CENTER + RADIUS * Math.cos(angle),
      y: CENTER + RADIUS * Math.sin(angle),
    }
  })
  return map
})

const visibleNodes = computed(() =>
  NODES.filter((n) => props.enabledTypes.has(n.type))
)

const visibleEdges = computed(() => {
  const visibleIds = new Set(visibleNodes.value.map((n) => n.id))
  return EDGES.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
})
</script>

<template>
  <section class="graph">
    <h2>온톨로지 그래프</h2>
    <svg viewBox="0 0 260 260" class="graph-svg">
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
          :fill="TYPE_COLORS[node.type]"
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
.graph-svg {
  width: 100%;
  max-width: 320px;
}
.node-label {
  font-size: 9px;
  fill: #333;
}
</style>
