<script setup>
import { computed, onMounted, ref, watch } from 'vue'
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
const schemas = ref([])
const isUsingSchema = ref(false)
const schemaUseError = ref('')
const schemaVersions = ref([])
const versionActionError = ref('')
const isChunking = ref(false)
const chunkError = ref('')
const isSummarizing = ref(false)
const summaryError = ref('')

const currentFile = computed(() => files.value.find((f) => f.filename === props.file?.filename))

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
    { key: 'md', label: 'MD', done: true },
    { key: 'pdf', label: 'PDF', done: !!f.has_pdf },
    { key: 'chunk', label: 'Chunk', done: !!f.has_chunks },
    { key: 'goldenset', label: 'Golden', done: !!f.has_goldenset },
    { key: 'schema', label: 'Schema', done: !!f.has_schema },
    { key: 'graph', label: 'Graph', done: !!f.has_graph },
  ]
}

const pipelineStages = computed(() => (currentFile.value ? fileStageBadges(currentFile.value) : []))

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

watch(() => props.file?.filename, loadSchemaVersions)
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
</script>

<template>
  <section class="flex h-full flex-col overflow-hidden">
    <div class="panel-header">
      <span>File Explorer</span>
    </div>
    <div class="flex flex-1 overflow-hidden">
      <!-- Left column: upload + file list -->
      <div class="flex w-[320px] flex-shrink-0 flex-col overflow-y-auto border-r border-border p-4">
        <section class="mb-5">
          <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">문서 업로드</h3>
          <div class="mb-2 flex gap-3 text-[11px] text-ink-muted">
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
            class="block w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-surface-sunken file:px-2.5 file:py-1 file:text-xs file:text-ink hover:file:bg-white/5"
          />
          <p v-if="uploadConverter === 'table_aware'" class="mt-1 text-[11px] leading-snug text-ink-faint">
            PDF가 아닌 파일은 자동으로 anydoc으로 변환됩니다.
          </p>
          <p v-if="isUploading" class="mt-1 text-[11px] text-ink-muted">업로드 중...</p>
          <p v-if="uploadError" class="mt-1 text-[11px] text-red-400">{{ uploadError }}</p>
        </section>

        <section>
          <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">업로드된 문서</h3>
          <p v-if="files.length === 0" class="text-[11px] text-ink-faint">문서가 없습니다</p>
          <ul v-else class="space-y-0.5">
            <li
              v-for="f in files"
              :key="f.filename"
              class="cursor-pointer rounded-md px-2 py-1.5 text-xs hover:bg-white/5"
              :class="f.filename === file?.filename ? 'bg-accent-muted/60 font-medium text-ink' : 'text-ink-muted'"
              @click="selectFile(f.filename)"
            >
              <div class="break-all">{{ f.original_filename }}</div>
              <div class="mt-1 flex gap-1">
                <span
                  v-for="stage in fileStageBadges(f)"
                  :key="stage.key"
                  class="rounded px-1.5 py-0.5 text-[10px]"
                  :class="stage.done ? 'bg-emerald-500/15 text-emerald-400' : 'bg-white/5 text-ink-faint'"
                >{{ stage.label }}</span>
              </div>
            </li>
          </ul>
        </section>
      </div>

      <!-- Right column: metadata + schema versions/library for the selected file -->
      <div class="flex-1 overflow-y-auto p-4">
        <p v-if="!file" class="text-[11px] text-ink-faint">좌측에서 문서를 선택하세요</p>
        <template v-else>
          <section class="mb-5">
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">메타 정보</h3>
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
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

            <div class="mt-3">
              <div class="mb-1 flex items-center justify-between">
                <span class="text-[10px] uppercase tracking-wide text-ink-faint">요약</span>
                <button
                  type="button"
                  class="btn px-2 py-0.5 text-[11px]"
                  :disabled="isSummarizing"
                  @click="createSummary"
                >{{ currentFile?.summary ? '재생성' : '요약 생성' }}</button>
              </div>
              <p v-if="isSummarizing" class="text-[11px] text-ink-muted">생성 중...</p>
              <p v-else-if="currentFile?.summary" class="whitespace-pre-line text-xs leading-snug text-ink-muted">{{ currentFile.summary }}</p>
              <p v-else class="text-[11px] text-ink-faint">생성된 요약이 없습니다</p>
              <p v-if="summaryError" class="mt-1 text-[11px] text-red-400">{{ summaryError }}</p>
            </div>

            <div class="mt-3">
              <span class="mb-1 block text-[10px] uppercase tracking-wide text-ink-faint">파이프라인 단계</span>
              <div class="flex flex-wrap items-center gap-1.5">
                <span
                  v-for="stage in pipelineStages"
                  :key="stage.key"
                  class="rounded px-1.5 py-0.5 text-[10px]"
                  :class="stage.done ? 'bg-emerald-500/15 text-emerald-400' : 'bg-white/5 text-ink-faint'"
                  :title="stage.key === 'graph' && currentFile?.has_graph ? `그래프DB: ${currentFile.graphdb_name}` : ''"
                >{{ stage.label }}</span>
                <button
                  type="button"
                  class="btn px-2 py-0.5 text-[11px]"
                  :disabled="isChunking"
                  @click="createChunks"
                >{{ currentFile?.has_chunks ? '청크 재생성' : '청크 생성' }}</button>
              </div>
              <p v-if="isChunking" class="mt-1 text-[11px] text-ink-muted">청크 생성 중...</p>
              <p v-if="chunkError" class="mt-1 text-[11px] text-red-400">{{ chunkError }}</p>
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
                    class="rounded px-1.5 py-0.5 text-[10px] bg-emerald-500/15 text-emerald-400"
                  >활성</span>
                  <span
                    class="rounded px-1.5 py-0.5 text-[10px]"
                    :class="v.has_graph ? 'bg-emerald-500/15 text-emerald-400' : 'bg-white/5 text-ink-faint'"
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
            <p v-if="versionActionError" class="mt-1 text-[11px] text-red-400">{{ versionActionError }}</p>
          </section>

          <section>
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">스키마 라이브러리</h3>
            <p v-if="schemas.length === 0" class="text-[11px] text-ink-faint">생성된 스키마가 없습니다</p>
            <ul v-else class="space-y-0.5">
              <li
                v-for="s in schemas"
                :key="s.stem"
                class="cursor-pointer rounded-md px-2 py-1.5 text-xs text-ink-muted hover:bg-white/5"
                :class="{ 'pointer-events-none opacity-40': isUsingSchema }"
                @click="useSchema(s.stem)"
              >
                {{ s.stem }}
              </li>
            </ul>
            <p v-if="isUsingSchema" class="mt-1 text-[11px] text-ink-muted">적용 중...</p>
            <p v-if="schemaUseError" class="mt-1 text-[11px] text-red-400">{{ schemaUseError }}</p>
          </section>
        </template>
      </div>
    </div>
  </section>
</template>
