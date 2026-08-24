<script setup>
import { marked } from 'marked'
import { onMounted, onUnmounted, ref } from 'vue'

const props = defineProps({
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
  renderMarkdown: { type: Boolean, default: true },
  enabledTypes: { type: Set, default: () => new Set() },
  enabledEdgeTypes: { type: Set, default: () => new Set() },
})

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
    const res = await fetch('/api/chat', {
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
      relatedNodes: data.related_nodes ?? [],
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
  <section class="chat">
    <h2 class="panel-title">Chat</h2>

    <div class="panel-body">
      <div class="messages">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="message"
          :class="msg.role"
        >
          <strong>{{ msg.role === 'user' ? '나' : '챗봇' }}</strong>
          <div
            v-if="(msg.nodeTypes && msg.nodeTypes.length) || (msg.edgeTypes && msg.edgeTypes.length)"
            class="type-analysis"
          >
            <div class="type-analysis-row">
              <span class="type-analysis-label">노드:</span>
              <template v-if="msg.nodeTypes.length">
                <button
                  v-for="type in msg.nodeTypes"
                  :key="'n-' + type"
                  type="button"
                  class="type-chip node-type"
                  :class="{ inactive: !enabledTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'node', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="type-analysis-empty">없음</span>
            </div>
            <div class="type-analysis-row">
              <span class="type-analysis-label">엣지:</span>
              <template v-if="msg.edgeTypes.length">
                <button
                  v-for="type in msg.edgeTypes"
                  :key="'e-' + type"
                  type="button"
                  class="type-chip edge-type"
                  :class="{ inactive: !enabledEdgeTypes.has(type) }"
                  @click="emit('toggle-type', { kind: 'edge', type })"
                >
                  {{ type }}
                </button>
              </template>
              <span v-else class="type-analysis-empty">없음</span>
            </div>
          </div>
          <div v-if="renderMarkdown" class="markdown" v-html="marked.parse(msg.content)"></div>
          <p v-else>{{ msg.content }}</p>
          <div v-if="msg.relatedNodes && msg.relatedNodes.length" class="related-nodes">
            <span class="related-label">관련 노드:</span>
            <button
              v-for="node in msg.relatedNodes"
              :key="node.id"
              type="button"
              class="node-chip"
              @click="emit('highlight-nodes', [node.id])"
            >
              {{ node.label }}
            </button>
          </div>
        </div>
        <p v-if="isLoading">응답 중... (ESC로 취소)</p>
        <p v-if="error" class="error">{{ error }}</p>
      </div>

      <form class="input-row" @submit.prevent="sendMessage">
        <input v-model="input" type="text" placeholder="메시지를 입력하세요" />
        <button type="submit" :disabled="isLoading">전송</button>
      </form>
    </div>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
.panel-title {
  flex-shrink: 0;
  margin: 0;
  padding: 0.6rem 1rem;
  font-size: 1rem;
  color: #fff;
  background: #2563eb;
}
.panel-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 1rem;
}
.messages {
  flex: 1;
  overflow-y: auto;
  border: 1px solid #ccc;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
}
.message {
  margin-bottom: 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
}
.message.user {
  text-align: right;
  background: #dbe9ff;
}
.message.assistant {
  background: #f0f0f0;
}
.message p {
  margin: 0.25rem 0 0;
  white-space: pre-wrap;
}
.message .markdown {
  margin: 0.25rem 0 0;
}
.message .markdown :deep(p) {
  margin: 0.25rem 0;
}
.message .markdown :deep(table) {
  border-collapse: collapse;
}
.message .markdown :deep(td),
.message .markdown :deep(th) {
  border: 1px solid #ccc;
  padding: 0.25rem 0.5rem;
}
.type-analysis {
  margin: 0.25rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px dashed #ccc;
}
.type-analysis-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 0.25rem;
}
.type-analysis-label {
  font-size: 0.75rem;
  color: #666;
}
.type-analysis-empty {
  font-size: 0.75rem;
  color: #999;
}
.type-chip {
  font-size: 0.75rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  cursor: pointer;
  background: white;
}
.type-chip.node-type {
  border: 1px solid #4fbf7a;
  color: #2f8a54;
}
.type-chip.node-type:hover {
  background: #4fbf7a;
  color: white;
}
.type-chip.edge-type {
  border: 1px solid #8a6d3b;
  color: #8a6d3b;
}
.type-chip.edge-type:hover {
  background: #8a6d3b;
  color: white;
}
.type-chip.inactive {
  opacity: 0.4;
  text-decoration: line-through;
}
.related-nodes {
  margin-top: 0.5rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
}
.related-label {
  font-size: 0.75rem;
  color: #666;
}
.node-chip {
  font-size: 0.75rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  border: 1px solid #4f8ef7;
  background: white;
  color: #4f8ef7;
  cursor: pointer;
}
.node-chip:hover {
  background: #4f8ef7;
  color: white;
}
.error {
  color: red;
}
.input-row {
  display: flex;
  gap: 0.5rem;
}
.input-row input {
  flex: 1;
  padding: 0.5rem;
}
</style>
