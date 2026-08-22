<script setup>
import { onMounted, ref } from 'vue'

const emit = defineEmits(['file-parsed', 'filters-changed'])

const model = ref('로딩 중...')
const isUploading = ref(false)
const uploadError = ref('')
const nodeTypes = ['Person', 'Organization', 'Concept']
const enabledTypes = ref(new Set(nodeTypes))

onMounted(async () => {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    model.value = data.model
  } catch (err) {
    model.value = '알 수 없음'
  }
})

async function handleFileChange(event) {
  const file = event.target.files[0]
  if (!file) return

  isUploading.value = true
  uploadError.value = ''

  try {
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch('/api/parse', { method: 'POST', body: formData })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    emit('file-parsed', data)
  } catch (err) {
    uploadError.value = '업로드 실패: ' + err.message
  } finally {
    isUploading.value = false
    event.target.value = ''
  }
}

function toggleType(type) {
  const next = new Set(enabledTypes.value)
  if (next.has(type)) {
    next.delete(type)
  } else {
    next.add(type)
  }
  enabledTypes.value = next
  emit('filters-changed', next)
}
</script>

<template>
  <aside class="settings">
    <h2>설정</h2>

    <section>
      <h3>LLM 모델</h3>
      <p class="model-name">{{ model }}</p>
    </section>

    <section>
      <h3>문서 업로드</h3>
      <input type="file" @change="handleFileChange" :disabled="isUploading" />
      <p v-if="isUploading">업로드 중...</p>
      <p v-if="uploadError" class="error">{{ uploadError }}</p>
    </section>

    <section>
      <h3>그래프 노드 필터</h3>
      <label v-for="type in nodeTypes" :key="type" class="filter-item">
        <input
          type="checkbox"
          :checked="enabledTypes.has(type)"
          @change="toggleType(type)"
        />
        {{ type }}
      </label>
    </section>
  </aside>
</template>

<style scoped>
.settings {
  width: 280px;
  flex-shrink: 0;
  border-right: 1px solid #ccc;
  padding: 1rem;
  overflow-y: auto;
}
.settings section {
  margin-bottom: 1.5rem;
}
.settings h3 {
  font-size: 0.9rem;
  text-transform: uppercase;
  color: #666;
  margin-bottom: 0.5rem;
}
.model-name {
  font-family: monospace;
  background: #f0f0f0;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  display: inline-block;
}
.filter-item {
  display: block;
  margin-bottom: 0.25rem;
}
.error {
  color: red;
}
</style>
