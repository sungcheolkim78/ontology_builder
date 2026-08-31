<script setup>
import { marked } from 'marked'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'

const props = defineProps({
  file: { type: Object, default: null },
})

const html = ref('')
const rawText = ref('')
const error = ref('')

const scrollRef = ref(null)
const scrollTop = ref(0)
const scrollHeight = ref(0)
const clientHeight = ref(0)
let resizeObserver = null

function measureScroll() {
  const el = scrollRef.value
  if (!el) return
  scrollTop.value = el.scrollTop
  scrollHeight.value = el.scrollHeight
  clientHeight.value = el.clientHeight
}

function onScroll() {
  measureScroll()
}

watch(scrollRef, (el) => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (el) {
    resizeObserver = new ResizeObserver(measureScroll)
    resizeObserver.observe(el)
    measureScroll()
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
})

const totalLines = computed(() => (rawText.value ? rawText.value.split('\n').length : 0))

const thumbHeightPercent = computed(() => {
  if (scrollHeight.value === 0) return 100
  return Math.max(4, Math.min(100, (clientHeight.value / scrollHeight.value) * 100))
})

const thumbTopPercent = computed(() => {
  const scrollable = scrollHeight.value - clientHeight.value
  const fraction = scrollable > 0 ? scrollTop.value / scrollable : 0
  return (100 - thumbHeightPercent.value) * fraction
})

const thumbStyle = computed(() => ({
  height: thumbHeightPercent.value + '%',
  top: thumbTopPercent.value + '%',
}))

const currentLine = computed(() => {
  if (totalLines.value === 0) return 0
  const scrollable = scrollHeight.value - clientHeight.value
  const fraction = scrollable > 0 ? scrollTop.value / scrollable : 0
  return Math.min(totalLines.value, Math.round(fraction * (totalLines.value - 1)) + 1)
})

watch(
  () => props.file,
  async (file) => {
    error.value = ''
    html.value = ''
    rawText.value = ''
    if (!file) return
    try {
      const res = await apiFetch(`/api/files/${encodeURIComponent(file.filename)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()
      rawText.value = text
      html.value = marked.parse(text)
      await nextTick()
      if (scrollRef.value) scrollRef.value.scrollTop = 0
      measureScroll()
    } catch (err) {
      error.value = '문서를 불러오지 못했습니다: ' + err.message
    }
  },
  { immediate: true }
)
</script>

<template>
  <section class="flex h-full flex-col">
    <div class="panel-header">
      <span>문서 Preview</span>
    </div>
    <div class="flex min-h-0 flex-1 flex-col p-3">
      <p v-if="!file" class="text-xs text-ink-faint">업로드된 문서가 없습니다</p>
      <p v-else-if="error" class="text-xs text-red-400">{{ error }}</p>
      <template v-else>
        <div class="flex min-h-0 flex-1 gap-2">
          <div class="min-h-0 flex-1 overflow-y-scroll" ref="scrollRef" @scroll="onScroll">
            <div class="markdown text-[13px] leading-relaxed text-ink" v-html="html"></div>
          </div>
          <div class="relative w-1.5 flex-shrink-0 rounded-full bg-white/5">
            <div class="absolute left-0 right-0 min-h-[16px] rounded-full bg-accent/60" :style="thumbStyle"></div>
          </div>
        </div>
        <p class="mt-1 flex-shrink-0 border-t border-border pt-1 text-[11px] text-ink-faint">
          {{ currentLine }} of {{ totalLines }} lines
        </p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.markdown :deep(table) {
  border-collapse: collapse;
  margin: 0.5rem 0;
}
.markdown :deep(td),
.markdown :deep(th) {
  border: 1px solid theme('colors.border.DEFAULT');
  padding: 0.25rem 0.5rem;
}
.markdown :deep(h1),
.markdown :deep(h2),
.markdown :deep(h3) {
  color: theme('colors.ink.DEFAULT');
  font-weight: 600;
  margin: 0.75rem 0 0.35rem;
}
.markdown :deep(code) {
  background: rgba(255, 255, 255, 0.08);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-size: 0.85em;
}
.markdown :deep(a) {
  color: theme('colors.accent.DEFAULT');
}
</style>
