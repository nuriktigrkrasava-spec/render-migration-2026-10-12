import type { ReactNode } from 'react'
import { motion } from 'framer-motion'

/* ── Формат (₸, проценты, дельты) ── */
export const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(Math.round(n))
export const tg = (n: number) => fmt(n) + ' ₸'

export function Delta({ v, invert = false, size = 13 }: { v?: number | null; invert?: boolean; size?: number }) {
  if (v == null) return null
  const good = invert ? v < 0 : v > 0
  const color = v === 0 ? 'var(--mute)' : good ? 'var(--grn)' : 'var(--red)'
  const a = v === 0 ? '' : v > 0 ? '▲' : '▼'
  return (
    <span className="tnum" style={{ color, fontSize: size, fontWeight: 600 }}>
      {a} {Math.abs(v).toFixed(1).replace('.', ',')}%
    </span>
  )
}

/* Trend — когда процент САМ является значением карточки, а не припиской к нему.
   Delta рядом с тем же числом даёт «+49,3% ▲ 49,3%» — число дважды. Здесь оно одно:
   направление несут цвет и стрелка, размер шрифта наследуется от карточки. */
export function Trend({ v, invert = false }: { v?: number | null; invert?: boolean }) {
  if (v == null) return null
  const good = invert ? v < 0 : v > 0
  const color = v === 0 ? 'var(--mute)' : good ? 'var(--grn)' : 'var(--red)'
  const a = v === 0 ? '' : v > 0 ? '▲' : '▼'
  return (
    <span className="tnum" style={{ color }}>
      {a && <span style={{ fontSize: '.62em', verticalAlign: 'middle', marginRight: '.18em' }}>{a}</span>}
      {Math.abs(v).toFixed(1).replace('.', ',')}%
    </span>
  )
}

/* ── Пружины iOS ── */
export const spring = { type: 'spring', stiffness: 300, damping: 32 } as const
export const container = { hidden: {}, show: { transition: { staggerChildren: 0.035, delayChildren: 0.02 } } }
export const item = {
  hidden: { opacity: 0, y: 8, scale: 0.995 },
  show: { opacity: 1, y: 0, scale: 1, transition: spring },
}

/* ── Стеклянная карточка ── */
export function Glass({
  children, span = 4, tap = false, warp = false, className = '', style,
}: { children: ReactNode; span?: number; spot?: boolean; tap?: boolean; warp?: boolean; className?: string; style?: React.CSSProperties }) {
  return (
    <motion.div
      variants={item}
      whileHover={tap ? { y: -3, transition: spring } : undefined}
      whileTap={tap ? { scale: 0.985 } : undefined}
      className={`glass ${warp ? 'warp' : ''} ${className}`}
      style={{
        gridColumn: `span ${span}`, cursor: tap ? 'pointer' : undefined,
        ...style,
      }}
    >
      {children}
    </motion.div>
  )
}

/* Единое состояние экрана: вместо одинаковых локальных Note-компонентов.
   На загрузке три спокойных штриха сохраняют геометрию страницы; ошибка и
   пустое состояние остаются обычным читаемым текстом. */
export function PageState({ children, loading = false }: { children: ReactNode; loading?: boolean }) {
  return (
    <Glass span={12} className="lg-page-state">
      {loading && <span className="lg-state-mark" aria-hidden><i /><i /><i /></span>}
      <span role="status" aria-live="polite" aria-busy={loading}>{children}</span>
    </Glass>
  )
}

export function AppFallback({ label = 'Загружаю…' }: { label?: string }) {
  return <div className="lg-app-loader" role="status"><span className="lg-state-mark" aria-hidden><i /><i /><i /></span>{label}</div>
}

/* ── SVG-график из строки ── */
export function Chart({ html, style }: { html: string; style?: React.CSSProperties }) {
  return <div style={{ marginTop: 14, ...style }} dangerouslySetInnerHTML={{ __html: html }} />
}

/* ── Орб светофора ── */
export function Orb({ tone }: { tone: 'green' | 'amber' | 'red' }) {
  const c = tone === 'green' ? 'var(--grn)' : tone === 'amber' ? 'var(--amb)' : 'var(--red)'
  return (
    <span style={{ position: 'relative', flex: 'none' }}>
      <span style={{ display: 'block', width: 13, height: 13, borderRadius: '50%', background: c }} />
      <motion.span
        aria-hidden
        style={{ position: 'absolute', inset: -5, borderRadius: '50%', background: c, filter: 'blur(6px)', opacity: 0.55 }}
        animate={tone === 'red' ? { opacity: [0.35, 0.7, 0.35] } : {}}
        transition={tone === 'red' ? { duration: 2.2, repeat: Infinity } : {}}
      />
    </span>
  )
}

export function Light({ tone, k, v }: { tone: 'green' | 'amber' | 'red'; k: string; v: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 11, padding: '12px 13px', borderRadius: 15,
      border: '1px solid var(--hair)', background: 'var(--glass-2)',
    }}>
      <Orb tone={tone} />
      <span style={{ fontSize: 12.5, color: 'var(--lo)' }}>{k}</span>
      <span className="tnum" style={{ fontSize: 13.5, fontWeight: 650, marginLeft: 'auto' }}>{v}</span>
    </div>
  )
}

/* ── Стеклянная заливка бара (градиент + свечение + фаска) — единый вид ── */
export const gfill = (color: string): React.CSSProperties => ({
  background: `linear-gradient(180deg, color-mix(in srgb, ${color} 76%, #fff), ${color})`,
  boxShadow: `0 0 10px color-mix(in srgb, ${color} 42%, transparent), inset 0 1px 0 rgba(255,255,255,.5), inset 0 -2px 4px rgba(0,0,0,.16)`,
})

/* ── Переиспользуемый глас-бар (трек + анимированная стеклянная заливка) ── */
export function GBar({ pct, color = 'var(--acc)', h = 10, delay = 0 }: { pct: number; color?: string; h?: number; delay?: number }) {
  const w = Math.min(100, Math.max(0, pct))
  return (
    <div style={{ position: 'relative', height: h, borderRadius: h / 2 + 2, background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
      <motion.div initial={{ width: 0 }} animate={{ width: `calc(${w}% - 4px)` }} transition={{ duration: 0.7, delay }}
        style={{ position: 'absolute', top: 2, bottom: 2, left: 2, borderRadius: Math.max(3, (h - 4) / 2), ...gfill(color) }} />
    </div>
  )
}

/* ── Буллет «факт vs цель» (стеклянный) ── */
export function Bullet({ value, target, max, tone = 'acc' }: { value: number; target?: number; max: number; tone?: 'acc' | 'warn' | 'bad' }) {
  const pct = Math.min(100, (value / max) * 100)
  const col = tone === 'bad' ? 'var(--red)' : tone === 'warn' ? 'var(--amb)' : 'var(--acc)'
  return (
    <div style={{ position: 'relative', height: 10, borderRadius: 7, background: 'var(--glass-2)', border: '1px solid var(--hair)', overflow: 'visible' }}>
      <motion.div initial={{ width: 0 }} animate={{ width: `calc(${pct}% - 4px)` }} transition={{ ...spring, delay: 0.15 }}
        style={{ position: 'absolute', top: 2, bottom: 2, left: 2, borderRadius: 5, ...gfill(col) }} />
      {target != null && (
        <div style={{ position: 'absolute', top: -3, bottom: -3, left: `${(target / max) * 100}%`, width: 2, background: 'var(--hi)', borderRadius: 2, zIndex: 2 }} />
      )}
    </div>
  )
}

/* ── Раскрывающийся инсайт ── */
export function Insight({ tag, title, money, tone, detail }: { tag: string; title: string; money: string; tone: 'neg' | 'warn' | 'pos'; detail: string }) {
  const c = tone === 'pos' ? 'var(--grn)' : tone === 'warn' ? 'var(--amb)' : 'var(--red)'
  return (
    <motion.details style={{ borderTop: '1px solid var(--hair-2)', padding: '14px 0' }}>
      <summary style={{ listStyle: 'none', cursor: 'pointer', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 16 }}>
        <span style={{ fontSize: 14, fontWeight: 620, letterSpacing: '-.01em', display: 'flex', gap: 9, alignItems: 'baseline' }}>
          <span style={{ fontSize: 9, letterSpacing: '.09em', textTransform: 'uppercase', color: 'var(--acc)', fontWeight: 700, background: 'color-mix(in srgb,var(--acc) 13%,transparent)', padding: '3px 7px', borderRadius: 7 }}>{tag}</span>
          {title}
        </span>
        <span className="tnum" style={{ color: c, fontWeight: 650, fontSize: 13, whiteSpace: 'nowrap' }}>{money}</span>
      </summary>
      <p style={{ margin: '10px 0 0', fontSize: 12.5, lineHeight: 1.55, color: 'var(--lo)' }}>{detail}</p>
    </motion.details>
  )
}

/* ── Табличные строки ── */
export function Row({ children }: { children: ReactNode }) {
  return <tr style={{ borderBottom: '1px solid var(--hair-2)' }}>{children}</tr>
}

/* ── План-фоллбэк ──
   Показываем ТОЛЬКО когда владелец реально не заполнил источник в iiko
   (has_recipes/has_socials/… = false). Не «значение = 0», не демо:
   пусто при has_*=true — это баг, его чиним, а не маскируем планом.
   reason — почему данных нет; action — что владельцу сделать (шаг плана). */
export function PlanFallback({ title, reason, action }: { title?: string; reason: string; action: string }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 8,
      padding: '16px 18px', borderRadius: 14,
      border: '1px solid color-mix(in srgb, var(--amb) 30%, transparent)',
      background: 'color-mix(in srgb, var(--amb) 7%, transparent)',
    }}>
      <span style={{
        alignSelf: 'flex-start', fontSize: 10, fontWeight: 700, letterSpacing: '.11em',
        textTransform: 'uppercase', color: 'var(--amb)',
        padding: '3px 8px', borderRadius: 999,
        background: 'color-mix(in srgb, var(--amb) 15%, transparent)',
      }}>План</span>
      {title && <div style={{ fontSize: 14, fontWeight: 650, color: 'var(--hi)' }}>{title}</div>}
      <div style={{ fontSize: 12.5, color: 'var(--lo)', lineHeight: 1.45 }}>{reason}</div>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--hi)', lineHeight: 1.45 }}>→ {action}</div>
    </div>
  )
}
