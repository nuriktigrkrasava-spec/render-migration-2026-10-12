import { gradeOf } from './grade'

describe('gradeOf', () => {
  it('отдаёт A на девяноста и выше', () => {
    expect(gradeOf(90)).toBe('A')
    expect(gradeOf(100)).toBe('A')
  })
  it('различает B+ и B', () => {
    expect(gradeOf(86)).toBe('B+')
    expect(gradeOf(75)).toBe('B')
  })
  it('различает C+ и C', () => {
    expect(gradeOf(72)).toBe('C+')
    expect(gradeOf(61)).toBe('C')
  })
  it('отдаёт D и E внизу шкалы', () => {
    expect(gradeOf(55)).toBe('D')
    expect(gradeOf(10)).toBe('E')
  })
  it('зажимает выход за границы шкалы', () => {
    expect(gradeOf(140)).toBe('A')
    expect(gradeOf(-20)).toBe('E')
  })
})
