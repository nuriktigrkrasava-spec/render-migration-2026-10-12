/* Недели ISO для вкладки «План». Нумерация та же, что в таблице владельца
   («Неделя 35»), поэтому считаем по ISO, а не по семидневкам от 1-го числа.
   Вынесено из экрана: арифметика недель на границе года ошибается тихо. */

/** Дата → 'YYYY-Www'. Год берётся у четверга недели — так устроен ISO,
    иначе 31 декабря уезжало бы в первую неделю прошлого года. */
export function isoWeekOf(d: Date): string {
  const x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  x.setUTCDate(x.getUTCDate() + 4 - (x.getUTCDay() || 7))
  const y0 = new Date(Date.UTC(x.getUTCFullYear(), 0, 1))
  const n = Math.ceil(((x.getTime() - y0.getTime()) / 86400000 + 1) / 7)
  return `${x.getUTCFullYear()}-W${String(n).padStart(2, '0')}`
}

/** Понедельник недели 'YYYY-Www' (UTC-полночь). */
export function mondayOfWeek(week: string): Date {
  const [y, w] = week.split('-W').map(Number)
  const jan4 = new Date(Date.UTC(y, 0, 4))
  const mon = new Date(jan4)
  mon.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() || 7) - 1) + (w - 1) * 7)
  return mon
}

/** Соседняя неделя. Через понедельник, поэтому переход года считается сам. */
export function shiftWeek(week: string, delta: number): string {
  const mon = mondayOfWeek(week)
  mon.setUTCDate(mon.getUTCDate() + delta * 7)
  return isoWeekOf(new Date(mon.getUTCFullYear(), mon.getUTCMonth(), mon.getUTCDate()))
}


/** Процент выполнения плана. Округление НЕ имеет права объявить план
    выполненным: 996 заказов из 1000 — это 99,6%, и «100%» зелёным на собрании
    читается как «сделали», хотя не сделали. Перевыполнение при этом остаётся
    перевыполнением: 100,4% так и округляется до 100. */
export function planPct(fact: number, plan: number): number {
  if (!plan) return 0
  const r = Math.round((fact / plan) * 100)
  return r >= 100 && fact < plan ? 99 : r
}

/* ---- ФОТ недели. Один контракт на вкладку «Планы» и на отчёт «Прогресс»:
   разойдись они — на собрании висели бы две разные зарплаты. ---- */

export type FotGap = { role: string; hours: number }

/** priced/total — ПОКРЫТИЕ: часы, посчитанные по ставке должности из iiko, из
    всех отработанных (unit 'hour'); либо смены с оплатой из графика ('shift').
    unit=null — данных нет вовсе, и это не то же самое, что ноль. */
export type Fot = {
  amount: number; priced: number; total: number
  unit: 'hour' | 'shift' | 'mixed' | null; complete: boolean; gaps?: FotGap[]
}

/** Строка «кого не хватает». Показываем три верхних должности, но остаток
    ДОГОВАРИВАЕМ: под суммой стоит «столько-то из стольких часов со ставкой», и
    если список объясняет только часть разницы, числа на экране не сходятся. */
export function fotGapsLine(f?: Fot): string {
  const gaps = f?.gaps || []
  if (!f || !gaps.length) return ''
  const n = (v: number) => v.toLocaleString('ru-RU')
  const top = gaps.slice(0, 3)
  const rest = gaps.slice(3)
  const restHours = rest.reduce((s, g) => s + g.hours, 0)
  const head = top.map((g) => `${g.role} (${n(g.hours)} ч)`).join(', ')
  return rest.length
    ? `нет ставки в iiko: ${head} и ещё ${n(restHours)} ч по ${rest.length} должн.`
    : `нет ставки в iiko: ${head}`
}

/** Подпись под суммой ФОТ. Главное — не выдать неполную сумму за полную: часы
    без ставки должности в iiko в неё не попали, и владелец должен это видеть. */
export function fotCoverage(f?: Fot): string {
  const n = (v: number) => v.toLocaleString('ru-RU')
  if (!f || f.unit === null) return 'нет данных о явках'
  if (f.unit === 'shift') return `${n(f.priced)} из ${n(f.total)} смен с оплатой`
  if (f.complete) return `${n(f.total)} ч по ставкам iiko`
  const share = f.total ? Math.round((100 * f.priced) / f.total) : 0
  return `${n(f.priced)} из ${n(f.total)} ч со ставкой · ${share}%`
}
