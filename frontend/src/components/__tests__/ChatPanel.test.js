import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ChatPanel from '../ChatPanel.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
}))

import { apiFetch } from '../../utils/api.js'

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

const GOLDEN_REPORT = {
  questions: [
    {
      id: 'q001',
      question: '보험금은 언제 지급되나요?',
      question_type: 'attribute',
      importance: 'high',
      answerable: true,
      answer: '사망 시 지급됩니다.',
      evidence: [],
    },
  ],
  warnings: [],
}

beforeEach(() => {
  apiFetch.mockReset()
})

describe('ChatPanel goldenset toggle', () => {
  it('does not show a goldenset toggle when the document has no goldenset', async () => {
    apiFetch.mockImplementation((path) => {
      if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(null, 404))
      return Promise.resolve(jsonResponse({}))
    })
    const wrapper = mount(ChatPanel, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    expect(wrapper.find('[data-testid="chat-view-toggle"]').exists()).toBe(false)
  })

  it('shows a goldenset toggle and switches to the goldenset view on click', async () => {
    apiFetch.mockImplementation((path) => {
      if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(GOLDEN_REPORT, 200))
      return Promise.resolve(jsonResponse({}))
    })
    const wrapper = mount(ChatPanel, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    expect(wrapper.find('[data-testid="chat-view-toggle"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="goldenset-view"]').exists()).toBe(false)

    await wrapper.find('[data-testid="view-mode-golden"]').trigger('click')

    expect(wrapper.find('[data-testid="goldenset-view"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('보험금은 언제 지급되나요?')
  })

  it('re-fetches the goldenset and resets to chat view when the file changes', async () => {
    apiFetch.mockImplementation((path) => {
      if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(GOLDEN_REPORT, 200))
      return Promise.resolve(jsonResponse({}))
    })
    const wrapper = mount(ChatPanel, { props: { file: { filename: 'a_raw.md' } } })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-golden"]').trigger('click')
    expect(wrapper.find('[data-testid="goldenset-view"]').exists()).toBe(true)

    apiFetch.mockImplementation((path) => {
      if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(null, 404))
      return Promise.resolve(jsonResponse({}))
    })
    await wrapper.setProps({ file: { filename: 'b_raw.md' } })
    await flushPromises()

    expect(wrapper.find('[data-testid="chat-view-toggle"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="goldenset-view"]').exists()).toBe(false)
  })
})

describe('ChatPanel evidence citation', () => {
  function mountWithRelatedNode(relatedNode) {
    apiFetch.mockImplementation((path) => {
      if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(null, 404))
      return Promise.resolve(
        jsonResponse({
          role: 'assistant',
          content: '답변',
          node_types: [],
          edge_types: [],
          related_nodes: [relatedNode],
        })
      )
    })
    return mount(ChatPanel, { props: { file: { filename: 'doc_raw.md' } } })
  }

  async function sendAndClickChip(wrapper, label) {
    await wrapper.find('input[type="text"]').setValue('질문')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const chip = wrapper.findAll('button').find((b) => b.text() === label)
    await chip.trigger('click')
  }

  it('emits cite-evidence with evidence_text when present', async () => {
    const wrapper = mountWithRelatedNode({
      id: 'n1',
      label: '보험금',
      type: 'Benefit',
      evidence_text: '사망 시 보험금을 지급한다',
    })

    await sendAndClickChip(wrapper, '보험금')

    expect(wrapper.emitted('cite-evidence')).toEqual([[{ text: '사망 시 보험금을 지급한다' }]])
  })

  it('falls back to label when evidence_text is absent', async () => {
    const wrapper = mountWithRelatedNode({ id: 'n1', label: '보험금', type: 'Benefit' })

    await sendAndClickChip(wrapper, '보험금')

    expect(wrapper.emitted('cite-evidence')).toEqual([[{ text: '보험금' }]])
  })

  it('still emits highlight-nodes as before when a chip is clicked', async () => {
    const wrapper = mountWithRelatedNode({ id: 'n1', label: '보험금', type: 'Benefit' })

    await sendAndClickChip(wrapper, '보험금')

    expect(wrapper.emitted('highlight-nodes')).toEqual([[['n1']]])
  })
})
