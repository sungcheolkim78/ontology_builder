<script setup>
import { reactive, watch } from 'vue'
import { apiFetch } from '../utils/api.js'

const props = defineProps({
  report: { type: Object, default: () => ({ questions: [] }) },
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
})

// One generation slot per question id, independent of the others -- clicking
// "답변 생성" on one question must never disturb another question's already-
// generated answer or in-flight state.
const generated = reactive({})

watch(
  () => props.report,
  (report) => {
    for (const q of report?.questions ?? []) {
      if (!generated[q.id]) {
        generated[q.id] = { loading: false, error: '', content: '', nodeTypes: [], edgeTypes: [] }
      }
    }
  },
  { immediate: true }
)

async function generateAnswer(question) {
  const state = generated[question.id]
  state.loading = true
  state.error = ''
  try {
    const res = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [{ role: 'user', content: question.question }],
        filename: props.file?.filename ?? null,
        hops: props.hops,
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    state.content = data.content
    state.nodeTypes = data.node_types ?? []
    state.edgeTypes = data.edge_types ?? []
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
          <p v-else-if="generated[q.id]?.content" class="whitespace-pre-wrap text-[12px] text-ink">{{ generated[q.id].content }}</p>
          <p v-else class="text-[12px] text-ink-faint">아직 생성되지 않았습니다</p>
        </div>
      </div>
    </div>
  </div>
</template>
