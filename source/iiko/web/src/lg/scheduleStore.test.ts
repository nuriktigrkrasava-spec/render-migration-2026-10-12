import { getPlan, setShift, removeShift, shiftFor } from './scheduleStore'
import { setOrg } from '../lib/api'

// Тень графика лежит ПО ФИЛИАЛУ, поэтому филиал нужно выбрать: без него
// store молчит намеренно — смена без филиала непонятно чья.
beforeEach(() => {
  localStorage.clear()
  setOrg('org-a')
})

describe('scheduleStore', () => {
  it('пустой план в начале', () => {
    expect(getPlan()).toEqual([])
  })
  it('ставит смену и находит её по сотруднику и дню', () => {
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00' })
    expect(shiftFor('e1', '2026-07-20')).toEqual({
      employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00',
    })
  })
  it('повтор для того же дня перезаписывает, а не дублирует', () => {
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00' })
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '11:00', end: '19:00' })
    expect(getPlan()).toHaveLength(1)
    expect(shiftFor('e1', '2026-07-20')?.start).toBe('11:00')
  })
  it('удаляет смену', () => {
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00' })
    removeShift('e1', '2026-07-20')
    expect(shiftFor('e1', '2026-07-20')).toBeNull()
  })

  it('оплата хранится вместе со сменой', () => {
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00', pay: 12000 })
    expect(shiftFor('e1', '2026-07-20')?.pay).toBe(12000)
  })
  // Пустое поле в редакторе значит «не проставили»: если бы стёртая сумма
  // оставалась прежней, недельный фонд врал бы в большую сторону.
  it('стёртая оплата не остаётся прошлой', () => {
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00', pay: 12000 })
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00', pay: null })
    expect(shiftFor('e1', '2026-07-20')?.pay).toBeNull()
  })

  // Ради этого и заводили ключ с org: раньше тень была одна на всё приложение,
  // владелец переключал филиал — и в сетке оставались чужие смены, а первая же
  // правка уходила на сервер НЕ того филиала.
  it('смены не перетекают между филиалами', () => {
    setShift({ employeeId: 'e1', date: '2026-07-20', start: '10:00', end: '18:00' })
    setOrg('org-b')
    expect(getPlan()).toEqual([])
    expect(shiftFor('e1', '2026-07-20')).toBeNull()

    setShift({ employeeId: 'e1', date: '2026-07-20', start: '14:00', end: '22:00' })
    expect(shiftFor('e1', '2026-07-20')?.start).toBe('14:00')

    setOrg('org-a')
    expect(shiftFor('e1', '2026-07-20')?.start).toBe('10:00')
  })
})
