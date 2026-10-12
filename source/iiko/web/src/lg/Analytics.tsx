import { useApi } from '../lib/useApi'
import { periodLabel } from '../lib/api'
import { Glass, Chart, Delta, Orb, PageState as Note } from './ui'
import { barChart } from './charts'
import { tenge } from '../lib/format'

/* ── Формы данных ── */
type Health = { key: string; value: string; status: 'red' | 'yellow' | 'green' }
type Insights = {
  leakage_total: number
  health: Health[]
  headline: string
  insights: { title: string; detail: string; money: string; severity: string; dimension: string }[]
  plan: { action: string; effect: string; priority: number }[]
  ai: boolean
}
type Dash = {
  revenue_today: number; revenue_change: number
  avg_check: number; avg_check_change: number
  guests: number; guests_change: number
  chart_7days: { labels: string[]; data: number[] }
}
type FC = { fact_pct: number; norm_pct: number; diff_pct: number }

const GRAD = 'linear-gradient(90deg,#4f6ef7 0%,#7b5cff 42%,#ff9d4d 78%,#ffd24d 100%)'
const orbTone = (s: Health['status']) => (s === 'red' ? 'red' : s === 'yellow' ? 'amber' : 'green')

/* ─────────── Вариант A · Советник (вывод + потери + план) ─────────── */
export function AIAdvisor() {
  const { data, error, loading } = useApi<Insights>('insights')
  if (loading) return <Note loading>ИИ сводит все данные…</Note>
  if (error || !data) return <Note>Не удалось собрать аналитику: {error}</Note>

  const hColor = (s: Health['status']) => (s === 'red' ? 'var(--red)' : s === 'yellow' ? 'var(--amb)' : 'var(--grn)')
  const greenN = data.health.filter((h) => h.status === 'green').length
  const driver = data.insights[0]?.dimension

  const SEV: Record<string, { c: string; rank: number; tag: string; grew: boolean }> = {
    high: { c: 'var(--red)', rank: 0, tag: 'риск', grew: false },
    medium: { c: 'var(--amb)', rank: 1, tag: 'внимание', grew: false },
    good: { c: 'var(--grn)', rank: 2, tag: 'растёт', grew: true },
  }
  const sev = (s: string) => SEV[s] ?? SEV.medium
  // приоритеты: сначала риски, потом внимание, потом «растёт»
  const ranked = [...data.insights].sort((a, b) => sev(a.severity).rank - sev(b.severity).rank)

  return (
    <>
      {/* ── Брифинг: вывод + утечка + здоровье (одна поверхность) ── */}
      <Glass span={12} spot warp>
        <div className="eyebrow"><span>ИИ-советник · сводка месяца</span>
          {data.ai && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, letterSpacing: 0, textTransform: 'none', color: 'var(--grn)', fontWeight: 600 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--grn)', boxShadow: '0 0 6px var(--grn)' }} />live</span>}
        </div>
        <p className="display" style={{ margin: '16px 0 0', fontSize: 24, lineHeight: 1.42, letterSpacing: '-.015em', color: 'var(--hi)', maxWidth: '60ch' }}>{data.headline}</p>
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 32, marginTop: 26, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: '.09em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 650 }}>Утечка за месяц</div>
            <div className="tnum" style={{ fontSize: 48, fontWeight: 720, letterSpacing: '-.035em', lineHeight: 1, marginTop: 8, color: '#ff8f6b' }}>{tenge(data.leakage_total)}</div>
            {driver && <div style={{ fontSize: 13, color: 'var(--lo)', marginTop: 8 }}>основной источник — {driver.toLowerCase()}</div>}
          </div>
          <div style={{ minWidth: 200 }}>
            <div style={{ fontSize: 11, letterSpacing: '.09em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 650, marginBottom: 10 }}>Здоровье — {greenN} из {data.health.length} в норме</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {data.health.map((h, i) => (
                <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, padding: '5px 10px', borderRadius: 9, background: `color-mix(in srgb, ${hColor(h.status)} 13%, transparent)`, border: `1px solid color-mix(in srgb, ${hColor(h.status)} 30%, transparent)` }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: hColor(h.status), flex: 'none' }} />
                  <span style={{ color: 'var(--lo)' }}>{h.key}</span>
                  <span className="tnum" style={{ fontWeight: 700, color: hColor(h.status) }}>{h.value}</span>
                </span>
              ))}
            </div>
          </div>
        </div>
        <div style={{ height: 8, borderRadius: 6, marginTop: 22, background: GRAD, opacity: 0.92 }} />
      </Glass>

      {/* ── Приоритеты — ранжированный список (что важнее, что растёт) ── */}
      <Glass span={12}>
        <div className="eyebrow"><span>Приоритеты · по важности</span><span className="hint">{data.insights.length} находок</span></div>
        <div style={{ marginTop: 8 }}>
          {ranked.map((x, i) => {
            const m = sev(x.severity)
            return (
              <div key={i} style={{ display: 'flex', gap: 16, padding: '16px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
                <span style={{ flex: 'none', width: 28, height: 28, borderRadius: 9, display: 'grid', placeItems: 'center', fontSize: 13, fontWeight: 700, color: m.c, background: `color-mix(in srgb, ${m.c} 15%, transparent)` }}>{i + 1}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 15.5, fontWeight: 650, color: 'var(--hi)', letterSpacing: '-.01em' }}>{x.title}</span>
                    <span style={{ fontSize: 10.5, letterSpacing: '.06em', textTransform: 'uppercase', fontWeight: 700, color: m.c, background: `color-mix(in srgb, ${m.c} 14%, transparent)`, padding: '2px 8px', borderRadius: 6 }}>{m.tag}</span>
                    <span style={{ fontSize: 12, color: 'var(--mute)' }}>{x.dimension}</span>
                    <span className="tnum" style={{ marginLeft: 'auto', fontSize: 15, fontWeight: 700, color: m.c, whiteSpace: 'nowrap' }}>{m.grew ? '▲ ' : ''}{x.money}</span>
                  </div>
                  <p style={{ margin: '7px 0 0', fontSize: 13.5, lineHeight: 1.55, color: 'var(--lo)', maxWidth: '82ch' }}>{x.detail}</p>
                </div>
              </div>
            )
          })}
        </div>
      </Glass>

      {/* ── План на неделю ── */}
      <Glass span={12}>
        <div className="eyebrow"><span>План на неделю</span><span className="hint">по приоритету · с эффектом в ₸</span></div>
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {data.plan.map((p, i) => (
            <div key={i} style={{ display: 'flex', gap: 14, alignItems: 'center', padding: '14px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
              <span style={{ flex: 'none', width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: 13, fontWeight: 700, background: 'color-mix(in srgb,var(--acc) 20%,transparent)', color: 'var(--acc)' }}>{p.priority}</span>
              <div style={{ flex: 1, minWidth: 0, fontSize: 14.5, lineHeight: 1.5, color: 'var(--hi)' }}>{p.action}</div>
              <span style={{ flex: 'none', fontSize: 12.5, fontWeight: 700, color: 'var(--grn)', background: 'color-mix(in srgb,var(--grn) 14%,transparent)', padding: '6px 12px', borderRadius: 9, whiteSpace: 'nowrap' }}>{p.effect}</span>
            </div>
          ))}
        </div>
      </Glass>
    </>
  )
}

/* ─────────── Вариант B · Обзор (баннер + сетка KPI + график) ─────────── */
function Kpi({ label, value, delta, invert }: { label: string; value: string; delta?: number; invert?: boolean }) {
  return (
    <Glass span={3} tap>
      <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>{label}</div>
      <div className="tnum" style={{ fontSize: 25, fontWeight: 660, letterSpacing: '-.02em', marginTop: 8, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        {value}{delta != null && <Delta v={delta} invert={invert} size={12} />}
      </div>
    </Glass>
  )
}

export function AIOverview() {
  const ins = useApi<Insights>('insights')
  const dash = useApi<Dash>('dashboard', { period: 'month' })
  const fc = useApi<FC>('foodcost', { period: 'month' })
  if (ins.loading || dash.loading || fc.loading) return <Note loading>ИИ сводит все данные…</Note>
  if (ins.error || !ins.data || !dash.data || !fc.data) return <Note>Не удалось собрать аналитику: {ins.error || dash.error || fc.error}</Note>

  const d = dash.data
  const bars = d.chart_7days.labels.map((l, i) => ({ l, v: +(d.chart_7days.data[i] / 1_000_000).toFixed(2) }))
  const hiIdx = d.chart_7days.data.indexOf(Math.max(...d.chart_7days.data))

  return (
    <>
      {/* Баннер с выводом ИИ */}
      <Glass span={12} spot warp>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
          <span style={{ flex: 'none', fontWeight: 700, fontSize: 12, color: 'var(--on-acc)', borderRadius: 11, padding: '8px 11px', lineHeight: 1, background: 'linear-gradient(135deg,var(--acc),var(--acc-2))', boxShadow: '0 6px 18px -6px var(--acc-glow)' }}>ИИ</span>
          <p className="display" style={{ margin: '2px 0 0', fontSize: 20, lineHeight: 1.34, letterSpacing: '-.015em', color: 'var(--hi)' }}>{ins.data.headline}</p>
        </div>
      </Glass>

      {/* KPI-плитки из живого дашборда.
          Ярлык — по фактическому периоду запроса (месяц), а не «сегодня»: поле
          зовётся revenue_today, но содержит сумму запрошенного периода.
          Дельты убраны: бэкенд отдаёт *_change нулями, и вечное «0,0%» читалось
          как «изменений нет», хотя это просто отсутствие расчёта. */}
      <Kpi label={`Выручка · ${periodLabel()}`} value={tenge(d.revenue_today)} />
      <Kpi label="Средний чек" value={tenge(d.avg_check)} />
      <Kpi label="Гостей" value={String(d.guests)} />
      <Kpi label="Фудкост" value={`${fc.data.fact_pct}%`} delta={fc.data.diff_pct} invert />

      {/* Большой график выручки */}
      <Glass span={8}>
        <div className="eyebrow"><span>Выручка · 7 дней</span><span className="hint">млн ₸ · пик выделен</span></div>
        <Chart html={barChart(bars, hiIdx, 'var(--acc)', ' млн')} />
      </Glass>

      {/* Здоровье бизнеса */}
      <Glass span={4}>
        <div className="eyebrow"><span>Здоровье</span></div>
        <div style={{ marginTop: 8 }}>
          {ins.data.health.map((h, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '11px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
              <Orb tone={orbTone(h.status)} />
              <span style={{ fontSize: 13, color: 'var(--lo)' }}>{h.key}</span>
              <span className="tnum" style={{ marginLeft: 'auto', fontSize: 13.5, fontWeight: 650 }}>{h.value}</span>
            </div>
          ))}
        </div>
      </Glass>
    </>
  )
}

/* Новая «Аналитика» = ИИ-советник */
export default function Analytics() {
  return <AIAdvisor />
}
