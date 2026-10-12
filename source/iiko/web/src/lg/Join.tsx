import { useEffect, useMemo, useState } from 'react'
import { Glass } from './ui'
import { saveStaffCode } from '../lib/api'

/* Подключение по QR филиала: человек сканирует код из подсобки и ОДИН РАЗ
   выбирает себя. Дальше он привязан к своему телеграму и ходит по личной
   ссылке — QR ему больше не нужен.

   Личные ссылки каждому оказались бы работой, про которую управляющий забудет
   на третьем новичке, и человек тихо перестал бы отмечаться. Подменить коллегу
   можно только в этот единственный момент, и это видно управляющему. */

type Emp = { id: string; name: string; shifts: number }
type Info = { branch: string; already: boolean; code?: string; employees: Emp[] }

export default function Join({ token, onDone }: { token: string; onDone: (code: string) => void }) {
  const [info, setInfo] = useState<Info | null>(null)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    fetch(`/api/join/${encodeURIComponent(token)}`, { headers: { Accept: 'application/json' } })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`)
        return d as Info
      })
      .then((d) => {
        // Уже подключался с этого телефона — второй раз выбирать себя незачем.
        if (d.already && d.code) { saveStaffCode(d.code); onDone(d.code); return }
        setInfo(d)
      })
      .catch((e) => setError(String((e as Error).message || e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const hits = useMemo(() => {
    const list = info?.employees ?? []
    const s = q.trim().toLowerCase()
    return (s ? list.filter((e) => e.name.toLowerCase().includes(s)) : list).slice(0, 40)
  }, [info, q])

  const claim = async (e: Emp) => {
    setBusy(e.id); setError('')
    try {
      const r = await fetch(`/api/join/${encodeURIComponent(token)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ employee_id: e.id, name: e.name }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`)
      // Не перезагружаемся: подпись Telegram лежит в хеше адреса и при смене
      // адреса пропадёт — человек тут же получит «откройте ссылку в Telegram».
      saveStaffCode(d.code)
      onDone(d.code)
    } catch (err) {
      setError(String((err as Error).message || err)); setBusy('')
    }
  }

  if (error && !info) {
    return (
      <div style={wrap}>
        <Glass span={12}><div style={{ padding: 16, fontSize: 14, color: 'var(--red)' }}>{error}</div></Glass>
      </div>
    )
  }
  if (!info) return <div style={{ padding: 20, color: 'var(--mute)', fontSize: 14 }}>Загружаю…</div>

  return (
    <div style={wrap}>
      <div style={{ padding: '0 4px' }}>
        <div style={{ fontSize: 22, fontWeight: 680, letterSpacing: '-.02em', color: 'var(--hi)' }}>Выберите себя</div>
        <div style={{ fontSize: 12.5, color: 'var(--mute)', marginTop: 2 }}>{info.branch}</div>
      </div>

      <Glass span={12}>
        <div style={{ padding: 14 }}>
          <input
            value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти свою фамилию"
            aria-label="Поиск по фамилии"
            style={{
              width: '100%', background: 'var(--glass-2)', border: '1px solid var(--hair)',
              borderRadius: 10, padding: '11px 12px', color: 'var(--hi)', fontSize: 15,
            }}
          />
          <div style={{ marginTop: 10, maxHeight: '58vh', overflowY: 'auto' }}>
            {hits.map((e) => (
              <button
                key={e.id} onClick={() => claim(e)} disabled={Boolean(busy)}
                style={{
                  display: 'block', width: '100%', textAlign: 'left', padding: '14px 12px',
                  marginTop: 6, borderRadius: 12, border: '1px solid var(--hair)',
                  background: busy === e.id ? 'var(--glass-2)' : 'transparent',
                  color: 'var(--hi)', fontSize: 15, cursor: busy ? 'default' : 'pointer',
                  opacity: busy && busy !== e.id ? 0.4 : 1,
                }}
              >
                {busy === e.id ? 'Подключаю…' : e.name}
              </button>
            ))}
            {!hits.length && (
              <div style={{ padding: '14px 2px', fontSize: 13.5, color: 'var(--mute)' }}>
                {q ? 'Никого не нашли — проверьте фамилию.' : 'Список пуст: все уже подключены или имена ещё не приехали из iiko.'}
              </div>
            )}
          </div>
          {error && <div style={{ marginTop: 10, fontSize: 13, color: 'var(--red)' }}>{error}</div>}
        </div>
      </Glass>

      <div style={{ padding: '0 6px', fontSize: 11.5, color: 'var(--mute)' }}>
        Выбирайте себя внимательно: это делается один раз. Ошиблись — позовите управляющего, он отвяжет.
      </div>
    </div>
  )
}

const wrap: React.CSSProperties = {
  padding: '18px 14px 40px', maxWidth: 560, margin: '0 auto', display: 'grid', gap: 14,
}
