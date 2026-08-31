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
const showRunSettings = ref(false)

const currentFile = computed(() => files.value.find((f) => f.filename === props.selectedFilename))

const schemaDocumentType = ref('general')
const isGeneratingSchema = ref(false)
const isExtracting = ref(false)
const isEmbedding = ref(false)
const isValidating = ref(false)
const validationReport = ref(null)
const validationError = ref('')
const showValidationReport = ref(false)
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
  if (isValidating.value) return `문서와 스키마, 추출된 그래프를 검토하여 보고서 작성 중... ${elapsedSeconds.value}초`
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

async function validateOntology() {
  if (!props.selectedFilename) return
  isValidating.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  validationError.value = ''
  startElapsedTimer()
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_chars: maxSchemaChars.value }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    validationReport.value = await res.json()
    showValidationReport.value = true
  } catch (err) {
    validationError.value = '온톨로지 검증 실패: ' + err.message
  } finally {
    isValidating.value = false
    stopElapsedTimer()
  }
}

const SEVERITY_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
const SEVERITY_STYLES = {
  CRITICAL: 'border-red-500/50 bg-red-500/15 text-red-400',
  HIGH: 'border-orange-500/50 bg-orange-500/15 text-orange-400',
  MEDIUM: 'border-amber-500/50 bg-amber-500/15 text-amber-400',
  LOW: 'border-sky-500/50 bg-sky-500/15 text-sky-400',
  INFO: 'border-border bg-white/5 text-ink-muted',
}

function severityClass(severity) {
  return SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.INFO
}

const sortedIssues = computed(() => {
  const issues = validationReport.value?.issues ?? []
  return [...issues].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)
  )
})

const missingElementGroups = computed(() => {
  const missing = validationReport.value?.missing_elements ?? {}
  return [
    ['classes', '클래스', missing.classes],
    ['relationships', '관계', missing.relationships],
    ['attributes', '속성', missing.attributes],
    ['events', '이벤트', missing.events],
    ['rules', '규칙', missing.rules],
  ].filter(([, , items]) => Array.isArray(items) && items.length > 0)
})

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
  <aside class="flex w-[280px] flex-shrink-0 flex-col overflow-hidden border-r border-border bg-surface">
    <div class="flex-1 overflow-y-auto px-3 py-3">
      <div class="mb-4">
        <h2 class="section-label">워크플로우</h2>
        <div class="space-y-2.5">
          <div>
            <button type="button" class="btn w-full" @click="showFileExplorer = true">
              <svg class="h-3.5 w-3.5 text-ink-faint" viewBox="0 0 20 20" fill="currentColor">
                <path
                  d="M2 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6Z"
                />
              </svg>
              문서 / 스키마 선택
            </button>
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              문서를 업로드하고, 업로드된 문서를 선택하거나 스키마 라이브러리를 적용할 수 있습니다.
            </p>
          </div>

          <div class="flex items-center gap-1.5">
            <button
              type="button"
              class="btn flex-1"
              :disabled="!selectedFilename || isGeneratingSchema"
              @click="generateSchema"
            >
              {{ isGeneratingSchema ? '생성 중...' : '스키마 생성' }}
            </button>
            <select
              v-model="schemaDocumentType"
              :disabled="isGeneratingSchema"
              class="field w-[92px] flex-shrink-0"
            >
              <option value="general">일반 문서</option>
              <option value="legal">법률·보험</option>
            </select>
          </div>

          <button
            type="button"
            class="btn w-full"
            :disabled="!selectedFilename || isExtracting"
            @click="extractGraph"
          >
            {{ isExtracting ? '추출 중...' : '그래프 추출' }}
          </button>

          <button
            type="button"
            class="btn w-full"
            :disabled="!selectedFilename || isEmbedding || !currentFile?.has_graph"
            @click="embed"
          >
            {{ isEmbedding ? '임베딩 생성 중...' : '임베딩 생성' }}
          </button>

          <button
            type="button"
            class="btn w-full"
            :disabled="!selectedFilename || isValidating || !currentFile?.has_graph"
            @click="validateOntology"
          >
            {{ isValidating ? '검증 중...' : '온톨로지 검증' }}
          </button>

          <p v-if="workflowProgress" class="text-[11px] italic text-ink-muted">{{ workflowProgress }}</p>
          <p v-if="workflowMessage" class="text-[11px] text-emerald-400">{{ workflowMessage }}</p>
          <p v-if="workflowError" class="text-[11px] text-red-400">{{ workflowError }}</p>
          <p v-if="validationError" class="text-[11px] text-red-400">{{ validationError }}</p>
          <button
            v-if="validationReport && !showValidationReport"
            type="button"
            class="text-[11px] text-accent hover:underline"
            @click="showValidationReport = true"
          >
            마지막 검증 보고서 다시 보기
          </button>
        </div>
      </div>

      <div class="mb-4 border-t border-border pt-3.5">
        <h2 class="section-label">실행 설정</h2>
        <button type="button" class="btn w-full" @click="showRunSettings = true">
          <svg class="h-3.5 w-3.5 text-ink-faint" viewBox="0 0 20 20" fill="currentColor">
            <path
              fill-rule="evenodd"
              d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 0 1-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 0 1 .947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 0 1 2.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 0 1 2.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 0 1 .947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 0 1-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 0 1-2.287-.947ZM10 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"
              clip-rule="evenodd"
            />
          </svg>
          실행 설정
        </button>
        <p class="mt-1 text-[11px] leading-snug text-ink-faint">
          LLM 모델, 채팅/GraphRAG 옵션, DB 관리 설정을 확인·변경합니다.
        </p>
      </div>

      <div class="border-t border-border pt-3.5">
        <h2 class="section-label">온톨로지 설정</h2>
        <div class="space-y-3">
          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">그래프 노드 필터</h3>
            <p v-if="availableTypes.length === 0" class="text-[11px] text-ink-faint">
              아직 추출된 그래프가 없습니다
            </p>
            <label
              v-for="type in availableTypes"
              :key="type"
              class="flex cursor-pointer items-center justify-between gap-2 rounded px-1 py-0.5 text-xs hover:bg-white/5"
            >
              <span class="flex min-w-0 items-center gap-1.5 break-all">
                <input
                  type="checkbox"
                  :checked="enabledTypes.has(type)"
                  @change="toggleType(type)"
                  class="h-3.5 w-3.5 flex-shrink-0 rounded border-border bg-surface-sunken accent-accent"
                />
                {{ type }}
              </span>
              <span
                class="h-2.5 w-2.5 flex-shrink-0 rounded-full"
                :style="{ background: colorForType(type) }"
              ></span>
            </label>
          </div>

          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">그래프 엣지 필터</h3>
            <p v-if="availableEdgeTypes.length === 0" class="text-[11px] text-ink-faint">
              아직 추출된 그래프가 없습니다
            </p>
            <label
              v-for="type in availableEdgeTypes"
              :key="type"
              class="flex cursor-pointer items-center justify-between gap-2 rounded px-1 py-0.5 text-xs hover:bg-white/5"
            >
              <span class="flex min-w-0 items-center gap-1.5 break-all">
                <input
                  type="checkbox"
                  :checked="enabledEdgeTypes.has(type)"
                  @change="toggleEdgeType(type)"
                  class="h-3.5 w-3.5 flex-shrink-0 rounded border-border bg-surface-sunken accent-accent"
                />
                {{ type }}
              </span>
              <span
                class="h-1 w-5 flex-shrink-0 rounded-sm"
                :style="{ background: colorForEdgeType(type) }"
              ></span>
            </label>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="showFileExplorer"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showFileExplorer = false"
    >
      <div class="flex max-h-[80vh] w-[500px] max-w-[90vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">File Explorer</h2>
          <button type="button" class="btn" @click="showFileExplorer = false">닫기</button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <section class="mb-5">
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">문서 업로드</h3>
            <input
              type="file"
              @change="handleFileChange"
              :disabled="isUploading"
              class="block w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-surface-sunken file:px-2.5 file:py-1 file:text-xs file:text-ink hover:file:bg-white/5"
            />
            <p v-if="isUploading" class="mt-1 text-[11px] text-ink-muted">업로드 중...</p>
            <p v-if="uploadError" class="mt-1 text-[11px] text-red-400">{{ uploadError }}</p>
          </section>

          <section class="mb-5">
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">업로드된 문서</h3>
            <p v-if="files.length === 0" class="text-[11px] text-ink-faint">문서가 없습니다</p>
            <ul v-else class="space-y-0.5">
              <li
                v-for="f in files"
                :key="f.filename"
                class="cursor-pointer rounded-md px-2 py-1.5 text-xs hover:bg-white/5"
                :class="f.filename === selectedFilename ? 'bg-accent-muted/60 font-medium text-ink' : 'text-ink-muted'"
                @click="selectFile(f.filename)"
              >
                <div class="break-all">{{ f.original_filename }}</div>
                <div class="mt-1 flex gap-1.5">
                  <span
                    class="rounded px-1.5 py-0.5 text-[10px]"
                    :class="f.has_schema ? 'bg-emerald-500/15 text-emerald-400' : 'bg-white/5 text-ink-faint'"
                  >스키마</span>
                  <span
                    class="rounded px-1.5 py-0.5 text-[10px]"
                    :class="f.has_graph ? 'bg-emerald-500/15 text-emerald-400' : 'bg-white/5 text-ink-faint'"
                    :title="f.has_graph ? `그래프DB: ${f.graphdb_name}` : ''"
                  >그래프</span>
                </div>
              </li>
            </ul>
          </section>

          <section>
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">스키마 라이브러리</h3>
            <p v-if="schemas.length === 0" class="text-[11px] text-ink-faint">생성된 스키마가 없습니다</p>
            <ul v-else class="space-y-0.5">
              <li
                v-for="s in schemas"
                :key="s.stem"
                class="cursor-pointer rounded-md px-2 py-1.5 text-xs text-ink-muted hover:bg-white/5"
                :class="{ 'pointer-events-none opacity-40': isUsingSchema || !selectedFilename }"
                @click="useSchema(s.stem)"
              >
                {{ s.stem }}
              </li>
            </ul>
            <p v-if="isUsingSchema" class="mt-1 text-[11px] text-ink-muted">적용 중...</p>
            <p v-if="schemaUseError" class="mt-1 text-[11px] text-red-400">{{ schemaUseError }}</p>
          </section>
        </div>
      </div>
    </div>

    <div
      v-if="showRunSettings"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showRunSettings = false"
    >
      <div class="flex max-h-[80vh] w-[420px] max-w-[90vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">실행 설정</h2>
          <button type="button" class="btn" @click="showRunSettings = false">닫기</button>
        </div>
        <div class="flex-1 space-y-4 overflow-y-auto p-4">
          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">LLM 모델</h3>
            <p class="inline-block rounded border border-border bg-surface-sunken px-1.5 py-0.5 font-mono text-[11px] text-ink-muted">
              {{ model }}
            </p>
          </div>

          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">채팅 표시 설정</h3>
            <label class="flex items-center gap-2 text-xs text-ink">
              <input
                type="checkbox"
                :checked="renderMarkdown"
                @change="onMarkdownToggle"
                class="h-3.5 w-3.5 rounded border-border bg-surface-sunken accent-accent"
              />
              마크다운 HTML로 렌더링
            </label>
          </div>

          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">GraphRAG 설정</h3>
            <label class="flex items-center gap-2 text-xs text-ink">
              검색 hop 수
              <input
                type="number"
                min="1"
                max="5"
                :value="graphRagHops"
                @change="onHopsInput"
                class="field w-14"
              />
            </label>
          </div>

          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">스키마 생성 설정</h3>
            <label class="flex items-center gap-2 text-xs text-ink">
              최대 문자수
              <input
                type="number"
                min="1"
                step="1000"
                :value="maxSchemaChars"
                @change="onMaxSchemaCharsInput"
                class="field w-24"
              />
            </label>
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              이 값을 넘는 문서는 스키마 생성 시 오류가 발생합니다. 필요시 늘리세요.
            </p>
          </div>

          <div>
            <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">데이터베이스 관리</h3>
            <button type="button" class="btn-danger w-full" :disabled="isResettingDb" @click="resetDatabase">
              {{ isResettingDb ? '초기화 중...' : 'LadybugDB 초기화' }}
            </button>
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              WAL 파일 손상 등으로 그래프 조회가 계속 실패할 때 사용하세요. 모든 문서의 추출된 그래프가 삭제됩니다.
            </p>
            <p v-if="resetDbError" class="mt-1 text-[11px] text-red-400">{{ resetDbError }}</p>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="showValidationReport && validationReport"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showValidationReport = false"
    >
      <div class="flex max-h-[85vh] w-[720px] max-w-[92vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">온톨로지 검증 보고서</h2>
          <button type="button" class="btn" @click="showValidationReport = false">닫기</button>
        </div>
        <div class="flex-1 space-y-5 overflow-y-auto p-4">
          <div>
            <h3 class="section-label">요약</h3>
            <p class="mb-2 text-xs leading-relaxed text-ink">
              {{ validationReport.validation_summary?.overall_quality }}
            </p>
            <div class="flex flex-wrap gap-1.5">
              <span
                class="chip"
                :class="validationReport.validation_summary?.ontology_valid
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-400'"
              >
                온톨로지 {{ validationReport.validation_summary?.ontology_valid ? '유효' : '문제 있음' }}
              </span>
              <span
                class="chip"
                :class="validationReport.validation_summary?.extraction_valid
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-400'"
              >
                추출 {{ validationReport.validation_summary?.extraction_valid ? '유효' : '문제 있음' }}
              </span>
              <span
                class="chip"
                :class="validationReport.validation_summary?.provenance_valid
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-400'"
              >
                근거 {{ validationReport.validation_summary?.provenance_valid ? '유효' : '문제 있음' }}
              </span>
              <span
                class="chip"
                :class="validationReport.validation_summary?.competency_questions_answerable
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-400'"
              >
                질의응답 {{ validationReport.validation_summary?.competency_questions_answerable ? '가능' : '불가' }}
              </span>
            </div>
          </div>

          <div v-if="sortedIssues.length">
            <h3 class="section-label">발견된 문제 ({{ sortedIssues.length }}건)</h3>
            <div class="space-y-2">
              <div
                v-for="(issue, i) in sortedIssues"
                :key="i"
                class="rounded-md border border-border bg-surface-sunken p-2.5"
              >
                <div class="mb-1 flex flex-wrap items-center gap-1.5">
                  <span class="chip" :class="severityClass(issue.severity)">{{ issue.severity }}</span>
                  <span class="text-[10px] uppercase tracking-wide text-ink-faint">{{ issue.category }}</span>
                </div>
                <p class="text-xs text-ink">{{ issue.description }}</p>
                <p v-if="issue.affected_element" class="mt-1 text-[11px] text-ink-muted">
                  대상: {{ issue.affected_element }}
                </p>
                <p v-if="issue.evidence" class="mt-1 text-[11px] italic text-ink-faint">"{{ issue.evidence }}"</p>
                <p v-if="issue.recommended_action" class="mt-1 text-[11px] text-sky-400">
                  제안: {{ issue.recommended_action }}
                </p>
              </div>
            </div>
          </div>

          <div v-if="missingElementGroups.length">
            <h3 class="section-label">누락된 요소</h3>
            <div class="space-y-1.5">
              <div v-for="[key, label, items] in missingElementGroups" :key="key" class="text-xs">
                <span class="text-ink-faint">{{ label }}:</span>
                <span class="text-ink">{{ items.join(', ') }}</span>
              </div>
            </div>
          </div>

          <div v-if="validationReport.contradictions?.length">
            <h3 class="section-label">모순</h3>
            <ul class="list-disc space-y-1 pl-4 text-xs text-ink">
              <li v-for="(c, i) in validationReport.contradictions" :key="i">{{ c }}</li>
            </ul>
          </div>

          <div v-if="validationReport.ambiguities?.length">
            <h3 class="section-label">모호한 부분</h3>
            <ul class="list-disc space-y-1 pl-4 text-xs text-ink">
              <li v-for="(a, i) in validationReport.ambiguities" :key="i">{{ a }}</li>
            </ul>
          </div>

          <div v-if="validationReport.competency_questions?.length">
            <h3 class="section-label">질의응답 가능 여부</h3>
            <div class="space-y-2">
              <div
                v-for="(q, i) in validationReport.competency_questions"
                :key="i"
                class="rounded-md border border-border bg-surface-sunken p-2.5"
              >
                <div class="flex items-start justify-between gap-2">
                  <p class="text-xs text-ink">{{ q.question }}</p>
                  <span
                    class="chip flex-shrink-0"
                    :class="q.answerable
                      ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400'
                      : 'border-red-500/50 bg-red-500/15 text-red-400'"
                  >
                    {{ q.answerable ? '가능' : '불가' }}
                  </span>
                </div>
                <p v-if="q.missing_elements?.length" class="mt-1 text-[11px] text-ink-muted">
                  누락: {{ q.missing_elements.join(', ') }}
                </p>
                <p v-if="q.evidence" class="mt-1 text-[11px] italic text-ink-faint">{{ q.evidence }}</p>
              </div>
            </div>
          </div>

          <div v-if="validationReport.recommended_changes?.length">
            <h3 class="section-label">권장 변경 사항</h3>
            <ul class="list-disc space-y-1 pl-4 text-xs text-ink">
              <li v-for="(c, i) in validationReport.recommended_changes" :key="i">{{ c }}</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
