import { useEffect, useMemo, useRef, useState } from 'react'

/* Выбор поставщика поиском, а не прокруткой.

   В справочнике iiko их сотни, и раньше здесь стоял обычный <select>: чтобы
   найти нужного, оператор листал весь список — и часто брал не того. Здесь
   строка поиска, недавние сверху и совпадение по любому куску названия, включая
   середину слова: «кола» должна находить «ТОО Кока-Кола Алматы».

   Клавиатура работает как в родном списке: ↑/↓ ведут по строкам, Enter
   выбирает, Esc закрывает. */

export type Supplier = { id: string; name: string }

const fold = (s: string) => (s || '').toLowerCase().replace('ё', 'е').trim()

/** Совпадения по подстроке; те, где с запроса начинается СЛОВО, — выше.

   Ранг по всей строке тут не работает: почти все названия начинаются с «ТОО» и
   «ИП», и на запрос «ко» «Молоко» вставало вровень с «Кока-Кола». */
export function filterSuppliers(list: Supplier[], q: string, recent: string[] = []): Supplier[] {
  const f = fold(q)
  if (!f) {
    const by = new Map(list.map((s) => [s.id, s]))
    const top = recent.map((id) => by.get(id)).filter(Boolean) as Supplier[]
    const rest = list.filter((s) => !recent.includes(s.id))
    return [...top, ...rest]
  }
  const wordStart = (name: string) =>
    fold(name).split(/[\s"«»().,-]+/).some((w) => w.startsWith(f)) ? 0 : 1
  const hit = list.filter((s) => fold(s.name).includes(f))
  return hit.sort((a, b) =>
    wordStart(a.name) - wordStart(b.name) || a.name.localeCompare(b.name, 'ru'))
}

export default function PickSupplier({
  suppliers, value, onChange, recent = [], field,
}: {
  suppliers: Supplier[]; value: string; onChange: (id: string) => void
  recent?: string[]; field: React.CSSProperties
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)
  const box = useRef<HTMLDivElement>(null)
  const chosen = suppliers.find((s) => s.id === value)
  const found = useMemo(() => filterSuppliers(suppliers, q, recent).slice(0, 40),
                        [suppliers, q, recent])

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  function pick(s: Supplier) {
    onChange(s.id)
    setQ('')
    setOpen(false)
  }

  return (
    <div ref={box} style={{ position: 'relative' }}>
      <input
        value={open ? q : (chosen?.name || '')}
        placeholder={chosen ? chosen.name : 'начни печатать название'}
        onFocus={() => { setOpen(true); setQ(''); setCursor(0) }}
        onChange={(e) => { setQ(e.target.value); setCursor(0); setOpen(true) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, found.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
          else if (e.key === 'Enter' && found[cursor]) { e.preventDefault(); pick(found[cursor]) }
          else if (e.key === 'Escape') setOpen(false)
        }}
        style={{ ...field, marginTop: 5 }} />

      {open && (
        <div style={{
          position: 'absolute', zIndex: 30, left: 0, right: 0, top: '100%', marginTop: 4,
          maxHeight: 260, overflowY: 'auto', borderRadius: 12,
          border: '1px solid var(--hair)', background: 'var(--card)',
          boxShadow: 'var(--shadow)',
        }}>
          {found.length === 0 && (
            <div style={{ padding: '10px 12px', fontSize: 12.5, color: 'var(--mute)' }}>
              Никого не нашли. Поставщика заводят в iikoOffice.
            </div>
          )}
          {found.map((s, i) => {
            const isRecent = !q && recent.includes(s.id)
            return (
              <div key={s.id} onMouseDown={() => pick(s)} onMouseEnter={() => setCursor(i)}
                style={{
                  padding: '9px 12px', fontSize: 13, cursor: 'pointer',
                  display: 'flex', gap: 8, alignItems: 'baseline',
                  background: i === cursor ? 'var(--glass-2)' : 'transparent',
                  color: s.id === value ? 'var(--acc)' : 'var(--hi)',
                  fontWeight: s.id === value ? 700 : 400,
                }}>
                <span style={{ flex: 1 }}>{s.name}</span>
                {isRecent && <span style={{ fontSize: 10.5, color: 'var(--mute)' }}>недавно</span>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
