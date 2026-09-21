<script setup>
import { marked } from 'marked'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'
import { lineForPage, pageForLine, totalPagesInLines } from '../utils/pdfHighlight.js'
import ChunkView from './ChunkView.vue'
import PdfViewer from './PdfViewer.vue'

const props = defineProps({
  file: { type: Object, default: null },
})

const chunkData = ref(null)
const viewMode = ref('raw')
const hasPdf = ref(false)
const pdfJumpRequest = ref(null)
let pdfJumpCounter = 0

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

const lines = computed(() => (rawText.value ? rawText.value.split('\n') : []))
const totalLines = computed(() => lines.value.length)

const currentLine = computed(() => {
  if (totalLines.value === 0) return 0
  const scrollable = scrollHeight.value - clientHeight.value
  const fraction = scrollable > 0 ? scrollTop.value / scrollable : 0
  return Math.min(totalLines.value, Math.round(fraction * (totalLines.value - 1)) + 1)
})

// Status-bar page display -- derived from the markdown scroll position
// alone (via the same `<!-- page: N -->` markers the PDF jump/sync features
// use), so it stays in sync without PreviewView needing to ask PdfViewer
// for anything.
const currentPage = computed(() => (hasPdf.value ? pageForLine(lines.value, currentLine.value || 1) : null))
const totalPages = computed(() => (hasPdf.value ? totalPagesInLines(lines.value) : null))

// Fired by PdfViewer's 'sync' emit (its Sync button) -- the reverse
// direction of the old scroll-triggered auto-jump this replaced: an
// explicit, one-shot "show me where this PDF page is in the markdown"
// action instead of every markdown scroll silently dragging the PDF along.
async function scrollToLine(targetLine) {
  if (viewMode.value !== 'raw') viewMode.value = 'raw'
  await nextTick()
  const el = scrollRef.value
  if (!el || totalLines.value <= 1) return
  const scrollable = el.scrollHeight - el.clientHeight
  const fraction = (targetLine - 1) / (totalLines.value - 1)
  el.scrollTop = Math.max(0, Math.min(scrollable, fraction * scrollable))
  measureScroll()
}

function onSyncFromPdf(page) {
  scrollToLine(lineForPage(lines.value, page))
}

// Fired by ChunkView's chunk-selected emit -- same mechanism as the PDF
// viewer's own page navigation, just keyed off the chunk's own line_start
// instead of a PDF page.
function onChunkSelected(chunk) {
  if (!hasPdf.value) return
  pdfJumpCounter += 1
  pdfJumpRequest.value = {
    page: pageForLine(lines.value, chunk.line_start),
    text: (lines.value[chunk.line_start - 1] || '').trim(),
    id: pdfJumpCounter,
  }
}

async function loadChunkData(file) {
  chunkData.value = null
  try {
    const res = await apiFetch(`/api/documents/${encodeURIComponent(file.filename)}/chunk`)
    chunkData.value = res.ok ? await res.json() : null
  } catch (err) {
    chunkData.value = null
  }
}

watch(
  () => props.file,
  async (file) => {
    error.value = ''
    html.value = ''
    rawText.value = ''
    viewMode.value = 'raw'
    chunkData.value = null
    hasPdf.value = !!file?.has_pdf
    pdfJumpRequest.value = null
    if (!file) return
    loadChunkData(file)
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
      <span>Preview</span>
    </div>
    <p v-if="!file" class="p-3 text-xs text-ink-faint">업로드된 문서가 없습니다</p>
    <p v-else-if="error" class="p-3 text-xs text-red-600 dark:text-red-400">{{ error }}</p>
    <div v-else class="flex min-h-0 flex-1">
      <div v-if="hasPdf" class="min-h-0 min-w-0 flex-1 border-r border-border">
        <PdfViewer :filename="file.filename" :jump-request="pdfJumpRequest" @sync="onSyncFromPdf" />
      </div>
      <div class="flex min-h-0 min-w-0 flex-1 flex-col p-3">
        <div v-if="chunkData" data-testid="view-toggle" class="mb-2 flex flex-shrink-0 gap-1 text-[11px]">
          <button
            type="button"
            data-testid="view-mode-raw"
            class="rounded px-1.5 py-0.5"
            :class="viewMode === 'raw' ? 'bg-accent-muted/60 text-ink' : 'text-ink-faint hover:bg-ink/5'"
            @click="viewMode = 'raw'"
          >원문</button>
          <button
            type="button"
            data-testid="view-mode-chunk"
            class="rounded px-1.5 py-0.5"
            :class="viewMode === 'chunk' ? 'bg-accent-muted/60 text-ink' : 'text-ink-faint hover:bg-ink/5'"
            @click="viewMode = 'chunk'"
          >청크</button>
        </div>
        <div v-if="viewMode === 'chunk' && chunkData" class="min-h-0 flex-1 overflow-y-scroll">
          <ChunkView :data="chunkData" @chunk-selected="onChunkSelected" />
        </div>
        <template v-else>
          <div class="min-h-0 flex-1 overflow-y-scroll" ref="scrollRef" @scroll="onScroll">
            <div class="markdown text-[13px] leading-relaxed text-ink" v-html="html"></div>
          </div>
          <p class="mt-1 flex-shrink-0 border-t border-border pt-1 text-[11px] text-ink-faint">
            {{ currentLine }} / {{ totalLines }} 줄<template v-if="hasPdf"> · {{ currentPage }} / {{ totalPages }} 페이지</template>
          </p>
        </template>
      </div>
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
  background: rgb(var(--color-ink) / 0.08);
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-size: 0.85em;
}
.markdown :deep(a) {
  color: theme('colors.accent.DEFAULT');
}
</style>
