// Живой бэкенд: FastAPI api.py. Демо-организация "demo-org-1" всегда отдаёт
// демо-данные (см. _use_demo_data в api.py) — без Telegram-авторизации.
// Выбор филиала: ORG динамический (getOrg/setOrg), useApi перезапрашивает при смене.
// Офлайн-демо (56 kB вшитых ответов) нужно только сборке с VITE_BAKED=1, а
// импортировалось всегда — и ехало в бандл каждому реальному пользователю.
// Грузим по требованию и запоминаем промис, чтобы модуль тянулся один раз.
let bakedPromise: Promise<Record<string, unknown>> | null = null
function loadBaked(): Promise<Record<string, unknown>> {
  if (!bakedPromise) bakedPromise = import('./bakedData').then((m) => m.BAKED)
  return bakedPromise
}

export type Period = 'today' | 'yesterday' | 'week' | 'month'
export type Branch = { organization_id: string; organization_name?: string; name?: string }

const BAKED_MODE = import.meta.env.VITE_BAKED === '1'
const DEMO_ORG = 'demo-org-1'

// ── сохранение выбора между перезагрузками ──
// Без этого F5 сбрасывал владельца на первый филиал по алфавиту — при 13 филиалах
// это значит «каждое обновление уводит не туда». Храним на устройстве, как расписание.
const LS_PREFIX = 'lg:'
export function loadPref(key: string): string | null {
  try { return localStorage.getItem(LS_PREFIX + key) } catch { return null }
}
export function savePref(key: string, val: string) {
  try { localStorage.setItem(LS_PREFIX + key, val) } catch { /* приватный режим — просто не сохраняем */ }
}

/* ── Авторизация: один раз на все запросы ──
   Экранов два десятка, и почти каждый ходит в /api собственным fetch. Прописать
   заголовок в каждом — гарантированно забыть в одном и поймать 401 на проде,
   где, в отличие от локали, BOT_TOKEN задан и dev-режим выключен. Поэтому
   оборачиваем fetch один раз:
     • внутри Telegram шлём его подпись (X-Telegram-Init-Data);
     • по личной ссылке ?m=… шлём токен (X-Site-Token) — он подписан на
       ENCRYPTION_KEY и даёт ровно ту личность, что в нём зашита;
     • по личной ссылке СОТРУДНИКА ?e=… шлём код (X-Staff-Code). Это отдельный,
       гораздо более узкий вход: он открывает только свои смены, оплату и
       сообщения, и на сервере обрабатывается мимо get_current_user — иначе
       сотрудник получил бы кабинет филиала целиком.
   Трогаем только свои /api/-запросы: чужие домены обёртка пропускает как есть. */
type TgWindow = Window & { Telegram?: { WebApp?: { initData?: string } }; __lgAuthPatched?: boolean }

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = {}
  if (typeof window === 'undefined') return h
  try {
    const init = (window as TgWindow).Telegram?.WebApp?.initData
    if (init) h['X-Telegram-Init-Data'] = init
  } catch { /* не в Telegram */ }
  try {
    const q = new URLSearchParams(window.location.search)
    const m = q.get('m')
    if (m) h['X-Site-Token'] = m
    const e = q.get('e') || loadPref('staffCode')
    if (e) h['X-Staff-Code'] = e
  } catch { /* нет доступа к адресу */ }
  return h
}

/** Открыто по ДЕМО-ссылке: токен показа начинается с «d».

    По ней человек видит выдуманную сеть, поэтому именно здесь и предлагаем
    подключить свой iiko — на личной ссылке владельца это было бы навязчиво. */
export function isDemoLink(): boolean {
  if (typeof window === 'undefined') return false
  try {
    const m = new URLSearchParams(window.location.search).get('m') || ''
    return m.startsWith('d') && m.includes('.')
  } catch {
    return false
  }
}

/** Ссылка подключения (QR филиала). Пусто — открыли не по ней. */
export function getJoinToken(): string {
  if (typeof window === 'undefined') return ''
  try {
    return new URLSearchParams(window.location.search).get('j') || ''
  } catch {
    return ''
  }
}

/* Код сотрудника: из адреса, иначе из запомненного.

   Запоминаем намеренно. Мини-апп открывают кнопкой бота, и подпись Telegram
   приезжает в хеше адреса — любая перезагрузка со сменой адреса её теряет.
   Сохранённый код позволяет открыть приложение повторно, ничего не пересылая. */
export function getStaffCode(): string {
  if (typeof window === 'undefined') return ''
  try {
    const q = new URLSearchParams(window.location.search).get('e')
    if (q) { savePref('staffCode', q); return q }
    return loadPref('staffCode') || ''
  } catch {
    return ''
  }
}

export function saveStaffCode(code: string) {
  savePref('staffCode', code)
}

if (typeof window !== 'undefined' && !(window as TgWindow).__lgAuthPatched) {
  ;(window as TgWindow).__lgAuthPatched = true
  const orig = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.startsWith('/api/')) return orig(input, init)
    const extra = authHeaders()
    if (!Object.keys(extra).length) return orig(input, init)
    const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined))
    for (const [k, v] of Object.entries(extra)) if (!headers.has(k)) headers.set(k, v)
    return orig(input, { ...init, headers })
  }
}

// ── текущий выбранный филиал (org) + подписка на смену ──
// В baked — сразу демо. В реальном режиме берём филиал из прошлой сессии; если его
// нет — стартуем «не выбран» (''), чтобы НЕ мигать демо-данными: useApi ждёт, пока
// BranchSwitcher поставит филиал. Сохранённый org валидирует BranchSwitcher по
// /api/restaurants: чужой/удалённый филиал не должен пережить смену аккаунта.
let currentOrg = BAKED_MODE ? DEMO_ORG : (loadPref('org') || '')
const listeners = new Set<() => void>()
export function getOrg() { return currentOrg }
export function setOrg(id: string) {
  if (id && id !== currentOrg) {
    currentOrg = id
    if (!BAKED_MODE) savePref('org', id)
    listeners.forEach((l) => l())
  }
}
export function onOrgChange(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn) } }
export const ORG = DEMO_ORG // обратная совместимость

// ── глобальный период (тот же паттерн, что и org) ──
// периодичные экраны НЕ шлют period в useApi → берут отсюда и перезапрашивают при смене.
// экраны с фиксированным периодом (Home «сегодня») передают period явно и игнорируют это.
const PERIOD_VALUES: Period[] = ['today', 'yesterday', 'week', 'month']
const savedPeriod = loadPref('period') as Period | null
let currentPeriod: Period = savedPeriod && PERIOD_VALUES.includes(savedPeriod) ? savedPeriod : 'month'
const periodListeners = new Set<() => void>()
export function getPeriod(): Period { return currentPeriod }
export function setPeriod(p: Period) {
  if (p && p !== currentPeriod) { currentPeriod = p; savePref('period', p); periodListeners.forEach((l) => l()) }
}
export function onPeriodChange(fn: () => void) { periodListeners.add(fn); return () => { periodListeners.delete(fn) } }
// человекочитаемый ярлык текущего периода (для подписей/eyebrow)
const PERIOD_LABELS: Record<Period, string> = { today: 'сегодня', yesterday: 'вчера', week: 'за неделю', month: 'за месяц' }
export function periodLabel(): string { return PERIOD_LABELS[currentPeriod] }

// данные демо (офлайн-сборка или демо-организация), а не реальный iiko
export function isDemoData() { return BAKED_MODE || currentOrg === DEMO_ORG }

// в демо/baked — только один демо-ресторан (без выдуманных чужих филиалов).
// реальные филиалы появляются лишь после подключения своего аккаунта (/api/restaurants, изоляция по юзеру).
const DEMO_BRANCHES: Branch[] = [
  { organization_id: 'demo-org-1', organization_name: 'Демо-ресторан' },
]

/* Какой филиал показывать: сохранённый — ТОЛЬКО если он есть среди доступных.

   Сохранённый в localStorage филиал переживал смену ссылки: человек однажды
   смотрел демо или сеть владельца, открывал ссылку своего филиала — и экраны
   продолжали спрашивать прошлый org. С `demo-org-1` это давало демо-данные на
   всём сайте (_get_org_id пропускает его даже по ссылке филиала), с чужим
   реальным филиалом — 403. Проверку делал BranchSwitcher, но у управляющего он
   скрыт, и проверять стало некому. Правило переехало сюда — оно не зависит от
   того, нарисован переключатель или нет. */
export function pickOrg(branches: Branch[], saved: string): string {
  if (!branches.length) return saved
  if (saved && branches.some((b) => b.organization_id === saved)) return saved
  return branches[0].organization_id
}

export async function getBranches(): Promise<Branch[]> {
  if (BAKED_MODE) return DEMO_BRANCHES
  try {
    const res = await fetch('/api/restaurants', { headers: { Accept: 'application/json' } })
    if (!res.ok) throw new Error(String(res.status))
    const data = await res.json()
    return Array.isArray(data) && data.length ? data : DEMO_BRANCHES
  } catch { return DEMO_BRANCHES }
}

export async function apiGet<T>(path: string, params: Record<string, string> = {}, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams(params).toString()
  const org = currentOrg
  const url = `/api/${path}/${org}${qs ? `?${qs}` : ''}`
  if (BAKED_MODE) {
    // в baked данные вшиты только под demo-org-1 — любой филиал показывает демо (для демонстрации выбора)
    const BAKED = await loadBaked()
    const demoUrl = `/api/${path}/${DEMO_ORG}${qs ? `?${qs}` : ''}`
    const hit = BAKED[url] ?? BAKED[`/api/${path}/${org}`] ?? BAKED[demoUrl] ?? BAKED[`/api/${path}/${DEMO_ORG}`]
    if (hit !== undefined) return Promise.resolve(hit as T)
    throw new Error(`${path}: нет вшитых данных`)
  }
  const res = await fetch(url, { headers: { Accept: 'application/json' }, signal })
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`)
  return res.json() as Promise<T>
}
