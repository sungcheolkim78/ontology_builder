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
const maxTokens = ref(null)
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
const schemaVersions = ref([])
const versionActionError = ref('')

const currentFile = computed(() => files.value.find((f) => f.filename === props.selectedFilename))

const schemaDocumentType = ref('general')
const isGeneratingSchema = ref(false)
const isExtracting = ref(false)
const isEmbedding = ref(false)
const isValidating = ref(false)
const validationReport = ref(null)
const validationError = ref('')
const showValidationReport = ref(false)
const isProposingEvolution = ref(false)
const evolutionProposal = ref(null)
const evolutionError = ref('')
const showEvolutionReview = ref(false)
const acceptedChangeIds = ref(new Set())
const isApplyingEvolution = ref(false)
const evolutionApplyError = ref('')
const evolutionApplyMessage = ref('')
const isDiscovering = ref(false)
const discoveryReport = ref(null)
const discoveryError = ref('')
const showDiscoveryReport = ref(false)
const useDiscoveryForSchema = ref(false)
const showDomainSchema = ref(false)
const domains = ref([])
const selectedDomain = ref('')
const newDomainName = ref('')
const domainSchema = ref(null)
const selectedCalibrationFiles = ref(new Set())
const isConverging = ref(false)
const convergeError = ref('')
const convergeMessage = ref('')
const domainEvaluation = ref(null)
const acceptedDomainReviewIds = ref(new Set())
const isApplyingDomainReview = ref(false)
const domainReviewError = ref('')
const isUsingDomainSchema = ref(false)
const domainUseError = ref('')
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
  if (isProposingEvolution.value) return `검증 보고서를 바탕으로 개선안을 도출하는 중... ${elapsedSeconds.value}초`
  if (isDiscovering.value) return `문서에서 후보 온톨로지(개념/관계/속성/이벤트/규칙)를 발견하는 중... ${elapsedSeconds.value}초`
  return ''
})

onUnmounted(() => stopElapsedTimer())

async function discoverOntology() {
  if (!props.selectedFilename) return
  isDiscovering.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  discoveryError.value = ''
  startElapsedTimer()
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/discover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_chars: maxSchemaChars.value }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    discoveryReport.value = await res.json()
    useDiscoveryForSchema.value = true
    showDiscoveryReport.value = true
  } catch (err) {
    discoveryError.value = '온톨로지 발견 실패: ' + err.message
  } finally {
    isDiscovering.value = false
    stopElapsedTimer()
  }
}

async function loadDiscovery() {
  useDiscoveryForSchema.value = false
  if (!props.selectedFilename) {
    discoveryReport.value = null
    return
  }
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/discover`)
    discoveryReport.value = res.ok ? await res.json() : null
  } catch (err) {
    discoveryReport.value = null
  }
}

const discoveryClasses = computed(() => discoveryReport.value?.classes ?? [])
const discoveryRelationships = computed(() => discoveryReport.value?.relationships ?? [])

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
        use_discovery: useDiscoveryForSchema.value && !!discoveryReport.value,
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

// Only ADD/MODIFY/MERGE/DEPRECATE are pre-checked -- REJECT has nothing to
// apply, and NEEDS_HUMAN_REVIEW must never be auto-selected (the person
// reviewing has to deliberately check it themselves) per
// docs/ontology/ontology_evolution_prompt.md's governance rule.
const AUTO_ACCEPT_DECISIONS = new Set(['ADD', 'MODIFY', 'MERGE', 'DEPRECATE'])

async function proposeEvolution() {
  if (!props.selectedFilename || !validationReport.value) return
  isProposingEvolution.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  evolutionError.value = ''
  evolutionApplyError.value = ''
  evolutionApplyMessage.value = ''
  startElapsedTimer()
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/evolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        validation_report: validationReport.value,
        max_chars: maxSchemaChars.value,
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    evolutionProposal.value = await res.json()
    acceptedChangeIds.value = new Set(
      evolutionProposal.value.changes
        .filter((c) => AUTO_ACCEPT_DECISIONS.has(c.decision))
        .map((c) => c.change_id)
    )
    showValidationReport.value = false
    showEvolutionReview.value = true
  } catch (err) {
    evolutionError.value = '개선안 도출 실패: ' + err.message
  } finally {
    isProposingEvolution.value = false
    stopElapsedTimer()
  }
}

function toggleChangeAccepted(changeId) {
  const next = new Set(acceptedChangeIds.value)
  if (next.has(changeId)) {
    next.delete(changeId)
  } else {
    next.add(changeId)
  }
  acceptedChangeIds.value = next
}

async function applyEvolution() {
  if (!props.selectedFilename || !evolutionProposal.value) return
  isApplyingEvolution.value = true
  evolutionApplyError.value = ''
  evolutionApplyMessage.value = ''
  try {
    const changes = evolutionProposal.value.changes.filter((c) => acceptedChangeIds.value.has(c.change_id))
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.selectedFilename)}/evolve/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const result = await res.json()
    evolutionApplyMessage.value = `v${result.version}로 반영 완료 (노드 ${result.node_count}개, 엣지 ${result.edge_count}개)`
    evolutionProposal.value = null
    validationReport.value = null
    emit('graph-extracted')
  } catch (err) {
    evolutionApplyError.value = '개선안 반영 실패: ' + err.message
  } finally {
    isApplyingEvolution.value = false
  }
}

const DECISION_STYLES = {
  ADD: 'border-emerald-500/50 bg-emerald-500/15 text-emerald-400',
  MODIFY: 'border-sky-500/50 bg-sky-500/15 text-sky-400',
  MERGE: 'border-violet-500/50 bg-violet-500/15 text-violet-400',
  DEPRECATE: 'border-amber-500/50 bg-amber-500/15 text-amber-400',
  REJECT: 'border-border bg-white/5 text-ink-faint',
  NEEDS_HUMAN_REVIEW: 'border-red-500/50 bg-red-500/15 text-red-400',
}

function decisionClass(decision) {
  return DECISION_STYLES[decision] ?? DECISION_STYLES.REJECT
}

function changeSummary(change) {
  const el = change.element ?? {}
  if (change.element_type === 'node_type' || change.element_type === 'edge_type') return el.name
  if (change.element_type === 'node') return `${el.label} (${el.type})`
  if (change.element_type === 'edge') return `${el.source} → ${el.target} (${el.type})`
  return ''
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

watch(() => props.selectedFilename, loadSchemaVersions)
watch(() => props.schemaVersion, loadSchemaVersions)
watch(() => props.selectedFilename, loadDiscovery)

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
    maxTokens.value = data.max_tokens ?? null
  } catch (err) {
    model.value = '알 수 없음'
  }

  await loadDocuments()
  await loadSchemas()
  await loadSchemaVersions()
  await loadDiscovery()
  await loadDomains()
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

async function loadDomains() {
  try {
    const res = await apiFetch('/api/ontology/domain-schemas')
    const data = await res.json()
    domains.value = data.domains
  } catch (err) {
    // domain list is best-effort; leave as-is on failure
  }
}

async function loadDomainSchema(domain) {
  if (!domain) {
    domainSchema.value = null
    return
  }
  try {
    const res = await apiFetch(`/api/ontology/domain-schema/${encodeURIComponent(domain)}`)
    domainSchema.value = res.ok ? await res.json() : null
  } catch (err) {
    domainSchema.value = null
  }
}

function onSelectDomain() {
  domainEvaluation.value = null
  acceptedDomainReviewIds.value = new Set()
  convergeMessage.value = ''
  convergeError.value = ''
  loadDomainSchema(selectedDomain.value)
}

function toggleCalibrationFile(filename) {
  const next = new Set(selectedCalibrationFiles.value)
  if (next.has(filename)) {
    next.delete(filename)
  } else {
    next.add(filename)
  }
  selectedCalibrationFiles.value = next
}

async function runDomainConvergence() {
  const domain = selectedDomain.value || newDomainName.value.trim()
  if (!domain || selectedCalibrationFiles.value.size === 0) return
  isConverging.value = true
  convergeError.value = ''
  convergeMessage.value = ''
  try {
    const res = await apiFetch(`/api/ontology/domain-schema/${encodeURIComponent(domain)}/converge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames: [...selectedCalibrationFiles.value] }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const result = await res.json()
    domainEvaluation.value = result.evaluation
    convergeMessage.value =
      `수렴 완료 (노드 타입 ${result.schema.node_types.length}개, ` +
      `엣지 타입 ${result.schema.edge_types.length}개, 검토 대기 ${result.pending_review.length}건)`
    selectedDomain.value = domain
    newDomainName.value = ''
    selectedCalibrationFiles.value = new Set()
    await loadDomains()
    await loadDomainSchema(domain)
  } catch (err) {
    convergeError.value = '수렴 실행 실패: ' + err.message
  } finally {
    isConverging.value = false
  }
}

function toggleDomainReviewAccepted(changeId) {
  const next = new Set(acceptedDomainReviewIds.value)
  if (next.has(changeId)) {
    next.delete(changeId)
  } else {
    next.add(changeId)
  }
  acceptedDomainReviewIds.value = next
}

async function applyDomainReview() {
  if (!selectedDomain.value || !domainSchema.value) return
  isApplyingDomainReview.value = true
  domainReviewError.value = ''
  try {
    const changes = domainSchema.value.pending_review.filter((c) =>
      acceptedDomainReviewIds.value.has(c.change_id)
    )
    const res = await apiFetch(
      `/api/ontology/domain-schema/${encodeURIComponent(selectedDomain.value)}/pending-review/apply`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes }),
      }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    acceptedDomainReviewIds.value = new Set()
    await loadDomainSchema(selectedDomain.value)
  } catch (err) {
    domainReviewError.value = '변경 반영 실패: ' + err.message
  } finally {
    isApplyingDomainReview.value = false
  }
}

async function useDomainSchemaForCurrentFile() {
  if (!props.selectedFilename || !selectedDomain.value) return
  isUsingDomainSchema.value = true
  domainUseError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.selectedFilename)}/schema/use-domain`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: selectedDomain.value }),
      }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    emit('schema-used')
    showDomainSchema.value = false
  } catch (err) {
    domainUseError.value = '스키마 적용 실패: ' + err.message
  } finally {
    isUsingDomainSchema.value = false
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

          <div>
            <button
              type="button"
              class="btn w-full"
              :disabled="!selectedFilename || isDiscovering"
              @click="discoverOntology"
            >
              {{ isDiscovering ? '발견 중...' : '온톨로지 발견' }}
            </button>
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              문서에서 후보 개념·관계·속성·이벤트·규칙을 탐색적으로 도출합니다. 스키마 생성과는 별개의
              참고용 보고서이며, 자동으로 스키마에 반영되지 않습니다.
            </p>
            <p v-if="discoveryError" class="mt-1 text-[11px] text-red-400">{{ discoveryError }}</p>
            <button
              v-if="discoveryReport && !showDiscoveryReport"
              type="button"
              class="mt-1 text-[11px] text-accent hover:underline"
              @click="showDiscoveryReport = true"
            >
              발견 결과 다시 보기
            </button>
          </div>

          <div>
            <button type="button" class="btn w-full" @click="showDomainSchema = true">
              도메인 스키마
            </button>
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              보험 약관처럼 같은 도메인의 여러 문서에 공통으로 쓸 스키마를 문서별로 따로 만들지 않고
              점진적으로 수렴시키고, 그 결과를 문서에 재사용합니다.
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
          <label
            v-if="discoveryReport"
            class="mt-1 flex items-center gap-1.5 text-[11px] text-ink-muted"
          >
            <input
              type="checkbox"
              v-model="useDiscoveryForSchema"
              class="h-3.5 w-3.5 rounded border-border bg-surface-sunken accent-accent"
            />
            발견 결과 참고하여 생성 (최종 판단은 문서 본문 기준)
          </label>
          <p v-if="activeVersionLabel" class="mt-1 text-[11px] text-ink-faint">{{ activeVersionLabel }}</p>

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

          <section class="mb-5">
            <h3 class="mb-1.5 text-[10px] uppercase tracking-wide text-ink-faint">선택된 문서의 스키마 버전</h3>
            <p v-if="!selectedFilename" class="text-[11px] text-ink-faint">문서를 먼저 선택하세요</p>
            <p v-else-if="schemaVersions.length === 0" class="text-[11px] text-ink-faint">생성된 버전이 없습니다</p>
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
            <p
              v-if="maxTokens"
              class="mt-1 text-[11px] text-ink-muted"
            >
              max tokens: {{ maxTokens.toLocaleString('en-US') }}
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
        <div class="flex flex-shrink-0 items-center justify-between gap-2 border-t border-border px-4 py-2.5">
          <p v-if="evolutionError" class="text-[11px] text-red-400">{{ evolutionError }}</p>
          <span v-else></span>
          <button
            type="button"
            class="btn-primary"
            :disabled="isProposingEvolution"
            @click="proposeEvolution"
          >
            {{ isProposingEvolution ? '개선안 도출 중...' : '이 보고서로 개선안 도출' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="showEvolutionReview && evolutionProposal"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showEvolutionReview = false"
    >
      <div class="flex max-h-[85vh] w-[720px] max-w-[92vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">스키마/그래프 개선안 검토</h2>
          <button type="button" class="btn" @click="showEvolutionReview = false">닫기</button>
        </div>
        <div class="flex-1 space-y-2 overflow-y-auto p-4">
          <p class="mb-1 text-[11px] leading-snug text-ink-faint">
            체크한 항목만 새 스키마 버전으로 반영됩니다. 기존 버전은 그대로 남아 언제든 되돌릴 수 있습니다.
            {{ evolutionProposal.evolution_summary?.human_review_required
              ? '검토가 필요한(NEEDS_HUMAN_REVIEW) 항목은 기본적으로 선택되어 있지 않습니다.'
              : '' }}
          </p>
          <div
            v-for="change in evolutionProposal.changes"
            :key="change.change_id"
            class="rounded-md border border-border bg-surface-sunken p-2.5"
          >
            <label class="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                :checked="acceptedChangeIds.has(change.change_id)"
                @change="toggleChangeAccepted(change.change_id)"
                class="mt-0.5 h-3.5 w-3.5 flex-shrink-0 rounded border-border bg-surface-sunken accent-accent"
              />
              <div class="min-w-0 flex-1">
                <div class="mb-1 flex flex-wrap items-center gap-1.5">
                  <span class="chip" :class="decisionClass(change.decision)">{{ change.decision }}</span>
                  <span class="text-[10px] uppercase tracking-wide text-ink-faint">{{ change.element_type }}</span>
                  <span class="text-[10px] text-ink-faint">{{ change.confidence }}</span>
                </div>
                <p class="text-xs font-medium text-ink">{{ changeSummary(change) }}</p>
                <p v-if="change.reason" class="mt-1 text-[11px] text-ink-muted">{{ change.reason }}</p>
                <p v-if="change.evidence" class="mt-1 text-[11px] italic text-ink-faint">"{{ change.evidence }}"</p>
              </div>
            </label>
          </div>
        </div>
        <div class="flex flex-shrink-0 items-center justify-between gap-2 border-t border-border px-4 py-2.5">
          <div class="min-w-0">
            <p v-if="evolutionApplyError" class="text-[11px] text-red-400">{{ evolutionApplyError }}</p>
            <p v-else-if="evolutionApplyMessage" class="text-[11px] text-emerald-400">{{ evolutionApplyMessage }}</p>
            <p v-else class="text-[11px] text-ink-faint">{{ acceptedChangeIds.size }}개 선택됨</p>
          </div>
          <button
            type="button"
            class="btn-primary"
            :disabled="isApplyingEvolution || acceptedChangeIds.size === 0"
            @click="applyEvolution"
          >
            {{ isApplyingEvolution ? '반영 중...' : '선택한 개선안 반영' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="showDiscoveryReport && discoveryReport"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showDiscoveryReport = false"
    >
      <div class="flex max-h-[85vh] w-[760px] max-w-[92vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">온톨로지 발견 결과 (후보안)</h2>
          <button type="button" class="btn" @click="showDiscoveryReport = false">닫기</button>
        </div>
        <div class="flex-1 space-y-5 overflow-y-auto p-4">
          <div v-if="discoveryReport.domain_model">
            <h3 class="section-label">도메인</h3>
            <p class="mb-1 text-xs text-ink">{{ discoveryReport.domain_model.domain }}</p>
            <div class="space-y-1 text-[11px] text-ink-muted">
              <p v-if="discoveryReport.domain_model.subdomains?.length">
                하위 도메인: {{ discoveryReport.domain_model.subdomains.join(', ') }}
              </p>
              <p v-if="discoveryReport.domain_model.document_types?.length">
                문서 유형: {{ discoveryReport.domain_model.document_types.join(', ') }}
              </p>
              <p v-if="discoveryReport.domain_model.business_processes?.length">
                업무 프로세스: {{ discoveryReport.domain_model.business_processes.join(', ') }}
              </p>
              <p v-if="discoveryReport.domain_model.major_actors?.length">
                주요 행위자: {{ discoveryReport.domain_model.major_actors.join(', ') }}
              </p>
            </div>
          </div>

          <div v-if="discoveryClasses.length">
            <h3 class="section-label">후보 클래스 ({{ discoveryClasses.length }}개)</h3>
            <div class="space-y-1.5">
              <div
                v-for="(c, i) in discoveryClasses"
                :key="i"
                class="rounded-md border border-border bg-surface-sunken p-2.5"
              >
                <div class="mb-1 flex flex-wrap items-center gap-1.5">
                  <span class="text-xs font-medium text-ink">{{ c.name }}</span>
                  <span class="chip border-border bg-white/5 text-ink-muted">{{ c.category }}</span>
                  <span v-if="c.parent" class="text-[10px] text-ink-faint">parent: {{ c.parent }}</span>
                  <span class="text-[10px] text-ink-faint">{{ c.confidence }}</span>
                </div>
                <p class="text-[11px] text-ink-muted">{{ c.definition }}</p>
                <p v-if="c.rationale" class="mt-1 text-[11px] italic text-ink-faint">{{ c.rationale }}</p>
              </div>
            </div>
          </div>

          <div v-if="discoveryRelationships.length">
            <h3 class="section-label">후보 관계 ({{ discoveryRelationships.length }}개)</h3>
            <div class="space-y-1.5">
              <div
                v-for="(r, i) in discoveryRelationships"
                :key="i"
                class="rounded-md border border-border bg-surface-sunken p-2.5"
              >
                <div class="mb-1 flex flex-wrap items-center gap-1.5">
                  <span class="text-xs font-medium text-ink">{{ r.source }} → {{ r.target }} ({{ r.name }})</span>
                  <span class="chip border-border bg-white/5 text-ink-muted">{{ r.category }}</span>
                  <span class="text-[10px] text-ink-faint">{{ r.confidence }}</span>
                </div>
                <p class="text-[11px] text-ink-muted">{{ r.definition }}</p>
              </div>
            </div>
          </div>

          <div v-if="discoveryReport.attributes?.length">
            <h3 class="section-label">후보 속성</h3>
            <ul class="space-y-1 text-xs text-ink">
              <li v-for="(a, i) in discoveryReport.attributes" :key="i">
                <span class="font-medium">{{ a.defined_on }}.{{ a.name }}</span>
                <span class="text-ink-muted"> ({{ a.datatype }}{{ a.unit ? `, ${a.unit}` : '' }}{{ a.required ? ', 필수' : '' }}) — {{ a.definition }}</span>
              </li>
            </ul>
          </div>

          <div v-if="discoveryReport.events?.length">
            <h3 class="section-label">후보 이벤트</h3>
            <ul class="space-y-1 text-xs text-ink">
              <li v-for="(e, i) in discoveryReport.events" :key="i">
                <span class="font-medium">{{ e.name }}</span>
                <span class="text-ink-muted"> — {{ e.definition }}</span>
                <span v-if="e.affected_entities?.length" class="text-[11px] text-ink-faint">
                  ({{ e.affected_entities.join(', ') }})
                </span>
              </li>
            </ul>
          </div>

          <div v-if="discoveryReport.rules?.length">
            <h3 class="section-label">후보 규칙</h3>
            <div class="space-y-1.5">
              <div
                v-for="(r, i) in discoveryReport.rules"
                :key="i"
                class="rounded-md border border-border bg-surface-sunken p-2.5"
              >
                <p class="text-xs font-medium text-ink">{{ r.name }}</p>
                <p class="mt-1 text-[11px] text-ink-muted">{{ r.description }}</p>
                <p v-if="r.conditions?.length" class="mt-1 text-[11px] text-ink-faint">조건: {{ r.conditions.join(', ') }}</p>
                <p v-if="r.consequences?.length" class="mt-1 text-[11px] text-ink-faint">결과: {{ r.consequences.join(', ') }}</p>
                <p v-if="r.exceptions?.length" class="mt-1 text-[11px] text-ink-faint">예외: {{ r.exceptions.join(', ') }}</p>
              </div>
            </div>
          </div>

          <div v-if="discoveryReport.terminology?.length">
            <h3 class="section-label">용어 매핑</h3>
            <ul class="space-y-1 text-xs text-ink">
              <li v-for="(t, i) in discoveryReport.terminology" :key="i">
                <span class="font-medium">{{ t.canonical_term }}</span>
                <span v-if="t.synonyms?.length" class="text-ink-muted"> = {{ t.synonyms.join(', ') }}</span>
                <span v-if="t.abbreviations?.length" class="text-[11px] text-ink-faint"> ({{ t.abbreviations.join(', ') }})</span>
              </li>
            </ul>
          </div>

          <div v-if="discoveryReport.competency_questions?.length">
            <h3 class="section-label">역량 질문</h3>
            <ul class="list-disc space-y-1 pl-4 text-xs text-ink">
              <li v-for="(q, i) in discoveryReport.competency_questions" :key="i">{{ q }}</li>
            </ul>
          </div>

          <div v-if="discoveryReport.warnings?.length">
            <h3 class="section-label">주의/검증 필요</h3>
            <ul class="list-disc space-y-1 pl-4 text-xs text-amber-400">
              <li v-for="(w, i) in discoveryReport.warnings" :key="i">{{ w }}</li>
            </ul>
          </div>
        </div>
        <div class="flex flex-shrink-0 items-center justify-between gap-2 border-t border-border px-4 py-2.5">
          <p class="text-[11px] leading-snug text-ink-faint">
            이 결과는 후보안입니다. 아래에서 "발견 결과 참고하여 생성"을 켠 뒤 스키마 생성을 실행하면
            참고 자료로만 반영되며, 최종 스키마는 여전히 문서 본문을 근거로 판단됩니다.
          </p>
          <label class="flex flex-shrink-0 items-center gap-1.5 text-[11px] text-ink-muted">
            <input
              type="checkbox"
              v-model="useDiscoveryForSchema"
              class="h-3.5 w-3.5 rounded border-border bg-surface-sunken accent-accent"
            />
            참고하여 생성
          </label>
        </div>
      </div>
    </div>

    <div
      v-if="showDomainSchema"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showDomainSchema = false"
    >
      <div class="flex max-h-[85vh] w-[720px] max-w-[92vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">도메인 스키마</h2>
          <button type="button" class="btn" @click="showDomainSchema = false">닫기</button>
        </div>
        <div class="flex-1 space-y-5 overflow-y-auto p-4">
          <section>
            <h3 class="section-label">도메인 선택</h3>
            <select v-model="selectedDomain" class="field w-full" @change="onSelectDomain">
              <option value="">새 도메인...</option>
              <option v-for="d in domains" :key="d" :value="d">{{ d }}</option>
            </select>
            <input
              v-if="!selectedDomain"
              v-model="newDomainName"
              type="text"
              placeholder="새 도메인 이름 (예: insurance_policy)"
              class="field mt-1.5 w-full"
            />
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              기존 도메인을 고르면 저장된 스키마를 시드로 계속 다듬고, 새 도메인 이름을 입력하면
              아래에서 고른 첫 문서로 새로 시작합니다.
            </p>
          </section>

          <section v-if="selectedDomain && domainSchema">
            <h3 class="section-label">현재 도메인 스키마</h3>
            <p class="mb-1.5 text-[11px] text-ink-faint">
              캘리브레이션 문서 {{ domainSchema.calibration_stems.length }}개 · 실행 이력
              {{ domainSchema.history.length }}회
            </p>
            <div class="flex flex-wrap gap-1.5">
              <span
                v-for="t in domainSchema.node_types"
                :key="t.name"
                class="chip border-border bg-white/5 text-ink-muted"
              >{{ t.name }}</span>
              <span
                v-for="t in domainSchema.edge_types"
                :key="t.name"
                class="chip border-sky-500/40 bg-sky-500/10 text-sky-400"
              >{{ t.name }}</span>
            </div>
          </section>

          <section>
            <h3 class="section-label">캘리브레이션 문서 선택</h3>
            <p v-if="files.length === 0" class="text-[11px] text-ink-faint">문서가 없습니다</p>
            <ul v-else class="max-h-40 space-y-0.5 overflow-y-auto rounded-md border border-border p-1.5">
              <li v-for="f in files" :key="f.filename">
                <label class="flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-white/5">
                  <input
                    type="checkbox"
                    :checked="selectedCalibrationFiles.has(f.filename)"
                    @change="toggleCalibrationFile(f.filename)"
                    class="h-3.5 w-3.5 flex-shrink-0 rounded border-border bg-surface-sunken accent-accent"
                  />
                  <span class="break-all text-ink-muted">{{ f.original_filename }}</span>
                </label>
              </li>
            </ul>
            <button
              type="button"
              class="btn-primary mt-2 w-full"
              :disabled="isConverging || selectedCalibrationFiles.size === 0 || (!selectedDomain && !newDomainName.trim())"
              @click="runDomainConvergence"
            >
              {{ isConverging ? '수렴 실행 중...' : '선택한 문서로 수렴 실행' }}
            </button>
            <p class="mt-1 text-[11px] leading-snug text-ink-faint">
              체크한 문서를 순서대로 반영해 도메인 스키마를 진화시킵니다. 문서 수가 많을수록 LLM
              호출이 문서당 여러 번 발생하니 대표 문서 위주로 고르세요.
            </p>
            <p v-if="convergeError" class="mt-1 text-[11px] text-red-400">{{ convergeError }}</p>
            <p v-if="convergeMessage" class="mt-1 text-[11px] text-emerald-400">{{ convergeMessage }}</p>
          </section>

          <section v-if="domainEvaluation">
            <h3 class="section-label">평가 지표</h3>
            <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-ink-muted">
              <div>
                평균 이슈 수:
                <span class="text-ink">{{ domainEvaluation.coverage.avg_issue_count.toFixed(2) }}</span>
              </div>
              <div>
                평균 누락 요소 수:
                <span class="text-ink">{{ domainEvaluation.coverage.avg_missing_element_count.toFixed(2) }}</span>
              </div>
              <div>
                QA 성공률:
                <span class="text-ink">
                  {{ domainEvaluation.qa_success_rate != null
                    ? (domainEvaluation.qa_success_rate * 100).toFixed(0) + '%'
                    : '—' }}
                </span>
              </div>
            </div>
            <div v-if="Object.keys(domainEvaluation.type_utilization).length" class="mt-2">
              <h4 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">
                타입 활용도 (해당 타입이 인스턴스를 가진 캘리브레이션 문서 비율)
              </h4>
              <div class="space-y-0.5">
                <div
                  v-for="(v, name) in domainEvaluation.type_utilization"
                  :key="name"
                  class="flex items-center justify-between text-[11px]"
                >
                  <span class="text-ink-muted">{{ name }}</span>
                  <span class="text-ink">{{ (v * 100).toFixed(0) }}%</span>
                </div>
              </div>
            </div>
          </section>

          <section v-if="domainSchema?.pending_review?.length">
            <h3 class="section-label">검토 대기 중인 변경 ({{ domainSchema.pending_review.length }}건)</h3>
            <p class="mb-1.5 text-[11px] leading-snug text-ink-faint">
              사람의 검토가 필요하다고 판단된(NEEDS_HUMAN_REVIEW) 변경입니다. 체크한 항목만 도메인
              스키마에 반영됩니다.
            </p>
            <div class="space-y-2">
              <div
                v-for="change in domainSchema.pending_review"
                :key="change.change_id"
                class="rounded-md border border-border bg-surface-sunken p-2.5"
              >
                <label class="flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    :checked="acceptedDomainReviewIds.has(change.change_id)"
                    @change="toggleDomainReviewAccepted(change.change_id)"
                    class="mt-0.5 h-3.5 w-3.5 flex-shrink-0 rounded border-border bg-surface-sunken accent-accent"
                  />
                  <div class="min-w-0 flex-1">
                    <div class="mb-1 flex flex-wrap items-center gap-1.5">
                      <span class="chip" :class="decisionClass(change.decision)">{{ change.decision }}</span>
                      <span class="text-[10px] uppercase tracking-wide text-ink-faint">{{ change.element_type }}</span>
                      <span class="text-[10px] text-ink-faint">출처: {{ change.stem }}</span>
                    </div>
                    <p class="text-xs font-medium text-ink">{{ changeSummary(change) }}</p>
                    <p v-if="change.reason" class="mt-1 text-[11px] text-ink-muted">{{ change.reason }}</p>
                    <p v-if="change.evidence" class="mt-1 text-[11px] italic text-ink-faint">"{{ change.evidence }}"</p>
                  </div>
                </label>
              </div>
            </div>
            <button
              type="button"
              class="btn-primary mt-2"
              :disabled="isApplyingDomainReview || acceptedDomainReviewIds.size === 0"
              @click="applyDomainReview"
            >
              {{ isApplyingDomainReview ? '반영 중...' : '선택한 변경 반영' }}
            </button>
            <p v-if="domainReviewError" class="mt-1 text-[11px] text-red-400">{{ domainReviewError }}</p>
          </section>
        </div>
        <div class="flex flex-shrink-0 items-center justify-between gap-2 border-t border-border px-4 py-2.5">
          <p v-if="domainUseError" class="text-[11px] text-red-400">{{ domainUseError }}</p>
          <p v-else class="text-[11px] text-ink-faint">
            {{ selectedFilename ? '' : '문서를 먼저 선택하면 이 도메인 스키마를 적용할 수 있습니다' }}
          </p>
          <button
            type="button"
            class="btn-primary"
            :disabled="!selectedFilename || !selectedDomain || isUsingDomainSchema"
            @click="useDomainSchemaForCurrentFile"
          >
            {{ isUsingDomainSchema ? '적용 중...' : '현재 문서에 이 도메인 스키마 적용' }}
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>
