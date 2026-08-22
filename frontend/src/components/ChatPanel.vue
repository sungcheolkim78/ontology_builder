<script setup>
import { ref } from 'vue'

const props = defineProps({
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
})

const messages = ref([])
const input = ref('')
const isLoading = ref(false)
const error = ref('')

async function sendMessage() {
  const content = input.value.trim()
  if (!content || isLoading.value) return

  messages.value.push({ role: 'user', content })
  input.value = ''
  error.value = ''
  isLoading.value = true

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: messages.value,
        filename: props.file?.filename ?? null,
        hops: props.hops,
      }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    messages.value.push({ role: data.role, content: data.content })
  } catch (err) {
    error.value = '메시지 전송 실패: ' + err.message
  } finally {
    isLoading.value = false
  }
}
</script>

<template>
  <section class="chat">
    <h2>Chat</h2>

    <div class="messages">
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message"
        :class="msg.role"
      >
        <strong>{{ msg.role === 'user' ? '나' : '챗봇' }}</strong>
        <p>{{ msg.content }}</p>
      </div>
      <p v-if="isLoading">응답 중...</p>
      <p v-if="error" class="error">{{ error }}</p>
    </div>

    <form class="input-row" @submit.prevent="sendMessage">
      <input v-model="input" type="text" placeholder="메시지를 입력하세요" />
      <button type="submit" :disabled="isLoading">전송</button>
    </form>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
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
}
.message.user {
  text-align: right;
}
.message p {
  margin: 0.25rem 0 0;
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
