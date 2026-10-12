import { useState } from 'react'
import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { useApi } from '../lib/useApi'
import { Glass, GBar, gfill, PlanFallback, PageState as Note } from './ui'
import { tenge } from '../lib/format'

/* ШАГ 4 — Директор (домашний экран): ОДНА оценка состояния 0–100 из взвешенных
   факторов (по тапу — разбор вклада), мастер-полоска возврата, путь-станции и план
   РЕЗУЛЬТАТОВ (сейчас → после = +₸), ранжир по деньги ÷ усилие. Чего нет в данных —
   «нет данных», не выдумываем. Цвет только на деньгах и на самой оценке-здоровье. */

type Health = { key: string; value: string; status: 'red' | 'yellow' | 'green' }
type Insights = { leakage_total: number; health: Health[] }
type Dish = { name: string; revenue: number; cost_pct: number }
type FCT = { norm_pct: number; fact_pct: number | null; fact_sum: number; norm_sum: number; dishes: Dish[] }
type ExpCat = { name: string; amount: number }
type Exp = { total: number; categories: ExpCat[] }
type Cancels = { total_amount: number; total_orders: number }
type StockD = { total_discrepancy: number }
type Writeoffs = { total_written: number; loss_pct: number }
type Platform = { label: string; rating: number; scale: number }
type ReviewsT = { platforms: Platform[] }
type Dash = { revenue_today: number; avg_check: number; avg_check_change: number; guests: number }

type Effort = 'быстро' | 'средне' | 'долго'
const EW: Record<Effort, number> = { быстро: 1, средне: 2, долго: 3 }
type Task = { key: string; metric: string; before: string; after: string; money: number; effort: Effort; note: string; steps: string[] }

const RED = 'var(--red)'; const GRN = 'var(--grn)'; const AMB = 'var(--amb)'
const scoreCol = (s: number) => (s >= 70 ? GRN : s >= 45 ? AMB : RED)

function Eyebrow({ t, hint }: { t: string; hint?: ReactNode }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}
/* Кольцо-гейдж оценки */
function Gauge({ score }: { score: number }) {
  const size = 188, r = 80, cx = size / 2, C = 2 * Math.PI * r, arc = 0.72
  const track = C * arc, fill = track * (score / 100), col = scoreCol(score)
  return (
    <div style={{ position: 'relative', width: size, height: size, flex: 'none' }}>
      <svg width={size} height={size} style={{ transform: 'rotate(130deg)' }}>
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--hair)" strokeWidth={13} strokeDasharray={`${track} ${C}`} strokeLinecap="round" />
        <motion.circle cx={cx} cy={cx} r={r} fill="none" stroke={col} strokeWidth={13} strokeLinecap="round"
          initial={{ strokeDasharray: `0 ${C}` }} animate={{ strokeDasharray: `${fill} ${C}` }} transition={{ duration: 1.1, ease: 'easeOut' }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <div className="tnum" style={{ fontSize: 58, fontWeight: 720, lineHeight: 1, color: col, letterSpacing: '-.03em' }}>{score}</div>
          <div style={{ fontSize: 10.5, letterSpacing: '.13em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, marginTop: 2 }}>из 100</div>
        </div>
      </div>
    </div>
  )
}

/* Красивая анимация размышления Директора: вращающееся кольцо + пульсирующий
   ИИ-орб + последовательно мерцающие шаги + бегущий шиммер. */
function Thinking() {
  const steps = ['Считаю фудкост по техкартам', 'Свожу расходы и структуру', 'Ищу утечки, стоп-лист, отмены', 'Строю план по деньги ÷ усилие']
  return (
    <Glass span={12}>
      <div style={{ display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap', padding: '6px 2px' }}>
        <div style={{ position: 'relative', width: 90, height: 90, flex: 'none', display: 'grid', placeItems: 'center' }}>
          <motion.span aria-hidden animate={{ rotate: 360 }} transition={{ duration: 2.6, repeat: Infinity, ease: 'linear' }}
            style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: '3px solid transparent', borderTopColor: 'var(--acc)', borderRightColor: 'color-mix(in srgb, var(--acc) 35%, transparent)' }} />
          <motion.span aria-hidden animate={{ scale: [1, 1.14, 1], opacity: [0.55, 1, 0.55] }} transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
            style={{ width: 52, height: 52, borderRadius: '50%', display: 'grid', placeItems: 'center', background: 'radial-gradient(circle, color-mix(in srgb, var(--acc) 55%, transparent), transparent 72%)' }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="var(--acc)"><path d="M12 3l1.8 4.7L18 9.5l-4.2 1.8L12 16l-1.8-4.7L6 9.5l4.2-1.8z" /><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z" /></svg>
          </motion.span>
        </div>
        <div style={{ flex: '1 1 300px', minWidth: 260 }}>
          <div style={{ fontSize: 11, letterSpacing: '.13em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 650 }}>Директор думает</div>
          <div className="display" style={{ fontSize: 22, margin: '4px 0 14px' }}>Свожу картину по ресторану…</div>
          {steps.map((s, i) => (
            <motion.div key={s} animate={{ opacity: [0.3, 1, 0.3] }} transition={{ duration: 2.2, repeat: Infinity, delay: i * 0.5 }}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '5px 0', fontSize: 13, color: 'var(--lo)' }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--acc)', boxShadow: '0 0 8px var(--acc)', flex: 'none' }} />{s}
            </motion.div>
          ))}
          <div style={{ marginTop: 12, height: 6, borderRadius: 4, background: 'var(--glass-2)', overflow: 'hidden', position: 'relative' }}>
            <motion.div animate={{ x: ['-45%', '145%'] }} transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
              style={{ position: 'absolute', top: 0, bottom: 0, width: '45%', borderRadius: 4, background: 'linear-gradient(90deg, transparent, var(--acc), transparent)' }} />
          </div>
        </div>
      </div>
    </Glass>
  )
}

const subOf = (s: string) => (s === 'green' ? 92 : s === 'yellow' ? 58 : 28)
const weightOf = (k: string) => (/удкост|маржа/i.test(k) ? 0.26 : /ыручк/i.test(k) ? 0.18 : /чек/i.test(k) ? 0.12 : /тмен/i.test(k) ? 0.12 : /гис|тзыв|wolt|olt/i.test(k) ? 0.10 : 0.10)

export default function Director() {
  const ins = useApi<Insights>('insights')
  const fc = useApi<FCT>('foodcost', { period: 'month', compute: '1' })
  const cn = useApi<Cancels>('cancels', { period: 'month' })
  const stk = useApi<StockD>('stock')
  const wo = useApi<Writeoffs>('writeoffs', { period: 'month' })
  const rev = useApi<ReviewsT>('reviews', { period: 'month' })
  // period: 'month' — значит revenue_today и guests содержат МЕСЯЦ, а не день.
  // Умножать их ещё на 30 (как было) — завышать потенциал задачи в 30 раз.
  const dash = useApi<Dash>('dashboard', { period: 'month' })
  const exp = useApi<Exp>('expenses', { period: 'month' })
  const hall = useApi<{ connected?: boolean }>('hall')
  const [open, setOpen] = useState(false)

  if (ins.loading || fc.loading) return <Thinking />
  if (ins.error || !ins.data) return <Note>Не удалось собрать данные: {ins.error}</Note>

  /* ── План: результаты по зонам + усилие по правилу ── */
  const tasks: Task[] = []
  if (fc.data?.dishes?.length) {
    const norm = fc.data.norm_pct
    const worst = [...fc.data.dishes].filter((d) => d.cost_pct > norm).sort((a, b) => (b.cost_pct - norm) * b.revenue - (a.cost_pct - norm) * a.revenue)[0]
    if (worst) { const m = Math.round((worst.revenue * (worst.cost_pct - norm)) / 100); if (m > 0) tasks.push({ key: 'fc', metric: `Маржа: ${worst.name}`, before: `${Math.round(100 - worst.cost_pct)}%`, after: `${Math.round(100 - norm)}%`, money: m, effort: 'средне', note: `себестоимость ${worst.cost_pct}% при норме ${norm}%`, steps: ['Открой техкарту блюда в iiko', 'Найди 2 самых дорогих ингредиента, передоговорись с поставщиком или замени', 'Убери перевес порции — сверь факт-выход с картой'] }) }
  }
  if (dash.data && dash.data.avg_check_change < 0) { const m = Math.round(dash.data.guests * dash.data.avg_check * Math.abs(dash.data.avg_check_change) / 100); if (m > 0) tasks.push({ key: 'chk', metric: 'Средний чек', before: `${dash.data.avg_check_change.toFixed(1)}%`, after: '+0%', money: m, effort: 'быстро', note: 'чек падает — недобор допродаж', steps: ['Введи скрипт допродажи на кассе: гарнир / напиток / десерт', 'Собери 2–3 комбо к топ-горячему', 'Прогони обе смены по скрипту за неделю'] }) }
  if (rev.data?.platforms?.length && dash.data) { const p = [...rev.data.platforms].sort((a, b) => a.rating / a.scale - b.rating / b.scale)[0]; const target = p.scale >= 10 ? 9 : 4.7; const gap = Math.max(0, (target - p.rating) / target); const m = Math.round(dash.data.revenue_today * Math.min(0.1, gap * 1.1)); if (m > 0) tasks.push({ key: 'rev', metric: `Отзывы · ${p.label}`, before: `${p.rating}`, after: `${target}`, money: m, effort: 'долго', note: 'низкий рейтинг уводит гостей', steps: ['Ответь на все негативные отзывы за неделю', 'Найди повторяющуюся жалобу (кухня / зал / скорость)', 'Устрани причину и попроси довольных гостей оставить отзыв'] }) }
  if (stk.data?.total_discrepancy) tasks.push({ key: 'stock', metric: 'Недостача склад', before: tenge(stk.data.total_discrepancy), after: '0 ₸', money: stk.data.total_discrepancy, effort: 'долго', note: 'ушло больше, чем продано', steps: ['Сверь приход по накладным с фактом', 'Проведи инвентаризацию 5 ключевых позиций', 'Поставь контроль порционирования и списаний'] })
  if (wo.data && wo.data.loss_pct > 5) { const m = Math.round(wo.data.total_written * (wo.data.loss_pct - 5) / wo.data.loss_pct); if (m > 0) tasks.push({ key: 'wo', metric: 'Списания', before: `${wo.data.loss_pct.toFixed(1)}%`, after: '5%', money: m, effort: 'долго', note: 'порча выше нормы', steps: ['Формируй заявки строго под план расхода', 'Введи FIFO — старое в работу первым', 'Проверяй сроки скоропорта каждое утро'] }) }
  if (cn.data?.total_amount) tasks.push({ key: 'cn', metric: 'Отмены и возвраты', before: tenge(cn.data.total_amount), after: '0 ₸', money: cn.data.total_amount, effort: 'средне', note: `${cn.data.total_orders} отмен за месяц`, steps: ['Выгрузи причины отмен за месяц', 'Закрой топ-причину: стоп-лист или обучение', 'Проверь связку касса↔кухня в час пик'] })

  // Приоритет = деньги ÷ усилие (быстрый фикс может стоять выше жирного)
  tasks.sort((a, b) => (b.money / EW[b.effort]) - (a.money / EW[a.effort]))
  const inWork = tasks.slice(0, 3)
  const queue = tasks.slice(3)
  const onTable = tasks.reduce((s, t) => s + t.money, 0) || ins.data.leakage_total
  const maxMoney = Math.max(1, ...tasks.map((t) => t.money))
  const moneyFor = (name: string) => tasks.find((t) => (/удкост|маржа/i.test(name) && t.key === 'fc') || (/тмен/i.test(name) && t.key === 'cn') || (/тзыв|гис|wolt/i.test(name) && t.key === 'rev'))?.money

  /* ── Единая оценка: факторы iiko + план; нет источника → «нет данных» ── */
  const health = ins.data.health || []
  const returnedPct = 0
  const factors = [
    ...health.map((h) => ({ name: h.key, sub: subOf(h.status), weight: weightOf(h.key), status: h.status, money: moneyFor(h.key) })),
    { name: 'Выполнение плана', sub: returnedPct, weight: 0.2, status: (returnedPct >= 70 ? 'green' : returnedPct >= 40 ? 'yellow' : 'red') as string, money: undefined as number | undefined },
  ]
  const wsum = factors.reduce((s, f) => s + f.weight, 0) || 1
  const score = Math.round(factors.reduce((s, f) => s + f.sub * f.weight, 0) / wsum)
  // «нет данных» определяем динамически: зал подключён (Cloud) и соцсети найдены — не показываем.
  const noData = [
    ...(hall.data?.connected ? [] : ['Посадка зала']),
    'KDS · тайминги кухни',
    ...(rev.data?.platforms?.length ? [] : ['Соцсети · охваты']),
  ]

  /* ── Путь: станции по факторам ── */
  const stations = factors.filter((f) => f.name !== 'Выполнение плана')

  /* ── Структура расходов: куда уходит выручка (закуп + операционные + прибыль) ── */
  // индиго-глас для операционных (наш акцент), янтарь — на снижаемых, мятный — прибыль
  const INDIGO = ['#5b6cf0', '#616fc8', '#6673a4', '#6f7994', '#7e88a3', '#8d95ac', '#9aa2b6', '#a7adbf']
  const fcd = fc.data
  // База — РЕАЛЬНАЯ выручка филиала (dashboard), а не выведенная из фудкоста (при низком
  // покрытии техкарт та база сильно врала → прибыль уходила в −200%).
  const revenue = (dash.data?.revenue_today || 0) > 0
    ? dash.data!.revenue_today
    : (fcd && fcd.fact_pct ? fcd.fact_sum / (fcd.fact_pct / 100) : 0)
  type CostLine = { name: string; amount: number; pct: number; color: string; kind: 'cogs' | 'opex' | 'profit' }
  const costLines: CostLine[] = []
  if (revenue > 0 && fcd) {
    costLines.push({ name: 'Закуп (себестоимость)', amount: fcd.fact_sum, pct: (fcd.fact_sum / revenue) * 100, color: '#e0a13a', kind: 'cogs' })
    // «Расход продуктов» — это тот же COGS, уже учтён Закупом; в опекс не берём (дубль).
    const COGS_DUP = /расход\s*продукт|себестоим/i
    const cats = [...(exp.data?.categories ?? [])].filter((c) => !COGS_DUP.test(c.name)).sort((a, b) => b.amount - a.amount)
    cats.forEach((c, i) => costLines.push({ name: c.name, amount: c.amount, pct: (c.amount / revenue) * 100, color: INDIGO[i % INDIGO.length], kind: 'opex' }))
    const opexTotal = cats.reduce((s, c) => s + c.amount, 0)
    const profit = revenue - fcd.fact_sum - opexTotal
    costLines.push({ name: 'Прибыль', amount: profit, pct: (profit / revenue) * 100, color: '#37c489', kind: 'profit' })
  }
  const profitLine = costLines.find((c) => c.kind === 'profit')
  const avgCheck = dash.data?.avg_check
  const maxPct = Math.max(1, ...costLines.map((c) => c.pct))

  /* ── Планы снижения: категории выше здоровой нормы → цель, экономия, как ── */
  const CAT_NORM: Record<string, number> = { 'Закуп (себестоимость)': 30, 'ФОТ (зарплаты)': 25, 'Аренда': 8, 'Эквайринг': 1.5, 'Коммуналка': 2, 'Хозрасходы': 1.5, 'Маркетинг': 4 }
  const CAT_HOW: Record<string, string> = {
    'Закуп (себестоимость)': 'Пересобери техкарты топ-блюд, передоговор с поставщиками, убери перевес порций',
    'ФОТ (зарплаты)': 'Ставь смены под реальный час-пик, считай выработку на человека',
    'Аренда': 'Пересмотри ставку/договор, сдай в субаренду простаивающие зоны',
    'Эквайринг': 'Договорись с банком на сниженную ставку, стимулируй переводы',
    'Коммуналка': 'Энергоэффективность, проверь тарифы и ночные режимы',
    'Хозрасходы': 'Заявки строго под план, FIFO, контроль списаний',
  }
  const plans = costLines
    .filter((c) => c.kind !== 'profit' && CAT_NORM[c.name] != null && c.pct > CAT_NORM[c.name] + 0.1)
    .map((c) => ({ name: c.name, cur: c.pct, target: CAT_NORM[c.name], save: ((c.pct - CAT_NORM[c.name]) / 100) * revenue, how: CAT_HOW[c.name] || '', color: c.color }))
    .sort((a, b) => b.save - a.save)
  const totalSave = plans.reduce((s, p) => s + p.save, 0)

  return (
    <>
      {/* ОЦЕНКА + МАСТЕР-ПОЛОСКА */}
      <Glass span={12}>
        <Eyebrow t="Директор · здоровье ресторана" hint={<button onClick={() => setOpen((v) => !v)} style={{ cursor: 'pointer', background: 'none', border: 'none', color: 'var(--acc)', font: 'inherit' }}>{open ? 'свернуть' : 'из чего складывается ›'}</button>} />
        <div style={{ display: 'flex', gap: 26, alignItems: 'center', flexWrap: 'wrap', marginTop: 12 }}>
          <Gauge score={score} />
          <div style={{ flex: '1 1 260px', minWidth: 240 }}>
            <div className="tnum" style={{ fontSize: 34, fontWeight: 680, letterSpacing: '-.03em', color: RED }}>
              {tenge(onTable)}<span style={{ fontSize: 14, color: 'var(--mute)', fontWeight: 500 }}> на столе / мес</span>
            </div>
            <div style={{ fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, margin: '16px 0 7px' }}>возвращено {returnedPct}%</div>
            <div style={{ display: 'flex', height: 10, borderRadius: 7, overflow: 'hidden', background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
              <div style={{ width: `${returnedPct}%`, ...gfill(GRN) }} />
              <div style={{ width: `${100 - returnedPct}%`, ...gfill(RED), opacity: 0.92 }} />
            </div>
            <div style={{ marginTop: 8, fontSize: 12, color: 'var(--mute)' }}>закрываешь задачу → возврат растёт, оценка поднимается</div>
          </div>
        </div>

        {/* Разбор вклада факторов (по тапу) */}
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} style={{ overflow: 'hidden', marginTop: 18, paddingTop: 16, borderTop: '1px solid var(--hair)' }}>
            {factors.map((f) => (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0' }}>
                <span style={{ flex: '1 1 150px', fontSize: 12.5, color: 'var(--lo)' }}>{f.name}<span style={{ color: 'var(--mute)' }}> · вес {Math.round(f.weight * 100)}%</span></span>
                <div style={{ flex: '2 1 160px' }}>
                  <GBar pct={f.sub} color={scoreCol(f.sub)} h={8} />
                </div>
                <span className="tnum" style={{ width: 40, textAlign: 'right', fontSize: 12.5, fontWeight: 650, color: scoreCol(f.sub) }}>{f.sub}</span>
                <span className="tnum" style={{ width: 92, textAlign: 'right', fontSize: 12, color: f.money ? RED : 'var(--mute)' }}>{f.money ? `−${tenge(f.money)}` : '·'}</span>
              </div>
            ))}
            {noData.map((n) => (
              <div key={n} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', fontSize: 12.5, color: 'var(--mute)' }}>
                <span>{n}</span><span>нет данных — источник не подключён</span>
              </div>
            ))}
          </motion.div>
        )}
      </Glass>

      {/* KPI-строка — сводка одним взглядом */}
      {revenue > 0 && ([
        { k: 'Выручка / мес', v: tenge(revenue), c: 'var(--hi)' },
        { k: 'Прибыль', v: profitLine ? `${profitLine.pct.toFixed(0)}%` : '—', c: GRN },
        { k: 'Фудкост', v: fcd && fcd.fact_pct != null ? `${fcd.fact_pct.toFixed(0)}%` : '—', c: fcd && fcd.fact_pct != null && fcd.fact_pct > fcd.norm_pct ? RED : 'var(--hi)', sub: fcd && fcd.fact_pct != null ? `норма ${fcd.norm_pct}%` : '' },
        { k: 'Средний чек', v: avgCheck ? tenge(avgCheck) : '—', c: 'var(--hi)' },
      ].map((m) => (
        <Glass key={m.k} span={3} tap>
          <div style={{ fontSize: 10, letterSpacing: '.11em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 650 }}>{m.k}</div>
          <div className="tnum" style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-.03em', color: m.c, marginTop: 9 }}>{m.v}</div>
          {m.sub && <div style={{ fontSize: 11, color: 'var(--mute)', marginTop: 4 }}>{m.sub}</div>}
        </Glass>
      )))}

      {/* СТРУКТУРА РАСХОДОВ — куда уходит выручка (светящиеся стеклянные бары) */}
      {costLines.length === 0 && fcd && fcd.fact_pct == null && (
        <Glass span={12}>
          <PlanFallback
            title="«Куда уходит выручка» появится после техкарт"
            reason="Структуру расходов и прибыль считаем от фудкоста, а он берётся из техкарт — в iiko они пока не заполнены."
            action="Заполни техкарты и приходные цены в iiko — разбор выручки и план экономии посчитаются сами." />
        </Glass>
      )}
      {costLines.length > 0 && (
        <Glass span={12}>
          <Eyebrow t="Куда уходит выручка" hint={`выручка ≈ ${tenge(revenue)}/мес · прибыль ${profitLine ? Math.round(profitLine.pct) : 0}%`} />
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 11 }}>
            {costLines.map((c) => {
              const w = Math.max(3, (c.pct / maxPct) * 100)
              return (
                <div key={c.name} style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                  <div style={{ width: 168, flex: 'none', fontSize: 12.5, color: c.kind === 'profit' ? 'var(--hi)' : 'var(--lo)', fontWeight: c.kind === 'profit' ? 650 : 450 }}>{c.name}</div>
                  <div style={{ flex: 1, height: 26, borderRadius: 9, background: 'var(--glass-2)', border: '1px solid var(--hair)', position: 'relative' }}>
                    <motion.div initial={{ width: 0 }} animate={{ width: `calc(${w}% - 4px)` }} transition={{ duration: 0.7, ease: 'easeOut' }}
                      style={{
                        position: 'absolute', top: 2, bottom: 2, left: 2, borderRadius: 7,
                        background: `linear-gradient(180deg, color-mix(in srgb, ${c.color} 78%, #fff), ${c.color})`,
                        boxShadow: `0 0 16px ${c.color}80, 0 5px 12px -5px ${c.color}, inset 0 1px 0 rgba(255,255,255,.55), inset 0 -3px 6px rgba(0,0,0,.18)`,
                      }} />
                  </div>
                  <div style={{ width: 150, flex: 'none', textAlign: 'right' }}>
                    <span className="tnum" style={{ fontSize: 15, fontWeight: 700, color: c.kind === 'profit' ? '#37c489' : 'var(--hi)' }}>{c.pct.toFixed(1)}%</span>
                    <span className="tnum" style={{ fontSize: 12, color: 'var(--mute)', marginLeft: 8 }}>{tenge(c.amount)}</span>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Как снизить — планы по категориям выше нормы */}
          {plans.length > 0 && (
            <div style={{ marginTop: 18, paddingTop: 16, borderTop: '1px solid var(--hair)' }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}><span>Как снизить · план</span><span className="hint">до здоровой нормы ≈ +{tenge(totalSave)}/мес</span></div>
              {plans.map((p) => (
                <div key={p.name} style={{ display: 'flex', gap: 12, alignItems: 'baseline', padding: '11px 0', borderTop: '1px solid var(--hair-2)' }}>
                  <span style={{ width: 8, height: 8, borderRadius: 3, background: p.color, flex: 'none', alignSelf: 'center' }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13.5, color: 'var(--hi)', fontWeight: 600 }}>{p.name} <span className="tnum" style={{ color: 'var(--mute)', fontWeight: 400 }}>· {p.cur.toFixed(1)}% → цель {p.target}%</span></div>
                    <div style={{ fontSize: 12, color: 'var(--mute)', marginTop: 4, lineHeight: 1.5 }}>{p.how}</div>
                  </div>
                  <span className="tnum" style={{ fontSize: 14, fontWeight: 700, color: '#37c489', flex: 'none' }}>+{tenge(p.save)}/мес</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ marginTop: 12, fontSize: 11.5, color: 'var(--mute)' }}>Закуп — из техкарт (foodcost), операционные — из проводок iiko. Прибыль = выручка − закуп − операционные.</div>
        </Glass>
      )}

      {/* ПУТЬ — станции прогресса */}
      <Glass span={12}>
        <Eyebrow t="Путь" hint={`${stations.filter((s) => s.status === 'green').length}/${stations.length} восстановлено`} />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 14 }}>
          {stations.map((s) => {
            const ok = s.status === 'green'; const c = ok ? GRN : s.status === 'yellow' ? AMB : RED
            return (
              <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 13px', borderRadius: 12, border: `1px solid ${ok ? 'var(--hair)' : c}`, background: 'var(--glass-2)', opacity: ok ? 0.85 : 1 }}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: c }} />
                <span style={{ fontSize: 12.5, color: 'var(--lo)' }}>{s.name}</span>
                {!ok && s.money && <span className="tnum" style={{ fontSize: 12, fontWeight: 650, color: RED }}>−{tenge(s.money)}</span>}
                {ok && <span style={{ fontSize: 11, color: GRN }}>✓</span>}
              </div>
            )
          })}
        </div>
      </Glass>

      {/* ПЛАН — в работе + очередь */}
      <Glass span={12}>
        <Eyebrow t="План месяца · в работе" hint="по деньги ÷ усилие · 2–3 задачи" />
        <div style={{ marginTop: 8 }}>
          {inWork.map((t, i) => <PlanRow key={t.key} t={t} rank={i} maxMoney={maxMoney} />)}
        </div>
        {queue.length > 0 && (
          <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--hair)' }}>
            <div style={{ fontSize: 10, letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, marginBottom: 4 }}>очередь · после закрытия текущих</div>
            {queue.map((t) => (
              <div key={t.key} style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, padding: '9px 0', borderTop: '1px solid var(--hair-2)' }}>
                <span style={{ fontSize: 13, color: 'var(--lo)' }}>{t.metric} <span style={{ fontSize: 11, color: 'var(--mute)' }}>· {t.effort}</span></span>
                <span className="tnum" style={{ fontSize: 13, fontWeight: 650, color: GRN }}>+{tenge(t.money)}/мес</span>
              </div>
            ))}
          </div>
        )}
        {tasks.length === 0 && <div style={{ padding: '14px 0', fontSize: 13, color: 'var(--mute)' }}>Потерь по деньгам не найдено — держим норму.</div>}
      </Glass>
    </>
  )
}

function PlanRow({ t, rank, maxMoney }: { t: Task; rank: number; maxMoney: number }) {
  const effCol = t.effort === 'быстро' ? GRN : t.effort === 'средне' ? AMB : 'var(--mute)'
  return (
    <div style={{ padding: '18px 0', borderTop: rank === 0 ? 'none' : '1px solid var(--hair-2)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
        <span style={{ fontSize: 13.5, fontWeight: 620, color: 'var(--hi)' }}>{t.metric}</span>
        <span style={{ fontSize: 10.5, letterSpacing: '.06em', textTransform: 'uppercase', fontWeight: 600, color: effCol }}>усилие · {t.effort}</span>
      </div>
      <div className="tnum" style={{ display: 'flex', alignItems: 'baseline', gap: 11, marginTop: 11, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 24, fontWeight: 660, color: RED }}>{t.before}</span>
        <span style={{ fontSize: 16, color: 'var(--mute)' }}>→</span>
        <span style={{ fontSize: 24, fontWeight: 660, color: GRN }}>{t.after}</span>
        <span style={{ fontSize: 14.5, fontWeight: 650, color: GRN, marginLeft: 4 }}>= +{tenge(t.money)}/мес</span>
      </div>
      <div style={{ marginTop: 11 }}>
        <GBar pct={(t.money / maxMoney) * 100} color={GRN} h={8} delay={0.1 + rank * 0.08} />
      </div>
      <div style={{ marginTop: 10, fontSize: 12, color: 'var(--mute)' }}>{t.note}</div>
      <div style={{ marginTop: 10 }}>
        <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, marginBottom: 7 }}>как достичь</div>
        {t.steps.map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '4px 0' }}>
            <span className="tnum" style={{ flex: 'none', width: 18, height: 18, borderRadius: '50%', border: '1px solid var(--hair)', fontSize: 10.5, fontWeight: 650, color: 'var(--lo)', display: 'grid', placeItems: 'center' }}>{i + 1}</span>
            <span style={{ fontSize: 12.5, color: 'var(--lo)', lineHeight: 1.5 }}>{s}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
