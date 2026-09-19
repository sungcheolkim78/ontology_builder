<script setup>
// package.json pins pdfjs-dist to 5.6.205 deliberately -- pdfjs-dist@5.7+
// and every 6.x release raise their engines.node floor above the frontend
// Docker image's Node 20 (see frontend/Dockerfile), so a caret range would
// let `npm install` silently pick a version that fails to install/run there.
import * as pdfjsLib from 'pdfjs-dist'
// Vite's `?url` suffix hands back the built worker script's URL instead of
// its (huge, Node-oriented) module contents -- pdf.js refuses to run its
// parsing/rendering off the main thread without this being set first.
import pdfWorkerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { API_BASE, authState } from '../utils/api.js'
import { buildHaystack, findMatchRange, itemsInRange, normalizeForMatch } from '../utils/pdfHighlight.js'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerSrc

const props = defineProps({
  filename: { type: String, default: null },
  // A fresh object each time the caller wants this viewer to jump somewhere
  // -- identity, not value, drives the watcher below, so re-requesting the
  // exact same page/text still re-runs the jump (see App.vue's
  // schemaRefreshRequest for the same pattern elsewhere in this app).
  jumpRequest: { type: Object, default: null },
})

const RENDER_SCALE = 1.4

const containerRef = ref(null)
const canvasRef = ref(null)
const pageNumber = ref(1)
const pageCount = ref(0)
const isLoading = ref(false)
const error = ref('')
const highlights = ref([])

let pdfDoc = null
let renderTask = null
let pendingJump = null

function itemViewportRect(item, viewport) {
  const transform = item.transform
  const fontHeight = Math.hypot(transform[2], transform[3]) || Math.hypot(transform[0], transform[1]) || 1
  const [x1, y1] = viewport.convertToViewportPoint(transform[4], transform[5])
  const [x2, y2] = viewport.convertToViewportPoint(
    transform[4] + item.width,
    transform[5] + (item.height || fontHeight)
  )
  return {
    left: Math.min(x1, x2),
    top: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  }
}

async function findHighlightRects(page, viewport, searchText) {
  const needle = normalizeForMatch(searchText)
  if (!needle) return []
  const content = await page.getTextContent()
  const { haystack, ranges } = buildHaystack(content.items)
  const match = findMatchRange(haystack, needle)
  if (!match) return []
  return itemsInRange(ranges, match).map((item) => itemViewportRect(item, viewport))
}

async function scrollToFirstHighlight() {
  await nextTick()
  const container = containerRef.value
  const [first] = highlights.value
  if (!container || !first) return
  container.scrollTop = Math.max(0, first.top - container.clientHeight / 3)
}

async function renderPage(targetPage, highlightText) {
  if (!pdfDoc || !canvasRef.value) return
  const clamped = Math.min(Math.max(1, targetPage || 1), pdfDoc.numPages)
  pageNumber.value = clamped
  const page = await pdfDoc.getPage(clamped)
  const viewport = page.getViewport({ scale: RENDER_SCALE })
  const canvas = canvasRef.value
  canvas.width = viewport.width
  canvas.height = viewport.height
  renderTask?.cancel()
  renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport })
  try {
    await renderTask.promise
  } catch (err) {
    if (err?.name === 'RenderingCancelledException') return
    throw err
  }
  highlights.value = highlightText ? await findHighlightRects(page, viewport, highlightText) : []
  await scrollToFirstHighlight()
}

async function applyPendingJump() {
  if (!pdfDoc) return
  const jump = pendingJump
  try {
    await renderPage(jump?.page ?? pageNumber.value, jump?.text ?? null)
  } catch (err) {
    error.value = 'PDF 페이지를 표시하지 못했습니다: ' + err.message
  }
}

async function loadDocument(filename) {
  highlights.value = []
  error.value = ''
  pageCount.value = 0
  pdfDoc?.destroy()
  pdfDoc = null
  if (!filename) return
  isLoading.value = true
  try {
    const headers = {}
    if (authState.token) headers.Authorization = `Bearer ${authState.token}`
    const url = `${API_BASE}/api/documents/${encodeURIComponent(filename)}/pdf`
    pdfDoc = await pdfjsLib.getDocument({ url, httpHeaders: headers }).promise
    pageCount.value = pdfDoc.numPages
  } catch (err) {
    error.value = 'PDF를 불러오지 못했습니다: ' + err.message
  } finally {
    isLoading.value = false
  }
}

watch(
  () => props.filename,
  async (filename) => {
    // A new document invalidates whatever jump was meant for the previous
    // one -- start it at page 1 and wait for the caller's next jumpRequest.
    pendingJump = null
    await loadDocument(filename)
    await applyPendingJump()
  },
  { immediate: true }
)

watch(
  () => props.jumpRequest,
  async (request) => {
    pendingJump = request
    await applyPendingJump()
  }
)

function goToPage(page) {
  pendingJump = { page, text: null }
  applyPendingJump()
}

onBeforeUnmount(() => {
  renderTask?.cancel()
  pdfDoc?.destroy()
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex flex-shrink-0 items-center gap-2 border-b border-border px-2 py-1 text-[11px] text-ink-faint">
      <button
        type="button"
        class="rounded px-1.5 py-0.5 hover:bg-ink/5 disabled:opacity-30"
        :disabled="pageNumber <= 1"
        @click="goToPage(pageNumber - 1)"
      >이전</button>
      <span>{{ pageNumber }} / {{ pageCount || '?' }} 페이지</span>
      <button
        type="button"
        class="rounded px-1.5 py-0.5 hover:bg-ink/5 disabled:opacity-30"
        :disabled="pageCount === 0 || pageNumber >= pageCount"
        @click="goToPage(pageNumber + 1)"
      >다음</button>
    </div>
    <div class="min-h-0 flex-1 overflow-auto bg-black/20 p-2" ref="containerRef">
      <p v-if="error" class="text-xs text-red-600 dark:text-red-400">{{ error }}</p>
      <p v-else-if="isLoading" class="text-xs text-ink-faint">PDF 불러오는 중...</p>
      <div v-else class="relative inline-block">
        <canvas ref="canvasRef"></canvas>
        <div
          v-for="(rect, index) in highlights"
          :key="index"
          class="pointer-events-none absolute rounded-sm bg-yellow-300/40 ring-2 ring-yellow-400/80"
          :style="{ left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' }"
        ></div>
      </div>
    </div>
  </div>
</template>
