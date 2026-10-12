import { motion, useReducedMotion } from 'framer-motion'
import type { ReactNode } from 'react'
import steakPhoto from '../assets/salmon/steak.webp'
import slicePhoto from '../assets/salmon/slice.webp'
import trimPhoto from '../assets/salmon/trim.webp'
import teriyakiPhoto from '../assets/salmon/teriyaki.webp'
import saucePhoto from '../assets/salmon/sauce.webp'
import wastePhoto from '../assets/salmon/waste.webp'
import eelRawPhoto from '../assets/eel/raw.webp'
import eelSlicePhoto from '../assets/eel/slice.webp'
import eelTrimPhoto from '../assets/eel/trim.webp'

/* Общее для трёх карточек разбора: фото позиций, поля и мелкая типографика.

   Фото здесь не украшение. На весах стоит человек, который путает нарезку с
   обрезками — подпись он читает второй, а картинку узнаёт сразу. */

export const PHOTO = {
  raw: steakPhoto, slice: slicePhoto, trim: trimPhoto,
  teriyaki: teriyakiPhoto, sauce: saucePhoto, waste: wastePhoto,
}

/* Угорь приходит уже копчёным пластом, и на своих фото он на лосося не похож
   совсем — потому у него свой набор, а не лососёвый. Отход не сняли ни у той,
   ни у другой рыбы по-отдельности: кости и кожа выглядят одинаково. */
export const EEL_PHOTO = { raw: eelRawPhoto, slice: eelSlicePhoto, trim: eelTrimPhoto,
                           waste: wastePhoto }

/* Где фото нет — знак: узнать позицию надо с одного взгляда, а чужая рыба на
   картинке хуже, чем честный значок. */
export const GLYPH = { raw: '🐟', slice: '🍣', trim: '✂️', teriyaki: '🍢', sauce: '🍯', waste: '🦴' }

export type Slot = 'raw' | 'slice' | 'trim' | 'teriyaki' | 'waste'

/** Картинки вида разбора: у каждой рыбы свои. */
export const photosOf = (kind: string): Partial<Record<Slot, string>> =>
  kind === 'eel' ? EEL_PHOTO : PHOTO

export const kg = (n: number | null | undefined) =>
  (Number(n) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })

/** Процент по-русски: рядом с весом «7,4 кг» процент «4.9%» читается чужим. */
export const pc = (n: number | null | undefined) =>
  (Number(n) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + '%'

/** «3 разбора» вместо «3 разбор(ов)»: скобки в интерфейсе — это недоделка. */
export const plural = (n: number, one: string, few: string, many: string) => {
  const a = Math.abs(Math.round(n)) % 100
  if (a > 10 && a < 20) return many
  const b = a % 10
  return b === 1 ? one : b >= 2 && b <= 4 ? few : many
}

/** Цвет процента отхода: до 5 % — норма, до 10 % — присмотреться, дальше — плохо. */
export const wasteTone = (p: number) => (p <= 5 ? 'var(--grn)' : p <= 10 ? 'var(--amb)' : 'var(--red)')

export const field: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '9px 11px', fontSize: 13.5,
  fontFamily: 'inherit', fontVariantNumeric: 'tabular-nums',
  background: 'var(--glass-2)', color: 'var(--hi)',
  border: '1px solid var(--hair)', borderRadius: 11,
}
export const td: React.CSSProperties = { padding: '9px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
export const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
export const th: React.CSSProperties = {
  fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)',
  fontWeight: 600, textAlign: 'left', padding: '0 0 9px', borderBottom: '1px solid var(--hair)',
}

/* ── Период ───────────────────────────────────────────────────────────────
   Один календарь на свод филиала и на таблицу по сети: если границы периода
   разъедутся, управляющий будет сравнивать свой месяц с чужой неделей. */

export type Scope = 'open' | 'month' | 'prev' | 'dates'

/* Дата в местном часовом поясе. Через toISOString() Астана (UTC+5) получала
   «1 февраля 00:00» как 2026-01-31 — период начинался на день раньше, и в свод
   заезжал чужой разбор. */
export const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export function periodBounds(scope: Scope, from: string, to: string) {
  const now = new Date()
  if (scope === 'month') return { from: iso(new Date(now.getFullYear(), now.getMonth(), 1)), to: iso(now) }
  if (scope === 'prev') return {
    from: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
    to: iso(new Date(now.getFullYear(), now.getMonth(), 0)),
  }
  if (scope === 'dates') return { from, to }
  // «к занесению» — всё, что накопилось: границу ставит не календарь, а проводка
  return { from: '2020-01-01', to: iso(now) }
}

export function Eyebrow({ t, hint }: { t: string; hint?: ReactNode }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}

/* ── Плитка позиции ────────────────────────────────────────────────────────
   Фото сверху, под ним — крупный вес. Вводимые плитки ведут себя как поле:
   отклик на нажатие сразу, а не после расчёта. Итоговая (отход) не редактируется
   и подписана процентом — ради него весь экран и открывают. */

export function Tile({
  photo, glyph, label, hint, value, onChange, placeholder, readOnly, tone, foot, unit = 'кг',
}: {
  photo?: string; glyph?: string; label: string; hint?: string
  value: string; onChange?: (v: string) => void
  placeholder?: string; readOnly?: boolean; tone?: string; foot?: ReactNode
  /** Соус льют половником и меряют граммами: «0,2 кг» повар прочитает как ноль. */
  unit?: string
}) {
  const still = useReducedMotion()
  return (
    <motion.label
      className="sal-tile"
      whileTap={onChange && !still ? { scale: 0.985 } : undefined}
      transition={{ type: 'spring', bounce: 0, duration: 0.35 }}
      style={{ cursor: onChange ? 'text' : 'default' }}
    >
      <div style={{ position: 'relative', height: 74, display: 'grid', placeItems: 'center' }}>
        {/* мягкая тень под вырезом: без неё фото висит в воздухе */}
        <span aria-hidden className="sal-shade" />
        {photo ? (
          <img src={photo} alt="" className="sal-photo"
            style={{ maxHeight: 74, maxWidth: '86%', objectFit: 'contain', position: 'relative' }} />
        ) : (
          <span aria-hidden style={{ position: 'relative', fontSize: 34, lineHeight: 1, opacity: .85 }}>
            {glyph || '⬚'}
          </span>
        )}
      </div>
      <div style={{ marginTop: 9, fontSize: 11.5, color: 'var(--mute)' }}>
        {label}{hint && <span style={{ opacity: .75 }}> · {hint}</span>}
      </div>
      {readOnly ? (
        <div style={{
          marginTop: 3, fontSize: 22, fontWeight: 700, letterSpacing: '-.02em',
          fontVariantNumeric: 'tabular-nums', color: tone || 'var(--hi)',
        }}>{value || '—'}<span style={{ fontSize: 13, fontWeight: 500, color: 'var(--mute)' }}> {unit}</span></div>
      ) : (
        <div style={{ marginTop: 3, display: 'flex', alignItems: 'baseline', gap: 4 }}>
          <input value={value} inputMode="decimal" placeholder={placeholder}
            onChange={(e) => onChange?.(e.target.value)} />
          <span style={{ fontSize: 13, color: 'var(--mute)' }}>{unit}</span>
        </div>
      )}
      {foot && <div style={{ marginTop: 4, fontSize: 11.5, color: 'var(--mute)' }}>{foot}</div>}
    </motion.label>
  )
}

/* ── Переключатель периода ────────────────────────────────────────────────
   Выделение едет за выбором общим слоем (layoutId), а не мигает: видно, что
   это один орган управления, а не четыре разные кнопки. */

export function Segmented<T extends string>({
  value, onChange, options, id,
}: { value: T; onChange: (v: T) => void; options: { k: T; t: string }[]; id: string }) {
  return (
    <div className="sal-seg">
      {options.map((o) => (
        <button key={o.k} onClick={() => onChange(o.k)} aria-pressed={value === o.k}>
          {value === o.k && (
            <motion.span layoutId={id} aria-hidden className="sal-pill"
              transition={{ type: 'spring', bounce: 0, duration: 0.35 }} />
          )}
          <span style={{ position: 'relative' }}>{o.t}</span>
        </button>
      ))}
    </div>
  )
}

export function Button({
  children, onClick, disabled, kind = 'primary',
}: { children: ReactNode; onClick?: () => void; disabled?: boolean; kind?: 'primary' | 'ghost' }) {
  const primary = kind === 'primary'
  return (
    <motion.button onClick={onClick} disabled={disabled}
      whileTap={disabled ? undefined : { scale: 0.97 }}
      transition={{ type: 'spring', bounce: 0, duration: 0.3 }}
      style={{
        padding: primary ? '12px 20px' : '9px 14px', borderRadius: 14,
        border: primary ? 'none' : '1px solid var(--hair)',
        background: primary ? 'linear-gradient(135deg,var(--acc),var(--acc-2))' : 'var(--glass-2)',
        color: primary ? 'var(--on-acc)' : 'var(--hi)', fontFamily: 'inherit',
        fontSize: primary ? 13.5 : 12.5, fontWeight: 700,
        cursor: disabled ? 'default' : 'pointer', opacity: disabled ? .5 : 1,
      }}>
      {children}
    </motion.button>
  )
}
