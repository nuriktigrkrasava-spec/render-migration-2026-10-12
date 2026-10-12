/* Графики Stripe-типа: гладкая площадь, бары-градиент, буллет, спарклайн, дрейф.
   Возвращают SVG-строки — рендерим через Chart. */
let UID = 0
const acc = 'var(--acc)'

/* Красочная категориальная палитра — только для донатов/легенд, где цвет несёт смысл */
export const PALETTE = ['#4f6ef7', '#7b5cff', '#22c1a4', '#3ecf8e', '#e0b341', '#ff9d4d', '#e5484d', '#38bdf8', '#f472b6', '#818cf8']
export const color = (i: number) => PALETTE[i % PALETTE.length]

/* Единый цвет СТОЛБЦОВ — наш акцент-индиго (адаптируется к теме). Единая гамма
   во всех вкладках; сигналы (красный=потеря, зелёный=рост) задаются явно точечно. */
export const bar = (_i?: number) => 'var(--acc)'

/* Цветной донат с числом в центре. data: [{l,v,c?}] */
export function donut(data: { l: string; v: number; c?: string }[], center?: { label?: string; sub?: string }): string {
  const W = 220, cx = 110, cy = 110, r = 82, sw = 22, GAP = 0.02
  const total = data.reduce((s, d) => s + d.v, 0) || 1
  let a0 = -Math.PI / 2, g = ''
  data.forEach((d, i) => {
    const frac = d.v / total
    const a1 = a0 + frac * Math.PI * 2
    const s = a0 + GAP, e = a1 - GAP
    if (e > s) {
      const large = (e - s) > Math.PI ? 1 : 0
      const x0 = cx + Math.cos(s) * r, y0 = cy + Math.sin(s) * r
      const x1 = cx + Math.cos(e) * r, y1 = cy + Math.sin(e) * r
      g += `<path d="M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${d.c || color(i)}" stroke-width="${sw}" stroke-linecap="round"/>`
    }
    a0 = a1
  })
  g += `<text x="${cx}" y="${cy - 1}" text-anchor="middle" fill="var(--hi)" font-size="27" font-weight="750" style="font-variant-numeric:tabular-nums">${center?.label ?? String(total)}</text>`
  if (center?.sub) g += `<text x="${cx}" y="${cy + 19}" text-anchor="middle" fill="var(--mute)" font-size="11">${center.sub}</text>`
  return `<svg viewBox="0 0 ${W} ${W}" style="width:100%;max-width:220px;height:auto;display:block;margin:0 auto">${g}</svg>`
}

/* Горизонтальные цветные бары со значением. data: [{l,v,c?,sub?}] */
export function hbar(data: { l: string; v: number; c?: string; sub?: string; disp?: string }[], unit = ''): string {
  const max = Math.max(...data.map((d) => d.v)) || 1
  const rows = data.map((d, i) => {
    const w = Math.max(2, (d.v / max) * 100)
    const col = d.c || bar(i)
    const val = Number.isInteger(d.v) ? new Intl.NumberFormat('ru-RU').format(d.v) : d.v.toFixed(1).replace('.', ',')
    // светящийся стеклянный бар (как в Директоре): градиент + свечение + фаска
    const fill = `background:linear-gradient(180deg, color-mix(in srgb, ${col} 76%, #fff), ${col});`
      + `box-shadow:0 0 12px color-mix(in srgb, ${col} 42%, transparent), inset 0 1px 0 rgba(255,255,255,.5), inset 0 -2px 4px rgba(0,0,0,.16);`
    return `<div style="display:flex;align-items:center;gap:12px;padding:8px 0">
      <div style="width:36%;min-width:96px;font-size:12.5px;color:var(--lo)">${d.l}${d.sub ? `<span style="color:var(--mute);font-size:11px"> · ${d.sub}</span>` : ''}</div>
      <div style="flex:1;height:16px;border-radius:8px;background:var(--glass-2);border:1px solid var(--hair);position:relative">
        <div style="position:absolute;top:2px;bottom:2px;left:2px;width:calc(${w.toFixed(1)}% - 4px);border-radius:6px;${fill}"></div>
      </div>
      <div class="tnum" style="width:88px;text-align:right;font-size:12.5px;font-weight:650">${d.disp ?? `${val}${unit}`}</div>
    </div>`
  }).join('')
  return `<div style="margin-top:10px">${rows}</div>`
}

/* Легенда категорий: цветная точка · подпись · значение. data: [{l,v?,c?}] */
export function legend(data: { l: string; v?: string; c?: string }[]): string {
  return `<div style="display:flex;flex-wrap:wrap;gap:10px 16px;margin-top:12px">${data.map((d, i) =>
    `<span style="display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--lo)"><i style="width:10px;height:10px;border-radius:3px;background:${d.c || color(i)}"></i>${d.l}${d.v ? `<b class="tnum" style="color:var(--hi);font-weight:650">${d.v}</b>` : ''}</span>`,
  ).join('')}</div>`
}

function smooth(pts: [number, number][]): string {
  if (pts.length < 2) return `M${pts[0]?.[0] ?? 0} ${pts[0]?.[1] ?? 0}`
  let s = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6
    s += `C${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`
  }
  return s
}

export function areaChart(data: { l: string; v: number }[], color = acc): string {
  const id = 'a' + UID++, W = 580, H = 200, pX = 10, pT = 18, pB = 30
  const max = Math.max(...data.map((x) => x.v)) * 1.1, min = Math.min(...data.map((x) => x.v)) * 0.86
  const x = (i: number) => pX + i * ((W - pX * 2) / (data.length - 1))
  const y = (v: number) => pT + (1 - (v - min) / (max - min || 1)) * (H - pT - pB)
  const pts = data.map((r, i) => [x(i), y(r.v)] as [number, number])
  const line = smooth(pts), area = line + ` L${x(data.length - 1).toFixed(1)} ${H - pB} L${x(0).toFixed(1)} ${H - pB} Z`
  let g = ''
  ;[0, 0.25, 0.5, 0.75, 1].forEach((t) => { const yy = pT + t * (H - pT - pB); g += `<line x1="${pX}" y1="${yy.toFixed(1)}" x2="${W - pX}" y2="${yy.toFixed(1)}" stroke="var(--hair)" stroke-width="1"/>` })
  data.forEach((r, i) => { if (r.l) g += `<text x="${x(i).toFixed(1)}" y="${H - 9}" text-anchor="middle" fill="var(--mute)" font-size="11">${r.l}</text>` })
  const last = pts[pts.length - 1], lastV = data[data.length - 1].v
  const lbl = Number.isInteger(lastV) ? String(lastV) : lastV.toFixed(1).replace('.', ',')
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    ${g}<path d="${area}" fill="url(#${id})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="9" fill="${color}" opacity=".16"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4" fill="${color}"/>
    <text x="${last[0].toFixed(1)}" y="${(last[1] - 14).toFixed(1)}" text-anchor="end" fill="${color}" font-size="13" font-weight="700" style="font-variant-numeric:tabular-nums">${lbl}</text></svg>`
}

export function barChart(data: { l: string; v: number }[], hiIdx = -1, color = acc, unit = ''): string {
  const max = Math.max(...data.map((x) => x.v)) || 1
  // Крупные суммы пишем полностью и с пробелами между разрядами: «747 213 ₸»
  // читается однозначно, «0,7 млн» — нет.
  const fnum = (v: number) => (Number.isInteger(v)
    ? new Intl.NumberFormat('ru-RU').format(v)
    : v.toFixed(1).replace('.', ','))
  // стеклянная вертикальная колонка: градиент + свечение + фаска (как горизонтальные бары)
  const glass = `background:linear-gradient(180deg, color-mix(in srgb, ${color} 76%, #fff), ${color});`
    + `box-shadow:0 0 14px color-mix(in srgb, ${color} 45%, transparent), inset 0 1px 0 rgba(255,255,255,.55), inset 0 -3px 6px rgba(0,0,0,.16);`
  // Полные суммы («747 213 ₸») длиннее, чем колонка на телефоне: семь штук в
  // ряд наезжали друг на друга. Колонке даём минимум под самую длинную подпись,
  // а ряду — горизонтальную прокрутку: лучше сдвинуть график пальцем, чем
  // читать наложенные цифры.
  const labels = data.map((r) => fnum(r.v) + unit)
  const longest = labels.reduce((a, b) => (b.length > a.length ? b : a), '')
  const fs = longest.length > 8 ? 10.5 : 12.5
  const colMin = Math.ceil(longest.length * fs * 0.62) + 8
  const cols = data.map((r, i) => {
    const h = Math.max(6, (r.v / max) * 100)
    const hot = i === hiIdx
    return `<div style="flex:1 0 ${colMin}px;min-width:${colMin}px;display:flex;flex-direction:column;align-items:center;gap:8px">
      <div class="tnum" style="font-size:${fs}px;font-weight:700;white-space:nowrap;color:${hot ? 'var(--hi)' : 'var(--lo)'}">${labels[i]}</div>
      <div style="width:100%;height:150px;display:flex;align-items:flex-end;justify-content:center">
        <div style="width:58%;height:${h.toFixed(1)}%;border-radius:8px 8px 4px 4px;${glass};opacity:${hot ? '1' : '.5'}"></div>
      </div>
      <div style="font-size:11px;color:var(--mute);white-space:nowrap">${r.l}</div>
    </div>`
  }).join('')
  return `<div style="overflow-x:auto;margin-top:8px"><div style="display:flex;gap:8px;align-items:flex-end;min-width:100%">${cols}</div></div>`
}

export function slope(series: { c: string; v: number[] }[]): string {
  const W = 580, H = 184, pX = 14
  const all = series.flatMap((s) => s.v), max = Math.max(...all), min = Math.min(...all)
  const x = (i: number) => pX + i * ((W - pX * 2) / (series[0].v.length - 1))
  const y = (v: number) => 16 + (1 - (v - min) / (max - min || 1)) * (H - 42)
  let g = ''
  ;[0, 0.5, 1].forEach((t) => { const yy = 16 + t * (H - 42); g += `<line x1="${pX}" y1="${yy}" x2="${W - pX}" y2="${yy}" stroke="var(--hair)" stroke-width="1"/>` })
  series.forEach((s) => {
    const pts = s.v.map((v, i) => [x(i), y(v)] as [number, number]), path = smooth(pts), l = pts[pts.length - 1]
    g += `<path d="${path}" fill="none" stroke="${s.c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>`
    g += `<circle cx="${l[0].toFixed(1)}" cy="${l[1].toFixed(1)}" r="8" fill="${s.c}" opacity=".16"/><circle cx="${l[0].toFixed(1)}" cy="${l[1].toFixed(1)}" r="3.6" fill="${s.c}"/>`
  })
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}</svg>`
}

export function spark(v: number[], color = acc): string {
  const id = 's' + UID++, W = 160, H = 46, max = Math.max(...v), min = Math.min(...v)
  const x = (i: number) => i * (W / (v.length - 1)), y = (t: number) => 5 + (1 - (t - min) / (max - min || 1)) * (H - 10)
  const pts = v.map((t, i) => [x(i), y(t)] as [number, number]), line = smooth(pts), area = line + ` L${W} ${H} L0 ${H} Z`
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:46px;display:block">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".32"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round"/>
    <circle cx="${x(v.length - 1).toFixed(1)}" cy="${y(v[v.length - 1]).toFixed(1)}" r="3" fill="${color}"/></svg>`
}

/* радиальный «спектр»-циферблат как в image 2: кольцо лучей разной длины (моно-серый),
   с двумя категориями-метками. Центр пустой (донат). */
export function radialDial(a: { l: string; pct: number }, b: { l: string; pct: number }): string {
  const W = 300, cx = W / 2, cy = W / 2, rIn = 74, N = 96
  let g = ''
  for (let i = 0; i < N; i++) {
    const ang = (i / N) * Math.PI * 2 - Math.PI / 2
    // псевдо-шумовая длина луча
    const n = 0.5 + 0.5 * Math.sin(i * 0.7) * Math.cos(i * 0.31 + 1.3)
    const len = 10 + n * 46
    const x1 = cx + Math.cos(ang) * rIn, y1 = cy + Math.sin(ang) * rIn
    const x2 = cx + Math.cos(ang) * (rIn + len), y2 = cy + Math.sin(ang) * (rIn + len)
    const light = 0.32 + 0.62 * n
    g += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="rgba(224,226,238,${light.toFixed(2)})" stroke-width="2" stroke-linecap="round"/>`
  }
  // дуга-акцент (bronze) под верхний показатель
  const arc = (frac: number, color: string, r: number) => {
    const a0 = -Math.PI / 2, a1 = a0 + frac * Math.PI * 2
    const x0 = cx + Math.cos(a0) * r, y0 = cy + Math.sin(a0) * r
    const x1 = cx + Math.cos(a1) * r, y1 = cy + Math.sin(a1) * r
    const large = frac > 0.5 ? 1 : 0
    return `<path d="M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round"/>`
  }
  g += arc(a.pct / 100, 'rgba(224,226,238,.4)', rIn - 16)
  g += arc(b.pct / 100, 'var(--acc)', rIn - 8)
  return `<svg viewBox="0 0 ${W} ${W}" style="width:100%;max-width:300px;height:auto;display:block;margin:0 auto">${g}</svg>`
}

export function heatStrip(hours: { h: number; v: number }[]): string {
  const max = Math.max(...hours.map((h) => h.v))
  return `<div style="display:flex;gap:5px;margin-top:14px;align-items:flex-end">${hours.map((h) => {
    const r = h.v / max, o = (0.28 + 0.72 * r).toFixed(2)
    const c = r > 0.85 ? 'var(--red)' : r > 0.62 ? 'var(--amb)' : 'var(--acc)'
    const pct = Math.round(r * 100)
    return `<div style="flex:1;text-align:center">
      <div style="font-size:9.5px;font-weight:700;color:${c};margin-bottom:5px" class="tnum">${pct}</div>
      <div style="border-radius:7px;height:${(28 + 62 * r).toFixed(0)}px;background:linear-gradient(180deg, color-mix(in srgb, ${c} 78%, #fff), ${c});opacity:${o};box-shadow:0 0 9px color-mix(in srgb, ${c} 42%, transparent), inset 0 1px 0 rgba(255,255,255,.5)" title="${h.h}:00 · ${pct}%"></div>
      <div style="font-size:9.5px;color:var(--mute);margin-top:6px" class="tnum">${h.h}</div></div>`
  }).join('')}</div>`
}
