import { render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import Mountain, { popoverBox, ScoreRing } from './Mountain'

/* Регрессия по ролям на «Горе».

   Сайт управляющего однажды показал доску ВЛАДЕЛЬЦА: форму постановки задач,
   рассылку «отправить всем» и прогресс всех девяти филиалов — а сдача чек-листа
   при этом пропала, потому что она рисуется только не-владельцу. Причина одна:
   доска приехала с role="owner". Здесь фиксируем обе стороны — что управляющий
   видит своё, и что протухшая ссылка филиала НЕ открывает кабинет владельца. */

const level = { step: 0, steps: 40, height_pct: 0, to_next: 25, summit: 1000 }
const row = (id: string, name: string) => ({
  organization_id: id, name, tasks_open: [], tasks_done: 0, task_points: 0,
  checklist: { done: 0, missed: 0, points: 0 }, points: 0, level, score: null,
})
const board = (role: string, names: [string, string][]) => ({
  role, month: '2026-08',
  scale: { tasks: {}, step_points: 25, steps: 40 },
  ranking: names.map(([id, n]) => ({ organization_id: id, name: n, points: 0, level, place: 1, tasks_done: 0 })),
  branches: names.map(([id, n]) => row(id, n)),
})

const OWNER_BRANCHES: [string, string][] = [
  ['a', 'Астана, ул. Бейбітшілік 71'], ['b', 'Астана, ул. Бокейхана 25а'],
  ['c', 'Астана, ул. Г.Алиева 10 (EXPO)'],
]

function mockFetch(routes: Record<string, () => Response | Promise<Response>>) {
  const json = (d: unknown, status = 200) =>
    new Response(JSON.stringify(d), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    for (const [frag, make] of Object.entries(routes)) if (url.includes(frag)) return make()
    return json({})
  }))
  return json
}

afterEach(() => { vi.unstubAllGlobals(); history.replaceState({}, '', '/') })

describe('«Гора» глазами управляющего', () => {
  it('не даёт ставить задачи, рассылать всем и не показывает чужой прогресс', async () => {
    history.replaceState({}, '', '/?m=900010.sig')
    const json = mockFetch({
      '/api/mountain/data/': () => new Response(
        JSON.stringify(board('manager', [['c', 'Астана, ул. Г.Алиева 10 (EXPO)']])),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    })
    void json
    render(<Mountain />)

    await screen.findByText('Мои задачи')
    expect(screen.queryByText('Поставить задачу')).toBeNull()
    expect(screen.queryByText('Отправить всем')).toBeNull()
    // прогресс — только по своему филиалу
    expect(screen.getAllByText(/Ступень/)).toHaveLength(1)
  })

  it('показывает сдачу чек-листа — ради неё управляющий и заходит', async () => {
    history.replaceState({}, '', '/?m=900010.sig')
    mockFetch({
      '/api/mountain/data/': () => new Response(
        JSON.stringify(board('manager', [['c', 'Астана, ул. Г.Алиева 10 (EXPO)']])),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    })
    render(<Mountain />)
    await screen.findByText('Сдать чек-лист')
  })

  it('протухшая ссылка филиала не открывает кабинет владельца', async () => {
    history.replaceState({}, '', '/?m=900010.stale')
    mockFetch({
      '/api/mountain/data/': () => new Response('{"error":"bad token"}', { status: 404 }),
      // сервер в dev-режиме отдаёт ссылку владельца любому, кто спросил без токена
      '/api/mountain/links': () => new Response(
        JSON.stringify({ owner: { tid: 1, link: '/m/8727082281.own' } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    })
    render(<Mountain />)

    await waitFor(() => expect(screen.getByText(/Ссылка/)).toBeTruthy())
    expect(screen.queryByText('Поставить задачу')).toBeNull()
    expect(screen.queryByText('Отправить всем')).toBeNull()
  })
})

describe('«Гора» глазами владельца', () => {
  it('видит все филиалы и картину по чек-листам, а не только свою сдачу', async () => {
    mockFetch({
      '/api/mountain/data/': () => new Response(
        JSON.stringify(board('owner', OWNER_BRANCHES)),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
      '/api/mountain/links': () => new Response(
        JSON.stringify({ owner: { tid: 1, link: '/m/8727082281.own' } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    })
    render(<Mountain />)

    await screen.findByText('Задачи филиалам')
    expect(screen.getAllByText(/Ступень/)).toHaveLength(3)
    // сдавать чек-листы владельцу нечего — но видеть, кто сдал, он обязан
    expect(screen.queryByText('Сдать чек-лист')).toBeNull()
    await screen.findByText('Чек-листы филиалов')
  })
})

describe('филиал ниже нуля', () => {
  /* Ступень у всех отстающих нулевая: филиал со штрафом −28 выглядел ровно как
     тот, кто просто ещё не начал, — «0 из 40» и там, и там. Минус должен быть
     виден, иначе провал читается как старт. */
  it('показывает накопленный минус и сколько до нуля', async () => {
    history.replaceState({}, '', '/?m=b-org.sig')
    mockFetch({
      '/api/mountain/data/': () => new Response(JSON.stringify({
        ...board('manager', [['c', 'Астана, ул. Г.Алиева 10 (EXPO)']]),
        branches: [{ ...row('c', 'Астана, ул. Г.Алиева 10 (EXPO)'), points: -28,
                     checklist: { done: 1, missed: -29, points: -28 } }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    })
    render(<Mountain />)
    await screen.findByText('Мои задачи')
    expect(screen.getByText('ниже нуля на 28 — сначала выйти в ноль')).toBeTruthy()
  })

  it('филиал в нуле такой подписи не получает', async () => {
    history.replaceState({}, '', '/?m=b-org.sig')
    mockFetch({
      '/api/mountain/data/': () => new Response(
        JSON.stringify(board('manager', [['c', 'Астана, ул. Г.Алиева 10 (EXPO)']])),
        { status: 200, headers: { 'Content-Type': 'application/json' } }),
    })
    render(<Mountain />)
    await screen.findByText('Мои задачи')
    expect(screen.queryByText(/ниже нуля/)).toBeNull()
  })
})


/* Разбор оценки («из чего сложилась») висел внутри строки и не помещался:
   кольцо стоит у правого края карточки, а критериев восемь. */
describe('разбор оценки помещается на экран', () => {
  const rect = (x: number, y: number): DOMRect =>
    ({ left: x, right: x + 36, top: y, bottom: y + 36, width: 36, height: 36,
       x, y, toJSON: () => ({}) }) as DOMRect
  const host = (x: number, y: number) => ({ getBoundingClientRect: () => rect(x, y) })
  const win = { innerWidth: 390, innerHeight: 800 } as Window & typeof globalThis

  it('не уезжает за правый край окна', () => {
    const b = popoverBox(host(360, 300), win)   // кольцо у самого края
    const right = Number(b.left) + Number(b.width)
    expect(Number(b.left)).toBeGreaterThanOrEqual(8)
    expect(right).toBeLessThanOrEqual(win.innerWidth - 8)
  })

  it('не уезжает за левый край', () => {
    const b = popoverBox(host(2, 300), win)
    expect(Number(b.left)).toBeGreaterThanOrEqual(8)
  })

  it('раскрывается туда, где больше места', () => {
    expect(popoverBox(host(100, 40), win).top).toBeDefined()      // сверху тесно — вниз
    expect(popoverBox(host(100, 40), win).bottom).toBeUndefined()
    expect(popoverBox(host(100, 760), win).bottom).toBeDefined()   // снизу тесно — вверх
    expect(popoverBox(host(100, 760), win).top).toBeUndefined()
  })

  it('высота не больше свободного места — дальше прокрутка внутри', () => {
    const b = popoverBox(host(100, 700), win)
    expect(Number(b.maxHeight)).toBeLessThanOrEqual(win.innerHeight)
    expect(Number(b.maxHeight)).toBeGreaterThan(0)
  })

  it('без окна и без кольца не падает', () => {
    expect(popoverBox(null, win).position).toBe('absolute')
    expect(popoverBox(host(10, 10), null as unknown as Window & typeof globalThis).position).toBe('absolute')
  })
})


/* Карточки «Горы» — motion.div с трансформом, а трансформированный предок
   превращает position:fixed в position:absolute относительно себя. Поэтому
   разбор живёт в портале на body: иначе он снова уедет за край карточки. */
describe('разбор оценки не заперт в карточке', () => {
  const criteria = [
    { label: 'Динамика выручки', value: 80, weight: 20, counted: true },
    { label: 'Рост заказов', value: 60, weight: 20, counted: true },
    { label: 'Опоздавшие заказы', value: 40, weight: 15, counted: true },
    { label: 'Опоздания сотрудников', value: 75, weight: 12, counted: true },
    { label: 'Чек-листы', value: 90, weight: 10, counted: true },
    { label: 'Рейтинг', value: null, weight: 10, counted: false },
    { label: 'План недели', value: 55, weight: 8, counted: true },
    { label: 'Кассовые смены', value: 100, weight: 5, counted: true },
  ]

  it('открывается в body, а не внутри строки, и показывает все критерии', () => {
    const { container } = render(<div style={{ overflow: 'hidden' }}>
      <ScoreRing score={72} criteria={criteria} />
    </div>)
    fireEvent.click(screen.getByRole('button'))
    const pop = screen.getByText('из чего сложилась').parentElement!
    expect(container.contains(pop)).toBe(false)      // вынесен из обрезающего предка
    expect(document.body.contains(pop)).toBe(true)
    for (const c of criteria) expect(screen.getByText(new RegExp(c.label))).toBeTruthy()
    expect(getComputedStyle(pop).position).toBe('fixed')
    expect(getComputedStyle(pop).overflowY).toBe('auto')   // не влезло — прокрутка
  })

  it('закрывается по Escape', () => {
    render(<ScoreRing score={50} criteria={criteria} />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.queryByText('из чего сложилась')).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByText('из чего сложилась')).toBeNull()
  })
})
