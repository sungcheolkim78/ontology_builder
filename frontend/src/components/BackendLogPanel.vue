<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { apiFetch } from '../utils/api.js'

// Matches app.utils.event_log.MAX_EVENTS -- the backend never holds more
// than this many events anyway, so trimming the client's own list to the
// same size just keeps a long-running session's array from growing past
// what could ever be shown.
const MAX_DISPLAYED = 100

// Exponential backoff, not a fixed slow interval -- an idle backend (the
// common case between actions) should cost far fewer requests than an
// active one, without the user having to notice or tune anything beyond
// the base interval below.
const MAX_BACKOFF_MULTIPLIER = 8
const MAX_POLL_DELAY_MS = 60_000

const intervalSeconds = ref(5)
const events = ref([])
const pollError = ref('')
const logEl = ref(null)

let lastEventId = 0
let bootId = null
let backoffMultiplier = 1
let pollTimer = null
let stopped = false

const KIND_LABEL = { command: '명령어', result: '결과', error: '에러' }

function isScrolledNearBottom() {
  const el = logEl.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

async function pollOnce() {
  const shouldStickToBottom = isScrolledNearBottom()
  // Set by whichever branch below runs, then read once in `finally` --
  // schedules exactly one next poll per call, instead of a restart-detected
  // early return *and* a catch-all reschedule both queuing a timer.
  let restartDetected = false
  try {
    const res = await apiFetch(`/api/events?since=${lastEventId}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    pollError.value = ''

    // A changed boot_id means the backend restarted (a routine occurrence
    // here -- see CLAUDE.md's podman-compose down/up gotcha) since our
    // last poll: its event ids restarted from 1, so our stale, much larger
    // lastEventId would otherwise filter out every real event from here on.
    if (bootId !== null && data.boot_id !== bootId) {
      events.value = []
      lastEventId = 0
      bootId = data.boot_id
      backoffMultiplier = 1
      restartDetected = true
      return
    }
    bootId = data.boot_id

    if (data.events.length > 0) {
      events.value.push(...data.events)
      if (events.value.length > MAX_DISPLAYED) {
        events.value.splice(0, events.value.length - MAX_DISPLAYED)
      }
      lastEventId = data.events[data.events.length - 1].id
      backoffMultiplier = 1
      if (shouldStickToBottom) {
        await nextTick()
        if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
      }
    } else {
      backoffMultiplier = Math.min(backoffMultiplier * 2, MAX_BACKOFF_MULTIPLIER)
    }
  } catch (err) {
    pollError.value = '이벤트 로그 조회 실패: ' + err.message
    backoffMultiplier = Math.min(backoffMultiplier * 2, MAX_BACKOFF_MULTIPLIER)
  } finally {
    if (!stopped) scheduleNext(restartDetected ? 0 : undefined)
  }
}

function scheduleNext(delayOverrideMs) {
  const delay = delayOverrideMs ?? Math.min(intervalSeconds.value * 1000 * backoffMultiplier, MAX_POLL_DELAY_MS)
  pollTimer = setTimeout(pollOnce, delay)
}

function onIntervalChange() {
  // Applies the new base interval right away instead of waiting out
  // whatever backoff delay was already in flight.
  backoffMultiplier = 1
  clearTimeout(pollTimer)
  pollOnce()
}

onMounted(() => {
  pollOnce()
})

onUnmounted(() => {
  stopped = true
  clearTimeout(pollTimer)
})
</script>

<template>
  <section class="flex h-full flex-col overflow-hidden border-l border-border">
    <div class="panel-header">
      <span>Backend Activity</span>
      <label class="flex items-center gap-1.5 text-[11px] font-normal normal-case text-ink-faint">
        폴링 주기
        <input
          type="number"
          min="2"
          max="60"
          v-model.number="intervalSeconds"
          @change="onIntervalChange"
          class="field w-14"
        />초
      </label>
    </div>
    <div
      ref="logEl"
      class="flex-1 overflow-y-auto bg-surface-sunken p-2 font-mono text-[11px] leading-relaxed"
    >
      <p v-if="!events.length" class="text-ink-faint">이벤트 없음</p>
      <div
        v-for="event in events"
        :key="event.id"
        class="whitespace-pre-wrap break-all"
        :class="{
          'text-red-600 dark:text-red-400': event.kind === 'error',
          'text-emerald-600 dark:text-emerald-400': event.kind === 'result',
          'text-ink-muted': event.kind === 'command',
        }"
      >
        <span class="text-ink-faint">{{ event.timestamp.slice(11, 19) }}</span>
        <span class="mx-1">[{{ KIND_LABEL[event.kind] }}]</span>{{ event.message }}
      </div>
    </div>
    <p v-if="pollError" class="border-t border-border px-2 py-1 text-[11px] text-red-600 dark:text-red-400">
      {{ pollError }}
    </p>
  </section>
</template>
