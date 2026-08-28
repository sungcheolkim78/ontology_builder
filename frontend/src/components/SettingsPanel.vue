<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
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
  'schema-generated',
  'graph-extracted',
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
const maxSchemaChars = ref(300000)
const renderMarkdown = ref(true)
const showFileExplorer = ref(false)
const showConfigurations = ref(false)
const schemaVersions = ref([])
const versionActionError = ref('')

const currentFile = computed(() => files.value.find((f) => f.filename === props.selectedFilename))

const schemaDocumentType = ref('general')
const isGeneratingSchema = ref(false)
const isExtracting = ref(false)
const isEmbedding = ref(false)
const workflowMessage = ref('')
const workflowError = ref('')
const elapsedSeconds = ref(0)
let elapsedTimer = null

function startElapsedTimer() {
  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value += 1
  }, 1000)
}

function stopElapsedTimer() {
  clearInterval(elapsedTimer)
  elapsedTimer = null
}

const workflowProgress = computed(() => {
  if (isGeneratingSchema.value) return `문서를 읽어 스키마 생성 중... ${elapsedSeconds.value}초`
  if (isExtracting.value) return `문서를 읽고 주어진 스키마로 노드와 에지를 생성 중... ${elapsedSeconds.value}초`
  if (isEmbedding.value) return `노드 임베딩 생성 중... ${elapsedSeconds.value}초`
  return ''
})

onUnmounted(() => stopElapsedTimer())

async function generateSchema() {
  if (!props.selectedFilename) return
  isGeneratingSchema.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  startElapsedTimer()
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        document_type: schemaDocumentType.value,
        max_chars: maxSchemaChars.value,
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const schema = await res.json()
    workflowMessage.value = `스키마 생성 완료 (노드 타입 ${schema.node_types.length}개, 엣지 타입 ${schema.edge_types.length}개)`
    emit('schema-generated')
  } catch (err) {
    workflowError.value = '스키마 생성 실패: ' + err.message
  } finally {
    isGeneratingSchema.value = false
    stopElapsedTimer()
  }
}

async function extractGraph() {
  if (!props.selectedFilename) return
  isExtracting.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  startElapsedTimer()
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/extract`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const graph = await res.json()
    workflowMessage.value = `그래프 추출 완료 (노드 ${graph.nodes.length}개, 엣지 ${graph.edges.length}개)`
    emit('graph-extracted')
  } catch (err) {
    workflowError.value = '그래프 추출 실패: ' + err.message
  } finally {
    isExtracting.value = false
    stopElapsedTimer()
  }
}

async function embed() {
  if (!props.selectedFilename) return
  isEmbedding.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  startElapsedTimer()
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/embed`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const result = await res.json()
    workflowMessage.value = `임베딩 생성 완료 (노드 ${result.embedded}개)`
  } catch (err) {
    workflowError.value = '임베딩 생성 실패: ' + err.message
  } finally {
    isEmbedding.value = false
    stopElapsedTimer()
  }
}

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

function onMaxSchemaCharsInput(event) {
  maxSchemaChars.value = Math.max(1, Number(event.target.value) || 300000)
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

watch(() => props.selectedFilename, loadSchemaVersions)
watch(() => props.schemaVersion, loadSchemaVersions)

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

async function loadSchemaVersions() {
  if (!props.selectedFilename) {
    schemaVersions.value = []
    return
  }
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/versions`
    )
    const data = await res.json()
    schemaVersions.value = data.versions
  } catch (err) {
    // version list is best-effort; leave as-is on failure
  }
}

const activeVersionLabel = computed(() => {
  const active = schemaVersions.value.find((v) => v.is_active)
  return active ? `v${active.version} 활성` : ''
})

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
  await loadSchemaVersions()
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

async function activateVersion(version) {
  versionActionError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/versions/${version}/activate`,
      { method: 'POST' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadSchemaVersions()
    emit('graph-extracted')
  } catch (err) {
    versionActionError.value = '버전 활성화 실패: ' + err.message
  }
}

async function deleteVersion(version) {
  const confirmed = window.confirm(`v${version} 스키마와 그 그래프 데이터를 삭제하시겠습니까?`)
  if (!confirmed) return

  versionActionError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/versions/${version}`,
      { method: 'DELETE' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadSchemaVersions()
    emit('graph-extracted')
  } catch (err) {
    versionActionError.value = '버전 삭제 실패: ' + err.message
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
  <aside class="flex w-[270px] shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white">
    <h1 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">
      Ontology Builder
    </h1>
    <div class="flex-1 overflow-y-auto p-3">
      <div class="mb-3 border-b border-slate-200 pb-2">
        <h2 class="mb-1.5 text-sm font-semibold text-slate-900">워크플로우</h2>
        <section class="mb-2.5">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            @click="showFileExplorer = true"
          >
            1 원본 파일 선택
          </button>
        </section>

        <section class="mb-2.5">
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
              :disabled="!selectedFilename || isGeneratingSchema"
              @click="generateSchema"
            >
              {{ isGeneratingSchema ? '생성 중...' : '2 스키마 생성' }}
            </button>
            <select
              v-model="schemaDocumentType"
              :disabled="isGeneratingSchema"
              class="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="general">일반 문서</option>
              <option value="legal">법률·보험 문서</option>
            </select>
          </div>
          <p v-if="activeVersionLabel" class="mt-1 text-xs text-slate-500">{{ activeVersionLabel }}</p>
        </section>

        <section class="mb-2.5">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
            :disabled="!selectedFilename || isExtracting"
            @click="extractGraph"
          >
            {{ isExtracting ? '추출 중...' : '3 그래프 추출' }}
          </button>
        </section>

        <section class="mb-2.5">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
            :disabled="!selectedFilename || isEmbedding || !currentFile?.has_graph"
            @click="embed"
          >
            {{ isEmbedding ? '임베딩 생성 중...' : '4 임베딩 생성' }}
          </button>
        </section>

        <p v-if="workflowProgress" class="text-sm italic text-slate-500">{{ workflowProgress }}</p>
        <p v-if="workflowMessage" class="text-sm text-green-700">{{ workflowMessage }}</p>
        <p v-if="workflowError" class="text-sm text-red-600">{{ workflowError }}</p>
      </div>

      <div class="mb-3 border-b border-slate-200 pb-2">
        <h2 class="mb-1.5 text-sm font-semibold text-slate-900">실행 설정</h2>
        <section>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            @click="showConfigurations = true"
          >
            Configurations
          </button>
        </section>
      </div>

      <div>
        <h2 class="mb-1.5 text-sm font-semibold text-slate-900">온톨로지 설정</h2>
        <section class="mb-2.5">
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">그래프 노드 필터</h3>
          <p v-if="availableTypes.length === 0" class="text-sm text-slate-500">
            아직 추출된 그래프가 없습니다
          </p>
          <label v-for="type in availableTypes" :key="type" class="mb-1 flex items-center justify-between gap-2">
            <span class="flex items-center gap-1.5 [overflow-wrap:anywhere]">
              <input
                type="checkbox"
                class="accent-indigo-600"
                :checked="enabledTypes.has(type)"
                @change="toggleType(type)"
              />
              {{ type }}
            </span>
            <span class="h-3 w-3 shrink-0 rounded-full" :style="{ background: colorForType(type) }"></span>
          </label>
        </section>

        <section>
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">그래프 엣지 필터</h3>
          <p v-if="availableEdgeTypes.length === 0" class="text-sm text-slate-500">
            아직 추출된 그래프가 없습니다
          </p>
          <label v-for="type in availableEdgeTypes" :key="type" class="mb-1 flex items-center justify-between gap-2">
            <span class="flex items-center gap-1.5 [overflow-wrap:anywhere]">
              <input
                type="checkbox"
                class="accent-indigo-600"
                :checked="enabledEdgeTypes.has(type)"
                @change="toggleEdgeType(type)"
              />
              {{ type }}
            </span>
            <span class="h-[5px] w-5 shrink-0 rounded" :style="{ background: colorForEdgeType(type) }"></span>
          </label>
        </section>
      </div>
    </div>

    <div v-if="showFileExplorer" class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40">
      <div class="flex max-h-[80vh] w-[480px] max-w-[90vw] flex-col overflow-hidden rounded-lg bg-white shadow-lg">
        <div class="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 class="text-base font-semibold text-slate-900">File Explorer</h2>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            @click="showFileExplorer = false"
          >닫기</button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">문서 업로드</h3>
            <input
              type="file"
              @change="handleFileChange"
              :disabled="isUploading"
              class="text-sm text-slate-600 file:mr-2 file:rounded-md file:border-0 file:bg-slate-100 file:px-2 file:py-1 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
            />
            <p v-if="isUploading" class="mt-1 text-sm text-slate-500">업로드 중...</p>
            <p v-if="uploadError" class="mt-1 text-sm text-red-600">{{ uploadError }}</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">업로드된 문서</h3>
            <p v-if="files.length === 0" class="text-sm text-slate-500">문서가 없습니다</p>
            <ul v-else class="m-0 list-none p-0">
              <li
                v-for="f in files"
                :key="f.filename"
                class="cursor-pointer rounded-md px-2 py-1.5 text-sm [overflow-wrap:anywhere]"
                :class="f.filename === selectedFilename ? 'bg-indigo-50 font-semibold' : 'hover:bg-slate-50'"
                @click="selectFile(f.filename)"
              >
                <div class="[overflow-wrap:anywhere]">{{ f.original_filename }}</div>
                <div class="mt-1 flex gap-1.5">
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                    :class="f.has_schema ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'"
                  >스키마</span>
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                    :class="f.has_graph ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'"
                    :title="f.has_graph ? `그래프DB: ${f.graphdb_name}` : ''"
                  >그래프</span>
                </div>
              </li>
            </ul>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">선택된 문서의 스키마 버전</h3>
            <p v-if="!selectedFilename" class="text-sm text-slate-500">문서를 먼저 선택하세요</p>
            <p v-else-if="schemaVersions.length === 0" class="text-sm text-slate-500">생성된 버전이 없습니다</p>
            <ul v-else class="m-0 list-none p-0">
              <li
                v-for="v in schemaVersions"
                :key="v.version"
                class="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm"
                :class="{ 'bg-indigo-50': v.is_active }"
              >
                <div class="flex items-center gap-1.5 [overflow-wrap:anywhere]">
                  <span class="font-semibold text-slate-900">v{{ v.version }} · {{ v.document_type }}</span>
                  <span
                    v-if="v.is_active"
                    class="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-700"
                  >활성</span>
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                    :class="v.has_graph ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'"
                  >그래프</span>
                </div>
                <div class="flex shrink-0 gap-1">
                  <button
                    v-if="!v.is_active"
                    type="button"
                    class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    @click="activateVersion(v.version)"
                  >활성화</button>
                  <button
                    type="button"
                    class="inline-flex items-center justify-center rounded-md border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                    @click="deleteVersion(v.version)"
                  >삭제</button>
                </div>
              </li>
            </ul>
            <p v-if="versionActionError" class="mt-1 text-sm text-red-600">{{ versionActionError }}</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">스키마 라이브러리</h3>
            <p v-if="schemas.length === 0" class="text-sm text-slate-500">생성된 스키마가 없습니다</p>
            <ul v-else class="m-0 list-none p-0">
              <li
                v-for="s in schemas"
                :key="s.stem"
                class="cursor-pointer rounded-md px-2 py-1.5 text-sm [overflow-wrap:anywhere] hover:bg-slate-50"
                :class="{ 'pointer-events-none opacity-50': isUsingSchema || !selectedFilename }"
                @click="useSchema(s.stem)"
              >
                {{ s.stem }}
              </li>
            </ul>
            <p v-if="isUsingSchema" class="mt-1 text-sm text-slate-500">적용 중...</p>
            <p v-if="schemaUseError" class="mt-1 text-sm text-red-600">{{ schemaUseError }}</p>
          </section>
        </div>
      </div>
    </div>

    <div v-if="showConfigurations" class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40">
      <div class="flex max-h-[80vh] w-[480px] max-w-[90vw] flex-col overflow-hidden rounded-lg bg-white shadow-lg">
        <div class="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 class="text-base font-semibold text-slate-900">Configurations</h2>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            @click="showConfigurations = false"
          >닫기</button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">LLM 모델</h3>
            <p class="inline-block rounded-md bg-slate-100 px-2 py-1 font-mono text-sm text-slate-700">{{ model }}</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">채팅 표시 설정</h3>
            <label class="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                class="accent-indigo-600"
                :checked="renderMarkdown"
                @change="onMarkdownToggle"
              />
              마크다운 HTML로 렌더링
            </label>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">GraphRAG 설정</h3>
            <label class="flex items-center gap-2 text-sm text-slate-700">
              검색 hop 수
              <input
                type="number"
                min="1"
                max="5"
                :value="graphRagHops"
                @change="onHopsInput"
                class="w-16 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </label>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">스키마 생성 설정</h3>
            <label class="flex items-center gap-2 text-sm text-slate-700">
              최고 문자수
              <input
                type="number"
                min="1"
                step="1000"
                :value="maxSchemaChars"
                @change="onMaxSchemaCharsInput"
                class="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </label>
            <p class="mt-1 text-xs text-slate-500">이 값을 넘는 문서는 스키마 생성 시 오류가 발생합니다. 필요시 늘리세요.</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">데이터베이스 관리</h3>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-white"
              :disabled="isResettingDb"
              @click="resetDatabase"
            >
              {{ isResettingDb ? '초기화 중...' : 'LadybugDB 초기화' }}
            </button>
            <p class="mt-1 text-xs text-slate-500">WAL 파일 손상 등으로 그래프 조회가 계속 실패할 때 사용하세요. 모든 문서의 추출된 그래프가 삭제됩니다.</p>
            <p v-if="resetDbError" class="mt-1 text-sm text-red-600">{{ resetDbError }}</p>
          </section>
        </div>
      </div>
    </div>
  </aside>
</template>
