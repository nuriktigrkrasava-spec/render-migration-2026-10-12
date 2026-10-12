import { render, screen } from '@testing-library/react'
import { NoticeToast, oldestUnread, freshIds, type Notice } from './Notices'

const n = (id: number, is_read?: number): Notice => ({ id, text: `с${id}`, is_read })

describe('очередь сообщений владельца', () => {
  it('показывает самое старое непрочитанное (сервер отдаёт от новых к старым)', () => {
    expect(oldestUnread([n(5), n(4), n(3)])?.id).toBe(3)
  })
  it('прочитанные пропускает', () => {
    expect(oldestUnread([n(5), n(4, 1), n(3, 1)])?.id).toBe(5)
  })
  it('когда всё прочитано — плашки нет', () => {
    expect(oldestUnread([n(5, 1), n(4, 1)])).toBeUndefined()
  })

  // Звонок вешаем на ЛЮБОЕ новое сообщение, а не на самое старое: то уже
  // прозвучало при открытии страницы и так и оставалось первым — новое его не
  // меняло, и звука не было.
  it('звонит на новое сообщение, даже если старое непрочитанное уже звонило', () => {
    expect(freshIds([n(7), n(3)], [3])).toEqual([7])
  })
  it('второй раз по тому же сообщению не звонит', () => {
    expect(freshIds([n(3)], [3])).toEqual([])
  })
})

describe('плашка уведомления', () => {
  /* Регрессия по вёрстке: раньше плашка центровалась через
     `left: 50% + transform: translateX(-50%)`, а motion пишет в style.transform
     свои translateY/scale и статический transform затирал. Плашка вставала
     левым краем на середину экрана и на телефоне почти целиком уезжала за
     правый край — сообщение владельца было не видно. */
  it('центруется без transform, который затрёт motion', () => {
    const { container } = render(<NoticeToast notice={n(1)} onRead={() => {}} />)
    const el = container.firstElementChild as HTMLElement
    expect(el.style.left).not.toBe('50%')
    expect(el.style.left).toBe('0px')
    expect(el.style.right).toBe('0px')
    // motion действительно занял transform под свою анимацию
    expect(el.style.transform).toContain('translateY')
    expect(el.style.transform).not.toContain('translateX')
  })

  it('текст и кнопка «Прочитал» на месте', () => {
    render(<NoticeToast notice={{ id: 1, text: 'сдать инвентаризацию' }} onRead={() => {}} />)
    expect(screen.getByText('сдать инвентаризацию')).toBeTruthy()
    expect(screen.getByText('Прочитал')).toBeTruthy()
  })
})
