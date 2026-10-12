import { useEffect, useState } from 'react'
import { Glass } from './ui'

/* Данные нашего собственного КДС (saya-kds) через прокси /api/chaspik.
   Ключ X-API-Key живёт на бэке и во фронт не попадает; эндпоинт открыт только
   владельцу (CHASPIK_TG_IDS в api.py) и считает ОДИН филиал — CHASPIK_ORG_ID,
   поэтому карточки не зависят от переключателя филиалов в шапке. */

type Cell = { count: number; avg_sec: number | null }
type ZoneLoad = {
  zones: string[]
  hours: number[]
  cells: Record<string, Record<string, Cell>>
  totals_by_zone: Record<string, Cell>
}
type DeliveryPoint = { zone_name: string | null; delivery_seconds: number | null; lat?: number | null; lon?: number | null }
type Stage = { key: string; label: string; minutes: number; seconds: number; count: number }
type OrderPath = { stages: Stage[]; total_minutes: number; unit_label?: string }
type Chaspik = {
  zone_load?: ZoneLoad
  delivery_points?: DeliveryPoint[]
  order_path?: OrderPath
  period?: { from: string; to: string }
}

const STAGE_COLORS: Record<string, string> = {
  cooking: 'var(--amb)', assembly: '#7b5cff', pickup_wait: 'var(--mute)', delivery: 'var(--acc)',
}

const ZONE_RU: Record<string, string> = {
  pizza: 'Пицца', rolls: 'Роллы', hot: 'Горячий цех', cold: 'Холодный цех', other: 'Прочее',
}
const zoneLabel = (z: string) => ZONE_RU[z] || z
const mins = (sec: number | null | undefined) => (sec == null ? '—' : `${Math.round(sec / 60)} мин`)

function Eyebrow({ t, hint }: { t: string; hint?: string }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}

export default function Kds() {
  const [data, setData] = useState<Chaspik | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [locked, setLocked] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetch('/api/chaspik', { headers: { Accept: 'application/json' } })
      .then(async (r) => {
        const body = await r.json().catch(() => null)
        // 403 — это не поломка, а другой продукт: карточки считаются по данным
        // нашего КДС и закрыты для тех, у кого его нет. Отделяем от настоящих
        // сбоев, чтобы не показывать человеку «forbidden» и переменные окружения.
        if (r.status === 403) { setLocked(true); return null }
        if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
        return body as Chaspik
      })
      .then((d) => { if (alive) setData(d) })
      .catch((e) => { if (alive) setErr(String(e.message || e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  if (loading) return <Glass span={12}><div style={{ padding: '10px 2px', fontSize: 13.5, color: 'var(--mute)' }}>Тяну данные КДС…</div></Glass>
  if (locked) {
    return (
      <Glass span={12}>
        <Eyebrow t="Кухня и доставка · КДС" hint="для пользователей нашего КДС" />
        <div style={{ marginTop: 10, fontSize: 13.5, color: 'var(--lo)', lineHeight: 1.55 }}>
          Эти карточки считаются по данным <b>нашего КДС</b>: загрузка зон кухни по
          часам, зоны доставки и путь заказа по этапам — от готовки до передачи курьеру.
          <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--mute)' }}>
            iiko таких данных не отдаёт — их даёт кухонный экран. Раздел откроется,
            когда на кухне будет наш КДС.
          </div>
        </div>
      </Glass>
    )
  }
  if (err) {
    return (
      <Glass span={12}>
        <Eyebrow t="Кухня и доставка · КДС" hint="источник недоступен" />
        <div style={{ marginTop: 10, fontSize: 13, color: 'var(--lo)', lineHeight: 1.5 }}>
          {err}
          <div style={{ marginTop: 6, fontSize: 12, color: 'var(--mute)' }}>
            Нужны ANALYTICS_API_KEY и CHASPIK_ORG_ID в окружении бэкенда.
          </div>
        </div>
      </Glass>
    )
  }

  const zl = data?.zone_load
  const pts = data?.delivery_points || []
  const op = data?.order_path

  // ── зоны кухни × час ──
  const zones = (zl?.zones || []).filter((z) => (zl?.totals_by_zone?.[z]?.count || 0) > 0)
  const cellCount = (z: string, h: number) => zl?.cells?.[z]?.[String(h)]?.count || 0
  // часы показываем только рабочие: пустые колонки съедают ширину и ничего не говорят
  const hours = (zl?.hours || []).filter((h) => zones.some((z) => cellCount(z, h) > 0))
  const maxCell = Math.max(1, ...zones.flatMap((z) => hours.map((h) => cellCount(z, h))))
  const byHour = hours.map((h) => ({ h, n: zones.reduce((s, z) => s + cellCount(z, h), 0) }))
  const peak = byHour.reduce<{ h: number; n: number } | null>((m, x) => (x.n > (m?.n ?? -1) ? x : m), null)

  // ── зоны доставки ──
  const zmap = new Map<string, { n: number; secs: number[] }>()
  for (const p of pts) {
    const k = p.zone_name || 'Без зоны'
    const e = zmap.get(k) || { n: 0, secs: [] }
    e.n += 1
    if (p.delivery_seconds != null) e.secs.push(p.delivery_seconds)
    zmap.set(k, e)
  }
  // Зоны (D3/E3/C3) убраны: это клетки авто-сетки, владельцу они ничего не говорят.
  // Остаётся то, что читается без легенды: где заказывают и как быстро возим.
  const allSecs = pts.map((p) => p.delivery_seconds).filter((s): s is number => s != null).sort((a, b) => a - b)
  const medAll = allSecs.length ? allSecs[Math.floor(allSecs.length / 2)] : null

  // ── карта: точки заказов по координатам ──
  // Рисуем инлайновым SVG: ни тайлов, ни библиотек — тридцать кружков ничего не грузят.
  // Одинаковые адреса схлопываем (~100 м, как кластеры в КДС) и растим точку по числу
  // заказов: «где заказывают чаще» должно читаться размером, а не подписью.
  const geo = pts.filter((p) => p.lat != null && p.lon != null)
  const clusters = new Map<string, { lat: number; lon: number; n: number }>()
  for (const p of geo) {
    const key = `${p.lat!.toFixed(3)},${p.lon!.toFixed(3)}`
    const c = clusters.get(key)
    if (c) c.n += 1
    else clusters.set(key, { lat: p.lat!, lon: p.lon!, n: 1 })
  }
  const cl = [...clusters.values()]
  const lats = cl.map((c) => c.lat), lons = cl.map((c) => c.lon)
  const pad = 0.004
  const minLat = Math.min(...lats) - pad, maxLat = Math.max(...lats) + pad
  const minLon = Math.min(...lons) - pad, maxLon = Math.max(...lons) + pad
  // на широте Астаны (51°) градус долготы примерно вдвое короче градуса широты —
  // без этой поправки карта растянута по горизонтали и город «съезжает»
  const kx = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180)
  const mapW = (maxLon - minLon) * kx, mapH = maxLat - minLat
  const VW = 1000, VH = Math.max(280, Math.round((mapH / (mapW || 1)) * VW))
  const px = (lon: number) => (((lon - minLon) * kx) / (mapW || 1)) * VW
  const py = (lat: number) => ((maxLat - lat) / (mapH || 1)) * VH // север сверху
  const maxN = Math.max(1, ...cl.map((c) => c.n))

  return (
    <>
      <Glass span={12}>
        <Eyebrow t="Кухня по зонам · час" hint={peak ? `пик в ${peak.h}:00 — ${peak.n} блюд` : 'нагрузка КДС'} />
        {zones.length && hours.length ? (
          <div style={{ overflowX: 'auto', marginTop: 12 }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 11, minWidth: '100%' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '0 8px 8px 0', color: 'var(--mute)', fontWeight: 600, position: 'sticky', left: 0, background: 'var(--card)' }}>Зона</th>
                  {hours.map((h) => (
                    <th key={h} style={{ padding: '0 2px 8px', color: h === peak?.h ? 'var(--hi)' : 'var(--mute)', fontWeight: h === peak?.h ? 700 : 500, minWidth: 26 }}>{h}</th>
                  ))}
                  <th style={{ padding: '0 0 8px 10px', color: 'var(--mute)', fontWeight: 600, textAlign: 'right' }}>Ср. готовка</th>
                </tr>
              </thead>
              <tbody>
                {zones.map((z) => (
                  <tr key={z}>
                    <td style={{ padding: '3px 8px 3px 0', fontWeight: 600, whiteSpace: 'nowrap', position: 'sticky', left: 0, background: 'var(--card)' }}>{zoneLabel(z)}</td>
                    {hours.map((h) => {
                      const c = cellCount(z, h)
                      return (
                        <td key={h} className="tnum" style={{
                          padding: '3px 2px', textAlign: 'center', borderRadius: 5,
                          background: c ? `color-mix(in srgb, var(--acc) ${Math.round(12 + 76 * (c / maxCell))}%, transparent)` : 'transparent',
                          color: c && c / maxCell > 0.55 ? 'var(--on-acc)' : 'var(--lo)',
                        }}>{c || ''}</td>
                      )
                    })}
                    <td className="tnum" style={{ padding: '3px 0 3px 10px', textAlign: 'right', color: 'var(--mute)' }}>{mins(zl?.totals_by_zone?.[z]?.avg_sec)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div style={{ marginTop: 10, fontSize: 13, color: 'var(--mute)' }}>КДС не отдал нагрузку по зонам за период.</div>}
      </Glass>

      {op && op.stages?.length > 0 && (
        <Glass span={5}>
          <Eyebrow t="Путь заказа" hint={`${op.total_minutes} ${op.unit_label || 'мин до клиента'}`} />
          <div className="tnum" style={{ fontSize: 40, fontWeight: 680, letterSpacing: '-.03em', marginTop: 14 }}>
            {op.total_minutes} <span style={{ fontSize: 15, fontWeight: 500, color: 'var(--mute)' }}>мин</span>
          </div>
          {/* одна полоса на весь путь: сразу видно, какой этап съедает время */}
          <div style={{ display: 'flex', height: 12, borderRadius: 7, overflow: 'hidden', marginTop: 16, border: '1px solid var(--hair)' }}>
            {op.stages.map((s) => (
              <div key={s.key} title={`${s.label} · ${s.minutes} мин`}
                style={{ width: `${(s.minutes / (op.total_minutes || 1)) * 100}%`, background: STAGE_COLORS[s.key] || 'var(--acc)' }} />
            ))}
          </div>
          <div style={{ marginTop: 14 }}>
            {[...op.stages].sort((a, b) => b.minutes - a.minutes).map((s, i) => (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '9px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
                <span style={{ width: 9, height: 9, borderRadius: 3, background: STAGE_COLORS[s.key] || 'var(--acc)', flex: 'none' }} />
                <span style={{ fontSize: 13, color: 'var(--hi)', flex: 1 }}>{s.label}</span>
                <span style={{ fontSize: 11, color: 'var(--mute)' }}>{s.count} шт</span>
                <span className="tnum" style={{ fontSize: 13.5, fontWeight: 650, minWidth: 54, textAlign: 'right' }}>{s.minutes} мин</span>
              </div>
            ))}
          </div>
        </Glass>
      )}

      <Glass span={op && op.stages?.length ? 7 : 12}>
        <Eyebrow t="Карта доставки" hint={`${geo.length} заказов${medAll != null ? ` · медиана ${mins(medAll)}` : ''}`} />
        {cl.length ? (
          <>
            {/* высота ограничена: карта — это ориентир «куда возим», а не главный
                экран, разворачивать её на пол-страницы незачем */}
            <svg viewBox={`0 0 ${VW} ${VH}`} width="100%" preserveAspectRatio="xMidYMid meet"
              style={{ marginTop: 12, display: 'block', maxHeight: 190, borderRadius: 14, background: 'var(--glass-2)', border: '1px solid var(--hair)' }}
              role="img" aria-label="Карта заказов доставки">
              {cl.map((c, i) => {
                const r = 9 + (c.n / maxN) * 24
                return (
                  <g key={i}>
                    <circle cx={px(c.lon)} cy={py(c.lat)} r={r} fill="var(--acc)" opacity={0.18} />
                    <circle cx={px(c.lon)} cy={py(c.lat)} r={r * 0.42} fill="var(--acc)" opacity={0.95} />
                    {c.n > 1 && (
                      <text x={px(c.lon)} y={py(c.lat) + 6} textAnchor="middle" fontSize="17" fontWeight="700" fill="var(--hi)">{c.n}</text>
                    )}
                  </g>
                )
              })}
            </svg>
            <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--mute)' }}>
              Точка — адрес доставки, размер и число — сколько раз оттуда заказывали.
            </div>
          </>
        ) : (
          <div style={{ marginTop: 10, fontSize: 13, color: 'var(--mute)' }}>
            Заказов с координатами за период нет — рисовать на карте нечего.
          </div>
        )}
      </Glass>
    </>
  )
}
