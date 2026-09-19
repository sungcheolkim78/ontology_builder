<script setup>
import { FileText, MessageSquare, Network, Settings, UploadCloud } from 'lucide-vue-next'

defineProps({
  activeView: { type: String, default: 'files' },
})
const emit = defineEmits(['nav-select'])

const ITEMS = [
  { id: 'files', label: 'File Explorer', icon: UploadCloud },
  { id: 'preview', label: 'Preview', icon: FileText },
  { id: 'ontology', label: 'Ontology', icon: Network },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'settings', label: 'Settings', icon: Settings },
]
</script>

<template>
  <nav class="flex w-[76px] flex-shrink-0 flex-col items-stretch gap-1 border-r border-border bg-surface py-2">
    <button
      v-for="item in ITEMS"
      :key="item.id"
      type="button"
      :data-testid="`nav-item-${item.id}`"
      class="flex flex-col items-center gap-1 rounded-md px-1.5 py-2.5 text-[10px] font-medium transition-colors"
      :class="activeView === item.id
        ? 'bg-accent-muted/60 text-ink'
        : 'text-ink-faint hover:bg-ink/5 hover:text-ink-muted'"
      @click="emit('nav-select', item.id)"
    >
      <component :is="item.icon" :size="18" />
      {{ item.label }}
    </button>
  </nav>
</template>
