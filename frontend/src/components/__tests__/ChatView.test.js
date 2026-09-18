import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ChatView from '../ChatView.vue'
import ChatPanel from '../ChatPanel.vue'
import MarkdownEvidenceViewer from '../MarkdownEvidenceViewer.vue'
import PdfViewer from '../PdfViewer.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
  API_BASE: '',
  authState: { token: null },
}))

vi.mock('pdfjs-dist', () => ({
  getDocument: vi.fn(),
  GlobalWorkerOptions: {},
}))
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '' }))

import { apiFetch } from '../../utils/api.js'

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body, text: async () => body }
}

function mockApi(rawText = '사망 시 보험금을 지급한다.') {
  apiFetch.mockImplementation((path) => {
    if (path.includes('/goldenset')) return Promise.resolve(jsonResponse(null, 404))
    if (path.startsWith('/api/files/')) return Promise.resolve(jsonResponse(rawText, 200))
    return Promise.resolve(jsonResponse({}))
  })
}

beforeEach(() => {
  apiFetch.mockReset()
})

describe('ChatView', () => {
  it('shows MarkdownEvidenceViewer (not PdfViewer) for a document without a pdf', async () => {
    mockApi()
    const wrapper = mount(ChatView, { props: { file: { filename: 'doc_raw.md', has_pdf: false } } })
    await flushPromises()

    expect(wrapper.findComponent(PdfViewer).exists()).toBe(false)
    expect(wrapper.findComponent(MarkdownEvidenceViewer).exists()).toBe(true)
  })

  it('shows PdfViewer (not MarkdownEvidenceViewer) for a document with a pdf', async () => {
    mockApi()
    const wrapper = mount(ChatView, {
      props: { file: { filename: 'doc_raw.md', has_pdf: true } },
      global: { stubs: { PdfViewer: true } },
    })
    await flushPromises()

    expect(wrapper.findComponent(PdfViewer).exists()).toBe(true)
    expect(wrapper.findComponent(MarkdownEvidenceViewer).exists()).toBe(false)
  })

  it('passes the cited quote to MarkdownEvidenceViewer when ChatPanel emits cite-evidence', async () => {
    mockApi('전문 사망 시 보험금을 지급한다 후문')
    const wrapper = mount(ChatView, { props: { file: { filename: 'doc_raw.md', has_pdf: false } } })
    await flushPromises()

    wrapper.findComponent(ChatPanel).vm.$emit('cite-evidence', { text: '보험금을 지급한다' })
    await flushPromises()

    expect(wrapper.findComponent(MarkdownEvidenceViewer).props('quote')).toBe('보험금을 지급한다')
  })

  it('pushes a page jump to PdfViewer when ChatPanel emits cite-evidence for a pdf document', async () => {
    mockApi('# 제목\n<!-- page: 2 -->\n사망 시 보험금을 지급한다.')
    const wrapper = mount(ChatView, {
      props: { file: { filename: 'doc_raw.md', has_pdf: true } },
      global: { stubs: { PdfViewer: true } },
    })
    await flushPromises()

    wrapper.findComponent(ChatPanel).vm.$emit('cite-evidence', { text: '보험금을 지급한다' })
    await flushPromises()

    expect(wrapper.findComponent(PdfViewer).props('jumpRequest')).toMatchObject({ page: 2 })
  })

  it('forwards highlight-nodes and toggle-type from ChatPanel', async () => {
    mockApi()
    const wrapper = mount(ChatView, { props: { file: { filename: 'doc_raw.md', has_pdf: false } } })
    await flushPromises()

    wrapper.findComponent(ChatPanel).vm.$emit('highlight-nodes', ['n1'])
    wrapper.findComponent(ChatPanel).vm.$emit('toggle-type', { kind: 'node', type: 'Person' })

    expect(wrapper.emitted('highlight-nodes')).toEqual([[['n1']]])
    expect(wrapper.emitted('toggle-type')).toEqual([[{ kind: 'node', type: 'Person' }]])
  })
})
