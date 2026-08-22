<script setup>
import { marked } from 'marked'
import { ref, watch } from 'vue'

const props = defineProps({
  file: { type: Object, default: null },
})

const html = ref('')
const error = ref('')

watch(
  () => props.file,
  async (file) => {
    error.value = ''
    html.value = ''
    if (!file) return
    try {
      const res = await fetch(`/api/files/${encodeURIComponent(file.filename)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()
      html.value = marked.parse(text)
    } catch (err) {
      error.value = '문서를 불러오지 못했습니다: ' + err.message
    }
  },
  { immediate: true }
)
</script>

<template>
  <section class="preview">
    <h2>문서 Preview</h2>
    <p v-if="!file" class="placeholder">업로드된 문서가 없습니다</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <div v-else class="markdown" v-html="html"></div>
  </section>
</template>

<style scoped>
.preview {
  height: 100%;
  overflow-y: auto;
  padding: 1rem;
  border-bottom: 1px solid #ccc;
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
