import { describe, expect, it } from 'vitest'
import { bars, compact, share, templateParts, when } from '../src/format.ts'

describe('format', () => {
  it('splits templates into fixed and variable parts', () => {
    expect(templateParts('Receiving block <*> src: <*>')).toEqual([
      { text: 'Receiving block ', variable: false },
      { text: '<*>', variable: true },
      { text: ' src: ', variable: false },
      { text: '<*>', variable: true },
    ])
  })
  it('shortens numbers and shares', () => {
    expect(compact(11175629)).toBe('11 M')
    expect(compact(4747963)).toBe('4.7 M')
    expect(compact(13845)).toBe('14 k')
    expect(share(1, 2000)).toBe('0.05%')
    expect(share(0, 0)).toBe('–')
  })
  it('reads ClickHouse timestamps as UTC', () => {
    expect(when('2008-11-09 20:35:18.000000')).toBe('9 Nov 2008, 20:35')
  })
  it('scales bars to the tallest value', () => {
    const b = bars([0, 5, 10], 30, 100)
    expect(b.map((x) => x.h)).toEqual([0, 50, 100])
    expect(b[1].x).toBe(10)
  })
})
