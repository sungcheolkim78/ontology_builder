import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChunkView from '../ChunkView.vue'

const SAMPLE_DATA = {
  source: 'doc_raw',
  preamble: { line_start: 1, line_end: 3, text: '표지 텍스트' },
  chunks: [
    {
      id: '0::제1조',
      section_index: 0,
      section_label: '주계약',
      article_no: '1',
      sub_no: null,
      title: '목적',
      path: '주계약 > 제1조(목적)',
      line_start: 4,
      line_end: 6,
      text: '이 계약은 **성립**됩니다.',
    },
    {
      id: '0::제2조',
      section_index: 0,
      section_label: '주계약',
      article_no: '2',
      sub_no: null,
      title: '정의',
      path: '주계약 > 제2조(정의)',
      line_start: 7,
      line_end: 9,
      text: '용어의 정의는 다음과 같습니다.',
    },
  ],
}

function mountView(data = SAMPLE_DATA) {
  return mount(ChunkView, { props: { data } })
}

describe('ChunkView', () => {
  it('shows the source and preamble line count, not the preamble text', () => {
    const wrapper = mountView()
    expect(wrapper.text()).toContain('doc_raw')
    expect(wrapper.text()).toContain('3')
    expect(wrapper.text()).not.toContain('표지 텍스트')
  })

  it('renders one row per chunk showing its path', () => {
    const wrapper = mountView()
    expect(wrapper.text()).toContain('주계약 > 제1조(목적)')
    expect(wrapper.text()).toContain('주계약 > 제2조(정의)')
  })

  it('starts with every chunk collapsed (no rendered chunk text)', () => {
    const wrapper = mountView()
    expect(wrapper.html()).not.toContain('<strong>성립</strong>')
    expect(wrapper.text()).not.toContain('용어의 정의는 다음과 같습니다.')
  })

  it('expands a chunk on click, rendering its text as markdown', async () => {
    const wrapper = mountView()
    const rows = wrapper.findAll('[data-testid="chunk-row-header"]')

    await rows[0].trigger('click')

    expect(wrapper.html()).toContain('<strong>성립</strong>')
    // the other chunk stays collapsed
    expect(wrapper.text()).not.toContain('용어의 정의는 다음과 같습니다.')
  })

  it('collapses an expanded chunk back on a second click', async () => {
    const wrapper = mountView()
    const rows = wrapper.findAll('[data-testid="chunk-row-header"]')

    await rows[0].trigger('click')
    await rows[0].trigger('click')

    expect(wrapper.html()).not.toContain('<strong>성립</strong>')
  })

  it('allows multiple chunks to be expanded independently', async () => {
    const wrapper = mountView()
    const rows = wrapper.findAll('[data-testid="chunk-row-header"]')

    await rows[0].trigger('click')
    await rows[1].trigger('click')

    expect(wrapper.html()).toContain('<strong>성립</strong>')
    expect(wrapper.text()).toContain('용어의 정의는 다음과 같습니다.')
  })
})
