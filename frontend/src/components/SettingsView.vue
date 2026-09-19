<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { apiFetch } from '../utils/api.js'
import { setTheme, themeState } from '../utils/theme.js'

const emit = defineEmits(['hops-changed', 'markdown-changed', 'database-reset'])

const model = ref('로딩 중...')
const maxTokens = ref(null)
const modelCatalog = ref([])
const selectedModel = ref('')
const isSettingModel = ref(false)
const modelSetError = ref('')

const OPERATION_MODELS = [
  { key: 'discover_ontology', label: '온톨로지 발견' },
  { key: 'generate_schema', label: '스키마 생성' },
  { key: 'extract_graph', label: '그래프 추출' },
  { key: 'validate_ontology', label: '온톨로지 검증' },
]
const operationModel = reactive({})
const selectedOperationModel = reactive({})
const isSettingOperationModel = reactive({})
const operationModelSetError = reactive({})

const renderMarkdown = ref(true)
const graphRagHops = ref(1)
const isResettingDb = ref(false)
const resetDbError = ref('')

onMounted(async () => {
  try {
    const res = await apiFetch('/api/config')
    const data = await res.json()
    model.value = data.model
    maxTokens.value = data.max_tokens ?? null
    modelCatalog.value = data.models ?? []
    selectedModel.value = data.model
    for (const { key } of OPERATION_MODELS) {
      const current = data.operation_models?.[key] ?? data.model
      operationModel[key] = current
      selectedOperationModel[key] = current
    }
  } catch (err) {
    model.value = '알 수 없음'
  }
})

const modelGroups = computed(() => {
  const groups = []
  const byProvider = new Map()
  for (const m of modelCatalog.value) {
    const provider = m.id.split('/')[0]
    if (!byProvider.has(provider)) {
      const models = []
      byProvider.set(provider, models)
      groups.push({ provider, models })
    }
    byProvider.get(provider).push(m.id)
  }
  return groups
})

async function onModelChange() {
  const previous = model.value
  const next = selectedModel.value
  if (next === previous) return
  isSettingModel.value = true
  modelSetError.value = ''
  try {
    const res = await apiFetch('/api/config/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: next }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    model.value = next
  } catch (err) {
    selectedModel.value = previous
    modelSetError.value = '모델 변경 실패: ' + err.message
  } finally {
    isSettingModel.value = false
  }
}

async function onOperationModelChange(key) {
  const previous = operationModel[key]
  const next = selectedOperationModel[key]
  if (next === previous) return
  isSettingOperationModel[key] = true
  operationModelSetError[key] = ''
  try {
    const res = await apiFetch('/api/config/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: next, operation: key }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
    }
    operationModel[key] = next
  } catch (err) {
    selectedOperationModel[key] = previous
    operationModelSetError[key] = '모델 변경 실패: ' + err.message
  } finally {
    isSettingOperationModel[key] = false
  }
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
</script>

<template>
  <section class="flex h-full flex-col overflow-hidden">
    <div class="panel-header">
      <span>Settings</span>
    </div>
    <div class="flex-1 space-y-4 overflow-y-auto p-4">
      <div>
        <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">테마</h3>
        <div class="inline-flex rounded-md border border-border p-0.5">
          <button
            type="button"
            class="rounded px-2.5 py-1 text-xs transition-colors"
            :class="themeState.mode === 'light' ? 'bg-accent-muted text-ink' : 'text-ink-faint hover:bg-ink/5'"
            @click="setTheme('light')"
          >Light</button>
          <button
            type="button"
            class="rounded px-2.5 py-1 text-xs transition-colors"
            :class="themeState.mode === 'dark' ? 'bg-accent-muted text-ink' : 'text-ink-faint hover:bg-ink/5'"
            @click="setTheme('dark')"
          >Dark</button>
        </div>
      </div>

      <div>
        <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">LLM 모델</h3>
        <p class="mb-1 text-[11px] text-ink-faint">기본 모델 (채팅, 골든셋 생성 등 아래에 없는 모든 작업)</p>
        <select
          v-model="selectedModel"
          class="field w-full"
          :disabled="isSettingModel"
          @change="onModelChange"
        >
          <option v-if="!modelCatalog.length" :value="selectedModel">{{ selectedModel }}</option>
          <optgroup v-for="group in modelGroups" :key="group.provider" :label="group.provider">
            <option v-for="id in group.models" :key="id" :value="id">{{ id }}</option>
          </optgroup>
        </select>
        <p v-if="isSettingModel" class="mt-1 text-[11px] text-ink-muted">적용 중...</p>
        <p v-if="modelSetError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ modelSetError }}</p>
        <p v-if="maxTokens" class="mt-1 text-[11px] text-ink-muted">
          max tokens: {{ maxTokens.toLocaleString('en-US') }}
        </p>

        <div v-for="op in OPERATION_MODELS" :key="op.key" class="mt-3">
          <p class="mb-1 text-[11px] text-ink-faint">{{ op.label }}</p>
          <select
            v-model="selectedOperationModel[op.key]"
            class="field w-full"
            :disabled="isSettingOperationModel[op.key]"
            @change="onOperationModelChange(op.key)"
          >
            <option v-if="!modelCatalog.length" :value="selectedOperationModel[op.key]">
              {{ selectedOperationModel[op.key] }}
            </option>
            <optgroup v-for="group in modelGroups" :key="group.provider" :label="group.provider">
              <option v-for="id in group.models" :key="id" :value="id">{{ id }}</option>
            </optgroup>
          </select>
          <p v-if="isSettingOperationModel[op.key]" class="mt-1 text-[11px] text-ink-muted">적용 중...</p>
          <p v-if="operationModelSetError[op.key]" class="mt-1 text-[11px] text-red-600 dark:text-red-400">
            {{ operationModelSetError[op.key] }}
          </p>
        </div>
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
        <h3 class="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">데이터베이스 관리</h3>
        <button type="button" class="btn-danger w-full" :disabled="isResettingDb" @click="resetDatabase">
          {{ isResettingDb ? '초기화 중...' : 'LadybugDB 초기화' }}
        </button>
        <p class="mt-1 text-[11px] leading-snug text-ink-faint">
          WAL 파일 손상 등으로 그래프 조회가 계속 실패할 때 사용하세요. 모든 문서의 추출된 그래프가 삭제됩니다.
        </p>
        <p v-if="resetDbError" class="mt-1 text-[11px] text-red-600 dark:text-red-400">{{ resetDbError }}</p>
      </div>
    </div>
  </section>
</template>
