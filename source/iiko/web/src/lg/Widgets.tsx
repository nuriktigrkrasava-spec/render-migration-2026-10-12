import { useApi } from '../lib/useApi'
import { Glass } from './ui'
import { tenge } from '../lib/format'

/* Макеты iOS-виджетов — внутри рамки iPhone, в реальном масштабе телефона.
   Small / Medium / Large. Данные — dashboard + foodcost (реальные), остальное демо. */

type Dash = { revenue_today: number }
type FCT = { fact_pct: number; norm_pct: number }

function Ring({ pct, color, size = 58 }: { pct: number; color: string; size?: number }) {
  const r = size / 2 - 5, C = 2 * Math.PI * r, off = C * (1 - Math.min(100, pct) / 100)
  return (
    <svg width={size} height={size} style={{ transform: 'rotate(-90deg)', flex: 'none' }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(20,26,44,.1)" strokeWidth={5} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={5} strokeLinecap="round" strokeDasharray={C} strokeDashoffset={off} />
    </svg>
  )
}

const Ic = () => <span className="pw-ic">iiko</span>

const WEEK = [
  { d: 'Пн', t: 'Техкарта «Стейк рибай»', eff: '+924 т' },
  { d: 'Вт', t: 'Поставщик мяса', eff: '+2,9 млн' },
  { d: 'Ср', t: 'Скрипт допродаж', eff: '+15 т/д' },
  { d: 'Чт', t: 'Отзывы Wolt', eff: '+693 т' },
  { d: 'Пт', t: 'Запас на выходные', eff: 'пик' },
  { d: 'Сб', t: 'Списания, FIFO', eff: '+171 т' },
  { d: 'Вс', t: 'Итоги недели', eff: '—' },
]

export default function Widgets() {
  const dash = useApi<Dash>('dashboard', { period: 'today' })
  const fc = useApi<FCT>('foodcost', { period: 'month' })
  const rev = dash.data?.revenue_today
  const fcp = fc.data?.fact_pct ?? 36
  const norm = fc.data?.norm_pct ?? 30
  const over = fcp > norm

  return (
    <Glass span={12}>
      <div className="eyebrow"><span>Виджеты для iOS</span><span className="hint">как метрики ресторана лягут на домашний экран iPhone</span></div>
      <div className="phone">
        <div className="phone-screen">
          <div className="phone-time">9:41</div>
          <div className="phone-grid">
            {/* SMALL — выручка */}
            <div className="pw s">
              <div className="pw-top"><Ic />Выручка</div>
              <div className="pw-big" style={{ marginTop: 'auto' }}>{rev ? tenge(rev) : '944 тыс ₸'}</div>
              <div className="pw-sub">сегодня · ▲ 21%</div>
            </div>

            {/* SMALL — фудкост кольцо */}
            <div className="pw s">
              <div className="pw-top"><Ic />Фудкост</div>
              <div style={{ display: 'grid', placeItems: 'center', margin: 'auto', position: 'relative' }}>
                <Ring pct={fcp} color={over ? 'var(--red)' : 'var(--grn)'} />
                <span className="tnum" style={{ position: 'absolute', fontSize: 15, fontWeight: 750, color: over ? 'var(--red)' : 'var(--grn)' }}>{Math.round(fcp)}%</span>
              </div>
              <div className="pw-sub" style={{ textAlign: 'center' }}>норма {norm}%</div>
            </div>

            {/* MEDIUM — зал сейчас */}
            <div className="pw m">
              <div className="pw-top"><Ic />Зал сейчас</div>
              <div style={{ display: 'flex', gap: 18, marginTop: 8 }}>
                {[['19', 'гостей'], ['16', 'заказов'], ['67%', 'загрузка']].map((k) => (
                  <div key={k[1]}><div className="tnum" style={{ fontSize: 20, fontWeight: 750, color: '#141a2c' }}>{k[0]}</div><div style={{ fontSize: 10, color: '#8a90a2' }}>{k[1]}</div></div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 4, marginTop: 'auto', paddingTop: 10 }}>
                {[1, 1, 1, 0, 1, 1, 0, 1, 1].map((o, i) => <span key={i} style={{ flex: 1, height: 7, borderRadius: 3, background: o ? 'var(--grn)' : 'rgba(20,26,44,.08)' }} />)}
              </div>
            </div>

            {/* LARGE — план недели */}
            <div className="pw l">
              <div className="pw-top"><Ic />План недели · по дням</div>
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 2 }}>
                {WEEK.map((d) => (
                  <div key={d.d} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '5px 0', borderTop: '1px solid rgba(20,26,44,.06)' }}>
                    <span style={{ flex: 'none', width: 26, height: 22, borderRadius: 7, display: 'grid', placeItems: 'center', fontSize: 10.5, fontWeight: 750, color: 'var(--acc)', background: 'color-mix(in srgb, var(--acc) 12%, transparent)' }}>{d.d}</span>
                    <span style={{ flex: '1 1 auto', fontSize: 12, color: '#141a2c', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.t}</span>
                    <span className="tnum" style={{ flex: 'none', fontSize: 11.5, fontWeight: 700, color: d.eff.startsWith('+') ? 'var(--grn)' : '#8a90a2' }}>{d.eff}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div style={{ marginTop: 14, fontSize: 11.5, color: 'var(--mute)', textAlign: 'center' }}>Реальные виджеты соберём под WidgetKit по этим данным. План — из приоритетов Директора, разложен по дням.</div>
    </Glass>
  )
}
