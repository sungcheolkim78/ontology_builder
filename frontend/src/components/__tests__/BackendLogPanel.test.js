import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BackendLogPanel from '../BackendLogPanel.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
  API_BASE: '',
  authState: { token: null },
}))

import { apiFetch } from '../../utils/api.js'

function jsonResponse(body) {
  return { ok: true, status: 200, json: async () => body }
}

function eventsResponse(events, bootId = 'boot-1') {
  return jsonResponse({ events, boot_id: bootId })
}

beforeEach(() => {
  apiFetch.mockReset()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('BackendLogPanel polling', () => {
  it('fetches events on mount and renders them', async () => {
    apiFetch.mockResolvedValueOnce(
      eventsResponse([{ id: 1, timestamp: '2026-09-26T01:02:03+00:00', kind: 'command', message: 'GET /api/documents' }])
    )
    const wrapper = mount(BackendLogPanel)
    await flushPromises()

    expect(apiFetch).toHaveBeenCalledWith('/api/events?since=0')
    expect(wrapper.text()).toContain('GET /api/documents')
  })

  it('backs off after an empty poll instead of polling again at the base interval', async () => {
    apiFetch.mockResolvedValue(eventsResponse([]))
    mount(BackendLogPanel)
    await flushPromises()
    expect(apiFetch).toHaveBeenCalledTimes(1)

    // Base interval (default 5s) elapses with nothing new seen yet -- the
    // next poll should be scheduled further out, not immediately again.
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiFetch).toHaveBeenCalledTimes(1)

    // Backed-off delay (5s * 2 backoff multiplier) has now elapsed.
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiFetch).toHaveBeenCalledTimes(2)
  })

  it('resets backoff and polls again once a new event shows up', async () => {
    apiFetch
      .mockResolvedValueOnce(eventsResponse([])) // poll 1 (mount): empty -> backoff x2, next in 10s
      .mockResolvedValueOnce(eventsResponse([])) // poll 2 (t=10s): empty -> backoff x4, next in 20s
      .mockResolvedValueOnce(
        eventsResponse([{ id: 1, timestamp: '2026-09-26T01:00:00+00:00', kind: 'result', message: 'ok' }])
      ) // poll 3 (t=30s): event found -> backoff reset, next in 5s
      .mockResolvedValue(eventsResponse([]))
    mount(BackendLogPanel)
    await flushPromises()
    expect(apiFetch).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(10000)
    expect(apiFetch).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(20000)
    expect(apiFetch).toHaveBeenCalledTimes(3)

    // Backoff reset by the event above -- next poll should be back at the
    // 5s base interval, not another backed-off delay.
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiFetch).toHaveBeenCalledTimes(4)
  })

  it('resets its cursor and refetches immediately when the backend restarts', async () => {
    apiFetch
      .mockResolvedValueOnce(
        eventsResponse([{ id: 5, timestamp: '2026-09-26T01:00:00+00:00', kind: 'command', message: 'before restart' }], 'boot-1')
      )
      // The backend restarted: its ids reset from 1, so filtering by our
      // stale since=5 legitimately finds nothing yet -- only the changed
      // boot_id tips the frontend off.
      .mockResolvedValueOnce(eventsResponse([], 'boot-2'))
      // Immediate follow-up poll at since=0, once the restart is detected.
      .mockResolvedValueOnce(
        eventsResponse([{ id: 1, timestamp: '2026-09-26T01:05:00+00:00', kind: 'command', message: 'after restart' }], 'boot-2')
      )
    const wrapper = mount(BackendLogPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('before restart')

    await vi.advanceTimersByTimeAsync(5000)
    await vi.advanceTimersByTimeAsync(1) // the immediate (0-delay) re-poll scheduled on restart detection
    await flushPromises()

    expect(apiFetch).toHaveBeenNthCalledWith(2, '/api/events?since=5')
    expect(apiFetch).toHaveBeenNthCalledWith(3, '/api/events?since=0')
    expect(wrapper.text()).not.toContain('before restart')
    expect(wrapper.text()).toContain('after restart')
  })

  it('applies a new polling interval immediately', async () => {
    apiFetch.mockResolvedValue(eventsResponse([]))
    const wrapper = mount(BackendLogPanel)
    await flushPromises()
    expect(apiFetch).toHaveBeenCalledTimes(1)

    // setValue() on a native <input> already dispatches both 'input' and
    // 'change' -- @change is what the component listens on.
    await wrapper.find('input[type="number"]').setValue(30)
    await flushPromises()

    expect(apiFetch).toHaveBeenCalledTimes(2)
  })
})
