import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import TeriyakiAct from './TeriyakiAct'
import SalmonAct from './SalmonAct'

/* Терияки — передел, а не разбор. Требование со скрина: жарки в разборе быть не
   должно, у неё своя вкладка, свои два веса и граммы соуса. */

const CTX = {
  role: 'manager',
  branch: { organization_id: 'org-1', name: 'Астана, Бейбітшілік' },
  branches: [{ organization_id: 'org-1', name: 'Астана, Бейбітшілік' }],
  products: {
    trim: { id: '2', name: 'П/Ф ОБРЕЗКИ ЛОСОСЬ' },
    teriyaki: { id: '4', name: 'П/Ф ЛОСОСЬ ТЕРИЯКИ' },
    sauce: { id: '7', name: 'Соус терияки' },
  },
  trim_price: 8800,
  sauce_price: 4200,
  sauce_from: 'Азия Фуд · 2026-09-02',
  kind: 'teriyaki',
  title: 'Терияки из обрезков',
  kinds: [{ key: 'salmon', title: 'Разбор лосося', tab: 'Лосось' },
          { key: 'eel', title: 'Разбор угря', tab: 'Угорь' },
          { key: 'teriyaki', title: 'Терияки из обрезков', tab: 'Терияки' }],
}

const SALMON = {
  ...CTX,
  products: {
    raw: { id: '1', name: 'П/Ф ЛОСОСЬ СТЕЙК' },
    slice: { id: '3', name: 'П/Ф НАРЕЗКА ЛОСОСЬ' },
    trim: { id: '2', name: 'П/Ф ОБРЕЗКИ ЛОСОСЬ' },
    waste: { id: '6', name: 'Отход лосося' },
  },
  price_per_kg: 9300, price_from: 'ЦЕХ · 2026-08-30', trim_const: true,
  kind: 'salmon', title: 'Разбор лосося', raw_label: 'Стейк в разбор', raw_short: 'стейк',
}

/* 2 кг обрезков и 200 г соуса → 1.4 кг терияки. Вход 2.2 кг: соус лили на ту же
   сковороду, и выход считается от него — 1.4 / 2.2 = 63.6 %, ужарка 0.8 кг.
   Себестоимость наращена на отход: 18 440 × (1 + 0.8/2.2) = 25 145.45 ₸, то есть
   на 6 705.45 ₸ больше списанного. Цифры — ровно то, что отдаёт fry_teriyaki. */
const CALC = {
  ok: true, money: true, need: '',
  input_weight: 2.2,
  trim: { weight: 2, price_per_kg: 8800, sum: 17600 },
  sauce: { grams: 200, price_per_kg: 4200, sum: 840 },
  teriyaki: { weight: 1.4, price_per_kg: 17961.04, sum: 25145.45, from_trim: 2, from_input: 2.2, shrink: 0.8 },
  yield_pct: 63.6, shrink_pct: 36.4, sauce_g_per_kg: 142.9,
  spent: 18440, markup: 6705.45, check: 18440,
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, opts?: RequestInit) => ({
    ok: true,
    json: async () => (String(url).includes('/period') ? { acts: [], total: { acts: 0 } }
      : String(url).includes('/network') ? { branches: [] }
      : opts?.method === 'POST' ? CALC
      : String(url).includes('/api/teriyaki/') ? CTX : SALMON),
  })) as unknown as typeof fetch)
})

describe('вкладка терияки', () => {
  it('спрашивает два веса и граммы соуса, ужарку считает сама', async () => {
    render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    // плитки узнаём по подписи под полем — она у каждой своя
    expect(screen.getByText('сколько сняли')).toBeTruthy()
    expect(screen.getByText('сколько налили')).toBeTruthy()
    // ужарка не вводится — она разница двух весов
    expect(screen.getByText('обрезки + соус − терияки')).toBeTruthy()
    expect(screen.getByText('г')).toBeTruthy()            // соус в граммах
  })

  it('цена обрезков — константа учёта, цена соуса — из накладных', async () => {
    render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    expect(screen.getByPlaceholderText('8800')).toBeTruthy()
    expect(screen.getByPlaceholderText('4200')).toBeTruthy()
    expect(screen.getByText(/Азия Фуд/)).toBeTruthy()
  })

  it('показывает выход жарки и соус на килограмм', async () => {
    render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '2' } })
    fireEvent.change(screen.getByPlaceholderText('1,4'), { target: { value: '1.4' } })
    fireEvent.change(screen.getByPlaceholderText('200'), { target: { value: '200' } })
    await waitFor(() => expect(screen.getByText(/Выход жарки/)).toBeTruthy(), { timeout: 2000 })
    // 1,4 / (2 + 0,2) — соус в знаменателе, иначе горело бы 70 %
    expect(screen.getAllByText('63,6%').length).toBeGreaterThan(0)
    expect(screen.getByText('142,9 г')).toBeTruthy()
  })

  it('ужарка считается от входа целиком, а не от одних обрезков', async () => {
    const { container } = render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '2' } })
    fireEvent.change(screen.getByPlaceholderText('1,4'), { target: { value: '1.4' } })
    fireEvent.change(screen.getByPlaceholderText('200'), { target: { value: '200' } })
    await waitFor(() => expect(screen.getByText(/Выход жарки/)).toBeTruthy(), { timeout: 2000 })
    const shrink = Array.from(container.querySelectorAll('.sal-tile'))
      .map((el) => el.textContent || '').find((t) => t.includes('Ужарка')) || ''
    expect(shrink).toMatch(/0,8/)          // 2,2 − 1,4, а не 2 − 1,4
    expect(shrink).toMatch(/2,2 − 1,4/)    // вход подписан суммой, а не обрезками
  })

  it('наценку на ужарку показывает отдельной строкой', async () => {
    /* Формула владельца приходует больше, чем списывает. Спрятать разницу —
       значит отдать в iiko таблицу, которая не сходится в столбик. */
    render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '2' } })
    await waitFor(() => expect(screen.getByText(/наценка на отход/)).toBeTruthy(), { timeout: 2000 })
    expect(screen.getByText(/наценка на отход 36,4%/)).toBeTruthy()
  })

  it('в запрос уезжают обрезки, терияки и граммы соуса', async () => {
    render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '2' } })
    fireEvent.change(screen.getByPlaceholderText('1,4'), { target: { value: '1.4' } })
    fireEvent.change(screen.getByPlaceholderText('200'), { target: { value: '200' } })
    await waitFor(() => {
      const calls = (fetch as unknown as { mock: { calls: [string, RequestInit?][] } }).mock.calls
      const post = calls.find(([u, o]) => o?.method === 'POST' && String(u).includes('/api/teriyaki/'))
      expect(post).toBeTruthy()
      const body = JSON.parse(String(post![1]!.body))
      expect(body).toMatchObject({ weight_fry: 2, weight_teriyaki: 1.4, weight_sauce: 200 })
    }, { timeout: 2000 })
  })

  it('таблица говорит, что списать и что оприходовать', async () => {
    render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '2' } })
    await waitFor(() => expect(screen.getByText(/П\/Ф ОБРЕЗКИ ЛОСОСЬ/)).toBeTruthy(), { timeout: 2000 })
    expect(screen.getAllByText(/списать/).length).toBe(2)      // обрезки и соус
    expect(screen.getByText(/оприходовать/)).toBeTruthy()      // П/Ф терияки
  })
})

describe('жарка без цены', () => {
  /* Требование со скрина: ужарка «неправильно читается». Она разница двух
     весов, и когда себестоимости обрезков в iiko нет, экран раньше отдавал
     ошибку целиком — вместе с ужаркой, к которой цена отношения не имеет. */
  const NO_PRICE = {
    ok: true, money: false, need: 'trim_price',
    need_text: 'Цена обрезков не задана — впиши, почём их считать.',
    trim: { weight: 2, price_per_kg: null, sum: null },
    sauce: { grams: 0, price_per_kg: null, sum: null },
    teriyaki: { weight: 1.4, price_per_kg: null, sum: null, from_trim: 2, from_input: 2, shrink: 0.6 },
    input_weight: 2, yield_pct: 70, shrink_pct: 30, sauce_g_per_kg: 0, check: null,
  }

  it('ужарка и выход считаются, даже когда цены обрезков нет', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, opts?: RequestInit) => ({
      ok: true,
      json: async () => (String(url).includes('/period') ? { acts: [], total: { acts: 0 } }
        : opts?.method === 'POST' ? NO_PRICE
        : { ...CTX, trim_price: 0 }),
    })) as unknown as typeof fetch)
    const { container } = render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '2' } })
    fireEvent.change(screen.getByPlaceholderText('1,4'), { target: { value: '1.4' } })
    await waitFor(() => expect(screen.getByText(/уже посчитаны/)).toBeTruthy(), { timeout: 2000 })
    const shrink = Array.from(container.querySelectorAll('.sal-tile'))
      .map((el) => el.textContent || '').find((t) => t.includes('Ужарка')) || ''
    expect(shrink).toMatch(/0,6/)        // 2 − 1,4 — кг ужарки
    expect(shrink).toMatch(/70%/)        // выход жарки
    expect(screen.queryByText(/оприходовать/)).toBeNull()            // денег нет
  })
})

describe('порядок на экране', () => {
  it('соус стоит перед терияки: сперва что кладут, потом что снимают', async () => {
    const { container } = render(<TeriyakiAct token="1.sig" switcher={null} />)
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    const labels = Array.from(container.querySelectorAll('.sal-tile'))
      .map((el) => el.textContent || '')
    const sauce = labels.findIndex((t) => t.includes('Соус терияки'))
    const teriyaki = labels.findIndex((t) => /Терияки · со сковороды/.test(t))
    expect(sauce).toBeGreaterThan(-1)
    expect(teriyaki).toBeGreaterThan(sauce)
  })
})

describe('переключатель', () => {
  it('терияки открывается третьей вкладкой, а не плитками в разборе', async () => {
    render(<SalmonAct token="1.sig" />)
    await waitFor(() => expect(screen.getByText(/Стейк в разбор/)).toBeTruthy())
    expect(screen.queryByText(/^В жарку/)).toBeNull()

    fireEvent.click(screen.getByText('Терияки'))
    await waitFor(() => expect(screen.getByText(/Обрезки в жарку/)).toBeTruthy())
    // разбора на этом экране больше нет
    expect(screen.queryByText(/Стейк в разбор/)).toBeNull()
    expect((fetch as unknown as { mock: { calls: [string][] } }).mock.calls
      .some(([u]) => String(u).startsWith('/api/teriyaki/'))).toBe(true)
  })
})
