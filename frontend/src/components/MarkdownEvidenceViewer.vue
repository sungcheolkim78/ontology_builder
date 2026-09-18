<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  text: { type: String, default: '' },
  quote: { type: String, default: '' },
})

const containerRef = ref(null)

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// Shows the raw markdown source (not marked.parse'd HTML) with the cited
// quote wrapped in <mark> -- rendering it through `marked` first would risk
// reformatting the text enough that the verbatim quote no longer appears as
// a substring of the rendered HTML.
const html = computed(() => {
  const index = props.quote ? props.text.indexOf(props.quote) : -1
  if (index === -1) {
    return `<pre class="whitespace-pre-wrap font-sans">${escapeHtml(props.text)}</pre>`
  }
  const before = escapeHtml(props.text.slice(0, index))
  const match = escapeHtml(props.text.slice(index, index + props.quote.length))
  const after = escapeHtml(props.text.slice(index + props.quote.length))
  return `<pre class="whitespace-pre-wrap font-sans">${before}<mark class="rounded-sm bg-yellow-300/40 px-0.5 ring-1 ring-yellow-400/80">${match}</mark>${after}</pre>`
})

watch(html, async () => {
  await nextTick()
  const mark = containerRef.value?.querySelector('mark')
  // jsdom (used by Vitest) has no scrollIntoView implementation -- guard so
  // tests don't hit an unhandled rejection here, same spirit as
  // vitest.setup.js's ResizeObserver stub for DocumentPreview/PreviewView.
  if (typeof mark?.scrollIntoView === 'function') mark.scrollIntoView({ block: 'center' })
})
</script>

<template>
  <div class="h-full overflow-y-auto p-3 text-[13px] leading-relaxed text-ink" ref="containerRef" v-html="html"></div>
</template>
