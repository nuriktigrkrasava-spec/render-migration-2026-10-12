import { useCallback, useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { tenge } from '../lib/format'

/* «Что с ценами в этой накладной» — одна сводка вместо тридцати карточек.

   Главный вопрос при пробитии не «какая лучшая цена у колы», а «сколько мы на
   этой накладной потеряли». Поэтому переплата считается в деньгах и на
   фактическое количество: «дороже на 12 %» ничего не значит, пока не видно,
   что это 12 % от одной пачки или от двадцати ящиков.

   Переключатель «мой филиал / вся сеть» — потому что «у кого-то дешевле» чаще
   значит «у соседнего филиала», а не «у другого поставщика здесь же». */

type Line = {
  product_id: string; name: string; quantity: number; unit: string
  our_price: number; best_price: number; over_pct: number; money: number
  supplier_name: string; branch: string; date: string
}
type Overpay = { lines: Line[]; positions: number; cheaper: number; compared: number; total: number }
type Data = { overpay?: Overpay; scope?: string; branches?: string[]; days?: number }
type Item = {
  iiko_product_id?: string; iiko_product_name?: string; name: string
  quantity?: number; unit?: string; price_per_unit?: number
}

export default function InvoicePrices({ org, items }: { org: string; items: Item[] }) {
  const [network, setNetwork] = useState(false)
  const [d, setD] = useState<Data | null>(null)
  const [busy, setBusy] = useState(false)
  const still = useReducedMotion()
  const matched = items.filter((i) => i.iiko_product_id)

  const load = useCallback(() => {
    if (!org || !matched.length) { setD(null); return }
    setBusy(true)
    fetch(`/api/invoice-prices/${org}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: matched, days: 60, network }),
    }).then((r) => r.json()).then(setD).catch(() => setD(null)).finally(() => setBusy(false))
    // items меняются на каждый ввод — пересчитываем по составу, а не по ссылке
  }, [org, network, JSON.stringify(matched.map((i) => [i.iiko_product_id, i.quantity, i.price_per_unit]))])

  useEffect(load, [load])

  if (!matched.length) return null
  const o = d?.overpay
  const good = o && o.positions === 0

  return (
    <div style={{
      marginTop: 16, padding: '13px 15px', borderRadius: 16,
      border: '1px solid var(--hair)', background: 'var(--glass-2)',
    }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--hi)' }}>Цены по этой накладной</span>
        <label style={{ marginLeft: 'auto', display: 'flex', gap: 7, alignItems: 'center', fontSize: 12, color: 'var(--mute)', cursor: 'pointer' }}>
          <input type="checkbox" checked={network} onChange={(e) => setNetwork(e.target.checked)} />
          сравнить со всей сетью
        </label>
      </div>

      {busy && !d && <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>считаю цены…</div>}

      {o && (
        <>
          <div style={{ marginTop: 9, fontSize: 13 }}>
            {good ? (
              <span style={{ color: 'var(--grn)', fontWeight: 700 }}>
                Дороже прошлых закупок ничего нет
                {o.cheaper > 0 && <span style={{ color: 'var(--mute)', fontWeight: 400 }}> · {o.cheaper} поз. взяли дешевле</span>}
              </span>
            ) : (
              <>
                <span style={{ color: 'var(--mute)' }}>Переплата </span>
                <b className="tnum" style={{ fontSize: 19, color: 'var(--red)' }}>{tenge(o.total)}</b>
                <span style={{ color: 'var(--mute)' }}> · {o.positions} из {o.compared} сравненных позиций</span>
              </>
            )}
          </div>

          <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
            {o.lines.slice(0, 5).map((l, i) => (
              <motion.div key={l.product_id}
                initial={still ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                transition={{ type: 'spring', bounce: 0, duration: 0.35, delay: i * 0.04 }}
                style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 12.5, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--hi)', fontWeight: 600 }}>{l.name}</span>
                <span className="tnum" style={{ color: 'var(--mute)' }}>
                  {tenge(l.our_price)} вместо {tenge(l.best_price)}/{l.unit || 'ед'} · {l.quantity} {l.unit || 'ед'}
                </span>
                <span style={{ color: 'var(--mute)' }}>
                  дешевле у <b style={{ color: 'var(--hi)' }}>{l.supplier_name}</b>
                  {network && l.branch ? ` · ${l.branch}` : ''}{l.date ? ` · ${l.date}` : ''}
                </span>
                <b className="tnum" style={{ marginLeft: 'auto', color: 'var(--red)' }}>+{tenge(l.money)}</b>
              </motion.div>
            ))}
            {o.lines.length > 5 && (
              <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>…и ещё {o.lines.length - 5} позиций</span>
            )}
          </div>

          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--mute)' }}>
            Сравнение за {d?.days || 60} дней{d?.scope === 'network' && d.branches?.length
              ? ` · филиалы: ${d.branches.slice(0, 4).join(', ')}${d.branches.length > 4 ? ' и др.' : ''}`
              : ''}. Позиции без истории закупок не сравниваются.
          </div>
        </>
      )}
    </div>
  )
}
