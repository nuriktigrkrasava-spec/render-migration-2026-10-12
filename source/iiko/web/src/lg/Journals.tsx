import { useCallback, useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { Glass } from './ui'

/* Бумажные журналы: температурный, бракеражный, уборки.

   Сами данные бот собирает отдельно (фото зон, замеры камер) — здесь отмечают
   факт заполнения тетради, которую спрашивает проверка. Экран стоит рядом с
   чек-листами: заполняют их одни и те же люди в один заход.

   Галочка ставится сразу, не дожидаясь сервера: отклик через сеть на 300 мс
   превращает отметку в «нажал и не понял, сработало ли». Не прошло — вернём
   обратно и скажем. */

type Def = { key: string; title: string; hint: string }
type Day = { day: string; done: string[] }
type Branch = {
  organization_id: string; name: string
  days: Day[]; today: Record<string, boolean>
}
type Data = {
  role?: 'owner' | 'manager'; journals?: Def[]; slot?: string
  today?: string; days?: string[]; branches?: Branch[]; error?: string
}

function Eyebrow({ t, hint }: { t: string; hint?: string }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}

const dayShort = (iso: string) => {
  const d = new Date(iso + 'T12:00:00')
  return { wd: d.toLocaleDateString('ru-RU', { weekday: 'short' }), n: d.getDate() }
}

/** Точка дня в истории: закрашена — журнал заполнен. */
function Dot({ on, today }: { on: boolean; today: boolean }) {
  return (
    <span style={{
      display: 'block', width: 12, height: 12, borderRadius: '50%',
      background: on ? 'var(--grn)' : 'transparent',
      border: on ? 'none' : `1px solid ${today ? 'var(--acc)' : 'var(--hair)'}`,
      boxShadow: today && on ? '0 0 0 2px var(--acc-glow)' : undefined,
    }} />
  )
}

export default function Journals({ token, org }: { token: string; org?: string }) {
  const [d, setD] = useState<Data | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const still = useReducedMotion()

  const load = useCallback(() => {
    fetch(`/api/mountain/journals/${token}${org ? `?org=${org}` : ''}`)
      .then((r) => r.json()).then(setD).catch((e) => setD({ error: String(e.message || e) }))
  }, [token, org])

  useEffect(load, [load])

  async function toggle(b: Branch, key: string) {
    const next = !b.today[key]
    setErr('')
    setBusy(key)
    // ставим галочку сразу, сервер только подтверждает
    setD((cur) => cur && {
      ...cur,
      branches: (cur.branches || []).map((x) => x.organization_id !== b.organization_id ? x : {
        ...x, today: { ...x.today, [key]: next },
        days: x.days.map((dd) => dd.day !== cur.today ? dd : {
          ...dd, done: next ? [...dd.done, key] : dd.done.filter((k) => k !== key),
        }),
      }),
    })
    try {
      const r = await fetch(`/api/mountain/journals/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ org: b.organization_id, journal: key, on: next }),
      })
      const j = await r.json()
      if (!j.ok) throw new Error(j.error || `HTTP ${r.status}`)
    } catch (e) {
      setErr(String((e as Error).message || e))
      load()                                   // не прошло — показываем, как есть на сервере
    } finally { setBusy('') }
  }

  if (!d || d.error) return null
  const defs = d.journals || []
  const branches = d.branches || []
  const mine = branches[0]
  const owner = d.role === 'owner'

  return (
    <Glass span={12}>
      <Eyebrow t="Журналы" hint={`отмечайте после заполнения · напоминание в ${d.slot || '11:00'}`} />

      {err && <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--red)' }}>{err}</div>}

      {!owner && mine && (
        <div style={{ marginTop: 14, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit,minmax(210px,1fr))' }}>
          {defs.map((j) => {
            const on = !!mine.today[j.key]
            return (
              <motion.button key={j.key} onClick={() => toggle(mine, j.key)} disabled={busy === j.key}
                whileTap={still ? undefined : { scale: 0.985 }}
                transition={{ type: 'spring', bounce: 0, duration: 0.3 }}
                className="sal-tile"
                style={{
                  display: 'flex', gap: 11, alignItems: 'center', textAlign: 'left',
                  cursor: 'pointer', font: 'inherit', width: '100%',
                  borderColor: on ? 'var(--grn)' : undefined,
                }}>
                <span aria-hidden style={{
                  flex: 'none', width: 26, height: 26, borderRadius: '50%',
                  display: 'grid', placeItems: 'center',
                  background: on ? 'var(--grn)' : 'transparent',
                  border: on ? 'none' : '1.5px solid var(--hair)',
                  color: '#fff', fontSize: 14, fontWeight: 800,
                }}>{on ? '✓' : ''}</span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 13.5, fontWeight: 650, color: 'var(--hi)' }}>{j.title}</span>
                  <span style={{ display: 'block', fontSize: 11.5, color: 'var(--mute)' }}>
                    {on ? 'заполнен сегодня' : j.hint}
                  </span>
                </span>
              </motion.button>
            )
          })}
        </div>
      )}

      {/* История недели: у управляющего — свои три строки, у владельца — филиалы */}
      <div style={{ marginTop: 16, overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 460, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 9px', borderBottom: '1px solid var(--hair)' }}>
                {owner ? 'Филиал' : 'Журнал'}
              </th>
              {(d.days || []).map((day) => {
                const { wd, n } = dayShort(day)
                const today = day === d.today
                return (
                  <th key={day} style={{
                    fontSize: 10, fontWeight: today ? 800 : 600, textAlign: 'center',
                    color: today ? 'var(--hi)' : 'var(--mute)', padding: '0 0 9px',
                    borderBottom: '1px solid var(--hair)', width: 34,
                  }}>
                    {wd}<br /><span style={{ fontSize: 11 }}>{n}</span>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {owner
              ? branches.map((b) => (
                <tr key={b.organization_id}>
                  <td style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13, color: 'var(--hi)' }}>{b.name}</td>
                  {b.days.map((dd) => (
                    <td key={dd.day} style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)', textAlign: 'center' }}>
                      <span style={{
                        fontSize: 11.5, fontVariantNumeric: 'tabular-nums', fontWeight: 650,
                        color: dd.done.length === defs.length ? 'var(--grn)'
                          : dd.done.length ? 'var(--amb)' : 'var(--mute)',
                      }}>{dd.done.length}/{defs.length}</span>
                    </td>
                  ))}
                </tr>
              ))
              : defs.map((j) => (
                <tr key={j.key}>
                  <td style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13, color: 'var(--hi)' }}>{j.title}</td>
                  {(mine?.days || []).map((dd) => (
                    <td key={dd.day} style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)' }}>
                      <div style={{ display: 'grid', placeItems: 'center' }}>
                        <Dot on={dd.done.includes(j.key)} today={dd.day === d.today} />
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </Glass>
  )
}
