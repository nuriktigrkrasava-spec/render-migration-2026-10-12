import { useState } from 'react'
import { Glass } from './ui'
import { useApi } from '../lib/useApi'
import { tenge } from '../lib/format'

/* Опоздания по доставкам выбранного филиала — из OLAP «Заказы-доставка».
   Сводка + КАЖДОЕ опоздание поимённо: номер, курьер, адрес, обещали/приехали. */

type LateOrder = {
  number: number | string | null
  date: string
  courier: string
  address: string
  source: string
  expected: string
  actual: string
  delay_min: number
  amount: number
  suspicious: boolean
  cause: { key: string; label: string; minutes: number }
  stages: { kitchen: number | null; idle: number | null; road: number | null }
}
type LateT = {
  delivered: number
  late: number
  late_pct: number
  median_delay: number
  worst_delay: number
  suspicious: number
  norms?: { kitchen: number; idle: number; road: number }
  causes?: { key: string; label: string; count: number }[]
  by_type?: Record<string, {
    title: string; orders: number; measured: number
    kitchen: number; idle: number; road: number; total: number
  }>
  top_cause?: { key: string; label: string; count: number } | null
  orders: LateOrder[]
  period?: { from: string; to: string }
}

const SHOW_STEP = 12

/* Причина опоздания считается из отметок времени заказа: принят → приготовлен →
   отдан курьеру → доставлен. Каждый этап сравнивается с медианой ЭТОГО филиала,
   поэтому «долго» значит «дольше, чем у них обычно». */
const CAUSE_TONE: Record<string, string> = {
  kitchen: 'var(--red)',        // наша зона: кухня не успела
  idle: 'var(--red)',           // наша зона: готовое стояло
  road: 'var(--amb)',           // дорога — пробки, расстояние
  accepted_late: 'var(--amb)',  // срок обещали нереальный
  unknown: 'var(--mute)',
}
const CAUSE_SHORT: Record<string, string> = {
  kitchen: 'кухня', idle: 'ждал курьера', road: 'дорога',
  accepted_late: 'срок нереальный', unknown: 'нет данных',
}

/* Своя шкала периода: экран «Смены и персонал» непериодический, глобального
   переключателя в шапке на нём нет, а опоздания смотрят то за вчера, то за
   конкретный день («что было в субботу»). Поэтому выбор живёт внутри карточки. */
type Preset = 'today' | 'yesterday' | 'week' | 'month'
const PRESETS: [Preset, string][] = [
  ['today', 'Сегодня'], ['yesterday', 'Вчера'], ['week', 'Неделя'], ['month', 'Месяц'],
]
const iso = (d: Date) => d.toISOString().slice(0, 10)
const ddmm = (s: string) => (s && s.length >= 10 ? `${s.slice(8, 10)}.${s.slice(5, 7)}` : s)

function Eyebrow({ t, hint }: { t: string; hint?: string }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}
const th: React.CSSProperties = {
  fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)',
  fontWeight: 600, textAlign: 'left', padding: '0 0 10px', borderBottom: '1px solid var(--hair)',
}
const td: React.CSSProperties = { padding: '10px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 12.5 }

export default function LateDeliveries() {
  const [preset, setPreset] = useState<Preset>('month')
  const [custom, setCustom] = useState(false)
  const today = iso(new Date())
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(today)
  const [shown, setShown] = useState(SHOW_STEP)

  // period передаём явно — иначе useApi подставил бы глобальный фильтр периода
  const params: Record<string, string> = custom
    ? { period: 'custom', from, to: to < from ? from : to }
    : { period: preset }
  const { data, error, loading } = useApi<LateT>('late-deliveries', params)

  const pick = (p: Preset) => { setPreset(p); setCustom(false); setShown(SHOW_STEP) }
  const setDay = (d: string) => { setFrom(d); setTo(d); setCustom(true); setShown(SHOW_STEP) }

  const chip = (on: boolean): React.CSSProperties => ({
    font: 'inherit', fontSize: 12, fontWeight: 600, cursor: 'pointer', padding: '6px 12px', borderRadius: 10,
    color: on ? 'var(--on-acc)' : 'var(--lo)',
    background: on ? 'linear-gradient(135deg,var(--acc),var(--acc-2))' : 'var(--glass-2)',
    border: `1px solid ${on ? 'transparent' : 'var(--hair)'}`,
  })
  const dateInput: React.CSSProperties = {
    font: 'inherit', fontSize: 12, color: 'var(--hi)', background: 'var(--glass-2)',
    border: '1px solid var(--hair)', borderRadius: 10, padding: '5px 9px',
  }

  const picker = (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 14 }}>
      {PRESETS.map(([p, label]) => (
        <button key={p} onClick={() => pick(p)} style={chip(!custom && preset === p)}>{label}</button>
      ))}
      <span style={{ width: 1, height: 20, background: 'var(--hair)', margin: '0 4px' }} />
      <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>день</span>
      <input type="date" max={today} value={custom && from === to ? from : ''}
        onChange={(e) => e.target.value && setDay(e.target.value)} style={dateInput} aria-label="Конкретный день" />
      <span style={{ fontSize: 11.5, color: 'var(--mute)', marginLeft: 4 }}>период</span>
      <input type="date" max={today} value={from}
        onChange={(e) => { setFrom(e.target.value); setCustom(true); setShown(SHOW_STEP) }} style={dateInput} aria-label="Период с" />
      <span style={{ color: 'var(--mute)' }}>–</span>
      <input type="date" max={today} value={to}
        onChange={(e) => { setTo(e.target.value); setCustom(true); setShown(SHOW_STEP) }} style={dateInput} aria-label="Период по" />
    </div>
  )

  const shell = (body: React.ReactNode) => (
    <Glass span={12}>
      <Eyebrow t="Опоздания по доставкам" hint="этот филиал · источник — отчёт «Заказы-доставка»" />
      {picker}
      {body}
    </Glass>
  )

  if (loading) return shell(<div style={{ marginTop: 16, fontSize: 13.5, color: 'var(--mute)' }}>Считаю опоздания…</div>)
  if (error || !data) return shell(<div style={{ marginTop: 16, fontSize: 13.5, color: 'var(--mute)' }}>Не загрузилось: {error}</div>)

  const list = data.orders || []
  const tone = data.late_pct >= 15 ? 'var(--red)' : data.late_pct >= 7 ? 'var(--amb)' : 'var(--grn)'
  const span = data.period ? `${ddmm(data.period.from)} – ${ddmm(data.period.to)}` : ''

  return (
    <Glass span={12}>
      <Eyebrow t="Опоздания по доставкам" hint={`${span} · этот филиал · источник — отчёт «Заказы-доставка»`} />
      {picker}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 26, alignItems: 'baseline', marginTop: 14 }}>
        <div>
          <div className="tnum" style={{ fontSize: 40, fontWeight: 680, letterSpacing: '-.03em', color: tone }}>{data.late}</div>
          <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>опоздали из {data.delivered} доставок · {data.late_pct}%</div>
        </div>
        <div>
          <div className="tnum" style={{ fontSize: 22, fontWeight: 660 }}>{data.median_delay} мин</div>
          <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>медиана опоздания</div>
        </div>
        <div>
          <div className="tnum" style={{ fontSize: 22, fontWeight: 660 }}>{data.worst_delay} мин</div>
          <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 2 }}>худшее</div>
        </div>
      </div>

      {data.top_cause && (
        <div style={{ marginTop: 14, padding: '11px 13px', borderRadius: 12,
          background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
          <div style={{ fontSize: 13, color: 'var(--hi)' }}>
            Чаще всего — <b>{data.top_cause.label}</b>: {data.top_cause.count} из {data.late}
          </div>
          {data.norms && (
            <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 5 }}>
              Обычные сроки этого филиала: кухня {data.norms.kitchen} мин · заказ ждёт курьера {data.norms.idle} мин ·
              дорога {data.norms.road} мин. Это {data.norms.kitchen + data.norms.road} минут от приёма до двери — с этим и
              нужно сверять обещанное гостю время.
            </div>
          )}
          {(data.causes?.length ?? 0) > 1 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 9 }}>
              {data.causes!.map((c) => (
                <span key={c.key} style={{ fontSize: 11.5, color: CAUSE_TONE[c.key] || 'var(--lo)',
                  border: '1px solid var(--hair)', borderRadius: 8, padding: '3px 8px' }}>
                  {CAUSE_SHORT[c.key] || c.key} · {c.count}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {data.by_type && Object.keys(data.by_type).length > 0 && (() => {
        const types = Object.entries(data.by_type!)
        const max = Math.max(...types.map(([, t]) => t.total), 1)
        const SEG: [keyof typeof types[0][1], string, string][] = [
          ['kitchen', 'кухня', 'var(--acc)'],
          ['idle', 'ждал курьера', 'var(--amb)'],
          ['road', 'дорога', 'var(--acc-2)'],
        ]
        return (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 11.5, color: 'var(--mute)', marginBottom: 10 }}>
              Сколько идёт заказ от приёма до гостя — медиана по этапам
            </div>
            {types.map(([k, t]) => (
              <div key={k} style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>{t.title}</span>
                  <span className="tnum" style={{ fontSize: 12.5, color: 'var(--lo)' }}>{t.total} мин</span>
                </div>
                <div style={{ display: 'flex', height: 12, borderRadius: 7, overflow: 'hidden',
                  background: 'var(--glass-2)', marginTop: 6 }}>
                  {SEG.map(([f, , c]) => {
                    const v = t[f] as number
                    return v > 0 ? <div key={f as string} title={`${f as string}: ${v} мин`}
                      style={{ width: `${(v / max) * 100}%`, background: c }} /> : null
                  })}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 5, fontSize: 11 , color: 'var(--mute)' }}>
                  {SEG.map(([f, label, c]) => {
                    const v = t[f] as number
                    return v > 0 ? (
                      <span key={f as string} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                        <i style={{ width: 8, height: 8, borderRadius: 2, background: c, display: 'inline-block' }} />
                        {label} {v} мин
                      </span>
                    ) : null
                  })}
                  <span style={{ marginLeft: 'auto' }}>
                    {t.measured === t.orders
                      ? `${t.orders} заказов`
                      : `посчитано по ${t.measured} из ${t.orders} — у остальных нет отметки «приготовлено»`}
                  </span>
                </div>
              </div>
            ))}
            <div style={{ fontSize: 11, color: 'var(--mute)', lineHeight: 1.55 }}>
              Заказы зала сюда не попадают: iiko не фиксирует у них время приготовления —
              чек открывается и закрывается на кассе одним действием. Эти цифры может дать только КДС.
            </div>
          </div>
        )
      })()}

      {data.suspicious > 0 && (
        <div style={{ marginTop: 12, fontSize: 11.5, color: 'var(--amb)' }}>
          {data.suspicious} {data.suspicious === 1 ? 'заказ' : 'заказов'} с опозданием больше двух часов — обычно это
          не курьер, а заказ, который забыли закрыть в iiko. Отмечены значком.
        </div>
      )}

      {list.length === 0 ? (
        <div style={{ marginTop: 14, fontSize: 13, color: 'var(--grn)' }}>Опоздавших доставок за период нет.</div>
      ) : (
        <>
          <div style={{ overflowX: 'auto', marginTop: 16 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
              <thead>
                <tr>
                  <th style={th}>№</th>
                  <th style={th}>Дата</th>
                  <th style={th}>Курьер</th>
                  <th style={th}>Адрес</th>
                  <th style={{ ...th, textAlign: 'right' }}>Обещали</th>
                  <th style={{ ...th, textAlign: 'right' }}>Приехали</th>
                  <th style={{ ...th, textAlign: 'right' }}>Опоздание</th>
                  <th style={th}>Причина</th>
                  <th style={{ ...th, textAlign: 'right' }}>Сумма</th>
                </tr>
              </thead>
              <tbody>
                {list.slice(0, shown).map((o, i) => (
                  <tr key={`${o.number}-${o.date}-${i}`}>
                    <td className="tnum" style={{ ...td, fontWeight: 650 }}>
                      {o.suspicious && <span title="похоже, заказ не закрыли вовремя" style={{ color: 'var(--amb)', marginRight: 5 }}>⚑</span>}
                      {o.number ?? '—'}
                    </td>
                    <td className="tnum" style={{ ...td, color: 'var(--mute)' }}>{o.date.slice(8, 10)}.{o.date.slice(5, 7)}</td>
                    <td style={td}>{o.courier}</td>
                    <td style={{ ...td, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: o.address ? 'var(--lo)' : 'var(--mute)' }} title={o.address}>
                      {o.address || 'адрес не заполнен'}
                      {/* источник заказа: у сайта и приложения адрес приходит другим полем */}
                      {o.source && <span style={{ marginLeft: 7, fontSize: 10.5, color: 'var(--mute)', border: '1px solid var(--hair)', borderRadius: 6, padding: '1px 5px' }}>{o.source}</span>}
                    </td>
                    <td className="tnum" style={{ ...td, textAlign: 'right', color: 'var(--mute)' }}>{o.expected}</td>
                    <td className="tnum" style={{ ...td, textAlign: 'right' }}>{o.actual}</td>
                    <td className="tnum" style={{ ...td, textAlign: 'right', fontWeight: 700, color: o.delay_min >= 30 ? 'var(--red)' : 'var(--amb)' }}>+{o.delay_min} мин</td>
                    <td style={{ ...td, whiteSpace: 'nowrap' }}
                      title={`кухня ${o.stages?.kitchen ?? '—'} мин · ждал курьера ${o.stages?.idle ?? '—'} мин · дорога ${o.stages?.road ?? '—'} мин`}>
                      <span style={{ color: CAUSE_TONE[o.cause?.key] || 'var(--lo)', fontWeight: 600 }}>
                        {CAUSE_SHORT[o.cause?.key] || '—'}
                      </span>
                      {o.cause?.minutes ? <span style={{ color: 'var(--mute)', marginLeft: 6, fontSize: 11.5 }}>+{o.cause.minutes}</span> : null}
                    </td>
                    <td className="tnum" style={{ ...td, textAlign: 'right' }}>{tenge(o.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shown < list.length && (
            <button onClick={() => setShown((s) => s + SHOW_STEP)}
              style={{
                marginTop: 14, font: 'inherit', fontSize: 12.5, fontWeight: 600, color: 'var(--acc)', cursor: 'pointer',
                background: 'color-mix(in srgb,var(--acc) 10%,transparent)',
                border: '1px solid color-mix(in srgb,var(--acc) 26%,transparent)', borderRadius: 11, padding: '8px 14px',
              }}>
              Показать ещё {Math.min(SHOW_STEP, list.length - shown)} из {list.length - shown}
            </button>
          )}
        </>
      )}
    </Glass>
  )
}
