import { useApi } from '../lib/useApi'
import { Glass, Chart, Bullet, Insight, GBar, PageState as Note } from './ui'
import { barChart, color } from './charts'
import { tenge, num } from '../lib/format'

/* ── Формы данных ── */
type Seg = { count: number; pct: number }
type RfmSeg = { key: string; emoji: string; label: string; count: number; pct: number; revenue: number }
type Clients = {
  segments: {
    total: number
    unique: Seg
    loyal: Seg
    vip: Seg
    repeat_pct: number
    revenue: number
    ltv: number
    loyal_revenue: number
    loyal_potential: number
  }
  top_vip: { phone: string; orders: number; spend: number }[]
  rfm: { total: number; segments: RfmSeg[] }
  source: string
  year: string
}
type Retention = {
  clients: number
  orders: number
  cohorts: { name: string; size: number; jan: number | null; feb: number | null; mar: number | null }[]
  frequency: number[]
}

const th: React.CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 10px', borderBottom: '1px solid var(--hair)' }
const td: React.CSSProperties = { padding: '11px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }

/* иконка RFM-сегмента (корона / сердце / росток / звезда / тревога / сон / призрак) */
const SEG_ICON: Record<string, string> = {
  champions: '<path d="M5 8l3 3 4-6 4 6 3-3-1.5 10h-11z"/><path d="M6 21h12"/>',
  loyal: '<path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2.5A4 4 0 0 1 19 11c0 4.6-7 9-7 9z"/>',
  promising: '<path d="M12 20v-8"/><path d="M12 12c0-3 2-5 5-5 0 3-2 5-5 5z"/><path d="M12 13c0-2.5-1.7-4-4-4 0 2.5 1.7 4 4 4z"/>',
  new: '<path d="M12 3l2.3 4.7 5.2.7-3.8 3.6.9 5.1L12 14.8 7.4 17.2l.9-5.1L4.5 8.4l5.2-.7z"/>',
  promising_new: '<path d="M12 5v14M5 12h14"/>',
  at_risk: '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  hibernating: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.4 6.4 0 0 0 10.5 10.5z"/>',
  lost: '<circle cx="12" cy="12" r="8.5"/><path d="M9 9l6 6M15 9l-6 6"/>',
}
const segPath = (k: string) => SEG_ICON[k] || SEG_ICON[k.replace('-', '_')] || '<circle cx="12" cy="12" r="7"/>'
function SegIcon({ k }: { k: string }) {
  return <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: segPath(k) }} />
}

/* ── Плитка сегмента ── */
function Tile({ label, value, share, c }: { label: string; value: string; share: string; c: string }) {
  return (
    <Glass span={3} tap>
      <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>{label}</div>
      <div className="tnum" style={{ fontSize: 27, fontWeight: 670, letterSpacing: '-.025em', marginTop: 8 }}>{value}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 9 }}>
        <span style={{ width: 8, height: 8, borderRadius: 3, background: c, flex: 'none' }} />
        <span style={{ fontSize: 12, color: 'var(--lo)', fontWeight: 550 }}>{share}</span>
      </div>
    </Glass>
  )
}

export default function Clients() {
  const cl = useApi<Clients>('clients', { period: 'month' })
  const rt = useApi<Retention>('retention', { period: 'month' })
  if (cl.loading || rt.loading) return <Note loading>Считаем клиентскую базу и RFM…</Note>
  if (cl.error || !cl.data || !rt.data) return <Note>Не удалось собрать аналитику по клиентам: {cl.error || rt.error}</Note>

  const s = cl.data.segments
  const rfm = cl.data.rfm
  const rt_ = rt.data

  /* цвет на сегмент — единый для баров и легенды */
  const segColor = (i: number) => color(i)
  const seg = (k: string) => rfm.segments.find((x) => x.key === k)

  const maxSegCount = Math.max(...rfm.segments.map((x) => x.count), 1)

  /* ── потенциалы для идей ── */
  const champ = seg('champions')
  const loyalSeg = seg('loyal')
  const promising = seg('promising')
  const atRiskRev = (seg('at_risk')?.revenue ?? 0) + (seg('hibernating')?.revenue ?? 0) + (seg('lost')?.revenue ?? 0)
  const atRiskCnt = (seg('at_risk')?.count ?? 0) + (seg('hibernating')?.count ?? 0) + (seg('lost')?.count ?? 0)

  /* топ-VIP: шкала под Bullet */
  const maxSpend = Math.max(...cl.data.top_vip.map((v) => v.spend), 1)

  /* частота визитов: 1,2,3,4,5+ */
  const freqLabels = ['1', '2', '3', '4', '5+']
  const freqBars = rt_.frequency.map((v, i) => ({ l: freqLabels[i] ?? `${i + 1}`, v }))
  const freqHi = rt_.frequency.indexOf(Math.max(...rt_.frequency))

  return (
    <>
      {/* 1 · Герой — RFM-сводка базы */}
      <Glass span={5} spot>
        <div className="eyebrow"><span>Клиенты · RFM</span><span className="hint">{cl.data.source} · {cl.data.year}</span></div>
        <div className="tnum" style={{ fontSize: 46, fontWeight: 680, letterSpacing: '-.035em', lineHeight: 1, marginTop: 16 }}>{num(s.total)}</div>
        <div style={{ marginTop: 9, fontSize: 12.5, color: 'var(--mute)', fontWeight: 500 }}>клиентов с телефоном · повторных {s.repeat_pct}%</div>
        <div style={{ display: 'flex', gap: 30, marginTop: 22, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>LTV на клиента</div>
            <div className="tnum" style={{ fontSize: 22, fontWeight: 660, letterSpacing: '-.02em', marginTop: 6 }}>{tenge(s.ltv)}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>Выручка с базы</div>
            <div className="tnum" style={{ fontSize: 22, fontWeight: 660, letterSpacing: '-.02em', marginTop: 6 }}>{tenge(s.revenue)}</div>
          </div>
        </div>
        <p style={{ marginTop: 18, fontSize: 12.5, lineHeight: 1.5, color: 'var(--lo)', maxWidth: '46ch' }}>
          Лояльные ({s.loyal.count}) — ядро: {tenge(s.loyal_revenue)}. Апселл-потенциал ещё {tenge(s.loyal_potential)}.
        </p>
      </Glass>

      {/* 2 · RFM 7 сегментов — иконка · люди · выручка */}
      <Glass span={7}>
        <div className="eyebrow"><span>RFM-сегменты</span><span className="hint">{rfm.total} клиентов · люди и ₸</span></div>
        <div style={{ marginTop: 12 }}>
          {rfm.segments.map((x, i) => {
            const c = segColor(i)
            const w = Math.max(4, (x.count / maxSegCount) * 100)
            return (
              <div key={x.key} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
                <span style={{ width: 32, height: 32, borderRadius: 9, flex: 'none', display: 'grid', placeItems: 'center', color: c, background: `color-mix(in srgb, ${c} 16%, transparent)` }}><SegIcon k={x.key} /></span>
                <div style={{ flex: '0 0 34%', minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--hi)' }}>{x.label}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--mute)' }} className="tnum">{x.count} чел · {x.pct}%</div>
                </div>
                <div style={{ flex: 1 }}>
                  <GBar pct={w} color={c} h={10} />
                </div>
                <div className="tnum" style={{ width: 84, textAlign: 'right', fontSize: 12.5, fontWeight: 650 }}>{tenge(x.revenue)}</div>
              </div>
            )
          })}
        </div>
      </Glass>

      {/* 3 · Плитки сегментов */}
      <Tile label="Уникальные" value={num(s.unique.count)} share={`${s.unique.pct}% базы · 1 визит`} c={color(6)} />
      <Tile label="Лояльные" value={num(s.loyal.count)} share={`${s.loyal.pct}% базы · 2+ визита`} c={color(3)} />
      <Tile label="VIP" value={num(s.vip.count)} share={`${s.vip.pct}% базы · ядро`} c={color(4)} />
      <Tile label="Повторные" value={`${s.repeat_pct}%`} share="доля вернувшихся" c={color(0)} />

      {/* 4 · Топ-VIP */}
      <Glass span={7}>
        <div className="eyebrow"><span>Топ-VIP клиенты</span><span className="hint">заказы · сумма · доля от лидера</span></div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr>
            <th style={th}>Телефон</th>
            <th style={{ ...th, textAlign: 'right' }}>Заказы</th>
            <th style={{ ...th, width: '30%' }}>Сумма</th>
            <th style={{ ...th, textAlign: 'right' }}>₸</th>
          </tr></thead>
          <tbody>{cl.data.top_vip.map((v, i) => (
            <tr key={i}>
              <td style={td} className="tnum">{v.phone}</td>
              <td style={tdR}>{v.orders}</td>
              <td style={td}><Bullet value={v.spend} max={maxSpend} tone="acc" /></td>
              <td style={tdR}>{tenge(v.spend)}</td>
            </tr>
          ))}</tbody>
        </table>
      </Glass>

      {/* 5 · Идеи по RFM — каждая с ₸/действием */}
      <Glass span={5}>
        <div className="eyebrow"><span>Идеи по RFM</span><span className="hint">тап → детали</span></div>
        <div style={{ marginTop: 6 }}>
          <Insight
            tag="Champions"
            title={`Реферал для ${champ?.count ?? 0} чемпионов`}
            money={tenge(champ?.revenue ?? 0)}
            tone="pos"
            detail={`Ваши адвокаты. Бонус «приведи друга»: 1 гость ≈ +${tenge(s.ltv)} LTV.`}
          />
          <Insight
            tag="Лояльные"
            title={`VIP-апселл ${loyalSeg?.count ?? 0} лояльным`}
            money={tenge(s.loyal_potential)}
            tone="pos"
            detail={`Уже дали ${tenge(s.loyal_revenue)}. Клубная карта и сет-апселл поднимут чек: +${tenge(s.loyal_potential)}.`}
          />
          <Insight
            tag="В зоне риска"
            title={`Win-back ${atRiskCnt} уходящих`}
            money={tenge(atRiskRev)}
            tone="warn"
            detail={`Уходящие тратили ${tenge(atRiskRev)}. Оффер −15% на 7 дней; цель вернуть треть = ${tenge(atRiskRev / 3)}.`}
          />
          <Insight
            tag="Перспективные"
            title={`2-й визит за 14 дней · ${promising?.count ?? 0} новых`}
            money={tenge(promising?.revenue ?? 0)}
            tone="warn"
            detail={`Пришли недавно. Автопуш «−10% на 2-й заказ» за 14 дней растит их в лояльных — чек ×${(s.loyal_revenue / Math.max(promising?.revenue ?? 1, 1)).toFixed(1)}.`}
          />
        </div>
      </Glass>

      {/* 6 · Retention — когорты + частота визитов */}
      <Glass span={12}>
        <div className="eyebrow"><span>Удержание</span><span className="hint">{num(rt_.clients)} клиентов · {num(rt_.orders)} заказов</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1fr', gap: 28, marginTop: 12, alignItems: 'start' }}>
          {/* когорты */}
          <div>
            <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, marginBottom: 6 }}>Когорты · возврат по месяцам</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={th}>Когорта</th>
                <th style={{ ...th, textAlign: 'right' }}>Размер</th>
                <th style={{ ...th, textAlign: 'right' }}>Янв</th>
                <th style={{ ...th, textAlign: 'right' }}>Фев</th>
                <th style={{ ...th, textAlign: 'right' }}>Мар</th>
              </tr></thead>
              <tbody>{rt_.cohorts.map((cohort, i) => {
                const cell = (v: number | null) => v == null
                  ? <span style={{ color: 'var(--mute)' }}>—</span>
                  : <span style={{ color: v >= 15 ? 'var(--grn)' : v >= 8 ? 'var(--amb)' : 'var(--red)' }}>{v}%</span>
                return (
                  <tr key={i}>
                    <td style={td}>{cohort.name}</td>
                    <td style={tdR}>{num(cohort.size)}</td>
                    <td style={tdR}>{cell(cohort.jan)}</td>
                    <td style={tdR}>{cell(cohort.feb)}</td>
                    <td style={tdR}>{cell(cohort.mar)}</td>
                  </tr>
                )
              })}</tbody>
            </table>
          </div>
          {/* частота визитов */}
          <div>
            <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>Частота визитов</div>
            <Chart html={barChart(freqBars, freqHi, 'var(--acc)')} />
            <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>ось — число визитов клиента</div>
          </div>
        </div>
      </Glass>
    </>
  )
}
