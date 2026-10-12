import { render, screen, waitFor } from '@testing-library/react'
import InvoicePrices from './InvoicePrices'
import { filterSuppliers } from './PickSupplier'

/* Сводка переплаты и поиск поставщика — то, ради чего экран пробития и
   открывают: «сколько потеряли на этой накладной» и «где тот самый поставщик». */

const ITEMS = [
  { iiko_product_id: 'cola', iiko_product_name: 'Кока-Кола 0.5', name: 'кока кола',
    quantity: 120, unit: 'шт', price_per_unit: 320 },
  { name: 'новинка без сопоставления', quantity: 1, price_per_unit: 100 },
]
const ANSWER = {
  days: 60, scope: 'network', branches: ['Астана', 'Алматы'],
  overpay: {
    total: 4200, positions: 1, cheaper: 0, compared: 1,
    lines: [{
      product_id: 'cola', name: 'Кока-Кола 0.5', quantity: 120, unit: 'шт',
      our_price: 320, best_price: 285, over_pct: 12.3, money: 4200,
      supplier_name: 'ТОО Кока-Кола', branch: 'Алматы', date: '2026-08-28',
    }],
  },
}

let sent: unknown = null

beforeEach(() => {
  sent = null
  vi.stubGlobal('fetch', vi.fn(async (_u: string, init?: RequestInit) => {
    sent = JSON.parse(String(init?.body))
    return { ok: true, json: async () => ANSWER }
  }) as unknown as typeof fetch)
})

describe('цены по накладной', () => {
  it('показывает переплату в деньгах и у кого дешевле', async () => {
    render(<InvoicePrices org="a" items={ITEMS} />)
    // 4 200 ₸ стоит дважды: итогом и в строке позиции
    await waitFor(() => expect(screen.getAllByText(/4 200/)).toHaveLength(2))
    expect(screen.getByText(/1 из 1 сравненных/)).toBeTruthy()
    expect(screen.getByText(/ТОО Кока-Кола/)).toBeTruthy()
  })

  it('несопоставленные позиции на сервер не отправляет', async () => {
    render(<InvoicePrices org="a" items={ITEMS} />)
    await waitFor(() => expect(sent).toBeTruthy())
    expect((sent as { items: unknown[] }).items).toHaveLength(1)
  })

  it('без единой сопоставленной позиции панели нет', () => {
    const { container } = render(<InvoicePrices org="a" items={[ITEMS[1]]} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('поиск поставщика', () => {
  const list = [
    { id: '1', name: 'ТОО Кока-Кола Алматы' },
    { id: '2', name: 'ИП Колесников' },
    { id: '3', name: 'ТОО Молоко' },
  ]

  it('находит по куску названия в середине', () => {
    expect(filterSuppliers(list, 'кола').map((s) => s.id)).toEqual(['1'])
  })

  it('слово, начинающееся с запроса, выше совпадения в середине', () => {
    // «Колесников» и «Кока-Кола» вперёд, «Молоко» — в конец
    expect(filterSuppliers(list, 'ко').map((s) => s.id)).toEqual(['2', '1', '3'])
  })

  it('без запроса недавние идут первыми', () => {
    expect(filterSuppliers(list, '', ['3']).map((s) => s.id)).toEqual(['3', '1', '2'])
  })
})
