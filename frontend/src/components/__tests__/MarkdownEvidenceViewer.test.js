import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MarkdownEvidenceViewer from '../MarkdownEvidenceViewer.vue'

describe('MarkdownEvidenceViewer', () => {
  it('renders the raw text with no highlight when there is no quote', () => {
    const wrapper = mount(MarkdownEvidenceViewer, { props: { text: '본문 내용입니다.', quote: '' } })
    expect(wrapper.text()).toContain('본문 내용입니다.')
    expect(wrapper.find('mark').exists()).toBe(false)
  })

  it('wraps the matching quote in <mark>', () => {
    const wrapper = mount(MarkdownEvidenceViewer, {
      props: { text: '사망 시 보험금을 지급한다. 그 외 조항.', quote: '보험금을 지급한다' },
    })
    const mark = wrapper.find('mark')
    expect(mark.exists()).toBe(true)
    expect(mark.text()).toBe('보험금을 지급한다')
    expect(wrapper.text()).toContain('사망 시')
    expect(wrapper.text()).toContain('그 외 조항')
  })

  it('escapes HTML-significant characters in the surrounding text', () => {
    const wrapper = mount(MarkdownEvidenceViewer, {
      props: { text: '<script>alert(1)</script> 보험금을 지급한다', quote: '보험금을 지급한다' },
    })
    expect(wrapper.html()).not.toContain('<script>alert(1)</script>')
    expect(wrapper.text()).toContain('<script>alert(1)</script>')
  })

  it('falls back to plain (unhighlighted) text when the quote is not found', () => {
    const wrapper = mount(MarkdownEvidenceViewer, {
      props: { text: '본문 내용입니다.', quote: '존재하지 않는 문구' },
    })
    expect(wrapper.find('mark').exists()).toBe(false)
    expect(wrapper.text()).toContain('본문 내용입니다.')
  })
})
