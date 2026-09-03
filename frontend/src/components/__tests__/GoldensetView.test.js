import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import GoldensetView from '../GoldensetView.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
}))

import { apiFetch } from '../../utils/api.js'

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

const REPORT = {
  source_file: 'doc_raw.md',
  questions: [
    {
      id: 'q001',
      question: '보험금은 언제 지급되나요?',
      question_type: 'attribute',
      importance: 'high',
      answerable: true,
      answer: '사망 시 지급됩니다.',
      evidence: [{ quote: '사망 시 보험금을 지급한다', line_start: 10, line_end: 10 }],
    },
    {
      id: 'q002',
      question: '이 문서에 없는 것은?',
      question_type: 'unanswerable',
      importance: 'medium',
      answerable: false,
      answer: null,
      evidence: [],
    },
  ],
  warnings: [],
}

beforeEach(() => {
  apiFetch.mockReset()
})

describe('GoldensetView', () => {
  it('shows an empty state when there are no questions', () => {
    const wrapper = mount(GoldensetView, {
      props: { report: { questions: [] }, file: { filename: 'doc_raw.md' }, hops: 1 },
    })

    expect(wrapper.text()).toContain('골든셋이 없습니다')
  })

  it('renders each question with its meta info and golden answer/evidence', () => {
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })

    expect(wrapper.text()).toContain('보험금은 언제 지급되나요?')
    expect(wrapper.text()).toContain('attribute')
    expect(wrapper.text()).toContain('high')
    expect(wrapper.text()).toContain('사망 시 지급됩니다.')
    expect(wrapper.text()).toContain('사망 시 보험금을 지급한다')
    expect(wrapper.text()).toContain('이 문서에 없는 것은?')
  })

  it('generates a GraphRAG answer for one question and shows it beside the golden answer', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({
        role: 'assistant',
        content: 'GraphRAG 답변입니다.',
        node_types: ['Policy'],
        edge_types: [],
        related_nodes: [],
      })
    )
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 2 },
    })

    await wrapper.find('[data-testid="generate-answer-q001"]').trigger('click')
    await flushPromises()

    expect(apiFetch).toHaveBeenCalledWith(
      '/api/chat',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          messages: [{ role: 'user', content: '보험금은 언제 지급되나요?' }],
          filename: 'doc_raw.md',
          hops: 2,
        }),
      })
    )
    expect(wrapper.text()).toContain('GraphRAG 답변입니다.')
    // q002's own generation state must stay untouched by q001's click.
    expect(wrapper.find('[data-testid="generate-answer-q002"]').text()).toContain('답변 생성')
  })

  it('shows a loading state while a generation request is in flight', async () => {
    let resolveFetch
    apiFetch.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve
      })
    )
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })

    const clickPromise = wrapper.find('[data-testid="generate-answer-q001"]').trigger('click')
    await Promise.resolve()
    expect(wrapper.find('[data-testid="generate-answer-q001"]').text()).toContain('생성 중')

    resolveFetch(jsonResponse({ content: 'done', node_types: [], edge_types: [] }))
    await clickPromise
    await flushPromises()
    expect(wrapper.text()).toContain('done')
  })

  it('shows an error message when generation fails', async () => {
    apiFetch.mockResolvedValue(jsonResponse({ detail: 'boom' }, 500))
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })

    await wrapper.find('[data-testid="generate-answer-q001"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toMatch(/실패/)
  })
})
