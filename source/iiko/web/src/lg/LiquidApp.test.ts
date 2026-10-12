import { describe, expect, it } from 'vitest'
import { SECTION_IDS, resolveSection } from './LiquidApp'

/* Белый экран 16.09: кнопка «Подключить ресторан» слала событие lg:go с
   разделом 'connect', а такого раздела не было. SECTIONS.find возвращал
   undefined, «!» прятал это от TypeScript, и sec.render роняло всё дерево. */

describe('разделы приложения', () => {
  it('раздел подключения существует — иначе кнопка ведёт в белый экран', () => {
    expect(SECTION_IDS).toContain('connect')
  })

  it('неизвестный раздел не роняет приложение, а откатывается к первому', () => {
    const s = resolveSection('такого-раздела-нет')
    // Именно это обращение и роняло дерево, когда find возвращал undefined.
    // React.lazy даёт объект, а не функцию — проверяем, что он вообще есть.
    expect(s).toBeDefined()
    expect(s.render).toBeTruthy()
    expect(s.id).toBe(SECTION_IDS[0])
  })

  it('известный раздел находится', () => {
    expect(resolveSection('plan').id).toBe('plan')
    expect(resolveSection('connect').id).toBe('connect')
  })
})
