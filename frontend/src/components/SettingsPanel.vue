<script setup>
import { onMounted, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'

const props = defineProps({
  selectedFilename: { type: String, default: null },
  availableTypes: { type: Array, default: () => [] },
  availableEdgeTypes: { type: Array, default: () => [] },
  schemaVersion: { type: Number, default: 0 },
  toggleTypeRequest: { type: Object, default: null },
  toggleEdgeTypeRequest: { type: Object, default: null },
})
const emit = defineEmits([
  'file-selected',
  'filters-changed',
  'edge-filters-changed',
  'schema-used',
  'hops-changed',
  'markdown-changed',
  'database-reset',
])

const model = ref('로딩 중...')
const isUploading = ref(false)
const uploadError = ref('')
const files = ref([])
const schemas = ref([])
const isUsingSchema = ref(false)
const schemaUseError = ref('')
const isResettingDb = ref(false)
const resetDbError = ref('')
const enabledTypes = ref(new Set(props.availableTypes))
const enabledEdgeTypes = ref(new Set(props.availableEdgeTypes))
const graphRagHops = ref(1)
const renderMarkdown = ref(true)
const showFileExplorer = ref(false)

const TYPE_COLORS = ['#4f8ef7', '#f7a24f', '#4fbf7a', '#c96fd6', '#e0555a', '#5ac8d8']
const EDGE_TYPE_COLORS = ['#8a6d3b', '#2f9e8f', '#a05195', '#d45087', '#665191', '#2c7fb8']

function colorForType(type) {
  const index = props.availableTypes.indexOf(type)
  return TYPE_COLORS[index % TYPE_COLORS.length]
}

function colorForEdgeType(type) {
  const index = props.availableEdgeTypes.indexOf(type)
  return EDGE_TYPE_COLORS[index % EDGE_TYPE_COLORS.length]
}

function onHopsInput(event) {
  const value = Math.max(1, Math.min(5, Number(event.target.value) || 1))
  graphRagHops.value = value
  emit('hops-changed', value)
}

function onMarkdownToggle(event) {
  renderMarkdown.value = event.target.checked
  emit('markdown-changed', renderMarkdown.value)
}

watch(
  () => props.availableTypes,
  (types) => {
    enabledTypes.value = new Set(types)
    emit('filters-changed', enabledTypes.value)
  }
)

watch(
  () => props.availableEdgeTypes,
  (types) => {
    enabledEdgeTypes.value = new Set(types)
    emit('edge-filters-changed', enabledEdgeTypes.value)
  }
)

watch(
  () => props.toggleTypeRequest,
  (req) => {
    if (req) toggleType(req.type)
  }
)

watch(
  () => props.toggleEdgeTypeRequest,
  (req) => {
    if (req) toggleEdgeType(req.type)
  }
)

async function loadSchemas() {
  try {
    const res = await apiFetch('/api/ontology/schemas')
    const data = await res.json()
    schemas.value = data.schemas
  } catch (err) {
    // schema list is best-effort; leave as-is on failure
  }
}

async function loadDocuments() {
  try {
    const res = await apiFetch('/api/documents')
    const data = await res.json()
    files.value = data.documents
  } catch (err) {
    // document list is best-effort; leave as-is on failure
  }
}

onMounted(async () => {
  try {
    const res = await apiFetch('/api/config')
    const data = await res.json()
    model.value = data.model
  } catch (err) {
    model.value = '알 수 없음'
  }

  await loadDocuments()
  await loadSchemas()
})

watch(() => props.schemaVersion, () => {
  loadSchemas()
  loadDocuments()
})

function selectFile(filename) {
  emit('file-selected', { filename, path: `data/${filename}` })
}

async function useSchema(sourceStem) {
  if (!props.selectedFilename) return
  isUsingSchema.value = true
  schemaUseError.value = ''
  try {
    const res = await apiFetch(
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
    const res = await apiFetch('/api/parse', { method: 'POST', body: formData })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    await loadDocuments()
    emit('file-selected', data)
  } catch (err) {
    uploadError.value = '업로드 실패: ' + err.message
  } finally {
    isUploading.value = false
    event.target.value = ''
  }
}

async function resetDatabase() {
  const confirmed = window.confirm(
    '그래프 데이터베이스를 초기화하면 지금까지 추출된 모든 문서의 그래프(노드/엣지)가 삭제됩니다. ' +
      '문서와 스키마는 남아있어 재추출은 가능합니다. 계속하시겠습니까?'
  )
  if (!confirmed) return

  isResettingDb.value = true
  resetDbError.value = ''
  try {
    const res = await apiFetch('/api/ontology/reset-database', { method: 'POST' })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    emit('database-reset')
  } catch (err) {
    resetDbError.value = '초기화 실패: ' + err.message
  } finally {
    isResettingDb.value = false
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

function toggleEdgeType(type) {
  const next = new Set(enabledEdgeTypes.value)
  if (next.has(type)) {
    next.delete(type)
  } else {
    next.add(type)
  }
  enabledEdgeTypes.value = next
  emit('edge-filters-changed', next)
}
</script>

<template>
  <aside class="settings">
    <h1 class="panel-title">Ontology Builder</h1>
    <div class="panel-body">
    <div class="settings-group">
      <h2 class="group-title">파일 설정</h2>
      <section>
        <button type="button" class="explorer-button" @click="showFileExplorer = true">
          파일 설정
        </button>
        <p class="hint">문서를 업로드하고, 업로드된 문서를 선택하거나 스키마 라이브러리를 적용할 수 있습니다.</p>
      </section>
    </div>

    <div class="settings-group">
      <h2 class="group-title">실행 설정</h2>
      <section>
        <h3>LLM 모델</h3>
        <p class="model-name">{{ model }}</p>
      </section>

      <section>
        <h3>채팅 표시 설정</h3>
        <label class="markdown-label">
          <input
            type="checkbox"
            :checked="renderMarkdown"
            @change="onMarkdownToggle"
          />
          마크다운 HTML로 렌더링
        </label>
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
        <h3>데이터베이스 관리</h3>
        <button
          type="button"
          class="danger-button"
          :disabled="isResettingDb"
          @click="resetDatabase"
        >
          {{ isResettingDb ? '초기화 중...' : 'LadybugDB 초기화' }}
        </button>
        <p class="hint">WAL 파일 손상 등으로 그래프 조회가 계속 실패할 때 사용하세요. 모든 문서의 추출된 그래프가 삭제됩니다.</p>
        <p v-if="resetDbError" class="error">{{ resetDbError }}</p>
      </section>
    </div>

    <div class="settings-group">
      <h2 class="group-title">온톨로지 설정</h2>
      <section>
        <h3>그래프 노드 필터</h3>
        <p v-if="availableTypes.length === 0" class="placeholder">
          아직 추출된 그래프가 없습니다
        </p>
        <label v-for="type in availableTypes" :key="type" class="filter-item">
          <span class="filter-item-left">
            <input
              type="checkbox"
              :checked="enabledTypes.has(type)"
              @change="toggleType(type)"
            />
            {{ type }}
          </span>
          <span class="type-swatch" :style="{ background: colorForType(type) }"></span>
        </label>
      </section>

      <section>
        <h3>그래프 엣지 필터</h3>
        <p v-if="availableEdgeTypes.length === 0" class="placeholder">
          아직 추출된 그래프가 없습니다
        </p>
        <label v-for="type in availableEdgeTypes" :key="type" class="filter-item">
          <span class="filter-item-left">
            <input
              type="checkbox"
              :checked="enabledEdgeTypes.has(type)"
              @change="toggleEdgeType(type)"
            />
            {{ type }}
          </span>
          <span class="edge-swatch" :style="{ background: colorForEdgeType(type) }"></span>
        </label>
      </section>
    </div>
    </div>

    <div v-if="showFileExplorer" class="file-explorer-overlay">
      <div class="file-explorer-window">
        <div class="file-explorer-header">
          <h2>File Explorer</h2>
          <button type="button" class="close-button" @click="showFileExplorer = false">닫기</button>
        </div>
        <div class="file-explorer-body">
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
                <div class="file-item-name">{{ f.original_filename }}</div>
                <div class="file-item-meta">
                  <span
                    class="status-badge"
                    :class="{ on: f.has_schema }"
                  >스키마</span>
                  <span
                    class="status-badge"
                    :class="{ on: f.has_graph }"
                    :title="f.has_graph ? `그래프DB: ${f.graphdb_name}` : ''"
                  >그래프</span>
                </div>
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
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.settings {
  width: 270px;
  flex-shrink: 0;
  border-right: 1px solid #ccc;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.panel-title {
  flex-shrink: 0;
  margin: 0;
  padding: 0.6rem 1rem;
  font-size: 1rem;
  color: #fff;
  background: #1f2937;
}
.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.6rem 0.75rem;
}
.settings section {
  margin-bottom: 0.6rem;
}
.settings-group {
  margin-bottom: 0.6rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #e0e0e0;
}
.settings-group:last-child {
  border-bottom: none;
}
.group-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: #1f2937;
  margin: 0 0 0.35rem;
}
.settings h3 {
  font-size: 0.8rem;
  text-transform: uppercase;
  color: #666;
  margin: 0 0 0.25rem;
}
.settings p {
  margin: 0.25rem 0 0;
}
.model-name {
  font-family: monospace;
  background: #f0f0f0;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  display: inline-block;
}
.filter-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.15rem;
}
.filter-item-left {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  overflow-wrap: anywhere;
}
.type-swatch {
  flex-shrink: 0;
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
}
.edge-swatch {
  flex-shrink: 0;
  display: inline-block;
  width: 20px;
  height: 5px;
  border-radius: 2px;
}
.error {
  color: red;
}
.placeholder {
  color: #888;
  font-size: 0.9rem;
}
.hint {
  color: #888;
  font-size: 0.8rem;
  margin-top: 0.25rem;
}
.danger-button {
  padding: 0.4rem 0.75rem;
  border: 1px solid #c0392b;
  border-radius: 4px;
  background: #fff;
  color: #c0392b;
  font-size: 0.85rem;
  cursor: pointer;
}
.danger-button:hover:not(:disabled) {
  background: #fdecea;
}
.danger-button:disabled {
  opacity: 0.6;
  cursor: default;
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
.file-item-name {
  overflow-wrap: anywhere;
}
.file-item-meta {
  display: flex;
  gap: 0.35rem;
  margin-top: 0.25rem;
}
.status-badge {
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  background: #eee;
  color: #999;
}
.status-badge.on {
  background: #dff0d8;
  color: #3c763d;
}
.hops-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}
.markdown-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}
.hops-input {
  width: 4rem;
  padding: 0.25rem;
}
.explorer-button {
  padding: 0.4rem 0.75rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #f0f0f0;
  color: #333;
  font-size: 0.9rem;
  cursor: pointer;
}
.explorer-button:hover {
  background: #e4e4e4;
}
.file-explorer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.file-explorer-window {
  width: 480px;
  max-width: 90vw;
  max-height: 80vh;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.file-explorer-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  background: #1f2937;
  color: #fff;
}
.file-explorer-header h2 {
  margin: 0;
  font-size: 1rem;
}
.close-button {
  padding: 0.3rem 0.75rem;
  border: 1px solid #fff;
  border-radius: 4px;
  background: transparent;
  color: #fff;
  font-size: 0.85rem;
  cursor: pointer;
}
.close-button:hover {
  background: rgba(255, 255, 255, 0.15);
}
.file-explorer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}
.file-explorer-body section {
  margin-bottom: 1.5rem;
}
</style>
