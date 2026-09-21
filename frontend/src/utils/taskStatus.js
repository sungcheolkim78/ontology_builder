import { reactive } from 'vue'

// Global, module-level task tracker -- same singleton-reactive() pattern as
// authState (api.js) / themeState (theme.js). Living outside any component
// instance is the point: App.vue mounts each nav view with v-if/v-else-if
// (no <KeepAlive>), so a long-running ontology operation started from
// OntologyWorkflowView must be tracked here to survive the user switching to
// another tab and back -- the button's own component-local refs get torn
// down on unmount, but this object doesn't.
export const taskState = reactive({
  tasks: [], // { id, label, status: 'running' | 'success' | 'error', message, startedAt, finishedAt }
})

const AUTO_DISMISS_MS = 8000
let nextTaskId = 1

export function startTask(label) {
  const id = nextTaskId++
  taskState.tasks.push({
    id,
    label,
    status: 'running',
    message: '',
    startedAt: Date.now(),
    finishedAt: null,
  })
  return id
}

export function finishTask(id, { status = 'success', message = '' } = {}) {
  const task = taskState.tasks.find((t) => t.id === id)
  if (!task) return
  task.status = status
  task.message = message
  task.finishedAt = Date.now()
  setTimeout(() => dismissTask(id), AUTO_DISMISS_MS)
}

export function dismissTask(id) {
  const index = taskState.tasks.findIndex((t) => t.id === id)
  if (index !== -1) taskState.tasks.splice(index, 1)
}
