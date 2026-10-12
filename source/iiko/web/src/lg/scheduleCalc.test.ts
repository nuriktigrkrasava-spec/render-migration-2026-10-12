import { shiftMinutes } from './scheduleCalc'

describe('shiftMinutes', () => {
  it('считает обычную смену', () => {
    expect(shiftMinutes('10:00', '18:00')).toBe(480)
  })
  it('смена через полночь не отрицательная', () => {
    expect(shiftMinutes('22:00', '02:00')).toBe(240)
  })
})
