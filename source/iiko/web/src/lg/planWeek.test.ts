import { fotGapsLine, isoWeekOf, mondayOfWeek, planPct, shiftWeek } from './planWeek'
import type { Fot } from './planWeek'

const iso = (d: Date) => d.toISOString().slice(0, 10)

describe('недели ISO во вкладке «План»', () => {
  it('номер недели совпадает с нумерацией в таблице владельца', () => {
    expect(isoWeekOf(new Date(2026, 7, 25))).toBe('2026-W35')
    expect(iso(mondayOfWeek('2026-W35'))).toBe('2026-08-24')
  })

  it('воскресенье остаётся в своей неделе, понедельник начинает следующую', () => {
    expect(isoWeekOf(new Date(2026, 7, 30))).toBe('2026-W35')
    expect(isoWeekOf(new Date(2026, 7, 31))).toBe('2026-W36')
  })

  it('переход через новый год не теряет неделю', () => {
    // 2026-W53 существует: 2026 год начинается с четверга
    expect(shiftWeek('2027-W01', -1)).toBe('2026-W53')
    expect(shiftWeek('2026-W53', 1)).toBe('2027-W01')
  })

  it('шаг вперёд и назад возвращает на место', () => {
    for (const w of ['2026-W01', '2026-W35', '2026-W52']) {
      expect(shiftWeek(shiftWeek(w, 1), -1)).toBe(w)
    }
  })

  it('понедельник недели — всегда понедельник', () => {
    for (const w of ['2026-W01', '2026-W09', '2026-W35', '2026-W53']) {
      expect(mondayOfWeek(w).getUTCDay()).toBe(1)
    }
  })
})

describe('процент выполнения плана', () => {
  it('округление не объявляет план выполненным', () => {
    // 996 из 1000 — это 99,6%. Показать «100%» зелёным значит соврать на
    // собрании: план НЕ выполнен, а выглядит выполненным.
    expect(planPct(996, 1000)).toBe(99)
    expect(planPct(9990, 10000)).toBe(99)
  })

  it('ровно выполненный и перевыполненный план — сто и больше', () => {
    expect(planPct(1000, 1000)).toBe(100)
    expect(planPct(1004, 1000)).toBe(100)
    expect(planPct(1200, 1000)).toBe(120)
  })

  it('обычные значения округляются как раньше', () => {
    expect(planPct(850, 1000)).toBe(85)
    expect(planPct(856, 1000)).toBe(86)
    expect(planPct(0, 1000)).toBe(0)
  })

  it('без плана процента нет, а не деление на ноль', () => {
    expect(planPct(500, 0)).toBe(0)
  })
})

describe('строка «кого не хватает» в ФОТ', () => {
  const fot = (gaps: { role: string; hours: number }[]): Fot =>
    ({ amount: 0, priced: 0, total: 0, unit: 'hour', complete: false, gaps })

  // toLocaleString разделяет разряды неразрывным пробелом — сравниваем по смыслу,
  // а не по коду пробела.
  const plain = (s: string) => s.replace(/[\u00a0\u202f]/g, ' ')

  it('до трёх должностей перечисляет поимённо', () => {
    expect(plain(fotGapsLine(fot([{ role: 'Кассир', hours: 2595 }, { role: 'Курьер', hours: 1537 }]))))
      .toBe('нет ставки в iiko: Кассир (2 595 ч), Курьер (1 537 ч)')
  })

  it('остаток договаривает, а не молчит о нём', () => {
    // Иначе под «2158 из 6181 ч со ставкой» стоял бы список, объясняющий
    // только часть разницы, и числа на собрании не сошлись бы.
    const line = fotGapsLine(fot([
      { role: 'Кассир', hours: 2595 }, { role: 'Курьер', hours: 1537 },
      { role: 'Тех персонал', hours: 1298 }, { role: 'Официант', hours: 660 },
      { role: 'Повар', hours: 332 },
    ]))
    expect(plain(line)).toContain('и ещё 992 ч по 2 должн.')
  })

  it('без дыр строки нет', () => {
    expect(fotGapsLine(fot([]))).toBe('')
    expect(fotGapsLine(undefined)).toBe('')
  })
})
