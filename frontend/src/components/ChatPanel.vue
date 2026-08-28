<script setup>
import { marked } from 'marked'
import markedKatex from 'marked-katex-extension'
import 'katex/dist/katex.min.css'
import { onMounted, onUnmounted, ref } from 'vue'
import { colorForNodeType } from '../utils/nodeColors.js'
import { apiFetch } from '../utils/api.js'

marked.use(markedKatex({ throwOnError: false }))

const props = defineProps({
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
  renderMarkdown: { type: Boolean, default: true },
  enabledTypes: { type: Set, default: () => new Set() },
  enabledEdgeTypes: { type: Set, default: () => new Set() },
  // Same sorted type list OntologyGraph.vue uses for its own node colors
  // (see nodeColors.js) -- needed here so a related-node chip's color
  // matches that node's color in the graph view.
  availableTypes: { type: Array, default: () => [] },
})

// related_nodes already comes back relevance-ordered from the backend
// (query-matched nodes before hop-expanded neighbors -- see
// graphrag.search_graph); this only reorders by whether the node's type is
// one the question was actually determined to be about, keeping that
// backend order as the tie-break within each tier (Array#sort is stable).
function sortRelatedNodes(relatedNodes, nodeTypes) {
  const relevantTypes = new Set(nodeTypes ?? [])
  return [...relatedNodes].sort((a, b) => {
    const aRank = relevantTypes.has(a.type) ? 0 : 1
    const bRank = relevantTypes.has(b.type) ? 0 : 1
    return aRank - bRank
  })
}

const emit = defineEmits(['highlight-nodes', 'toggle-type'])

const messages = ref([])
const input = ref('')
const isLoading = ref(false)
const error = ref('')

let abortController = null

function handleKeydown(e) {
  if (e.key === 'Escape' && isLoading.value && abortController) {
    abortController.abort()
  }
}

onMounted(() => window.addEventListener('keydown', handleKeydown))
onUnmounted(() => window.removeEventListener('keydown', handleKeydown))

async function sendMessage() {
  const content = input.value.trim()
  if (!content || isLoading.value) return

  messages.value.push({ role: 'user', content })
  input.value = ''
  error.value = ''
  isLoading.value = true
  abortController = new AbortController()

  try {
    const res = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: messages.value.map(({ role, content }) => ({ role, content })),
        filename: props.file?.filename ?? null,
        hops: props.hops,
      }),
      signal: abortController.signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    messages.value.push({
      role: data.role,
      content: data.content,
      nodeTypes: data.node_types ?? [],
      edgeTypes: data.edge_types ?? [],
      relatedNodes: sortRelatedNodes(data.related_nodes ?? [], data.node_types),
    })
  } catch (err) {
    if (err.name === 'AbortError') {
      error.value = '요청이 취소되었습니다.'
    } else {
      error.value = '메시지 전송 실패: ' + err.message
    }
  } finally {
    isLoading.value = false
    abortController = null
  }
}
</script>

<template>
  <section class="flex h-full min-w-0 flex-col">
    <h2 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">Chat</h2>

    <div class="flex-1 min-h-0 flex flex-col p-4">
      <div class="mb-4 flex-1 overflow-y-auto rounded-lg border border-slate-200 p-4">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="message mb-3 rounded-lg px-3 py-2"
          :class="msg.role === 'user' ? 'bg-indigo-100 text-right' : 'bg-slate-100'"
        >
          <strong>{{ msg.role === 'user' ? '나' : '챗봇' }}</strong>
          <div
            v-if="(msg.nodeTypes && msg.nodeTypes.length) || (msg.edgeTypes && msg.edgeTypes.length)"
            class="mt-1 mb-4 border-b border-dashed border-slate-300 pb-2"
          >
            <div class="mb-1 flex flex-wrap items-center gap-1.5">
              <span class="text-xs text-slate-500">노드:</span>
              <template v-if="msg.nodeTypes.length">
                <button
                  v-for="type in msg.nodeTypes"
                  :key="'n-' + type"
                  type="button"
                  class="cursor-pointer rounded-full border border-emerald-600 bg-white px-2.5 py-0.5 text-xs text-emerald-700 hover:bg-emerald-600 hover:text-white"
                  :class="{ 'opacity-40 line-through': !enabledTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'node', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="text-xs text-slate-400">없음</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-xs text-slate-500">엣지:</span>
              <template v-if="msg.edgeTypes.length">
                <button
                  v-for="type in msg.edgeTypes"
                  :key="'e-' + type"
                  type="button"
                  class="cursor-pointer rounded-full border border-amber-800 bg-white px-2.5 py-0.5 text-xs text-amber-800 hover:bg-amber-800 hover:text-white"
                  :class="{ 'opacity-40 line-through': !enabledEdgeTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'edge', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="text-xs text-slate-400">없음</span>
            </div>
          </div>
          <div v-if="renderMarkdown" class="markdown mt-1" v-html="marked.parse(msg.content)"></div>
          <p v-else class="mt-1 whitespace-pre-wrap">{{ msg.content }}</p>
          <div v-if="msg.relatedNodes && msg.relatedNodes.length" class="mt-2 flex flex-wrap items-center gap-1.5">
            <span class="text-xs text-slate-500">관련 노드:</span>
            <button
              v-for="node in msg.relatedNodes"
              :key="node.id"
              type="button"
              class="cursor-pointer rounded-full border border-[var(--chip-color)] bg-white px-2.5 py-0.5 text-xs text-[var(--chip-color)] hover:bg-[var(--chip-color)] hover:text-white"
              :style="{ '--chip-color': colorForNodeType(node.type, availableTypes) }"
              @click="emit('highlight-nodes', [node.id])"
            >
              {{ node.label }}
            </button>
          </div>
        </div>
        <p v-if="isLoading" class="text-sm text-slate-500">응답 중... (ESC로 취소)</p>
        <p v-if="error" class="text-sm text-red-600">{{ error }}</p>
      </div>

      <form class="flex gap-2" @submit.prevent="sendMessage">
        <input
          v-model="input"
          type="text"
          placeholder="메시지를 입력하세요"
          class="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <button
          type="submit"
          :disabled="isLoading"
          class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
        >전송</button>
      </form>
    </div>
  </section>
</template>

<style scoped>
.message .markdown :deep(p) {
  margin: 0.25rem 0;
}
.message .markdown :deep(h1) {
  font-size: 1.15rem;
  font-weight: 700;
  margin: 0.5rem 0 0.3rem;
}
.message .markdown :deep(h2) {
  font-size: 1.05rem;
  font-weight: 700;
  margin: 0.5rem 0 0.3rem;
}
.message .markdown :deep(h3) {
  font-size: 1rem;
  font-weight: 600;
  margin: 0.4rem 0 0.25rem;
}
.message .markdown :deep(h4) {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0.4rem 0 0.25rem;
}
.message .markdown :deep(ul),
.message .markdown :deep(ol) {
  list-style: revert;
  padding-left: 1.4em;
  margin: 0.25rem 0;
}
.message .markdown :deep(table) {
  border-collapse: collapse;
}
.message .markdown :deep(td),
.message .markdown :deep(th) {
  border: 1px solid #e2e8f0;
  padding: 0.25rem 0.5rem;
}
</style>
