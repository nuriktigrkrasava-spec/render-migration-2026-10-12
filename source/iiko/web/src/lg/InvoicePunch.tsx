import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { getBranches } from '../lib/api'
import { useOrg } from '../lib/useApi'
import { tenge } from '../lib/format'
import InvoicePrices from './InvoicePrices'
import PickSupplier from './PickSupplier'

/* «Пробить накладную» — весь путь на сайте, без телеграма:
   1 фото → 2 распознавание → 3 сопоставление → 4 отправка в iiko → 5 проведение.

   Филиал не выбирается: накладная уходит в тот, что выбран сверху на сайте, и
   он подписан на первом шаге. Склад, комментарий и поставщик — как в редакторе
   мини-аппа (store_id / comment / supplier_id доезжают до iikoOffice).
   Лучшая цена — по КАЖДОМУ товару номенклатуры, тот же /api/price-stats, что
   в мини-аппе. Локальный черновик создаётся молча перед отправкой: это
   механика, а не шаг для оператора. */

type Item = {
  name: string; quantity?: number; unit?: string; price_per_unit?: number; total?: number
  iiko_product_id?: string; iiko_product_name?: string; iiko_unit?: string
  needs_review?: boolean; match_score?: number
}
type Recognized = {
  ok: boolean; error?: string; supplier?: string; date?: string
  supplier_id?: string; supplier_matched?: string
  total_amount?: number; vat_amount?: number
  items?: Item[]; matched?: number; unmatched?: number; needs_review?: number
}
type Product = { id: string; name: string; unit?: string; containers?: unknown[] }
type Supplier = { id: string; name: string }
type Store = { id: string; name: string }
type PriceRow = { supplier_name: string; price_per_unit: number; unit?: string; date?: string; delta_pct?: number }
type PriceStats = {
  product?: { name?: string; unit?: string }
  best?: PriceRow | null
  suppliers?: PriceRow[]
  stats?: { purchases?: number; days?: number }
  iiko_ok?: boolean
}

const MAX_MB = 12
const STEPS = ['Фото', 'Распознавание', 'Сопоставление', 'Отправка в iiko', 'Проведение']

function fileToBase64(f: File): Promise<{ data: string; mime: string }> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onerror = () => reject(new Error('файл не читается'))
    r.onload = () => resolve({ data: String(r.result).split(',', 2)[1] || '', mime: f.type || 'image/jpeg' })
    r.readAsDataURL(f)
  })
}

/* нормализация имени поставщика для автоподбора: «ТОО "Альфа"» ≈ «Альфа ТОО» */
const norm = (s: string) => (s || '').toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9]+/g, ' ').trim()
function guessSupplier(list: Supplier[], recognized?: string): Supplier | null {
  const want = norm(recognized || '')
  if (!want || !list.length) return null
  const words = want.split(' ').filter((w) => w.length > 2 && !['тоо', 'ооо', 'ип', 'зао'].includes(w))
  let best: Supplier | null = null
  let bestHit = 0
  for (const s of list) {
    const n = norm(s.name)
    const hit = words.filter((w) => n.includes(w)).length
    if (hit > bestHit) { bestHit = hit; best = s }
  }
  return bestHit ? best : null
}

const th: React.CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 9px', borderBottom: '1px solid var(--hair)' }
const td: React.CSSProperties = { padding: '10px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13, verticalAlign: 'top' }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const cell: React.CSSProperties = { padding: '5px 7px', fontSize: 12.5, textAlign: 'right', fontFamily: 'inherit', fontVariantNumeric: 'tabular-nums', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 8 }
const field: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '9px 11px', fontSize: 13, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 11 }

function Steps({ at }: { at: number }) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
      {STEPS.map((s, i) => {
        const done = i < at, now = i === at
        return (
          <div key={s} style={{
            display: 'inline-flex', alignItems: 'center', gap: 7, padding: '7px 12px', borderRadius: 11,
            border: '1px solid var(--hair)', background: now ? 'var(--glass-2)' : 'transparent',
            color: done ? 'var(--grn)' : now ? 'var(--hi)' : 'var(--mute)',
            fontSize: 11.5, fontWeight: 650,
          }}>
            <span style={{ opacity: .7 }}>{done ? '✓' : i + 1}</span>{s}
          </div>
        )
      })}
    </div>
  )
}

/* Лучшая цена по товару — копия смысла карточки «Поиск цены» из мини-аппа */
function BestPrice({ org, productId, ourPrice }: { org: string; productId: string; ourPrice?: number }) {
  const [d, setD] = useState<PriceStats | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    fetch(`/api/price-stats/${org}?product_id=${encodeURIComponent(productId)}&days=60`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((x: PriceStats) => alive && setD(x))
      .catch((e) => alive && setErr(String(e.message || e)))
    return () => { alive = false }
  }, [org, productId])

  if (err) return <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>цены не загрузились</div>
  if (!d) return <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>считаю цены…</div>
  const rows = d.suppliers ?? []
  if (!rows.length) return <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>закупок за {d.stats?.days ?? 60} дней не было</div>
  const best = d.best
  const over = best && ourPrice && best.price_per_unit > 0 ? ((ourPrice - best.price_per_unit) / best.price_per_unit) * 100 : null
  return (
    <div style={{ marginTop: 8, borderLeft: '2px solid var(--hair)', paddingLeft: 12 }}>
      {best && (
        <div style={{ fontSize: 12.5 }}>
          <span style={{ color: 'var(--grn)', fontWeight: 700 }}>🟢 Лучшая цена </span>
          <span className="tnum" style={{ fontWeight: 700, color: 'var(--hi)' }}>{tenge(best.price_per_unit)}</span>
          <span style={{ color: 'var(--mute)' }}>/{best.unit || d.product?.unit || 'ед'} · {best.supplier_name}{best.date ? ` · ${best.date}` : ''}</span>
          {over != null && over > 1 && (
            <span style={{ color: 'var(--red)', fontWeight: 700 }}> · сейчас дороже на {over.toFixed(0)}%</span>
          )}
        </div>
      )}
      <div style={{ marginTop: 6, display: 'grid', gap: 4 }}>
        {rows.slice(0, 6).map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, fontSize: 12, color: 'var(--mute)' }}>
            <span style={{ flex: 1 }}>{s.supplier_name}{s.date ? ` · ${s.date}` : ''}</span>
            <span className="tnum" style={{ color: 'var(--hi)' }}>{tenge(s.price_per_unit)}/{s.unit || d.product?.unit || 'ед'}</span>
            <span style={{ width: 52, textAlign: 'right', color: (s.delta_pct ?? 0) >= 10 ? 'var(--red)' : 'var(--mute)' }}>
              {s.delta_pct != null && Math.abs(s.delta_pct) > 0.5 ? `${s.delta_pct > 0 ? '+' : ''}${s.delta_pct.toFixed(0)}%` : '—'}
            </span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 6, fontSize: 11, color: 'var(--mute)' }}>
        Закупок за {d.stats?.days ?? 60} дней: {d.stats?.purchases ?? 0}
        {d.iiko_ok === false && ' · iiko недоступен, только данные бота'}
      </div>
    </div>
  )
}

/* Ручной подбор товара для строки, которая не сопоставилась или помечена «проверьте» */
function ProductPicker({ org, onPick }: { org: string; onPick: (p: Product) => void }) {
  const [q, setQ] = useState('')
  const [list, setList] = useState<Product[]>([])
  useEffect(() => {
    if (q.trim().length < 2) { setList([]); return }
    let alive = true
    const t = setTimeout(() => {
      fetch(`/api/nomenclature/search/${org}?q=${encodeURIComponent(q)}&limit=8`, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: { products?: Product[] }) => alive && setList(d.products ?? []))
        .catch(() => alive && setList([]))
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q, org])
  return (
    <div style={{ marginTop: 8 }}>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти товар в iiko" style={field} />
      {list.map((p) => (
        <button key={p.id} onClick={() => onPick(p)}
          style={{ display: 'block', width: '100%', textAlign: 'left', marginTop: 6, padding: '8px 11px', borderRadius: 10, border: '1px solid var(--hair)', background: 'var(--glass-2)', color: 'var(--hi)', fontSize: 12.5, cursor: 'pointer' }}>
          {p.name}{p.unit ? ` · ${p.unit}` : ''}
        </button>
      ))}
    </div>
  )
}

export default function InvoicePunch() {
  const input = useRef<HTMLInputElement>(null)
  const org = useOrg()
  const [branch, setBranch] = useState('')
  const [busy, setBusy] = useState(false)
  const [rec, setRec] = useState<Recognized | null>(null)
  const [manual, setManual] = useState(false)   // накладную вбивают руками, без фото
  const [items, setItems] = useState<Item[]>([])
  const [openRow, setOpenRow] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [supplierId, setSupplierId] = useState('')
  const [storeId, setStoreId] = useState('')
  const [comment, setComment] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState<{ number?: string; id?: string; invoiceId?: number } | null>(null)
  const [posted, setPosted] = useState(false)
  const [posting, setPosting] = useState(false)

  useEffect(() => {
    getBranches().then((bs) => {
      const b = bs.find((x) => x.organization_id === org)
      setBranch(b?.organization_name || b?.name || '')
    }).catch(() => {})
  }, [org])

  // Итог в ручном режиме — сумма строк: доверять нечему, накладную вбивают руками.
  const rowsTotal = items.reduce((a, i) => a + (Number(i.total) || 0), 0)
  const unmatched = items.filter((i) => !i.iiko_product_id).length
  const review = items.filter((i) => i.iiko_product_id && i.needs_review).length
  const step = posted ? 5 : sent ? 4 : items.length ? (unmatched ? 2 : 3) : busy ? 1 : 0

  function loadRefs() {
    fetch(`/api/suppliers/${org}`).then((x) => x.json())
      .then((x: { suppliers?: Supplier[] }) => setSuppliers(x.suppliers ?? [])).catch(() => {})
    fetch(`/api/stores/${org}`).then((x) => x.json())
      .then((x: { stores?: Store[] }) => setStores(x.stores ?? [])).catch(() => {})
  }

  /* Ручной ввод: тот же путь, только без фото и распознавания. Нужен, когда
     накладная бумажная и мятая, поставщик прислал позиции текстом или надо
     провести одну строку — фотографировать ради этого нечего. */
  function startManual() {
    setErr(null); setRec({ ok: true, date: new Date().toISOString().slice(0, 10), items: [] })
    setItems([]); setSent(null); setPosted(false); setManual(true); loadRefs()
    addRow()
  }

  function addRow() {
    setItems((prev) => [...prev, { name: '', quantity: 1, unit: '', price_per_unit: 0, total: 0 }])
  }

  function editRow(idx: number, patch: Partial<Item>) {
    setItems((prev) => prev.map((it, i) => {
      if (i !== idx) return it
      const next = { ...it, ...patch }
      const q = Number(next.quantity) || 0
      const p = Number(next.price_per_unit) || 0
      return { ...next, total: Math.round(q * p * 100) / 100 }
    }))
  }

  function dropRow(idx: number) {
    setItems((prev) => prev.filter((_, i) => i !== idx))
  }

  async function onPick(list: FileList | null) {
    if (!list || !list.length) return
    const files = Array.from(list).slice(0, 10)
    const big = files.find((f) => f.size > MAX_MB * 1024 * 1024)
    if (big) { setErr(`${big.name}: больше ${MAX_MB} МБ`); return }
    setErr(null); setBusy(true); setRec(null); setItems([]); setSent(null)
    try {
      const images = await Promise.all(files.map(fileToBase64))
      const r = await fetch(`/api/invoice/recognize/${org}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ images }),
      })
      const d: Recognized = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }))
      if (!r.ok || !d.ok) { setErr(d.error || `HTTP ${r.status}`); return }
      setRec(d); setItems(d.items ?? []); setManual(false)
      // справочники нужны только на шаге отправки — тянем их сразу после разбора
      fetch(`/api/suppliers/${org}`).then((x) => x.json()).then((x: { suppliers?: Supplier[] }) => {
        const list = x.suppliers ?? []
        setSuppliers(list)
        // Поставщика подбирает бэкенд по справочнику iiko (_best_supplier_match):
        // он видит настоящие имена со всей их грязью — кавычками, ТОО, казахскими
        // буквами. Здешний guessSupplier остаётся страховкой на случай, когда
        // бэкенд не уверен: подставить кандидата лучше, чем пустой список.
        if (d.supplier_id && list.some((s) => s.id === d.supplier_id)) {
          setSupplierId(d.supplier_id)
        } else {
          const g = guessSupplier(list, d.supplier)
          if (g) setSupplierId(g.id)
        }
      }).catch(() => {})
      fetch(`/api/stores/${org}`).then((x) => x.json()).then((x: { stores?: Store[] }) => setStores(x.stores ?? [])).catch(() => {})
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  function applyProduct(idx: number, p: Product) {
    const line = items[idx]?.name || p.name
    setItems((prev) => prev.map((it, i) => i === idx
      ? {
          ...it, iiko_product_id: p.id, iiko_product_name: p.name,
          iiko_unit: p.unit || '', needs_review: false,
          // ручная строка пустая: имя и единицу берём у выбранного товара
          name: it.name || p.name,
          unit: it.unit || p.unit || '',
        }
      : it))
    setOpenRow(null)
    /* Запоминаем выбор СРАЗУ — так же делает iiko со своим прайс-листом
       поставщика. Раньше соответствие писалось только после успешной отправки
       накладной: оператор мог разобрать десять строк и не отправить, и вся
       работа пропадала — следующая накладная приходила такой же пустой.
       Ответ не ждём: справочник — побочная польза, разбор важнее. */
    fetch(`/api/invoice/match-memory/${org}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: line, product_id: p.id, product_name: p.name }),
    }).catch(() => {})
  }

  const recentKey = `inv-suppliers:${org}`
  const [recent, setRecent] = useState<string[]>([])
  useEffect(() => {
    try { setRecent(JSON.parse(localStorage.getItem(recentKey) || '[]')) } catch { setRecent([]) }
  }, [recentKey])

  function rememberSupplier(id: string) {
    // список короткий: пять последних, свежие сверху, без повторов
    const next = [id, ...recent.filter((x) => x !== id)].slice(0, 5)
    setRecent(next)
    try { localStorage.setItem(recentKey, JSON.stringify(next)) } catch { /* приватный режим */ }
  }

  async function send() {
    if (!supplierId) { setErr('Выбери поставщика'); return }
    if (unmatched) { setErr(`Не сопоставлено ${unmatched} позиций — выбери товар для каждой`); return }
    setErr(null); setSending(true)
    try {
      // черновик создаётся молча: оператору он не нужен как шаг, но ручке отправки нужен id
      const dr = await fetch(`/api/invoice/draft/${org}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items, supplier: rec?.supplier, supplier_id: supplierId,
          date: rec?.date, total_amount: manual ? rowsTotal : rec?.total_amount, vat_amount: rec?.vat_amount,
        }),
      })
      const d = await dr.json()
      if (!dr.ok || !d.invoice_id) { setErr(d.detail || d.error || `HTTP ${dr.status}`); return }
      const sr = await fetch(`/api/invoice-saved/${d.invoice_id}/send`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items, supplier: rec?.supplier, supplier_id: supplierId, organization_id: org,
          date: rec?.date, total_amount: manual ? rowsTotal : rec?.total_amount, vat_amount: rec?.vat_amount,
          store_id: storeId || null, comment: comment || null,
        }),
      })
      const s = await sr.json()
      if (!sr.ok || !s.ok) { setErr(s.error || s.detail || `HTTP ${sr.status}`); return }
      rememberSupplier(supplierId)
      setSent({ number: s.iiko_doc_number, id: s.iiko_doc_id, invoiceId: d.invoice_id })
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setSending(false)
    }
  }

  /* Проведение — как в мини-аппе (invoicePost/invoiceUnpost): та же ручка,
     то же подтверждение. Черновик в iiko не меняет остатки, пока не проведён,
     поэтому доводим накладную до конца здесь, а не «потом в iikoOffice». */
  async function post(un = false) {
    if (!sent?.invoiceId) return
    const q = un ? 'Распровести накладную?' : 'Провести накладную в iiko?'
    if (!window.confirm(q)) return
    setErr(null); setPosting(true)
    try {
      const r = await fetch(`/api/invoices/${sent.invoiceId}/${un ? 'unpost' : 'post'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok || d.ok === false) { setErr(d.error || d.detail || `HTTP ${r.status}`); return }
      setPosted(!un)
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setPosting(false)
    }
  }

  return (
    <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--hair)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button onClick={() => input.current?.click()} disabled={busy}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 9, padding: '11px 18px', borderRadius: 14, border: '1px solid var(--hair)', background: 'var(--glass-2)', color: 'var(--hi)', fontSize: 13.5, fontWeight: 650, cursor: busy ? 'default' : 'pointer', opacity: busy ? .6 : 1 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 7h3l2-2h6l2 2h3v12H4z" /><circle cx="12" cy="13" r="3.4" />
          </svg>
          {busy ? 'Читаю накладную…' : 'Пробить накладную'}
        </button>
        <button onClick={startManual} disabled={busy}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '11px 16px', borderRadius: 14, border: '1px solid var(--hair)', background: 'transparent', color: 'var(--hi)', fontSize: 13.5, fontWeight: 650, cursor: 'pointer' }}>
          Ввести вручную
        </button>
        <span style={{ fontSize: 12, color: 'var(--mute)' }}>
          уйдёт в филиал <b style={{ color: 'var(--hi)' }}>{branch || '—'}</b> · фото или скан, до 10 страниц
        </span>
        <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => onPick(e.target.files)} />
      </div>

      <Steps at={step} />

      {err && <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--red)' }}>{err}</div>}

      <AnimatePresence>
        {rec && !sent && (
          <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            style={{ marginTop: 14, border: '1px solid var(--hair)', borderRadius: 16, padding: '16px 18px', background: 'var(--glass-2)' }}>
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'baseline' }}>
              <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--hi)' }}>{rec.supplier || (manual ? 'Ручная накладная' : 'Поставщик не распознан')}</div>
              {/* Что именно подставлено в iiko: с фото приходит «ТОО "Ер-Амаль"»,
                  а в справочнике он заведён как « Ер-Амаль» — показываем обоих,
                  иначе непонятно, почему в списке выбрано другое название. */}
              {rec.supplier_matched && rec.supplier_matched !== rec.supplier && (
                <div style={{ fontSize: 11.5, color: 'var(--grn)', fontWeight: 600 }}>✓ в iiko: {rec.supplier_matched}</div>
              )}
              <div style={{ fontSize: 12, color: 'var(--mute)' }}>{rec.date || 'без даты'}</div>
              <div className="tnum" style={{ marginLeft: 'auto', fontSize: 16, fontWeight: 700, color: 'var(--hi)' }}>{tenge(manual ? rowsTotal : (rec.total_amount || 0))}</div>
            </div>
            <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>
              Сопоставлено {items.length - unmatched} из {items.length}
              {review ? ` · проверить ${review}` : ''}{unmatched ? ` · без товара ${unmatched}` : ''}
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 14 }}>
              <thead><tr>
                <th style={th}>Строка накладной</th>
                <th style={th}>Товар в iiko · цены поставщиков</th>
                <th style={{ ...th, textAlign: 'right' }}>Кол-во</th>
                <th style={{ ...th, textAlign: 'right' }}>Сумма</th>
              </tr></thead>
              <tbody>
                {items.map((it, i) => {
                  const ok = !!it.iiko_product_id
                  const tone = !ok ? 'var(--red)' : it.needs_review ? 'var(--amb)' : 'var(--grn)'
                  const isOpen = openRow === i
                  return (
                    <tr key={i}>
                      <td style={td}>{it.name || '—'}</td>
                      <td style={td}>
                        <button onClick={() => setOpenRow(isOpen ? null : i)}
                          style={{ background: 'none', border: 'none', padding: 0, color: tone, fontSize: 13, cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit' }}>
                          {ok ? `${it.needs_review ? '🔎 ' : '✓ '}${it.iiko_product_name || ''}` : '— не найден, выбрать'}
                          {ok && it.match_score != null && <span style={{ fontSize: 10.5, color: 'var(--mute)', marginLeft: 6 }}>{it.match_score}</span>}
                        </button>
                        {isOpen && (
                          <>
                            <ProductPicker org={org} onPick={(p) => applyProduct(i, p)} />
                            {ok && <BestPrice org={org} productId={it.iiko_product_id!} ourPrice={it.price_per_unit} />}
                          </>
                        )}
                      </td>
                      <td style={tdR}>
                        <input value={it.quantity ?? ''} inputMode="decimal"
                          onChange={(e) => editRow(i, { quantity: Number(e.target.value.replace(',', '.')) || 0 })}
                          style={{ ...cell, width: 62 }} />
                        <input value={it.unit ?? ''} placeholder="ед"
                          onChange={(e) => editRow(i, { unit: e.target.value })}
                          style={{ ...cell, width: 46, marginLeft: 4 }} />
                      </td>
                      <td style={tdR}>
                        <input value={it.price_per_unit ?? ''} inputMode="decimal"
                          onChange={(e) => editRow(i, { price_per_unit: Number(e.target.value.replace(',', '.')) || 0 })}
                          style={{ ...cell, width: 78 }} title="цена за единицу" />
                        <div style={{ fontSize: 11, color: 'var(--mute)', marginTop: 3 }}>
                          = {tenge(it.total || 0)}
                          <button onClick={() => dropRow(i)} title="убрать строку"
                            style={{ marginLeft: 8, background: 'none', border: 'none', color: 'var(--mute)', cursor: 'pointer', fontSize: 13 }}>×</button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <div style={{ marginTop: 10, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <button onClick={addRow}
                style={{ padding: '8px 14px', borderRadius: 11, border: '1px dashed var(--hair)', background: 'transparent', color: 'var(--hi)', fontSize: 12.5, fontWeight: 650, cursor: 'pointer' }}>
                + Добавить позицию
              </button>
              <span className="tnum" style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--mute)' }}>
                Итого по строкам: <b style={{ color: 'var(--hi)' }}>{tenge(rowsTotal)}</b>
              </span>
            </div>

            {/* Сколько эта накладная стоила лишнего — одним числом, до отправки */}
            <InvoicePrices org={org} items={items} />

            {/* шаг 4: поставщик, склад, комментарий — как в редакторе мини-аппа */}
            <div style={{ marginTop: 16, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))' }}>
              <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Поставщик
                <PickSupplier suppliers={suppliers} value={supplierId} onChange={setSupplierId}
                  recent={recent} field={field} />
              </label>
              <label style={{ fontSize: 11.5, color: 'var(--mute)' }}>Склад
                <select value={storeId} onChange={(e) => setStoreId(e.target.value)} style={{ ...field, marginTop: 5 }}>
                  <option value="">по умолчанию</option>
                  {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </label>
              <label style={{ fontSize: 11.5, color: 'var(--mute)', gridColumn: '1 / -1' }}>Комментарий для iiko Office
                <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2}
                  placeholder="необязательно" style={{ ...field, marginTop: 5, resize: 'vertical' }} />
              </label>
            </div>

            <button onClick={send} disabled={sending || !!unmatched}
              style={{ marginTop: 14, padding: '12px 20px', borderRadius: 14, border: 'none', background: unmatched ? 'var(--hair)' : 'linear-gradient(135deg,var(--acc),var(--acc-2))', color: unmatched ? 'var(--mute)' : 'var(--on-acc)', fontSize: 13.5, fontWeight: 700, cursor: sending || unmatched ? 'default' : 'pointer' }}>
              {sending ? 'Отправляю…' : unmatched ? `Сначала сопоставь ${unmatched} позиций` : 'Отправить в iiko'}
            </button>
          </motion.div>
        )}

        {sent && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{ marginTop: 14, border: '1px solid var(--hair)', borderRadius: 16, padding: '16px 18px', background: 'var(--glass-2)' }}>
            <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--grn)' }}>
              {posted ? 'Накладная проведена' : 'Накладная в iiko'}{sent.number ? ` · №${sent.number}` : ''}
            </div>
            <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--mute)' }}>
              Филиал {branch || '—'}. {posted
                ? 'Проведена: остатки и себестоимость в iiko обновлены.'
                : 'Пока черновик — на остатки не влияет, пока не проведёшь.'}
            </div>
            <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {!posted ? (
                <button onClick={() => post(false)} disabled={posting}
                  style={{ padding: '11px 18px', borderRadius: 12, border: 'none', background: 'linear-gradient(135deg,var(--acc),var(--acc-2))', color: 'var(--on-acc)', fontSize: 13, fontWeight: 700, cursor: posting ? 'default' : 'pointer', opacity: posting ? .6 : 1 }}>
                  {posting ? 'Провожу…' : 'Провести в iiko'}
                </button>
              ) : (
                <button onClick={() => post(true)} disabled={posting}
                  style={{ padding: '11px 18px', borderRadius: 12, border: '1px solid var(--hair)', background: 'transparent', color: 'var(--mute)', fontSize: 13, fontWeight: 650, cursor: posting ? 'default' : 'pointer' }}>
                  {posting ? 'Распровожу…' : 'Распровести'}
                </button>
              )}
              <button onClick={() => { setRec(null); setItems([]); setSent(null); setPosted(false); setComment('') }}
                style={{ padding: '11px 18px', borderRadius: 12, border: '1px solid var(--hair)', background: 'transparent', color: 'var(--hi)', fontSize: 13, fontWeight: 650, cursor: 'pointer' }}>
                Пробить ещё одну
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
