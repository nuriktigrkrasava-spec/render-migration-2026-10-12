import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Journals from './Journals'

/* Три тетради, галочка ставится сразу и неделя видна одним взглядом — то, что
   просил владелец. Тест сторожит состав журналов и то, что отметка не ждёт сети. */

const DAYS = ['2026-08-28', '2026-08-29', '2026-08-30', '2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03']
const DATA = {
  role: 'manager',
  slot: '11:00',
  today: '2026-09-03',
  days: DAYS,
  journals: [
    { key: 'temp', title: 'Температурный', hint: 'показания камер за сутки' },
    { key: 'brak', title: 'Бракеражный', hint: 'оценка готовых блюд' },
    { key: 'clean', title: 'Уборки', hint: 'кто и когда убирал' },
  ],
  branches: [{
    organization_id: 'a', name: 'Астана',
    today: { temp: true, brak: false, clean: false },
    days: DAYS.map((d) => ({ day: d, done: d === '2026-09-03' ? ['temp'] : ['temp', 'brak', 'clean'] })),
  }],
}

let posted: unknown[] = []

beforeEach(() => {
  posted = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { posted.push(JSON.parse(String(init.body))); return { ok: true, json: async () => ({ ok: true }) } }
    return { ok: true, json: async () => DATA }
  }) as unknown as typeof fetch)
})

describe('журналы филиала', () => {
  it('показывает три журнала и время напоминания', async () => {
    render(<Journals token="1.sig" />)
    // каждое название встречается дважды: плитка с галочкой и строка истории
    await waitFor(() => expect(screen.getAllByText('Температурный')).toHaveLength(2))
    expect(screen.getAllByText('Бракеражный')).toHaveLength(2)
    expect(screen.getAllByText('Уборки')).toHaveLength(2)
    expect(screen.getByText(/напоминание в 11:00/)).toBeTruthy()
  })

  it('заполненный сегодня подписан, остальные — подсказкой', async () => {
    render(<Journals token="1.sig" />)
    await waitFor(() => expect(screen.getByText('заполнен сегодня')).toBeTruthy())
    expect(screen.getByText('оценка готовых блюд')).toBeTruthy()
  })

  it('галочка встаёт сразу, не дожидаясь ответа сервера', async () => {
    render(<Journals token="1.sig" />)
    await waitFor(() => expect(screen.getByText('оценка готовых блюд')).toBeTruthy())
    const tile = screen.getAllByRole('button').find((b) => b.textContent?.includes('Бракеражный'))!
    fireEvent.click(tile)   // через fireEvent: React 19 иначе не успевает применить состояние
    // подпись меняется в том же кадре: сеть ещё не ответила
    expect(screen.getAllByText('заполнен сегодня').length).toBe(2)
    await waitFor(() => expect(posted).toEqual([{ org: 'a', journal: 'brak', on: true }]))
  })
})
