import { render, screen } from '@testing-library/react'
import ChecklistUpload from './ChecklistUpload'
import { issueText, nearestSlot, sentIn, zoneVerdict, zoneCriteria,
         type Done, type Review } from './checklist'

/* Чек-лист глазами управляющего: что сдано и что с этим не так. */

const done: Done[] = [{
  branch: 'Астана, ул. Г.Алиева 10 (EXPO)', slot: '10:45',
  zone: 'зал', zone_key: 'hall', photos: 4, at: '10:47',
}]

function mock(body: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body),
    { status: 200, headers: { 'Content-Type': 'application/json' } })))
}

beforeEach(() => {
  // Время прибито: экран сам выбирает ближайший прошедший слот, и без этого
  // тест был бы зелёным или красным по часам запуска.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 7, 26, 10, 50))
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('замечание разбора', () => {
  /* Модель отдаёт объекты {zone, what}. Их склеивали как строки — на экран
     падало «[object Object]», то есть ровно то, ради чего разбор и затевался,
     управляющий не узнавал. */
  it('объект превращается в читаемую строку, а не в [object Object]', () => {
    expect(issueText({ zone: 'санузел', what: 'нет мыла' })).toBe('санузел: нет мыла')
  })
  it('строку оставляет как есть — старый формат ответа', () => {
    expect(issueText('грязный пол')).toBe('грязный пол')
  })
  it('половинчатое замечание не теряется', () => {
    expect(issueText({ what: 'мусор у входа' })).toBe('мусор у входа')
  })
})

describe('сверка сдачи', () => {
  it('сходится по ключу зоны, хотя подписи в разном регистре', () => {
    expect(sentIn(done, '10:45', 'hall')?.at).toBe('10:47')
  })
  it('несданную зону не выдумывает', () => {
    expect(sentIn(done, '10:45', 'wc')).toBeUndefined()
  })
  it('ближайший слот — прошедший, а не будущий', () => {
    expect(nearestSlot(new Date(2026, 7, 26, 16, 10))).toBe('15:00')
  })
})

describe('экран сдачи', () => {
  it('показывает, какие зоны сданы: галочка на зоне и счётчик на слоте', async () => {
    mock({ done })
    render(<ChecklistUpload token="btok.sig" />)
    // на кнопке зоны — время сдачи, на кнопке слота — сколько зон из трёх
    expect(await screen.findByText('10:47')).toBeTruthy()
    expect(screen.getByText('1/3')).toBeTruthy()
    expect(screen.getAllByText('0/3')).toHaveLength(3)   // остальные три слота
  })

  it('показывает замечания разбора за сегодня', async () => {
    mock({ done, reviews: [{ slot: '10:45', ok: false, issues: [{ zone: 'санузел', what: 'нет мыла' }] }] })
    render(<ChecklistUpload token="btok.sig" />)
    await screen.findByText('Замечания за сегодня')
    expect(screen.getByText('санузел: нет мыла')).toBeTruthy()
  })

  it('чистый разбор в замечания не попадает', async () => {
    mock({ done, reviews: [{ slot: '10:45', ok: true, issues: [] }] })
    render(<ChecklistUpload token="btok.sig" />)
    await screen.findByText('Сдать чек-лист')
    expect(screen.queryByText('Замечания за сегодня')).toBeNull()
  })
})

describe('сдано ещё не значит чисто', () => {
  /* Зелёный до сих пор значил «кадры присланы»: филиал, снявший загаженный
     санузел, получал ровно тот же зелёный, что и вымытый. */
  const dirty: Review[] = [{
    slot: '10:45', ok: false, issues: [],
    criteria: [{ zone: 'зал', item: 'Урна не переполнена', ok: false, what: 'урна с горкой' },
               { zone: 'зал', item: 'Пол под столами и у входа', ok: true }],
  }]

  it('зона с проваленным критерием считается грязной', () => {
    expect(zoneVerdict(dirty, '10:45', 'hall')).toBe('dirty')
  })
  it('зона без замечаний — чистая', () => {
    expect(zoneVerdict([{ slot: '10:45', ok: true, issues: [], criteria: [
      { zone: 'кухня', item: 'Раковина без посуды', ok: true }] }], '10:45', 'kitchen')).toBe('clean')
  })
  it('без разбора вердикта нет — молчание не приговор', () => {
    expect(zoneVerdict([], '10:45', 'hall')).toBeNull()
  })

  it('размечает критерии: выполнен, не выполнен, не разбирали', () => {
    const out = zoneCriteria(dirty, '10:45', 'hall',
      ['Пол под столами и у входа', 'Урна не переполнена', 'Витрина и стекло без разводов'])
    expect(out.map((c) => c.ok)).toEqual([true, false, null])
    expect(out[1].what).toBe('урна с горкой')
  })

  it('на экране грязная зона отмечена крестиком, а не галочкой', async () => {
    mock({ done, reviews: dirty, criteria: { hall: { label: 'Зал',
      items: ['Пол под столами и у входа', 'Урна не переполнена'] } } })
    render(<ChecklistUpload token="btok.sig" />)
    // Ждём текст, который рисуется ИЗ ЗАГРУЖЕННОГО разбора. «Сдать чек-лист»
    // появляется сразу, ещё до ответа fetch, и синхронная проверка следом
    // выигрывала гонку только на незанятой машине: под нагрузкой (например,
    // когда рядом идёт сборка) тест падал, хотя код исправен.
    expect(await screen.findByText(/но разбор нашёл замечания/)).toBeTruthy()
    expect(screen.getByText('есть замечания')).toBeTruthy()
    // критерий с провалом показан текстом замечания
    expect(screen.getByText(/урна с горкой/)).toBeTruthy()
  })
})

describe('очередь кадров', () => {
  /* Раньше отправка начиналась на onChange: снял один кадр — он и улетел,
     второй ракурс той же зоны добавить было уже некуда. */
  it('кнопка отправки не активна, пока не снят ни один кадр', async () => {
    mock({ done: [] })
    render(<ChecklistUpload token="btok.sig" />)
    const send = await screen.findByRole('button', { name: /Сдать 10:45/ })
    expect((send as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Снять кадр' })).toBeTruthy()
  })
})
