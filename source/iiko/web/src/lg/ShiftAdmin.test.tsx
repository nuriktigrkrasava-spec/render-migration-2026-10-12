import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StaffLinks } from './ShiftAdmin'

/* Перевыпуск QR и отвязка — единственные кнопки в панели, которые молча ломают
   человеку вход: код на стене умирает, сотрудник продолжает его сканировать и
   не понимает, почему не пускает. Поэтому оба действия просят второй клик, и
   тест сторожит именно это: одиночный промах ничего не отправляет. */

const JOIN = 'https://t.me/bot?start=join_old'
const NEW = 'https://t.me/bot?start=join_new'
const ACCESS = {
  join_url: JOIN,
  access: [{ employee_id: 'e1', name: 'Ким', bound: true, bound_at: null,
             url: 'https://t.me/bot?start=emp_x' }],
}

let posted: { url: string; body: unknown }[] = []

beforeEach(() => {
  posted = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posted.push({ url: String(url), body: JSON.parse(String(init.body)) })
      return { ok: true, json: async () => ({ ok: true, join_url: NEW }) }
    }
    return { ok: true, json: async () => ACCESS }
  }) as unknown as typeof fetch)
})

const draw = () => render(<StaffLinks org="org-1" employees={[{ id: 'e1', name: 'Ким' }]} onWrite={() => {}} />)

describe('доступ в мини-апп: QR филиала', () => {
  it('первый клик по «Новый QR» ничего не перевыпускает', async () => {
    draw()
    await waitFor(() => screen.getByText('Новый QR'))
    fireEvent.click(screen.getByText('Новый QR'))
    expect(posted).toHaveLength(0)
    expect(screen.getByText('Точно? Старый умрёт')).toBeTruthy()
  })

  it('второй клик печатает новый код', async () => {
    draw()
    await waitFor(() => screen.getByText('Новый QR'))
    fireEvent.click(screen.getByText('Новый QR'))
    fireEvent.click(screen.getByText('Точно? Старый умрёт'))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0].url).toBe('/api/staff-access/org-1/rotate')
  })

  it('отвязка тоже в два клика и уходит с id сотрудника', async () => {
    draw()
    await waitFor(() => screen.getByText('Отвязать'))
    fireEvent.click(screen.getByText('Отвязать'))
    expect(posted).toHaveLength(0)
    fireEvent.click(screen.getByText('Точно?'))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0].url).toBe('/api/staff-access/org-1/revoke')
    expect(posted[0].body).toEqual({ employee_id: 'e1' })
  })

  it('привязанному видна и ссылка, и отвязка — выдавать заново нечего', async () => {
    draw()
    await waitFor(() => screen.getByText('Ссылка'))
    expect(screen.getByText('Отвязать')).toBeTruthy()
    expect(screen.queryByText('Выдать')).toBeNull()
  })
})
