import type { ReactNode } from 'react'
import { Glass, Chart, Light, Bullet, Insight, Delta, fmt, PlanFallback, PageState as Note } from './ui'
import { areaChart, barChart, slope, donut, hbar, legend, color, bar } from './charts'
import { useApi } from '../lib/useApi'
import { periodLabel } from '../lib/api'
import { tenge } from '../lib/format'
import Kds from './Kds'
import LateDeliveries from './LateDeliveries'

/* ── мелкие помощники ── */
function Eyebrow({ t, hint }: { t: string; hint?: string }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}
function Hero({ eyebrow, hint, value, sub, note, demo }: { eyebrow: string; hint?: string; value: ReactNode; sub?: string; note?: string; demo?: string }) {
  return (
    <>
      <Eyebrow t={eyebrow} hint={hint} />
      <div className="tnum" style={{ fontSize: 46, fontWeight: 680, letterSpacing: '-.035em', lineHeight: 1, marginTop: 16, display: 'flex', alignItems: 'baseline', gap: 12 }}>{value}</div>
      {sub && <div style={{ marginTop: 9, fontSize: 12.5, color: 'var(--mute)', fontWeight: 500 }}>{sub}</div>}
      {note && <p style={{ marginTop: 14, fontSize: 13, lineHeight: 1.5, color: 'var(--lo)', maxWidth: '46ch' }}>{note}</p>}
      {demo && <div style={{ marginTop: 14, fontSize: 11.5, color: 'var(--amb)', display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--amb)', boxShadow: '0 0 6px var(--amb)' }} />{demo}</div>}
    </>
  )
}
const th: React.CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 10px', borderBottom: '1px solid var(--hair)' }
const td: React.CSSProperties = { padding: '11px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }
function Sub({ children }: { children: ReactNode }) { return <div style={{ fontSize: 10, color: 'var(--mute)', marginTop: 2 }}>{children}</div> }
/* компактная строка «подпись → значение» — заполняет витринную карточку без лишнего текста */
function Stat({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 14, padding: '11px 0', borderTop: '1px solid var(--hair-2)' }}>
      <span style={{ fontSize: 12.5, color: 'var(--mute)' }}>{k}</span>
      <span className="tnum" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--hi)', textAlign: 'right' }}>{v}</span>
    </div>
  )
}

/* ─────────── Главная — живой /api/dashboard ─────────── */
type DashT = {
  revenue_today: number; revenue_change: number
  avg_check: number; avg_check_change: number
  guests: number; guests_change: number
  open_orders: number; forecast: number
  chart_7days: { labels: string[]; data: number[] }
}
function Tile({ label, value, delta, invert }: { label: string; value: string; delta?: number; invert?: boolean }) {
  return (
    <Glass span={3} tap>
      <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>{label}</div>
      <div className="tnum" style={{ fontSize: 24, fontWeight: 660, letterSpacing: '-.02em', marginTop: 7, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>{value}{delta != null && <Delta v={delta} invert={invert} size={12} />}</div>
    </Glass>
  )
}
export function Dash() {
  const { data, error, loading } = useApi<DashT>('dashboard')
  if (loading) return <Note loading>Собираю выручку…</Note>
  if (error || !data) return <Note>Не удалось загрузить: {error}</Note>
  const c = data.chart_7days
  // Полные суммы, а не «0,6 млн»: владелец сравнивает дни между собой, и
  // округление до сотых миллиона стирает разницу в десятки тысяч.
  const bars = c.labels.map((l, i) => ({ l, v: Math.round(c.data[i]) }))
  const hiIdx = c.data.indexOf(Math.max(...c.data))
  const avg7 = Math.round(c.data.reduce((a, b) => a + b, 0) / c.data.length)
  return (
    <>
      <Glass span={8} spot warp>
        <div style={{ fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>Выручка · {periodLabel()}</div>
        <div className="tnum" style={{ fontSize: 40, fontWeight: 680, letterSpacing: '-.03em', marginTop: 8, display: 'flex', alignItems: 'baseline', gap: 14 }}>{tenge(data.revenue_today)}</div>
        <Chart html={barChart(bars, hiIdx, 'var(--acc)', ' ₸')} />
        <div style={{ fontSize: 12, color: 'var(--mute)', marginTop: 4 }}>Средняя за 7 дней — {tenge(avg7)}</div>
      </Glass>
      <Glass span={4}>
        <Eyebrow t="Прогноз месяца" hint="при текущем темпе" />
        <div className="tnum" style={{ fontSize: 28, fontWeight: 680, letterSpacing: '-.02em', margin: '14px 0 16px' }}>≈ {tenge(data.forecast)}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* «г/г» убрано: это были *_change из /api/dashboard, всегда 0, а подпись
              обещала сравнение год к году. Настоящее г/г — в «Отчёте» (/api/comparison). */}
          <Light tone="green" k="Средний чек" v={tenge(data.avg_check)} />
          <Light tone="green" k="Гостей" v={String(data.guests)} />
          <Light tone="amber" k="Открытые заказы" v={String(data.open_orders)} />
        </div>
      </Glass>
      {/* дельт нет: бэкенд отдаёт *_change нулями, показывать «0,0%» — врать */}
      <Tile label={`Выручка · ${periodLabel()}`} value={tenge(data.revenue_today)} />
      <Tile label="Средний чек" value={tenge(data.avg_check)} />
      <Tile label="Гостей" value={String(data.guests)} />
      <Tile label="Открытые заказы" value={String(data.open_orders)} />
    </>
  )
}

/* ─────────── Фудкост — живой /api/foodcost ─────────── */
type FCT = {
  norm_pct: number; fact_pct: number | null; diff_pct: number | null; norm_sum: number; fact_sum: number | null; warning: string | null
  cost_available?: boolean; coverage_pct?: number | null
  trend: { month: string; fact: number | null; norm: number }[]
  dishes: { name: string; category: string; revenue: number; count: number; cost_pct: number }[]
}
export function FC() {
  // самохостед iikoWeb считает теоретический фудкост по техкартам только с compute=1
  const { data, error, loading } = useApi<FCT>('foodcost', { compute: '1' })
  if (loading) return <Note loading>Считаю фудкост по техкартам…</Note>
  if (error || !data) return <Note>Не удалось загрузить: {error}</Note>
  // Владелец не завёл техкарты/приходные цены → себестоимость взять неоткуда: ставим планом, а не 0/пусто.
  if (data.fact_pct == null) return (
    <Glass span={12} spot>
      <PlanFallback
        title="Фудкост пока не считается"
        reason="В iiko не заполнены техкарты и/или приходные цены на ингредиенты — себестоимость блюд взять неоткуда."
        action="Заполни техкарты и приходные накладные в iiko — фудкост посчитается по ним автоматически." />
    </Glass>
  )
  const over = (data.fact_sum ?? 0) - data.norm_sum
  const toneOf = (p: number): 'acc' | 'warn' | 'bad' => (p >= 45 ? 'bad' : p >= 35 ? 'warn' : 'acc')
  const dishes = [...data.dishes].sort((a, b) => b.cost_pct - a.cost_pct)
  return (
    <>
      <Glass span={5} spot>
        <Hero eyebrow="Фудкост по техкартам" hint={`цель ${data.norm_pct}%`} value={<>{data.fact_pct}% <Delta v={data.diff_pct} invert /></>}
          note={`${data.warning ? data.warning + '. ' : ''}Перерасход к норме — ${tenge(over)} за период.`} />
        <div style={{ marginTop: 16 }}><Bullet value={data.fact_pct} target={data.norm_pct} max={50} tone={toneOf(data.fact_pct)} /></div>
        <Sub>факт {data.fact_pct}% · <span style={{ color: 'var(--hi)' }}>| цель {data.norm_pct}%</span> · порог 35%</Sub>
      </Glass>
      <Glass span={7}>
        <Eyebrow t="Динамика фудкоста" hint="факт (красный) vs норма" />
        <Chart html={slope([{ c: 'var(--red)', v: data.trend.map((t) => t.fact ?? 0) }, { c: 'var(--mute)', v: data.trend.map((t) => t.norm) }])} />
        <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
          {data.trend.map((t) => {
            const tf = t.fact ?? 0
            const c = tf <= t.norm ? 'var(--grn)' : tf <= t.norm + 5 ? 'var(--amb)' : 'var(--red)'
            return (
              <span key={t.month} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12.5 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, boxShadow: `0 0 7px ${c}`, flex: 'none' }} />
                <span style={{ color: 'var(--lo)' }}>{t.month}</span>
                <span className="tnum" style={{ fontWeight: 700, color: c }}>{tf}%</span>
              </span>
            )
          })}
        </div>
      </Glass>
      <Glass span={12}>
        <Eyebrow t="Блюда по себестоимости" hint="фудкост > 35% — сигнал маржи" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Блюдо</th><th style={{ ...th, textAlign: 'right' }}>Выручка</th><th style={{ ...th, textAlign: 'right' }}>Продаж</th><th style={{ ...th, width: '30%' }}>Фудкост vs норма</th><th style={{ ...th, textAlign: 'right' }}>%</th></tr></thead>
          <tbody>{dishes.map((x, i) => (
            <tr key={i}><td style={td}>{x.name}<Sub>{x.category}</Sub></td><td style={tdR}>{tenge(x.revenue)}</td><td style={tdR}>{x.count}</td>
              <td style={td}><Bullet value={x.cost_pct} target={data.norm_pct} max={80} tone={toneOf(x.cost_pct)} /></td>
              <td style={{ ...tdR, color: x.cost_pct >= 45 ? 'var(--red)' : x.cost_pct >= 35 ? 'var(--amb)' : 'var(--grn)' }}>{x.cost_pct}%</td></tr>
          ))}</tbody>
        </table>
      </Glass>
    </>
  )
}

/* ─────────── Расходы — живой /api/expenses ─────────── */
type ExpT = {
  total: number
  operations: { name: string; cat: string; date: string; kassa: string; amount: number }[]
  categories: { name: string; amount: number }[]
}
export function Exp() {
  const { data, error, loading } = useApi<ExpT>('expenses')
  if (loading) return <Note loading>Собираю расходы…</Note>
  if (error || !data) return <Note>Не удалось загрузить: {error}</Note>
  const cats = [...data.categories].sort((a, b) => b.amount - a.amount)
  const dseg = cats.map((c, i) => ({ l: c.name, v: c.amount, c: color(i) }))
  const top = cats[0]
  const ops = [...data.operations].sort((a, b) => b.amount - a.amount)
  return (
    <>
      <Glass span={5} spot warp>
        <Hero eyebrow={`Расходы · ${periodLabel()}`} value={<>{tenge(data.total)}</>} sub="сумма операций за период" />
        <div style={{ marginTop: 20 }}>
          <Stat k="Крупнейшая статья" v={<>{top?.name ?? '—'} · {tenge(top?.amount ?? 0)}</>} />
          <Stat k="Статей расходов" v={cats.length} />
          <Stat k="Операций" v={data.operations.length} />
        </div>
      </Glass>
      <Glass span={7}>
        <Eyebrow t="Структура расходов" hint="доля по статьям" />
        <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
          <div style={{ flex: '0 0 200px' }}><Chart html={donut(dseg, { label: tenge(data.total), sub: 'всего' })} style={{ marginTop: 0 }} /></div>
          <div style={{ flex: 1, minWidth: 180 }}><Chart html={legend(cats.map((c, i) => ({ l: c.name, v: tenge(c.amount), c: color(i) })))} style={{ marginTop: 0 }} /></div>
        </div>
      </Glass>
      <Glass span={5}>
        <Eyebrow t="Статьи расходов" hint="доля бюджета" />
        <Chart html={hbar(cats.map((c) => ({ l: c.name, v: c.amount, c: bar(0) })), ' ₸')} />
      </Glass>
      <Glass span={7}>
        <Eyebrow t="Операции" hint="крупнейшие сначала" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Операция</th><th style={th}>Касса</th><th style={{ ...th, textAlign: 'right' }}>Дата</th><th style={{ ...th, textAlign: 'right' }}>Сумма</th></tr></thead>
          <tbody>{ops.map((x, i) => (
            <tr key={i}><td style={td}>{x.name}<Sub>{x.cat}</Sub></td><td style={{ ...td, color: 'var(--lo)' }}>{x.kassa}</td><td style={tdR}>{x.date}</td><td style={tdR}>{tenge(x.amount)}</td></tr>
          ))}</tbody>
        </table>
      </Glass>
    </>
  )
}

/* ─────────── Накладные и закупки ─────────── */
export function Inv() {
  const drift = [{ c: 'var(--red)', v: [100, 104, 111, 118] }, { c: 'var(--amb)', v: [100, 102, 106, 109] }, { c: 'var(--lo)', v: [100, 101, 103, 104] }, { c: 'var(--grn)', v: [100, 98, 96, 94] }]
  const top = [['Говядина', '3 520 ₸/кг', '+18%', -1, 'Мал Онимдери'], ['Масло сливочное', '2 940 ₸/кг', '+9%', -1, 'Фудмастер'], ['Мука в/с', '168 ₸/кг', '+4%', 0, 'Цесна'], ['Овощи (сезон)', '340 ₸/кг', '−6%', 1, 'Базар Опт'], ['Курица', '1 780 ₸/кг', '+2%', 0, 'Усть Куш']] as const
  const varc = [['Масло сливочное', 'Фудмастер', '2 940 ₸', 'Магнум', '3 380 ₸', '15%'], ['Говядина', 'Мал Онимдери', '3 520 ₸', 'Городской', '3 910 ₸', '11%'], ['Рис', 'Цесна', '520 ₸', 'Small', '470 ₸', '−10%']] as const
  return (
    <>
      <Glass span={5} spot>
        <Hero eyebrow="Накладные и закупки" hint="142 накладные" value={<>9,1 млн ₸ <Delta v={7.2} invert /></>}
          note="Закуп растёт быстрее выручки. Драйвер — говядина +18% за 3 недели. По трём позициям тот же товар дешевле у другого поставщика." demo="поля API проверяются — /api/price-stats" />
      </Glass>
      <Glass span={7}>
        <Eyebrow t="Дрейф цен закупа" hint="индекс, 4 недели, 100 = старт" />
        <Chart html={slope(drift)} />
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 11, color: 'var(--lo)', marginTop: 12 }}>
          {[['var(--red)', 'Говядина +18%'], ['var(--amb)', 'Масло +9%'], ['var(--lo)', 'Мука +4%'], ['var(--grn)', 'Овощи −6%']].map((l, i) => <span key={i}><i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: l[0], marginRight: 6, verticalAlign: -1 }} />{l[1]}</span>)}
        </div>
      </Glass>
      <Glass span={6}>
        <Eyebrow t="Топ закупок" hint="цена и дрейф" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Продукт</th><th style={{ ...th, textAlign: 'right' }}>Цена</th><th style={{ ...th, textAlign: 'right' }}>Δ 3 нед</th></tr></thead>
          <tbody>{top.map((x, i) => (
            <tr key={i}><td style={td}>{x[0]}<Sub>{x[4]}</Sub></td><td style={tdR}>{x[1]}</td>
              <td style={{ ...tdR, color: x[3] === 1 ? 'var(--grn)' : x[3] === -1 ? 'var(--red)' : 'var(--mute)' }}>{x[2]}</td></tr>
          ))}</tbody>
        </table>
      </Glass>
      <Glass span={6}>
        <Eyebrow t="Разброс цен" hint="один товар — разные поставщики" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Товар</th><th style={{ ...th, textAlign: 'right' }}>Дешевле</th><th style={{ ...th, textAlign: 'right' }}>Дороже</th><th style={{ ...th, textAlign: 'right' }}>Δ</th></tr></thead>
          <tbody>{varc.map((x, i) => (
            <tr key={i}><td style={td}>{x[0]}</td><td style={{ ...tdR, color: 'var(--grn)' }}>{x[2]}<Sub>{x[1]}</Sub></td><td style={tdR}>{x[4]}<Sub>{x[3]}</Sub></td><td style={tdR}>{x[5]}</td></tr>
          ))}</tbody>
        </table>
      </Glass>
    </>
  )
}

/* ─────────── Смены и персонал — живые /api/shifts · couriers · load-heatmap ─────────── */
type ShiftsT = { staff: { name: string; since: string; revenue: number; orders: number }[] }
type CourT = { couriers: { name: string; deliveries: number; avg_way: number; avg_delay: number; late_pct: number }[]; summary: { deliveries: number; couriers: number; avg_way: number; avg_delay: number; late_pct: number } }
type HeatT = { cells: { d: number; h: number; orders: number }[]; max: number; peak: { d: number; h: number; orders: number } }
export function Staff() {
  const sh = useApi<ShiftsT>('shifts')
  const co = useApi<CourT>('couriers')
  // Период задан явно: «час-пик по дням недели» имеет смысл только на длинном
  // отрезке. Раньше карточка молча слушалась глобального фильтра, которого на
  // этом экране нет — и при фильтре «Вчера» шесть дней из семи показывали «—».
  const hm = useApi<HeatT>('load-heatmap', { period: 'month' })
  if (sh.loading || co.loading || hm.loading) return <Note loading>Собираю смены и загрузку…</Note>
  if (sh.error || !sh.data) return <Note>Не удалось загрузить: {sh.error}</Note>
  const staff = [...sh.data.staff].sort((a, b) => b.revenue - a.revenue)
  const maxRev = Math.max(...staff.map((s) => s.revenue), 1)
  // час-пик ПО КАЖДОМУ ДНЮ НЕДЕЛИ: пиковый час и его заказы для каждого дня
  const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
  const perDay = WD.map((wd, d) => {
    const dayCells = (hm.data?.cells || []).filter((c) => c.d === d)
    const pk = dayCells.reduce<{ h: number; orders: number } | null>((m, c) => (c.orders > (m?.orders ?? -1) ? c : m), null)
    return { wd, peakH: pk?.h, peakOrders: pk?.orders ?? 0 }
  })
  const maxDayPeak = Math.max(1, ...perDay.map((x) => x.peakOrders))
  const sum = co.data?.summary
  return (
    <>
      <Glass span={5} spot>
        {/* у филиала может не быть ни одного официанта с выручкой (staff пустой) —
            обращение к staff[length-1] роняло ВЕСЬ экран, а вместе с ним и
            ИИ-ассистента: React снимает всё дерево при выбросе в рендере */}
        <Hero eyebrow="Смены и персонал" hint={periodLabel()} value={<span style={{ fontSize: 34 }}>{staff.length} официанта · {sum?.couriers ?? 0} курьера</span>}
          note={hm.data
            ? `Пик — ${hm.data.peak.h}:00, ${hm.data.peak.orders} заказов.`
              + (staff.length ? ` Разброс по официантам — ${Math.round((1 - staff[staff.length - 1].revenue / maxRev) * 100)}%.` : '')
            : 'Смены за период.'} />
      </Glass>
      <Glass span={7}>
        <Eyebrow t="Час-пик по дням недели" hint="за месяц · пиковый час каждого дня · когда ставить больше людей" />
        {hm.data?.cells?.length ? (
          <div style={{
            display: 'grid',
            // Семь жёстких колонок не ужимались ниже «12:00» и распирали страницу
            // на телефоне в горизонтальную прокрутку. auto-fit — семь в ряд на
            // широком экране, 4+3 на узком.
            gridTemplateColumns: 'repeat(auto-fit,minmax(74px,1fr))', gap: 8, marginTop: 12,
          }}>
            {perDay.map((x) => {
              const c = x.peakOrders >= maxDayPeak * 0.85 ? 'var(--red)' : x.peakOrders >= maxDayPeak * 0.6 ? 'var(--amb)' : 'var(--hi)'
              return (
                <div key={x.wd} style={{ textAlign: 'center', padding: '10px 4px', borderRadius: 12, background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
                  <div style={{ fontSize: 11, color: 'var(--mute)' }}>{x.wd}</div>
                  <div className="tnum" style={{ fontSize: 15, fontWeight: 700, marginTop: 4, color: c }}>{x.peakH != null ? `${x.peakH}:00` : '—'}</div>
                  <div className="tnum" style={{ fontSize: 11, color: 'var(--mute)', marginTop: 2 }}>{x.peakOrders} зак.</div>
                </div>
              )
            })}
          </div>
        ) : <div style={{ fontSize: 13, color: 'var(--mute)', marginTop: 10 }}>нет данных по часам</div>}
      </Glass>
      <Glass span={7}>
        <Eyebrow t="Официанты" hint="выручка · заказы · с начала смены" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Имя</th><th style={{ ...th, width: '34%' }}>Выручка</th><th style={{ ...th, textAlign: 'right' }}>Заказов</th><th style={{ ...th, textAlign: 'right' }}>С</th></tr></thead>
          {/* сумма цифрой: одна полоса без числа не даёт ответа «сколько», а
              угадывать выручку по длине бара владелец не должен */}
          <tbody>{staff.map((x, i) => (
            <tr key={i}><td style={td}>{x.name}</td>
              <td style={td}>
                <div className="tnum" style={{ fontSize: 13, fontWeight: 600, color: 'var(--hi)' }}>{tenge(x.revenue)}</div>
                <div style={{ marginTop: 5 }}><Bullet value={x.revenue} max={maxRev} /></div>
              </td>
              <td style={tdR}>{x.orders}</td><td style={tdR}>{x.since}</td></tr>
          ))}</tbody>
        </table>
      </Glass>
      <Glass span={5}>
        <Eyebrow t="Курьеры" hint="доставки · опоздания" />
        {sum && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 6 }}>
            <Light tone="green" k="Доставок за период" v={fmt(sum.deliveries)} />
            <Light tone="amber" k="Среднее время" v={`${sum.avg_way} мин`} />
            <Light tone={sum.late_pct > 10 ? 'red' : 'green'} k="Опозданий" v={`${sum.late_pct}%`} />
          </div>
        )}
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 14 }}>
          <thead><tr><th style={th}>Курьер</th><th style={{ ...th, textAlign: 'right' }}>Доставок</th><th style={{ ...th, textAlign: 'right' }}>Опозд.</th></tr></thead>
          <tbody>{(co.data?.couriers ?? []).map((c, i) => (
            <tr key={i}><td style={td}>{c.name}</td><td style={tdR}>{c.deliveries}</td><td style={{ ...tdR, color: c.late_pct > 10 ? 'var(--red)' : 'var(--lo)' }}>{c.late_pct}%</td></tr>
          ))}</tbody>
        </table>
      </Glass>
      {/* опоздания по доставкам выбранного филиала — из OLAP «Заказы-доставка» */}
      <LateDeliveries />
      {/* наш КДС: зоны кухни по часам + зоны доставки (один филиал — CHASPIK_ORG_ID) */}
      <Kds />
    </>
  )
}

/* ─────────── Аномалии ─────────── */
export function Anom() {
  const a = [['Скидки', 'Скидка 30% пробита 9 раз одной картой сотрудника', '−94 000 ₸', 'neg', 'Вторник, смена 14:00–22:00. Средняя скидка по залу 4%, здесь — 30% на полный чек. Проверить основание.'], ['Возвраты', '3 возврата подряд после закрытия чека, стол 12', '−38 000 ₸', 'warn', '21:40–21:52, один официант. Возврат после оплаты — редкий паттерн. Записи камеры за слот.'], ['Касса', 'Расхождение по кассе +12 400 ₸ — излишек', '+12 400 ₸', 'pos', 'Излишек, не недостача. Скорее непробитый возврат. Сверить Z-отчёт со сменой.']] as const
  return (
    <>
      <Glass span={5} spot>
        <Hero eyebrow="Аномалии" hint="отклонения за месяц" value={<>3 события <Delta v={-25} /></>}
          note="Меньше, чем в прошлом месяце. Крупнейшее — серия 30% скидок с одной карты. Тап раскрывает контекст." />
      </Glass>
      <Glass span={7}>
        <Eyebrow t="События" hint="тап → детали" />
        <div style={{ marginTop: 6 }}>{a.map((x, i) => <Insight key={i} tag={x[0]} title={x[1]} money={x[2]} tone={x[3] as 'neg' | 'warn' | 'pos'} detail={x[4]} />)}</div>
      </Glass>
    </>
  )
}

/* ─────────── Контент и соцсети ─────────── */
export function Soc() {
  const spT = [2, 3, 5, 4, 6, 9, 8, 12, 15, 14].map((v) => ({ l: '', v }))
  const kpi = [['Подписчики', '18,4 тыс', 3.1], ['Охват / мес', '1,2 млн', 22.0], ['Вовлечённость', '4,2%', 0.6], ['Переходов в меню', '2 140', 8.0]] as const
  const posts = [['Reels: раздача плова', 'Instagram', '142 тыс', '4,8%'], ['Хинкали крупным планом', 'TikTok', '310 тыс', '6,1%'], ['Отзыв гостя', 'Instagram', '54 тыс', '3,2%'], ['Кухня в 7 утра', 'TikTok', '98 тыс', '5,4%']] as const
  return (
    <>
      <Glass span={5} spot>
        <Hero eyebrow="Контент и соцсети" hint="Instagram · TikTok · 30 дней" value={<>4,2% <Delta v={0.6} /></>} sub="средняя вовлечённость за 30 дней"
          note="TikTok растёт вдвое быстрее Instagram по охвату. Еда крупным планом собирает больше, чем постановочные кадры." demo="источник не подключён — данные примерные" />
      </Glass>
      <Glass span={7}>
        <Eyebrow t="TikTok — охват" hint="10 недель" />
        <Chart html={areaChart(spT, 'var(--grn)')} />
      </Glass>
      {kpi.map((k, i) => (
        <Glass key={i} span={3} tap>
          <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>{k[0]}</div>
          <div className="tnum" style={{ fontSize: 26, fontWeight: 660, letterSpacing: '-.02em', marginTop: 7, display: 'flex', alignItems: 'baseline', gap: 8 }}>{k[1]}<Delta v={k[2]} size={12} /></div>
        </Glass>
      ))}
      <Glass span={12}>
        <Eyebrow t="Топ-посты" hint="охват и вовлечённость" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Пост</th><th style={th}>Площадка</th><th style={{ ...th, textAlign: 'right' }}>Охват</th><th style={{ ...th, textAlign: 'right' }}>Вовл.</th></tr></thead>
          <tbody>{posts.map((p, i) => (
            <tr key={i}><td style={td}>{p[0]}</td><td style={{ ...td, color: 'var(--lo)' }}>{p[1]}</td><td style={tdR}>{p[2]}</td><td style={tdR}>{p[3]}</td></tr>
          ))}</tbody>
        </table>
      </Glass>
    </>
  )
}
