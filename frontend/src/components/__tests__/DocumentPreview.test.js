import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DocumentPreview from '../DocumentPreview.vue'

vi.mock('../../utils/api.js', () => ({
  apiFetch: vi.fn(),
}))

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

describe('DocumentPreview chunk toggle', () => {
  it('does not render a view toggle when the document has no chunks', async () => {
    mockApi({ chunkStatus: 404 })
    const wrapper = mount(DocumentPreview, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(false)
  })

  it('renders a view toggle defaulting to raw view when chunks exist', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(DocumentPreview, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    expect(wrapper.find('[data-testid="view-toggle"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('switches to the chunk view on click', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(DocumentPreview, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')

    expect(wrapper.text()).toContain('주계약 > 제1조(목적)')
  })

  it('switches back to the raw view on click', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(DocumentPreview, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()

    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    await wrapper.find('[data-testid="view-mode-raw"]').trigger('click')

    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })

  it('resets to raw view when the file changes', async () => {
    mockApi({ chunkStatus: 200, chunkBody: CHUNK_DATA })
    const wrapper = mount(DocumentPreview, { props: { file: { filename: 'doc_raw.md' } } })
    await flushPromises()
    await wrapper.find('[data-testid="view-mode-chunk"]').trigger('click')
    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(true)

    await wrapper.setProps({ file: { filename: 'other_raw.md' } })
    await flushPromises()

    expect(wrapper.find('[data-testid="chunk-row-header"]').exists()).toBe(false)
  })
})
