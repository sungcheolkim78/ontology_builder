import { describe, expect, it } from 'vitest'
import { chunkLineCount, preambleLineCount } from '../chunkFormat.js'

describe('preambleLineCount', () => {
  it('counts an inclusive 1-based line range', () => {
    expect(preambleLineCount({ line_start: 1, line_end: 5 })).toBe(5)
  })

  it('returns 0 for an empty preamble (heading is the first line)', () => {
    expect(preambleLineCount({ line_start: 1, line_end: 0 })).toBe(0)
  })
})

describe('chunkLineCount', () => {
  it('counts an inclusive 1-based line range', () => {
    expect(chunkLineCount({ line_start: 10, line_end: 12 })).toBe(3)
  })

  it('returns 1 for a single-line chunk', () => {
    expect(chunkLineCount({ line_start: 4, line_end: 4 })).toBe(1)
  })
})
