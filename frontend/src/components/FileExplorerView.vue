<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'

const props = defineProps({
  file: { type: Object, default: null },
  schemaVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['file-selected', 'schema-used', 'graph-extracted'])

const isUploading = ref(false)
const uploadError = ref('')
const uploadConverter = ref('table_aware')
const files = ref([])
const fileSearch = ref('')
const schemas = ref([])
const isUsingSchema = ref(false)
const schemaUseError = ref('')
const schemaVersions = ref([])
const versionActionError = ref('')
const isChunking = ref(false)
const chunkError = ref('')
const isGeneratingMd = ref(false)
const generateMdError = ref('')
const generateMdElapsed = ref(0)
let generateMdPollTimer = null
let generateMdElapsedTimer = null
const isSummarizing = ref(false)
const summaryError = ref('')
const isEditingManifest = ref(false)
const manifestDraft = reactive({ original_filename: '', converter: 'anydoc' })
const isSavingManifest = ref(false)
const manifestSaveError = ref('')
const isDeletingDocument = ref(false)
const deleteDocumentError = ref('')
const samsunglifeQuery = ref('')
const samsunglifeSearching = ref(false)
const samsunglifeSearched = ref(false)
const samsunglifeSearchError = ref('')
const samsunglifeResults = ref([])
const samsunglifeDownloadingName = ref('')
const samsunglifeDownloadError = ref('')

const currentFile = computed(() => files.value.find((f) => f.filename === props.file?.filename))

const filteredFiles = computed(() => {
  const query = fileSearch.value.trim().toLowerCase()
  if (!query) return files.value
  return files.value.filter((f) =>
    (f.original_filename ?? '').toLowerCase().includes(query) ||
    f.filename.toLowerCase().includes(query)
  )
})

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

function formatModifiedAt(epochSeconds) {
  if (!epochSeconds) return '-'
  return new Date(epochSeconds * 1000).toLocaleString('ko-KR')
}

const converterLabel = (converter) => (converter === 'table_aware' ? 'Table-aware (pdfplumber)' : 'anydoc')

function fileStageBadges(f) {
  return [
    { key: 'md', label: 'MD', done: !!f.has_md },
    { key: 'pdf', label: 'PDF', done: !!f.has_pdf },
    { key: 'chunk', label: 'Chunk', done: !!f.has_chunks },
    { key: 'goldenset', label: 'Golden', done: !!f.has_goldenset },
    { key: 'schema', label: 'Schema', done: !!f.has_schema },
    { key: 'graph', label: 'Graph', done: !!f.has_graph },
  ]
}

const pipelineStages = computed(() => (currentFile.value ? fileStageBadges(currentFile.value) : []))

function stopGenerateMdTimers() {
  if (generateMdPollTimer) {
    clearInterval(generateMdPollTimer)
    generateMdPollTimer = null
  }
  if (generateMdElapsedTimer) {
    clearInterval(generateMdElapsedTimer)
    generateMdElapsedTimer = null
  }
}

// A large policy PDF's table-aware conversion can take several minutes and
// pegs the backend process (see the "MD 생성" hang investigation) -- running
// it as a request the browser holds open for that long is exactly what made
// it look frozen and vulnerable to a dropped connection. The backend now
// runs it in a background thread and returns immediately; this polls for
// completion instead, so a single missed poll is just a retry.
async function pollGenerateMdStatus(filename) {
  let status
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(filename)}/generate-md`)
    if (!res.ok) return // transient poll failure -- try again next tick
    status = await res.json()
  } catch (err) {
    return
  }
  if (status.status === 'running') return
  stopGenerateMdTimers()
  isGeneratingMd.value = false
  if (status.status === 'error') {
    generateMdError.value = status.detail || 'MD 생성 실패'
  } else if (status.status === 'idle') {
    // Only reachable if the backend process restarted mid-conversion and
    // lost the in-memory task (see app.preprocess.md_generation) -- a plain
    // "try again" is accurate, not a bug to chase further.
    generateMdError.value = 'MD 생성이 중단되었습니다 (백엔드가 재시작된 것으로 보입니다). 다시 시도해주세요.'
  } else {
    await loadDocuments()
  }
}

function startGenerateMdPolling(filename) {
  stopGenerateMdTimers()
  isGeneratingMd.value = true
  generateMdElapsed.value = 0
  generateMdElapsedTimer = setInterval(() => {
    generateMdElapsed.value += 1
  }, 1000)
  generateMdPollTimer = setInterval(() => pollGenerateMdStatus(filename), 3000)
  pollGenerateMdStatus(filename)
}

async function generateMd() {
  const filename = props.file?.filename
  if (!filename) return
  generateMdError.value = ''
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(filename)}/generate-md`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    // Re-check the current selection hasn't moved on to a different
    // document while the POST above was in flight, so polling never tracks
    // the wrong file's status.
    if (props.file?.filename === filename) startGenerateMdPolling(filename)
  } catch (err) {
    if (props.file?.filename === filename) generateMdError.value = 'MD 생성 실패: ' + err.message
  }
}

// Resume watching an in-progress conversion after switching away and back
// to the same document (the background task survives navigation even
// though this component's own polling timers don't).
async function resumeGenerateMdIfRunning(filename) {
  stopGenerateMdTimers()
  isGeneratingMd.value = false
  if (!filename) return
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(filename)}/generate-md`)
    if (!res.ok) return
    const status = await res.json()
    if (status.status === 'running') startGenerateMdPolling(filename)
  } catch (err) {
    // best-effort -- if this fails, the user can still click "MD 생성" again
  }
}

async function createChunks() {
  if (!props.file?.filename) return
  isChunking.value = true
  chunkError.value = ''
  try {
    const res = await apiFetch(
      `/api/documents/${encodeURIComponent(props.file.filename)}/chunk`,
      { method: 'POST' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadDocuments()
  } catch (err) {
    chunkError.value = '청크 생성 실패: ' + err.message
  } finally {
    isChunking.value = false
  }
}

async function createSummary() {
  if (!props.file?.filename) return
  isSummarizing.value = true
  summaryError.value = ''
  try {
    const res = await apiFetch(
      `/api/documents/${encodeURIComponent(props.file.filename)}/summary`,
      { method: 'POST' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadDocuments()
  } catch (err) {
    summaryError.value = '요약 생성 실패: ' + err.message
  } finally {
    isSummarizing.value = false
  }
}

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
  if (!props.file?.filename) {
    schemaVersions.value = []
    return
  }
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.file.filename)}/schema/versions`
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

function selectFile(filename) {
  const doc = files.value.find((f) => f.filename === filename)
  emit('file-selected', { filename, path: `data/${filename}`, has_pdf: !!doc?.has_pdf })
}

async function useSchema(sourceStem) {
  if (!props.file?.filename) return
  isUsingSchema.value = true
  schemaUseError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.file.filename)}/schema/use`,
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
      `/api/ontology/${encodeURIComponent(props.file.filename)}/schema/versions/${version}/activate`,
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
      `/api/ontology/${encodeURIComponent(props.file.filename)}/schema/versions/${version}`,
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

function startEditManifest() {
  manifestDraft.original_filename = currentFile.value?.original_filename ?? ''
  manifestDraft.converter = currentFile.value?.converter ?? 'anydoc'
  manifestSaveError.value = ''
  isEditingManifest.value = true
}

function cancelEditManifest() {
  isEditingManifest.value = false
}

async function saveManifest() {
  if (!props.file?.filename) return
  isSavingManifest.value = true
  manifestSaveError.value = ''
  try {
    const res = await apiFetch(
      `/api/documents/${encodeURIComponent(props.file.filename)}/manifest`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          original_filename: manifestDraft.original_filename,
          converter: manifestDraft.converter,
        }),
      }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadDocuments()
    isEditingManifest.value = false
  } catch (err) {
    manifestSaveError.value = '메타정보 저장 실패: ' + err.message
  } finally {
    isSavingManifest.value = false
  }
}

async function deleteDocument() {
  if (!props.file?.filename) return
  const label = currentFile.value?.original_filename ?? props.file.filename
  const confirmed = window.confirm(
    `"${label}" 문서를 삭제하시겠습니까? 원문, 스키마, 그래프 등 이 문서의 모든 데이터가 삭제되며 되돌릴 수 없습니다.`
  )
  if (!confirmed) return

  isDeletingDocument.value = true
  deleteDocumentError.value = ''
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(props.file.filename)}`, {
      method: 'DELETE',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    await loadDocuments()
    emit('file-selected', null)
  } catch (err) {
    deleteDocumentError.value = '문서 삭제 실패: ' + err.message
  } finally {
    isDeletingDocument.value = false
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
    formData.append('converter', uploadConverter.value)
    const res = await apiFetch('/api/parse', { method: 'POST', body: formData })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    await loadDocuments()
    const doc = files.value.find((f) => f.filename === data.filename)
    emit('file-selected', { ...data, has_pdf: !!doc?.has_pdf })
  } catch (err) {
    uploadError.value = '업로드 실패: ' + err.message
  } finally {
    isUploading.value = false
    event.target.value = ''
  }
}

async function searchSamsunglifeTerms() {
  const query = samsunglifeQuery.value.trim()
  if (!query) return

  samsunglifeSearching.value = true
  samsunglifeSearchError.value = ''
  try {
    const res = await apiFetch(`/api/samsunglife/terms?q=${encodeURIComponent(query)}`)
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    samsunglifeResults.value = data.terms
  } catch (err) {
    samsunglifeSearchError.value = '검색 실패: ' + err.message
  } finally {
    samsunglifeSearching.value = false
    samsunglifeSearched.value = true
  }
}

async function downloadSamsunglifeTerm(term) {
  samsunglifeDownloadingName.value = term.name
  samsunglifeDownloadError.value = ''
  try {
    const res = await apiFetch('/api/samsunglife/terms/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: term.name, categories: [term.category] }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    await loadDocuments()
    const doc = files.value.find((f) => f.filename === data.filename)
    emit('file-selected', { ...data, has_pdf: !!doc?.has_pdf })
    samsunglifeResults.value = []
    samsunglifeSearched.value = false
    samsunglifeQuery.value = ''
  } catch (err) {
    samsunglifeDownloadError.value = '다운로드 실패: ' + err.message
  } finally {
    samsunglifeDownloadingName.value = ''
  }
}

watch(() => props.file?.filename, () => {
  isEditingManifest.value = false
  manifestSaveError.value = ''
  deleteDocumentError.value = ''
  generateMdError.value = ''
})
watch(() => props.file?.filename, loadSchemaVersions)
watch(() => props.file?.filename, resumeGenerateMdIfRunning, { immediate: true })
watch(() => props.schemaVersion, loadSchemaVersions)
watch(() => props.schemaVersion, () => {
  loadSchemas()
  loadDocuments()
})

onMounted(async () => {
  await loadDocuments()
  await loadSchemas()
  await loadSchemaVersions()
})

onBeforeUnmount(() => {
  stopGenerateMdTimers()
})
</script>

<template>
  <section class="flex h-full flex-col overflow-hidden">
    <div class="panel-header">
      <span>File Explorer</span>
    </div>
    <div class="flex flex-1 overflow-hidden">
      <!-- Column 1: upload (more actions land here over time) -->
      <div class="flex w-[240px] flex-shrink-0 flex-col overflow-y-auto border-r border-border p-4">
        <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">문서 업로드</h3>
        <div class="mb-2 flex flex-col gap-1.5 text-[11px] text-ink-muted">
          <label class="flex items-center gap-1.5">
            <input
              type="radio"
              value="anydoc"
              v-model="uploadConverter"
              class="h-3 w-3 accent-accent"
            />
            anydoc (일반)
          </label>
          <label class="flex items-center gap-1.5">
            <input
              type="radio"
              value="table_aware"
              v-model="uploadConverter"
              class="h-3 w-3 accent-accent"
            />
            Table-aware (PDF 표 인식)
          </label>
        </div>
        <input
          type="file"
          @change="handleFileChange"
          :disabled="isUploading"
          class="block w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-surface-sunken file:px-2.5 file:py-1 file:text-xs file:text-ink hover:file:bg-ink/5"
        />
        <p v-if="uploadConverter === 'table_aware'" class="mt-1 text-[11px] leading-snug text-ink-faint">
          PDF가 아닌 파일은 자동으로 anydoc으로 변환됩니다.
        </p>
        <p v-if="isUploading" class="mt-1 text-[11px] text-ink-muted">업로드 중...</p>
        <p v-if="uploadError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ uploadError }}</p>

        <div class="mt-4 border-t border-border pt-3">
          <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">삼성생명 약관 다운로드</h3>
          <form class="flex gap-1" @submit.prevent="searchSamsunglifeTerms">
            <input
              v-model="samsunglifeQuery"
              type="text"
              placeholder="상품명으로 검색"
              class="field w-full text-xs"
              :disabled="!!samsunglifeDownloadingName"
            />
            <button
              type="submit"
              class="btn flex-shrink-0 px-2 py-1 text-[11px]"
              :disabled="samsunglifeSearching || !!samsunglifeDownloadingName"
            >{{ samsunglifeSearching ? '검색 중...' : '검색' }}</button>
          </form>
          <p v-if="samsunglifeSearchError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ samsunglifeSearchError }}</p>
          <ul v-if="samsunglifeResults.length" class="mt-2 space-y-1">
            <li
              v-for="term in samsunglifeResults"
              :key="`${term.category}-${term.name}`"
              class="flex items-center justify-between gap-1.5 rounded-md px-1.5 py-1 text-[11px] hover:bg-ink/5"
            >
              <div class="min-w-0">
                <div class="break-all text-ink">{{ term.name }}</div>
                <div class="text-[10px] text-ink-faint">
                  {{ term.category }} · {{ term.currently_listed ? '판매중' : '판매종료' }}
                </div>
              </div>
              <button
                type="button"
                class="btn flex-shrink-0 px-2 py-0.5 text-[11px]"
                :disabled="!!samsunglifeDownloadingName"
                @click="downloadSamsunglifeTerm(term)"
              >{{ samsunglifeDownloadingName === term.name ? '다운로드 중...' : '다운로드' }}</button>
            </li>
          </ul>
          <p
            v-else-if="samsunglifeSearched && !samsunglifeSearching && !samsunglifeSearchError"
            class="mt-1 text-[11px] text-ink-faint"
          >검색 결과가 없습니다</p>
          <p v-if="samsunglifeDownloadingName" class="mt-1 text-[11px] text-ink-muted">
            "{{ samsunglifeDownloadingName }}" 다운로드 중...
          </p>
          <p v-if="samsunglifeDownloadError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ samsunglifeDownloadError }}</p>
        </div>
      </div>

      <!-- Column 2: document list, searchable -->
      <div class="flex w-[280px] flex-shrink-0 flex-col overflow-hidden border-r border-border p-4">
        <h3 class="mb-1.5 flex-shrink-0 text-[10px] uppercase tracking-wide text-ink-faint">업로드된 문서</h3>
        <input
          v-model="fileSearch"
          type="text"
          placeholder="파일명 검색"
          class="field mb-2 w-full flex-shrink-0"
        />
        <div class="min-h-0 flex-1 overflow-y-auto">
          <p v-if="files.length === 0" class="text-[11px] text-ink-faint">문서가 없습니다</p>
          <p v-else-if="filteredFiles.length === 0" class="text-[11px] text-ink-faint">검색 결과가 없습니다</p>
          <ul v-else class="space-y-0.5">
            <li
              v-for="f in filteredFiles"
              :key="f.filename"
              class="cursor-pointer rounded-md px-2 py-1.5 text-xs hover:bg-ink/5"
              :class="f.filename === file?.filename ? 'bg-accent-muted/60 font-medium text-ink' : 'text-ink-muted'"
              @click="selectFile(f.filename)"
            >
              <div class="break-all">{{ f.original_filename }}</div>
              <div class="mt-1 flex gap-1">
                <span
                  v-for="stage in fileStageBadges(f)"
                  :key="stage.key"
                  class="rounded px-1.5 py-0.5 text-[10px]"
                  :class="stage.done ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400' : 'bg-ink/5 text-ink-faint'"
                >{{ stage.label }}</span>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <!-- Column 3: metadata + schema versions/library for the selected file -->
      <div class="flex-1 overflow-y-auto p-4">
        <p v-if="!file" class="text-[11px] text-ink-faint">좌측에서 문서를 선택하세요</p>
        <template v-else>
          <section class="mb-5">
            <div class="mb-1.5 flex items-center justify-between">
              <h3 class="text-[10px] uppercase tracking-wide text-ink-faint">메타 정보</h3>
              <div class="flex gap-1.5">
                <button
                  v-if="!isEditingManifest"
                  type="button"
                  class="btn px-2 py-0.5 text-[11px]"
                  @click="startEditManifest"
                >편집</button>
                <button
                  type="button"
                  class="btn-danger px-2 py-0.5 text-[11px]"
                  :disabled="isDeletingDocument"
                  @click="deleteDocument"
                >{{ isDeletingDocument ? '삭제 중...' : '문서 삭제' }}</button>
              </div>
            </div>

            <form v-if="isEditingManifest" class="space-y-2" @submit.prevent="saveManifest">
              <div>
                <label class="mb-0.5 block text-[10px] text-ink-faint">원본 파일명</label>
                <input
                  v-model="manifestDraft.original_filename"
                  type="text"
                  class="field w-full"
                  :disabled="isSavingManifest"
                />
              </div>
              <div>
                <label class="mb-0.5 block text-[10px] text-ink-faint">변환기</label>
                <select v-model="manifestDraft.converter" class="field w-full" :disabled="isSavingManifest">
                  <option value="anydoc">anydoc (일반)</option>
                  <option value="table_aware">Table-aware (pdfplumber)</option>
                </select>
              </div>
              <div class="flex gap-1.5">
                <button type="submit" class="btn-primary px-2 py-1 text-[11px]" :disabled="isSavingManifest">
                  {{ isSavingManifest ? '저장 중...' : '저장' }}
                </button>
                <button
                  type="button"
                  class="btn px-2 py-1 text-[11px]"
                  :disabled="isSavingManifest"
                  @click="cancelEditManifest"
                >취소</button>
              </div>
              <p v-if="manifestSaveError" class="text-[11px] text-red-600 dark:text-red-400">{{ manifestSaveError }}</p>
            </form>
            <dl v-else class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <dt class="text-ink-faint">원본 파일명</dt>
              <dd class="break-all text-ink">{{ currentFile?.original_filename ?? '-' }}</dd>
              <dt class="text-ink-faint">저장된 파일명</dt>
              <dd class="break-all text-ink-muted">{{ currentFile?.filename ?? '-' }}</dd>
              <dt class="text-ink-faint">변환기</dt>
              <dd class="text-ink-muted">{{ converterLabel(currentFile?.converter) }}</dd>
              <dt class="text-ink-faint">크기</dt>
              <dd class="text-ink-muted">{{ formatBytes(currentFile?.size_bytes) }}</dd>
              <dt class="text-ink-faint">수정 시각</dt>
              <dd class="text-ink-muted">{{ formatModifiedAt(currentFile?.modified_at) }}</dd>
            </dl>
            <p v-if="deleteDocumentError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ deleteDocumentError }}</p>

            <div class="mt-3">
              <div class="mb-1 flex items-center justify-between">
                <span class="text-[10px] uppercase tracking-wide text-ink-faint">요약</span>
                <button
                  type="button"
                  class="btn px-2 py-0.5 text-[11px]"
                  :disabled="isSummarizing || !currentFile?.has_md"
                  @click="createSummary"
                >{{ currentFile?.summary ? '재생성' : '요약 생성' }}</button>
              </div>
              <p v-if="isSummarizing" class="text-[11px] text-ink-muted">생성 중...</p>
              <p v-else-if="currentFile?.summary" class="whitespace-pre-line text-xs leading-snug text-ink-muted">{{ currentFile.summary }}</p>
              <p v-else class="text-[11px] text-ink-faint">생성된 요약이 없습니다</p>
              <p v-if="summaryError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ summaryError }}</p>
            </div>

            <div class="mt-3">
              <span class="mb-1 block text-[10px] uppercase tracking-wide text-ink-faint">파이프라인 단계</span>
              <div class="flex flex-wrap items-center gap-1.5">
                <span
                  v-for="stage in pipelineStages"
                  :key="stage.key"
                  class="rounded px-1.5 py-0.5 text-[10px]"
                  :class="stage.done ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400' : 'bg-ink/5 text-ink-faint'"
                  :title="stage.key === 'graph' && currentFile?.has_graph ? `그래프DB: ${currentFile.graphdb_name}` : ''"
                >{{ stage.label }}</span>
                <button
                  v-if="!currentFile?.has_md"
                  type="button"
                  class="btn px-2 py-0.5 text-[11px]"
                  :disabled="isGeneratingMd"
                  @click="generateMd"
                >{{ isGeneratingMd ? 'MD 생성 중...' : 'MD 생성' }}</button>
                <button
                  type="button"
                  class="btn px-2 py-0.5 text-[11px]"
                  :disabled="isChunking || !currentFile?.has_md"
                  @click="createChunks"
                >{{ currentFile?.has_chunks ? '청크 재생성' : '청크 생성' }}</button>
              </div>
              <p v-if="isGeneratingMd" class="mt-1 text-[11px] text-ink-muted">
                PDF 표 인식 변환 중... ({{ generateMdElapsed }}초 경과) 분량이 많은 문서는
                수 분 정도 걸릴 수 있습니다. 다른 화면으로 이동해도 백그라운드에서 계속 진행됩니다.
              </p>
              <p v-if="generateMdError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ generateMdError }}</p>
              <p v-if="isChunking" class="mt-1 text-[11px] text-ink-muted">청크 생성 중...</p>
              <p v-if="chunkError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ chunkError }}</p>
            </div>
          </section>

          <section class="mb-5">
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">선택된 문서의 스키마 버전</h3>
            <p v-if="schemaVersions.length === 0" class="text-[11px] text-ink-faint">생성된 버전이 없습니다</p>
            <ul v-else class="space-y-0.5">
              <li
                v-for="v in schemaVersions"
                :key="v.version"
                class="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-xs"
                :class="{ 'bg-accent-muted/60': v.is_active }"
              >
                <div class="flex min-w-0 items-center gap-1.5 break-all">
                  <span class="font-medium text-ink">v{{ v.version }} · {{ v.document_type }}</span>
                  <span
                    v-if="v.is_active"
                    class="rounded px-1.5 py-0.5 text-[10px] bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                  >활성</span>
                  <span
                    class="rounded px-1.5 py-0.5 text-[10px]"
                    :class="v.has_graph ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400' : 'bg-ink/5 text-ink-faint'"
                  >그래프</span>
                </div>
                <div class="flex flex-shrink-0 gap-1">
                  <button
                    v-if="!v.is_active"
                    type="button"
                    class="btn px-2 py-1 text-[11px]"
                    @click="activateVersion(v.version)"
                  >활성화</button>
                  <button
                    type="button"
                    class="btn-danger px-2 py-1 text-[11px]"
                    @click="deleteVersion(v.version)"
                  >삭제</button>
                </div>
              </li>
            </ul>
            <p v-if="versionActionError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ versionActionError }}</p>
          </section>

          <section>
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">스키마 라이브러리</h3>
            <p v-if="schemas.length === 0" class="text-[11px] text-ink-faint">생성된 스키마가 없습니다</p>
            <ul v-else class="space-y-0.5">
              <li
                v-for="s in schemas"
                :key="s.stem"
                class="cursor-pointer rounded-md px-2 py-1.5 text-xs text-ink-muted hover:bg-ink/5"
                :class="{ 'pointer-events-none opacity-40': isUsingSchema }"
                @click="useSchema(s.stem)"
              >
                {{ s.stem }}
              </li>
            </ul>
            <p v-if="isUsingSchema" class="mt-1 text-[11px] text-ink-muted">적용 중...</p>
            <p v-if="schemaUseError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ schemaUseError }}</p>
          </section>
        </template>
      </div>
    </div>
  </section>
</template>
