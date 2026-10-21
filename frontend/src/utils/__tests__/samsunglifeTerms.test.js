import { describe, expect, it } from 'vitest'
import {
  SAMSUNGLIFE_CATEGORIES,
  SAMSUNGLIFE_STATUSES,
  countSamsunglifeTerms,
  filterSamsunglifeTerms,
  termStatus,
  toggleSelection,
} from '../samsunglifeTerms.js'

const terms = [
  { name: 'A', category: '보장성', currently_listed: true },
  { name: 'B', category: '보장성', currently_listed: false },
  { name: 'C', category: '저축성', currently_listed: true },
  { name: 'D', category: '어린이', currently_listed: false },
]
const allCategories = SAMSUNGLIFE_CATEGORIES
const allStatuses = SAMSUNGLIFE_STATUSES.map((s) => s.key)

describe('termStatus', () => {
  it('maps currently_listed to the status key', () => {
    expect(termStatus({ currently_listed: true })).toBe('listed')
    expect(termStatus({ currently_listed: false })).toBe('delisted')
  })
})

describe('filterSamsunglifeTerms', () => {
  it('keeps everything when every category and status is selected', () => {
    expect(filterSamsunglifeTerms(terms, allCategories, allStatuses)).toEqual(terms)
  })

  it('filters by category', () => {
    const result = filterSamsunglifeTerms(terms, ['보장성', '어린이'], allStatuses)
    expect(result.map((t) => t.name)).toEqual(['A', 'B', 'D'])
  })

  it('filters by status', () => {
    const result = filterSamsunglifeTerms(terms, allCategories, ['listed'])
    expect(result.map((t) => t.name)).toEqual(['A', 'C'])
  })

  it('applies both filters together, keeping the result order', () => {
    const result = filterSamsunglifeTerms(terms, ['보장성'], ['delisted'])
    expect(result.map((t) => t.name)).toEqual(['B'])
  })
})

describe('countSamsunglifeTerms', () => {
  it('counts the results per category and per status', () => {
    expect(countSamsunglifeTerms(terms)).toEqual({
      categories: { 보장성: 2, 저축성: 1, 어린이: 1 },
      statuses: { listed: 2, delisted: 2 },
    })
  })

  it('reports 0 for a category or status with no result', () => {
    expect(countSamsunglifeTerms([]).categories).toEqual({ 보장성: 0, 저축성: 0, 어린이: 0 })
  })
})

describe('toggleSelection', () => {
  it('turns a selected key off and an unselected one on', () => {
    expect(toggleSelection(['보장성', '저축성'], '저축성')).toEqual(['보장성'])
    expect(toggleSelection(['보장성'], '저축성')).toEqual(['보장성', '저축성'])
  })

  it('never turns off the last selected key', () => {
    expect(toggleSelection(['보장성'], '보장성')).toEqual(['보장성'])
  })
})
