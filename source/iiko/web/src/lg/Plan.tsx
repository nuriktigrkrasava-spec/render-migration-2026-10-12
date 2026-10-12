import { useCallback, useEffect, useRef, useState, lazy, Suspense, type CSSProperties } from 'react'
import { Glass } from './ui'
import { useOrg } from '../lib/useApi'
import { fotCoverage, fotGapsLine, isoWeekOf, planPct, shiftWeek } from './planWeek'
import type { Fot } from './planWeek'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
// Экран собрания открывают редко — незачем тянуть его в общий кусок вкладки
const Progress = lazy(() => import('./Progress'))
import type { ScoreRow } from './Progress'

/* Вкладка «План» — повторяет таблицу владельца.

   Карточка на филиал: строки Пн–Вс, столбцы Доставка / Зал+вынос / Агрегаторы,
   под каждым днём план и факт, снизу «Итого план / Итого факт». Ниже — сводка
   сети с процентом выполнения и «база плана»: средняя неделя за последние
   шесть недель, из которой план в таблице и ставится.

   Владелец видит «Итого сеть» и сводку по филиалам, управляющий — карточку
   своего филиала с днями (роль приходит из /api/mountain/whoami, как в шапке
   сайта). Девять карточек по семь дней владельцу не нужны: он смотрит итоги,
   а по дням правит тот, кто на смене.

   Главное число — НЕДЕЛЬНОЕ и сравнивается день в день: сколько дней текущей
   недели закрыто, столько же берётся у прошлой. Собрание во вторник, и «факт
   полутора дней против всей прошлой недели» читался бы как провал на ровном
   месте. Сегодняшний день не закрыт и в сравнение не входит. */

type Grid = Record<string, Record<string, number>>
type Branch = {
  organization_id: string; name: string
  plan: Grid; fact: Grid; base: Grid; prev: Grid
  total_plan: Record<string, number>; total_fact: Record<string, number>
  total_base: Record<string, number>; total_prev: Record<string, number>
  plan_auto?: boolean          // план поставлен сам по средней, руками не ставили
  fot?: Fot
}
type Cell = { org: string; dow: number; ch_type: string; planned: number }
type Win = { key: string; label: string; unit: string; from: number; to: number; better: number; pct: number | null }
type MetricRow = {
  organization_id: string; name: string
  now: Record<string, number | null>; prev: Record<string, number | null>; wins: Win[]
  /* что просело — теми же порогами, что и wins; и счётчики явок за неделю */
  losses?: Win[]
  staff?: { total: number; late: number; late_pct: number; late_minutes: number } | null
}
type MetricsData = {
  week: string
  // границы недели сервер отдаёт всегда — их показывает экран собрания
  from: string
  to: string
  done_days?: number
  metrics: { key: string; label: string; unit: string; lower_better: boolean }[]
  branches: MetricRow[]
}
type PlanData = {
  week: string; from: string; to: string; types: string[]
  /* network приходит НЕ ВСЕГДА: когда филиалов ноль, сервер отдаёт короткий
     ответ без сводки сети. Обязательный тип это скрывал, и вкладка падала на
     `network.fact` — белым экраном на всё приложение, а не пустой карточкой. */
  branches: Branch[]; network?: Branch; base_weeks: number; demo: boolean
  /* Сколько дней недели уже ЗАКРЫТО. Считает сервер: сравнение с прошлой
     неделей идёт день в день, и разойдись фронт с бэком хоть на день —
     динамика во вторник соврёт. Сегодняшний день не закрыт. */
  done_days: number; running: boolean
  /* Сегодняшний (незакрытый) день попал в факт. Так бывает только когда филиал
     в ответе один — управляющему он нужен, владельцу сети ради него девять
     серверов не дёргаем. */
  live_today: boolean
}

/* Вынос отделён от зала (решение владельца 26.08.2026): у самовывоза своя
   нагрузка на кухню и свой ритм, в одной колонке с залом он прятался.
   В iiko это типы «Самовывоз» и «Сайт самовывоз». */
const TYPES = ['delivery', 'hall', 'pickup', 'agg'] as const
const TYPE_LABEL: Record<string, string> = {
  delivery: 'Доставка', hall: 'Зал', pickup: 'Вынос', agg: 'Агрегаторы',
}
const DOW = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
const DOW_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

const dm = (iso: string) => iso.slice(8, 10) + '.' + iso.slice(5, 7)
const pctColor = (p: number) => (p >= 100 ? 'var(--grn)' : p >= 85 ? 'var(--amb)' : 'var(--red)')
const pct = planPct
const nfmt = (n: number) => n.toLocaleString('ru-RU')


/* Динамика к прошлой неделе. Без базы процента нет — рисуем прочерк, а не
   «+100%»: рост с нуля не рост, а первая неделя работы. Колебание меньше
   процента не красим: на собрании зелёное «+0,4%» — ложный сигнал. */
function Delta({ now, was }: { now: number; was: number }) {
  if (!was) return <span style={{ color: 'var(--mute)' }}>—</span>
  const d = Math.round(((now - was) / was) * 1000) / 10
  const col = Math.abs(d) < 1 ? 'var(--mute)' : d > 0 ? 'var(--grn)' : 'var(--red)'
  const arrow = Math.abs(d) < 1 ? '' : d > 0 ? '↑' : '↓'
  return (
    <span className="tnum" style={{ color: col, fontWeight: 700, whiteSpace: 'nowrap' }}>
      {d > 0 ? '+' : ''}{String(d).replace('.', ',').replace('-', '−')}%{arrow && ` ${arrow}`}
    </span>
  )
}

/* Что переносить с прошлой недели.

   Филиал, у которого на прошлой неделе плана НЕ БЫЛО, пропускаем. Иначе одно
   нажатие «перенести» разослало бы нули и стёрло план текущей недели: живой
   случай 26.08 — план стоял у всех девяти филиалов, а на прошлой неделе только
   у одного, и кнопка молча обнулила бы восемь. «Перенести» значит взять то,
   что было, а не обнулить то, что есть. */
export function prevWeekCells(branches: Branch[], types: readonly string[]) {
  const cells: Cell[] = []
  const next: Record<string, Grid> = {}
  for (const b of branches) {
    if (!types.some((t) => (b.total_plan?.[t] ?? 0) > 0)) continue
    next[b.organization_id] = JSON.parse(JSON.stringify(b.plan))
    for (let d = 1; d <= 7; d++) {
      for (const t of types) {
        cells.push({ org: b.organization_id, dow: d, ch_type: t, planned: b.plan[d]?.[t] ?? 0 })
      }
    }
  }
  return { cells, next, moved: Object.keys(next).length }
}

/* Топ филиалов «кто прибавил».

   Два показателя, за которые владелец спрашивает на собрании: сколько заказов
   и сколько опозданий по доставке. Считаются они уже в /metrics — здесь только
   отбор и порядок.

   Показываем ТОЛЬКО улучшившихся: это не рейтинг, а список тех, кого стоит
   назвать вслух. Кто просел — виден в «Сводке сети» по динамике, и вываливать
   его отдельной доской позора владелец не просил.

   Пороги обязательны. Заказы: +1% — иначе в топ попадает филиал, сделавший на
   два заказа больше из тысячи. Опоздания: 0,5 процентного пункта — меньше это
   один заказ на двести, шум замера, а не работа смены. */
const TOP_LIMIT = 5

type Improver = { name: string; from: number; to: number; diff: number; pct: number | null }

export function topImprovers(rows: MetricRow[], key: 'orders' | 'late_pct'): Improver[] {
  const lowerBetter = key === 'late_pct'
  const out: Improver[] = []
  for (const r of rows) {
    const to = r.now?.[key], from = r.prev?.[key]
    if (to == null || from == null) continue
    const diff = lowerBetter ? from - to : to - from
    if (diff <= 0) continue
    if (lowerBetter ? diff < 0.5 : diff < Math.abs(from) * 0.01) continue
    out.push({ name: r.name, from, to, diff: Math.round(diff * 10) / 10,
               pct: from ? Math.round((diff / Math.abs(from)) * 100) : null })
  }
  out.sort((a, b) => b.diff - a.diff)
  return out.slice(0, TOP_LIMIT)
}

/* Список строк — flex-колонка, а НЕ grid: у grid неявная колонка `auto`
   растягивалась до max-content строки (414 px в карточке 282 px) и утаскивала
   всю страницу в горизонтальную прокрутку на телефоне. Строка переносится:
   на узком экране числа уходят под название филиала. */
function TopLane({ title, hint, rows, unit, empty }: {
  title: string; hint: string; rows: Improver[]; unit: string; empty: string
}) {
  return (
    <div style={{ padding: 14, borderRadius: 14, background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
      <div style={{ fontSize: 12.5, fontWeight: 750, color: 'var(--hi)' }}>{title}</div>
      <div style={{ fontSize: 11, color: 'var(--mute)', marginTop: 2 }}>{hint}</div>
      {!rows.length ? (
        <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--mute)' }}>{empty}</div>
      ) : (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 7, minWidth: 0 }}>
          {rows.map((r, i) => (
            <div key={r.name} style={{ display: 'flex', gap: 9, alignItems: 'baseline', fontSize: 12.5, minWidth: 0, flexWrap: 'wrap' }}>
              {/* Место цифрой, а не медалькой: филиалов девять, и «серебро»
                  за минус полпроцента опозданий выглядело бы издёвкой. */}
              <span className="tnum" style={{ width: 15, flex: 'none', color: i === 0 ? 'var(--grn)' : 'var(--mute)', fontWeight: 750 }}>{i + 1}</span>
              <span style={{ flex: '1 1 110px', minWidth: 0, color: 'var(--hi)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              <span className="tnum" style={{ color: 'var(--mute)', fontSize: 11.5, whiteSpace: 'nowrap' }}>
                {nfmt(r.from)}{unit} → {nfmt(r.to)}{unit}
              </span>
              <span className="tnum" style={{ color: 'var(--grn)', fontWeight: 750, whiteSpace: 'nowrap' }}>
                {unit === '%' ? `−${String(r.diff).replace('.', ',')} п.п.` : `+${nfmt(r.diff)}`}
                {unit !== '%' && r.pct != null && ` · +${r.pct}%`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Improved({ data, running, doneDays }: { data: MetricsData; running: boolean; doneDays: number }) {
  const orders = topImprovers(data.branches, 'orders')
  const late = topImprovers(data.branches, 'late_pct')
  const period = running
    ? `${doneDays} закр. ${doneDays === 1 ? 'день' : 'дн.'} против тех же дней прошлой недели`
    : 'неделя против прошлой'
  return (
    <Glass span={12}>
      <div className="eyebrow"><span>Кто прибавил</span><span className="hint">{period}</span></div>
      <div style={{ marginTop: 12, display: 'grid', gap: 12,
                    gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))' }}>
        <TopLane title="Заказов больше" hint="количество заказов, все каналы" rows={orders} unit=""
          empty="Пока никто не прибавил в заказах." />
        <TopLane title="Меньше опозданий" hint="доля опоздавших доставок" rows={late} unit="%"
          empty="Пока никто не снизил опоздания." />
      </div>
    </Glass>
  )
}

/* Поздравление во весь экран.

   Это момент, а не строка в отчёте: показываем один раз на неделю, поверх всего,
   с затемнением — и уходим. Второй раз за ту же неделю не лезем (помним в
   localStorage), иначе открытка превратится в баннер, который закрывают не глядя.

   Движение: вход пружиной с лёгким отскоком — он здесь уместен, потому что это
   празднование, а не служебная панель. При prefers-reduced-motion — простое
   проявление без прыжков. Выход по тому же пути, что и вход. */
function Celebration({ rows, week, onClose }: { rows: MetricRow[]; week: string; onClose: () => void }) {
  const reduce = useReducedMotion()
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  const best = [...rows].sort((a, b) => b.wins.length - a.wins.length)
  return (
    <motion.div role="dialog" aria-label="Что улучшилось за неделю" onClick={onClose}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.22 }}
      style={{
        position: 'fixed', inset: 0, zIndex: 60, display: 'grid', placeItems: 'center',
        padding: 20, background: 'color-mix(in srgb, var(--ink, #0b1020) 55%, transparent)',
        backdropFilter: 'blur(18px) saturate(160%)', WebkitBackdropFilter: 'blur(18px) saturate(160%)',
      }}>
      <motion.div onClick={(e) => e.stopPropagation()}
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 24, scale: 0.94 }}
        animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, y: 24, scale: 0.94 }}
        transition={reduce ? { duration: 0.2 } : { type: 'spring', bounce: 0.2, duration: 0.5 }}
        style={{
          width: 'min(760px, 100%)', maxHeight: '82vh', overflowY: 'auto',
          background: 'var(--card)', border: '1px solid var(--hair)', borderRadius: 24,
          padding: '28px 26px', boxShadow: '0 40px 90px -30px rgba(10,20,50,.55)',
        }}>
        <motion.div aria-hidden
          initial={reduce ? {} : { scale: 0.4, rotate: -20 }}
          animate={reduce ? {} : { scale: 1, rotate: 0 }}
          transition={reduce ? {} : { type: 'spring', bounce: 0.5, duration: 0.7, delay: 0.08 }}
          style={{ fontSize: 44, lineHeight: 1 }}>🎉</motion.div>
        <h2 style={{
          margin: '12px 0 4px', fontSize: 'clamp(24px, 4vw, 34px)', lineHeight: 1.08,
          letterSpacing: '-0.02em', fontWeight: 760, color: 'var(--hi)',
        }}>
          За неделю стало лучше
        </h2>
        <div style={{ fontSize: 13.5, color: 'var(--mute)' }}>
          Неделя {week.split('-W')[1]} против прошлой · {best.length} филиалов улучшили показатели
        </div>

        <div style={{ marginTop: 20, display: 'grid', gap: 12 }}>
          {best.map((r, i) => (
            <motion.div key={r.organization_id}
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
              animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
              transition={reduce ? { duration: 0.2 } : { type: 'spring', bounce: 0, duration: 0.4, delay: 0.12 + i * 0.05 }}
              style={{
                padding: '14px 16px', borderRadius: 16, border: '1px solid var(--hair)',
                background: 'linear-gradient(135deg, color-mix(in srgb, var(--grn) 9%, transparent), transparent)',
              }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--hi)', letterSpacing: '-0.01em' }}>{r.name}</div>
              <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {r.wins.map((w) => (
                  <span key={w.key} style={{
                    fontSize: 13, fontWeight: 620, color: 'var(--grn)', padding: '5px 11px',
                    borderRadius: 10, background: 'color-mix(in srgb, var(--grn) 13%, transparent)',
                    fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap',
                  }}>
                    {w.label} {w.from} → {w.to}{w.unit ? ` ${w.unit}` : ''}
                  </span>
                ))}
              </div>
            </motion.div>
          ))}
        </div>

        <button onClick={onClose} style={{
          marginTop: 22, width: '100%', padding: '13px 18px', borderRadius: 14, border: 'none',
          fontSize: 15, fontWeight: 680, fontFamily: 'inherit', cursor: 'pointer',
          color: 'var(--on-acc)', background: 'var(--acc)',
        }}>Отлично</button>
      </motion.div>
    </motion.div>
  )
}

/* Тот же материал строкой в потоке — чтобы вернуться к нему после закрытия. */
function Congrats({ rows, onOpen }: { rows: MetricRow[]; onOpen: () => void }) {
  const winners = rows.filter((r) => r.wins.length)
  if (!winners.length) return null
  const total = winners.reduce((n, r) => n + r.wins.length, 0)
  return (
    <Glass span={12}>
      <div className="eyebrow"><span>Что улучшилось</span><span className="hint">неделя к неделе</span></div>
      <button onClick={onOpen} style={{
        marginTop: 12, width: '100%', textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit',
        display: 'flex', gap: 14, alignItems: 'center', padding: '14px 16px', borderRadius: 16,
        border: '1px solid var(--hair)',
        background: 'linear-gradient(135deg, color-mix(in srgb, var(--grn) 10%, transparent), transparent)',
      }}>
        <span aria-hidden style={{ fontSize: 26, lineHeight: 1 }}>🎉</span>
        <span style={{ flex: 1 }}>
          <span style={{ display: 'block', fontSize: 15, fontWeight: 700, color: 'var(--hi)', letterSpacing: '-0.01em' }}>
            {winners.length} филиалов улучшили показатели
          </span>
          <span style={{ display: 'block', fontSize: 12.5, color: 'var(--mute)', marginTop: 2 }}>
            {total} показателей стало лучше — нажми, чтобы показать на собрании
          </span>
        </span>
        <span style={{ fontSize: 18, color: 'var(--mute)' }}>›</span>
      </button>
    </Glass>
  )
}

/* Лист «Неделя NN» из таблицы владельца: время приготовления, курьер в дороге,
   ожидание, опоздания и рейтинг — по каждому филиалу, со стрелкой к прошлой неделе. */
function WeekMetrics({ data }: { data: MetricsData }) {
  const fmt = (v: number | null | undefined, unit: string) =>
    v == null ? '—' : `${v}${unit ? ' ' + unit : ''}`
  return (
    <Glass span={12}>
      <div className="eyebrow"><span>Показатели недели</span><span className="hint">и сдвиг к прошлой неделе</span></div>
      <div style={{ overflowX: 'auto', marginTop: 12 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 700, tableLayout: 'fixed' }}>
          <colgroup>
            <col style={{ width: 210 }} />
            {data.metrics.map((m) => <col key={m.key} />)}
          </colgroup>
          <thead><tr>
            <th style={thL}>Филиал</th>
            {data.metrics.map((m) => <th key={m.key} style={thC}>{m.label}</th>)}
          </tr></thead>
          <tbody>
            {data.branches.map((b) => (
              <tr key={b.organization_id}>
                <td style={{ ...td, fontWeight: 650, color: 'var(--hi)', whiteSpace: 'nowrap' }}>{b.name}</td>
                {data.metrics.map((m) => {
                  const now = b.now[m.key], was = b.prev[m.key]
                  const diff = now != null && was != null ? +(now - was).toFixed(2) : null
                  const better = diff == null || diff === 0 ? null : (m.lower_better ? diff < 0 : diff > 0)
                  return (
                    <td key={m.key} style={{ ...td, textAlign: 'center' }}>
                      <div className="tnum" style={{ fontWeight: 700, color: 'var(--hi)' }}>{fmt(now, m.unit)}</div>
                      {diff != null && diff !== 0 && (
                        <div className="tnum" style={{ fontSize: 11.5, fontWeight: 650, color: better ? 'var(--grn)' : 'var(--red)' }}>
                          {diff > 0 ? '↑' : '↓'} {Math.abs(diff)}
                        </div>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 10, fontSize: 11, color: 'var(--mute)', lineHeight: 1.5 }}>
        Время — медиана по курьерским заказам: приготовление, ожидание курьера и дорога.
        Те же цифры, что в листе «Неделя NN» твоей таблицы, только считает их сайт.
        Прочерк значит, что за эту неделю у филиала не было курьерских доставок с отметками времени.
      </div>
    </Glass>
  )
}

export default function Plan() {
  const [wk, setWk] = useState(() => isoWeekOf(new Date()))
  const [data, setData] = useState<PlanData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [plan, setPlan] = useState<Record<string, Grid>>({})   // org → правки, до сохранения
  /* Зеркало того, что сейчас лежит НА СЕРВЕРЕ. Нужно, чтобы понять, изменилась
     ли ячейка. Сравнивать с ответом при загрузке нельзя: после «как есть» он
     устаревает, и правка ячейки обратно к исходному значению считалась бы
     «ничего не изменилось» — на экране одно, в базе другое. */
  const [serverPlan, setServerPlan] = useState<Record<string, Grid>>({})
  const [isManager, setIsManager] = useState<boolean | null>(null)
  const [saved, setSaved] = useState('')
  const [metrics, setMetrics] = useState<MetricsData | null>(null)
  const [celebrate, setCelebrate] = useState(false)
  const [showProgress, setShowProgress] = useState(false)
  // Оценки филиалов живут на «Горе», а не в метриках недели — тянем их
  // только когда экран собрания открывают: лишний запрос на каждой
  // загрузке вкладки «План» не нужен.
  const [scores, setScores] = useState<ScoreRow[]>([])
  const [busy, setBusy] = useState(false)
  const org = useOrg()
  // Ссылка управляющего: сервер по ней сам сузит охват до его филиала —
  // флагу network он не верит (иначе управляющий увидел бы чужие цифры).
  const token = new URLSearchParams(window.location.search).get('m') || ''

  // Управляющему сеть не показываем: его филиал задан ссылкой, чужие цифры —
  // обещание доступа, которого нет. Роль спрашиваем тем же лёгким запросом,
  // что и шапка сайта.
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('m')
    if (!t) { setIsManager(false); return }
    fetch(`/api/mountain/whoami/${t}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setIsManager(d?.role === 'manager'))
      .catch(() => setIsManager(false))
  }, [])

  /* Оценки филиалов для экрана собрания.

     Владелец открывает сайт без ?m= — токен у него берётся так же, как на
     «Горе»: из своей личной ссылки. Без этого колонка «Оценка» и «Топ по
     оценке» стояли пустыми ровно у того, для кого экран и сделан.

     Оценка собирается в фоне (десятки секунд на девять филиалов): если сервер
     сказал score_pending, один раз перезапрашиваем — как на «Горе». */
  const scoreTimer = useRef<number | null>(null)
  const loadScores = useCallback((t: string, again = true) => {
    fetch(`/api/mountain/data/${t}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('scores'))))
      .then((d: { ranking?: ScoreRow[]; score_pending?: boolean }) => {
        setScores(d.ranking || [])
        if (d.score_pending && again) {
          scoreTimer.current = window.setTimeout(() => loadScores(t, false), 25000)
        }
      })
      .catch(() => {})   // без оценок экран живёт: топ по движению остаётся
  }, [])
  useEffect(() => () => { if (scoreTimer.current) clearTimeout(scoreTimer.current) }, [])

  const load = useCallback(() => {
    if (!org || isManager === null) return
    setData(null); setErr(null)
    fetch(`/api/plan-week/${org}?week=${wk}&network=${isManager ? 0 : 1}${token ? `&token=${token}` : ''}`,
      { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: PlanData) => {
        setData(d)
        const p: Record<string, Grid> = {}
        for (const b of d.branches) p[b.organization_id] = JSON.parse(JSON.stringify(b.plan))
        setPlan(p)
        setServerPlan(JSON.parse(JSON.stringify(p)))
      })
      .catch((e) => setErr(String(e.message || e)))
    // Показатели недели грузим отдельно: сетка плана должна открыться сразу,
    // а эти цифры стоят двух обращений в iiko на филиал.
    setMetrics(null)
    fetch(`/api/plan-week/${org}/metrics?week=${wk}&network=${isManager ? 0 : 1}${token ? `&token=${token}` : ''}`,
      { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('metrics'))))
      .then((d: MetricsData) => {
        setMetrics(d)
        // Показываем во весь экран один раз на неделю: второй раз за ту же
        // неделю открытка превратилась бы в баннер, который закрывают не глядя.
        if (!d.branches?.some((b) => b.wins.length)) return
        const key = `lg:celebrated:${d.week}`
        try {
          if (localStorage.getItem(key) === '1') return
          localStorage.setItem(key, '1')
        } catch { /* приватный режим — покажем и не запомним */ }
        setCelebrate(true)
      })
      .catch(() => setMetrics(null))
  }, [org, wk, isManager, token])

  useEffect(load, [load])

  /* Ответ сервера ПРОВЕРЯЕМ. Раньше `await fetch` шёл без проверки `r.ok`:
     при 403 «чужой филиал» или 500 экран всё равно писал «сохранено», план
     оставался только в браузере и пропадал при первой же перезагрузке. На
     вкладке, вся работа которой — вводить план, это худший из возможных багов. */
  const save = async (cells: Cell[]): Promise<boolean> => {
    if (!cells.length) return true
    try {
      const r = await fetch(`/api/plan-week/${org}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ week: wk, cells, token: token || undefined }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaved(`НЕ сохранено: ${d.detail || `ошибка ${r.status}`}`)
        return false
      }
      // Запоминаем, что сервер теперь знает: без этого повторная правка ячейки
      // «обратно как было» считалась бы отсутствием изменений и не уходила.
      setServerPlan((g) => {
        const next = { ...g }
        for (const c of cells) {
          next[c.org] = { ...(next[c.org] || {}) }
          next[c.org][c.dow] = { ...(next[c.org][c.dow] || {}), [c.ch_type]: c.planned }
        }
        return next
      })
      setSaved(`сохранено · ${cells.length > 1 ? `${cells.length} ячеек` : 'ячейка'}`)
      setTimeout(() => setSaved(''), 2000)
      return true
    } catch (e) {
      setSaved(`НЕ сохранено: ${String((e as Error).message || e)}`)
      return false
    }
  }

  const onCell = (o: string, dow: number, t: string, raw: string) => {
    const v = Math.max(0, Math.round(+raw || 0))
    setPlan((g) => ({ ...g, [o]: { ...(g[o] || {}), [dow]: { ...((g[o] || {})[dow] || {}), [t]: v } } }))
  }
  const commit = (o: string, dow: number, t: string) => {
    const v = plan[o]?.[dow]?.[t] ?? 0
    const was = serverPlan[o]?.[dow]?.[t] ?? 0
    if (v !== was) save([{ org: o, dow, ch_type: t, planned: v }])
  }

  /* «Поставить по средней» — то самое, чем план в таблице и рождается:
     средняя неделя × коэффициент. Одним нажатием на всю карточку. */
  const applyBase = async (b: Branch, factor: number) => {
    const cells: Cell[] = []
    const next: Grid = {}
    for (let d = 1; d <= 7; d++) {
      next[d] = {}
      for (const t of TYPES) {
        const v = Math.round((b.base[d]?.[t] ?? 0) * factor)
        next[d][t] = v
        cells.push({ org: b.organization_id, dow: d, ch_type: t, planned: v })
      }
    }
    setPlan((g) => ({ ...g, [b.organization_id]: next }))
    await save(cells)
  }

  /* «По средней» сразу по всем филиалам: в таблице владелец проставляет план
     разом, а не ходит по девяти карточкам. */
  const applyBaseAll = async (factor: number) => {
    if (!data) return
    setBusy(true)
    const cells: Cell[] = []
    const next: Record<string, Grid> = {}
    for (const b of data.branches) {
      next[b.organization_id] = {}
      for (let d = 1; d <= 7; d++) {
        next[b.organization_id][d] = {}
        for (const t of TYPES) {
          const v = Math.round((b.base[d]?.[t] ?? 0) * factor)
          next[b.organization_id][d][t] = v
          cells.push({ org: b.organization_id, dow: d, ch_type: t, planned: v })
        }
      }
    }
    setPlan(next)
    await save(cells)
    setBusy(false)
  }

  /* Перенести план прошлой недели: чаще всего он и есть отправная точка. */
  const copyPrevWeek = async () => {
    if (!data) return
    setBusy(true)
    try {
      const prev = shiftWeek(wk, -1)
      const r = await fetch(`/api/plan-week/${org}?week=${prev}&network=${isManager ? 0 : 1}${token ? `&token=${token}` : ''}`,
        { headers: { Accept: 'application/json' } })
      const d: PlanData = await r.json()
      const { cells, next, moved } = prevWeekCells(d.branches || [], TYPES)
      if (!moved) {
        setSaved('на прошлой неделе плана не было — переносить нечего')
        setTimeout(() => setSaved(''), 4000)
        return
      }
      setPlan((g) => ({ ...g, ...next }))
      if (await save(cells)) {
        setSaved(`перенесено · ${moved} ${moved === 1 ? 'филиал' : 'филиалов'}`)
        setTimeout(() => setSaved(''), 3000)
      }
    } catch (e) { console.warn('copy prev week', e) }
    setBusy(false)
  }

  /* Ходить по столбцу как в таблице: Enter и стрелки переносят в тот же
     столбец следующего дня, а не выкидывают из сетки. */
  const cellId = (o: string, d: number, t: string) => `pc-${o}-${d}-${t}`
  const moveFocus = (o: string, d: number, t: string, step: number) => {
    const nd = d + step
    if (nd < 1 || nd > 7) return
    const el = document.getElementById(cellId(o, nd, t)) as HTMLInputElement | null
    if (el) { el.focus(); el.select() }
  }
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>, o: string, d: number, t: string) => {
    if (e.key === 'Enter' || e.key === 'ArrowDown') {
      e.preventDefault(); (e.target as HTMLInputElement).blur(); moveFocus(o, d, t, 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault(); (e.target as HTMLInputElement).blur(); moveFocus(o, d, t, -1)
    }
  }

  if (err) return <Glass span={12}><div style={{ padding: '10px 2px', fontSize: 13.5, color: 'var(--red)' }}>Не удалось загрузить план: {err}</div></Glass>
  if (!data) return <Glass span={12}><div style={{ padding: '10px 2px', fontSize: 13.5, color: 'var(--mute)' }}>Загружаю план…</div></Glass>
  /* Ни одного филиала — показываем это словами. Раньше здесь падал рендер:
     дальше по коду читается data.network, которого в коротком ответе нет. */
  if (!data.branches?.length || !data.network) return (
    <Glass span={12}>
      <div style={{ padding: '10px 2px', fontSize: 13.5, color: 'var(--mute)' }}>
        Филиалов для плана не нашлось. Если это ваша сеть — проверьте, что филиалы
        подключены к iiko и не помечены как служебные.
      </div>
    </Glass>
  )

  const weekNo = +data.week.split('-W')[1]
  const gv = (g: Grid | undefined, d: number, t: string) => g?.[d]?.[t] ?? 0

  /* Сколько дней недели закрыто — из ответа сервера. Считать это на фронте по
     часам браузера нельзя: у сервера свой часовой пояс, и в понедельник ночью
     две стороны разошлись бы на целый день, а с ними и вся динамика. */
  const doneDays = data.done_days
  const running = data.running

  /* Итоги считаем по ТОМУ, ЧТО СЕЙЧАС В ЯЧЕЙКАХ, а не по ответу сервера.
     Раньше и верхние карточки, и сводка сети брали total_plan из ответа —
     введёшь план, а сверху по-прежнему «план не задан», пока не перезагрузишь
     страницу. Именно это и выглядело как «вкладка работает неправильно». */
  const planTotal = (o: string, t: string, upto = 7) =>
    [1, 2, 3, 4, 5, 6, 7].filter((d) => d <= upto)
      .reduce((sum, d) => sum + gv(plan[o], d, t), 0)
  const netPlanTotal = (t: string, upto = 7) =>
    data.branches.reduce((sum, b) => sum + planTotal(b.organization_id, t, upto), 0)

  /* Факт и прошлая неделя — по ЗАКРЫТЫМ дням, обе стороны по одному правилу.
     Сравнивать факт полутора дней со всей прошлой неделей — врать: во вторник
     любой филиал выглядел бы провалившимся. */
  const daySum = (g: Grid | undefined, t: string, upto = doneDays) =>
    [1, 2, 3, 4, 5, 6, 7].reduce((sum, d) => (d <= upto ? sum + gv(g, d, t) : sum), 0)
  const factOf = (b: Branch, t: string) => daySum(b.fact, t)
  const prevOf = (b: Branch, t: string) => daySum(b.prev, t)
  const sumTypes = (fn: (t: string) => number) => TYPES.reduce((s, t) => s + fn(t), 0)
  const netFact = sumTypes((t) => factOf(data.network!, t))
  const netPrev = sumTypes((t) => prevOf(data.network!, t))
  const netPlanDone = sumTypes((t) => netPlanTotal(t, doneDays))
  const networkFot = data.network!.fot
  const hasFot = !!networkFot && networkFot.unit !== null
  const fotGaps = networkFot?.gaps || []

  function openProgress() {
    setShowProgress(true)
    if (scores.length) return
    if (token) { loadScores(token); return }
    fetch('/api/mountain/links', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('links'))))
      .then((d: { owner?: { link?: string } }) => {
        const t = (d.owner?.link || '').split('/m/')[1]
        if (t) loadScores(t)
      })
      .catch(() => {})
  }

  return (
    <>
      <Glass span={12}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div className="eyebrow">
            <span>План · количество заказов</span>
            <span className="hint">
              {doneDays === 0
                ? 'неделя только началась — закрытых дней ещё нет'
                : running
                  ? `закрыто ${doneDays} ${doneDays === 1 ? 'день' : doneDays < 5 ? 'дня' : 'дней'} из 7 · сравнение с прошлой неделей день в день`
                  : 'неделя закрыта · сравнение с прошлой неделей'}
            </span>
          </div>
          <div className="lg-seg" style={{ marginLeft: 'auto' }}>
            <button className="lg-per" onClick={() => setWk((w) => shiftWeek(w, -1))} aria-label="Пред. неделя"><span style={{ position: 'relative', zIndex: 1 }}>‹</span></button>
            <span style={{ padding: '0 12px', fontSize: 13, fontWeight: 650, color: 'var(--hi)' }}>
              Неделя {weekNo} · {dm(data.from)}–{dm(data.to)}
            </span>
            <button className="lg-per" onClick={() => setWk((w) => shiftWeek(w, 1))} aria-label="След. неделя"><span style={{ position: 'relative', zIndex: 1 }}>›</span></button>
          </div>
        </div>

        {/* Три ответа, с которых начинается собрание: план, динамика и люди. */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12, marginTop: 18 }}>
          <div style={heroStat}>
            <div style={heroLabel}>Выполнение плана</div>
            <div className="tnum" style={heroValue}>{netPlanDone ? `${pct(netFact, netPlanDone)}%` : '—'}</div>
            <div style={{ fontSize: 13.5, color: 'var(--mute)', marginTop: 7 }}>
              <b style={{ color: 'var(--hi)' }}>{nfmt(netFact)}</b> из {netPlanDone ? nfmt(netPlanDone) : '—'} заказов
            </div>
          </div>
          <div style={heroStat}>
            <div style={heroLabel}>К прошлой неделе</div>
            <div className="tnum" style={heroValue}><Delta now={netFact} was={netPrev} /></div>
            <div style={{ fontSize: 13.5, color: 'var(--mute)', marginTop: 7 }}>
              было {nfmt(netPrev)} · те же {doneDays || 0} закрытых дн.
            </div>
          </div>
          <div style={heroStat}>
            <div style={heroLabel}>{networkFot?.unit === 'shift' ? 'ФОТ по графику' : 'ФОТ по явкам'}</div>
            <div className="tnum" style={heroValue}>{hasFot ? `${nfmt(networkFot!.amount)} ₸` : '—'}</div>
            <div style={{ fontSize: 13.5, marginTop: 7,
                          color: !hasFot ? 'var(--mute)' : networkFot!.complete ? 'var(--grn)' : 'var(--amb)' }}>
              {fotCoverage(networkFot)}
            </div>
            {/* Неполный ФОТ бесполезен, если не сказать, ЧЕГО в нём не хватает:
                ставка должности заводится в iiko, и это единственное действие,
                которое превращает эту цифру в настоящую. */}
            {!!fotGaps.length && (
              <div style={{ fontSize: 12.5, marginTop: 5, color: 'var(--mute)', lineHeight: 1.4 }}>
                {fotGapsLine(networkFot)}
              </div>
            )}
          </div>
        </div>

        {/* Каналы вторым уровнем: сначала итог сети, потом разбор состава. */}
        <div style={{ marginTop: 18, fontSize: 13, fontWeight: 700, color: 'var(--hi)' }}>Заказы по каналам</div>
        {/* Сводка недели по каналам — как строка «Факт май, %» в таблице.
            Жёсткие три колонки на телефоне давали карточки по ~90 px: крупное
            число переносилось по слогам. auto-fit — три в ряд на широком, одна
            под другой на узком. */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(165px,1fr))', gap: 12, marginTop: 14 }}>
          {TYPES.map((t) => {
            const p = netPlanTotal(t)
            const pDone = netPlanTotal(t, doneDays)
            const f = factOf(data.network!, t)
            const was = prevOf(data.network!, t)
            return (
              <div key={t} style={{ padding: 14, borderRadius: 14, background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
                <div style={{ fontSize: 13.5, color: 'var(--mute)', fontWeight: 650 }}>{TYPE_LABEL[t]}</div>
                <div className="tnum" style={{
                  fontSize: 'clamp(26px, 3.4vw, 34px)', fontWeight: 720, marginTop: 6,
                  lineHeight: 1.05, letterSpacing: '-0.025em', color: 'var(--hi)',
                }}>
                  {nfmt(f)} <span style={{ fontSize: 13, fontWeight: 500, letterSpacing: 0, color: 'var(--mute)' }}>
                    {running ? 'заказов за закрытые дни' : 'заказов за неделю'}
                  </span>
                </div>
                {/* Динамика к прошлой неделе — то, ради чего вкладку и открывают
                    во вторник: не «сколько», а «лучше или хуже». */}
                <div style={{ marginTop: 4, fontSize: 12.5, display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <Delta now={f} was={was} />
                  <span className="tnum" style={{ color: 'var(--mute)', fontSize: 11.5 }}>
                    было {nfmt(was)}
                  </span>
                </div>
                <div className="tnum" style={{ fontSize: 12, fontWeight: 600, color: pDone ? pctColor(pct(f, pDone)) : 'var(--mute)', marginTop: 3 }}>
                  {pDone ? `${pct(f, pDone)}% плана` : (p ? 'план на закрытые дни не задан' : 'план не задан')}
                </div>
                {!!p && (
                  <div className="tnum" style={{ fontSize: 11, color: 'var(--mute)', marginTop: 2 }}>
                    план на неделю {nfmt(p)}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        {/* Действия над всей неделей — то, чем таблица и удобна: план ставится
            разом, а не по одной ячейке в девяти карточках. */}
        <div style={{ marginTop: 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button style={actBtn} disabled={busy} onClick={copyPrevWeek}>Перенести прошлую неделю</button>
          <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>по средней за {data.base_weeks} нед.:</span>
          {[1, 1.05, 1.1].map((f) => (
            <button key={f} style={actBtn} disabled={busy}
              onClick={() => applyBaseAll(f)}>{f === 1 ? 'как есть' : `+${Math.round((f - 1) * 100)}%`}</button>
          ))}
          {data.branches.length > 1 && (
            <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>— на все {data.branches.length} филиалов</span>
          )}
          <span style={{ marginLeft: 'auto', fontSize: 11.5, fontWeight: 650,
                         color: busy ? 'var(--amb)' : saved.startsWith('НЕ ') ? 'var(--red)' : 'var(--grn)' }}>
            {busy ? 'сохраняю…' : saved}
          </span>
        </div>
        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--mute)', lineHeight: 1.5 }}>
          {isManager ? (
            <>Ниже карточка твоего филиала: в белых полях — <b>план заказов</b> на день,
              под ними серым — <b>факт из кассы</b>. Проценты считаются сами.
              Enter и стрелки ↑↓ переносят на тот же столбец соседнего дня.</>
          ) : (
            <>Факт заказов приходит из iiko. Считается по <b>закрытым дням</b> недели и
              сравнивается с теми же днями прошлой недели — поэтому во вторник видно
              не «провал», а честную динамику. План ставится кнопками выше сразу на
              все филиалы; по дням его правит управляющий у себя.</>
          )}
        </div>
        {data.demo && <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--amb)' }}>демо-данные</div>}
      </Glass>

      <AnimatePresence>
        {celebrate && metrics && (
          <Celebration key="celebration" week={metrics.week}
            rows={metrics.branches.filter((b) => b.wins.length)}
            onClose={() => setCelebrate(false)} />
        )}
      </AnimatePresence>
      {metrics && <Congrats rows={metrics.branches} onOpen={() => setCelebrate(true)} />}

      {/* Экран собрания. Кнопка стоит рядом с поздравлением: там владелец и
          начинает разговор о неделе. Управляющему не показываем — у него один
          филиал, и «топ» из одной строки смысла не имеет. */}
      {/* isManager === false, а не !isManager: роль трёхзначная, и пока она
          неизвестна (null) кнопка успевала мигнуть у управляющего. */}
      {metrics && isManager === false && metrics.branches.length > 1 && (
        <Glass span={12}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 16, fontWeight: 750, color: 'var(--hi)' }}>Прогресс и динамика</div>
              <div style={{ marginTop: 4, fontSize: 13.5, lineHeight: 1.45, color: 'var(--mute)' }}>
                План и факт по филиалам, графики недели, что улучшилось и что просело.
                Во весь экран — для собрания.
              </div>
            </div>
            <button onClick={openProgress}
              style={{ marginLeft: 'auto', padding: '11px 18px', borderRadius: 14, border: 'none',
                       background: 'linear-gradient(135deg,var(--acc),var(--acc-2))',
                       color: 'var(--on-acc)', fontSize: 13.5, fontWeight: 700,
                       fontFamily: 'inherit', cursor: 'pointer' }}>
              Открыть отчёт
            </button>
          </div>
        </Glass>
      )}

      <AnimatePresence>
        {showProgress && metrics && (
          <Suspense fallback={null}>
            {/* network здесь уже точно есть: выше стоит ранний выход, если его нет */}
            <Progress key="progress" data={metrics} scores={scores}
              plan={data as typeof data & { network: NonNullable<typeof data.network> }}
              onClose={() => setShowProgress(false)} />
          </Suspense>
        )}
      </AnimatePresence>
      {/* Топ прибавивших — сразу под поздравлением: на собрании это первое,
          что называют вслух. Одному филиалу топ не нужен: он в нём и первый,
          и последний. */}
      {metrics && metrics.branches.length > 1 && (
        <Improved data={metrics} running={running} doneDays={doneDays} />
      )}
      {metrics && <WeekMetrics data={metrics} />}

      {/* Сводка сети: филиал × канал с процентом выполнения, прошлая неделя и
          динамика. Владельцу показываем ВСЕГДА — карточек по дням у него больше
          нет, и с одним филиалом вкладка иначе оказалась бы пустой. */}
      {(!isManager || data.branches.length > 1) && (
        <Glass span={12}>
          <div className="eyebrow">
            <span>Сводка сети</span>
            <span className="hint">
              неделя {weekNo}{running && doneDays > 0 ? ` · ${doneDays} закр. ${doneDays === 1 ? 'день' : 'дн.'}` : ''}
              {' '}· против недели {weekNo - 1} по тем же дням
            </span>
          </div>
          <div style={{ overflowX: 'auto', marginTop: 12 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1080, tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: '19%' }} />
                {TYPES.map((t) => <col key={t} style={{ width: '9%' }} />)}
                <col style={{ width: '11%' }} />
                <col style={{ width: '10%' }} />
                <col style={{ width: '10%' }} />
                <col style={{ width: '14%' }} />
              </colgroup>
              <thead><tr>
                <th style={thL}>Филиал</th>
                {TYPES.map((t) => <th key={t} style={thC}>{TYPE_LABEL[t]}</th>)}
                <th style={thC}>Всего</th>
                <th style={thC}>Нед. {weekNo - 1}</th>
                <th style={thC}>Динамика</th>
                <th style={thC}>ФОТ</th>
              </tr></thead>
              <tbody>
                {/* Сортируем по проценту выполнения: на собрании первый вопрос —
                    «кто отстаёт», и он не должен требовать чтения всей таблицы. */}
                {/* Сортируем по ДИНАМИКЕ, а не по проценту плана: на собрании
                    первый вопрос — «кто просел против прошлой недели», и план
                    у филиалов бывает не задан, а прошлая неделя есть всегда.
                    Филиалы без прошлой недели уходят вниз, а не наверх. */}
                {[...[...data.branches].sort((x, y) => {
                  const dyn = (bb: Branch) => {
                    const was = sumTypes((t) => prevOf(bb, t))
                    if (!was) return -Infinity
                    return sumTypes((t) => factOf(bb, t)) / was
                  }
                  return dyn(y) - dyn(x)
                }), ...(data.branches.length > 1 ? [data.network!] : [])].map((b, i) => {
                  const isNet = i === data.branches.length
                  const bp = (t: string) => (isNet ? netPlanTotal(t, doneDays) : planTotal(b.organization_id, t, doneDays))
                  const tp = sumTypes(bp)
                  const tf = sumTypes((t) => factOf(b, t))
                  const tw = sumTypes((t) => prevOf(b, t))
                  return (
                    <tr key={b.organization_id || 'net'}>
                      <td style={{ ...td, fontWeight: isNet ? 800 : 650, color: 'var(--hi)', whiteSpace: 'nowrap' }}>{b.name}</td>
                      {TYPES.map((t) => {
                        const p = bp(t), f = factOf(b, t)
                        return (
                          <td key={t} style={{ ...td, textAlign: 'center' }}>
                            <span className="tnum" style={{ color: 'var(--hi)', fontWeight: 650 }}>{nfmt(f)}</span>
                            <span className="tnum" style={{ color: 'var(--mute)', fontSize: 11.5 }}> / {p ? nfmt(p) : '—'}</span>
                            {!!p && <div className="tnum" style={{ fontSize: 11.5, fontWeight: 700, color: pctColor(pct(f, p)) }}>{pct(f, p)}%</div>}
                          </td>
                        )
                      })}
                      <td style={{ ...td, textAlign: 'center' }}>
                        <span className="tnum" style={{ color: 'var(--hi)', fontWeight: 750 }}>{nfmt(tf)}</span>
                        <span className="tnum" style={{ color: 'var(--mute)', fontSize: 11.5 }}> / {tp ? nfmt(tp) : '—'}</span>
                        {!!tp && <div className="tnum" style={{ fontSize: 11.5, fontWeight: 700, color: pctColor(pct(tf, tp)) }}>{pct(tf, tp)}%</div>}
                      </td>
                      <td style={{ ...td, textAlign: 'center' }}>
                        <span className="tnum" style={{ color: 'var(--lo)', fontWeight: 650 }}>{tw ? nfmt(tw) : '—'}</span>
                      </td>
                      <td style={{ ...td, textAlign: 'center', fontSize: 12.5 }}>
                        <Delta now={tf} was={tw} />
                      </td>
                      <td style={{ ...td, textAlign: 'right' }}>
                        <span className="tnum" style={{ color: 'var(--hi)', fontWeight: 750 }}>
                          {b.fot && b.fot.unit ? `${nfmt(b.fot.amount)} ₸` : '—'}
                        </span>
                        {b.fot && b.fot.unit && !b.fot.complete && (
                          <div className="tnum" style={{ marginTop: 2, fontSize: 11.5, color: 'var(--amb)' }}>
                            {nfmt(b.fot.priced)}/{nfmt(b.fot.total)} {b.fot.unit === 'shift' ? 'смен' : 'ч'}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Glass>
      )}

      {/* Карточка с днями недели — ТОЛЬКО управляющему, по его филиалу.
          Владельцу девять таких карточек по семь дней не нужны: он смотрит
          итоги недели и динамику, а план ставит кнопками «как есть / +5% /
          +10%» и «перенести прошлую неделю» — они бьют по всем филиалам разом.
          По дням правит тот, кто на смене. */}
      {isManager && data.branches.map((b) => (
        <Glass span={12} key={b.organization_id}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <div className="eyebrow"><span>Неделя {weekNo} · {b.name}</span></div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>поставить по средней за {data.base_weeks} нед.:</span>
              {[1, 1.05, 1.1].map((f) => (
                <button key={f} onClick={() => applyBase(b, f)} style={baseBtn}>
                  {f === 1 ? 'как есть' : `+${Math.round((f - 1) * 100)}%`}
                </button>
              ))}
            </div>
          </div>

          {b.plan_auto && (
            /* План теперь ставится сам: без него у филиала нет ни процентов
               выполнения, ни критерия «выполнение плана» в оценке, а нажать
               кнопку легко забыть. Говорим об этом прямо — иначе непонятно,
               откуда взялись цифры, и «плановые» числа выглядят спущенными
               сверху, хотя это собственная средняя филиала. */
            <div style={{ marginTop: 10, padding: '9px 12px', borderRadius: 11, fontSize: 12.5,
              color: 'var(--mute)', background: 'var(--glass-2)' }}>
              План поставлен автоматически — по средней неделе за {data.base_weeks} недель.
              Правь руками или подними кнопкой «+5% / +10%» справа сверху.
            </div>
          )}
          {!b.plan_auto && TYPES.every((t) => planTotal(b.organization_id, t) === 0) && (
            <div style={{ marginTop: 10, padding: '9px 12px', borderRadius: 11, fontSize: 12.5,
              color: 'var(--amb)', background: 'color-mix(in srgb, var(--amb) 10%, transparent)' }}>
              План на эту неделю стоит нулём — ни процентов выполнения, ни критерия
              «выполнение плана» в оценке у филиала не будет. Нажми «как есть» справа
              сверху: подставит среднюю неделю за {data.base_weeks} недель.
            </div>
          )}
          {/* table-layout: fixed — ширины колонок заданы заранее и НЕ зависят от
              содержимого. С auto (по умолчанию) нажатие «как есть» заполняло все
              ячейки разом, колонки пересчитывались, и карточка на глазах
              перекладывалась — то самое «разбивается». */}
          <div style={{ overflowX: 'auto', marginTop: 12 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640, tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: 132 }} />
                {TYPES.map((t) => <col key={t} style={{ width: `calc((100% - 132px) / ${TYPES.length})` }} />)}
              </colgroup>
              <thead><tr>
                <th style={thL}>День</th>
                {TYPES.map((t) => <th key={t} style={thC}>{TYPE_LABEL[t]}</th>)}
              </tr></thead>
              <tbody>
                {DOW.map((dname, i) => {
                  const d = i + 1
                  return (
                    <tr key={d}>
                      <td style={{ ...td, whiteSpace: 'nowrap' }}>
                        <b style={{ color: 'var(--hi)' }}>{dname}</b>
                        <div style={{ fontSize: 10.5, color: 'var(--mute)' }}>{DOW_SHORT[i]}</div>
                      </td>
                      {TYPES.map((t) => {
                        const pv = gv(plan[b.organization_id], d, t)
                        const fv = gv(b.fact, d, t)
                        return (
                          <td key={t} style={{ ...td, textAlign: 'center' }}>
                            <div style={lbl}>план</div>
                            <input type="number" min={0} value={pv || ''} placeholder="0"
                              id={cellId(b.organization_id, d, t)}
                              onChange={(e) => onCell(b.organization_id, d, t, e.target.value)}
                              onBlur={() => commit(b.organization_id, d, t)}
                              onKeyDown={(e) => onKey(e, b.organization_id, d, t)}
                              style={inp} />
                            {/* День, который ещё не наступил, показывает «—», а не
                                «факт: 0 · 0%»: красный ноль на пятницу в среду —
                                ложная тревога. Сегодняшний помечаем «идёт». */}
                            <div className="tnum" style={{ fontSize: 12, marginTop: 5, color: 'var(--lo)' }}>
                              {d > doneDays + (data.live_today && running ? 1 : 0) && running ? (
                                <span style={{ color: 'var(--mute)' }}>—</span>
                              ) : (
                                <>
                                  факт: <b style={{ color: 'var(--hi)' }}>{fv}</b>
                                  {d === doneDays + 1 && running
                                    ? <span style={{ color: 'var(--mute)' }}> · идёт</span>
                                    : !!pv && <span style={{ color: pctColor(pct(fv, pv)) }}> · {pct(fv, pv)}%</span>}
                                </>
                              )}
                            </div>
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
                <tr>
                  <td style={{ ...td, fontWeight: 800, color: 'var(--hi)' }}>
                    Итого
                    <div style={{ fontSize: 10.5, fontWeight: 500, color: 'var(--mute)' }}>
                      {running ? `за ${doneDays} закр. ${doneDays === 1 ? 'день' : 'дн.'}` : 'за неделю'}
                    </div>
                  </td>
                  {TYPES.map((t) => {
                    /* Процент — по ЗАКРЫТЫМ дням, как и везде на вкладке.
                       Делить факт двух дней на план всей недели — это «28%» в
                       среду и разговор о провале там, где филиал идёт с
                       опережением. Незакрытый сегодняшний день в процент тоже
                       не берём: он стоит своей строкой с пометкой «идёт».
                       Недельный план — рядом справочно. */
                    const pDone = planTotal(b.organization_id, t, doneDays)
                    const pWeek = planTotal(b.organization_id, t)
                    const f = daySum(b.fact, t, doneDays)
                    return (
                      <td key={t} style={{ ...td, textAlign: 'center' }}>
                        <div className="tnum" style={{ fontSize: 15, fontWeight: 750, color: 'var(--hi)' }}>{nfmt(f)}</div>
                        <div className="tnum" style={{ fontSize: 11.5, color: pDone ? pctColor(pct(f, pDone)) : 'var(--mute)', fontWeight: 650 }}>
                          {pDone ? `${pct(f, pDone)}% плана` : (pWeek ? 'план на закрытые дни не задан' : 'план не задан')}
                        </div>
                        <div className="tnum" style={{ fontSize: 10.5, color: 'var(--mute)', marginTop: 2 }}>
                          {pWeek ? `неделя ${nfmt(pWeek)} · ` : ''}прошл. нед. {nfmt(daySum(b.prev, t, doneDays))}
                        </div>
                        <div style={{ fontSize: 10.5, marginTop: 2 }}>
                          <Delta now={f} was={daySum(b.prev, t, doneDays)} />
                        </div>
                      </td>
                    )
                  })}
                </tr>
              </tbody>
            </table>
          </div>
        </Glass>
      ))}
    </>
  )
}

const thL: CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 10px', borderBottom: '1px solid var(--hair)' }
const thC: CSSProperties = { ...thL, textAlign: 'center' }
const td: CSSProperties = {
  padding: '11px 8px', borderBottom: '1px solid var(--hair-2)', fontSize: 13,
  verticalAlign: 'top', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums',
}
const lbl: CSSProperties = { fontSize: 9, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--mute)', marginBottom: 3 }
const inp: CSSProperties = { width: '100%', maxWidth: 92, fontSize: 15, fontWeight: 650, textAlign: 'center', padding: '6px 8px', borderRadius: 10, border: '1px solid var(--hair)', background: 'var(--glass-2)', color: 'var(--hi)', fontVariantNumeric: 'tabular-nums', fontFamily: 'inherit' }
const heroStat: CSSProperties = { padding: '18px 19px', borderRadius: 17, background: 'var(--glass-2)', border: '1px solid var(--hair)', minWidth: 0 }
const heroLabel: CSSProperties = { fontSize: 13, color: 'var(--mute)', fontWeight: 650 }
const heroValue: CSSProperties = { marginTop: 8, fontSize: 'clamp(28px,3.5vw,38px)', fontWeight: 780, letterSpacing: '-.035em', lineHeight: 1, color: 'var(--hi)' }
const actBtn: CSSProperties = { fontSize: 12, fontWeight: 650, color: 'var(--hi)', background: 'var(--glass-2)', border: '1px solid var(--hair)', borderRadius: 9, padding: '6px 11px', cursor: 'pointer', fontFamily: 'inherit' }
const baseBtn: CSSProperties = { fontSize: 12, fontWeight: 650, color: 'var(--acc)', background: 'transparent', border: '1px solid var(--hair)', borderRadius: 9, padding: '5px 10px', cursor: 'pointer', fontFamily: 'inherit' }
