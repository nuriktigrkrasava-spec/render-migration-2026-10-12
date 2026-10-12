const nf = new Intl.NumberFormat('ru-RU')

/** 944617 → "944 617 ₸" */
export function tenge(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `${nf.format(Math.round(n))} ₸`
}

/** компактно: 18720000 → "18,7 млн ₸" */
export function tengeShort(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  const a = Math.abs(n)
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace('.', ',')} млн ₸`
  if (a >= 1_000) return `${Math.round(n / 1_000)} тыс ₸`
  return `${nf.format(Math.round(n))} ₸`
}

export function num(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return nf.format(n)
}

/** +21.4 → "+21,4%" */
export function pct(n: number | null | undefined, sign = true): string {
  if (n == null || Number.isNaN(n)) return '—'
  const s = sign && n > 0 ? '+' : ''
  return `${s}${n.toFixed(1).replace('.', ',')}%`
}
