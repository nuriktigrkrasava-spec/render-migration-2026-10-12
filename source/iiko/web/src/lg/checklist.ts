/* Общее для двух экранов чек-листа: управляющий СДАЁТ (ChecklistUpload),
   владелец СПРАШИВАЕТ (ChecklistBoard). Слоты, зоны и разбор описаны один раз —
   разъехавшиеся подписи зон уже стоили нам того, что сданный слот выглядел
   несданным. */

export const SLOTS = ['10:45', '15:00', '20:45', '23:45']
export const ZONES: [string, string][] = [['hall', 'Зал'], ['kitchen', 'Кухня'], ['wc', 'Санузел']]
// как зоны подписывает сервер (строчными) — нужно для старых ответов без zone_key
export const ZONE_LABEL: Record<string, string> = { hall: 'зал', kitchen: 'кухня', wc: 'санузел' }

export type Done = { branch: string; slot: string; zone: string; zone_key?: string; photos: number; at: string }
/* Замечание разбора приходит объектом {zone, what}. Раньше их склеивали как
   строки — на экран падало «[object Object]», то есть управляющий не узнавал
   ровно того, ради чего разбор и затевался. */
export type Issue = string | { zone?: string; what?: string }
/** Ответ модели по одному пункту чек-листа. item — дословно из ZONE_CRITERIA. */
export type Criterion = { zone?: string; item?: string; ok?: boolean; what?: string }
export type Review = {
  slot: string; ok: boolean; issues: Issue[]; criteria?: Criterion[]
  reason?: string; branch?: string
}

export function issueText(i: Issue): string {
  if (typeof i === 'string') return i
  const zone = (i.zone || '').trim()
  const what = (i.what || '').trim()
  if (zone && what) return `${zone}: ${what}`
  return what || zone || 'замечание без описания'
}

/** Ближайший УЖЕ ПРОШЕДШИЙ слот — с него и начинают сдавать. */
export function nearestSlot(now = new Date()): string {
  const mins = now.getHours() * 60 + now.getMinutes()
  let best = 0
  SLOTS.forEach((s, i) => { if (slotMinutes(s) <= mins) best = i })
  return SLOTS[best]
}

export const slotMinutes = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3))

/** Прошёл ли слот: за будущие слоты спрашивать нечего, красным их красить нельзя. */
export const slotPassed = (slot: string, now = new Date()) =>
  slotMinutes(slot) <= now.getHours() * 60 + now.getMinutes()

/** Сдана ли зона в слоте. Сверяем по КЛЮЧУ («hall»), не по подписи: сервер
    пишет «зал», экран — «Зал», и сравнение подписей не сходилось на регистре. */
export const sentIn = (done: Done[], slot: string, key: string) =>
  done.find((d) => d.slot === slot
    && (d.zone_key || d.zone?.toLowerCase()) === (d.zone_key ? key : ZONE_LABEL[key]))


/* Вердикт по КОНКРЕТНОЙ зоне слота: 'clean' | 'dirty' | null (не разбирали).

   Нужен, чтобы не красить зелёным грязь. Зелёный до сих пор значил «кадры
   присланы» — то есть филиал, снявший загаженный санузел, получал ровно тот же
   зелёный, что и вымытый. Сдано и чисто — разные вещи, и на экране они теперь
   разные. */
export function zoneVerdict(reviews: Review[], slot: string, key: string,
                            branch?: string): 'clean' | 'dirty' | null {
  const r = reviews.find((x) => x.slot === slot && (!branch || x.branch === branch))
  if (!r) return null
  const label = ZONE_LABEL[key]
  const mine = (r.criteria || []).filter((c) => (c.zone || '').trim().toLowerCase() === label)
  if (mine.length) return mine.some((c) => c.ok === false) ? 'dirty' : 'clean'
  // Критериев по зоне нет (старый разбор) — смотрим, есть ли про неё замечание
  const named = (r.issues || []).some(
    (i) => typeof i !== 'string' && (i.zone || '').trim().toLowerCase() === label)
  if (named) return 'dirty'
  return r.ok ? 'clean' : null
}

/** Критерии зоны с ответом модели по каждому: выполнен, не выполнен, не разбирали. */
export function zoneCriteria(reviews: Review[], slot: string, key: string,
                             items: string[]): { item: string; ok: boolean | null; what?: string }[] {
  const r = reviews.find((x) => x.slot === slot)
  const label = ZONE_LABEL[key]
  const mine = (r?.criteria || []).filter((c) => (c.zone || '').trim().toLowerCase() === label)
  return items.map((item) => {
    const hit = mine.find((c) => (c.item || '').trim().toLowerCase() === item.trim().toLowerCase())
    return { item, ok: hit ? hit.ok !== false : null, what: hit?.what }
  })
}
