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
  <section class="preview">
    <h2 class="panel-title">문서 Preview</h2>
    <div class="panel-body">
      <p v-if="!file" class="placeholder">업로드된 문서가 없습니다</p>
      <p v-else-if="error" class="error">{{ error }}</p>
      <template v-else>
        <div class="content-row">
          <div class="markdown-scroll" ref="scrollRef" @scroll="onScroll">
            <div class="markdown" v-html="html"></div>
          </div>
          <div class="position-track">
            <div class="position-thumb" :style="thumbStyle"></div>
          </div>
        </div>
        <p class="status-line">{{ currentLine }} of {{ totalLines }} lines</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.preview {
  height: 100%;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid #ccc;
}
.panel-title {
  flex-shrink: 0;
  margin: 0;
  padding: 0.6rem 1rem;
  font-size: 1rem;
  color: #fff;
  background: #059669;
}
.panel-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 1rem;
}
.content-row {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 0.5rem;
}
.position-track {
  flex-shrink: 0;
  width: 6px;
  border-radius: 3px;
  background: #eee;
  position: relative;
}
.position-thumb {
  position: absolute;
  left: 0;
  right: 0;
  min-height: 16px;
  border-radius: 3px;
  background: #059669;
}
.markdown-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: scroll;
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
.status-line {
  flex-shrink: 0;
  margin: 0.15rem 0 0;
  padding-top: 0.15rem;
  border-top: 1px solid #eee;
  font-size: 0.75rem;
  color: #888;
}
.placeholder {
  color: #888;
}
.error {
  color: red;
}
.markdown :deep(table) {
  border-collapse: collapse;
}
.markdown :deep(td),
.markdown :deep(th) {
  border: 1px solid #ccc;
  padding: 0.25rem 0.5rem;
}
</style>
