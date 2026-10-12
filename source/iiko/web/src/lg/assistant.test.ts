import { describe, it, expect, vi } from 'vitest'
import { runAction } from './assistant'

describe('runAction', () => {
  it('navigate диспатчит lg:go с нужной вкладкой', () => {
    const spy = vi.spyOn(window, 'dispatchEvent')
    runAction({ type: 'navigate', tab: 'fc' })
    const ev = spy.mock.calls.at(-1)![0] as CustomEvent
    expect(ev.type).toBe('lg:go')
    expect(ev.detail).toBe('fc')
    spy.mockRestore()
  })
})
