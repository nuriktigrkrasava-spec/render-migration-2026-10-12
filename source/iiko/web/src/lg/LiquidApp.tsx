import { useEffect, useState, lazy, Suspense } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import './lg.css'
import { container, PageState } from './ui'
import { Icon } from './icons'
import BranchSwitcher from './Branch'
import DataBadge from './DataBadge'
import NoticeCenter from './Notices'

/* Экраны грузятся по требованию: раньше все 16 ехали одним куском в 514 kB,
   хотя при заходе виден ровно один. Главная остаётся статичной — она открывается
   первой, и ленивая загрузка дала бы ей лишнее мигание. */
const lazyFrom = <M extends Record<string, unknown>>(load: () => Promise<M>, key: keyof M) =>
  lazy(() => load().then((m) => ({ default: m[key] as React.ComponentType })))

const Signup = lazy(() => import('./Signup'))
const Onboarding = lazy(() => import('./Onboarding'))
const Dash = lazyFrom(() => import('./screens'), 'Dash')
const Staff = lazyFrom(() => import('./screens'), 'Staff')
const Plan = lazy(() => import('./Plan'))
const Reputation = lazy(() => import('./Reputation'))
const Menu = lazy(() => import('./Menu'))
const Supply = lazy(() => import('./Supply'))
const Schedule = lazy(() => import('./Schedule'))
const Shifts = lazy(() => import('./Shifts'))
const Mountain = lazy(() => import('./Mountain'))
import { getPeriod, setPeriod as setGlobalPeriod, loadPref, savePref,
         getOrg, setOrg, getBranches, pickOrg, type Period, isDemoLink } from '../lib/api'

// render — компонент экрана: обычный или обёрнутый в React.lazy (ленивая загрузка)
/* hidden — раздел есть, но в доке его нет: в него попадают только по кнопке
   «Подключить ресторан» из переключателя филиалов. */
type Sec = { id: string; group: string; label: string; icon: string; badge?: string; periodic: boolean; hidden?: boolean; render: React.ComponentType }

const SECTIONS: Sec[] = [
  { id: 'dash', group: 'Сводка', label: 'Главная', icon: 'dash', periodic: true, render: Dash },
  { id: 'menu', group: 'Деньги', label: 'Меню · продажи', icon: 'menu', periodic: false, render: Menu },
  { id: 'supply', group: 'Деньги', label: 'Склад · заявки', icon: 'supply', badge: 'var(--red)', periodic: false, render: Supply },
  { id: 'shifts', group: 'Операции', label: 'Кассовые смены', icon: 'yoy', periodic: false, render: Shifts },
  { id: 'staff', group: 'Операции', label: 'Смены и персонал', icon: 'staff', periodic: false, render: Staff },
  { id: 'sched', group: 'Операции', label: 'Расписание', icon: 'sched', periodic: false, render: Schedule },
  { id: 'plan', group: 'Операции', label: 'План', icon: 'feed', periodic: false, render: Plan },
  { id: 'mountain', group: 'Сводка', label: 'Гора · задачи', icon: 'anom', periodic: false, render: Mountain },
  { id: 'rep', group: 'Гости', label: 'Соцсети', icon: 'rep', periodic: false, render: Reputation },
  // Кнопка «Подключить ресторан» слала сюда событие, а раздела не было:
  // SECTIONS.find возвращал undefined, «!» прятал это от типов, и обращение
  // к sec.render роняло всё дерево — пользователь видел белый экран.
  { id: 'connect', group: 'Сводка', label: 'Подключить ресторан', icon: 'dash',
    periodic: false, hidden: true, render: Onboarding },
]
const TABS = ['mountain', 'dash', 'supply']
// разделы, сгруппированные как в доке — для листа «Ещё» на узких экранах
const GROUPS: [string, Sec[]][] = SECTIONS.filter((s) => !s.hidden).reduce((acc, s) => {
  const g = acc.find((x) => x[0] === s.group)
  if (g) g[1].push(s); else acc.push([s.group, [s]])
  return acc
}, [] as [string, Sec[]][])
/** Раздел по id. Без запасного варианта любой неизвестный id — белый экран на
    всё приложение: SECTIONS.find возвращает undefined, «!» прячет это от типов,
    и обращение к .render роняет дерево. Такая цена за опечатку слишком высока. */
export function resolveSection(id: string): Sec {
  return SECTIONS.find((s) => s.id === id) || SECTIONS[0]
}

/** Куда разрешено слать событие lg:go. */
export const SECTION_IDS = SECTIONS.map((s) => s.id)

const PERIODS = [['today', 'Сегодня'], ['yesterday', 'Вчера'], ['week', 'Неделя'], ['month', 'Месяц']] as const

/* Подсказка появляется ПРИ ОТКРЫТИИ вкладки и объясняет то, что человек прямо
   сейчас видит. Списком на входе шесть карточек читались бы рекламным блоком.

   Только в демо: своему клиенту рассказывать про его же продукт незачем —
   сервер и отдаёт подсказки лишь по демо-ссылке.

   kind не косметика. «Обсудить» значит, что этого в продукте НЕТ, и говорить
   об этом прямо дешевле, чем на внедрении. */
type Hint = { kind: string; title: string; body: string }

/* Бейдж только там, где вещь НЕ обычная часть продукта. «Работает» не пишем:
   если экран открыт и на нём данные, слово ничего не добавляет — а рядом с
   «обсуждаемо» оно и вовсе обесценивает честную пометку. */
const HINT_TONE: Record<string, { label: string; color: string }> = {
  'сервис': { label: 'отдельный сервис', color: 'var(--acc)' },
  'обсудить': { label: 'обсуждаемо', color: 'var(--amb)' },
}

function DemoHint({ tab }: { tab: string }) {
  const [hints, setHints] = useState<Record<string, Hint> | null>(null)
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('m')
    if (!t) return
    fetch(`/api/demo/hints/${t}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { demo?: boolean; hints?: Record<string, Hint> } | null) => {
        if (d?.demo) setHints(d.hints || {})
      })
      .catch(() => {})
  }, [])
  const h = hints?.[tab]
  if (!h) return null
  const tone = HINT_TONE[h.kind]
  return (
    <div style={{
      gridColumn: 'span 12', padding: '14px 16px', borderRadius: 16,
      border: '1px solid var(--hair)', background: 'var(--glass-2)',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--hi)' }}>{h.title}</span>
        {tone && <span style={{
          fontSize: 10.5, letterSpacing: '.08em', textTransform: 'uppercase',
          fontWeight: 700, color: tone.color,
        }}>{tone.label}</span>}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--mute)' }}>
          демо · данные вымышленные
        </span>
      </div>
      <div style={{ marginTop: 7, fontSize: 13, lineHeight: 1.55, color: 'var(--mute)' }}>
        {h.body}
      </div>
    </div>
  )
}


export default function LiquidApp() {
  // раздел, период и тема переживают F5 — обновление страницы не должно
  // выкидывать из «Расписания» обратно на «Ресторан» (см. loadPref в lib/api).
  const [active, setActive] = useState(() => {
    const saved = loadPref('section')
    return saved && SECTIONS.some((s) => s.id === saved) ? saved : 'mountain'
  })
  const [period, setPeriodLocal] = useState<Period>(getPeriod())
  // единый источник периода: пилл подсвечивает локально и пишет в глобальный стор (useApi перезапрашивает)
  const setPeriod = (p: Period) => { setPeriodLocal(p); setGlobalPeriod(p) }
  const [dark, setDark] = useState(() => loadPref('theme') === 'dark') // светлый — главная тема (референс Truck&Co)
  const [moreOpen, setMoreOpen] = useState(false) // лист «Ещё» на узких экранах
  // Рамка регистрации — только на демо-ссылке и только до первого закрытия:
  // человек, решивший сначала посмотреть, не должен видеть её на каждом разделе.
  const [showSignup, setShowSignup] = useState(() => isDemoLink())
  // Переключатель филиалов нужен ВЛАДЕЛЬЦУ — он смотрит сеть. Прячем его только
  // у управляющего: его филиал задан ссылкой, а список чужих точек обещал бы
  // доступ, которого нет. Роль спрашиваем лёгким запросом, не всей доской.
  // По ссылке филиала переключателя нет с первого кадра: иначе он успевал
  // мигнуть выпадающим списком, пока едет ответ о роли.
  const [isManager, setIsManager] = useState(
    () => (new URLSearchParams(window.location.search).get('m') || '').startsWith('b'))
  const [myBranch, setMyBranch] = useState('')
  /* Филиал должен быть выбран ДО первого запроса данных — и по личной ссылке
     его задаёт ССЫЛКА, а не прошлый визит.

     Раньше это делал переключатель филиалов в шапке — он единственный звал
     getBranches и ставил org. Спрятав его от управляющего, мы оставили org
     пустым, и каждая вкладка висела в бесконечной загрузке. Починили ранним
     выходом `if (getOrg()) return` — и завели вторую беду: сохранённый в
     localStorage филиал прошлого визита побеждал ссылку. С `demo-org-1` весь
     сайт показывал ДЕМО-ДАННЫЕ, с чужим реальным филиалом — 403.

     Теперь филиал сверяется со списком доступных (pickOrg): по ссылке филиала
     он ровно один, у владельца сохранённый выбор переживает F5 как и раньше. */
  useEffect(() => {
    getBranches().then((b) => setOrg(pickOrg(b, getOrg()))).catch(() => {})
  }, [])

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('m')
    if (!t) return
    fetch(`/api/mountain/whoami/${t}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        // Ссылка филиала (`b…`) прибивает человека к одному филиалу — списка
        // быть не должно ни при каких обстоятельствах. Роль спрашиваем, но не
        // полагаемся только на неё: вид ссылки виден прямо из адреса.
        setIsManager(t.startsWith('b') || d?.role === 'manager')
        setMyBranch(d?.branch || '')
      })
      .catch(() => setIsManager(t.startsWith('b')))
  }, [])

  useEffect(() => { savePref('section', active) }, [active])

  // тема применяется к <html> сразу и при переключении
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    savePref('theme', dark ? 'dark' : 'light')
  }, [dark])

  // другие экраны просят переключить вкладку событием
  useEffect(() => {
    const go = (e: Event) => {
      const id = (e as CustomEvent<string>).detail
      // Демо-ссылка — это не чей-то кабинет: добавлять ресторан некуда, аккаунта
      // ещё нет. Отправляем в регистрацию, а не в онбординг.
      if (id === 'connect' && isDemoLink()) { setShowSignup(true); return }
      setActive(id)
    }
    window.addEventListener('lg:go', go)
    return () => window.removeEventListener('lg:go', go)
  }, [])
  const sec = resolveSection(active)
  const Body = sec.render

  function toggleTheme() {
    setDark((d) => !d)
  }

  const dockBtn = (s: Sec) => (
    <button key={s.id} onClick={() => setActive(s.id)} className={`lg-dock-btn ${active === s.id ? 'on' : ''}`} aria-label={s.label}>
      {active === s.id && <motion.span layoutId="dock" className="lg-dock-pill" transition={{ type: 'spring', stiffness: 340, damping: 30 }} />}
      <span style={{ position: 'relative', zIndex: 1, display: 'grid', placeItems: 'center' }}><Icon k={s.icon} size={19} /></span>
      {s.badge && <span className="lg-dock-badge" style={{ background: s.badge }} />}
      <span className="lg-dock-label">{s.label}</span>
    </button>
  )

  return (
    <div className="lg-root" style={{ minHeight: '100vh' }}>
      <div className="lg-wallpaper" aria-hidden />

      {/* Сообщения владельца. Живут на уровне приложения, а не внутри «Горы»:
          управляющий, оставивший сайт на «Главной», не видел их вообще —
          раздел не смонтирован, опрашивать некому. */}
      <NoticeCenter />

      {/* Демо-ссылка ведёт на выдуманную сеть. Рамку показываем сразу: человек
          пришёл смотреть, и вход «подключить своё» должен быть виден, а не
          спрятан. Закрыть можно — тогда остаётся обычное демо. */}
      {showSignup && (
        <Suspense fallback={null}>
          <Signup onClose={() => setShowSignup(false)} />
        </Suspense>
      )}

      {/* Основная (на всю ширину — меню ушло в нижний док) */}
      <main style={{ minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {(
          <header className="lg-head">
            <div className="lg-head-copy">
              <div className="lg-product-kicker">
                <span className="lg-product-mark" aria-hidden>i</span>
                <span>iiko · {sec.group}</span>
              </div>
              <motion.h1 key={sec.id} className="display lg-title" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>{sec.label}</motion.h1>
            </div>
            <div className="lg-head-actions">
              <DataBadge />
              {/* По личной ссылке (?m=…) филиал уже задан токеном — переключатель
                  сети управляющему только мешает и обещает доступ, которого нет. */}
              {/* Владельцу — выбор филиала, управляющему — та же плашка, но со
                  своим филиалом и без выпадающего списка: переключать ему нечего. */}
              {isManager
                ? <span className="lg-branch-btn" style={{ cursor: 'default' }} title={myBranch || 'ваш филиал'}>
                    <span className="lg-branch-ic" aria-hidden>◈</span>
                    {/* Название режем многоточием, как у владельца в BranchSwitcher:
                        «Астана, ул. Г.Алиева 10 (EXPO)» в одну строку распирало
                        шапку и вся страница ехала вбок на телефоне. */}
                    <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>
                      {myBranch || 'Ваш филиал'}
                    </span>
                  </span>
                : <BranchSwitcher />}
              {sec.periodic && (
                <div className="lg-seg">
                  {PERIODS.map((p) => (
                    <button key={p[0]} onClick={() => setPeriod(p[0])} className={`lg-per ${period === p[0] ? 'on' : ''}`}>
                      {period === p[0] && <motion.span layoutId="seg" className="lg-seg-pill" transition={{ type: 'spring', stiffness: 320, damping: 30 }} />}
                      <span style={{ position: 'relative', zIndex: 1 }}>{p[1]}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </header>
        )}

        <div className="lg-content">
          <AnimatePresence mode="wait">
            <motion.div key={active} variants={container} initial="hidden" animate="show" exit={{ opacity: 0, y: -8, transition: { duration: 0.18 } }}
              style={{ display: 'grid', gridTemplateColumns: 'repeat(12,1fr)', gap: 16, alignItems: 'stretch' }} className="lg-bento">
              <DemoHint tab={active} />
              <Suspense fallback={<PageState loading>Открываю раздел…</PageState>}>
                <Body />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      {/* Нижний плавающий стеклянный док (десктоп) — вся навигация */}
      <nav className="lg-dock glass warp" aria-label="Разделы">
        <span className="lg-dock-brand">iiko</span>
        <span className="lg-dock-sep" />
        {SECTIONS.filter((s) => !s.hidden).map(dockBtn)}
        <span className="lg-dock-sep" />
        <button onClick={toggleTheme} className="lg-dock-btn" aria-label="Сменить тему">
          <span style={{ position: 'relative', zIndex: 1, fontSize: 17 }}>{dark ? '◐' : '◑'}</span>
          <span className="lg-dock-label">{dark ? 'Ночь' : 'День'}</span>
        </button>
      </nav>

      {/* Таб-бар (телефон). Док на узких экранах скрыт, поэтому «Ещё» — единственный
          путь к остальным 13 разделам: без него Расписание/Склад/План недостижимы. */}
      <nav className="lg-tabbar glass">
        {TABS.map((id) => {
          const s = SECTIONS.find((x) => x.id === id)!
          return (
            <button key={id} onClick={() => { setActive(id); setMoreOpen(false) }} className={`lg-tab ${active === id && !moreOpen ? 'on' : ''}`}>
              <Icon k={s.icon} /><span>{s.label}</span>
            </button>
          )
        })}
        <button onClick={() => setMoreOpen((o) => !o)} className={`lg-tab ${moreOpen || !TABS.includes(active) ? 'on' : ''}`} aria-expanded={moreOpen}>
          <Icon k="menu" /><span>Ещё</span>
        </button>
      </nav>

      {/* Лист «Ещё» — полный список разделов, сгруппированный как в доке */}
      <AnimatePresence>
        {moreOpen && (
          <>
            <motion.div className="lg-sheet-scrim" onClick={() => setMoreOpen(false)}
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
            <motion.div className="lg-sheet glass" role="dialog" aria-label="Все разделы"
              initial={{ y: '100%' }} animate={{ y: 0 }} exit={{ y: '100%' }}
              transition={{ type: 'spring', stiffness: 340, damping: 34 }}>
              <div className="lg-sheet-grip" aria-hidden />
              {GROUPS.map(([group, items]) => (
                <div key={group} className="lg-sheet-group">
                  <div className="lg-sheet-head">{group}</div>
                  {items.map((s) => (
                    <button key={s.id} className={`lg-sheet-item ${active === s.id ? 'on' : ''}`}
                      onClick={() => { setActive(s.id); setMoreOpen(false) }}>
                      <Icon k={s.icon} size={17} />
                      <span>{s.label}</span>
                      {s.badge && <span className="lg-sheet-badge" style={{ background: s.badge }} />}
                    </button>
                  ))}
                </div>
              ))}
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Плавающий ассистент рядом с доком убран по просьбе владельца.
          Файл Agent.tsx и вся его логика на месте — вернуть можно одной строкой:
          <Agent /> здесь же. */}
    </div>
  )
}
