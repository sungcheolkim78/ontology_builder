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

function mockApi({ answersStatus = 200, answersBody = { active_schema_version: 1, answers: {} }, chatResponse } = {}) {
  apiFetch.mockImplementation((path, options) => {
    if (path.includes('/goldenset/answers')) {
      return Promise.resolve(jsonResponse(answersBody, answersStatus))
    }
    if (path.endsWith('/answer') && options?.method === 'POST') {
      return Promise.resolve(chatResponse ?? jsonResponse({}, 500))
    }
    return Promise.resolve(jsonResponse({}))
  })
}

beforeEach(() => {
  apiFetch.mockReset()
})

describe('GoldensetView', () => {
  it('shows an empty state when there are no questions', async () => {
    mockApi()
    const wrapper = mount(GoldensetView, {
      props: { report: { questions: [] }, file: { filename: 'doc_raw.md' }, hops: 1 },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('골든셋이 없습니다')
  })

  it('renders each question with its meta info and golden answer/evidence', async () => {
    mockApi()
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('보험금은 언제 지급되나요?')
    expect(wrapper.text()).toContain('attribute')
    expect(wrapper.text()).toContain('high')
    expect(wrapper.text()).toContain('사망 시 지급됩니다.')
    expect(wrapper.text()).toContain('사망 시 보험금을 지급한다')
    expect(wrapper.text()).toContain('이 문서에 없는 것은?')
  })

  it('loads and shows the existing schema-matching answer without needing to click generate', async () => {
    mockApi({
      answersBody: {
        active_schema_version: 3,
        answers: {
          q001: {
            schema_version: 3,
            hops: 2,
            generated_at: '2026-01-01T00:00:00',
            content: '기존에 생성된 답변입니다.',
            node_types: ['Policy'],
            edge_types: [],
            related_nodes: [],
            related_edges: [],
          },
        },
      },
    })
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('기존에 생성된 답변입니다.')
    // q002 has no saved answer for the active schema version.
    expect(wrapper.find('[data-testid="generate-answer-q002"]').exists()).toBe(true)
  })

  it('generates a GraphRAG answer for one question via the goldenset answer endpoint', async () => {
    mockApi({
      chatResponse: jsonResponse({
        schema_version: 1,
        hops: 2,
        generated_at: '2026-01-01T00:00:00',
        content: 'GraphRAG 답변입니다.',
        node_types: ['Policy'],
        edge_types: [],
        related_nodes: [],
        related_edges: [],
      }),
    })
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 2 },
    })
    await flushPromises()

    await wrapper.find('[data-testid="generate-answer-q001"]').trigger('click')
    await flushPromises()

    expect(apiFetch).toHaveBeenCalledWith(
      '/api/documents/doc_raw.md/goldenset/q001/answer',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ hops: 2 }),
      })
    )
    expect(wrapper.text()).toContain('GraphRAG 답변입니다.')
    // q002's own generation state must stay untouched by q001's click.
    expect(wrapper.find('[data-testid="generate-answer-q002"]').text()).toContain('답변 생성')
  })

  it('shows a loading state while a generation request is in flight', async () => {
    let resolveFetch
    mockApi({
      chatResponse: new Promise((resolve) => {
        resolveFetch = resolve
      }),
    })
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })
    await flushPromises()

    const clickPromise = wrapper.find('[data-testid="generate-answer-q001"]').trigger('click')
    await Promise.resolve()
    expect(wrapper.find('[data-testid="generate-answer-q001"]').text()).toContain('생성 중')

    resolveFetch(jsonResponse({ content: 'done', node_types: [], edge_types: [] }))
    await clickPromise
    await flushPromises()
    expect(wrapper.text()).toContain('done')
  })

  it('shows an error message when generation fails', async () => {
    mockApi({ chatResponse: jsonResponse({ detail: 'boom' }, 500) })
    const wrapper = mount(GoldensetView, {
      props: { report: REPORT, file: { filename: 'doc_raw.md' }, hops: 1 },
    })
    await flushPromises()

    await wrapper.find('[data-testid="generate-answer-q001"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toMatch(/실패/)
  })
})
