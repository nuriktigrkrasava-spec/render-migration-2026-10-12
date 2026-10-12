import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useOrg } from '../lib/useApi'
import { tenge } from '../lib/format'

/* «Поиск цены» — самостоятельный инструмент, как одноимённый экран мини-аппа.
   Вход — водитель фуры на Складе. Ищем любой товар номенклатуры и смотрим, у
   кого он дешевле: тот же /api/price-stats?product_id=&days=60, та же карточка
   «🟢 Лучшая цена» + все поставщики с процентом отклонения. К накладной не
   привязан: сюда заходят перед заказом, а не после. */

type Product = { id: string; name: string; unit?: string }
type PriceRow = { supplier_name: string; price_per_unit: number; unit?: string; date?: string; delta_pct?: number }
type PriceStats = {
  product?: { name?: string; unit?: string }
  best?: PriceRow | null
  suppliers?: PriceRow[]
  stats?: { purchases?: number; days?: number }
  iiko_ok?: boolean
}

const field: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '11px 13px', fontSize: 14, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 12 }

export default function PriceSearch({ onClose }: { onClose: () => void }) {
  const org = useOrg()
  const [q, setQ] = useState('')
  const [list, setList] = useState<Product[]>([])
  const [picked, setPicked] = useState<Product | null>(null)
  const [data, setData] = useState<PriceStats | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (picked || q.trim().length < 2) { setList([]); return }
    let alive = true
    const t = setTimeout(() => {
      fetch(`/api/nomenclature/search/${org}?q=${encodeURIComponent(q)}&limit=10`, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: { products?: Product[] }) => alive && setList(d.products ?? []))
        .catch(() => alive && setList([]))
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q, org, picked])

  function pick(p: Product) {
    setPicked(p); setQ(p.name); setList([]); setData(null); setErr(null)
    fetch(`/api/price-stats/${org}?product_id=${encodeURIComponent(p.id)}&days=60`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: PriceStats) => setData(d))
      .catch((e) => setErr(String(e.message || e)))
  }

  const rows = data?.suppliers ?? []
  const best = data?.best
  const unit = best?.unit || data?.product?.unit || picked?.unit || 'ед'

  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      style={{ marginTop: 14, border: '1px solid var(--hair)', borderRadius: 16, padding: '16px 18px', background: 'var(--glass-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--hi)' }}>Поиск цены</div>
        <div style={{ fontSize: 12, color: 'var(--mute)' }}>у кого товар дешевле · 60 дней закупок</div>
        <button onClick={onClose} style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--mute)', fontSize: 18, cursor: 'pointer', lineHeight: 1 }}>×</button>
      </div>

      <div style={{ marginTop: 12 }}>
        <input autoFocus value={q} placeholder="Название товара — например, «лосось»"
               onChange={(e) => { setQ(e.target.value); setPicked(null); setData(null) }}
               style={field} />
        {list.map((p) => (
          <button key={p.id} onClick={() => pick(p)}
            style={{ display: 'block', width: '100%', textAlign: 'left', marginTop: 6, padding: '9px 12px', borderRadius: 11, border: '1px solid var(--hair)', background: 'var(--card)', color: 'var(--hi)', fontSize: 13, cursor: 'pointer' }}>
            {p.name}{p.unit ? ` · ${p.unit}` : ''}
          </button>
        ))}
      </div>

      <AnimatePresence>
        {picked && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} style={{ marginTop: 14 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--hi)' }}>{data?.product?.name || picked.name}</div>
            {err && <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--red)' }}>Не загрузилось: {err}</div>}
            {!data && !err && <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>Смотрю историю закупок…</div>}
            {data?.iiko_ok === false && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--red)' }}>iiko недоступен — показаны только данные бота.</div>
            )}
            {data && !rows.length && (
              <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>
                За {data.stats?.days ?? 60} дней закупок этого товара не было.
              </div>
            )}
            {best && (
              <div style={{ marginTop: 10, padding: '12px 14px', borderRadius: 13, border: '1px solid var(--hair)', background: 'var(--card)' }}>
                <div style={{ fontSize: 11.5, color: 'var(--grn)', fontWeight: 700 }}>🟢 Лучшая цена</div>
                <div className="tnum" style={{ fontSize: 24, fontWeight: 720, letterSpacing: '-.02em', color: 'var(--hi)', marginTop: 4 }}>
                  {tenge(best.price_per_unit)}<span style={{ fontSize: 13, color: 'var(--mute)', fontWeight: 600 }}>/{unit}</span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--hi)', marginTop: 4 }}>{best.supplier_name}</div>
                {best.date && <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>{best.date}</div>}
              </div>
            )}
            {rows.length > 0 && (
              <>
                <div style={{ marginTop: 14, fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>Все поставщики</div>
                <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
                  {rows.map((s, i) => (
                    <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 13, paddingBottom: 6, borderBottom: '1px solid var(--hair-2)' }}>
                      <span style={{ flex: 1, color: 'var(--hi)' }}>
                        {s.supplier_name}
                        {s.date && <span style={{ fontSize: 11, color: 'var(--mute)', marginLeft: 8 }}>{s.date}</span>}
                      </span>
                      <span className="tnum" style={{ color: 'var(--hi)' }}>{tenge(s.price_per_unit)}/{s.unit || unit}</span>
                      <span style={{ width: 56, textAlign: 'right', fontWeight: 700, color: (s.delta_pct ?? 0) >= 10 ? 'var(--red)' : 'var(--mute)' }}>
                        {s.delta_pct != null && Math.abs(s.delta_pct) > 0.5 ? `${s.delta_pct > 0 ? '+' : ''}${s.delta_pct.toFixed(0)}%` : '—'}
                      </span>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--mute)' }}>
                  Закупок за {data?.stats?.days ?? 60} дней: {data?.stats?.purchases ?? 0}
                </div>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
