<script setup>
import { marked } from 'marked'
import { ref } from 'vue'
import { preambleLineCount } from '../utils/chunkFormat.js'

const props = defineProps({
  data: { type: Object, required: true },
})

const expandedIds = ref(new Set())

function isExpanded(id) {
  return expandedIds.value.has(id)
}

function toggle(id) {
  const next = new Set(expandedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expandedIds.value = next
}

function renderMarkdown(text) {
  return marked.parse(text)
}
</script>

<template>
  <div class="text-[13px] text-ink">
    <p class="mb-2 border-b border-border pb-2 text-xs text-ink-faint">
      출처: <span class="text-ink-muted">{{ data.source }}</span>
      · 서문 {{ preambleLineCount(data.preamble) }}줄
      (라인 {{ data.preamble.line_start }}–{{ data.preamble.line_end }})
    </p>
    <ul class="space-y-1">
      <li
        v-for="chunk in data.chunks"
        :key="chunk.id"
        class="rounded-md border border-border"
      >
        <button
          type="button"
          data-testid="chunk-row-header"
          class="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-xs text-ink hover:bg-white/5"
          @click="toggle(chunk.id)"
        >
          <span class="text-ink-faint">{{ isExpanded(chunk.id) ? '▾' : '▸' }}</span>
          <span class="break-all">{{ chunk.path }}</span>
        </button>
        <div
          v-if="isExpanded(chunk.id)"
          class="markdown border-t border-border px-3 py-2 text-[13px] leading-relaxed"
          v-html="renderMarkdown(chunk.text)"
        ></div>
      </li>
    </ul>
  </div>
</template>
