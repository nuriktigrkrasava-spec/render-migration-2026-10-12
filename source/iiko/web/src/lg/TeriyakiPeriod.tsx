import { useCallback, useEffect, useState } from 'react'
import { Glass } from './ui'
import { tenge } from '../lib/format'
import type { Scope } from './salmonKit'
import { Button, Eyebrow, GLYPH, PHOTO, Segmented, field, iso, kg, pc, periodBounds, plural, td, tdR, th } from './salmonKit'

/* Свод жарок за период — ВТОРОЙ акт, который администратор заносит в iiko, и
   он не смешивается со сводом разбора: там списывают рыбу, здесь — обрезки и
   соус, а приходуют П/Ф терияки.

   Главная цифра периода не вес, а выход жарки: из одних и тех же обрезков у
   аккуратного повара выходит 70 % терияки, у спешащего — 55. Рядом соус на
   килограмм: перелив виден только так.

   Выход и ужарка считаются от входа целиком — обрезки ВМЕСТЕ с соусом, как и
   в самой жарке. Складывать свод по другой формуле, чем акты, нельзя. */

type Act = {
  id: number; created_at: string; organization_id: string
  weight_fry: number; weight_teriyaki: number; weight_sauce?: number
  trim_price?: number; sauce_price?: number; slice_price?: number
  posted_at?: string | null
}
type Total = {
  acts: number; fry: number; teriyaki: number; shrink: number; input: number
  sauce: number; sauce_sum: number; sauce_g_per_kg: number; sauce_price: number
  trim_sum: number; teriyaki_sum: number; teriyaki_price: number; trim_price: number
  spent: number; markup: number
  yield_pct: number; shrink_pct: number
}
type Data = {
  branch?: { name: string }; period?: { from: string; to: string }
  acts?: Act[]; total?: Total; error?: string
}

const yieldTone = (p: number) => (p >= 70 ? 'var(--grn)' : p >= 55 ? 'var(--amb)' : 'var(--red)')

/** Крупная строка свода: то, что переписывают в iikoOffice. */
function Sum({ photo, glyph, label, value, unit, hint, tone }: {
  photo?: string; glyph?: string; label: string; value: number; unit: string
  hint?: React.ReactNode; tone?: string
}) {
  return (
    <div style={{ display: 'flex', gap: 11, alignItems: 'center', minWidth: 0 }}>
      {photo
        ? <img src={photo} alt="" className="sal-photo" style={{ width: 46, height: 46, objectFit: 'contain', flex: 'none' }} />
        : <span aria-hidden style={{ width: 46, fontSize: 26, textAlign: 'center', flex: 'none', opacity: .85 }}>{glyph || '⬚'}</span>}
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-.02em', fontVariantNumeric: 'tabular-nums', color: tone || 'var(--hi)' }}>
          {kg(value)}<span style={{ fontSize: 12, fontWeight: 500, color: 'var(--mute)' }}> {unit}</span>
        </div>
        {hint && <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>{hint}</div>}
      </div>
    </div>
  )
}

export default function TeriyakiPeriod({ token, org, reload }: {
  token: string; org: string; reload: number
}) {
  const [scope, setScope] = useState<Scope>('open')
  const now = new Date()
  const [from, setFrom] = useState(iso(new Date(now.getFullYear(), now.getMonth(), 1)))
  const [to, setTo] = useState(iso(now))
  const [d, setD] = useState<Data | null>(null)
  const [busy, setBusy] = useState(false)
  const [askDel, setAskDel] = useState(0)

  const load = useCallback(() => {
    if (!org) return
    const b = periodBounds(scope, from, to)
    const q = new URLSearchParams({ org, date_from: b.from, date_to: b.to })
    if (scope === 'open') q.set('only_open', 'true')
    fetch(`/api/teriyaki/${token}/period?${q}`)
      .then((r) => r.json()).then(setD).catch((e) => setD({ error: String(e.message || e) }))
  }, [token, org, scope, from, to])

  useEffect(load, [load, reload])

  async function post() {
    const ids = (d?.acts || []).map((a) => a.id)
    if (!ids.length) return
    const t = d!.total!
    const q = `Свод жарок занесён в iiko: списать обрезки ${kg(t.fry)} кг`
      + `${t.sauce ? ` и соус ${kg(t.sauce)} г` : ''}, `
      + `оприходовать терияки ${kg(t.teriyaki)} кг. Закрыть ${ids.length} `
      + `${plural(ids.length, 'жарку', 'жарки', 'жарок')}?`
    if (!window.confirm(q)) return
    setBusy(true)
    try {
      await fetch(`/api/teriyaki/${token}/posted`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ org, ids }),
      })
      load()
    } finally { setBusy(false) }
  }

  async function del(id: number) {
    if (askDel !== id) { setAskDel(id); setTimeout(() => setAskDel((c) => (c === id ? 0 : c)), 4000); return }
    setAskDel(0)
    await fetch(`/api/teriyaki/${token}/delete/${id}?org=${org}`, { method: 'POST' })
    load()
  }

  if (!d) return null
  const t = d.total || null
  const acts = d.acts || []
  const open = scope === 'open'
  const sauced = (t?.sauce || 0) > 0

  return (
    <Glass span={12}>
      <Eyebrow t="Свод жарок за период"
        hint={open ? 'ещё не занесено в iiko' : `${d.period?.from} — ${d.period?.to}`} />

      <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <Segmented id="teriyaki-scope" value={scope} onChange={setScope} options={[
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
          {open ? 'Всё занесено: незакрытых жарок нет.' : 'За этот период не жарили.'}
        </div>
      ) : (
        <>
          <div style={{ marginTop: 14, display: 'grid', gap: 14, gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))' }}>
            <Sum photo={PHOTO.trim} glyph={GLYPH.trim} unit="кг"
              label={`Обрезков в жарку · ${t.acts} ${plural(t.acts, 'жарка', 'жарки', 'жарок')}`}
              value={t.fry} hint={`${tenge(t.trim_price)}/кг`} />
            <Sum glyph={GLYPH.sauce} unit="г" label="Соуса" value={t.sauce}
              hint={sauced ? `${kg(t.sauce_g_per_kg)} г на кг · ${tenge(t.sauce_sum)}` : 'не лили'} />
            <Sum photo={PHOTO.teriyaki} glyph={GLYPH.teriyaki} unit="кг" label="Терияки" value={t.teriyaki}
              tone="var(--grn)" hint={`${tenge(t.teriyaki_price)}/кг`} />
            <Sum glyph="🔥" unit="кг" label="Ужарка" value={t.shrink} tone={yieldTone(t.yield_pct)}
              hint={<b style={{ color: yieldTone(t.yield_pct) }}>выход {pc(t.yield_pct)}</b>} />
          </div>

          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--mute)' }}>
            Списать <b style={{ color: 'var(--hi)' }}>{kg(t.fry)} кг</b> обрезков
            {sauced && <> и <b style={{ color: 'var(--hi)' }}>{kg(t.sauce)} г</b> соуса</>},
            оприходовать <b style={{ color: 'var(--grn)' }}>{kg(t.teriyaki)} кг</b> терияки
            по <b style={{ color: 'var(--hi)' }}>{tenge(t.teriyaki_price)}/кг</b>
            {' '}на <b style={{ color: 'var(--grn)' }}>{tenge(t.teriyaki_sum)}</b>
            {!!t.markup && <> — это на <b style={{ color: 'var(--amb)' }}>{tenge(t.markup)}</b> больше
              списанного: себестоимость наращена на отход {pc(t.shrink_pct)}</>}.
            Выход жарки — <b style={{ color: yieldTone(t.yield_pct) }}>{pc(t.yield_pct)}</b>,
            средневзвешенный: жарка на 10 кг весит больше, чем на один.
          </div>

          {open && (
            <div style={{ marginTop: 14, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button onClick={post} disabled={busy}>
                {busy ? 'Закрываю…' : 'Занёс в iiko вручную'}
              </Button>
              <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>
                Это отдельный акт: свод разбора закрывается своей кнопкой.
              </span>
            </div>
          )}

          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 16 }}>
            <thead><tr>
              <th style={th}>Когда</th>
              <th style={{ ...th, textAlign: 'right' }}>В жарку</th>
              <th style={{ ...th, textAlign: 'right' }}>Терияки</th>
              {sauced && <th style={{ ...th, textAlign: 'right' }}>Соус, г</th>}
              <th style={{ ...th, textAlign: 'right' }}>Выход</th>
              <th style={{ ...th, textAlign: 'right' }}>Терияки ₸/кг</th>
              <th style={{ ...th, textAlign: 'right' }}></th>
            </tr></thead>
            <tbody>
              {acts.map((a) => {
                // вход жарки — обрезки вместе с соусом; соус записан в граммах
                const wIn = a.weight_fry + (a.weight_sauce || 0) / 1000
                const y = wIn ? Math.round(a.weight_teriyaki / wIn * 1000) / 10 : 0
                return (
                  <tr key={a.id}>
                    <td style={td}>
                      {(a.created_at || '').replace('T', ' ').slice(5, 16)}
                      {a.posted_at && <span style={{ color: 'var(--mute)', fontSize: 11 }}> · в iiko</span>}
                    </td>
                    <td style={tdR}>{kg(a.weight_fry)}</td>
                    <td style={tdR}>{kg(a.weight_teriyaki)}</td>
                    {sauced && <td style={tdR}>{a.weight_sauce ? kg(a.weight_sauce) : '—'}</td>}
                    <td style={{ ...tdR, color: yieldTone(y) }}>{pc(y)}</td>
                    <td style={tdR}>{tenge(a.slice_price || 0)}</td>
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
