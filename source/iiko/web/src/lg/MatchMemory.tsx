import { useCallback, useEffect, useState } from 'react'
import { useOrg } from '../lib/useApi'

/* «Справочник соответствий» — наш аналог прайс-листа поставщика в iikoOffice.

   Поставщик печатает «Масло слив. Крест. 82,5%», в номенклатуре это «Масло
   сливочное». Один раз связав их, оператор не должен связывать снова: связка
   ложится сюда и в следующей накладной подставляется сама.

   Экран нужен не ради красоты: связка живёт вечно и молча. Ошибочную видно
   только здесь — иначе она годами подставляет не тот товар, а найти её негде. */

type Entry = {
  name_norm: string
  name_raw: string
  product_id: string
  product_name: string
  hits: number
  updated_at: string
}
type Product = { id: string; name: string; unit?: string }

const th: React.CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 9px', borderBottom: '1px solid var(--hair)' }
const td: React.CSSProperties = { padding: '10px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13, verticalAlign: 'top' }
const field: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '8px 11px', fontSize: 12.5, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 10 }

/* Тот же подбор товара, что в редакторе накладной: искать в номенклатуре iiko. */
function Picker({ org, onPick }: { org: string; onPick: (p: Product) => void }) {
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
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти другой товар" style={field} autoFocus />
      {list.map((p) => (
        <button key={p.id} onClick={() => onPick(p)}
          style={{ display: 'block', width: '100%', textAlign: 'left', marginTop: 6, padding: '8px 11px', borderRadius: 10, border: '1px solid var(--hair)', background: 'var(--glass-2)', color: 'var(--hi)', fontSize: 12.5, cursor: 'pointer' }}>
          {p.name}{p.unit ? ` · ${p.unit}` : ''}
        </button>
      ))}
    </div>
  )
}

export default function MatchMemory() {
  const org = useOrg()
  const [rows, setRows] = useState<Entry[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [edit, setEdit] = useState<string | null>(null)   // name_norm правимой строки
  const [q, setQ] = useState('')

  const load = useCallback(() => {
    if (!org) return
    fetch(`/api/invoice/match-memory/${org}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { entries?: Entry[] }) => { setRows(d.entries ?? []); setErr(null) })
      .catch((e) => setErr(String(e.message || e)))
  }, [org])

  useEffect(() => { if (open) load() }, [open, load])

  async function forget(e: Entry) {
    if (!window.confirm(`Забыть связку «${e.name_raw}» → «${e.product_name}»?`)) return
    await fetch(`/api/invoice/match-memory/${org}?name_norm=${encodeURIComponent(e.name_norm)}`,
      { method: 'DELETE' }).catch(() => {})
    load()
  }

  async function rebind(e: Entry, p: Product) {
    await fetch(`/api/invoice/match-memory/${org}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: e.name_raw, product_id: p.id, product_name: p.name }),
    }).catch(() => {})
    setEdit(null)
    load()
  }

  const shown = (rows ?? []).filter((r) =>
    !q.trim() || `${r.name_raw} ${r.product_name}`.toLowerCase().includes(q.trim().toLowerCase()))

  return (
    <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--hair)' }}>
      <button onClick={() => setOpen((v) => !v)}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 9, padding: '10px 16px', borderRadius: 13, border: '1px solid var(--hair)', background: 'transparent', color: 'var(--hi)', fontSize: 13, fontWeight: 650, cursor: 'pointer' }}>
        <span style={{ opacity: .6 }}>{open ? '▾' : '▸'}</span>
        Справочник соответствий
        {rows && <span style={{ color: 'var(--mute)', fontWeight: 500 }}>· {rows.length}</span>}
      </button>

      {open && (
        <div style={{ marginTop: 12, border: '1px solid var(--hair)', borderRadius: 16, padding: '16px 18px', background: 'var(--glass-2)' }}>
          <div style={{ fontSize: 12.5, color: 'var(--mute)', lineHeight: 1.5 }}>
            Чему научилось сопоставление: как строку печатает поставщик — и какой это товар в iiko.
            Связка ставится один раз, когда оператор выбирает товар в накладной, и дальше работает сама.
          </div>

          {err && <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--red)' }}>не загрузилось: {err}</div>}
          {rows === null && !err && <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--mute)' }}>читаю справочник…</div>}

          {rows !== null && !rows.length && (
            <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--mute)' }}>
              Пока пусто. Разберите накладную и выберите товар хотя бы для одной строки — связка появится здесь.
            </div>
          )}

          {rows !== null && rows.length > 0 && (
            <>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти в справочнике"
                style={{ ...field, marginTop: 12 }} />
              <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
                <thead><tr>
                  <th style={th}>Строка поставщика</th>
                  <th style={th}>Товар в iiko</th>
                  <th style={{ ...th, textAlign: 'right', width: 70 }}>Срабо́тала</th>
                  <th style={{ ...th, width: 90 }} />
                </tr></thead>
                <tbody>
                  {shown.map((e) => (
                    <tr key={e.name_norm}>
                      <td style={td}>
                        {e.name_raw}
                        <div style={{ fontSize: 10.5, color: 'var(--mute)', marginTop: 2 }}>ключ: {e.name_norm}</div>
                        {edit === e.name_norm && <Picker org={org} onPick={(p) => rebind(e, p)} />}
                      </td>
                      <td style={td}>{e.product_name || <span style={{ color: 'var(--mute)' }}>—</span>}</td>
                      <td style={{ ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{e.hits}</td>
                      <td style={{ ...td, textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <button onClick={() => setEdit(edit === e.name_norm ? null : e.name_norm)}
                          style={{ background: 'none', border: 'none', color: 'var(--acc)', cursor: 'pointer', fontSize: 12, fontFamily: 'inherit' }}>
                          {edit === e.name_norm ? 'отмена' : 'заменить'}
                        </button>
                        <button onClick={() => forget(e)} title="забыть связку"
                          style={{ marginLeft: 10, background: 'none', border: 'none', color: 'var(--mute)', cursor: 'pointer', fontSize: 13 }}>×</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!shown.length && (
                <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--mute)' }}>Ничего не нашлось по «{q}».</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
