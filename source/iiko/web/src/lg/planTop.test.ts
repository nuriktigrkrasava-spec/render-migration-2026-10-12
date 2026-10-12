import { topImprovers } from './Plan'

type Row = { organization_id: string; name: string; now: Record<string, number | null>; prev: Record<string, number | null>; wins: [] }
const br = (name: string, now: Record<string, number | null>, prev: Record<string, number | null>): Row =>
  ({ organization_id: name, name, now, prev, wins: [] })

describe('топ филиалов «кто прибавил»', () => {
  it('берёт только выросших по заказам и сортирует по приросту', () => {
    const rows = [
      br('А', { orders: 300 }, { orders: 250 }),   // +50
      br('Б', { orders: 210 }, { orders: 200 }),   // +10
      br('В', { orders: 180 }, { orders: 200 }),   // упал — не берём
    ]
    const top = topImprovers(rows as never, 'orders')
    expect(top.map((t) => t.name)).toEqual(['А', 'Б'])
    expect(top[0]).toMatchObject({ from: 250, to: 300, diff: 50, pct: 20 })
  })

  it('прирост меньше процента — шум, в топ не идёт', () => {
    // +5 из 1000 — это полпроцента: назвать такой филиал вслух не за что
    const rows = [br('А', { orders: 1005 }, { orders: 1000 })]
    expect(topImprovers(rows as never, 'orders')).toEqual([])
  })

  it('опоздания: меньше — лучше, считаем в процентных пунктах', () => {
    const rows = [
      br('А', { late_pct: 4 }, { late_pct: 12 }),    // −8 п.п.
      br('Б', { late_pct: 9 }, { late_pct: 11 }),    // −2 п.п.
      br('В', { late_pct: 15 }, { late_pct: 10 }),   // выросли — не берём
    ]
    const top = topImprovers(rows as never, 'late_pct')
    expect(top.map((t) => t.name)).toEqual(['А', 'Б'])
    expect(top[0]).toMatchObject({ from: 12, to: 4, diff: 8 })
  })

  it('сдвиг меньше половины пункта по опозданиям — не улучшение', () => {
    // 0,3 п.п. — это один заказ из трёхсот, дрожь замера
    const rows = [br('А', { late_pct: 9.7 }, { late_pct: 10 })]
    expect(topImprovers(rows as never, 'late_pct')).toEqual([])
  })

  it('филиал без одной из цифр молча пропускается, а не считается нулём', () => {
    // iiko промолчал по доставкам — «улучшение с нуля» было бы враньём
    const rows = [
      br('А', { late_pct: null }, { late_pct: 10 }),
      br('Б', { orders: 300 }, {}),
    ]
    expect(topImprovers(rows as never, 'late_pct')).toEqual([])
    expect(topImprovers(rows as never, 'orders')).toEqual([])
  })

  it('в топе не больше пяти строк — это список для собрания, а не таблица', () => {
    const rows = Array.from({ length: 9 }, (_, i) =>
      br(`Ф${i}`, { orders: 200 + (i + 1) * 10 }, { orders: 200 }))
    expect(topImprovers(rows as never, 'orders')).toHaveLength(5)
  })
})

import { prevWeekCells } from './Plan'

const TYPES = ['delivery', 'hall', 'pickup', 'agg'] as const
const grid = (v: number) => Object.fromEntries(
  Array.from({ length: 7 }, (_, i) => [String(i + 1), Object.fromEntries(TYPES.map((t) => [t, v]))]))
const branch = (id: string, planValue: number) => ({
  organization_id: id, name: id,
  plan: grid(planValue), fact: grid(0), base: grid(0), prev: grid(0),
  total_plan: Object.fromEntries(TYPES.map((t) => [t, planValue * 7])),
  total_fact: {}, total_base: {}, total_prev: {},
})

describe('перенос плана прошлой недели', () => {
  it('переносит только филиалы, у которых план на прошлой неделе был', () => {
    const { cells, moved } = prevWeekCells([branch('А', 10), branch('Б', 0)] as never, TYPES)
    expect(moved).toBe(1)
    expect(new Set(cells.map((c) => c.org))).toEqual(new Set(['А']))
    expect(cells).toHaveLength(7 * TYPES.length)
  })

  /* Живой случай 26.08: план стоял у всех девяти филиалов текущей недели, а на
     прошлой — только у одного. Старый код рассылал нули и стирал восемь. */
  it('не рассылает нули по филиалам без прошлого плана', () => {
    const branches = [branch('А', 12), ...['Б', 'В', 'Г', 'Д', 'Е', 'Ж', 'З', 'И'].map((n) => branch(n, 0))]
    const { cells, moved } = prevWeekCells(branches as never, TYPES)
    expect(moved).toBe(1)
    expect(cells.every((c) => c.planned > 0)).toBe(true)
    expect(cells.some((c) => c.org !== 'А')).toBe(false)
  })

  it('прошлая неделя пуста целиком — переносить нечего, ничего не шлём', () => {
    const { cells, moved } = prevWeekCells([branch('А', 0), branch('Б', 0)] as never, TYPES)
    expect(moved).toBe(0)
    expect(cells).toEqual([])
  })
})
