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
    <div class="panel-header">
      <span>Chat</span>
    </div>

    <div class="flex min-h-0 flex-1 flex-col gap-3 p-3">
      <div class="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto rounded-lg border border-border bg-surface-sunken p-3">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="max-w-[92%] rounded-lg px-3 py-2 text-[13px] leading-relaxed"
          :class="msg.role === 'user'
            ? 'self-end bg-accent/20 text-ink'
            : 'self-start bg-surface-raised text-ink border border-border'"
        >
          <div class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
            {{ msg.role === 'user' ? '나' : '챗봇' }}
          </div>
          <div
            v-if="(msg.nodeTypes && msg.nodeTypes.length) || (msg.edgeTypes && msg.edgeTypes.length)"
            class="mb-2 space-y-1 border-b border-dashed border-border pb-2"
          >
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-[10px] text-ink-faint">노드:</span>
              <template v-if="msg.nodeTypes.length">
                <button
                  v-for="type in msg.nodeTypes"
                  :key="'n-' + type"
                  type="button"
                  class="chip border-emerald-500/50 text-emerald-400 hover:bg-emerald-500 hover:text-white"
                  :class="{ 'opacity-40 line-through': !enabledTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'node', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="text-[10px] text-ink-faint">없음</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-[10px] text-ink-faint">엣지:</span>
              <template v-if="msg.edgeTypes.length">
                <button
                  v-for="type in msg.edgeTypes"
                  :key="'e-' + type"
                  type="button"
                  class="chip border-amber-500/50 text-amber-400 hover:bg-amber-500 hover:text-white"
                  :class="{ 'opacity-40 line-through': !enabledEdgeTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'edge', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="text-[10px] text-ink-faint">없음</span>
            </div>
          </div>
          <div v-if="renderMarkdown" class="markdown" v-html="marked.parse(msg.content)"></div>
          <p v-else class="whitespace-pre-wrap">{{ msg.content }}</p>
          <div v-if="msg.relatedNodes && msg.relatedNodes.length" class="mt-2 flex flex-wrap items-center gap-1.5">
            <span class="text-[10px] text-ink-faint">관련 노드:</span>
            <button
              v-for="node in msg.relatedNodes"
              :key="node.id"
              type="button"
              class="chip border-[color:var(--chip-color)] text-[color:var(--chip-color)] hover:bg-[color:var(--chip-color)] hover:text-white"
              :style="{ '--chip-color': colorForNodeType(node.type, availableTypes) }"
              @click="emit('highlight-nodes', [node.id])"
            >
              {{ node.label }}
            </button>
          </div>
        </div>
        <p v-if="isLoading" class="text-xs italic text-ink-faint">응답 중... (ESC로 취소)</p>
        <p v-if="error" class="text-xs text-red-400">{{ error }}</p>
      </div>

      <form class="flex flex-shrink-0 gap-2" @submit.prevent="sendMessage">
        <input
          v-model="input"
          type="text"
          placeholder="메시지를 입력하세요"
          class="field flex-1 py-2"
        />
        <button type="submit" class="btn-primary px-4" :disabled="isLoading">전송</button>
      </form>
    </div>
  </section>
</template>

<style scoped>
.markdown :deep(p) {
  margin: 0.25rem 0;
}
.markdown :deep(table) {
  border-collapse: collapse;
  margin: 0.25rem 0;
}
.markdown :deep(td),
.markdown :deep(th) {
  border: 1px solid theme('colors.border.DEFAULT');
  padding: 0.25rem 0.5rem;
}
.markdown :deep(code) {
  background: rgba(255, 255, 255, 0.08);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-size: 0.85em;
}
.markdown :deep(pre) {
  background: rgba(255, 255, 255, 0.05);
  padding: 0.5rem;
  border-radius: 6px;
  overflow-x: auto;
}
.markdown :deep(a) {
  color: theme('colors.accent.DEFAULT');
}
</style>
