<script setup>
import { ref } from 'vue'
import { apiFetch, setToken } from '../utils/api'

const password = ref('')
const error = ref('')
const isSubmitting = ref(false)

async function submit() {
  isSubmitting.value = true
  error.value = ''
  try {
    const response = await apiFetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value }),
    })
    if (!response.ok) {
      error.value = '비밀번호가 올바르지 않습니다.'
      return
    }
    const data = await response.json()
    setToken(data.token)
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="flex h-screen w-screen items-center justify-center bg-canvas">
    <form
      class="flex w-80 flex-col gap-3 rounded-lg border border-border bg-surface-raised p-6"
      @submit.prevent="submit"
    >
      <h1 class="text-sm font-semibold text-ink">Ontology Builder</h1>
      <input
        v-model="password"
        type="password"
        placeholder="비밀번호"
        autofocus
        class="field"
      />
      <button type="submit" class="btn-primary" :disabled="isSubmitting">
        {{ isSubmitting ? '확인 중...' : '입장' }}
      </button>
      <p v-if="error" class="text-xs text-red-400">{{ error }}</p>
    </form>
  </div>
</template>
