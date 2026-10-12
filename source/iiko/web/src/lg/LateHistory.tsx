import { useCallback, useEffect, useMemo, useState } from 'react'
import { Glass } from './ui'
import { useOrg } from '../lib/useApi'

/* История опозданий и рейтинг «кто чаще опаздывает».

   Один разовый опоздун на час и человек, который каждый день приходит на
   двадцать минут позже, — разные истории, и вторая дороже. Поэтому сортируем
   по ЧИСЛУ опозданий, а не по минутам, и рядом всегда показываем знаменатель:
   «7 из 12 выходов» честнее, чем «7 опозданий».

   Приход берём из явок iiko (тот же источник, что и «Кто на смене»), период —
   от выбранного дня назад. Дни «вне графика» в знаменатель не идут: филиал без
   заданного старта смены не должен выглядеть идеальным. */

type Emp = {
  employee_id: string; name: string
  days: number; late_days: number; late_pct: number | null
  late_minutes: number; worst_min: number; last_late: string | null; noplan: number
  avg_late_min: number
}
type DayStat = { date: string; total: number; late: number; late_minutes: number }
type Summary = { total: number; late: number; late_pct: number | null; late_minutes: number; noplan: number }
type Data = { available: boolean; reason?: string; summary: Summary; top: Emp[]; by_day: DayStat[] }

const PERIODS = [
  { days: 7, label: '7 дней' },
  { days: 30, label: '30 дней' },
  { days: 90, label: '90 дней' },
]

const th: React.CSSProperties = {
  fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)',
  fontWeight: 600, textAlign: 'left', padding: '0 0 9px', borderBottom: '1px solid var(--hair)',
}
const td: React.CSSProperties = { padding: '10px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const tab = (on: boolean): React.CSSProperties => ({
  fontSize: 12.5, fontWeight: 650, padding: '6px 12px', borderRadius: 10, cursor: 'pointer',
  fontFamily: 'inherit', border: '1px solid ' + (on ? 'transparent' : 'var(--hair)'),
  background: on ? 'var(--acc)' : 'transparent', color: on ? 'var(--on-acc)' : 'var(--mute)',
})

const shiftDay = (d: string, delta: number) => {
  const x = new Date(d + 'T12:00:00')
  x.setDate(x.getDate() + delta)
  return x.toISOString().slice(0, 10)
}
const dm = (d: string) => `${d.slice(8, 10)}.${d.slice(5, 7)}`

export default function LateHistory({ date }: { date: string }) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<Data | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const org = useOrg()
  const from = useMemo(() => shiftDay(date, -(days - 1)), [date, days])

  const load = useCallback(() => {
    if (!org) return
    setData(null); setErr(null)
    fetch(`/api/staff-late/${org}?date=${from}&to=${date}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch((e) => setErr(String(e.message || e)))
  }, [org, from, date])

  useEffect(load, [load])

  const top = (data?.top || []).filter((e) => e.days > 0)
  const late = top.filter((e) => e.late_days > 0)
  const hist = data?.by_day || []
  const worstDay = hist.reduce((m, d) => Math.max(m, d.late), 0)

  return (
    <Glass span={12}>
      <div className="eyebrow">
        <span>Кто чаще опаздывает</span>
        <span className="hint">{dm(from)}–{dm(date)} · приход из явок iiko</span>
      </div>

      <div style={{ marginTop: 12, display: 'flex', gap: 7, flexWrap: 'wrap' }}>
        {PERIODS.map((p) => (
          <button key={p.days} style={tab(p.days === days)} onClick={() => setDays(p.days)}>{p.label}</button>
        ))}
      </div>

      {err && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--red)' }}>{err}</div>}
      {!err && !data && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--mute)' }}>Считаю опоздания…</div>}
      {data && !data.available && (
        <div style={{ marginTop: 12, fontSize: 13, color: 'var(--mute)' }}>{data.reason}</div>
      )}

      {data?.available && (
        <>
          <div style={{ marginTop: 12, display: 'flex', gap: 14, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span className="tnum" style={{ fontSize: 20, fontWeight: 760, letterSpacing: '-.02em', color: data.summary.late ? 'var(--red)' : 'var(--grn)' }}>
              {data.summary.late}
              <span style={{ fontSize: 12, color: 'var(--mute)', fontWeight: 600 }}> опозданий из {data.summary.total} выходов</span>
            </span>
            {data.summary.late_pct != null && (
              <span className="tnum" style={{ fontSize: 12.5, color: 'var(--mute)' }}>{data.summary.late_pct}%</span>
            )}
            {!!data.summary.late_minutes && (
              <span className="tnum" style={{ fontSize: 12.5, color: 'var(--mute)' }}>{data.summary.late_minutes} мин суммарно</span>
            )}
          </div>

          {/* История по дням: видно, это разовый провал или так каждый день */}
          {!!hist.length && (
            <div style={{ overflowX: 'auto', minWidth: 0, marginTop: 14 }}>
              <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 46 }}>
                {hist.map((d) => (
                  <div key={d.date} title={`${d.date}: ${d.late} из ${d.total}, ${d.late_minutes} мин`}
                    style={{
                      flex: '1 0 6px', minWidth: 6, borderRadius: 3,
                      height: Math.max(4, worstDay ? (d.late / worstDay) * 42 : 4),
                      background: d.late ? 'var(--red)' : 'var(--grn)',
                      opacity: d.late ? 0.85 : 0.35,
                    }} />
                ))}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5, fontSize: 10.5, color: 'var(--mute)' }}>
                <span>{dm(hist[0].date)}</span><span>{dm(hist[hist.length - 1].date)}</span>
              </div>
            </div>
          )}

          {!late.length && !!top.length && (
            <div style={{ marginTop: 14, fontSize: 13, color: 'var(--grn)' }}>За период никто не опаздывал.</div>
          )}
          {!top.length && (
            <div style={{ marginTop: 14, fontSize: 13, color: 'var(--mute)' }}>
              За период нет ни одного прихода с эталоном старта — опоздания не считаем.
            </div>
          )}

          {!!late.length && (
            <div style={{ overflowX: 'auto', minWidth: 0, marginTop: 14 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 520 }}>
                <thead><tr>
                  <th style={th}>Сотрудник</th>
                  <th style={{ ...th, textAlign: 'right' }}>Опозданий</th>
                  <th style={{ ...th, textAlign: 'right' }}>Доля</th>
                  <th style={{ ...th, textAlign: 'right' }}>В среднем</th>
                  <th style={{ ...th, textAlign: 'right' }}>Всего</th>
                  <th style={{ ...th, textAlign: 'right' }}>Последнее</th>
                </tr></thead>
                <tbody>
                  {late.map((e) => (
                    <tr key={e.employee_id}>
                      <td style={td}>
                        <div style={{ fontWeight: 650, color: 'var(--hi)' }}>{e.name}</div>
                        {!!e.noplan && (
                          <div style={{ fontSize: 11, color: 'var(--mute)' }}>+{e.noplan} вне графика</div>
                        )}
                      </td>
                      <td style={{ ...tdR, fontWeight: 700, color: 'var(--hi)' }}>
                        {e.late_days}<span style={{ color: 'var(--mute)', fontWeight: 600 }}> из {e.days}</span>
                      </td>
                      <td style={{ ...tdR, color: (e.late_pct || 0) >= 50 ? 'var(--red)' : 'var(--mute)', fontWeight: 650 }}>
                        {e.late_pct != null ? `${e.late_pct}%` : '—'}
                      </td>
                      <td style={tdR}>{e.avg_late_min} мин</td>
                      <td style={{ ...tdR, color: 'var(--hi)', fontWeight: 650 }}>{e.late_minutes} мин</td>
                      <td style={{ ...tdR, color: 'var(--mute)' }}>{e.last_late ? dm(e.last_late) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Пришедшие вовремя — коротко, одной строкой: их не за что разбирать,
              но владелец должен видеть, что они в выборке есть. */}
          {!!top.filter((e) => !e.late_days).length && (
            <div style={{ marginTop: 10, fontSize: 12, color: 'var(--mute)' }}>
              Без опозданий: {top.filter((e) => !e.late_days).map((e) => e.name).join(', ')}
            </div>
          )}
        </>
      )}
    </Glass>
  )
}
