import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { fotCoverage, fotGapsLine, planPct } from './planWeek'
import type { Fot } from './planWeek'
import {
  Bar, BarChart, CartesianGrid, Cell as RCell, ComposedChart, Line,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

/* «Прогресс и динамика» — экран для собрания, во весь экран.

   Порядок разговора владельца, сверху вниз:
   1. сеть в цифрах — сколько заказов против плана и куда двинулось;
   2. план и факт по дням — где неделя провисает;
   3. выполнение плана по филиалам — кто тянет, кто нет;
   4. неделя к неделе по любому показателю — что улучшилось, что ухудшилось;
   5. поимённо: у кого много опоздавших заказов и опаздывающих сотрудников;
   6. таблица по филиалам целиком — то, к чему переходят после общих цифр.

   Считаем ровно то, что уже посчитал сервер: /api/plan-week (план, факт,
   прошлая неделя) и /api/plan-week/metrics (кухня, дорога, опоздания заказов
   и сотрудников, улучшения и просадки). Своей арифметики на фронте тем меньше,
   чем лучше: та же формула на двух сторонах расходится молча.

   Подача по apple-design:
   • появление пружиной без отскока — экран не жестовый, подпрыгивание было бы шумом;
   • у крупных чисел отрицательный трекинг и плотный интерлиньяж, цифры табличные,
     иначе на собрании колонки «пляшут» при обновлении;
   • цвет несёт ровно один смысл — стало лучше или хуже;
   • prefers-reduced-motion уважаем: движение заменяем проявлением. */

export type Win = { key: string; label: string; unit?: string; from?: number; to?: number; pct?: number | null }
export type MetricDef = { key: string; label: string; unit: string; lower_better: boolean }
export type Row = {
  organization_id: string; name: string
  now: Record<string, number | null>; prev: Record<string, number | null>
  wins: Win[]; losses?: Win[]
  /* из-за чего опаздывали заказы: сервер сравнивает каждый этап с нормой
     ЭТОГО ЖЕ филиала, поэтому «долго» значит «дольше, чем у них обычно» */
  late_cause?: { key: string; label: string; count: number } | null
  late_causes?: { key: string; label: string; count: number }[]
  median_delay?: number | null
  late_orders?: number | null
  /* причина считается за НЕДЕЛЮ до собрания, а не за закрытые дни ISO-недели */
  cause_period?: { from: string; to: string } | null
  /* счётчики явок за неделю: «12% опоздавших» на собрании сразу спрашивают
     «это сколько человек?». configured=false — филиал не задал старт смены,
     и доли опоздавших у него просто нет. */
  staff?: { total?: number; late?: number; late_pct?: number; late_minutes?: number
            configured?: boolean; starts?: string[] } | null
}
export type MetricsData = { week: string; from: string; to: string; done_days?: number
                            metrics: MetricDef[]; branches: Row[] }
export type ScoreRow = { organization_id: string; name: string; score?: number | null; points?: number }

type Grid = Record<string, Record<string, number>>
export type PlanBranch = {
  organization_id: string; name: string
  plan: Grid; fact: Grid; prev: Grid
  fot?: Fot
}
export type PlanWeek = {
  types: string[]; branches: PlanBranch[]; network: PlanBranch; done_days: number
}

/* Показатели верхнего ряда — в порядке разговора: сперва объём, потом качество
   доставки, потом где теряем минуты, потом люди. */
const SHOW = ['orders', 'late_pct', 'kitchen', 'road', 'staff_late_pct'] as const
/* Что можно поставить на график «неделя к неделе» */
const COMPARE = ['orders', 'late_pct', 'kitchen', 'road', 'staff_late_pct'] as const
const DOW = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

const shortName = (n: string) => n.replace(/^Астана,?\s*(ул\.?)?\s*/i, '').trim() || n
const fmt = (v: number | null | undefined, unit: string) =>
  v == null ? '—' : `${Math.round(v * 10) / 10}${unit ? ` ${unit}` : ''}`
const num = (v: number) => v.toLocaleString('ru-RU')

export type { Fot, FotGap } from './planWeek'

const dm = (d: string) => `${d.slice(8, 10)}.${d.slice(5, 7)}`

/** Сводка сети. Проценты считаем из общего числителя и знаменателя, а время
    взвешиваем числом доставок. Простое среднее процентов филиалов занижало
    сеть: маленькая точка влияла так же сильно, как филиал со 125 доставками. */
export function networkValue(rows: Row[], key: string, side: 'now' | 'prev'): number | null {
  const weighted = (countKey: string, totalKey: string) => {
    const pairs = rows.map((r) => [r[side]?.[countKey], r[side]?.[totalKey]] as const)
      .filter((p): p is readonly [number, number] => typeof p[0] === 'number' && typeof p[1] === 'number')
    const total = pairs.reduce((s, p) => s + p[1], 0)
    return total ? pairs.reduce((s, p) => s + p[0], 0) / total * 100 : null
  }
  if (key === 'late_pct') return weighted('late_orders_count', 'deliveries')
  if (key === 'staff_late_pct') return weighted('staff_late_count', 'staff_total_count')
  const vals = rows.map((r) => r[side]?.[key]).filter((v): v is number => typeof v === 'number')
  if (!vals.length) return null
  if (key === 'orders') return vals.reduce((a, b) => a + b, 0)
  if (['kitchen', 'road', 'idle'].includes(key)) {
    const pairs = rows.map((r) => [r[side]?.[key], r[side]?.deliveries] as const)
      .filter((p): p is readonly [number, number] => typeof p[0] === 'number' && typeof p[1] === 'number' && p[1] > 0)
    const total = pairs.reduce((s, p) => s + p[1], 0)
    if (total) return pairs.reduce((s, p) => s + p[0] * p[1], 0) / total
  }
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

/** Насколько стало лучше. Знак нормализуем: у «меньше — лучше» падение это плюс. */
export function delta(now: number | null, prev: number | null, lowerBetter: boolean) {
  if (now == null || prev == null || prev === 0) return null
  const diff = now - prev
  const pct = (diff / Math.abs(prev)) * 100
  return { diff, pct, better: lowerBetter ? diff < 0 : diff > 0 }
}

/** Сумма плана/факта по ЗАКРЫТЫМ дням недели. Считать план за все семь дней
    против факта трёх — показать провал там, где его нет. */
export function weekSum(g: Grid | undefined, types: string[], upto: number): number {
  if (!g) return 0
  let s = 0
  for (let d = 1; d <= Math.min(7, upto); d++) for (const t of types) s += Number(g[String(d)]?.[t] || 0)
  return s
}

/* Цвета для recharts. Токены темы живут в CSS-переменных, а recharts кладёт
   заливку АТРИБУТОМ svg — var(--acc) там не раскрывается. Поэтому читаем
   вычисленные значения и перечитываем их при смене темы. */
function useTokens() {
  const read = () => {
    if (typeof window === 'undefined') return { acc: '#2f6bff', grn: '#1faa53', amb: '#e08600', red: '#ff3b30', mute: '#8a90a2', hair: '#e6e8ef' }
    const cs = getComputedStyle(document.documentElement)
    const g = (n: string, f: string) => cs.getPropertyValue(n).trim() || f
    return {
      acc: g('--acc', '#2f6bff'), grn: g('--grn', '#1faa53'), amb: g('--amb', '#e08600'),
      red: g('--red', '#ff3b30'), mute: g('--mute', '#8a90a2'), hair: g('--hair', '#e6e8ef'),
    }
  }
  const [tok, setTok] = useState(read)
  useEffect(() => {
    const o = new MutationObserver(() => setTok(read()))
    o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => o.disconnect()
  }, [])
  return tok
}

function Delta({ d, unit }: { d: ReturnType<typeof delta>; unit: string }) {
  if (!d || Math.abs(d.pct) < 0.5) {
    return <span style={{ fontSize: 12.5, color: 'var(--mute)', fontWeight: 600 }}>без изменений</span>
  }
  const col = d.better ? 'var(--grn)' : 'var(--red)'
  return (
    <span className="tnum" style={{ fontSize: 12.5, fontWeight: 700, color: col, letterSpacing: '.01em' }}>
      {d.diff > 0 ? '↑' : '↓'} {Math.abs(Math.round(d.diff * 10) / 10)}{unit ? ` ${unit}` : ''}
      <span style={{ color: 'var(--mute)', fontWeight: 600 }}> · {Math.abs(Math.round(d.pct))}%</span>
    </span>
  )
}

const panel: React.CSSProperties = {
  padding: '16px 18px', borderRadius: 18, border: '1px solid var(--hair)',
  background: 'var(--card)', minWidth: 0,
}
const eyebrow: React.CSSProperties = {
  fontSize: 10.5, letterSpacing: '.1em', textTransform: 'uppercase',
  color: 'var(--mute)', fontWeight: 700,
}

function Section({ title, hint, children, style }: {
  title: string; hint?: string; children: React.ReactNode; style?: React.CSSProperties
}) {
  return (
    <div style={{ ...panel, ...style }}>
      <div style={eyebrow}>{title}</div>
      {hint && <div style={{ marginTop: 2, fontSize: 11.5, color: 'var(--mute)' }}>{hint}</div>}
      {children}
    </div>
  )
}

/* Карточка показателя. Иерархия: число крупное и плотное, подпись мелкая и
   разреженная — глаз ловит цифру первой, читает подпись второй. */
function Card({ def, rows, i, reduce, foot }: {
  def: MetricDef; rows: Row[]; i: number; reduce: boolean; foot?: React.ReactNode
}) {
  const now = networkValue(rows, def.key, 'now')
  const prev = networkValue(rows, def.key, 'prev')
  const d = delta(now, prev, def.lower_better)
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
      transition={{ type: 'spring', bounce: 0, duration: 0.4, delay: i * 0.05 }}
      style={panel}>
      <div style={eyebrow}>{def.label}</div>
      <div className="tnum" style={{ marginTop: 8, fontSize: 34, fontWeight: 780,
                                     letterSpacing: '-.03em', lineHeight: 1, color: 'var(--hi)' }}>
        {fmt(now, def.unit)}
      </div>
      <div style={{ marginTop: 7 }}><Delta d={d} unit={def.unit} /></div>
      <div style={{ marginTop: 3, fontSize: 11, color: 'var(--mute)' }}>
        неделей раньше {fmt(prev, def.unit)}
      </div>
      {foot}
    </motion.div>
  )
}

/* Топ. Две колонки одного размера: слева движение за неделю, справа оценка.
   Разделять их на «главный» и «второстепенный» нельзя — на собрании называют оба. */
function Top({ title, hint, items, reduce }: {
  title: string; hint: string; reduce: boolean
  items: { id: string; name: string; value: string; note?: string; tone?: string }[]
}) {
  return (
    <div style={panel}>
      <div style={eyebrow}>{title}</div>
      <div style={{ marginTop: 2, fontSize: 11.5, color: 'var(--mute)' }}>{hint}</div>
      <div style={{ marginTop: 12, display: 'grid', gap: 2, maxHeight: 340, overflowY: 'auto' }}>
        {items.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--mute)' }}>Пока нечего показать.</div>}
        {items.map((it, i) => (
          <motion.div key={it.id}
            initial={reduce ? { opacity: 0 } : { opacity: 0, x: -6 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0 }}
            transition={{ type: 'spring', bounce: 0, duration: 0.4, delay: 0.1 + i * 0.04 }}
            style={{ display: 'flex', alignItems: 'baseline', gap: 11, padding: '8px 0',
                     borderBottom: i < items.length - 1 ? '1px solid var(--hair-2)' : 'none' }}>
            <span className="tnum" style={{ width: 20, textAlign: 'center', flex: 'none',
                                            fontSize: i === 0 ? 14 : 12, fontWeight: 800,
                                            color: i === 0 ? 'var(--amb)' : 'var(--mute)' }}>
              {i === 0 ? '★' : i + 1}
            </span>
            <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, color: 'var(--hi)',
                           fontWeight: i === 0 ? 700 : 500, overflow: 'hidden',
                           textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {shortName(it.name)}
              {it.note && <span style={{ color: 'var(--mute)', fontWeight: 500 }}> · {it.note}</span>}
            </span>
            <span className="tnum" style={{ flex: 'none', fontSize: 13.5, fontWeight: 700,
                                            color: it.tone || 'var(--hi)' }}>{it.value}</span>
          </motion.div>
        ))}
      </div>
    </div>
  )
}

/* Список движений: филиал, показатель, было → стало. Одна строка — один факт,
   который можно назвать вслух и не сверяться с таблицей. */
function Moves({ title, hint, items, tone }: {
  title: string; hint: string; tone: string
  items: { org: string; name: string; w: Win }[]
}) {
  return (
    <div style={panel}>
      <div style={eyebrow}>{title}</div>
      <div style={{ marginTop: 2, fontSize: 11.5, color: 'var(--mute)' }}>{hint}</div>
      <div style={{ marginTop: 11, display: 'grid', gap: 7, maxHeight: 340, overflowY: 'auto' }}>
        {!items.length && <div style={{ fontSize: 12.5, color: 'var(--mute)' }}>Ничего не сдвинулось.</div>}
        {items.map(({ org, name, w }) => (
          <div key={org + w.key} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 12.5 }}>
            <span style={{ flex: 1, minWidth: 0, color: 'var(--hi)', fontWeight: 650,
                           overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {shortName(name)}
              <span style={{ color: 'var(--mute)', fontWeight: 500 }}> · {w.label}</span>
            </span>
            <span className="tnum" style={{ flex: 'none', color: 'var(--mute)' }}>
              {fmt(w.from ?? null, '')} → <b style={{ color: tone }}>{fmt(w.to ?? null, w.unit || '')}</b>
            </span>
            {w.pct != null && (
              <span className="tnum" style={{ flex: 'none', width: 44, textAlign: 'right', fontWeight: 700, color: tone }}>
                {w.pct}%
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Progress({ data, scores, plan, onClose }: {
  data: MetricsData; scores: ScoreRow[]; plan?: PlanWeek | null; onClose: () => void
}) {
  const reduce = typeof window !== 'undefined'
    && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const closeRef = useRef<HTMLButtonElement>(null)
  const tok = useTokens()
  const [cmp, setCmp] = useState<string>('late_pct')

  // Выход всегда доступен: экран не должен запирать. Esc — и мы снаружи.
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    closeRef.current?.focus()
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  const defs = useMemo(
    () => SHOW.map((k) => data.metrics.find((m) => m.key === k)).filter((m): m is MetricDef => !!m),
    [data.metrics])
  const rows = data.branches
  const byKey = useMemo(() => Object.fromEntries(data.metrics.map((m) => [m.key, m])), [data.metrics])
  const done = plan?.done_days ?? data.done_days ?? 0
  const types = plan?.types || []

  /* План и факт по сети: считаем по закрытым дням, иначе выполнение плана
     во вторник читалось бы как провал. */
  const totals = useMemo(() => {
    if (!plan || !done) return null
    const p = weekSum(plan.network?.plan, types, done)
    const f = weekSum(plan.network?.fact, types, done)
    return { plan: p, fact: f, pct: p ? planPct(f, p) : null }
  }, [plan, types, done])

  /* График «план и факт по дням»: факт только по закрытым дням — рисовать ноль
     за сегодня значит показать обвал, которого нет. */
  const byDay = useMemo(() => {
    if (!plan) return []
    return DOW.map((d, i) => {
      const k = String(i + 1)
      const sum = (g: Grid | undefined) => types.reduce((s, t) => s + Number(g?.[k]?.[t] || 0), 0)
      return {
        d,
        plan: sum(plan.network?.plan),
        fact: i < done ? sum(plan.network?.fact) : null,
        prev: sum(plan.network?.prev),
      }
    })
  }, [plan, types, done])

  /* Выполнение плана по филиалам — то, ради чего вкладка «План» и заводилась */
  const byBranchPlan = useMemo(() => {
    if (!plan || !done) return []
    return plan.branches.map((b) => {
      const p = weekSum(b.plan, types, done)
      const f = weekSum(b.fact, types, done)
      return { id: b.organization_id, name: shortName(b.name), plan: p, fact: f, fot: b.fot,
               pct: p ? planPct(f, p) : null }
    }).filter((b) => b.pct != null).sort((a, b) => (b.pct || 0) - (a.pct || 0))
  }, [plan, types, done])

  /* Неделя к неделе по выбранному показателю */
  /* Период причины один на всю сеть — берём из первой строки, где он есть. */
  const causePeriod = useMemo(() => rows.find((r) => r.cause_period)?.cause_period || null, [rows])
  const cmpDef = byKey[cmp] as MetricDef | undefined
  const cmpData = useMemo(() => rows
    .map((r) => ({ name: shortName(r.name),
                   now: typeof r.now?.[cmp] === 'number' ? (r.now[cmp] as number) : null,
                   prev: typeof r.prev?.[cmp] === 'number' ? (r.prev[cmp] as number) : null }))
    .filter((r) => r.now != null || r.prev != null)
    .sort((a, b) => (b.now ?? 0) - (a.now ?? 0)), [rows, cmp])

  const moved = useMemo(() => rows.filter((r) => r.wins.length)
    .sort((a, b) => b.wins.length - a.wins.length)
    .map((r) => ({ id: r.organization_id, name: r.name,
                   value: `+${r.wins.length}`, tone: 'var(--grn)' })), [rows])

  const byScore = useMemo(() => scores
    .filter((s) => typeof s.score === 'number')
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .map((s) => ({ id: s.organization_id, name: s.name, value: String(s.score),
                   tone: (s.score || 0) >= 70 ? 'var(--grn)' : (s.score || 0) >= 45 ? 'var(--amb)' : 'var(--red)' })),
    [scores])

  const wins = useMemo(() => rows.flatMap((r) => (r.wins || [])
    .map((w) => ({ org: r.organization_id, name: r.name, w })))
    .sort((a, b) => (b.w.pct || 0) - (a.w.pct || 0)), [rows])
  const losses = useMemo(() => rows.flatMap((r) => (r.losses || [])
    .map((w) => ({ org: r.organization_id, name: r.name, w })))
    .sort((a, b) => (b.w.pct || 0) - (a.w.pct || 0)), [rows])

  /* Кому внимание: не «плохие филиалы», а конкретные два вопроса собрания. */
  const lateOrders = useMemo(() => rows
    .filter((r) => typeof r.now?.late_pct === 'number')
    .sort((a, b) => (b.now.late_pct as number) - (a.now.late_pct as number))
    .map((r) => ({ id: r.organization_id, name: r.name,
                   value: `${Math.round((r.now.late_pct as number) * 10) / 10}%`,
                   note: r.late_cause ? r.late_cause.label.toLowerCase()
                         : typeof r.now?.deliveries === 'number' ? `${r.now.deliveries} доставок` : undefined,
                   tone: (r.now.late_pct as number) >= 15 ? 'var(--red)' : 'var(--amb)' })), [rows])
  const lateStaff = useMemo(() => {
    const counted = rows
      .filter((r) => typeof r.now?.staff_late_pct === 'number')
      .sort((a, b) => (b.now.staff_late_pct as number) - (a.now.staff_late_pct as number))
      .map((r) => ({ id: r.organization_id, name: r.name,
                     value: `${Math.round((r.now.staff_late_pct as number) * 10) / 10}%`,
                     note: r.staff?.total ? `${r.staff.late} из ${r.staff.total}` : undefined,
                     tone: (r.now.staff_late_pct as number) >= 30 ? 'var(--red)' : 'var(--amb)' }))
    /* Филиал сравнивают со стартом смены. Не задал — сравнение шло с
       дефолтом 09:00, а смена там начинается в 10:40, и «опоздали все»
       говорит про настройку, а не про людей. Такие идут в конец и серым. */
    const unset = rows.filter((r) => r.staff?.configured === false)
      .map((r) => ({ id: r.organization_id, name: r.name, value: '—',
                     note: 'старт смены не задан', tone: 'var(--mute)' }))
    return [...counted, ...unset]
  }, [rows])
  const staffCounted = rows.filter((r) => typeof r.now?.staff_late_pct === 'number').length

  const tip = { contentStyle: { background: 'var(--card)', border: '1px solid var(--hair)',
                                borderRadius: 12, fontSize: 12, color: 'var(--hi)' },
                labelStyle: { color: 'var(--mute)' }, cursor: { fill: 'rgba(127,127,127,.08)' } }
  const axis = { tick: { fill: tok.mute, fontSize: 11 }, tickLine: false, axisLine: false }

  return (
    <motion.div
      /* Затемнение + материал: задача модальная, фон уводим назад. Появление и
         уход одним путём — снизу и вниз (см. пространственную симметрию). */
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 60, overflowY: 'auto',
        background: 'rgba(10,16,30,.44)', backdropFilter: 'blur(18px) saturate(140%)',
        WebkitBackdropFilter: 'blur(18px) saturate(140%)',
        padding: 'clamp(8px, 2vw, 24px)',
      }}>
      <motion.div
        onClick={(e) => e.stopPropagation()}
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 24, scale: .985 }}
        animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, y: 24, scale: .985 }}
        transition={{ type: 'spring', bounce: 0, duration: 0.4 }}
        style={{
          /* во весь экран: это проекция на собрании, а не карточка */
          maxWidth: 1560, minHeight: 'calc(100vh - clamp(16px, 4vw, 48px))',
          margin: '0 auto', borderRadius: 26, padding: 'clamp(16px, 2.4vw, 30px)',
          /* Подложка — та же, что у страницы, и обязательно НЕПРОЗРАЧНАЯ: токена
             --bg в теме нет, и лист был стеклянным. Сквозь него просвечивал
             размытый экран, а серые подписи под заголовком тонули в этом фоне. */
          backgroundColor: 'var(--card)', backgroundImage: 'var(--wp-scrim)',
          border: '1px solid var(--hair)',
          boxShadow: '0 40px 90px -30px rgba(10,20,50,.55)',
        }}>

        {/* Отклик на нажатие, а не на отпускание: ждать click — значит
            потерять ощущение прямого управления. Движение убираем тем, кто
            просил его убрать. */}
        <style>{`
          .pg-press { transition: transform 100ms ease-out; }
          .pg-press:active { transform: scale(.97); }
          @media (prefers-reduced-motion: reduce) {
            .pg-press { transition: none; }
            .pg-press:active { transform: none; opacity: .7; }
          }
        `}</style>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 10.5, letterSpacing: '.14em', textTransform: 'uppercase',
                          color: 'var(--mute)', fontWeight: 700 }}>
              Неделя {data.week} · {data.from.slice(5)} — {data.to.slice(5)}
            </div>
            {/* Крупный заголовок: плотный интерлиньяж и отрицательный трекинг —
                на большом кегле буквы иначе расползаются. */}
            <h2 className="display" style={{ margin: '4px 0 0', fontSize: 'clamp(26px, 3.4vw, 38px)',
                                             lineHeight: 1.16, letterSpacing: '-.025em' }}>
              Прогресс и динамика
            </h2>
            <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>
              {rows.length} филиалов · сравнение с прошлой неделей день в день
              {done ? ` (${done} дн.)` : ''}
            </div>
          </div>
          <button ref={closeRef} onClick={onClose} aria-label="Закрыть" className="pg-press"
            style={{ marginLeft: 'auto', padding: '9px 16px', borderRadius: 12,
                     border: '1px solid var(--hair)', background: 'transparent',
                     color: 'var(--hi)', fontSize: 13, fontWeight: 650,
                     fontFamily: 'inherit', cursor: 'pointer', alignSelf: 'flex-start' }}>
            Закрыть
          </button>
        </div>

        {/* 1 · Сеть в цифрах */}
        <div style={{ marginTop: 20, display: 'grid', gap: 12,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(168px, 1fr))' }}>
          {totals && (
            <motion.div
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
              animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.4 }}
              style={panel}>
              <div style={eyebrow}>План недели</div>
              <div className="tnum" style={{ marginTop: 8, fontSize: 34, fontWeight: 780,
                                             letterSpacing: '-.03em', lineHeight: 1, color: 'var(--hi)' }}>
                {totals.pct != null ? `${totals.pct}%` : '—'}
              </div>
              <div className="tnum" style={{ marginTop: 7, fontSize: 12.5, fontWeight: 700,
                                             color: (totals.pct || 0) >= 100 ? 'var(--grn)'
                                                    : (totals.pct || 0) >= 90 ? 'var(--amb)' : 'var(--red)' }}>
                {num(totals.fact)} из {num(totals.plan)}
              </div>
              <div style={{ marginTop: 3, fontSize: 11, color: 'var(--mute)' }}>
                за {done} закрытых дн.
              </div>
            </motion.div>
          )}
          {plan?.network?.fot && (
            <motion.div
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
              animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.4, delay: 0.04 }}
              style={panel}>
              <div style={eyebrow}>{plan.network.fot.unit === 'shift' ? 'ФОТ по графику' : 'ФОТ по явкам'}</div>
              <div className="tnum" style={{ marginTop: 8, fontSize: 32, fontWeight: 780,
                                             letterSpacing: '-.03em', lineHeight: 1, color: 'var(--hi)' }}>
                {num(plan.network.fot.amount)} ₸
              </div>
              <div style={{ marginTop: 7, fontSize: 12.5,
                            color: plan.network.fot.complete ? 'var(--grn)' : 'var(--amb)' }}>
                {fotCoverage(plan.network.fot)}
              </div>
              {/* На собрании неполную сумму надо не просто пометить, а назвать
                  причину: ставка должности заводится в iiko, и это единственное
                  действие, которое превращает эту цифру в настоящую. */}
              {!plan.network.fot.complete && (
                <div style={{ marginTop: 3, fontSize: 11.5, color: 'var(--mute)', lineHeight: 1.4 }}>
                  {fotGapsLine(plan.network.fot) || 'сумма пока неполная'}
                </div>
              )}
            </motion.div>
          )}
          {defs.map((m, i) => (
            <Card key={m.key} def={m} rows={rows} i={i + 1} reduce={reduce}
              foot={m.key === 'staff_late_pct' && staffCounted < rows.length ? (
                <div style={{ marginTop: 6, fontSize: 11, color: 'var(--amb)' }}>
                  по {staffCounted} из {rows.length}: остальные не задали старт смены
                </div>
              ) : undefined} />
          ))}
        </div>

        {/* 2 · Разбор по филиалам. Стоит сразу под цифрами сети: с этой
            таблицы владелец и начинает называть филиалы по именам. */}
        <div style={{ ...panel, marginTop: 14, overflowX: 'auto' }}>
          <div style={eyebrow}>По филиалам</div>
          <div style={{ marginTop: 2, fontSize: 11.5, color: 'var(--mute)' }}>
            показатели — за {done} закрытых дн. недели; причина опозданий — за неделю
            {causePeriod ? ` ${dm(causePeriod.from)}–${dm(causePeriod.to)}` : ''}, ту, что заканчивается собранием
          </div>
          <table style={{ width: '100%', minWidth: 1080, borderCollapse: 'collapse', marginTop: 12, fontSize: 14 }}>
            <thead><tr>
              <th style={{ textAlign: 'left', padding: '0 0 8px', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>Филиал</th>
              {!!byBranchPlan.length && (
                <th style={{ textAlign: 'right', padding: '0 0 8px 12px', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>
                  План / факт
                </th>
              )}
              {!!byBranchPlan.length && (
                <th style={{ textAlign: 'right', padding: '0 0 8px 12px', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>
                  ФОТ
                </th>
              )}
              {defs.map((m) => (
                <th key={m.key} style={{ textAlign: 'right', padding: '0 0 8px 12px', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>
                  {m.label}
                </th>
              ))}
              <th style={{ textAlign: 'left', padding: '0 0 8px 12px', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>Причина опозданий</th>
              <th style={{ textAlign: 'right', padding: '0 0 8px 12px', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>Оценка</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => {
                const pl = byBranchPlan.find((b) => b.id === r.organization_id)
                const sc = scores.find((s) => s.organization_id === r.organization_id)
                return (
                  <tr key={r.organization_id}>
                    <td style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)', color: 'var(--hi)' }}>
                      {shortName(r.name)}
                    </td>
                    {!!byBranchPlan.length && (
                      <td className="tnum" style={{ padding: '9px 0 9px 12px', textAlign: 'right', borderBottom: '1px solid var(--hair-2)' }}>
                        {pl ? (
                          <>
                            <span style={{ color: 'var(--hi)', fontWeight: 650 }}>{num(pl.fact)}</span>
                            <span style={{ color: 'var(--mute)' }}> / {num(pl.plan)}</span>
                            <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 700,
                                           color: (pl.pct || 0) >= 100 ? 'var(--grn)' : (pl.pct || 0) >= 90 ? 'var(--amb)' : 'var(--red)' }}>
                              {pl.pct}%
                            </span>
                          </>
                        ) : '—'}
                      </td>
                    )}
                    {!!byBranchPlan.length && (
                      <td className="tnum" style={{ padding: '9px 0 9px 12px', textAlign: 'right', borderBottom: '1px solid var(--hair-2)' }}>
                        {pl?.fot && pl.fot.unit ? (
                          <>
                            <span style={{ color: 'var(--hi)', fontWeight: 700 }}>{num(pl.fot.amount)} ₸</span>
                            <div style={{ fontSize: 11, color: pl.fot.complete ? 'var(--mute)' : 'var(--amb)' }}>
                              {num(pl.fot.priced)}/{num(pl.fot.total)} {pl.fot.unit === 'shift' ? 'смен' : 'ч'}
                            </div>
                          </>
                        ) : '—'}
                      </td>
                    )}
                    {defs.map((m) => {
                      const d = delta(r.now?.[m.key] ?? null, r.prev?.[m.key] ?? null, m.lower_better)
                      return (
                        <td key={m.key} className="tnum" style={{ padding: '9px 0 9px 12px', textAlign: 'right', borderBottom: '1px solid var(--hair-2)' }}>
                          {/* Единица прямо в ячейке: в одной строке стоят минуты,
                              проценты и штуки — без подписи их путают. */}
                          <span style={{ color: 'var(--hi)', fontWeight: 650 }}>{fmt(r.now?.[m.key] ?? null, '')}</span>
                          {m.unit && r.now?.[m.key] != null && (
                            <span style={{ color: 'var(--mute)', fontWeight: 600 }}> {m.unit}</span>
                          )}
                          {m.key === 'staff_late_pct' && r.now?.[m.key] == null && r.staff?.configured === false && (
                            <div style={{ fontSize: 10.5, color: 'var(--amb)', fontWeight: 600 }}>старт смены не задан</div>
                          )}
                          {d && Math.abs(d.pct) >= 0.5 && (
                            <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 700,
                                           color: d.better ? 'var(--grn)' : 'var(--red)' }}>
                              {d.diff > 0 ? '↑' : '↓'}{Math.abs(Math.round(d.pct))}%
                            </span>
                          )}
                        </td>
                      )
                    })}
                    <td style={{ padding: '9px 0 9px 12px', borderBottom: '1px solid var(--hair-2)' }}>
                      {r.late_cause ? (
                        <>
                          <span style={{ color: 'var(--hi)' }}>{r.late_cause.label}</span>
                          <span className="tnum" style={{ color: 'var(--mute)' }}> · {r.late_cause.count} из {r.late_orders ?? '—'}</span>
                        </>
                      ) : <span style={{ color: 'var(--mute)' }}>опозданий нет</span>}
                    </td>
                    <td className="tnum" style={{ padding: '9px 0 9px 12px', textAlign: 'right', borderBottom: '1px solid var(--hair-2)',
                                                  fontWeight: 700,
                                                  color: typeof sc?.score !== 'number' ? 'var(--mute)'
                                                    : sc.score >= 70 ? 'var(--grn)' : sc.score >= 45 ? 'var(--amb)' : 'var(--red)' }}>
                      {typeof sc?.score === 'number' ? sc.score : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {/* 3 · План и факт по дням недели */}
        {!!byDay.length && (
          <Section title="План и факт по дням" style={{ marginTop: 14 }}
            hint="столбики — факт, линия — план, пунктир — прошлая неделя. Незакрытые дни не рисуем.">
            <div style={{ height: 260, marginTop: 12 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={byDay} margin={{ top: 8, right: 8, left: -6, bottom: 0 }}>
                  <CartesianGrid stroke={tok.hair} vertical={false} />
                  <XAxis dataKey="d" {...axis} />
                  <YAxis {...axis} width={56} />
                  <Tooltip {...tip} />
                  <Bar dataKey="fact" name="факт" fill={tok.acc} radius={[6, 6, 0, 0]} maxBarSize={54} />
                  <Line type="monotone" dataKey="plan" name="план" stroke={tok.amb}
                        strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="prev" name="прошлая неделя" stroke={tok.mute}
                        strokeWidth={1.5} strokeDasharray="4 4" dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Section>
        )}

        {/* 4 · Выполнение плана по филиалам + 5 · неделя к неделе */}
        <div style={{ marginTop: 14, display: 'grid', gap: 12,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))' }}>
          {!!byBranchPlan.length && (
            <Section title="Выполнение плана по филиалам"
              hint={`факт против плана за ${done} закрытых дн.`}>
              <div style={{ height: Math.max(200, byBranchPlan.length * 30), marginTop: 12 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byBranchPlan} layout="vertical"
                            margin={{ top: 0, right: 34, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke={tok.hair} horizontal={false} />
                    <XAxis type="number" {...axis} unit="%" />
                    <YAxis type="category" dataKey="name" width={132} {...axis} />
                    <Tooltip {...tip} formatter={(v) => [`${Number(v)}% плана`, '']} />
                    <ReferenceLine x={100} stroke={tok.mute} strokeDasharray="3 3" />
                    <Bar dataKey="pct" radius={[0, 6, 6, 0]} maxBarSize={18}>
                      {byBranchPlan.map((b) => (
                        <RCell key={b.id} fill={(b.pct || 0) >= 100 ? tok.grn : (b.pct || 0) >= 90 ? tok.amb : tok.red} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Section>
          )}

          {!!cmpData.length && (
            <Section title="Неделя к неделе"
              hint={cmpDef ? `${cmpDef.label}: тёмный — эта неделя, светлый — прошлая` : ''}>
              <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {COMPARE.map((k) => byKey[k] && (
                  <button key={k} onClick={() => setCmp(k)} className="pg-press"
                    style={{ fontSize: 12, fontWeight: 650, padding: '5px 11px', borderRadius: 10,
                             cursor: 'pointer', fontFamily: 'inherit',
                             border: '1px solid ' + (cmp === k ? 'transparent' : 'var(--hair)'),
                             background: cmp === k ? 'var(--acc)' : 'transparent',
                             color: cmp === k ? 'var(--on-acc)' : 'var(--mute)' }}>
                    {byKey[k].label}
                  </button>
                ))}
              </div>
              <div style={{ height: Math.max(200, cmpData.length * 34), marginTop: 12 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={cmpData} layout="vertical"
                            margin={{ top: 0, right: 34, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke={tok.hair} horizontal={false} />
                    <XAxis type="number" {...axis} unit={cmpDef?.unit ? ` ${cmpDef.unit}` : ''} />
                    <YAxis type="category" dataKey="name" width={132} {...axis} />
                    <Tooltip {...tip} />
                    <Bar dataKey="prev" name="прошлая" fill={tok.mute} fillOpacity={0.35}
                         radius={[0, 5, 5, 0]} maxBarSize={9} />
                    <Bar dataKey="now" name="эта" fill={tok.acc} radius={[0, 5, 5, 0]} maxBarSize={9} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Section>
          )}
        </div>

        {/* 6 · Что улучшилось и что просело — поимённо */}
        <div style={{ marginTop: 14, display: 'grid', gap: 12,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
          <Moves title="Что улучшилось" hint="было → стало, к прошлой неделе"
                 items={wins} tone="var(--grn)" />
          <Moves title="Что ухудшилось" hint="теми же порогами, что и улучшения"
                 items={losses} tone="var(--red)" />
        </div>

        {/* 7 · Кому внимание */}
        <div style={{ marginTop: 14, display: 'grid', gap: 12,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
          <Top title="Опоздания заказов"
               hint={`доля опоздавших доставок и что съело время${causePeriod ? ` · причина за ${dm(causePeriod.from)}–${dm(causePeriod.to)}` : ''}`}
               items={lateOrders} reduce={reduce} />
          <Top title="Опоздания сотрудников" hint="доля опоздавших приходов на смену"
               items={lateStaff} reduce={reduce} />
          <Top title="Топ по движению" hint="сколько показателей улучшили за неделю"
               items={moved} reduce={reduce} />
          <Top title="Топ по оценке" hint="оценка филиала 0–100 с «Горы»"
               items={byScore} reduce={reduce} />
        </div>

      </motion.div>
    </motion.div>
  )
}
