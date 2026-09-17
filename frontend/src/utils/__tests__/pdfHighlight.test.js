import { describe, expect, it } from 'vitest'
import {
  buildHaystack,
  findMatchRange,
  itemsInRange,
  normalizeForMatch,
  pageForLine,
} from '../pdfHighlight.js'

describe('normalizeForMatch', () => {
  it('strips heading markers', () => {
    expect(normalizeForMatch('### 제1조(목적)')).toBe('제1조(목적)')
  })

  it('strips bullet markers', () => {
    expect(normalizeForMatch('- 보험금을 지급합니다.')).toBe('보험금을지급합니다.')
  })

  it('strips blockquote markers', () => {
    expect(normalizeForMatch('> 갱신주기는 1년으로 합니다.')).toBe('갱신주기는1년으로합니다.')
  })

  it('strips table pipes and emphasis marks', () => {
    expect(normalizeForMatch('| **항목** | `내용` |')).toBe('항목내용')
  })

  it('collapses internal whitespace', () => {
    expect(normalizeForMatch('여러   단어   사이  공백')).toBe('여러단어사이공백')
  })
})

describe('buildHaystack / findMatchRange / itemsInRange', () => {
  const items = [
    { str: '제1조', id: 'a' },
    { str: '(목적)', id: 'b' },
    { str: ' 이 약관은', id: 'c' },
  ]

  it('finds a match spanning multiple items', () => {
    const { haystack, ranges } = buildHaystack(items)
    const match = findMatchRange(haystack, normalizeForMatch('제1조(목적)'))
    expect(match).not.toBeNull()
    const matched = itemsInRange(ranges, match)
    expect(matched.map((i) => i.id)).toEqual(['a', 'b'])
  })

  it('falls back to a shortened prefix for a long non-exact needle', () => {
    const longItems = [{ str: 'A'.repeat(30) + 'XYZ', id: 'only' }]
    const { haystack, ranges } = buildHaystack(longItems)
    const match = findMatchRange(haystack, 'A'.repeat(30) + 'DOES-NOT-MATCH')
    expect(match).not.toBeNull()
    expect(itemsInRange(ranges, match).map((i) => i.id)).toEqual(['only'])
  })

  it('returns null when nothing matches', () => {
    const { haystack } = buildHaystack(items)
    expect(findMatchRange(haystack, '전혀다른내용')).toBeNull()
  })

  it('returns null for an empty needle', () => {
    const { haystack } = buildHaystack(items)
    expect(findMatchRange(haystack, '')).toBeNull()
  })
})

describe('pageForLine', () => {
  const lines = [
    '# 제목',
    '<!-- page: 1 -->',
    '첫 페이지 내용',
    '',
    '<!-- page: 2 -->',
    '둘째 페이지 내용',
    '더 있음',
  ]

  it('uses the nearest preceding page marker', () => {
    expect(pageForLine(lines, 3)).toBe(1)
    expect(pageForLine(lines, 6)).toBe(2)
    expect(pageForLine(lines, 7)).toBe(2)
  })

  it('defaults to page 1 before any marker', () => {
    expect(pageForLine(lines, 1)).toBe(1)
  })

  it('picks up a marker that is the target line itself', () => {
    expect(pageForLine(lines, 5)).toBe(2)
  })
})
