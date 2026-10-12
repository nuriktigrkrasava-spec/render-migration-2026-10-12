import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { runAction, type Advice, type AssistantResp } from './assistant'

/* Проактивный ИИ-ассистент «Директор»: сам анализирует все филиалы и выдаёт
   советы «проблема → деньги → что сделать» + действия. Не чат.
   Советы сгруппированы ПО ФИЛИАЛУ — одна карточка на филиал со списком его
   проблем внутри (меньше прокрутки, видны все типы проблем сразу). */

type Sev = Advice['severity']
const SEV_COLOR: Record<Sev, string> = { good: 'var(--grn)', high: 'var(--red)', med: 'var(--amb)', low: 'var(--grn)' }
// 'good' — похвала, а не проблема: она не должна перекрашивать филиал в «всё зелено»,
// поэтому в ранге стоит ниже всех и в счётчик проблем не идёт
const SEV_RANK: Record<Sev, number> = { good: 0, high: 3, med: 2, low: 1 }
const isWin = (a: Advice) => a.severity === 'good'
// полными числами, как и на остальных экранах: «846 тыс» владелец просил не писать
const money = (n: number | null) =>
  n == null ? '' : `${new Intl.NumberFormat('ru-RU').format(Math.round(n))} ₸`
const plural = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? 'проблема' : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? 'проблемы' : 'проблем')

type Group = { branch: string; items: Advice[]; total: number; sev: Sev; wins: number; probs: number }

export default function Agent() {
  const [open, setOpen] = useState(false)
  const [advice, setAdvice] = useState<Advice[]>([])
  const [notif, setNotif] = useState<Group | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/assistant', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: AssistantResp) => { if (alive) setAdvice(d.advice || []) })
      .catch((e) => console.warn('assistant', e))
    return () => { alive = false }
  }, [])

  // группировка по филиалу: одна карточка = филиал + его проблемы
  const groups = useMemo<Group[]>(() => {
    const m = new Map<string, Advice[]>()
    for (const a of advice) {
      const k = a.branch || '—'
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(a)
    }
    const gs = [...m.entries()].map(([branch, items]) => {
      // похвала — первой: хороший разговор начинается с того, что получилось
      items.sort((a, b) => Number(isWin(b)) - Number(isWin(a))
        || (b.money || 0) - (a.money || 0) || SEV_RANK[b.severity] - SEV_RANK[a.severity])
      const total = items.reduce((s, x) => s + (x.money || 0), 0)
      const probList = items.filter((x) => !isWin(x))
      const sev = probList.reduce<Sev>((w, x) => (SEV_RANK[x.severity] > SEV_RANK[w] ? x.severity : w), 'low')
      return { branch, items, total, sev, wins: items.length - probList.length, probs: probList.length }
    })
    gs.sort((a, b) => b.total - a.total || SEV_RANK[b.sev] - SEV_RANK[a.sev])
    return gs
  }, [advice])

  // проактивно всплываем топ-филиалом, когда панель закрыта
  useEffect(() => {
    if (open || groups.length === 0) { setNotif(null); return }
    const t = setTimeout(() => setNotif(groups[0]), 3500)
    return () => clearTimeout(t)
  }, [open, groups])

  const act = (a: Advice) => { if (a.action) runAction(a.action); setOpen(false) }

  return (
    <>
      {!open && (
        <button className="lg-agent-fab" onClick={() => setOpen(true)} aria-label="ИИ-ассистент">
          <img src="/agent2.png" alt="" />
          <span className="lg-agent-dot" />
        </button>
      )}

      <AnimatePresence>
        {notif && !open && (
          <motion.button className="lg-agent-toast" onClick={() => setOpen(true)}
            initial={{ opacity: 0, y: 10, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10, scale: 0.96 }}>
            <img src="/agent2.png" alt="" />
            <span><b style={{ color: SEV_COLOR[notif.sev] }}>{notif.branch}</b>
              {notif.wins > 0 && <> · <span style={{ color: 'var(--grn)' }}>✓ {notif.wins}</span></>}
              {notif.probs > 0 && <> · {notif.probs} {plural(notif.probs)}</>}
              {notif.total > 0 && <> · <span className="tnum">≈ {money(notif.total)}/мес</span></>}</span>
          </motion.button>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {open && (
          <motion.div className="lg-chat glass" initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 16, scale: 0.96 }} transition={{ type: 'spring', stiffness: 320, damping: 30 }}>
            <div className="lg-chat-head">
              <img src="/agent2.png" alt="" />
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--hi)' }}>ИИ-директор</div>
                <div style={{ fontSize: 11, color: 'var(--grn)' }}>● смотрит все филиалы</div>
              </div>
              <button onClick={() => setOpen(false)} className="lg-chat-x" aria-label="Закрыть">×</button>
            </div>
            <div className="lg-chat-body">
              {groups.length === 0 && <div style={{ padding: '14px 2px', fontSize: 13, color: 'var(--mute)' }}>Собираю сигналы по филиалам…</div>}
              {groups.map((g) => (
                <div key={g.branch} style={{ padding: '13px 0', borderTop: '1px solid var(--hair-2)' }}>
                  {/* шапка филиала */}
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: SEV_COLOR[g.sev], flex: 'none' }} />
                    <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--hi)' }}>{g.branch}</span>
                    {g.wins > 0 && <span style={{ fontSize: 11, fontWeight: 650, color: 'var(--grn)' }}>✓ {g.wins}</span>}
                    <span style={{ fontSize: 11, color: 'var(--mute)' }}>{g.probs} {plural(g.probs)}</span>
                    {g.total > 0 && <span className="tnum" style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700, color: SEV_COLOR[g.sev] }}>≈ {money(g.total)}/мес</span>}
                  </div>
                  {/* проблемы филиала */}
                  {g.items.map((a) => (
                    <div key={a.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: isWin(a) ? '9px 10px' : '9px 0 0', marginLeft: 16,
                      ...(isWin(a) ? { marginTop: 9, borderRadius: 12, background: 'color-mix(in srgb,var(--grn) 10%,transparent)', border: '1px solid color-mix(in srgb,var(--grn) 22%,transparent)' } : {}) }}>
                      {isWin(a)
                        ? <span aria-hidden style={{ fontSize: 12, lineHeight: '18px', color: 'var(--grn)', flex: 'none' }}>✓</span>
                        : <span style={{ width: 6, height: 6, borderRadius: '50%', background: SEV_COLOR[a.severity], flex: 'none', marginTop: 5 }} />}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                          <span style={{ fontSize: 12.5, fontWeight: 600, color: isWin(a) ? 'var(--grn)' : 'var(--hi)' }}>{a.title}</span>
                          {a.money != null && <span className="tnum" style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 650, color: SEV_COLOR[a.severity] }}>≈ {money(a.money)}</span>}
                        </div>
                        <p style={{ margin: '3px 0 0', fontSize: 12, lineHeight: 1.45, color: 'var(--lo)' }}>{a.detail}</p>
                      </div>
                      {a.action && (
                        <button onClick={() => act(a)} aria-label="Открыть" style={{ flex: 'none', marginTop: 1, fontSize: 12, fontWeight: 700, color: 'var(--acc)', background: 'color-mix(in srgb,var(--acc) 12%,transparent)', border: '1px solid color-mix(in srgb,var(--acc) 26%,transparent)', borderRadius: 9, padding: '4px 9px', cursor: 'pointer' }}>→</button>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
