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
  <section class="flex h-full flex-col border-b border-slate-200">
    <h2 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">문서 Preview</h2>
    <div class="flex-1 min-h-0 flex flex-col p-4">
      <p v-if="!file" class="text-sm text-slate-500">업로드된 문서가 없습니다</p>
      <p v-else-if="error" class="text-sm text-red-600">{{ error }}</p>
      <template v-else>
        <div class="flex-1 min-h-0 flex gap-2">
          <div class="markdown-scroll flex-1 min-h-0 overflow-y-scroll" ref="scrollRef" @scroll="onScroll">
            <div class="markdown" v-html="html"></div>
          </div>
          <div class="relative w-1.5 shrink-0 rounded-full bg-slate-100">
            <div class="absolute inset-x-0 min-h-4 rounded-full bg-indigo-600" :style="thumbStyle"></div>
          </div>
        </div>
        <p class="mt-1 shrink-0 border-t border-slate-100 pt-1 text-xs text-slate-500">{{ currentLine }} of {{ totalLines }} lines</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.markdown-scroll {
  scrollbar-width: thin;
  scrollbar-color: #b0b0b0 #f0f0f0;
}
.markdown-scroll::-webkit-scrollbar {
  width: 10px;
}
.markdown-scroll::-webkit-scrollbar-track {
  background: #f0f0f0;
}
.markdown-scroll::-webkit-scrollbar-thumb {
  background-color: #b0b0b0;
  border-radius: 6px;
  border: 2px solid #f0f0f0;
}
.markdown :deep(h1) {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0.75rem 0 0.5rem;
}
.markdown :deep(h2) {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0.75rem 0 0.5rem;
}
.markdown :deep(h3) {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 0.5rem 0 0.35rem;
}
.markdown :deep(h4) {
  font-size: 1rem;
  font-weight: 600;
  margin: 0.5rem 0 0.35rem;
}
.markdown :deep(p) {
  margin: 0.5rem 0;
}
.markdown :deep(ul),
.markdown :deep(ol) {
  list-style: revert;
  padding-left: 1.5em;
  margin: 0.5rem 0;
}
.markdown :deep(table) {
  border-collapse: collapse;
}
.markdown :deep(td),
.markdown :deep(th) {
  border: 1px solid #e2e8f0;
  padding: 0.25rem 0.5rem;
}
</style>
