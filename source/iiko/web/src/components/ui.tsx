import type { ReactNode } from 'react'
import { pct, tenge } from '../lib/format'
import { gradeOf } from '../lib/grade'

/* ── Структура ───────────────────────────────────────────── */

/** Секция вместо карточки: заголовок капсом + линейка снизу. */
export function Section({ title, hint, children }: {
  title: string; hint?: string; children: ReactNode
}) {
  return (
    <section className="border-b border-rule py-6 last:border-b-0">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="eyebrow">{title}</h2>
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
      </div>
      {children}
    </section>
  )
}

/* ── Числа ───────────────────────────────────────────────── */

/** Рост зелёный, падение красное. invert — когда рост это плохо (фудкост). */
export function Delta({ value, invert = false }: {
  value: number | null | undefined; invert?: boolean
}) {
  if (value == null || Number.isNaN(value)) return null
  const good = invert ? value < 0 : value > 0
  const tone = value === 0 ? 'text-muted' : good ? 'text-pos' : 'text-neg'
  const arrow = value === 0 ? '' : value > 0 ? '▲' : '▼'
  return (
    <span className={`text-xs font-semibold ${tone}`}>
      {arrow && `${arrow} `}{pct(Math.abs(value), false)}
    </span>
  )
}

/** Герой экрана. Playfair — единственное место, где он крупный. */
export function Hero({ label, value, delta, note }: {
  label: string; value: string; delta?: number | null; note?: string
}) {
  return (
    <div className="border-b border-rule py-7 fade-in">
      <div className="eyebrow mb-3">{label}</div>
      <div className="flex items-baseline gap-4">
        <span className="font-display text-[46px] leading-none tracking-tight">{value}</span>
        <Delta value={delta} />
      </div>
      {note && <p className="mt-3 max-w-lg text-[13px] leading-relaxed text-ink2">{note}</p>}
    </div>
  )
}

/** Метрика в ряду. Playfair на 22px — нижняя граница его допустимого кегля. */
export function Metric({ label, value, delta, invert }: {
  label: string; value: ReactNode; delta?: number | null; invert?: boolean
}) {
  return (
    <div className="border-r border-rule px-5 py-4 last:border-r-0">
      <div className="eyebrow mb-2">{label}</div>
      <div className="flex items-baseline gap-2">
        <span className="font-display text-[24px] leading-none">{value}</span>
        <Delta value={delta} invert={invert} />
      </div>
    </div>
  )
}

/** Ряд метрик — сетка без промежутков, разделители держат линейки. */
export function MetricRow({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 border-b border-rule lg:grid-cols-4">{children}</div>
}

/* ── Таблица ─────────────────────────────────────────────── */

export type Column = {
  key: string
  label: string
  align?: 'left' | 'right'
  /** цифровая колонка: моноширинный шрифт и выравнивание вправо */
  numeric?: boolean
}

/** Выравнивание колонки: явный `align`, иначе вправо для числовых, иначе влево. */
function colAlign(c?: Column): 'left' | 'right' {
  return c?.align ?? (c?.numeric ? 'right' : 'left')
}

export function DataTable({ columns, rows }: { columns: Column[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-ink">
            {columns.map((c) => (
              <th
                key={c.key}
                className={`eyebrow pb-2 ${colAlign(c) === 'right' ? 'text-right' : 'text-left'}`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-rule last:border-b-0">
              {row.map((cell, j) => {
                const c = columns[j]
                const numeric = !!c?.numeric
                const align = colAlign(c)
                return (
                  <td
                    key={j}
                    className={`py-2.5 ${align === 'right' ? 'text-right' : 'text-left'} ${numeric ? 'tnum' : 'text-ink'}`}
                  >
                    {cell}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Горизонтальный бар внутри таблиц. */
export function MiniBar({ value, max, tone = 'ink' }: {
  value: number; max: number; tone?: 'ink' | 'neg' | 'pos'
}) {
  const w = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0
  const bg = tone === 'neg' ? 'bg-neg' : tone === 'pos' ? 'bg-pos' : 'bg-ink'
  return (
    <div className="h-1 w-full overflow-hidden bg-paper2">
      <div className={`h-full ${bg}`} style={{ width: `${w}%` }} />
    </div>
  )
}

/* ── Инсайты ─────────────────────────────────────────────── */

/**
 * Правило из «01 — Концепция»: нет числа в тенге — инсайт не показываем.
 * Поэтому `money` обязательное. Не делать его опциональным.
 */
export type Finding = {
  title: string
  detail: string
  money: number
  tone?: 'neg' | 'warn' | 'pos'
}

export function FindingRow({ finding }: { finding: Finding }) {
  const tone = finding.tone ?? 'neg'
  const color = tone === 'pos' ? 'text-pos' : tone === 'warn' ? 'text-warn' : 'text-neg'
  return (
    <div className="border-b border-rule py-4 last:border-b-0">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-[14px] font-semibold text-ink">{finding.title}</h3>
        <span className={`tnum shrink-0 text-[13px] font-semibold ${color}`}>
          {tenge(finding.money)}
        </span>
      </div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink2">{finding.detail}</p>
    </div>
  )
}

/* ── Оценка ──────────────────────────────────────────────── */

export function Grade({ score, label }: { score: number; label: string }) {
  const letter = gradeOf(score)
  const tone = score >= 75 ? 'text-pos' : score >= 60 ? 'text-warn' : 'text-neg'
  return (
    <div className="px-5 py-4">
      <div className="eyebrow mb-2">{label}</div>
      <div className="flex items-baseline gap-3">
        <span className={`font-display text-[38px] leading-none ${tone}`}>{letter}</span>
        <span className="tnum text-[12px] text-muted">{score} из 100</span>
      </div>
    </div>
  )
}

/* ── Состояния ───────────────────────────────────────────── */

export function Loading({ label = 'Загрузка данных…' }: { label?: string }) {
  return (
    <div className="flex h-64 flex-col items-center justify-center gap-3 text-muted">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-rule border-t-ink" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

export function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="border-l-2 border-neg bg-paper2 px-4 py-3 text-sm text-neg">
      Не удалось загрузить: {msg}
    </div>
  )
}

export function Empty({ msg }: { msg: string }) {
  return (
    <div className="flex h-40 items-center justify-center text-sm text-muted">{msg}</div>
  )
}
