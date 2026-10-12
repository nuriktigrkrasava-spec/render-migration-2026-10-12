import { useApi } from '../lib/useApi'
import { Glass, Chart, Bullet, Insight, PageState as Note } from './ui'
import { donut, hbar, legend, color, bar } from './charts'
import { tenge, num } from '../lib/format'

/* ── Формы данных ── */
type Channel = { name: string; revenue: number; guests: number; avg_check: number; top: boolean }
type PayType = { name: string; revenue: number; account: string }
type Account = { name: string; revenue: number }
type Dish = { name: string; revenue: number; count: number }
type Category = { name: string; value: number; color: string }
type Sales = {
  channels: Channel[]
  pay_types: PayType[]
  accounts: Account[]
  top_dishes: Dish[]
  categories: Category[]
}
type Pair = { a: string; b: string; count: number }
type Companion = { name: string; count: number }
type Companions = { pairs: Pair[]; companions: Record<string, Companion[]> }

/* ── Табличные стили (как в screens.tsx) ── */
const th: React.CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 10px', borderBottom: '1px solid var(--hair)' }
const td: React.CSSProperties = { padding: '11px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }

/* ── Медиана ── */
function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

/* ── Матрица Kasavana–Smith ── */
type Klass = { label: string; c: string; tone: 'acc' | 'warn' | 'bad' }
function classify(d: Dish, medCount: number, medRev: number): Klass {
  const popular = d.count >= medCount
  const profitable = d.revenue >= medRev
  if (popular && profitable) return { label: 'Звезда', c: 'var(--grn)', tone: 'acc' }
  if (popular && !profitable) return { label: 'Рабочая лошадка', c: 'var(--amb)', tone: 'warn' }
  if (!popular && profitable) return { label: 'Загадка', c: 'var(--acc)', tone: 'acc' }
  return { label: 'Собака', c: 'var(--red)', tone: 'bad' }
}
function Badge({ k }: { k: Klass }) {
  return (
    <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.02em', color: k.c, background: `color-mix(in srgb,${k.c} 15%,transparent)`, padding: '4px 9px', borderRadius: 8, whiteSpace: 'nowrap' }}>{k.label}</span>
  )
}

export default function Menu() {
  const sales = useApi<Sales>('sales', { period: 'month' })
  const comp = useApi<Companions>('companions', { period: 'month' })

  if (sales.loading || comp.loading) return <Note loading>Сводим продажи и меню…</Note>
  if (sales.error || !sales.data) return <Note>Не удалось собрать аналитику: {sales.error}</Note>
  const s = sales.data
  const c = comp.data

  /* Итоги для героя */
  const totalRev = s.channels.reduce((a, x) => a + x.revenue, 0)
  const totalGuests = s.channels.reduce((a, x) => a + x.guests, 0)
  const blended = totalGuests ? totalRev / totalGuests : 0
  const topChannel = s.channels.reduce((a, x) => (x.revenue > a.revenue ? x : a), s.channels[0])

  /* Каналы — донат + легенда */
  const chData = s.channels.map((x, i) => ({ l: x.name, v: x.revenue, c: color(i) }))
  const chLegend = s.channels.map((x, i) => ({ l: x.name, v: tenge(x.revenue), c: color(i) }))

  /* Категории — донат со своими цветами */
  const catData = s.categories.map((x) => ({ l: x.name, v: x.value, c: x.color }))
  const catLegend = s.categories.map((x) => ({ l: x.name, v: `${x.value}%`, c: x.color }))

  /* Меню-инжиниринг */
  const medCount = median(s.top_dishes.map((d) => d.count))
  const medRev = median(s.top_dishes.map((d) => d.revenue))
  const maxRev = Math.max(...s.top_dishes.map((d) => d.revenue))
  const dishes = [...s.top_dishes].sort((a, b) => b.revenue - a.revenue)

  /* Market basket */
  const pairData = (c?.pairs ?? []).map((p) => ({ l: `${p.a} + ${p.b}`, v: p.count, c: bar(0) }))
  const heroDishes = c ? Object.keys(c.companions).slice(0, 2) : []
  const strongest = c?.pairs?.[0]
  const DRINK = 700 // средняя цена сопутствующего напитка/допа, ₸
  const upside = strongest ? Math.round(strongest.count * 0.5) * DRINK : 0

  /* Способы оплаты */
  const payData = s.pay_types.map((p) => ({ l: p.name, v: p.revenue, disp: tenge(p.revenue), c: bar(0) }))

  return (
    <>
      {/* 1 · Герой */}
      <Glass span={5} spot warp>
        <div className="eyebrow"><span>Продажи · меню</span><span className="hint">за месяц</span></div>
        <div className="tnum" style={{ fontSize: 46, fontWeight: 680, letterSpacing: '-.035em', lineHeight: 1, marginTop: 16 }}>{tenge(totalRev)}</div>
        <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)', fontWeight: 500 }}>выручка по всем каналам</div>
        <div style={{ display: 'flex', gap: 30, marginTop: 26 }}>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>Гостей</div>
            <div className="tnum" style={{ fontSize: 24, fontWeight: 660, letterSpacing: '-.02em', marginTop: 6 }}>{num(totalGuests)}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>Средний чек</div>
            <div className="tnum" style={{ fontSize: 24, fontWeight: 660, letterSpacing: '-.02em', marginTop: 6 }}>{tenge(blended)}</div>
          </div>
        </div>
        <div style={{ height: 8, borderRadius: 6, marginTop: 24, background: 'linear-gradient(90deg, var(--acc), var(--acc-2))', opacity: 0.92 }} />
      </Glass>

      {/* 2 · Каналы */}
      <Glass span={7}>
        <div className="eyebrow"><span>Каналы продаж</span><span className="hint">лидер · {topChannel.name}</span></div>
        <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
          <div style={{ flex: '0 0 200px' }}><Chart html={donut(chData, { label: (totalRev / 1_000_000).toFixed(1).replace('.', ','), sub: 'млн ₸' })} /></div>
          <div style={{ flex: 1, minWidth: 200 }}><Chart html={legend(chLegend)} style={{ marginTop: 0 }} /></div>
        </div>
      </Glass>

      {/* 3 · Категории меню */}
      <Glass span={5}>
        <div className="eyebrow"><span>Категории меню</span><span className="hint">доля выручки</span></div>
        <Chart html={donut(catData, { label: String(s.categories.length), sub: 'категорий' })} />
        <Chart html={legend(catLegend)} style={{ marginTop: 4 }} />
      </Glass>

      {/* 4 · Меню-инжиниринг (матрица Kasavana–Smith) */}
      <Glass span={7}>
        <div className="eyebrow"><span>Меню-инжиниринг</span><span className="hint">матрица Kasavana–Smith</span></div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr>
            <th style={th}>Блюдо</th>
            <th style={th}>Класс</th>
            <th style={{ ...th, textAlign: 'right' }}>Продано</th>
            <th style={{ ...th, width: '26%' }}>Выручка</th>
            <th style={{ ...th, textAlign: 'right' }}>₸</th>
          </tr></thead>
          <tbody>{dishes.map((d, i) => {
            const k = classify(d, medCount, medRev)
            return (
              <tr key={i}>
                <td style={td}>{d.name}</td>
                <td style={td}><Badge k={k} /></td>
                <td style={tdR}>{num(d.count)}</td>
                <td style={td}><Bullet value={d.revenue} max={maxRev} tone={k.tone} /></td>
                <td style={tdR}>{tenge(d.revenue)}</td>
              </tr>
            )
          })}</tbody>
        </table>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', marginTop: 14, fontSize: 11, color: 'var(--mute)' }}>
          <span>медиана продаж {num(medCount)}</span><span>·</span><span>медиана выручки {tenge(medRev)}</span>
        </div>
      </Glass>

      {/* 5 · С чем берут (market basket) */}
      <Glass span={12}>
        <div className="eyebrow"><span>С чем берут · корзина</span><span className="hint">частые пары и допродажа</span></div>
        {strongest && (
          <div style={{ marginTop: 4 }}>
            <Insight
              tag="Бандл"
              title={`«${strongest.a}» + «${strongest.b}» · ${strongest.count} раз`}
              money={`≈ +${tenge(upside)} / мес`}
              tone="pos"
              detail={`Самая сильная пара месяца. Соберите комбо и предлагайте на кассе. +50% привязки напитка ≈ ${tenge(upside)} в месяц почти без затрат.`}
            />
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 24, marginTop: 8 }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, marginBottom: 4 }}>Топ пар</div>
            <Chart html={hbar(pairData)} style={{ marginTop: 6 }} />
          </div>
          {heroDishes.map((name) => (
            <div key={name}>
              <div style={{ fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, marginBottom: 8 }}>К «{name}» берут</div>
              {(c?.companions[name] ?? []).map((x, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '9px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
                  <i style={{ width: 9, height: 9, borderRadius: 3, background: bar(i), flex: 'none' }} />
                  <span style={{ fontSize: 13, color: 'var(--lo)' }}>{x.name}</span>
                  <span className="tnum" style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 650 }}>{num(x.count)} <span style={{ color: 'var(--mute)', fontWeight: 400, fontSize: 11 }}>зак.</span></span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Glass>

      {/* 6 · Способы оплаты */}
      <Glass span={12}>
        <div className="eyebrow"><span>Способы оплаты</span><span className="hint">выручка за месяц (с 1 числа)</span></div>
        <Chart html={hbar(payData)} />
      </Glass>
    </>
  )
}
