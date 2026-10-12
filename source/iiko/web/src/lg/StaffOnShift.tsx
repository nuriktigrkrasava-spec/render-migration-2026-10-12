import { useCallback, useEffect, useState } from 'react'
import { Glass } from './ui'
import { useOrg } from '../lib/useApi'

/* Кто на смене и кто опоздал.

   Факт прихода — из явок iiko (терминал), сотрудник ничего не отмечает.
   Эталон гибридный: время старта смены филиала, а если на человека стоит
   график — график. Допуск, чтобы «опоздал на 3 минуты» не всплывало каждый день.

   Число опоздавших уходит в оценку филиала на «Горе», поэтому важно, что
   филиал без заданного старта не выглядит идеальным: такие явки помечены
   «вне графика» и в знаменатель не идут. */

type Row = {
  employee_id: string; name: string; date: string
  arrival: string; planned: string | null; late_min: number
  status: 'late' | 'ontime' | 'noplan'
  /* явка ещё не закрыта — человек прямо сейчас на смене */
  open?: boolean
}
type Summary = { total: number; late: number; late_pct: number | null; late_minutes: number; noplan: number }
type Data = {
  available: boolean; reason?: string; rows: Row[]; summary: Summary
  starts?: string[]; grace_min?: number; configured?: boolean
}

const STATUS: Record<Row['status'], { t: string; c: string }> = {
  ontime: { t: 'вовремя', c: 'var(--grn)' },
  late: { t: 'опоздание', c: 'var(--red)' },
  noplan: { t: 'вне графика', c: 'var(--mute)' },
}

const th: React.CSSProperties = {
  fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)',
  fontWeight: 600, textAlign: 'left', padding: '0 0 9px', borderBottom: '1px solid var(--hair)',
}
const td: React.CSSProperties = { padding: '10px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
const inp: React.CSSProperties = {
  width: 74, fontSize: 13, fontWeight: 650, textAlign: 'center', padding: '5px 7px', borderRadius: 9,
  border: '1px solid var(--hair)', background: 'var(--glass-2)', color: 'var(--hi)', fontFamily: 'inherit',
}
const btn: React.CSSProperties = {
  fontSize: 12, fontWeight: 650, color: 'var(--acc)', background: 'transparent',
  border: '1px solid var(--hair)', borderRadius: 9, padding: '5px 10px', cursor: 'pointer', fontFamily: 'inherit',
}

export default function StaffOnShift({ date }: { date: string }) {
  const [data, setData] = useState<Data | null>(null)
  const [month, setMonth] = useState<Summary | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ morning: '09:00', evening: '16:00', grace: 10 })
  const [allBranches, setAllBranches] = useState(true)
  const org = useOrg()

  const load = useCallback(() => {
    if (!org) return
    setData(null); setErr(null)
    fetch(`/api/staff-late/${org}?date=${date}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: Data) => {
        setData(d)
        const s = d.starts || []
        setForm({ morning: s[0] || '09:00', evening: s[1] || '', grace: d.grace_min ?? 10 })
      })
      .catch((e) => setErr(String(e.message || e)))
    // Месяц рядом с днём — обязательно: в оценку филиала идёт МЕСЯЧНАЯ доля
    // опоздавших, и без неё экран «0 опоздали» противоречил нулю в оценке.
    const m0 = date.slice(0, 8) + '01'
    fetch(`/api/staff-late/${org}?date=${m0}&to=${date}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('month'))))
      .then((d: Data) => setMonth(d.summary))
      .catch(() => setMonth(null))
  }, [org, date])

  useEffect(load, [load])

  const saveStarts = async () => {
    const starts = [{ kind: 'morning', time: form.morning }]
    if (form.evening) starts.push({ kind: 'evening', time: form.evening })
    try {
      await fetch(`/api/shift-starts/${org}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ starts, grace_min: form.grace, all_branches: allBranches }),
      })
    } catch (e) { console.warn('shift starts', e) }
    setEditing(false)
    load()
  }

  const s = data?.summary
  const rows = data?.rows || []

  return (
    <Glass span={12}>
      <div className="eyebrow">
        <span>Кто на смене</span>
        <span className="hint">приход из явок iiko · опоздания идут в оценку филиала</span>
      </div>

      {err && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--red)' }}>{err}</div>}
      {!err && !data && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--mute)' }}>Смотрю явки…</div>}
      {data && !data.available && (
        <div style={{ marginTop: 12, fontSize: 13, color: 'var(--mute)' }}>{data.reason}</div>
      )}

      {data?.available && (
        <>
          <div style={{ marginTop: 12, display: 'flex', gap: 14, alignItems: 'baseline', flexWrap: 'wrap' }}>
            {s && s.total > 0 ? (
              <span className="tnum" style={{ fontSize: 20, fontWeight: 760, letterSpacing: '-.02em', color: s.late ? 'var(--red)' : 'var(--grn)' }}>
                {s.late}
                <span style={{ fontSize: 12, color: 'var(--mute)', fontWeight: 600 }}> опоздали из {s.total}</span>
                {s.late > 0 && (
                  <span style={{ fontSize: 12, color: 'var(--mute)', fontWeight: 600 }}> · {s.late_minutes} мин суммарно</span>
                )}
              </span>
            ) : rows.length ? (
              /* Люди отметились, но ни один приход не лёг ни на один старт смены:
                 «явок нет» тут было бы враньём — явки есть, эталона нет. */
              <span style={{ fontSize: 13, color: 'var(--mute)' }}>
                Отметились {rows.length} — все вне графика, опоздания по ним не считаем.
              </span>
            ) : (
              <span style={{ fontSize: 13, color: 'var(--mute)' }}>
                За этот день явок нет{date >= new Date().toISOString().slice(0, 10) ? ' — ещё никто не отметился на терминале' : ''}.
              </span>
            )}
            {!!s?.noplan && (
              <span style={{ fontSize: 11.5, color: 'var(--amb)' }}>{s.noplan} вне графика — не в счёт</span>
            )}
          </div>

          {/* Именно эта строка идёт в оценку филиала. Без неё день «0 опоздали»
              и ноль баллов за опоздания выглядели враньём. */}
          {month && month.total > 0 && (
            <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>
              За месяц: <b className="tnum" style={{ color: month.late ? 'var(--red)' : 'var(--grn)' }}>
                {month.late} из {month.total}</b>
              {month.late_pct != null && <span> · {month.late_pct}%</span>}
              <span style={{ color: 'var(--mute)' }}> — это и идёт в оценку филиала</span>
            </div>
          )}

          {/* Эталон: без него опоздание не вычислить, поэтому он на виду, а не в настройках */}
          <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 12.5, color: 'var(--mute)' }}>
            {editing ? (
              <>
                <span>Старт смен:</span>
                <input style={inp} value={form.morning} placeholder="09:00"
                  onChange={(e) => setForm((f) => ({ ...f, morning: e.target.value }))} />
                <input style={inp} value={form.evening} placeholder="16:00"
                  onChange={(e) => setForm((f) => ({ ...f, evening: e.target.value }))} />
                <span>допуск</span>
                <input style={{ ...inp, width: 54 }} type="number" min={0} value={form.grace}
                  onChange={(e) => setForm((f) => ({ ...f, grace: Math.max(0, +e.target.value || 0) }))} />
                <span>мин</span>
                <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                  <input type="checkbox" checked={allBranches} onChange={(e) => setAllBranches(e.target.checked)} />
                  <span>на все филиалы</span>
                </label>
                <button style={{ ...btn, color: 'var(--on-acc)', background: 'var(--acc)', border: 'none' }} onClick={saveStarts}>Сохранить</button>
                <button style={btn} onClick={() => setEditing(false)}>Отмена</button>
              </>
            ) : (
              <>
                <span>
                  Старт смен: <b style={{ color: 'var(--hi)' }}>{(data.starts || []).join(' и ') || '—'}</b>
                  {' '}· допуск {data.grace_min} мин
                  {!data.configured && <span style={{ color: 'var(--amb)' }}> · по умолчанию, филиал не настроен</span>}
                </span>
                <button style={btn} onClick={() => setEditing(true)}>Изменить</button>
              </>
            )}
          </div>

          {/* Таблица уезжает вбок, а не сжимается: имена сотрудников длинные,
              и на телефоне четыре колонки схлопывались в кашу. Как в «Плане». */}
          {!!rows.length && (
            <div style={{ overflowX: 'auto', minWidth: 0, marginTop: 14 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 420 }}>
              <thead><tr>
                <th style={th}>Сотрудник</th>
                <th style={{ ...th, textAlign: 'right' }}>Пришёл</th>
                <th style={{ ...th, textAlign: 'right' }}>План</th>
                <th style={{ ...th, textAlign: 'right' }}>Опоздание</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => {
                  const st = STATUS[r.status]
                  return (
                    <tr key={r.employee_id + r.date}>
                      <td style={td}>
                        <div style={{ fontWeight: 650, color: 'var(--hi)' }}>{r.name}</div>
                        <div style={{ fontSize: 11, color: st.c, fontWeight: 650 }}>
                          {st.t}
                          {/* Явка без закрытия = человек ещё на смене. Именно из-за
                              таких строк день раньше выглядел пустым. */}
                          {r.open && <span style={{ color: 'var(--acc)' }}> · на смене</span>}
                        </div>
                      </td>
                      <td className="tnum" style={{ ...td, textAlign: 'right', color: 'var(--hi)', fontWeight: 650 }}>{r.arrival}</td>
                      <td className="tnum" style={{ ...td, textAlign: 'right', color: 'var(--mute)' }}>{r.planned || '—'}</td>
                      <td className="tnum" style={{ ...td, textAlign: 'right', fontWeight: 700, color: r.late_min ? 'var(--red)' : 'var(--mute)' }}>
                        {r.late_min ? `+${r.late_min} мин` : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            </div>
          )}
        </>
      )}
    </Glass>
  )
}
