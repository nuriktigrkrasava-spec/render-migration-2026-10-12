import { useCallback, useEffect, useState } from 'react'
import { Glass } from './ui'
import { tenge } from '../lib/format'
import type { Scope } from './salmonKit'
import { Button, Eyebrow, GLYPH, Segmented, field, iso, kg, pc, periodBounds, photosOf, plural, td, tdR, th, wasteTone } from './salmonKit'

/* Свод разборов за период — то, что администратор заносит в iiko ОДНИМ актом.
   Жарки здесь нет: у неё свой свод и свой акт, и смешивать их нельзя — тут
   списывают рыбу, там обрезки и соус.

   По умолчанию открыт «к занесению»: копятся разборы, которых ещё нет в iiko.
   Занёс — нажал «Занёс в iiko», пачка закрывается, счёт идёт заново. Календарные
   периоды рядом: по ним сверяют месяц, когда акт уже проведён. */

type Act = {
  id: number; created_at: string; organization_id: string
  weight_raw: number; weight_slice: number; weight_trim: number; weight_waste: number
  weight_fry?: number        // старые разборы: часть обрезков лежит здесь
  price_per_kg: number; slice_price: number; posted_at?: string | null
}
type Total = {
  acts: number; raw: number; slice: number; trim: number; waste: number
  slice_price: number; raw_price: number
  slice_pct: number; trim_pct: number; waste_pct: number
  raw_sum: number; slice_sum: number; trim_sum: number
}
type Data = {
  branch?: { name: string }; period?: { from: string; to: string }
  acts?: Act[]; total?: Total; error?: string
}

/** Крупная строка свода: вес и доля. Это и переписывают в iikoOffice. */
function Sum({ photo, glyph, label, w, pct, tone, price }: {
  photo?: string; glyph?: string; label: string; w: number; pct?: number; tone?: string; price?: number
}) {
  return (
    <div style={{ display: 'flex', gap: 11, alignItems: 'center', minWidth: 0 }}>
      {photo
        ? <img src={photo} alt="" className="sal-photo" style={{ width: 46, height: 46, objectFit: 'contain', flex: 'none' }} />
        : <span aria-hidden style={{ width: 46, fontSize: 26, textAlign: 'center', flex: 'none', opacity: .85 }}>{glyph || '⬚'}</span>}
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-.02em', fontVariantNumeric: 'tabular-nums', color: tone || 'var(--hi)' }}>
          {kg(w)}<span style={{ fontSize: 12, fontWeight: 500, color: 'var(--mute)' }}> кг</span>
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>
          {pct != null && <b style={{ color: tone || 'var(--lo)' }}>{pc(pct)}</b>}
          {price ? `${pct != null ? ' · ' : ''}${tenge(price)}/кг` : ''}
        </div>
      </div>
    </div>
  )
}

export default function SalmonPeriod({ token, org, kind, reload }: {
  token: string; org: string; kind: string; reload: number
}) {
  const [scope, setScope] = useState<Scope>('open')
  const now = new Date()
  const [from, setFrom] = useState(iso(new Date(now.getFullYear(), now.getMonth(), 1)))
  const [to, setTo] = useState(iso(now))
  const [d, setD] = useState<Data | null>(null)
  const [busy, setBusy] = useState(false)
  const [askDel, setAskDel] = useState(0)          // «удалить?» подтверждаем вторым нажатием

  const load = useCallback(() => {
    if (!org) return
    const b = periodBounds(scope, from, to)
    const q = new URLSearchParams({ org, date_from: b.from, date_to: b.to })
    if (scope === 'open') q.set('only_open', 'true')
    fetch(`/api/acts/${kind}/${token}/period?${q}`)
      .then((r) => r.json()).then(setD).catch((e) => setD({ error: String(e.message || e) }))
  }, [token, org, kind, scope, from, to])

  useEffect(load, [load, reload])

  async function post() {
    const ids = (d?.acts || []).map((a) => a.id)
    if (!ids.length) return
    const t = d!.total!
    const q = `Свод занесён в iiko: вход ${kg(t.raw)} кг → нарезка ${kg(t.slice)}, `
      + `обрезки ${kg(t.trim)}, `
      + `отход ${kg(t.waste)}. Закрыть ${ids.length} `
      + `${plural(ids.length, 'разбор', 'разбора', 'разборов')}?`
    if (!window.confirm(q)) return
    setBusy(true)
    try {
      await fetch(`/api/acts/${kind}/${token}/posted`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ org, ids }),
      })
      load()
    } finally { setBusy(false) }
  }

  async function del(id: number) {
    if (askDel !== id) { setAskDel(id); setTimeout(() => setAskDel((c) => (c === id ? 0 : c)), 4000); return }
    setAskDel(0)
    await fetch(`/api/acts/${kind}/${token}/delete/${id}?org=${org}`, { method: 'POST' })
    load()
  }

  if (!d) return null
  const t = d.total || null
  const acts = d.acts || []
  const open = scope === 'open'
  const ph = photosOf(kind)

  return (
    <Glass span={12}>
      <Eyebrow t="Свод за период"
        hint={open ? 'ещё не занесено в iiko' : `${d.period?.from} — ${d.period?.to}`} />

      <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <Segmented id="salmon-scope" value={scope} onChange={setScope} options={[
          { k: 'open', t: 'К занесению' },
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

      {!t || t.acts === 0 ? (
        <div style={{ marginTop: 14, fontSize: 13, color: 'var(--mute)' }}>
          {open ? 'Всё занесено: незакрытых разборов нет.' : 'За этот период разборов не было.'}
        </div>
      ) : (
        <>
          <div style={{ marginTop: 14, display: 'grid', gap: 14, gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))' }}>
            <Sum photo={ph.raw} glyph={GLYPH.raw} label={`Переработано · ${t.acts} ${plural(t.acts, 'разбор', 'разбора', 'разборов')}`} w={t.raw} price={t.raw_price} />
            <Sum photo={ph.slice} glyph={GLYPH.slice} label="Нарезка" w={t.slice} pct={t.slice_pct} price={t.slice_price} tone="var(--grn)" />
            <Sum photo={ph.trim} glyph={GLYPH.trim} label="Обрезки" w={t.trim} pct={t.trim_pct} tone="var(--amb)" />
            <Sum photo={ph.waste} glyph={GLYPH.waste} label="Отход" w={t.waste} pct={t.waste_pct} tone={wasteTone(t.waste_pct)} />
          </div>

          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--mute)' }}>
            Себестоимость нарезки за период — <b style={{ color: 'var(--hi)' }}>{tenge(t.slice_price)}/кг</b>,
            средний отход — <b style={{ color: wasteTone(t.waste_pct) }}>{pc(t.waste_pct)}</b>.
            Проценты средневзвешенные: разбор на 40 кг весит больше, чем на два.
          </div>

          {open && (
            <div style={{ marginTop: 14, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button onClick={post} disabled={busy}>
                {busy ? 'Закрываю…' : 'Занёс в iiko вручную'}
              </Button>
              <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>
                Нажимать после того, как акт создан в iikoOffice: пачка закроется, счёт пойдёт заново.
              </span>
            </div>
          )}

          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 16 }}>
            <thead><tr>
              <th style={th}>Когда</th>
              <th style={{ ...th, textAlign: 'right' }}>Вход</th>
              <th style={{ ...th, textAlign: 'right' }}>Нарезка</th>
              <th style={{ ...th, textAlign: 'right' }}>Обрезки</th>
              <th style={{ ...th, textAlign: 'right' }}>Отход</th>
              <th style={{ ...th, textAlign: 'right' }}>Нарезка ₸/кг</th>
              <th style={{ ...th, textAlign: 'right' }}></th>
            </tr></thead>
            <tbody>
              {acts.map((a) => {
                const wp = a.weight_raw ? Math.round(a.weight_waste / a.weight_raw * 1000) / 10 : 0
                // до выноса жарки ушедшее на сковороду вычиталось из обрезков —
                // без него строка не сходится: вход ≠ нарезка + обрезки + отход
                const trim = a.weight_trim + (a.weight_fry || 0)
                return (
                  <tr key={a.id}>
                    <td style={td}>
                      {(a.created_at || '').replace('T', ' ').slice(5, 16)}
                      {a.posted_at && <span style={{ color: 'var(--mute)', fontSize: 11 }}> · в iiko</span>}
                    </td>
                    <td style={tdR}>{kg(a.weight_raw)}</td>
                    <td style={tdR}>{kg(a.weight_slice)}</td>
                    <td style={tdR}>{kg(trim)}</td>
                    <td style={{ ...tdR, color: wasteTone(wp) }}>{kg(a.weight_waste)} · {pc(wp)}</td>
                    <td style={tdR}>{tenge(a.slice_price)}</td>
                    <td style={{ ...tdR, width: 88 }}>
                      {!a.posted_at && (
                        <button onClick={() => del(a.id)}
                          style={{
                            border: 'none', background: 'transparent', cursor: 'pointer',
                            fontFamily: 'inherit', fontSize: 11.5, padding: 0,
                            color: askDel === a.id ? 'var(--red)' : 'var(--mute)',
                            fontWeight: askDel === a.id ? 700 : 400,
                          }}>
                          {askDel === a.id ? 'точно удалить' : 'удалить'}
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
    </Glass>
  )
}
