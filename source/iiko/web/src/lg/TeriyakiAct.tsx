import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Glass } from './ui'
import { tenge } from '../lib/format'
import { Button, Eyebrow, GLYPH, PHOTO, Tile, field, kg, pc, td, tdR, th } from './salmonKit'
import TeriyakiPeriod from './TeriyakiPeriod'

/* «Терияки» — передел обрезков, а не разбор рыбы.

   Отдельный экран не ради красоты: пока жарка стояла плитками внутри разбора,
   один документ отвечал на два разных вопроса — «насколько аккуратно разделали
   рыбу» и «сколько дала сковорода». Смешивать их нельзя: удачной жаркой нельзя
   замазать плохую разделку, а плохой — испортить хорошую.

   Взвешивают два веса и меряют третий: сколько обрезков положили, сколько
   терияки сняли и сколько граммов соуса налили. Между первыми двумя живёт
   ужарка — вес ушёл, деньги остались, и килограмм терияки ровно на неё дороже
   килограмма обрезков. Соус здесь единственный, кто деньги ДОБАВЛЯЕТ: он
   пришёл своей накладной со своего склада, и цена у него оттуда же.

   Граммы, а не килограммы: соус льют половником, и в килограммах повар
   ошибается на порядок. Рядом «сколько соуса на килограмм» — на этой цифре
   перелив и ловится, сами по себе граммы не значат ничего.

   Формулы владельца, считает их сервер (acts.fry_teriyaki):
     выход, %      = вес терияки / (вес обрезков + вес соуса) × 100
     себестоимость = (сумма обрезков + сумма соуса) × (1 + отход)
   Вторая наращивает деньги на ужарку, поэтому приход больше списания — эту
   разницу показываем отдельной строкой, чтобы таблица сходилась глазами. */

type Prod = { id: string; name: string; unit?: string } | null
type Ctx = {
  error?: string
  role?: 'owner' | 'manager'
  branch?: { organization_id: string; name: string }
  branches?: { organization_id: string; name: string }[]
  products?: { trim: Prod; teriyaki: Prod; sauce: Prod }
  trim_price?: number
  trim_from?: string
  trim_const?: boolean        // обрезки по константе учёта, а не по себестоимости
  sauce_price?: number
  sauce_from?: string
  title?: string
}
type Line = { weight: number; price_per_kg: number | null; sum: number | null; from_trim?: number; from_input?: number; shrink?: number }
type Sauce = { grams: number; price_per_kg: number | null; sum: number | null }
type Calc = {
  ok: boolean; error?: string
  trim?: Line; teriyaki?: Line; sauce?: Sauce
  yield_pct?: number; shrink_pct?: number; sauce_g_per_kg?: number; check?: number | null
  /** вход жарки — обрезки вместе с соусом; от него считаются выход и ужарка */
  input_weight?: number
  /** вложено (списание) и наценка на ужарку: приход = spent + markup */
  spent?: number; markup?: number
  /** деньги посчитаны; если нет — `need_text` говорит, какой цены не хватило */
  money?: boolean; need?: string; need_text?: string
  saved?: boolean; id?: number
}

const num = (s: string) => Number(s.replace(',', '.')) || 0

/** Выход жарки: ниже 70 % — сковорода съела больше обычного. */
const yieldTone = (p: number) => (p >= 70 ? 'var(--grn)' : p >= 55 ? 'var(--amb)' : 'var(--red)')

export default function TeriyakiAct({ token, switcher }: {
  token: string; switcher: React.ReactNode
}) {
  const [ctx, setCtx] = useState<Ctx | null>(null)
  const [org, setOrg] = useState('')
  const [fry, setFry] = useState('')          // обрезков положили на сковороду
  const [out, setOut] = useState('')          // терияки сняли со сковороды
  const [sauce, setSauce] = useState('')      // соуса налили, ГРАММЫ
  const [trimPrice, setTrimPrice] = useState('')
  const [saucePrice, setSaucePrice] = useState('')
  const [calc, setCalc] = useState<Calc | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<Calc | null>(null)
  const [reload, setReload] = useState(0)
  const still = useReducedMotion()

  /* Филиал приходит из первого же ответа, и раньше это гнало второй запрос за
     тем же филиалом — а на время него экран обнулялся и мигал «Гружу
     справочник…». Помним, за каким филиалом уже сходили. */
  const loadedOrg = useRef<string | null>(null)
  useEffect(() => {
    if (loadedOrg.current === org) return
    fetch(`/api/teriyaki/${token}${org ? `?org=${org}` : ''}`)
      .then((r) => r.json())
      .then((d: Ctx) => {
        loadedOrg.current = d.branch?.organization_id ?? org
        setCtx(d)
        if (!org && d.branch) setOrg(d.branch.organization_id)
      })
      .catch((e) => setCtx({ error: String(e.message || e) }))
  }, [token, org])

  // считаем на сервере: там же лежат тесты на эту арифметику
  useEffect(() => {
    const wf = num(fry)
    if (!wf) { setCalc(null); return }
    const t = setTimeout(() => {
      fetch(`/api/teriyaki/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org, weight_fry: wf, weight_teriyaki: num(out), weight_sauce: num(sauce),
          trim_price: trimPrice ? num(trimPrice) : null,
          sauce_price: saucePrice ? num(saucePrice) : null,
        }),
      }).then((r) => r.json()).then(setCalc).catch(() => setCalc(null))
    }, 250)
    return () => clearTimeout(t)
  }, [fry, out, sauce, trimPrice, saucePrice, org, token])

  async function save() {
    if (!calc?.ok || !calc.money) return
    setSaving(true)
    try {
      const r = await fetch(`/api/teriyaki/${token}/save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org, weight_fry: num(fry), weight_teriyaki: num(out), weight_sauce: num(sauce),
          trim_price: trimPrice ? num(trimPrice) : null,
          sauce_price: saucePrice ? num(saucePrice) : null,
        }),
      })
      const d: Calc = await r.json()
      if (d.saved) {
        setSaved(d)
        setFry(''); setOut(''); setSauce(''); setCalc(null)
        setReload((n) => n + 1)
        setTimeout(() => setSaved(null), 6000)
      } else {
        setCalc({ ...(calc as Calc), error: d.error || `HTTP ${r.status}`, ok: false })
      }
    } finally { setSaving(false) }
  }

  if (!ctx || ctx.error) return (
    <Glass span={12}>
      <Eyebrow t="Акты и разборы" />
      {switcher}
      <div style={{ padding: '12px 0 2px', fontSize: 13, color: ctx?.error ? 'var(--red)' : 'var(--mute)' }}>
        {ctx?.error || 'Гружу справочник…'}
      </div>
    </Glass>
  )

  const p = ctx.products || { trim: null, teriyaki: null, sauce: null }
  const missing = !p.trim || !p.teriyaki
  const w = calc?.ok ? calc : null
  const shownTrim = Number(trimPrice ? num(trimPrice) : ctx.trim_price || 0)
  const shownSauce = Number(saucePrice ? num(saucePrice) : ctx.sauce_price || 0)

  return (
    <>
      <Glass span={12}>
        <Eyebrow t={ctx.title || 'Терияки из обрезков'}
          hint={`передел обрезков${shownTrim ? ` · по ${tenge(shownTrim)}/кг` : ''}`} />
        {switcher}

        {missing && (
          <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--amb)' }}>
            В номенклатуре филиала нет обрезков или П/Ф терияки — жарку не записать.
          </div>
        )}

        {/* Два веса и граммы. Ужарка не вводится: она разница первых двух.
            Порядок плиток — порядок жарки: сперва то, что кладут на сковороду
            (обрезки и соус), потом то, что с неё снимают, и только следом
            ужарка. Терияки, стоявшее между обрезками и соусом, читалось как
            ещё один вход. */}
        <div style={{ marginTop: 14, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit,minmax(158px,1fr))' }}>
          <Tile photo={PHOTO.trim} glyph={GLYPH.trim} label="Обрезки в жарку" hint="вход"
            value={fry} onChange={setFry} placeholder="2"
            foot={shownTrim ? `${tenge(shownTrim)}/кг` : 'цену ниже'} />
          <Tile photo={PHOTO.sauce} glyph={GLYPH.sauce} label="Соус терияки" unit="г"
            hint={shownSauce ? `по ${tenge(shownSauce)}/кг` : 'цену ниже'}
            value={sauce} onChange={setSauce} placeholder="200"
            foot={w && w.sauce && w.sauce.grams > 0
              ? `${kg(w.sauce_g_per_kg)} г на кг${w.sauce.sum ? ` · ${tenge(w.sauce.sum)}` : ''}`
              : 'сколько налили'} />
          <Tile photo={PHOTO.teriyaki} glyph={GLYPH.teriyaki} label="Терияки" hint="со сковороды"
            value={out} onChange={setOut} placeholder="1,4"
            foot={w && w.teriyaki!.price_per_kg ? `${tenge(w.teriyaki!.price_per_kg)}/кг` : 'сколько сняли'} />
          <Tile glyph="🔥" label="Ужарка" hint="считается сама" readOnly
            value={w ? kg(w.teriyaki!.shrink) : ''}
            tone={w ? yieldTone(w.yield_pct || 0) : undefined}
            foot={w
              ? <>
                  <b style={{ color: yieldTone(w.yield_pct || 0) }}>выход {pc(w.yield_pct)}</b>
                  <div style={{ marginTop: 2, fontSize: 11, color: 'var(--mute)' }}>
                    {kg(w.input_weight)} − {kg(w.teriyaki!.weight)} · {pc(w.shrink_pct)}
                  </div>
                </>
              : 'обрезки + соус − терияки'} />
        </div>

        <div style={{ marginTop: 12, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))' }}>
          {ctx.role === 'owner' && (
            <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Филиал
              <select value={org} onChange={(e) => { setOrg(e.target.value); setCalc(null) }} style={{ ...field, marginTop: 5 }}>
                {(ctx.branches || []).map((b) => (
                  <option key={b.organization_id} value={b.organization_id}>{b.name}</option>
                ))}
              </select>
            </label>
          )}
          <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Обрезки, ₸/кг
            <input value={trimPrice} inputMode="decimal"
              placeholder={ctx.trim_price ? String(ctx.trim_price) : 'почём считать'}
              onChange={(e) => setTrimPrice(e.target.value)} style={{ ...field, marginTop: 5 }} />
          </label>
          <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Соус терияки, ₸/кг
            <input value={saucePrice} inputMode="decimal"
              placeholder={ctx.sauce_price ? String(ctx.sauce_price) : 'нет в накладных'}
              onChange={(e) => setSaucePrice(e.target.value)} style={{ ...field, marginTop: 5 }} />
          </label>
        </div>

        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--mute)' }}>
          {ctx.trim_price
            ? <>Обрезки — <b style={{ color: 'var(--hi)' }}>{tenge(ctx.trim_price)}/кг</b>{ctx.trim_from ? ` · ${ctx.trim_from}` : ''}.</>
            : <>Себестоимости обрезков в iiko нет — впиши, почём их считать.</>}
          {ctx.sauce_price
            ? <> Соус — <b style={{ color: 'var(--hi)' }}>{tenge(ctx.sauce_price)}/кг</b>{ctx.sauce_from ? ` · ${ctx.sauce_from}` : ''}.</>
            : <> Соуса нет ни в себестоимости, ни в накладных — впиши цену, иначе граммы не во что пересчитать.</>}
        </div>

        {/* Ужарка и выход — это веса, и цена к ним не относится: показываем их
            даже когда деньги посчитать не из чего. */}
        {w && !w.money && (
          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--amb)' }}>
            {w.need_text || 'Не хватает цены, чтобы посчитать деньги.'} Ужарка и выход жарки ниже уже посчитаны.
          </div>
        )}

        {w && !w.money && (
          <div style={{ marginTop: 14, display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--mute)' }}>
            <span>Выход жарки <b style={{ color: yieldTone(w.yield_pct || 0) }}>{pc(w.yield_pct)}</b></span>
            <span>Ужарка <b style={{ color: 'var(--amb)' }}>{kg(w.teriyaki!.shrink)} кг</b> · {pc(w.shrink_pct)}</span>
            {(w.sauce_g_per_kg || 0) > 0 && (
              <span>Соуса <b style={{ color: 'var(--hi)' }}>{kg(w.sauce_g_per_kg)} г</b> на кг</span>
            )}
          </div>
        )}

        {calc && !calc.ok && (
          <div style={{ marginTop: 12, fontSize: 13, color: 'var(--red)' }}>{calc.error}</div>
        )}

        {w && w.money && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 16 }}>
              <thead><tr>
                <th style={th}>Позиция</th>
                <th style={{ ...th, textAlign: 'right' }}>Вес</th>
                <th style={{ ...th, textAlign: 'right' }}>₸ / кг</th>
                <th style={{ ...th, textAlign: 'right' }}>Сумма</th>
              </tr></thead>
              <tbody>
                <tr>
                  <td style={td}>{p.trim?.name || 'Обрезки'} <span style={{ color: 'var(--mute)', fontSize: 11 }}>· списать</span></td>
                  <td style={tdR}>{kg(w.trim!.weight)} кг</td>
                  <td style={tdR}>{tenge(w.trim!.price_per_kg)}</td>
                  <td style={tdR}>{tenge(w.trim!.sum)}</td>
                </tr>
                {w.sauce && w.sauce.grams > 0 && (
                  <tr>
                    <td style={td}>{p.sauce?.name || 'Соус терияки'} <span style={{ color: 'var(--mute)', fontSize: 11 }}>· списать</span></td>
                    <td style={tdR}>{kg(w.sauce.grams)} г</td>
                    <td style={tdR}>{tenge(w.sauce.price_per_kg)}</td>
                    <td style={tdR}>{tenge(w.sauce.sum)}</td>
                  </tr>
                )}
                {!!w.markup && (
                  <tr>
                    <td style={td}>Ужарка <span style={{ color: 'var(--mute)', fontSize: 11 }}>
                      · наценка на отход {pc(w.shrink_pct)}</span></td>
                    <td style={tdR}>—</td>
                    <td style={tdR}>—</td>
                    <td style={{ ...tdR, color: 'var(--amb)' }}>+{tenge(w.markup)}</td>
                  </tr>
                )}
                <tr>
                  <td style={{ ...td, color: 'var(--grn)', fontWeight: 650 }}>{p.teriyaki?.name || 'П/Ф Лосось терияки'}{' '}
                    <span style={{ color: 'var(--mute)', fontSize: 11, fontWeight: 400 }}>
                      · оприходовать, ужарка {kg(w.teriyaki!.shrink)} кг
                    </span>
                  </td>
                  <td style={tdR}><b>{kg(w.teriyaki!.weight)} кг</b></td>
                  <td style={tdR}><b>{tenge(w.teriyaki!.price_per_kg)}</b></td>
                  <td style={tdR}><b>{tenge(w.teriyaki!.sum)}</b></td>
                </tr>
              </tbody>
            </table>

            <div style={{ marginTop: 10, display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--mute)' }}>
              <span>Выход жарки <b style={{ color: yieldTone(w.yield_pct || 0) }}>{pc(w.yield_pct)}</b></span>
              <span>Ужарка <b style={{ color: 'var(--amb)' }}>{kg(w.teriyaki!.shrink)} кг</b> · {pc(w.shrink_pct)}</span>
              {(w.sauce_g_per_kg || 0) > 0 && (
                <span>Соуса <b style={{ color: 'var(--hi)' }}>{kg(w.sauce_g_per_kg)} г</b> на кг</span>
              )}
              <span style={{ marginLeft: 'auto' }}>
                себестоимость терияки — <b style={{ color: 'var(--hi)' }}>{tenge(w.teriyaki!.price_per_kg)}/кг</b>
              </span>
            </div>

            <div style={{ marginTop: 14, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button onClick={save} disabled={saving || missing}>
                {saving ? 'Записываю…' : 'Зафиксировать жарку'}
              </Button>
              <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>
                В iiko поштучно не уходит — за период администратор заносит один сводный акт.
              </span>
            </div>
          </>
        )}

        <AnimatePresence>
          {saved && (
            <motion.div
              initial={still ? { opacity: 0 } : { opacity: 0, y: 8, scale: .98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.35 }}
              style={{
                marginTop: 14, padding: '12px 14px', borderRadius: 14,
                border: '1px solid var(--hair)', background: 'var(--glass-2)',
              }}>
              <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--grn)' }}>
                Жарка записана · терияки {kg(saved.teriyaki?.weight)} кг по {tenge(saved.teriyaki?.price_per_kg || 0)}/кг
              </div>
              <div style={{ marginTop: 4, fontSize: 12.5, color: 'var(--mute)' }}>
                Она попала в свод за период — ниже. Ошиблись? Жарку можно удалить, пока свод не занесён в iiko.
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Glass>

      <TeriyakiPeriod token={token} org={org} reload={reload} />
    </>
  )
}
