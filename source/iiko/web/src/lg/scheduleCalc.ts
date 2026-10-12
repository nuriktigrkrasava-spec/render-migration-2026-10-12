/* Логика расписания: план у нас — чистые функции, тестируются отдельно от UI.
   Время — строки 'HH:MM'.

   Сверка «план vs факт» жила здесь, пока фактом были явки iiko. Факт теперь
   наш (отметка смены, POST /api/attendance), у него другая форма — открытая
   смена без времени ухода, — и сверять его будет приложение смен. Старая
   сверка убрана намеренно: тип Fact в форме iiko вводил бы в заблуждение. */

export type Shift = { employeeId: string; date: string; start: string; end: string;
                     pay?: number | null }   // оплата за смену; null — не проставлена

const toMin = (t: string): number => {
  const [h, m] = t.split(':').map(Number)
  return h * 60 + m
}

/** Длина смены в минутах; конец ≤ начала трактуется как переход через полночь. */
export function shiftMinutes(start: string, end: string): number {
  const s = toMin(start)
  let e = toMin(end)
  if (e <= s) e += 24 * 60
  return e - s
}
