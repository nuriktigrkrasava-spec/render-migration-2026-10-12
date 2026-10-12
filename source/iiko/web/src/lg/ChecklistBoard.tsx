import { useEffect, useState } from 'react'
import { Glass } from './ui'
import { SLOTS as DEFAULT_SLOTS, ZONES, ZONE_LABEL, type Done, type Review,
         issueText, slotPassed, zoneVerdict } from './checklist'

/* Чек-листы филиалов глазами владельца: кто сдал, кто нет.

   Управляющий чек-лист СДАЁТ (ChecklistUpload), владелец его СПРАШИВАЕТ — это
   разные экраны, и второго на сайте не было вовсе: в «Горе» от всей сдачи
   оставался чип «чек-листы 3» внутри прогресса, по которому нельзя понять, кто
   именно не снял санузел в 20:45. Здесь — сетка филиалы × слоты за сегодня.

   В клетке — не «2/3», а сами зоны: З · К · С. Счётчик отвечал «сколько», но
   не «какую», а владельцу нужно именно это: одно дело не снят зал, другое —
   санузел. Зелёная — сдана, красная — слот прошёл и её нет, серая — слот ещё
   не наступил, спрашивать не за что.

   Ходим в тот же /api/mountain/checklist/{token}: владельцу он и так отдаёт
   сдачи по всем его филиалам. */

// Одна буква на зону: полные подписи в девять строк × четыре слота не влезают
// ни на телефон, ни на глаз. Наведение и aria-label дают полное название.
const ZONE_SHORT: Record<string, string> = { hall: 'З', kitchen: 'К', wc: 'С' }

const shortName = (name: string) => name.replace(/^Астана,?\s*(ул\.?)?\s*/i, '').trim() || name

export default function ChecklistBoard({ token, branches }: { token: string; branches: string[] }) {
  const [done, setDone] = useState<Done[]>([])
  const [reviews, setReviews] = useState<Review[]>([])
  const [slots, setSlots] = useState<string[]>(DEFAULT_SLOTS)
  const [zoneCount, setZoneCount] = useState(3)

  useEffect(() => {
    fetch(`/api/mountain/checklist/${token}`).then((r) => r.json())
      .then((d: { done?: Done[]; reviews?: Review[]; slots?: string[]; zones?: Record<string, string> }) => {
        setDone(d.done || [])
        setReviews(d.reviews || [])
        if (d.slots?.length) setSlots(d.slots)
        if (d.zones) setZoneCount(Object.keys(d.zones).length)
      }).catch(() => {})
  }, [token])

  const cell = (branch: string, slot: string) =>
    done.filter((d) => d.branch === branch && d.slot === slot)
  // Сдана ли КОНКРЕТНАЯ зона: сверяем по ключу, а подпись дочитываем у старых
  // ответов без zone_key — регистр у сервера и экрана разный.
  const zoneDone = (branch: string, slot: string, key: string) =>
    cell(branch, slot).find((d) => (d.zone_key || d.zone?.toLowerCase())
                                   === (d.zone_key ? key : ZONE_LABEL[key]))
  const problems = reviews.filter((r) => !r.ok && (r.issues || []).length)

  return (
    <Glass span={12}>
      <div className="eyebrow"><span>Чек-листы филиалов</span>
        <span className="hint">сегодня · зон в слоте {zoneCount}</span></div>

      <div style={{ marginTop: 12, overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 380, borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead><tr>
            <th style={{ textAlign: 'left', padding: '0 0 8px', fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>Филиал</th>
            {slots.map((s) => (
              <th key={s} className="tnum" style={{ textAlign: 'center', padding: '0 0 8px', fontSize: 10.5, color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>{s}</th>
            ))}
          </tr></thead>
          <tbody>
            {branches.map((b) => (
              <tr key={b}>
                <td style={{ padding: '9px 8px 9px 0', borderBottom: '1px solid var(--hair-2)', color: 'var(--hi)' }}>{shortName(b)}</td>
                {slots.map((s) => {
                  const passed = slotPassed(s)
                  return (
                    <td key={s} style={{ padding: '7px 4px', borderBottom: '1px solid var(--hair-2)', textAlign: 'center' }}>
                      <span style={{ display: 'inline-flex', gap: 3 }}>
                        {ZONES.map(([k, label]) => {
                          const d = zoneDone(b, s, k)
                          // Сдано — ещё не чисто. Зона со сдачей, в которой разбор
                          // нашёл грязь, красится КРАСНЫМ, а не зелёным: иначе
                          // загаженный санузел выглядел как вымытый.
                          const dirty = d && zoneVerdict(reviews, s, k, b) === 'dirty'
                          const good = d && !dirty
                          const col = good ? 'var(--grn)' : dirty || passed ? 'var(--red)' : 'var(--mute)'
                          const state = dirty ? `сдано в ${d?.at}, но есть замечания`
                            : d ? `сдано в ${d.at}`
                            : passed ? 'не сдано' : 'слот ещё не наступил'
                          return (
                            <span key={k} title={`${label}: ${state}`}
                              aria-label={`${label} ${state}`}
                              style={{
                                width: 18, height: 18, borderRadius: 6, display: 'inline-grid', placeItems: 'center',
                                fontSize: 9.5, fontWeight: 800,
                                color: good || dirty ? 'var(--on-acc)' : col,
                                background: good ? 'var(--grn)' : dirty ? 'var(--red)' : 'transparent',
                                border: `1px solid ${col}`,
                                opacity: d || passed ? 1 : .45,
                              }}>{ZONE_SHORT[k] || label[0]}</span>
                          )
                        })}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 9, display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 11, color: 'var(--mute)' }}>
        <span>{ZONES.map(([k, label]) => `${ZONE_SHORT[k]} — ${label.toLowerCase()}`).join(' · ')}</span>
        <span style={{ marginLeft: 'auto' }}>
          <span style={{ color: 'var(--grn)', fontWeight: 700 }}>зелёная</span> сдано и чисто ·{' '}
          <span style={{ color: 'var(--red)', fontWeight: 700 }}>красная</span> не сдано либо есть замечания ·{' '}
          серая — слот ещё не наступил
        </span>
      </div>

      {/* Замечания разбора: ради них чек-лист и снимают — иначе это просто галочки */}
      {problems.length > 0 && (
        <div style={{ marginTop: 12, display: 'grid', gap: 6 }}>
          {problems.slice(0, 6).map((r, i) => (
            <div key={`${r.branch}${r.slot}${i}`} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 12.5 }}>
              <span className="tnum" style={{ color: 'var(--mute)', flex: 'none' }}>{r.slot}</span>
              <span style={{ color: 'var(--hi)', flex: 'none', fontWeight: 650 }}>{shortName(r.branch || '')}</span>
              <span style={{ color: 'var(--red)' }}>{(r.issues || []).map(issueText).join(' · ')}</span>
            </div>
          ))}
        </div>
      )}
    </Glass>
  )
}
