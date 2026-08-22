<script setup>
import { onMounted, ref } from 'vue'

const props = defineProps({
  selectedFilename: { type: String, default: null },
})
const emit = defineEmits(['file-selected', 'filters-changed'])

const model = ref('로딩 중...')
const isUploading = ref(false)
const uploadError = ref('')
const files = ref([])
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

  try {
    const res = await fetch('/api/files')
    const data = await res.json()
    files.value = data.files
  } catch (err) {
    // file list is best-effort; leave empty on failure
  }
})

function selectFile(filename) {
  emit('file-selected', { filename, path: `data/${filename}` })
}

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
    files.value = [
      { filename: data.filename },
      ...files.value.filter((f) => f.filename !== data.filename),
    ]
    emit('file-selected', data)
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
      <h3>업로드된 문서</h3>
      <p v-if="files.length === 0" class="placeholder">문서가 없습니다</p>
      <ul v-else class="file-list">
        <li
          v-for="f in files"
          :key="f.filename"
          class="file-item"
          :class="{ selected: f.filename === selectedFilename }"
          @click="selectFile(f.filename)"
        >
          {{ f.filename }}
        </li>
      </ul>
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
.placeholder {
  color: #888;
  font-size: 0.9rem;
}
.file-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.file-item {
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.9rem;
  overflow-wrap: anywhere;
}
.file-item:hover {
  background: #f0f0f0;
}
.file-item.selected {
  background: #dbe9ff;
  font-weight: bold;
}
</style>
