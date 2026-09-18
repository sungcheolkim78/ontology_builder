import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import NavSidebar from '../NavSidebar.vue'

describe('NavSidebar', () => {
  it('renders all five nav items', () => {
    const wrapper = mount(NavSidebar, { props: { activeView: 'files' } })
    for (const id of ['files', 'preview', 'ontology', 'chat', 'settings']) {
      expect(wrapper.find(`[data-testid="nav-item-${id}"]`).exists()).toBe(true)
    }
  })

  it('highlights the active item', () => {
    const wrapper = mount(NavSidebar, { props: { activeView: 'chat' } })
    expect(wrapper.find('[data-testid="nav-item-chat"]').classes()).toContain('text-ink')
    expect(wrapper.find('[data-testid="nav-item-files"]').classes()).toContain('text-ink-faint')
  })

  it('emits nav-select with the clicked item id', async () => {
    const wrapper = mount(NavSidebar, { props: { activeView: 'files' } })
    await wrapper.find('[data-testid="nav-item-ontology"]').trigger('click')
    expect(wrapper.emitted('nav-select')).toEqual([['ontology']])
  })
})
