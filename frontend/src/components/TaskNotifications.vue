<script setup>
import { computed } from 'vue'
import { dismissTask, taskState } from '../utils/taskStatus.js'

// Renders regardless of which nav tab is active (mounted once in App.vue,
// outside the v-if/v-else-if view switch), so a task started on the
// ontology tab still pops its finished/error toast even after navigating
// away from that view.
const toasts = computed(() => taskState.tasks.filter((t) => t.status !== 'running'))
</script>

<template>
  <div class="pointer-events-none fixed right-3 top-14 z-[2000] flex w-80 max-w-[90vw] flex-col gap-2">
    <div
      v-for="t in toasts"
      :key="t.id"
      class="pointer-events-auto rounded-md border px-3 py-2 text-xs shadow-lg"
      :class="t.status === 'error'
        ? 'border-red-500/50 bg-red-500/15 text-red-700 dark:text-red-400'
        : 'border-emerald-500/50 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'"
    >
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0">
          <p class="font-medium">{{ t.label }}</p>
          <p v-if="t.message" class="mt-0.5 text-ink-muted">{{ t.message }}</p>
        </div>
        <button
          type="button"
          class="flex-shrink-0 text-ink-faint hover:text-ink"
          @click="dismissTask(t.id)"
        >
          ✕
        </button>
      </div>
    </div>
  </div>
</template>
