import { render, screen } from '@testing-library/react'
import Progress, { networkValue, delta, weekSum, type MetricsData, type Row, type PlanWeek } from './Progress'

/* Экран собрания: цифры, которые владелец называет вслух. Ошибиться в них
   хуже, чем не показать вовсе. */

const metrics = [
  { key: 'orders', label: 'Заказов за неделю', unit: '', lower_better: false },
  { key: 'late_pct', label: 'Опоздавших заказов', unit: '%', lower_better: true },
  { key: 'kitchen', label: 'Время приготовления', unit: 'мин', lower_better: true },
  { key: 'road', label: 'Курьер в дороге', unit: 'мин', lower_better: true },
  { key: 'staff_late_pct', label: 'Опозданий сотрудников', unit: '%', lower_better: true },
]
const row = (id: string, name: string, now: Record<string, number>, prev: Record<string, number>,
             wins: { key: string; label: string }[] = []): Row =>
  ({ organization_id: id, name, now, prev, wins })

const rows: Row[] = [
  row('a', 'Астана, ул. Г.Алиева 10', { orders: 100, late_pct: 10, late_orders_count: 10, deliveries: 100, kitchen: 20, road: 30, staff_late_pct: 12, staff_late_count: 3, staff_total_count: 25 },
      { orders: 80, late_pct: 20, late_orders_count: 16, deliveries: 80, kitchen: 25, road: 30, staff_late_pct: 40, staff_late_count: 8, staff_total_count: 20 }, [{ key: 'late_pct', label: 'опоздания' }]),
  row('b', 'Астана, ул. Бокейхана 25а', { orders: 50, late_pct: 20, late_orders_count: 2, deliveries: 10, kitchen: 30, road: 40, staff_late_pct: 55, staff_late_count: 11, staff_total_count: 20 },
      { orders: 60, late_pct: 10, late_orders_count: 1, deliveries: 10, kitchen: 25, road: 40 }),
]
rows[0].staff = { total: 25, late: 3, late_pct: 12, late_minutes: 60, configured: true }
rows[0].late_cause = { key: 'kitchen', label: 'Кухня', count: 7 }
rows[0].late_orders = 11
rows[1].losses = [{ key: 'kitchen', label: 'Время приготовления', unit: 'мин', from: 25, to: 30, pct: 20 }]

/* Сетка плана: только то, что экран действительно читает. Дни 1–3 закрыты. */
const grid = (v: number) => Object.fromEntries([1, 2, 3, 4, 5, 6, 7].map((d) => [String(d), { hall: v }]))
const planWeek: PlanWeek = {
  types: ['hall'], done_days: 3,
  network: { organization_id: '', name: 'Итого сеть', plan: grid(100), fact: grid(90), prev: grid(80),
             fot: { amount: 300000, priced: 740, total: 1100, unit: 'hour', complete: false,
                    gaps: [{ role: 'Кассир', hours: 260 }] } },
  branches: [
    { organization_id: 'a', name: 'Астана, ул. Г.Алиева 10', plan: grid(60), fact: grid(66), prev: grid(50),
      fot: { amount: 180000, priced: 500, total: 500, unit: 'hour', complete: true } },
    { organization_id: 'b', name: 'Астана, ул. Бокейхана 25а', plan: grid(40), fact: grid(24), prev: grid(30),
      fot: { amount: 120000, priced: 240, total: 600, unit: 'hour', complete: false } },
  ],
}
const data: MetricsData = { week: '2026-W35', from: '2026-08-24', to: '2026-08-30',
                            done_days: 3, metrics, branches: rows }

describe('арифметика сети', () => {
  /* Проценты по сети считаются из общих счётчиков, а не средним процентов. */
  it('заказы по сети складывает', () => {
    expect(networkValue(rows, 'orders', 'now')).toBe(150)
  })
  it('опоздания взвешивает количеством доставок', () => {
    expect(networkValue(rows, 'late_pct', 'now')).toBeCloseTo(12 / 110 * 100)
  })
  it('время взвешивает количеством доставок', () => {
    expect(networkValue(rows, 'kitchen', 'now')).toBeCloseTo((20 * 100 + 30 * 10) / 110)
  })
  it('опоздания сотрудников считает по людям', () => {
    expect(networkValue(rows, 'staff_late_pct', 'now')).toBeCloseTo(14 / 45 * 100)
  })
  it('без данных не выдумывает ноль', () => {
    expect(networkValue([], 'orders', 'now')).toBeNull()
  })
})

describe('динамика', () => {
  /* Знак нормализуем по смыслу показателя: падение опозданий — это улучшение,
     падение заказов — ухудшение. Красить одинаково нельзя. */
  it('рост заказов — это лучше', () => {
    expect(delta(100, 80, false)?.better).toBe(true)
  })
  it('падение опозданий — тоже лучше', () => {
    expect(delta(10, 20, true)?.better).toBe(true)
  })
  it('рост времени приготовления — хуже', () => {
    expect(delta(30, 25, true)?.better).toBe(false)
  })
  it('считает процент от прошлой недели', () => {
    expect(Math.round(delta(100, 80, false)!.pct)).toBe(25)
  })
  it('без прошлой недели молчит, а не делит на ноль', () => {
    expect(delta(100, null, false)).toBeNull()
    expect(delta(100, 0, false)).toBeNull()
  })
})

describe('экран', () => {
  it('показывает четыре показателя и оба топа', async () => {
    render(<Progress data={data} scores={[
      { organization_id: 'a', name: 'Астана, ул. Г.Алиева 10', score: 72 },
      { organization_id: 'b', name: 'Астана, ул. Бокейхана 25а', score: 41 },
    ]} onClose={() => {}} />)

    expect(await screen.findByText('Прогресс и динамика')).toBeTruthy()
    for (const m of metrics) expect(screen.getAllByText(m.label).length).toBeGreaterThan(0)
    expect(screen.getByText('Топ по движению')).toBeTruthy()
    expect(screen.getByText('Топ по оценке')).toBeTruthy()
    expect(screen.getByText('150')).toBeTruthy()          // заказов по сети
  })

  it('без оценок не падает — топ по движению остаётся', async () => {
    render(<Progress data={data} scores={[]} onClose={() => {}} />)
    await screen.findByText('Топ по оценке')
    expect(screen.getAllByText('Пока нечего показать.').length).toBeGreaterThan(0)
  })

  /* Без сетки плана экран открывается всё равно: метрики приходят отдельным
     запросом и раньше плана. Молчать про план честнее, чем рисовать нули. */
  it('без плана не падает и про выполнение не врёт', async () => {
    render(<Progress data={data} scores={[]} onClose={() => {}} />)
    await screen.findByText('Прогресс и динамика')
    expect(screen.queryByText('План недели')).toBeNull()
    expect(screen.queryByText('Выполнение плана по филиалам')).toBeNull()
  })

  it('показывает план и факт по закрытым дням, а не по всей неделе', async () => {
    render(<Progress data={data} scores={[]} plan={planWeek} onClose={() => {}} />)
    await screen.findByText('План недели')
    // три закрытых дня: план 300, факт 270 — 90%
    expect(screen.getByText('90%')).toBeTruthy()
    expect(screen.getByText('270 из 300')).toBeTruthy()
    expect(screen.getByText(/300.000/)).toBeTruthy()
    // ФОТ считается по явкам iiko, покрытие — в ЧАСАХ: кассир со 260 ч без
    // ставки весит иначе, чем бухгалтер с двумя.
    expect(screen.getByText('740 из 1 100 ч со ставкой · 67%')).toBeTruthy()
    // и неполная сумма обязана назвать, кого именно не хватает
    expect(screen.getByText('нет ставки в iiko: Кассир (260 ч)')).toBeTruthy()
    expect(screen.getByText('за 3 закрытых дн.')).toBeTruthy()
    expect(screen.getByText('План и факт по дням')).toBeTruthy()
    expect(screen.getByText('Выполнение плана по филиалам')).toBeTruthy()
  })

  it('называет обе стороны: что улучшилось и что просело', async () => {
    render(<Progress data={data} scores={[]} plan={planWeek} onClose={() => {}} />)
    await screen.findByText('Что улучшилось')
    expect(screen.getByText('Что ухудшилось')).toBeTruthy()
    // просадка кухни у второго филиала названа поимённо
    expect(screen.getAllByText(/Бокейхана/).length).toBeGreaterThan(0)
  })

  it('опоздания сотрудников показывает с числом людей', async () => {
    render(<Progress data={data} scores={[]} plan={planWeek} onClose={() => {}} />)
    await screen.findByText('Опоздания сотрудников')
    expect(screen.getByText(/3 из 25/)).toBeTruthy()
  })

  /* Филиал, не задавший старт смены, сравнивался с дефолтом 09:00 — и получал
     «опоздали 100%», хотя смена там начинается в 10:40. Это про настройку, а
     не про людей: так и пишем, вместо выдуманного процента. */
  it('филиал без заданного старта смены не выдаёт долю опоздавших', async () => {
    const noRef: Row[] = [{ ...rows[1], now: { ...rows[1].now, staff_late_pct: null },
                            staff: { configured: false, starts: ['09:00', '16:00'] } }]
    render(<Progress data={{ ...data, branches: [rows[0], ...noRef] }} scores={[]}
                     plan={planWeek} onClose={() => {}} />)
    await screen.findByText('Опоздания сотрудников')
    expect(screen.getAllByText(/старт смены не задан/).length).toBeGreaterThan(0)
  })

  it('называет причину опозданий заказов', async () => {
    render(<Progress data={data} scores={[]} plan={planWeek} onClose={() => {}} />)
    await screen.findByText('Причина опозданий')
    expect(screen.getByText(/7 из 11/)).toBeTruthy()
  })

  it('в списках показывает все филиалы, а не пятёрку', async () => {
    const many: Row[] = Array.from({ length: 9 }, (_, i) =>
      row(`x${i}`, `Филиал ${i}`, { orders: 10 + i, late_pct: 5 + i }, { orders: 9, late_pct: 4 }))
    render(<Progress data={{ ...data, branches: many }} scores={[]} onClose={() => {}} />)
    await screen.findByText('Опоздания заказов')
    expect(screen.getAllByText('Филиал 8').length).toBeGreaterThan(0)
  })
})

describe('план по закрытым дням', () => {
  /* Живой случай: план ставится на всю неделю, а собрание во вторник. План
     семи дней против факта трёх — «провал» на ровном месте. */
  it('складывает только закрытые дни', () => {
    expect(weekSum(grid(10), ['hall'], 3)).toBe(30)
    expect(weekSum(grid(10), ['hall'], 7)).toBe(70)
    expect(weekSum(undefined, ['hall'], 3)).toBe(0)
  })
})
