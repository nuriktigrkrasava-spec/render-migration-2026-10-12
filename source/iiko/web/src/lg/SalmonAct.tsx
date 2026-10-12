import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Glass } from './ui'
import { tenge } from '../lib/format'
import { Button, Eyebrow, GLYPH, Segmented, Tile, field, kg, pc, photosOf, td, tdR, th, wasteTone } from './salmonKit'
import SalmonPeriod from './SalmonPeriod'
import SalmonNetwork from './SalmonNetwork'
import TeriyakiAct from './TeriyakiAct'

/* «Акты и разборы» — рядом с горой, на тех же ссылках.

   Разбор один и тот же на две рыбы: лосось и угорь. Взвешивают то, что держат
   в руках: вход, нарезку и обрезки на выходе. Отход (кости и кожа) не
   взвешивают — он остаток веса, и его процент и есть оценка разбора.

   Жарки здесь НЕТ. Терияки — это уже не разбор рыбы, а передел обрезков на
   сковороде, и он живёт своей вкладкой: смешанные в одном экране, они путали
   вопрос «насколько аккуратно разделали рыбу» с вопросом «сколько дала
   сковорода».

   В iiko поштучно ничего не уходит: каждый разбор фиксируется у нас, а за
   период администратор заносит ОДИН сводный акт руками. Два документа на один
   и тот же вес задвоили бы списание.

   Цены — учётная себестоимость из iiko, по складам самого филиала. Накладная
   осталась запасным источником: П/Ф приходит в филиал перемещением из ЦЕХа, у
   него нет накладной вовсе, и поиск цены «у соседей» давал всей сети одну и ту
   же цифру. Поля рядом всё равно есть: бывает разовый закуп по другой цене.

   Веса считаются всегда, даже когда цены не нашлось: отход — это вес, и из-за
   ненайденной цены обрезков пропадать с экрана он не должен. */

type Prod = { id: string; name: string; unit?: string } | null
type Ctx = {
  error?: string
  role?: 'owner' | 'manager'
  branch?: { organization_id: string; name: string }
  branches?: { organization_id: string; name: string }[]
  products?: { raw: Prod; trim: Prod; slice: Prod; waste: Prod }
  price_per_kg?: number
  price_from?: string
  trim_price?: number
  trim_from?: string
  trim_const?: boolean        // обрезки по константе учёта, а не из накладных
  kind?: string
  title?: string
  raw_label?: string
  raw_short?: string
  kinds?: { key: string; title: string; tab: string }[]
}
type Line = { weight: number; price_per_kg: number | null; sum: number | null }
type Calc = {
  ok: boolean; error?: string
  raw?: Line; slice?: Line; trim?: Line; waste?: Line
  slice_pct?: number; trim_pct?: number; waste_pct?: number; check?: number | null
  /** деньги посчитаны; если нет — `need_text` говорит, какой цены не хватило */
  money?: boolean; need?: string; need_text?: string
  saved?: boolean; id?: number
}

const KINDS = [{ k: 'salmon', t: 'Лосось' }, { k: 'eel', t: 'Угорь' }, { k: 'teriyaki', t: 'Терияки' }]
const num = (s: string) => Number(s.replace(',', '.')) || 0

export default function SalmonAct({ token }: { token: string }) {
  const [kind, setKind] = useState('salmon')
  const [ctx, setCtx] = useState<Ctx | null>(null)
  const [org, setOrg] = useState<string>('')
  const [raw, setRaw] = useState('')
  const [slice, setSlice] = useState('')
  const [trim, setTrim] = useState('')
  const [price, setPrice] = useState('')          // пусто = цена из накладных
  const [trimPrice, setTrimPrice] = useState('')  // пусто = константа или накладная
  const [calc, setCalc] = useState<Calc | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<Calc | null>(null)
  const [reload, setReload] = useState(0)         // счётчик: обновить свод и сеть
  const still = useReducedMotion()

  /* Филиал приходит из первого же ответа, и раньше это гнало второй запрос за
     тем же филиалом — а на время него экран обнулялся и мигал «Гружу
     справочник…». Помним, за чем уже сходили; рыбу чистит pickKind.

     Ответ проверяем на актуальность. Справочник лосося идёт в iiko и едет
     секундами; если за это время переключили рыбу, поздний лососёвый ответ
     затирал уже пришедший угриный. Экран получался из двух рыб сразу: шапка
     от лосося — «обрезки 8 800 из накладной ЦЕХа», — а счёт уже от угря, и
     он говорил, что цена обрезков не задана. Числа спорили сами с собой. */
  const loadedKey = useRef('')
  const ctxReq = useRef(0)
  useEffect(() => {
    if (kind === 'teriyaki') return          // у передела свой экран и свой запрос
    if (loadedKey.current === `${kind}|${org}`) return
    const my = ++ctxReq.current
    fetch(`/api/acts/${kind}/${token}${org ? `?org=${org}` : ''}`)
      .then((r) => r.json())
      .then((d: Ctx) => {
        if (my !== ctxReq.current) return    // пока ходили, выбрали другую рыбу
        loadedKey.current = `${kind}|${d.branch?.organization_id ?? org}`
        setCtx(d)
        if (!org && d.branch) setOrg(d.branch.organization_id)
      })
      .catch((e) => {
        if (my === ctxReq.current) setCtx({ error: String(e.message || e) })
      })
  }, [token, org, kind])

  /** Рыбу сменили — веса чужие: у угря другой вход и другая цена обрезков. */
  function pickKind(k: string) {
    if (k === kind) return
    setKind(k)
    if (k !== 'teriyaki') setCtx(null)       // другая рыба — чужие позиции и цены
    setRaw(''); setSlice(''); setTrim('')
    setPrice(''); setTrimPrice(''); setCalc(null); setSaved(null)
  }

  // считаем на сервере: там же лежат тесты на эту арифметику
  const calcReq = useRef(0)
  useEffect(() => {
    const wr = num(raw)
    if (!wr) { setCalc(null); return }
    const t = setTimeout(() => {
      const my = ++calcReq.current
      fetch(`/api/acts/${kind}/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org, weight_raw: wr, weight_slice: num(slice),
          weight_trim: num(trim),
          price_per_kg: price ? num(price) : null,
          trim_price: trimPrice ? num(trimPrice) : null,
        }),
      }).then((r) => r.json())
        // тот же сторож, что и у справочника: счёт от прошлой рыбы или от
        // прошлого веса не должен ложиться поверх нового
        .then((d) => { if (my === calcReq.current) setCalc(d) })
        .catch(() => { if (my === calcReq.current) setCalc(null) })
    }, 250)
    return () => clearTimeout(t)
  }, [raw, slice, trim, price, trimPrice, org, token, kind])

  async function save() {
    if (!calc?.ok || !calc.money) return
    setSaving(true)
    try {
      const r = await fetch(`/api/acts/${kind}/${token}/save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          org,
          weight_raw: num(raw), weight_slice: num(slice), weight_trim: num(trim),
          price_per_kg: price ? num(price) : null,
          trim_price: trimPrice ? num(trimPrice) : null,
        }),
      })
      const d: Calc = await r.json()
      if (d.saved) {
        setSaved(d)
        setRaw(''); setSlice(''); setTrim(''); setCalc(null)
        setReload((n) => n + 1)
        setTimeout(() => setSaved(null), 6000)
      } else {
        setCalc({ ...(calc as Calc), error: d.error || `HTTP ${r.status}`, ok: false })
      }
    } finally { setSaving(false) }
  }

  const kinds = (ctx?.kinds || []).map((k) => ({ k: k.key, t: k.tab }))
  const tabs = kinds.length ? kinds : KINDS
  const switcher = (
    <div style={{ marginTop: 12 }}>
      <Segmented id="acts-kind" value={kind} onChange={pickKind} options={tabs} />
    </div>
  )

  // Терияки — не разбор: свой экран, свои адреса, свой свод. Переключатель
  // общий только потому, что открывают их подряд.
  if (kind === 'teriyaki') return <TeriyakiAct token={token} switcher={switcher} />

  if (!ctx || ctx.error) return (
    <Glass span={12}>
      <Eyebrow t="Акты и разборы" />
      {switcher}
      <div style={{ padding: '12px 0 2px', fontSize: 13, color: ctx?.error ? 'var(--red)' : 'var(--mute)' }}>
        {ctx?.error || 'Гружу справочник…'}
      </div>
    </Glass>
  )

  const p = ctx.products || { raw: null, trim: null, slice: null, waste: null }
  const missing = !p.raw || !p.trim || !p.slice
  /* Позиции может не быть в номенклатуре филиала — тогда в таблице оставалась
     пустая ячейка, и строка читалась как «· остаток стоимости» без хозяина.
     Пустое место не объясняет ничего: подписываем роль и говорим прямо, что
     позиции нет, — это и есть причина, по которой акт не собрать. */
  const ROLE_NAME: Record<string, string> = {
    raw: ctx.raw_label || 'Вход', slice: 'Нарезка', trim: 'Обрезки', waste: 'Отход',
  }
  const nameOf = (prod: Prod, role: string) => prod?.name || ROLE_NAME[role]
  const noProd = (prod: Prod) => !prod
  const gone = (['raw', 'slice', 'trim'] as const).filter((r) => !p[r]).map((r) => ROLE_NAME[r])
  const w = calc?.ok ? calc : null
  const ph = photosOf(kind)
  const short = ctx.raw_short || 'стейк'
  // цену спрашиваем только там, где её не нашли: у угря обрезки без константы
  // учёта, а соус в филиал мог не приходить 60 дней
  const askTrimPrice = !ctx.trim_price
  const shownTrim = Number(trimPrice ? num(trimPrice) : ctx.trim_price || 0)

  return (
    <>
      <Glass span={12}>
        <Eyebrow t={ctx.title || 'Разбор лосося'}
          hint={`вход ${p.raw ? p.raw.name : `${ROLE_NAME.raw} — нет в номенклатуре`}${shownTrim ? ` · обрезки по ${tenge(shownTrim)}/кг` : ''}`} />
        {switcher}

        {missing && (
          <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--amb)' }}>
            В номенклатуре этого филиала нет {gone.length > 1 ? 'позиций' : 'позиции'}:{' '}
            <b>{gone.join(', ').toLowerCase()}</b> — акт не собрать и в iiko его не занести.
            Сперва позицию заводят в номенклатуре, и она подтянется сюда сама.
          </div>
        )}

        {/* Вход и выходы. Отход не вводят: он остаток веса. */}
        <div style={{ marginTop: 14, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit,minmax(158px,1fr))' }}>
          <Tile photo={ph.raw} glyph={GLYPH.raw} label={ctx.raw_label || 'Стейк в разбор'} hint="вход"
            value={raw} onChange={setRaw} placeholder="10"
            foot={ctx.price_per_kg ? `${tenge(ctx.price_per_kg)}/кг` : 'цена не найдена'} />
          <Tile photo={ph.slice} glyph={GLYPH.slice} label="Нарезка" hint="выход" value={slice}
            onChange={setSlice} placeholder="8"
            foot={w ? `${pc(w.slice_pct)}${w.slice!.price_per_kg ? ` · ${tenge(w.slice!.price_per_kg)}/кг` : ''}` : undefined} />
          <Tile photo={ph.trim} glyph={GLYPH.trim} label="Обрезки"
            hint={shownTrim ? `по ${tenge(shownTrim)}` : 'цену ниже'}
            value={trim} onChange={setTrim} placeholder="2"
            foot={w ? `${pc(w.trim_pct)} веса` : undefined} />
          {/* Отход не вводят: он остаток веса. Поэтому здесь и вес, и процент,
              и само вычитание — видно, из чего он получился, а не только
              сколько вышло. */}
          <Tile photo={ph.waste} glyph={GLYPH.waste} label="Отход" hint="кости, кожа" readOnly
            value={w ? kg(w.waste!.weight) : ''}
            tone={w ? wasteTone(w.waste_pct || 0) : undefined}
            foot={w
              ? <>
                  <b style={{ color: wasteTone(w.waste_pct || 0) }}>{pc(w.waste_pct)} отхода</b>
                  <div style={{ marginTop: 2, fontSize: 11, color: 'var(--mute)' }}>
                    {kg(w.raw!.weight)} − {kg(w.slice!.weight)} − {kg(w.trim!.weight)}
                  </div>
                </>
              : `${short} − нарезка − обрезки`} />
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
          <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Цена входа, ₸/кг
            <input value={price} inputMode="decimal"
              placeholder={ctx.price_per_kg ? String(ctx.price_per_kg) : 'нет в накладных'}
              onChange={(e) => setPrice(e.target.value)} style={{ ...field, marginTop: 5 }} />
          </label>
          {askTrimPrice && (
            <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Обрезки, ₸/кг
              <input value={trimPrice} inputMode="decimal" placeholder="почём считать"
                onChange={(e) => setTrimPrice(e.target.value)} style={{ ...field, marginTop: 5 }} />
            </label>
          )}
        </div>

        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--mute)' }}>
          {ctx.price_per_kg
            ? <>Себестоимость входа: <b style={{ color: 'var(--hi)' }}>{tenge(ctx.price_per_kg)}/кг</b>{ctx.price_from ? ` · ${ctx.price_from}` : ''}. Поле можно переписать.</>
            : <>Себестоимости входа в iiko нет, и в накладных филиала его тоже нет — впиши цену вручную.</>}
          {askTrimPrice && <> Себестоимости обрезков этой рыбы в iiko нет, а константы учёта у неё не назначено — цену вводят здесь.</>}
          {!askTrimPrice && ctx.trim_from
            && <> Обрезки — <b style={{ color: 'var(--hi)' }}>{tenge(ctx.trim_price || 0)}/кг</b> · {ctx.trim_from}.</>}
        </div>

        {/* Веса посчитаны, денег нет: говорим ровно, какой цены не хватило, и
            не прячем при этом отход — он к цене отношения не имеет. */}
        {w && !w.money && (
          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--amb)' }}>
            {w.need_text || 'Не хватает цены, чтобы посчитать деньги.'} Веса и проценты ниже уже посчитаны.
          </div>
        )}

        {calc && !calc.ok && (
          <div style={{ marginTop: 12, fontSize: 13, color: 'var(--red)' }}>{calc.error}</div>
        )}

        {w && !w.money && (
          <div style={{ marginTop: 14, display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--mute)' }}>
            <span>Отход <b style={{ color: wasteTone(w.waste_pct || 0) }}>{kg(w.waste!.weight)} кг</b> · {pc(w.waste_pct)}</span>
            <span>В нарезку <b style={{ color: 'var(--grn)' }}>{kg(w.slice!.weight)} кг</b> · {pc(w.slice_pct)}</span>
            <span>В обрезки <b style={{ color: 'var(--hi)' }}>{kg(w.trim!.weight)} кг</b> · {pc(w.trim_pct)}</span>
          </div>
        )}

        {w && w.money && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 16 }}>
              <thead><tr>
                <th style={th}>Позиция</th>
                <th style={{ ...th, textAlign: 'right' }}>Вес, кг</th>
                <th style={{ ...th, textAlign: 'right' }}>₸ / кг</th>
                <th style={{ ...th, textAlign: 'right' }}>Сумма</th>
              </tr></thead>
              <tbody>
                <tr>
                  <td style={td}>{nameOf(p.raw, 'raw')} <span style={{ color: 'var(--mute)', fontSize: 11 }}>· {noProd(p.raw) ? 'нет в номенклатуре' : 'вход'}</span></td>
                  <td style={tdR}>{kg(w.raw!.weight)}</td>
                  <td style={tdR}>{tenge(w.raw!.price_per_kg)}</td>
                  <td style={tdR}>{tenge(w.raw!.sum)}</td>
                </tr>
                <tr>
                  <td style={{ ...td, color: 'var(--grn)', fontWeight: 650 }}>{nameOf(p.slice, 'slice')} <span style={{ color: 'var(--mute)', fontSize: 11, fontWeight: 400 }}>· {noProd(p.slice) ? 'нет в номенклатуре' : 'остаток стоимости'}</span></td>
                  <td style={tdR}><b>{kg(w.slice!.weight)}</b></td>
                  <td style={tdR}><b>{tenge(w.slice!.price_per_kg)}</b></td>
                  <td style={tdR}><b>{tenge(w.slice!.sum)}</b></td>
                </tr>
                <tr>
                  <td style={td}>{nameOf(p.trim, 'trim')}{noProd(p.trim) && <span style={{ color: 'var(--mute)', fontSize: 11 }}> · нет в номенклатуре</span>}</td>
                  <td style={tdR}>{kg(w.trim!.weight)}</td>
                  <td style={tdR}>{tenge(w.trim!.price_per_kg)}</td>
                  <td style={tdR}>{tenge(w.trim!.sum)}</td>
                </tr>
                <tr>
                  <td style={{ ...td, color: 'var(--mute)' }}>{nameOf(p.waste, 'waste')} <span style={{ fontSize: 11 }}>· по нулю</span></td>
                  <td style={tdR}>{kg(w.waste!.weight)}</td>
                  <td style={tdR}>0</td>
                  <td style={tdR}>0</td>
                </tr>
              </tbody>
            </table>

            <div style={{ marginTop: 10, display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--mute)' }}>
              <span>Отход <b style={{ color: wasteTone(w.waste_pct || 0) }}>{kg(w.waste!.weight)} кг</b> · {pc(w.waste_pct)}</span>
              <span>В обрезки <b style={{ color: 'var(--hi)' }}>{kg(w.trim!.weight)} кг</b> · {pc(w.trim_pct)}</span>
              {/* Здесь стояла «Сходимость: X из Y» зелёным. Она не могла не
                  сойтись: нарезка считается как «вход − обрезки», а сходимость —
                  как «обрезки + отход + нарезка», то есть тот же вход длинной
                  записью. Она показывала зелёное даже когда всё ушло в отход.
                  Галочка, которая не умеет краснеть, приучает верить цифрам,
                  которых никто не проверял. Настоящий контроль здесь — процент
                  отхода слева, он и остался. */}
              <span style={{ marginLeft: 'auto' }}>
                себестоимость нарезки — <b style={{ color: 'var(--hi)' }}>{tenge(w.slice!.price_per_kg)}/кг</b>
              </span>
            </div>

            <div style={{ marginTop: 14, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button onClick={save} disabled={saving || missing}>
                {saving ? 'Записываю…' : 'Зафиксировать разбор'}
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
                Разбор записан · нарезка {kg(saved.slice?.weight)} кг по {tenge(saved.slice?.price_per_kg || 0)}/кг
              </div>
              <div style={{ marginTop: 4, fontSize: 12.5, color: 'var(--mute)' }}>
                Он попал в свод за период — ниже. Ошиблись? Разбор можно удалить, пока свод не занесён в iiko.
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Glass>

      <SalmonPeriod token={token} org={org} kind={kind} reload={reload} />
      <SalmonNetwork token={token} kind={kind} reload={reload} />
    </>
  )
}
