<script setup>
import { reactive, watch } from 'vue'
import { apiFetch } from '../utils/api.js'

const props = defineProps({
  report: { type: Object, default: () => ({ questions: [] }) },
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
})

function emptyState() {
  return {
    loading: false,
    error: '',
    content: '',
    nodeTypes: [],
    edgeTypes: [],
    schemaVersion: null,
    hopsUsed: null,
    generatedAt: null,
  }
}

// One generation slot per question id, independent of the others -- clicking
// "답변 생성" on one question must never disturb another question's already-
// generated answer or in-flight state.
const generated = reactive({})

function ensureState(id) {
  if (!generated[id]) generated[id] = emptyState()
  return generated[id]
}

// Loads whatever this document's own GraphRAG pipeline most recently
// answered for each question *at the document's current active schema
// version* -- an answer generated against a since-replaced schema version
// is not shown, since it's no longer a trustworthy read of "the" current
// answer (see app.goldenset.latest_goldenset_answers on the backend).
async function loadExistingAnswers(file, questions) {
  for (const q of questions) ensureState(q.id)
  if (!file) return
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(file.filename)}/goldenset/answers`)
    if (!res.ok) return
    const data = await res.json()
    for (const [id, record] of Object.entries(data.answers ?? {})) {
      const state = ensureState(id)
      state.content = record.content
      state.nodeTypes = record.node_types ?? []
      state.edgeTypes = record.edge_types ?? []
      state.schemaVersion = record.schema_version
      state.hopsUsed = record.hops
      state.generatedAt = record.generated_at
    }
  } catch (err) {
    // Leave whatever was already loaded (or the empty default) in place --
    // this is a best-effort convenience load, not required for the golden
    // answers themselves to render.
  }
}

watch(
  [() => props.report, () => props.file],
  ([report, file]) => loadExistingAnswers(file, report?.questions ?? []),
  { immediate: true }
)

async function generateAnswer(question) {
  const state = ensureState(question.id)
  state.loading = true
  state.error = ''
  try {
    const res = await apiFetch(
      `/api/documents/${encodeURIComponent(props.file?.filename ?? '')}/goldenset/${encodeURIComponent(question.id)}/answer`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hops: props.hops }),
      }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const record = await res.json()
    state.content = record.content
    state.nodeTypes = record.node_types ?? []
    state.edgeTypes = record.edge_types ?? []
    state.schemaVersion = record.schema_version ?? null
    state.hopsUsed = record.hops ?? null
    state.generatedAt = record.generated_at ?? null
  } catch (err) {
    state.error = 'GraphRAG 답변 생성 실패: ' + err.message
  } finally {
    state.loading = false
  }
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2.5 overflow-y-auto" data-testid="goldenset-view">
    <p v-if="!report?.questions?.length" class="text-xs text-ink-faint">
      골든셋이 없습니다. File Explorer에서 "골든셋 작성"으로 먼저 생성하세요.
    </p>
    <div
      v-for="q in report.questions"
      :key="q.id"
      class="rounded-lg border border-border bg-surface-raised p-3"
    >
      <div class="mb-1 flex flex-wrap items-center gap-1.5">
        <span class="chip border-border bg-white/5 text-ink-muted">{{ q.question_type }}</span>
        <span class="text-[10px] text-ink-faint">{{ q.importance }}</span>
        <span
          class="chip"
          :class="q.answerable
            ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
            : 'border-amber-500/30 bg-amber-500/10 text-amber-400'"
        >{{ q.answerable ? '답변 가능' : '답변 불가' }}</span>
      </div>
      <p class="text-[13px] font-medium text-ink">{{ q.question }}</p>

      <div class="mt-2 grid grid-cols-2 gap-2">
        <div class="rounded-md border border-border bg-surface-sunken p-2">
          <div class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">정답 (골든셋)</div>
          <p v-if="q.answer" class="text-[12px] text-ink">{{ q.answer }}</p>
          <p v-else class="text-[12px] text-ink-faint">답변 불가 항목</p>
          <ul v-if="q.evidence?.length" class="mt-1 space-y-0.5 text-[11px] text-ink-faint">
            <li v-for="(e, i) in q.evidence" :key="i">
              "{{ e.quote }}" (L{{ e.line_start }}{{ e.line_end !== e.line_start ? `-${e.line_end}` : '' }})
            </li>
          </ul>
        </div>
        <div class="rounded-md border border-border bg-surface-sunken p-2">
          <div class="mb-1 flex items-center justify-between">
            <span class="text-[10px] uppercase tracking-wide text-ink-faint">GraphRAG 응답</span>
            <button
              type="button"
              class="btn px-2 py-0.5 text-[11px]"
              :data-testid="`generate-answer-${q.id}`"
              :disabled="generated[q.id]?.loading"
              @click="generateAnswer(q)"
            >{{ generated[q.id]?.loading ? '생성 중...' : '답변 생성' }}</button>
          </div>
          <p v-if="generated[q.id]?.error" class="text-[11px] text-red-400">{{ generated[q.id].error }}</p>
          <template v-else-if="generated[q.id]?.content">
            <p class="whitespace-pre-wrap text-[12px] text-ink">{{ generated[q.id].content }}</p>
            <div v-if="generated[q.id].nodeTypes?.length || generated[q.id].edgeTypes?.length" class="mt-1 flex flex-wrap gap-1">
              <span v-for="t in generated[q.id].nodeTypes" :key="'n-' + t" class="chip border-emerald-500/40 text-emerald-400">{{ t }}</span>
              <span v-for="t in generated[q.id].edgeTypes" :key="'e-' + t" class="chip border-amber-500/40 text-amber-400">{{ t }}</span>
            </div>
            <p class="mt-1 text-[10px] text-ink-faint">
              스키마 v{{ generated[q.id].schemaVersion }} · hops {{ generated[q.id].hopsUsed }} · {{ generated[q.id].generatedAt }}
            </p>
          </template>
          <p v-else class="text-[12px] text-ink-faint">아직 생성되지 않았습니다</p>
        </div>
      </div>
    </div>
  </div>
</template>
