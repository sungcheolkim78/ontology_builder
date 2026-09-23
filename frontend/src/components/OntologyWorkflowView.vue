<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'
import { finishTask, startTask } from '../utils/taskStatus.js'
import OntologyGraph from './OntologyGraph.vue'
import SchemaGraphPreview from './SchemaGraphPreview.vue'

const props = defineProps({
  file: { type: Object, default: null },
  availableTypes: { type: Array, default: () => [] },
  availableEdgeTypes: { type: Array, default: () => [] },
  schemaVersion: { type: Number, default: 0 },
  toggleTypeRequest: { type: Object, default: null },
  toggleEdgeTypeRequest: { type: Object, default: null },
  schemaRefreshRequest: { type: Object, default: null },
  highlightedNodeIds: { type: Array, default: () => [] },
})
const emit = defineEmits([
  'schema-generated',
  'schema-used',
  'graph-extracted',
  'filters-changed',
  'edge-filters-changed',
  'types-available',
  'edge-types-available',
])

// --- current-file has_graph/has_chunks lookup (see plan Task 5 Step 5) ---
const currentFileHasGraph = ref(false)

async function loadCurrentFileFlags(filename) {
  currentFileHasGraph.value = false
  if (!filename) return
  try {
    const res = await apiFetch('/api/documents')
    const data = await res.json()
    currentFileHasGraph.value = !!data.documents.find((f) => f.filename === filename)?.has_graph
  } catch (err) {
    // best-effort; buttons just stay disabled on failure
  }
}

watch(() => props.file?.filename, loadCurrentFileFlags, { immediate: true })
watch(() => props.schemaVersion, () => loadCurrentFileFlags(props.file?.filename))

// --- node/edge type filters (feeds the graph, lives next to it now) ---
const enabledTypes = ref(new Set(props.availableTypes))
const enabledEdgeTypes = ref(new Set(props.availableEdgeTypes))

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

// --- node/edge inspector (new) ---
const inspectedNode = ref(null)
const inspectedEdge = ref(null)

function onNodeSelected(node) {
  inspectedNode.value = node
  inspectedEdge.value = null
}
function onEdgeSelected(edge) {
  inspectedEdge.value = edge
  inspectedNode.value = null
}

// --- core pipeline: discover -> schema -> extract (+embed) -> validate -> evolve ---
const maxSchemaChars = ref(1000000)
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

// Polls GET /api/ontology/{filename}/progress?operation=... (backed by
// app.ontology.utils.ChunkProgress -- see backend) while discover/schema/
// extract are in flight, instead of only showing a locally-ticking clock.
// 404 just means "nothing recorded yet" (the POST hasn't reached its first
// group, or never wrote one for a whole-document call that's still
// mid-flight) -- not a real failure, so it's swallowed rather than shown.
const progressState = ref(null)
let progressTimer = null

function startProgressPolling(operation) {
  stopProgressPolling()
  progressState.value = null
  const poll = async () => {
    try {
      const res = await apiFetch(
        `/api/ontology/${encodeURIComponent(props.file.filename)}/progress?operation=${operation}`
      )
      progressState.value = res.ok ? await res.json() : null
    } catch (err) {
      // best-effort; a polling hiccup shouldn't interrupt the main request
    }
  }
  poll()
  progressTimer = setInterval(poll, 1200)
}

function stopProgressPolling() {
  clearInterval(progressTimer)
  progressTimer = null
  progressState.value = null
}

const STAGE_LABELS = { reduce: ' · 통합 중', merge: ' · 병합 중' }

function progressSuffix() {
  const p = progressState.value
  if (!p) return ''
  const groupPart = p.total > 1 ? ` (그룹 ${p.completed}/${p.total}${STAGE_LABELS[p.stage] ?? ''})` : ''
  const countsPart = p.nodes != null ? ` · 노드 ${p.nodes}개 · 엣지 ${p.edges ?? 0}개` : ''
  return groupPart + countsPart
}

const workflowProgress = computed(() => {
  if (isGeneratingSchema.value) return `문서를 읽어 스키마 생성 중...${progressSuffix()} ${elapsedSeconds.value}초`
  if (isExtracting.value) return `문서를 읽고 주어진 스키마로 노드와 에지를 생성 중...${progressSuffix()} ${elapsedSeconds.value}초`
  if (isEmbedding.value) return `노드 임베딩 생성 중... ${elapsedSeconds.value}초`
  if (isValidating.value) return `문서와 스키마, 추출된 그래프를 검토하여 보고서 작성 중... ${elapsedSeconds.value}초`
  if (isProposingEvolution.value) return `검증 보고서를 바탕으로 개선안을 도출하는 중... ${elapsedSeconds.value}초`
  if (isDiscovering.value) return `문서에서 후보 온톨로지(개념/관계/속성/이벤트/규칙)를 발견하는 중...${progressSuffix()} ${elapsedSeconds.value}초`
  return ''
})

onUnmounted(() => {
  stopElapsedTimer()
  stopProgressPolling()
})

function onMaxSchemaCharsInput(event) {
  maxSchemaChars.value = Math.max(1, Number(event.target.value) || 1000000)
}

async function discoverOntology() {
  if (!props.file?.filename) return
  isDiscovering.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  discoveryError.value = ''
  startElapsedTimer()
  startProgressPolling('discover')
  const taskId = startTask(`온톨로지 발견 — ${props.file.filename}`)
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/discover`, {
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
    finishTask(taskId, { status: 'success', message: '온톨로지 발견이 완료되었습니다.' })
  } catch (err) {
    discoveryError.value = '온톨로지 발견 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: discoveryError.value })
  } finally {
    isDiscovering.value = false
    stopElapsedTimer()
    stopProgressPolling()
  }
}

async function loadDiscovery() {
  useDiscoveryForSchema.value = false
  if (!props.file?.filename) {
    discoveryReport.value = null
    return
  }
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/discover`)
    discoveryReport.value = res.ok ? await res.json() : null
  } catch (err) {
    discoveryReport.value = null
  }
}

const discoveryClasses = computed(() => discoveryReport.value?.classes ?? [])
const discoveryRelationships = computed(() => discoveryReport.value?.relationships ?? [])

async function generateSchema() {
  if (!props.file?.filename) return
  isGeneratingSchema.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  startElapsedTimer()
  startProgressPolling('schema')
  const taskId = startTask(`스키마 생성 — ${props.file.filename}`)
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/schema`, {
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
    finishTask(taskId, { status: 'success', message: workflowMessage.value })
  } catch (err) {
    workflowError.value = '스키마 생성 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: workflowError.value })
  } finally {
    isGeneratingSchema.value = false
    stopElapsedTimer()
    stopProgressPolling()
  }
}

async function extractGraph() {
  if (!props.file?.filename) return
  isExtracting.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  startElapsedTimer()
  startProgressPolling('extract')
  const taskId = startTask(`그래프 추출 — ${props.file.filename}`)
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/extract`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const graph = await res.json()
    workflowMessage.value = `그래프 추출 완료 (노드 ${graph.nodes.length}개, 엣지 ${graph.edges.length}개)`
    emit('graph-extracted')
    finishTask(taskId, { status: 'success', message: workflowMessage.value })
  } catch (err) {
    workflowError.value = '그래프 추출 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: workflowError.value })
  } finally {
    isExtracting.value = false
    stopElapsedTimer()
    stopProgressPolling()
  }
}

async function embed() {
  if (!props.file?.filename) return
  isEmbedding.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  startElapsedTimer()
  const taskId = startTask(`임베딩 생성 — ${props.file.filename}`)
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/embed`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const result = await res.json()
    workflowMessage.value = `임베딩 생성 완료 (노드 ${result.embedded}개)`
    finishTask(taskId, { status: 'success', message: workflowMessage.value })
  } catch (err) {
    workflowError.value = '임베딩 생성 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: workflowError.value })
  } finally {
    isEmbedding.value = false
    stopElapsedTimer()
  }
}

async function validateOntology() {
  if (!props.file?.filename) return
  isValidating.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  validationError.value = ''
  startElapsedTimer()
  const taskId = startTask(`온톨로지 검증 — ${props.file.filename}`)
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/validate`, {
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
    finishTask(taskId, { status: 'success', message: '온톨로지 검증이 완료되었습니다.' })
  } catch (err) {
    validationError.value = '온톨로지 검증 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: validationError.value })
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
  if (!props.file?.filename || !validationReport.value) return
  isProposingEvolution.value = true
  workflowError.value = ''
  workflowMessage.value = ''
  evolutionError.value = ''
  evolutionApplyError.value = ''
  evolutionApplyMessage.value = ''
  startElapsedTimer()
  const taskId = startTask(`개선안 도출 — ${props.file.filename}`)
  try {
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/evolve`, {
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
    finishTask(taskId, { status: 'success', message: '개선안 도출이 완료되었습니다.' })
  } catch (err) {
    evolutionError.value = '개선안 도출 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: evolutionError.value })
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
  if (!props.file?.filename || !evolutionProposal.value) return
  isApplyingEvolution.value = true
  evolutionApplyError.value = ''
  evolutionApplyMessage.value = ''
  const taskId = startTask(`개선안 반영 — ${props.file.filename}`)
  try {
    const changes = evolutionProposal.value.changes.filter((c) => acceptedChangeIds.value.has(c.change_id))
    const res = await apiFetch(`/api/ontology/${encodeURIComponent(props.file.filename)}/evolve/apply`, {
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
    finishTask(taskId, { status: 'success', message: evolutionApplyMessage.value })
  } catch (err) {
    evolutionApplyError.value = '개선안 반영 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: evolutionApplyError.value })
  } finally {
    isApplyingEvolution.value = false
  }
}

const DECISION_STYLES = {
  ADD: 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  MODIFY: 'border-sky-500/50 bg-sky-500/15 text-sky-400',
  MERGE: 'border-violet-500/50 bg-violet-500/15 text-violet-700 dark:text-violet-400',
  DEPRECATE: 'border-amber-500/50 bg-amber-500/15 text-amber-700 dark:text-amber-400',
  REJECT: 'border-border bg-ink/5 text-ink-faint',
  NEEDS_HUMAN_REVIEW: 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400',
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
  CRITICAL: 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400',
  HIGH: 'border-orange-500/50 bg-orange-500/15 text-orange-700 dark:text-orange-400',
  MEDIUM: 'border-amber-500/50 bg-amber-500/15 text-amber-700 dark:text-amber-400',
  LOW: 'border-sky-500/50 bg-sky-500/15 text-sky-400',
  INFO: 'border-border bg-ink/5 text-ink-muted',
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

watch(() => props.file?.filename, loadDiscovery)

// --- Advanced: goldenset generation ---
const isGeneratingGoldenset = ref(false)
const goldensetError = ref('')
const goldensetReport = ref(null)
const showGoldensetReport = ref(false)

async function createGoldenset() {
  if (!props.file?.filename) return
  isGeneratingGoldenset.value = true
  goldensetError.value = ''
  const taskId = startTask(`골든셋 작성 — ${props.file.filename}`)
  try {
    const res = await apiFetch(
      `/api/documents/${encodeURIComponent(props.file.filename)}/goldenset`,
      { method: 'POST' }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    goldensetReport.value = await res.json()
    showGoldensetReport.value = true
    finishTask(taskId, { status: 'success', message: '골든셋 작성이 완료되었습니다.' })
  } catch (err) {
    goldensetError.value = '골든셋 작성 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: goldensetError.value })
  } finally {
    isGeneratingGoldenset.value = false
  }
}

async function loadGoldenset() {
  if (!props.file?.filename) {
    goldensetReport.value = null
    return
  }
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(props.file.filename)}/goldenset`)
    goldensetReport.value = res.ok ? await res.json() : null
  } catch (err) {
    goldensetReport.value = null
  }
}
watch(() => props.file?.filename, loadGoldenset, { immediate: true })

// --- Advanced: domain-schema convergence ---
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
// The per-document upload list this section's calibration-file checkboxes
// pick from -- Advanced's own copy, independent of FileExplorerView.vue's,
// consistent with this app's existing per-view-fetches-its-own-data pattern.
const domainFiles = ref([])

async function loadDomainFiles() {
  try {
    const res = await apiFetch('/api/documents')
    const data = await res.json()
    domainFiles.value = data.documents
  } catch (err) {
    // best-effort
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
  const taskId = startTask(`도메인 스키마 수렴 — ${domain}`)
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
    finishTask(taskId, { status: 'success', message: convergeMessage.value })
  } catch (err) {
    convergeError.value = '수렴 실행 실패: ' + err.message
    finishTask(taskId, { status: 'error', message: convergeError.value })
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
  if (!props.file?.filename || !selectedDomain.value) return
  isUsingDomainSchema.value = true
  domainUseError.value = ''
  try {
    const res = await apiFetch(
      `/api/ontology/${encodeURIComponent(props.file.filename)}/schema/use-domain`,
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
  } catch (err) {
    domainUseError.value = '스키마 적용 실패: ' + err.message
  } finally {
    isUsingDomainSchema.value = false
  }
}

onMounted(async () => {
  await loadDiscovery()
  await loadDomains()
  await loadDomainFiles()
})

// --- resizable split between the "온톨로지 그래프" / "스키마·그래프DB" columns ---
// Both used to be plain flex-1 siblings, which puts the divide wherever the
// browser's flex algorithm lands rather than guaranteeing it starts at the
// midpoint of whatever width is actually available for the row -- the left
// filter sidebar and the floating node/edge inspector panel both eat into
// that width first. splitPercent is the graph column's share of the row's
// own width (not the viewport), so it always starts at a true 50/50 of
// whatever's actually on screen, and dragging the bar changes that share
// directly instead of fighting flex-basis auto-sizing from wide table
// content in SchemaGraphPreview.
const splitRow = ref(null)
const splitPercent = ref(50)
const isDraggingSplit = ref(false)
const MIN_SPLIT_PERCENT = 20
const MAX_SPLIT_PERCENT = 80

function onSplitPointerDown() {
  isDraggingSplit.value = true
  window.addEventListener('pointermove', onSplitPointerMove)
  window.addEventListener('pointerup', onSplitPointerUp)
}

function onSplitPointerMove(event) {
  if (!splitRow.value) return
  const rect = splitRow.value.getBoundingClientRect()
  const percent = ((event.clientX - rect.left) / rect.width) * 100
  splitPercent.value = Math.min(MAX_SPLIT_PERCENT, Math.max(MIN_SPLIT_PERCENT, percent))
}

function onSplitPointerUp() {
  isDraggingSplit.value = false
  window.removeEventListener('pointermove', onSplitPointerMove)
  window.removeEventListener('pointerup', onSplitPointerUp)
}

onUnmounted(() => {
  window.removeEventListener('pointermove', onSplitPointerMove)
  window.removeEventListener('pointerup', onSplitPointerUp)
})
</script>

<template>
  <section class="flex h-full overflow-hidden">
    <!-- Step sidebar -->
    <aside class="flex w-[280px] flex-shrink-0 flex-col overflow-y-auto border-r border-border bg-surface px-3 py-3">
      <h2 class="section-label">온톨로지 파이프라인</h2>
      <div class="space-y-2.5">
        <div>
          <button
            type="button"
            class="btn w-full"
            :disabled="!file || isDiscovering"
            @click="discoverOntology"
          >
            {{ isDiscovering ? '발견 중...' : '1. 온톨로지 발견' }}
          </button>
          <p class="mt-1 text-[11px] leading-snug text-ink-faint">
            문서에서 후보 개념·관계·속성·이벤트·규칙을 탐색적으로 도출합니다. 스키마 생성과는 별개의
            참고용 보고서이며, 자동으로 스키마에 반영되지 않습니다.
          </p>
          <p v-if="discoveryError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ discoveryError }}</p>
          <button
            v-if="discoveryReport && !showDiscoveryReport"
            type="button"
            class="mt-1 text-[11px] text-accent hover:underline"
            @click="showDiscoveryReport = true"
          >
            발견 결과 다시 보기
          </button>
        </div>

        <div class="flex items-center gap-1.5">
          <button
            type="button"
            class="btn flex-1"
            :disabled="!file || isGeneratingSchema"
            @click="generateSchema"
          >
            {{ isGeneratingSchema ? '생성 중...' : '2. 스키마 생성' }}
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
          class="flex items-center gap-1.5 text-[11px] text-ink-muted"
        >
          <input
            type="checkbox"
            v-model="useDiscoveryForSchema"
            class="h-3.5 w-3.5 rounded border-border bg-surface-sunken accent-accent"
          />
          발견 결과 참고하여 생성 (최종 판단은 문서 본문 기준)
        </label>

        <button
          type="button"
          class="btn w-full"
          :disabled="!file || isExtracting"
          @click="extractGraph"
        >
          {{ isExtracting ? '추출 중...' : '3. 그래프 추출' }}
        </button>
        <button
          type="button"
          class="btn w-full"
          :disabled="!file || isEmbedding || !currentFileHasGraph"
          @click="embed"
        >
          {{ isEmbedding ? '임베딩 생성 중...' : '임베딩 생성' }}
        </button>

        <button
          type="button"
          class="btn w-full"
          :disabled="!file || isValidating || !currentFileHasGraph"
          @click="validateOntology"
        >
          {{ isValidating ? '검증 중...' : '4. 온톨로지 검증' }}
        </button>
        <button
          v-if="validationReport && !showValidationReport"
          type="button"
          class="text-[11px] text-accent hover:underline"
          @click="showValidationReport = true"
        >
          마지막 검증 보고서 다시 보기
        </button>

        <p v-if="workflowProgress" class="text-[11px] italic text-ink-muted">{{ workflowProgress }}</p>
        <p v-if="workflowMessage" class="text-[11px] text-emerald-700 dark:text-emerald-400">{{ workflowMessage }}</p>
        <p v-if="workflowError" class="text-[11px] text-red-600 dark:text-red-400">{{ workflowError }}</p>
        <p v-if="validationError" class="text-[11px] text-red-600 dark:text-red-400">{{ validationError }}</p>
      </div>

      <details class="mt-4 border-t border-border pt-3">
        <summary class="cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-ink-faint">
          Advanced
        </summary>
        <div class="mt-2.5 space-y-4">
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
            <div class="mb-1 flex items-center justify-between">
              <span class="text-[10px] uppercase tracking-wide text-ink-faint">골든셋</span>
              <button
                type="button"
                class="btn px-2 py-0.5 text-[11px]"
                :disabled="!file || isGeneratingGoldenset"
                @click="createGoldenset"
              >{{ isGeneratingGoldenset ? '작성 중...' : '골든셋 작성' }}</button>
            </div>
            <p class="text-[11px] leading-snug text-ink-faint">
              문서 전체를 기준으로 질문·정답·근거 인용을 생성합니다 (청크 단위가 아닌 문서 전체 기준).
            </p>
            <p v-if="goldensetError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ goldensetError }}</p>
            <button
              v-if="goldensetReport && !showGoldensetReport"
              type="button"
              class="mt-1 text-[11px] text-accent hover:underline"
              @click="showGoldensetReport = true"
            >
              골든셋 보기 ({{ goldensetReport.questions?.length ?? 0 }}문항)
            </button>
          </div>

          <div>
            <h3 class="section-label">도메인 스키마</h3>
            <section class="mb-3">
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

            <section v-if="selectedDomain && domainSchema" class="mb-3">
              <h4 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">현재 도메인 스키마</h4>
              <p class="mb-1.5 text-[11px] text-ink-faint">
                캘리브레이션 문서 {{ domainSchema.calibration_stems.length }}개 · 실행 이력
                {{ domainSchema.history.length }}회
              </p>
              <div class="flex flex-wrap gap-1.5">
                <span
                  v-for="t in domainSchema.node_types"
                  :key="t.name"
                  class="chip border-border bg-ink/5 text-ink-muted"
                >{{ t.name }}</span>
                <span
                  v-for="t in domainSchema.edge_types"
                  :key="t.name"
                  class="chip border-sky-500/40 bg-sky-500/10 text-sky-400"
                >{{ t.name }}</span>
              </div>
            </section>

            <section class="mb-3">
              <h4 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">캘리브레이션 문서 선택</h4>
              <p v-if="domainFiles.length === 0" class="text-[11px] text-ink-faint">문서가 없습니다</p>
              <ul v-else class="max-h-40 space-y-0.5 overflow-y-auto rounded-md border border-border p-1.5">
                <li v-for="f in domainFiles" :key="f.filename">
                  <label class="flex cursor-pointer items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-ink/5">
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
              <p v-if="convergeError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ convergeError }}</p>
              <p v-if="convergeMessage" class="mt-1 text-[11px] text-emerald-700 dark:text-emerald-400">{{ convergeMessage }}</p>
            </section>

            <section v-if="domainEvaluation" class="mb-3">
              <h4 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">평가 지표</h4>
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
                <h5 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">
                  타입 활용도 (해당 타입이 인스턴스를 가진 캘리브레이션 문서 비율)
                </h5>
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

            <section v-if="domainSchema?.pending_review?.length" class="mb-3">
              <h4 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">
                검토 대기 중인 변경 ({{ domainSchema.pending_review.length }}건)
              </h4>
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
              <p v-if="domainReviewError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ domainReviewError }}</p>
            </section>

            <p v-if="domainUseError" class="text-[11px] text-red-600 dark:text-red-400">{{ domainUseError }}</p>
            <button
              type="button"
              class="btn-primary w-full"
              :disabled="!file || !selectedDomain || isUsingDomainSchema"
              @click="useDomainSchemaForCurrentFile"
            >
              {{ isUsingDomainSchema ? '적용 중...' : '현재 문서에 이 도메인 스키마 적용' }}
            </button>
          </div>
        </div>
      </details>

      <div class="mt-4 border-t border-border pt-3.5">
        <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">그래프 노드 필터</h3>
        <p v-if="availableTypes.length === 0" class="text-[11px] text-ink-faint">
          아직 추출된 그래프가 없습니다
        </p>
        <label
          v-for="type in availableTypes"
          :key="type"
          class="flex cursor-pointer items-center justify-between gap-2 rounded px-1 py-0.5 text-xs hover:bg-ink/5"
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
          <span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" :style="{ background: colorForType(type) }"></span>
        </label>

        <h3 class="mb-1 mt-2.5 text-[10px] uppercase tracking-wide text-ink-faint">그래프 엣지 필터</h3>
        <p v-if="availableEdgeTypes.length === 0" class="text-[11px] text-ink-faint">
          아직 추출된 그래프가 없습니다
        </p>
        <label
          v-for="type in availableEdgeTypes"
          :key="type"
          class="flex cursor-pointer items-center justify-between gap-2 rounded px-1 py-0.5 text-xs hover:bg-ink/5"
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
          <span class="h-1 w-5 flex-shrink-0 rounded-sm" :style="{ background: colorForEdgeType(type) }"></span>
        </label>
      </div>
    </aside>

    <!-- Main area: 온톨로지 그래프 / 스키마·그래프DB side by side as two columns,
         split by a draggable bar that defaults to the midpoint of this row's
         own width (see splitPercent above) -- flex-1/flex-1 alone put the
         divide wherever flex-basis auto-sizing landed, which a
         SchemaGraphPreview table full of long descriptions could push past
         the visible edge. -->
    <div ref="splitRow" class="flex min-h-0 min-w-0 flex-1" :class="{ 'select-none': isDraggingSplit }">
      <div class="relative flex min-h-0 min-w-0 flex-shrink-0" :style="{ width: splitPercent + '%' }">
        <div class="min-h-0 min-w-0 flex-1">
          <OntologyGraph
            :file="file"
            :enabled-types="enabledTypes"
            :enabled-edge-types="enabledEdgeTypes"
            :highlighted-node-ids="highlightedNodeIds"
            :schema-refresh-request="schemaRefreshRequest"
            @types-available="(t) => emit('types-available', t)"
            @edge-types-available="(t) => emit('edge-types-available', t)"
            @node-selected="onNodeSelected"
            @edge-selected="onEdgeSelected"
          />
        </div>
        <div
          v-if="inspectedNode || inspectedEdge"
          class="absolute right-0 top-0 z-10 h-full w-[280px] overflow-y-auto border-l border-border bg-surface-raised p-3"
        >
          <h3 class="section-label">{{ inspectedNode ? '노드' : '엣지' }}</h3>
          <dl class="space-y-1.5 text-xs">
            <template v-if="inspectedNode">
              <dt class="text-ink-faint">Label</dt><dd class="text-ink">{{ inspectedNode.label }}</dd>
              <dt class="text-ink-faint">Type</dt><dd class="text-ink-muted">{{ inspectedNode.type }}</dd>
              <template v-if="inspectedNode.detail">
                <dt class="text-ink-faint">Detail</dt><dd class="text-ink-muted">{{ inspectedNode.detail }}</dd>
              </template>
              <template v-if="inspectedNode.confidence">
                <dt class="text-ink-faint">Confidence</dt><dd class="text-ink-muted">{{ inspectedNode.confidence }}</dd>
              </template>
              <template v-if="inspectedNode.evidence_text">
                <dt class="text-ink-faint">Evidence</dt>
                <dd class="italic text-ink-faint">"{{ inspectedNode.evidence_text }}"</dd>
              </template>
              <template v-if="inspectedNode.source_section">
                <dt class="text-ink-faint">Section</dt><dd class="text-ink-muted">{{ inspectedNode.source_section }}</dd>
              </template>
            </template>
            <template v-else-if="inspectedEdge">
              <dt class="text-ink-faint">Type</dt><dd class="text-ink">{{ inspectedEdge.type }}</dd>
              <dt class="text-ink-faint">Source → Target</dt>
              <dd class="text-ink-muted">{{ inspectedEdge.source }} → {{ inspectedEdge.target }}</dd>
              <template v-if="inspectedEdge.detail">
                <dt class="text-ink-faint">Detail</dt><dd class="text-ink-muted">{{ inspectedEdge.detail }}</dd>
              </template>
              <template v-if="inspectedEdge.confidence">
                <dt class="text-ink-faint">Confidence</dt><dd class="text-ink-muted">{{ inspectedEdge.confidence }}</dd>
              </template>
              <template v-if="inspectedEdge.evidence_text">
                <dt class="text-ink-faint">Evidence</dt>
                <dd class="italic text-ink-faint">"{{ inspectedEdge.evidence_text }}"</dd>
              </template>
              <template v-if="inspectedEdge.source_section">
                <dt class="text-ink-faint">Section</dt><dd class="text-ink-muted">{{ inspectedEdge.source_section }}</dd>
              </template>
            </template>
          </dl>
        </div>
      </div>
      <div
        class="w-1 flex-shrink-0 cursor-col-resize border-l border-border bg-transparent hover:bg-accent/40"
        :class="{ 'bg-accent/60': isDraggingSplit }"
        title="드래그하여 폭 조절"
        @pointerdown="onSplitPointerDown"
      ></div>
      <div class="min-h-0 min-w-0 flex-1">
        <SchemaGraphPreview :file="file" :schema-version="schemaVersion" />
      </div>
    </div>

    <!-- Report/review overlays -- carried over as-is from SettingsPanel.vue -->
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
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400'"
              >
                온톨로지 {{ validationReport.validation_summary?.ontology_valid ? '유효' : '문제 있음' }}
              </span>
              <span
                class="chip"
                :class="validationReport.validation_summary?.extraction_valid
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400'"
              >
                추출 {{ validationReport.validation_summary?.extraction_valid ? '유효' : '문제 있음' }}
              </span>
              <span
                class="chip"
                :class="validationReport.validation_summary?.provenance_valid
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400'"
              >
                근거 {{ validationReport.validation_summary?.provenance_valid ? '유효' : '문제 있음' }}
              </span>
              <span
                class="chip"
                :class="validationReport.validation_summary?.competency_questions_answerable
                  ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  : 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400'"
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
                      ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                      : 'border-red-500/50 bg-red-500/15 text-red-600 dark:text-red-400'"
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
          <p v-if="evolutionError" class="text-[11px] text-red-600 dark:text-red-400">{{ evolutionError }}</p>
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
            <p v-if="evolutionApplyError" class="text-[11px] text-red-600 dark:text-red-400">{{ evolutionApplyError }}</p>
            <p v-else-if="evolutionApplyMessage" class="text-[11px] text-emerald-700 dark:text-emerald-400">{{ evolutionApplyMessage }}</p>
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
                  <span class="chip border-border bg-ink/5 text-ink-muted">{{ c.category }}</span>
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
                  <span class="chip border-border bg-ink/5 text-ink-muted">{{ r.category }}</span>
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
            <ul class="list-disc space-y-1 pl-4 text-xs text-amber-700 dark:text-amber-400">
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
      v-if="showGoldensetReport && goldensetReport"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50"
      @click.self="showGoldensetReport = false"
    >
      <div class="flex max-h-[85vh] w-[760px] max-w-[92vw] flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-2xl">
        <div class="flex flex-shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
          <h2 class="text-sm font-semibold text-ink">골든셋 ({{ goldensetReport.questions?.length ?? 0 }}문항, 문서 전체 기준)</h2>
          <button type="button" class="btn" @click="showGoldensetReport = false">닫기</button>
        </div>
        <div class="flex-1 space-y-2 overflow-y-auto p-4">
          <div
            v-for="(q, i) in goldensetReport.questions"
            :key="q.id ?? i"
            class="rounded-md border border-border bg-surface-sunken p-2.5"
          >
            <div class="mb-1 flex flex-wrap items-center gap-1.5">
              <span class="chip border-border bg-ink/5 text-ink-muted">{{ q.question_type }}</span>
              <span class="text-[10px] text-ink-faint">{{ q.importance }}</span>
              <span
                class="chip"
                :class="q.answerable ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' : 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400'"
              >{{ q.answerable ? '답변 가능' : '답변 불가' }}</span>
            </div>
            <p class="text-xs font-medium text-ink">{{ q.question }}</p>
            <p v-if="q.answer" class="mt-1 text-[11px] text-ink-muted">{{ q.answer }}</p>
            <ul v-if="q.evidence?.length" class="mt-1 space-y-0.5 text-[11px] text-ink-faint">
              <li v-for="(e, ei) in q.evidence" :key="ei">
                "{{ e.quote }}" (L{{ e.line_start }}{{ e.line_end !== e.line_start ? `-${e.line_end}` : '' }})
              </li>
            </ul>
            <p v-if="q.rationale" class="mt-1 text-[11px] italic text-ink-faint">{{ q.rationale }}</p>
          </div>
          <div v-if="goldensetReport.warnings?.length">
            <h3 class="section-label">주의/검증 필요</h3>
            <ul class="list-disc space-y-1 pl-4 text-xs text-amber-700 dark:text-amber-400">
              <li v-for="(w, i) in goldensetReport.warnings" :key="i">{{ w }}</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
