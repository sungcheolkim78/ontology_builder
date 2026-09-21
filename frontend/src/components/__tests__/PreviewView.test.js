import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PreviewView from '../PreviewView.vue'
import PdfViewer from '../PdfViewer.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
  API_BASE: '',
  authState: { token: null },
}))

// PdfViewer.vue imports pdfjs-dist at module scope, and pdfjs-dist itself
// references browser APIs (DOMMatrix) jsdom doesn't provide -- mocking the
// package here is what lets PreviewView.vue's own (unrelated) tests import it
// at all. PdfViewer's own rendering/highlight logic is covered separately
// (see PdfViewer.test.js and utils/__tests__/pdfHighlight.test.js).
vi.mock('pdfjs-dist', () => ({
  getDocument: vi.fn(),
  GlobalWorkerOptions: {},
}))
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '' }))

import { apiFetch } from '../../utils/api.js'

const CHUNK_DATA = {
  source: 'doc_raw',
  preamble: { line_start: 1, line_end: 2, text: '표지' },
  chunks: [
    {
      id: '0::제1조',
      section_index: 0,
      section_label: '주계약',
      article_no: '1',
      sub_no: null,
      title: '목적',
      path: '주계약 > 제1조(목적)',
      line_start: 3,
      line_end: 5,
      text: '본문 내용',
    },
  ],
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body, text: async () => body }
}

function mockApi({ chunkStatus = 404, chunkBody = null } = {}) {
  apiFetch.mockImplementation((path) => {
    if (path.includes('/chunk')) {
      return Promise.resolve(jsonResponse(chunkBody, chunkStatus))
    }
    return Promise.resolve(jsonResponse('# 원문 내용', 200))
  })
}

beforeEach(() => {
  apiFetch.mockReset()
})

describe('PreviewView chunk toggle (no PDF)', () => {
  it('does not render a view toggle when the document has no chunks', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(false)
  })

  it('renders a view toggle defaulting to raw view when chunks exist', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('switches to the chunk view on click', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')

    expect(wrapper.text()).toContain('주계약 > 제1조(목적)')
  })

  it('switches back to the raw view on click', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    await wrapper.find('[data-testid="view-mode-raw"]').trigger('click')

    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('resets to raw view when the file changes', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(PreviewView, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(true)

    await wrapper.setProps({ file: { filename: 'other_raw.md' } })
    await flushPromises()

    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })
})

describe('PreviewView PDF split', () => {
  function mountWithStubbedPdfViewer(file) {
    return mount(PreviewView, {
      props: { file },
      global: { stubs: { PdfViewer: true } },
    })
  }

  it('does not render the PDF pane when the document has no source pdf', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: false })
    await flushPromises()

    expect(wrapper.findComponent(PdfViewer).exists()).toBe(false)
  })

  it('renders the PDF pane alongside the MD/Chunk tabs when the document has a source pdf', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()

    expect(wrapper.findComponent(PdfViewer).exists()).toBe(true)
    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="view-mode-pdf"]').exists()).toBe(false)
  })

  it('does not push an automatic jump request when the document loads', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()

    const pdfViewer = wrapper.findComponent(PdfViewer)
    expect(pdfViewer.props('jumpRequest')).toBeNull()
  })

  it('switches to the raw view when the PDF pane emits a sync request', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(true)

    await wrapper.findComponent(PdfViewer).vm.$emit('sync', 1)
    await flushPromises()

    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('pushes a jump request to the PDF pane when a chunk is selected', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()

    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    // ChunkView is real (not stubbed) here, so clicking its row triggers the
    // same chunk-selected emit PreviewView listens for.
    await wrapper.find('[data-testid="chunk-row-header"]').trigger('click')

    const pdfViewer = wrapper.findComponent(PdfViewer)
    expect(pdfViewer.props('jumpRequest')).toMatchObject({ page: 1 })
  })

  it('removes the PDF pane when the file changes to one without a pdf', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mountWithStubbedPdfViewer({ filename: 'doc_raw.md', has_pdf: true })
    await flushPromises()

    await wrapper.setProps({ file: { filename: 'other_raw.md', has_pdf: false } })
    await flushPromises()

    expect(wrapper.findComponent(PdfViewer).exists()).toBe(false)
  })
})
