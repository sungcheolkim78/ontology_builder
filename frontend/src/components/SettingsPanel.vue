<script setup>
import { onMounted, ref, watch } from 'vue'

const props = defineProps({
  selectedFilename: { type: String, default: null },
  availableTypes: { type: Array, default: () => [] },
  schemaVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['file-selected', 'filters-changed', 'schema-used', 'hops-changed'])

const model = ref('로딩 중...')
const isUploading = ref(false)
const uploadError = ref('')
const files = ref([])
const schemas = ref([])
const isUsingSchema = ref(false)
const schemaUseError = ref('')
const enabledTypes = ref(new Set(props.availableTypes))
const graphRagHops = ref(1)

function onHopsInput(event) {
  const value = Math.max(1, Math.min(5, Number(event.target.value) || 1))
  graphRagHops.value = value
  emit('hops-changed', value)
}

watch(
  () => props.availableTypes,
  (types) => {
    enabledTypes.value = new Set(types)
    emit('filters-changed', enabledTypes.value)
  }
)

async function loadSchemas() {
  try {
    const res = await fetch('/api/ontology/schemas')
    const data = await res.json()
    schemas.value = data.schemas
  } catch (err) {
    // schema list is best-effort; leave as-is on failure
  }
}

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

  await loadSchemas()
})

watch(() => props.schemaVersion, loadSchemas)

function selectFile(filename) {
  emit('file-selected', { filename, path: `data/${filename}` })
}

async function useSchema(sourceStem) {
  if (!props.selectedFilename) return
  isUsingSchema.value = true
  schemaUseError.value = ''
  try {
    const res = await fetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/use`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_stem: sourceStem }),
      }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    emit('schema-used')
  } catch (err) {
    schemaUseError.value = '스키마 적용 실패: ' + err.message
  } finally {
    isUsingSchema.value = false
  }
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
      <h3>스키마 라이브러리</h3>
      <p v-if="schemas.length === 0" class="placeholder">생성된 스키마가 없습니다</p>
      <ul v-else class="file-list">
        <li
          v-for="s in schemas"
          :key="s.stem"
          class="file-item"
          :class="{ disabled: isUsingSchema || !selectedFilename }"
          @click="useSchema(s.stem)"
        >
          {{ s.stem }}
        </li>
      </ul>
      <p v-if="isUsingSchema">적용 중...</p>
      <p v-if="schemaUseError" class="error">{{ schemaUseError }}</p>
    </section>

    <section>
      <h3>GraphRAG 설정</h3>
      <label class="hops-label">
        검색 hop 수
        <input
          type="number"
          min="1"
          max="5"
          :value="graphRagHops"
          @change="onHopsInput"
          class="hops-input"
        />
      </label>
    </section>

    <section>
      <h3>그래프 노드 필터</h3>
      <p v-if="availableTypes.length === 0" class="placeholder">
        아직 추출된 그래프가 없습니다
      </p>
      <label v-for="type in availableTypes" :key="type" class="filter-item">
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
.file-item.disabled {
  pointer-events: none;
  opacity: 0.5;
}
.hops-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}
.hops-input {
  width: 4rem;
  padding: 0.25rem;
}
</style>
