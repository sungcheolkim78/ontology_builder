<script setup>
import { computed, ref, watch } from 'vue'
import { apiFetch } from '../utils/api.js'
import { pageForQuote } from '../utils/pdfHighlight.js'
import ChatPanel from './ChatPanel.vue'
import MarkdownEvidenceViewer from './MarkdownEvidenceViewer.vue'
import PdfViewer from './PdfViewer.vue'

const props = defineProps({
  file: { type: Object, default: null },
  hops: { type: Number, default: 1 },
  renderMarkdown: { type: Boolean, default: true },
  enabledTypes: { type: Set, default: () => new Set() },
  enabledEdgeTypes: { type: Set, default: () => new Set() },
  availableTypes: { type: Array, default: () => [] },
})
const emit = defineEmits(['highlight-nodes', 'toggle-type'])

const rawText = ref('')
const citedQuote = ref('')
const pdfJumpRequest = ref(null)
let jumpCounter = 0

async function loadRawText(file) {
  rawText.value = ''
  citedQuote.value = ''
  pdfJumpRequest.value = null
  if (!file) return
  try {
    const res = await apiFetch(`/api/files/${encodeURIComponent(file.filename)}`)
    if (res.ok) rawText.value = await res.text()
  } catch (err) {
    rawText.value = ''
  }
}
watch(() => props.file, loadRawText, { immediate: true })

const lines = computed(() => (rawText.value ? rawText.value.split('\n') : []))

function onCiteEvidence({ text }) {
  citedQuote.value = text
  if (props.file?.has_pdf) {
    jumpCounter += 1
    pdfJumpRequest.value = { page: pageForQuote(rawText.value, lines.value, text), text, id: jumpCounter }
  }
}
</script>

<template>
  <div class="flex h-full min-w-0">
    <div class="min-h-0 min-w-0 flex-1 border-r border-border">
      <ChatPanel
        :file="file"
        :hops="hops"
        :render-markdown="renderMarkdown"
        :enabled-types="enabledTypes"
        :enabled-edge-types="enabledEdgeTypes"
        :available-types="availableTypes"
        @highlight-nodes="emit('highlight-nodes', $event)"
        @toggle-type="emit('toggle-type', $event)"
        @cite-evidence="onCiteEvidence"
      />
    </div>
    <div class="min-h-0 min-w-0 flex-1">
      <PdfViewer v-if="file?.has_pdf" :filename="file.filename" :jump-request="pdfJumpRequest" />
      <MarkdownEvidenceViewer v-else :text="rawText" :quote="citedQuote" />
    </div>
  </div>
</template>
