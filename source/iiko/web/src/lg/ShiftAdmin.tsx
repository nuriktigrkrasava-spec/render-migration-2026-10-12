import { useCallback, useEffect, useMemo, useState } from 'react'
import { Glass } from './ui'
import { useOrg } from '../lib/useApi'
import qrcode from 'qrcode-generator'

/* Панель управляющего под расписанием: доступы сотрудников, точка филиала и
   сообщения смене. Живёт рядом с сеткой намеренно — ссылку выдают, глядя на
   того же человека, которому только что поставили смену. */

type Emp = { id: string; name: string }
type Access = { employee_id: string; name: string; bound: boolean; bound_at: string | null; url: string }
type AccessResp = { join_url: string; access: Access[] }
type Geo = { lat: number; lon: number; radius: number } | null
type Hit = { label: string; lat: number; lon: number }
type Sent = { id: number; text: string; created_at: string; to_employee_id: string | null; read_by: number }
type PayRow = { employee_id: string; name: string; earned: number; paid: number; balance: number; shifts: number }
type Payout = { id: number; employee_id: string; day: string; amount: number; comment: string }
type PayResp = { month: string; rows: PayRow[]; payouts: Payout[];
                 total_earned: number; total_paid: number; total_balance: number }

const btn: React.CSSProperties = {
  border: '1px solid var(--hair)', borderRadius: 9, padding: '7px 12px',
  fontSize: 12.5, cursor: 'pointer', background: 'transparent', color: 'var(--lo)',
}
const inp: React.CSSProperties = {
  background: 'var(--glass-2)', border: '1px solid var(--hair)', borderRadius: 10,
  padding: '9px 11px', color: 'var(--hi)', fontSize: 13, width: '100%',
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)
  return r.json() as Promise<T>
}

/* QR печатают и вешают в подсобке, поэтому рисуем крупно и чёрным по белому:
   светлая тема тут не вопрос вкуса, сканеру нужен контраст. */
function QR({ text, size = 168 }: { text: string; size?: number }) {
  const svg = useMemo(() => {
    if (!text) return ''
    const q = qrcode(0, 'M')
    q.addData(text)
    q.make()
    const n = q.getModuleCount()
    let path = ''
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) if (q.isDark(r, c)) path += `M${c} ${r}h1v1h-1z`
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 ${n + 2} ${n + 2}" `
      + `width="${size}" height="${size}" shape-rendering="crispEdges">`
      + `<rect x="-1" y="-1" width="${n + 2}" height="${n + 2}" fill="#fff"/>`
      + `<path d="${path}" fill="#000"/></svg>`
  }, [text, size])
  if (!svg) return null
  return <div style={{ lineHeight: 0, borderRadius: 10, overflow: 'hidden', background: '#fff', padding: 8, width: 'fit-content' }}
              dangerouslySetInnerHTML={{ __html: svg }} />
}

/* ── Точка филиала: сверять гео не с чем, пока её не задали ── */
function BranchPoint({ org }: { org: string }) {
  const [geo, setGeo] = useState<Geo>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [addr, setAddr] = useState('')
  const [hits, setHits] = useState<Hit[]>([])

  const [noHouse, setNoHouse] = useState(false)

  useEffect(() => {
    if (!org) return
    fetch(`/api/branch-geo/${org}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : { geo: null }))
      .then((d: { geo: Geo; name?: string; has_house_number?: boolean }) => {
        setGeo(d.geo)
        // Филиалы в iiko называются своим адресом — подставляем его, чтобы
        // управляющему осталось только нажать «Найти».
        if (d.name && !d.geo) setAddr(d.name.replace(/\s*\(.*?\)\s*/g, ' ').trim())
        setNoHouse(d.has_house_number === false)
      })
      .catch(() => { /* не критично: просто предложим задать */ })
  }, [org])

  const here = () => {
    setBusy(true); setMsg('')
    navigator.geolocation?.getCurrentPosition(
      async (p) => { await save(p.coords.latitude, p.coords.longitude); setBusy(false) },
      () => { setBusy(false); setMsg('Телефон не дал геопозицию') },
      { enableHighAccuracy: true, timeout: 10000 },
    )
  }

  const save = async (lat: number, lon: number, label?: string) => {
    try {
      const d = await post<{ geo: Geo }>(`/api/branch-geo/${org}`, {
        lat, lon, radius: geo?.radius ?? 250,
      })
      setGeo(d.geo); setHits([]); setAddr('')
      setMsg(label ? `Точка: ${label.split(',').slice(0, 3).join(',')}` : 'Точка сохранена')
    } catch (e) { setMsg(String((e as Error).message)) }
  }

  const find = async () => {
    const q = addr.trim()
    if (q.length < 3) return
    setBusy(true); setMsg(''); setHits([])
    try {
      const r = await fetch(`/api/geocode/${org}?q=${encodeURIComponent(q)}`,
                            { headers: { Accept: 'application/json' } })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`)
      setHits(d.results || [])
      if (!(d.results || []).length) setMsg('Ничего не нашли — добавьте город и номер дома')
    } catch (e) { setMsg(String((e as Error).message)) } finally { setBusy(false) }
  }

  return (
    <Glass span={6}>
      <div className="eyebrow"><span>Точка филиала</span></div>
      <div style={{ marginTop: 10, fontSize: 13, color: geo ? 'var(--hi)' : 'var(--mute)' }}>
        {geo
          ? `Задана · радиус ${geo.radius} м`
          : 'Не задана — гео у сотрудников не проверяется, смены открываются без отметки о месте.'}
      </div>
      {/* Адресом — из кабинета; кнопкой — стоя на месте. Второе точнее, но
          ради него не поедешь через город. */}
      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <input
          value={addr} onChange={(e) => setAddr(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') find() }}
          placeholder="Город, улица, дом" aria-label="Адрес филиала"
          style={{ ...inp, flex: 1 }}
        />
        <button onClick={find} disabled={busy} style={{ ...btn, whiteSpace: 'nowrap' }}>
          {busy ? '…' : 'Найти'}
        </button>
      </div>

      {hits.length > 0 && (
        <div style={{ marginTop: 8, borderRadius: 11, border: '1px solid var(--hair)', overflow: 'hidden' }}>
          {hits.map((h, i) => (
            <button key={i} onClick={() => save(h.lat, h.lon, h.label)}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 11px',
                       fontSize: 12.5, background: 'transparent', border: 'none',
                       borderBottom: '1px solid var(--hair-2)', color: 'var(--hi)', cursor: 'pointer' }}>
              {h.label}
            </button>
          ))}
        </div>
      )}

      <button onClick={here} disabled={busy} style={{ ...btn, marginTop: 10 }}>
        {busy ? 'Определяю…' : 'Или взять моё место сейчас'}
      </button>
      {noHouse && !geo && (
        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--amb)' }}>
          В названии филиала нет номера дома — допишите его в поле, иначе точка
          встанет в центр улицы и радиус промахнётся.
        </div>
      )}
      <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--mute)' }}>{msg}</div>
    </Glass>
  )
}

/* ── Личные ссылки сотрудников ── */
export function StaffLinks({ org, employees, onWrite }: { org: string; employees: Emp[]; onWrite: (e: Emp) => void }) {
  const [access, setAccess] = useState<Access[]>([])
  const [joinUrl, setJoinUrl] = useState('')
  const [copied, setCopied] = useState('')
  const [ask, setAsk] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    if (!org) return
    fetch(`/api/staff-access/${org}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : { join_url: '', access: [] }))
      .then((d: AccessResp) => { setAccess(d.access || []); setJoinUrl(d.join_url || '') })
      .catch(() => { /* покажем пустым */ })
  }, [org])
  useEffect(load, [load])

  const byId = useMemo(() => new Map(access.map((a) => [a.employee_id, a])), [access])

  const issue = async (e: Emp) => {
    const d = await post<{ url: string }>(`/api/staff-access/${org}`, { employee_id: e.id, name: e.name })
    load()
    copy(d.url, e.id)
  }

  /* Перевыпуск и отвязка тихо ломают человеку вход, а кнопки стоят вплотную к
     безобидным. Поэтому подтверждаем вторым кликом — не модалкой: диалог тут
     прожимают не глядя, а «Точно?» на самой кнопке заметно. */
  const confirmed = (id: string) => {
    if (ask !== id) {
      setAsk(id)
      setTimeout(() => setAsk((a) => (a === id ? '' : a)), 4000)
      return false
    }
    setAsk('')
    return true
  }

  const rotate = async () => {
    if (!confirmed('__qr')) return
    setErr('')
    try {
      const d = await post<{ join_url: string }>(`/api/staff-access/${org}/rotate`, {})
      setJoinUrl(d.join_url)
    } catch (e) { setErr(String((e as Error).message)) }
  }

  const unbind = async (e: Emp) => {
    if (!confirmed(e.id)) return
    setErr('')
    try {
      await post(`/api/staff-access/${org}/revoke`, { employee_id: e.id })
      load()
    } catch (x) { setErr(String((x as Error).message)) }
  }
  const copy = (url: string, id: string) => {
    navigator.clipboard?.writeText(url).then(() => { setCopied(id); setTimeout(() => setCopied(''), 2000) })
      .catch(() => { /* буфер недоступен — ссылка видна в поле ниже */ })
  }

  return (
    <Glass span={6}>
      <div className="eyebrow"><span>Доступ в мини-апп</span><span className="hint">QR филиала</span></div>

      {/* Основной путь: повесить в подсобке. Личные ссылки ниже — запасной,
          для тех, кто сменил телефон или чья ссылка утекла. */}
      <div style={{ marginTop: 12, display: 'flex', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <QR text={joinUrl} />
        <div style={{ flex: 1, minWidth: 190 }}>
          <div style={{ fontSize: 13, color: 'var(--hi)' }}>Распечатайте и повесьте в подсобке.</div>
          <div style={{ fontSize: 12, color: 'var(--mute)', marginTop: 6 }}>
            Сотрудник сканирует, один раз выбирает себя — и дальше заходит по своей ссылке.
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <button onClick={() => copy(joinUrl, '__join')} style={btn}>
              {copied === '__join' ? 'Скопировано' : 'Скопировать ссылку'}
            </button>
            {/* QR сфотографировали или он уехал с уволенным управляющим —
                печатают новый, прежний умирает сразу. Привязанных не трогает. */}
            <button onClick={rotate} disabled={!joinUrl}
                    style={{ ...btn, ...(ask === '__qr' ? { borderColor: 'var(--red)', color: 'var(--red)' } : {}) }}>
              {ask === '__qr' ? 'Точно? Старый умрёт' : 'Новый QR'}
            </button>
          </div>
          {err && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--red)' }}>{err}</div>}
        </div>
      </div>

      <div style={{ marginTop: 16, fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>
        Кто подключился
      </div>
      <div style={{ marginTop: 4, maxHeight: 220, overflowY: 'auto' }}>
        {employees.map((e) => {
          const a = byId.get(e.id)
          return (
            <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 0', borderTop: '1px solid var(--hair-2)' }}>
              <span style={{ flex: 1, fontSize: 13, color: 'var(--hi)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {e.name}
                {a && <span style={{ marginLeft: 8, fontSize: 11, color: a.bound ? 'var(--grn)' : 'var(--mute)' }}>
                  {a.bound ? 'привязан' : 'ссылка выдана'}
                </span>}
              </span>
              <button onClick={() => onWrite(e)} style={{ ...btn, padding: '5px 9px' }}>Написать</button>
              {a
                ? <>
                    <button onClick={() => copy(a.url, e.id)} style={{ ...btn, padding: '5px 9px' }}>
                      {copied === e.id ? 'Скопировано' : 'Ссылка'}
                    </button>
                    {/* Выбрал не себя или уволился — отвязываем: ссылка умирает,
                        а место снова свободно для скана по QR. */}
                    <button onClick={() => unbind(e)}
                            style={{ ...btn, padding: '5px 9px', ...(ask === e.id ? { borderColor: 'var(--red)', color: 'var(--red)' } : {}) }}>
                      {ask === e.id ? 'Точно?' : 'Отвязать'}
                    </button>
                  </>
                : <button onClick={() => issue(e)} style={{ ...btn, padding: '5px 9px', borderColor: 'var(--acc)', color: 'var(--acc)' }}>Выдать</button>}
            </div>
          )
        })}
      </div>
      <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--mute)' }}>
        Привязка к телеграму происходит один раз. Выбрал не себя, сменил телефон или уволился — «Отвязать»: прежняя ссылка умрёт, а место снова свободно для скана по QR.
      </div>
    </Glass>
  )
}

/* ── Сообщения смене ── */
function ShiftMessage({ org, to, onDone }: { org: string; to: Emp | null; onDone: () => void }) {
  const [text, setText] = useState('')
  const [sent, setSent] = useState<Sent[]>([])
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    if (!org) return
    fetch(`/api/staff-notice/${org}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : { sent: [] }))
      .then((d) => setSent(d.sent || []))
      .catch(() => { /* пусто */ })
  }, [org])
  useEffect(load, [load])

  const send = async () => {
    const t = text.trim()
    if (!t) return
    try {
      await post(`/api/staff-notice/${org}`, { text: t, employee_id: to?.id ?? null })
      setText(''); setMsg(to ? `Отправлено: ${to.name}` : 'Отправлено всей смене'); onDone(); load()
    } catch (e) { setMsg(String((e as Error).message)) }
  }

  return (
    <Glass span={12}>
      <div className="eyebrow">
        <span>Сообщение смене</span>
        <span className="hint">{to ? `лично · ${to.name}` : 'всем сотрудникам филиала'}</span>
      </div>
      <textarea
        value={text} onChange={(e) => setText(e.target.value)} rows={3}
        placeholder={to ? `Что передать: ${to.name}` : 'Что передать всей смене'}
        style={{ ...inp, marginTop: 10, resize: 'vertical', fontFamily: 'inherit' }}
      />
      <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button onClick={send} style={{ ...btn, borderColor: 'var(--grn)', color: 'var(--grn)' }}>Отправить</button>
        {to && <button onClick={onDone} style={btn}>Всей смене</button>}
        <span style={{ fontSize: 12, color: 'var(--mute)' }}>{msg}</span>
      </div>
      {sent.length > 0 && (
        <div style={{ marginTop: 14 }}>
          {sent.slice(0, 5).map((s) => (
            <div key={s.id} style={{ padding: '9px 0', borderTop: '1px solid var(--hair-2)', display: 'flex', gap: 10, alignItems: 'baseline' }}>
              <span style={{ flex: 1, fontSize: 12.5, color: 'var(--lo)' }}>{s.text}</span>
              <span style={{ fontSize: 11, color: 'var(--mute)', whiteSpace: 'nowrap' }}>
                {s.to_employee_id ? 'лично' : 'всем'} · прочитали {s.read_by}
              </span>
            </div>
          ))}
        </div>
      )}
    </Glass>
  )
}

/* ── Выплаты: начислено сменами, выдано на руки, остаток ──
   Начисление и выдача хранятся раздельно, поэтому здесь их видно рядом. Ошибку
   исправляют удалением выплаты, а не вводом минуса: минус в истории выглядит
   как штраф сотруднику, и через месяц никто не вспомнит, что это была опечатка. */
function Payouts({ org, employees }: { org: string; employees: Emp[] }) {
  const [data, setData] = useState<PayResp | null>(null)
  const [to, setTo] = useState('')
  const [sum, setSum] = useState('')
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    if (!org) return
    fetch(`/api/payouts/${org}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setData(d))
      .catch(() => { /* покажем пустым */ })
  }, [org])
  useEffect(load, [load])

  const names = useMemo(() => {
    const m = new Map(employees.map((e) => [e.id, e.name]))
    for (const r of data?.rows ?? []) if (!m.has(r.employee_id)) m.set(r.employee_id, r.name)
    return m
  }, [employees, data])

  const give = async () => {
    const amount = Number(sum)
    if (!to || !amount || amount <= 0) { setMsg('Выберите сотрудника и сумму'); return }
    try {
      await post(`/api/payouts/${org}`, { employee_id: to, amount, comment: note.trim() })
      setSum(''); setNote(''); setMsg(`Записано: ${names.get(to) || ''}`); load()
    } catch (e) { setMsg(String((e as Error).message)) }
  }

  const drop = async (id: number) => {
    try { await post(`/api/payouts/${org}/delete`, { id }); load() }
    catch (e) { setMsg(String((e as Error).message)) }
  }

  const rows = data?.rows ?? []
  return (
    <Glass span={12}>
      <div className="eyebrow">
        <span>Выплаты</span>
        <span className="hint">{data?.month || ''} · осталось выдать {money(data?.total_balance)}</span>
      </div>

      {rows.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 460 }}>
            <thead><tr>
              {['Сотрудник', 'Смен', 'Начислено', 'Выдано', 'Осталось'].map((h, i) => (
                <th key={h} style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase',
                                     color: 'var(--mute)', fontWeight: 600, padding: '0 10px 9px 0',
                                     textAlign: i ? 'right' : 'left', borderBottom: '1px solid var(--hair)' }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.employee_id}>
                  <td style={td}>{r.name}</td>
                  <td style={tdR}>{r.shifts}</td>
                  <td style={tdR}>{money(r.earned)}</td>
                  <td style={{ ...tdR, color: 'var(--grn)' }}>{money(r.paid)}</td>
                  <td style={{ ...tdR, fontWeight: 700,
                               color: r.balance > 0 ? 'var(--hi)' : 'var(--mute)' }}>{money(r.balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Кому выдать"
                style={{ ...inp, width: 'auto', minWidth: 160 }}>
          <option value="">Кому выдать…</option>
          {[...names].map(([id, n]) => <option key={id} value={id}>{n}</option>)}
        </select>
        <input value={sum} onChange={(e) => setSum(e.target.value)} type="number" min={0}
               placeholder="Сумма, ₸" aria-label="Сумма выплаты" style={{ ...inp, width: 130 }} />
        <input value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="Комментарий (аванс, за неделю…)" aria-label="Комментарий"
               style={{ ...inp, flex: 1, minWidth: 180 }} />
        <button onClick={give} style={{ ...btn, borderColor: 'var(--grn)', color: 'var(--grn)' }}>Выдал</button>
      </div>
      <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--mute)' }}>
        Выплата сразу видна сотруднику в его мини-аппе. {msg}
      </div>

      {(data?.payouts.length ?? 0) > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>
            История выплат
          </div>
          {data!.payouts.map((p) => (
            <div key={p.id} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '9px 0', borderTop: '1px solid var(--hair-2)' }}>
              <span style={{ width: 46, fontSize: 12, color: 'var(--mute)' }}>{p.day.slice(8)}.{p.day.slice(5, 7)}</span>
              <span style={{ flex: 1, fontSize: 12.5, color: 'var(--hi)' }}>
                {names.get(p.employee_id) || p.employee_id.slice(0, 8)}
                {p.comment && <span style={{ color: 'var(--mute)' }}> · {p.comment}</span>}
              </span>
              <span className="tnum" style={{ fontSize: 13, fontWeight: 620, color: 'var(--grn)' }}>{money(p.amount)}</span>
              <button onClick={() => drop(p.id)} aria-label="Удалить выплату"
                      style={{ ...btn, padding: '3px 8px', fontSize: 11 }}>×</button>
            </div>
          ))}
        </div>
      )}
    </Glass>
  )
}

const td: React.CSSProperties = { padding: '10px 10px 10px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 12.5, whiteSpace: 'nowrap' }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const money = (n?: number) => (n == null ? '—' : new Intl.NumberFormat('ru-RU').format(Math.round(n)) + ' ₸')

export default function ShiftAdmin({ employees }: { employees: Emp[] }) {
  const org = useOrg()
  const [to, setTo] = useState<Emp | null>(null)
  if (!org) return null
  return (
    <>
      <BranchPoint org={org} />
      <StaffLinks org={org} employees={employees} onWrite={setTo} />
      <Payouts org={org} employees={employees} />
      <ShiftMessage org={org} to={to} onDone={() => setTo(null)} />
    </>
  )
}
