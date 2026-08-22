<script setup>
import { onMounted, ref } from 'vue'

const message = ref('불러오는 중...')

onMounted(async () => {
  try {
    const res = await fetch('/api/hello')
    const data = await res.json()
    message.value = data.message
  } catch (err) {
    message.value = 'backend 호출 실패: ' + err.message
  }
})
</script>

<template>
  <main>
    <h1>Ontology Builder</h1>
    <p>{{ message }}</p>
  </main>
</template>
