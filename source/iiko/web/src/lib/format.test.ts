import { tenge, tengeShort, num, pct } from './format'

// format.ts расставляет между разрядами и перед "тыс"/"млн"/"тг" неразрывный
// пробел U+00A0, а не обычный пробел U+0020 (чтобы не переносилось по строке).
// Intl.NumberFormat('ru-RU') в этой версии Node тоже отдаёт U+00A0 как
// разделитель разрядов. Поэтому ожидаемые строки собраны через NBSP явно.
const NBSP = ' '

describe('tenge', () => {
  it('форматирует с неразрывными разрядами и знаком тенге', () => {
    expect(tenge(944617)).toBe(`944${NBSP}617${NBSP}₸`)
  })
  it('отдаёт прочерк на пустом значении', () => {
    expect(tenge(null)).toBe('—')
    expect(tenge(undefined)).toBe('—')
    expect(tenge(NaN)).toBe('—')
  })
  it('округляет до целых тенге', () => {
    expect(tenge(1234.6)).toBe(`1${NBSP}235${NBSP}₸`)
  })
})

describe('tengeShort', () => {
  it('сокращает миллионы с запятой как разделителем', () => {
    expect(tengeShort(2906787)).toBe(`2,9${NBSP}млн${NBSP}₸`)
  })
  it('сокращает тысячи', () => {
    expect(tengeShort(52330)).toBe(`52${NBSP}тыс${NBSP}₸`)
  })
  it('не сокращает мелкие суммы', () => {
    expect(tengeShort(940)).toBe(`940${NBSP}₸`)
  })
  it('работает на отрицательных', () => {
    expect(tengeShort(-2906787)).toBe(`-2,9${NBSP}млн${NBSP}₸`)
  })
})

describe('pct', () => {
  it('ставит плюс на росте', () => {
    expect(pct(21.4)).toBe('+21,4%')
  })
  it('не ставит плюс когда попросили без знака', () => {
    expect(pct(21.4, false)).toBe('21,4%')
  })
  it('минус остаётся всегда', () => {
    expect(pct(-34.3)).toBe('-34,3%')
  })
})

describe('num', () => {
  it('разделяет разряды', () => {
    expect(num(14759)).toBe(`14${NBSP}759`)
  })
})
