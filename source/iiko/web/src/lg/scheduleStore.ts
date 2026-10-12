import type { Shift } from './scheduleCalc'
import { getOrg } from '../lib/api'

/* План (расписание) филиала. Раньше жил в localStorage — то есть у каждого
   свой: управляющий ставил график в своём браузере, владелец его не видел, и
   опоздания по нему посчитать было нельзя. Теперь источник — сервер
   (`/api/schedule/{org}`), а здесь лежит рабочая копия загруженной недели.

   Интерфейс остался синхронным намеренно: экран рисует сетку по дням и не
   должен ждать сеть на каждой ячейке. Запись идёт сразу в копию и следом
   уходит на сервер; localStorage остался тенью на случай офлайна.
   Одна плановая смена на (сотрудник, день). */

export type PlanShift = Shift

/* Тень — ПО ФИЛИАЛУ. Раньше ключ был один на всё приложение: владелец
   переключал филиал, а в сетке оставались смены предыдущего — и первая же
   правка уходила на сервер уже НЕ того филиала. Ключ с org это исключает.
   Старый общий ключ подчищаем: правда теперь на сервере, а смешанные смены
   из него пришлось бы разбирать вручную. */
const LEGACY_KEY = 'iiko.schedule.v1'
const keyFor = (org: string) => `${LEGACY_KEY}:${org}`
const idOf = (employeeId: string, date: string) => `${employeeId}|${date}`

function read(org = getOrg()): Record<string, PlanShift> {
  if (!org) return {}
  try {
    localStorage.removeItem(LEGACY_KEY)
    return JSON.parse(localStorage.getItem(keyFor(org)) || '{}') as Record<string, PlanShift>
  } catch {
    return {}
  }
}
function write(s: Record<string, PlanShift>, org = getOrg()) {
  if (!org) return
  try {
    localStorage.setItem(keyFor(org), JSON.stringify(s))
  } catch { /* приватный режим — молча */ }
}

/** Подтягивает график филиала за период с сервера в рабочую копию.
    Экран зовёт её при смене недели; ошибка сети — не беда, остаётся тень. */
export async function syncPlan(from: string, to: string): Promise<void> {
  const org = getOrg()
  if (!org) return
  try {
    const r = await fetch(`/api/schedule/${org}?from=${from}&to=${to}`, {
      headers: { Accept: 'application/json' },
    })
    if (!r.ok) return
    const d = (await r.json()) as { shifts?: PlanShift[] }
    // Пока ходили в сеть, филиал могли переключить — ответ прошлого филиала
    // тогда лёг бы в тень нового. Роняем его молча: за новый уже идёт свой запрос.
    if (getOrg() !== org) return
    const all = { ...read(org) }
    // Сервер — источник правды по загруженному периоду: снимаем локальные
    // смены этого диапазона, иначе удалённая кем-то смена «воскресала» бы.
    for (const k of Object.keys(all)) {
      const day = k.split('|')[1]
      if (day >= from && day <= to) delete all[k]
    }
    for (const s of d.shifts || []) all[idOf(s.employeeId, s.date)] = s
    write(all, org)
  } catch { /* офлайн — работаем по тени */ }
}

function push(body: Record<string, unknown>) {
  const org = getOrg()
  if (!org) return
  fetch(`/api/schedule/${org}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => { /* запись догонит при следующем сохранении */ })
}

export function getPlan(): PlanShift[] {
  return Object.values(read())
}

export function shiftFor(employeeId: string, date: string): PlanShift | null {
  return read()[idOf(employeeId, date)] ?? null
}

export function setShift(s: PlanShift) {
  const all = { ...read() }
  all[idOf(s.employeeId, s.date)] = s
  write(all)
  push({ employee_id: s.employeeId, date: s.date, start: s.start, end: s.end, pay: s.pay ?? null })
}

export function removeShift(employeeId: string, date: string) {
  const all = { ...read() }
  delete all[idOf(employeeId, date)]
  write(all)
  push({ employee_id: employeeId, date, start: null })
}
