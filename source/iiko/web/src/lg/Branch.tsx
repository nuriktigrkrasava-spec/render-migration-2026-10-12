import { useEffect, useRef, useState } from 'react'
import { getBranches, getOrg, setOrg, type Branch } from '../lib/api'

/* Переключатель филиалов. Список — из /api/restaurants (реальный бэк) или демо
   (baked). Выбор меняет ORG → все useApi перезапрашиваются по выбранному филиалу.
   «Изоляция»: каждый филиал видит только свои данные. */

const label = (b?: Branch) => b?.organization_name || b?.name || 'Филиал'

export default function BranchSwitcher() {
  const [branches, setBranches] = useState<Branch[]>([])
  const [org, setOrgLocal] = useState(getOrg())
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getBranches().then((b) => {
      setBranches(b)
      // Филиал из прошлой сессии переживает F5 — но только если он всё ещё наш.
      // Если его нет в /api/restaurants (сменился аккаунт, филиал удалён) — падаем
      // на первый, иначе экран молча запросил бы чужой org и получил пустоту.
      if (b.length && !b.some((x) => x.organization_id === getOrg())) {
        setOrg(b[0].organization_id); setOrgLocal(b[0].organization_id)
      } else {
        setOrgLocal(getOrg())
      }
    })
  }, [])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const cur = branches.find((b) => b.organization_id === org)
  function pick(id: string) { setOrg(id); setOrgLocal(id); setOpen(false) }

  if (branches.length <= 1 && !cur) return null

  return (
    <div className="lg-branch" ref={ref}>
      <button className="lg-branch-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open}>
        <span className="lg-branch-ic" aria-hidden>◈</span>
        <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>{label(cur)}</span>
        <span className="lg-branch-caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="lg-branch-menu" role="listbox">
          <div className="lg-branch-head">Филиал</div>
          {branches.map((b) => (
            <button key={b.organization_id} role="option" aria-selected={b.organization_id === org}
              className={`lg-branch-item ${b.organization_id === org ? 'on' : ''}`} onClick={() => pick(b.organization_id)}>
              <span className="lg-branch-dot" style={{ opacity: b.organization_id === org ? 1 : 0 }} />
              {label(b)}
            </button>
          ))}
          <button className="lg-branch-item lg-branch-add" onClick={() => { setOpen(false); window.dispatchEvent(new CustomEvent('lg:go', { detail: 'connect' })) }}>
            <span aria-hidden style={{ color: 'var(--acc)', fontWeight: 700, width: 7, textAlign: 'center' }}>＋</span>
            Подключить ресторан
          </button>
        </div>
      )}
    </div>
  )
}
