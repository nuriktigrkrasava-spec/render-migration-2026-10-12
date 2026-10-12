import { render, screen } from '@testing-library/react'
import ChecklistBoard from './ChecklistBoard'
import type { Done } from './checklist'

/* Владелец должен видеть не «сколько зон сдано», а КАКУЮ именно: одно дело не
   снят зал, другое — санузел. Клетка — три значка З · К · С. */

const done: Done[] = [
  { branch: 'Астана, ул. Г.Алиева 10 (EXPO)', slot: '10:45', zone: 'зал', zone_key: 'hall', photos: 4, at: '10:47' },
  { branch: 'Астана, ул. Г.Алиева 10 (EXPO)', slot: '10:45', zone: 'кухня', zone_key: 'kitchen', photos: 3, at: '10:52' },
]

function mock(body: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body),
    { status: 200, headers: { 'Content-Type': 'application/json' } })))
}

beforeEach(() => {
  // 16:10 — два первых слота прошли, два ещё нет: несданное в прошедших слотах
  // должно читаться как провинность, в будущих — нет.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 7, 26, 16, 10))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('показывает по каждой зоне: сдана, не сдана или слот ещё не наступил', async () => {
  mock({ done })
  render(<ChecklistBoard token="tok" branches={['Астана, ул. Г.Алиева 10 (EXPO)']} />)

  await screen.findByText('Чек-листы филиалов')
  // зал в 10:45 сдан, санузел в том же слоте — нет
  expect(await screen.findByTitle('Зал: сдано в 10:47')).toBeTruthy()
  expect(screen.getByTitle('Кухня: сдано в 10:52')).toBeTruthy()
  // санузел не сдан в двух ПРОШЕДШИХ слотах (10:45 и 15:00), зал — в одном
  expect(screen.getAllByTitle('Санузел: не сдано')).toHaveLength(2)
  expect(screen.getAllByTitle('Зал: не сдано')).toHaveLength(1)
  // 20:45 ещё не наступил — это не провинность
  expect(screen.getAllByTitle('Зал: слот ещё не наступил').length).toBe(2)
})

it('замечание разбора показывает текстом, а не [object Object]', async () => {
  mock({ done, reviews: [{ branch: 'Астана, ул. Г.Алиева 10 (EXPO)', slot: '10:45',
                           ok: false, issues: [{ zone: 'санузел', what: 'нет мыла' }] }] })
  render(<ChecklistBoard token="tok" branches={['Астана, ул. Г.Алиева 10 (EXPO)']} />)
  expect(await screen.findByText('санузел: нет мыла')).toBeTruthy()
})
