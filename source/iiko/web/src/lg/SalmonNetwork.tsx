import { useCallback, useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { Glass } from './ui'
import { tenge } from '../lib/format'
import type { Scope } from './salmonKit'
import { Eyebrow, Segmented, field, iso, kg, pc, periodBounds, td, tdR, th, wasteTone } from './salmonKit'

/* Публичная таблица по сети: где нарезка дешевле и где меньше уходит в отход.

   Числа открыты всем управляющим и филиалы названы — иначе сравнивать не с чем,
   а «ориентироваться» и значит увидеть, что у соседа из того же стейка выходит
   на два процента больше нарезки. Порядок задаёт сервер: наверху меньший отход. */

type Row = {
  organization_id: string; name: string; mine: boolean
  acts: number; raw: number; slice: number; trim: number; waste: number
  slice_price: number; slice_pct: number; trim_pct: number; waste_pct: number
}
type Data = { period?: { from: string; to: string }; branches?: Row[]; total?: Row; error?: string }

export default function SalmonNetwork({ token, kind, reload }: {
  token: string; kind: string; reload: number
}) {
  const [scope, setScope] = useState<Scope>('month')
  const now = new Date()
  const [from, setFrom] = useState(iso(new Date(now.getFullYear(), now.getMonth(), 1)))
  const [to, setTo] = useState(iso(now))
  const [d, setD] = useState<Data | null>(null)
  const still = useReducedMotion()

  const load = useCallback(() => {
    const b = periodBounds(scope, from, to)
    fetch(`/api/acts/${kind}/${token}/network?${new URLSearchParams({ date_from: b.from, date_to: b.to })}`)
      .then((r) => r.json()).then(setD).catch((e) => setD({ error: String(e.message || e) }))
  }, [token, kind, scope, from, to])

  useEffect(load, [load, reload])

  const rows = d?.branches || []
  const worst = Math.max(10, ...rows.map((r) => r.waste_pct))
  const cheapest = Math.min(...rows.filter((r) => r.slice_price > 0).map((r) => r.slice_price))

  return (
    <Glass span={12}>
      <Eyebrow t={kind === 'eel' ? 'Угорь по сети' : 'Разбор по сети'} hint="открыто всем филиалам" />

      <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <Segmented id="salmon-net-scope" value={scope} onChange={setScope} options={[
          { k: 'month', t: 'Этот месяц' },
          { k: 'prev', t: 'Прошлый' },
          { k: 'dates', t: 'Свои даты' },
        ]} />
        {scope === 'dates' && (
          <>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} style={{ ...field, width: 'auto' }} />
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} style={{ ...field, width: 'auto' }} />
          </>
        )}
      </div>

      {rows.length === 0 ? (
        <div style={{ marginTop: 14, fontSize: 13, color: 'var(--mute)' }}>
          За этот период разборов не было ни в одном филиале.
        </div>
      ) : (
        <>
          <div style={{ marginTop: 14, overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 520, borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={th}>Филиал</th>
                <th style={{ ...th, textAlign: 'right' }}>Вход, кг</th>
                <th style={{ ...th, textAlign: 'right' }}>Нарезка ₸/кг</th>
                <th style={{ ...th, textAlign: 'right', width: '34%' }}>Отход</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.organization_id}>
                    <td style={{ ...td, fontWeight: r.mine ? 700 : 400, color: r.mine ? 'var(--hi)' : 'var(--lo)' }}>
                      {r.name}
                      {r.mine && <span style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--acc)', fontWeight: 700 }}>ВЫ</span>}
                      {i === 0 && rows.length > 1 && (
                        <span style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--grn)' }}>меньше всех отхода</span>
                      )}
                    </td>
                    <td style={tdR}>{kg(r.raw)}</td>
                    <td style={{ ...tdR, color: r.slice_price === cheapest ? 'var(--grn)' : undefined, fontWeight: r.slice_price === cheapest ? 700 : 400 }}>
                      {tenge(r.slice_price)}
                    </td>
                    <td style={tdR}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 9, justifyContent: 'flex-end' }}>
                        <span className="sal-bar">
                          <motion.span
                            initial={still ? false : { width: 0 }}
                            animate={{ width: `${Math.min(100, r.waste_pct / worst * 100)}%` }}
                            transition={{ type: 'spring', bounce: 0, duration: 0.5 }}
                            style={{ position: 'absolute', inset: 0, borderRadius: 4, background: wasteTone(r.waste_pct) }} />
                        </span>
                        <b style={{ color: wasteTone(r.waste_pct), minWidth: 46, textAlign: 'right' }}>{pc(r.waste_pct)}</b>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {d?.total && (
            <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--mute)' }}>
              По сети за период: входа <b style={{ color: 'var(--hi)' }}>{kg(d.total.raw)} кг</b>,
              нарезка <b style={{ color: 'var(--hi)' }}>{tenge(d.total.slice_price)}/кг</b>,
              средний отход <b style={{ color: wasteTone(d.total.waste_pct) }}>{pc(d.total.waste_pct)}</b>.
            </div>
          )}
        </>
      )}
    </Glass>
  )
}
