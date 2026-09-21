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
import { buildHaystack, findMatchRange, itemsInRange, normalizeForMatch, normalizeForSearch } from '../utils/pdfHighlight.js'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerSrc

const props = defineProps({
  filename: { type: String, default: null },
  // A fresh object each time the caller wants this viewer to jump somewhere
  // -- identity, not value, drives the watcher below, so re-requesting the
  // exact same page/text still re-runs the jump (see App.vue's
  // schemaRefreshRequest for the same pattern elsewhere in this app).
  jumpRequest: { type: Object, default: null },
})

// 'sync': the current PDF page, so PreviewView.vue can scroll the markdown
// pane to the matching `<!-- page: N -->` position -- the reverse direction
// of jumpRequest above (markdown -> PDF).
const emit = defineEmits(['sync'])

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

// Shared by the two things that put a page on screen: a jump request's
// (page, highlightText) below, and the keyword search's (page, matchRange)
// further down -- both need the freshly-rendered page/viewport to compute
// highlight rects from, just via a different match-finding path.
async function renderPageCanvas(targetPage) {
  if (!pdfDoc || !canvasRef.value) return null
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
    if (err?.name === 'RenderingCancelledException') return null
    throw err
  }
  return { page, viewport }
}

async function renderPage(targetPage, highlightText) {
  const rendered = await renderPageCanvas(targetPage)
  if (!rendered) return
  highlights.value = highlightText
    ? await findHighlightRects(rendered.page, rendered.viewport, highlightText)
    : []
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

// --- keyword search ---
// committedSearchQuery/searchCursor are plain (non-reactive) bookkeeping,
// not shown in the template -- only searchQuery/searchNoMatch need to drive
// re-renders. A query is "committed" on its first Enter (search from the
// very start of the document, page 1); every following Enter with the same
// text continues from just past the last match instead, wrapping back to
// page 1 if nothing more is found -- editing the query resets this so the
// next Enter is a fresh "first result" search again.
const searchQuery = ref('')
const searchNoMatch = ref(false)
let committedSearchQuery = null
let searchCursor = { page: 1, offset: 0 }

async function findNextMatch(needle, fromPage, fromOffset) {
  const total = pdfDoc.numPages
  for (let i = 0; i <= total; i++) {
    const page = ((fromPage - 1 + i) % total) + 1
    const startOffset = i === 0 ? fromOffset : 0
    const pdfPage = await pdfDoc.getPage(page)
    const content = await pdfPage.getTextContent()
    const { haystack, ranges } = buildHaystack(content.items, (item) => item.str, normalizeForSearch)
    const match = findMatchRange(haystack.slice(startOffset), needle)
    if (match) {
      return {
        page,
        start: match.start + startOffset,
        end: match.end + startOffset,
        ranges,
      }
    }
  }
  return null
}

async function onSearchEnter() {
  if (!pdfDoc) return
  const needle = normalizeForSearch(searchQuery.value)
  if (!needle) return
  const isNewSearch = needle !== committedSearchQuery
  committedSearchQuery = needle
  const from = isNewSearch ? { page: 1, offset: 0 } : searchCursor
  const result = await findNextMatch(needle, from.page, from.offset)
  if (!result) {
    searchNoMatch.value = true
    return
  }
  searchNoMatch.value = false
  searchCursor = { page: result.page, offset: result.end }
  const rendered = await renderPageCanvas(result.page)
  if (!rendered) return
  highlights.value = itemsInRange(result.ranges, { start: result.start, end: result.end }).map((item) =>
    itemViewportRect(item, rendered.viewport)
  )
  await scrollToFirstHighlight()
}

function resetSearch() {
  searchQuery.value = ''
  searchNoMatch.value = false
  committedSearchQuery = null
  searchCursor = { page: 1, offset: 0 }
}

async function loadDocument(filename) {
  highlights.value = []
  error.value = ''
  pageCount.value = 0
  resetSearch()
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

function syncToMarkdown() {
  emit('sync', pageNumber.value)
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
      <div class="h-3.5 w-px bg-border"></div>
      <button
        type="button"
        class="rounded px-1.5 py-0.5 hover:bg-ink/5 disabled:opacity-30"
        :disabled="!pageCount"
        title="현재 PDF 페이지와 연관된 원문 위치로 이동"
        @click="syncToMarkdown"
      >Sync</button>
      <input
        v-model="searchQuery"
        type="text"
        placeholder="PDF 내 검색 (Enter)"
        class="field h-6 w-36 py-0 text-[11px]"
        :disabled="!pageCount"
        @input="searchNoMatch = false"
        @keydown.enter="onSearchEnter"
      />
      <span v-if="searchNoMatch" class="text-red-600 dark:text-red-400">검색 결과 없음</span>
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
