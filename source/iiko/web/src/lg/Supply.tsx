import { useState } from 'react'
import { motion } from 'framer-motion'
import { useApi } from '../lib/useApi'
import { Glass, Chart, Delta, Orb, Insight, PageState as Note } from './ui'
import { hbar, bar } from './charts'
import { tenge, num } from '../lib/format'
import InvoicePunch from './InvoicePunch'
import MatchMemory from './MatchMemory'
import PriceSearch from './PriceSearch'

/* ── Формы данных (подтверждены curl) ── */
type StockItem = { name: string; sku: string; balance: number; category: string; status: string }
type Stoplist = { items: StockItem[]; total: number; updated_at: string }
type WoCat = { name: string; written: number; purchases: number; pct: number; alert: boolean }
type Writeoffs = { total_written: number; purchases: number; loss_pct: number; categories: WoCat[] }
type Cancels = { items: { name: string; amount: number }[]; total_amount: number; total_orders: number }
type Withdrawals = { withdrawals: { date: string; sum: number; comment: string }[]; total: number }
type Pos = { name: string; qty: number; unit?: string; price: number; sum: number }
type Invoices = { invoices: { id: string; status: string; supplier: string; date: string; items: number; amount: number; positions?: Pos[] }[] }

/* ── табличные стили (как в screens.tsx) ── */
const th: React.CSSProperties = { fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, textAlign: 'left', padding: '0 0 10px', borderBottom: '1px solid var(--hair)' }
const td: React.CSSProperties = { padding: '11px 0', borderBottom: '1px solid var(--hair-2)', fontSize: 13 }
const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }
function Sub({ children }: { children: React.ReactNode }) { return <div style={{ fontSize: 10, color: 'var(--mute)', marginTop: 2 }}>{children}</div> }
function Eyebrow({ t, hint }: { t: string; hint?: string }) { return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div> }
/* мелкая иконка-искра для флагманского блока */
const Spark = () => <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.9 6.3 6.9.7-5.1 4.7 1.4 6.8L12 17.8 5.9 20.5l1.4-6.8L2.2 9l6.9-.7z" /></svg>
const Arrow = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h13M12 6l6 6-6 6" /></svg>

const stTone = (s: string): 'green' | 'amber' | 'red' => (s === 'stop' ? 'red' : s === 'low' ? 'amber' : 'green')
const stLabel = (s: string) => (s === 'stop' ? 'стоп' : s === 'low' ? 'мало' : 'ок')

/* ── Герой-фура: отсеки борта = поставки по накладным (референс Truck&Co) ─────
   Каждая ячейка кузова = накладная. Наведи — поставщик/дата/кол-во/сумма/статус;
   нажми — раскрывается деталь. Данные реальные из /api/invoices. Построчные
   позиции («что именно») подтянем из детали накладной следующим шагом. */
type Inv = { id: string; status: string; supplier: string; date: string; items: number; amount: number; positions?: Pos[] }
const invTone = (s: string) => (/провед|posted|accept/i.test(s) ? '#37c489' : /нов|обраб|работ|pending|ready|готов/i.test(s) ? '#e9b64a' : '#7c86a1')

function TruckHero({ wo, total, outN, lowN, invoices }: { wo: Writeoffs; total: number; outN: number; lowN: number; invoices: Inv[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const [sel, setSel] = useState<number | null>(null)
  // водитель на кабине — вход в «Поиск цены»: смотрят перед заказом, у кого дешевле
  const [driver, setDriver] = useState(false)
  const [driverHover, setDriverHover] = useState(false)
  const okN = Math.max(0, total - outN - lowN)
  const badges = [
    { k: 'Позиции', v: String(total), c: 'var(--acc)' },
    { k: 'В норме', v: String(okN), c: '#37c489' },
    { k: 'На исходе', v: String(outN + lowN), c: outN ? '#e5484d' : '#e9b64a' },
  ]
  const cells: (Inv | null)[] = Array.from({ length: 12 }, (_, i) => invoices[i] ?? null)
  const active = sel != null ? invoices[sel] : null
  // Отсеков всегда 12, а поставок за неделю бывает и 26 — не делаем вид, что
  // показали всё: подпись говорит, сколько было и сколько влезло в кузов.
  const hint = invoices.length > cells.length
    ? `за последнюю неделю ${invoices.length} накладных · в кузове 12 свежих · нажми для деталей`
    : 'накладные за последнюю неделю · ячейка = поставка · нажми для деталей'

  return (
    <Glass span={12}>
      <Eyebrow t="Склад · борт" hint={hint} />
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
        {badges.map((b) => (
          <div key={b.k} style={{ display: 'inline-flex', alignItems: 'center', gap: 9, padding: '9px 14px', borderRadius: 13, border: '1px solid var(--hair)', background: 'var(--glass-2)' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: b.c, flex: 'none' }} />
            <span style={{ fontSize: 11.5, color: 'var(--mute)', fontWeight: 600 }}>{b.k}</span>
            <span className="tnum" style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-.02em', color: 'var(--hi)' }}>{b.v}</span>
          </div>
        ))}
        <div style={{ display: 'inline-flex', alignItems: 'baseline', gap: 8, padding: '9px 14px', borderRadius: 13, border: '1px solid var(--hair)', background: 'var(--glass-2)', marginLeft: 'auto' }}>
          <span style={{ fontSize: 11.5, color: 'var(--mute)', fontWeight: 600 }}>Списано</span>
          <span className="tnum" style={{ fontSize: 16, fontWeight: 700, color: 'var(--red)' }}>{tenge(wo.total_written)}</span>
          <Delta v={wo.loss_pct} invert size={11} />
        </div>
      </div>

      {/* фотогерой: фура в первозданном виде (студийный фон как есть, уберём позже) + отсеки-накладные поверх кузова */}
      <div style={{ marginTop: 14, maxWidth: 560, margin: '14px auto 0' }}>
        <div style={{ position: 'relative' }}>
          <img src="/truck.png" alt="Фура — поставки по накладным" style={{ width: '100%', display: 'block' }} />
          {/* Водитель в кабине: нажатие открывает поиск лучшего поставщика по товару.
              Координаты — под тот же кадр 1536×700, что и отсеки кузова. */}
          <button
            onClick={() => setDriver((v) => !v)}
            onMouseEnter={() => setDriverHover(true)} onMouseLeave={() => setDriverHover(false)}
            aria-label="Поиск цены: у кого товар дешевле"
            style={{
              position: 'absolute', left: '12%', top: '18%', width: '15%', height: '30%',
              border: driverHover || driver ? '2px solid var(--acc)' : '2px solid transparent',
              borderRadius: 10, background: driverHover ? 'rgba(255,255,255,.10)' : 'transparent',
              cursor: 'pointer', padding: 0, transition: 'background .15s, border-color .15s',
            }}>
            {(driverHover || driver) && (
              <span style={{ position: 'absolute', bottom: 'calc(100% + 8px)', left: '50%', transform: 'translateX(-50%)', whiteSpace: 'nowrap', background: 'var(--card)', border: '1px solid var(--hair)', borderRadius: 11, padding: '8px 12px', fontSize: 12, fontWeight: 650, color: 'var(--hi)', boxShadow: '0 14px 30px -12px rgba(20,40,80,.55)' }}>
                Поиск цены · у кого дешевле
              </span>
            )}
          </button>
          {/* сетка отсеков поверх кузова (координаты под кадр 1536×700) */}
          <div style={{ position: 'absolute', left: '33.5%', top: '14%', width: '59%', height: '35%', display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', gridTemplateRows: 'repeat(2,1fr)', gap: '5%' }}>
            {cells.map((inv, i) => {
              const on = inv != null
              const tone = on ? invTone(inv!.status) : '#9098b0'
              const isSel = sel === i
              const cellStyle: React.CSSProperties = on
                ? { background: tone, boxShadow: isSel ? `0 0 0 2px #fff, 0 0 0 4px ${tone}` : '0 2px 8px -3px rgba(20,40,80,.5)', cursor: 'pointer' }
                : { background: 'rgba(120,132,156,.10)', border: '1px solid rgba(120,132,156,.28)' }
              return (
                <div key={i}
                  onMouseEnter={() => on && setHover(i)} onMouseLeave={() => setHover(null)}
                  onClick={() => on && setSel(isSel ? null : i)}
                  style={{ position: 'relative', borderRadius: 5, transition: 'transform .15s', transform: hover === i ? 'translateY(-2px)' : 'none', ...cellStyle }}>
                  {hover === i && inv && (
                    <div style={{ position: 'absolute', bottom: 'calc(100% + 9px)', left: '50%', transform: 'translateX(-50%)', whiteSpace: 'nowrap', zIndex: 6, background: 'var(--card)', border: '1px solid var(--hair)', borderRadius: 11, padding: '9px 12px', boxShadow: '0 14px 30px -12px rgba(20,40,80,.55)', textAlign: 'left' }}>
                      <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--hi)' }}>{inv.supplier}</div>
                      <div className="tnum" style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 3 }}>{inv.items} поз. · {tenge(inv.amount)} · {inv.date}</div>
                      <div style={{ fontSize: 10.5, color: invTone(inv.status), fontWeight: 700, marginTop: 3 }}>{inv.status}</div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Пустой кузов сам по себе читается как поломка — говорим прямо, что
          за неделю поставок не было, а не оставляем 12 серых ячеек молчать. */}
      {!invoices.length && (
        <div style={{ marginTop: 10, textAlign: 'center', fontSize: 12.5, color: 'var(--mute)' }}>
          За последнюю неделю накладных не было
        </div>
      )}

      {driver && <PriceSearch onClose={() => setDriver(false)} />}

      {/* «Пробить накладную»: фото → распознавание → наше автосопоставление */}
      <InvoicePunch />

      {/* Справочник соответствий — чем живёт автосопоставление. Стоит сразу под
          пробивкой: связка появляется здесь ровно тогда, когда оператор выбрал
          товар выше, и искать её в другом разделе неоткуда. */}
      <MatchMemory />

      {/* раскрытие: деталь накладной по клику */}
      {active && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          style={{ marginTop: 14, border: '1px solid var(--hair)', borderRadius: 16, padding: '16px 18px', background: 'var(--glass-2)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 680, color: 'var(--hi)' }}>{active.supplier}</div>
              <div style={{ fontSize: 11.5, color: 'var(--mute)', marginTop: 3 }}>Накладная №{active.id} · {active.date} · <span style={{ color: invTone(active.status), fontWeight: 700 }}>{active.status}</span></div>
            </div>
            <button onClick={() => setSel(null)} aria-label="Закрыть"
              style={{ width: 30, height: 30, borderRadius: '50%', border: '1px solid var(--hair)', background: 'var(--glass-2)', color: 'var(--lo)', cursor: 'pointer', fontSize: 15, lineHeight: 1 }}>×</button>
          </div>
          <div style={{ display: 'flex', gap: 26, marginTop: 14, flexWrap: 'wrap' }}>
            <div><div className="tnum" style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-.02em', color: 'var(--hi)' }}>{tenge(active.amount)}</div><Sub>сумма поставки</Sub></div>
            <div><div className="tnum" style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-.02em', color: 'var(--hi)' }}>{active.items}</div><Sub>позиций в накладной</Sub></div>
          </div>
          {active.positions && active.positions.length > 0 ? (
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--hair)' }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}><span>Что пришло</span><span className="hint">{active.positions.length} позиций · товар × кол-во × цена</span></div>
              {active.positions.map((p, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 12, padding: '10px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
                  <span style={{ flex: '1 1 auto', fontSize: 13.5, color: 'var(--hi)', fontWeight: 550 }}>{p.name}</span>
                  <span className="tnum" style={{ flex: 'none', fontSize: 12.5, color: 'var(--mute)' }}>{num(p.qty)} {p.unit} × {tenge(p.price)}</span>
                  <span className="tnum" style={{ flex: 'none', width: 108, textAlign: 'right', fontSize: 13.5, fontWeight: 680, color: 'var(--hi)' }}>{tenge(p.sum)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--hair)', fontSize: 12, color: 'var(--mute)' }}>Построчные позиции появятся с деталью накладной.</div>
          )}
        </motion.div>
      )}
    </Glass>
  )
}

export default function Supply() {
  const stock = useApi<Stoplist>('stoplist', { period: 'month' })
  const wo = useApi<Writeoffs>('writeoffs', { period: 'month' })
  const cn = useApi<Cancels>('cancels', { period: 'month' })
  const wd = useApi<Withdrawals>('withdrawals', { period: 'month' })
  const inv = useApi<Invoices>('invoices', { period: 'month' })
  /* Фура показывает ПОСЛЕДНЮЮ НЕДЕЛЮ: в кузове 12 отсеков, и за месяц свежие
     поставки вытеснялись старыми — борт переставал отвечать на вопрос «что
     приехало». Отдельный запрос, а не фильтр по месячному: «Закуп по
     поставщикам» ниже считается за месяц и терять его окно нельзя. */
  const invWeek = useApi<Invoices>('invoices', { period: 'week' })

  if (stock.loading || wo.loading || cn.loading || wd.loading || inv.loading || invWeek.loading) return <Note loading>Сводим склад и закупки…</Note>
  if (!stock.data || !wo.data || !cn.data || !wd.data) return <Note>Не удалось собрать данные склада: {stock.error || wo.error || cn.error || wd.error}</Note>

  const items = stock.data.items
  const outN = items.filter((i) => i.status === 'stop').length
  const lowN = items.filter((i) => i.status === 'low').length
  // стоп-лист компактный: сначала стоп, потом мало, топ-8 (карта не на пол-экрана)
  const stRank = (s: string) => (s === 'stop' ? 0 : s === 'low' ? 1 : 2)
  const itemsTop = [...items].sort((a, b) => stRank(a.status) - stRank(b.status)).slice(0, 8)

  /* ⭐ авто-заявка: строки на дозаказ из позиций low/stop.
     Нет данных о расходе/сроке поставки → правило «довести до пар-уровня». */
  const need = items.filter((i) => i.status === 'stop' || i.status === 'low')
  const reorder = need.map((i) => {
    const par = i.status === 'stop' ? 12 : 8 // целевой остаток (пар-уровень), эвристика
    return { ...i, par, qty: Math.max(1, par - i.balance) }
  })
  /* стоп-позиции повторяются в отменах → упущенная выручка из-за стоп-листа */
  const stopNames = new Set(need.map((n) => n.name))
  const lost = cn.data.items.filter((c) => stopNames.has(c.name)).reduce((s, c) => s + c.amount, 0)

  /* списания по категориям → % от закупа, alert красным, худшая по сумме */
  const cats = [...wo.data.categories].sort((a, b) => b.pct - a.pct)
  const woBars = cats.map((c) => ({ l: c.name, v: c.pct, c: c.alert ? 'var(--red)' : bar(0), sub: tenge(c.written) }))
  const worst = [...wo.data.categories].sort((a, b) => b.written - a.written)[0]

  /* отмены */
  const cnBars = cn.data.items.map((c) => ({ l: c.name, v: c.amount, c: bar(0) }))

  /* закуп по поставщикам (из накладных) — реальная агрегация вместо price-drift */
  const invs = inv.data?.invoices ?? []
  const bySup: Record<string, number> = {}
  invs.forEach((v) => { bySup[v.supplier] = (bySup[v.supplier] || 0) + v.amount })
  const supRows = Object.entries(bySup).map(([l, v]) => ({ l, v, c: bar(0) })).sort((a, b) => b.v - a.v)
  const supTotal = supRows.reduce((s, r) => s + r.v, 0)

  return (
    <>
      {/* 0 · Герой-фура — отсеки = поставки по накладным (референс Truck&Co) */}
      <TruckHero wo={wo.data} total={stock.data.total} outN={outN} lowN={lowN} invoices={invWeek.data?.invoices ?? []} />

      {/* 1 · Герой — списания + доля + остатки */}
      <Glass span={5} spot>
        <Eyebrow t="Склад · закупки" hint={`обновлено ${stock.data.updated_at}`} />
        <div className="tnum" style={{ fontSize: 44, fontWeight: 680, letterSpacing: '-.035em', lineHeight: 1, marginTop: 16, display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          {tenge(wo.data.total_written)} <Delta v={wo.data.loss_pct} invert />
        </div>
        <div style={{ marginTop: 9, fontSize: 12.5, color: 'var(--mute)' }}>списано за месяц · {wo.data.loss_pct.toFixed(1).replace('.', ',')}% от закупа {tenge(wo.data.purchases)}</div>
        <p style={{ marginTop: 14, fontSize: 13, lineHeight: 1.5, color: 'var(--lo)', maxWidth: '46ch' }}>
          Каждый шестой тенге закупа уходит в списание. {outN} позиции в стоп-листе — упущенная выручка.
        </p>
        <div style={{ display: 'flex', gap: 22, marginTop: 18, flexWrap: 'wrap' }}>
          <div><div className="tnum" style={{ fontSize: 22, fontWeight: 680 }}>{stock.data.total}</div><Sub>позиций на контроле</Sub></div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Orb tone="red" /><div><div className="tnum" style={{ fontSize: 22, fontWeight: 680, color: 'var(--red)' }}>{outN}</div><Sub>стоп (0 в остатке)</Sub></div></div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Orb tone="amber" /><div><div className="tnum" style={{ fontSize: 22, fontWeight: 680, color: 'var(--amb)' }}>{lowN}</div><Sub>заканчивается</Sub></div></div>
        </div>
      </Glass>

      {/* 2 · ⭐ ФЛАГМАН — авто-заявка на поставку */}
      <Glass span={7} spot warp>
        <div className="eyebrow">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><span style={{ color: 'var(--acc)', display: 'inline-flex' }}><Spark /></span>Авто-заявка на поставку</span>
          <span className="hint">{reorder.length} позиций · до пар-уровня</span>
        </div>
        <p style={{ margin: '12px 0 0', fontSize: 12.5, lineHeight: 1.5, color: 'var(--lo)', maxWidth: '46ch' }}>
          Данных о расходе пока нет — дозаказ по правилу <span style={{ color: 'var(--hi)' }}>«довести до пар-уровня»</span> (целевого остатка).
        </p>
        <div style={{ marginTop: 14, border: '1px solid var(--hair)', borderRadius: 16, overflow: 'hidden', background: 'var(--glass-2)' }}>
          {reorder.map((r, i) => (
            <div key={r.sku} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 15px', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
              <Orb tone={stTone(r.status)} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--hi)' }}>{r.name}</div>
                <Sub>{r.category} · остаток {num(r.balance)} → цель {r.par}</Sub>
              </div>
              <div style={{ fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase', fontWeight: 700, color: stTone(r.status) === 'red' ? 'var(--red)' : 'var(--amb)' }}>{stLabel(r.status)}</div>
              <div style={{ textAlign: 'right', minWidth: 78 }}>
                <div className="tnum" style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-.02em', color: 'var(--acc)' }}>+{num(r.qty)}</div>
                <Sub>к заказу, ед.</Sub>
              </div>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14, marginTop: 16, flexWrap: 'wrap' }}>
          <div style={{ fontSize: 12, color: 'var(--lo)' }}>
            Упущено на отменах стоп-блюд: <span className="tnum" style={{ color: '#ff8f6b', fontWeight: 700 }}>{tenge(lost)}</span>
          </div>
          <button
            onClick={(e) => e.preventDefault()}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 650, color: 'var(--on-acc,#fff)', border: 'none', borderRadius: 12, padding: '11px 18px', cursor: 'pointer', background: 'linear-gradient(135deg,var(--acc),var(--acc-2,#7b5cff))', boxShadow: '0 8px 22px -8px var(--acc-glow,rgba(79,110,247,.6))' }}>
            Сформировать заявку <Arrow />
          </button>
        </div>
      </Glass>

      {/* 3 · Стоп-лист / остатки — компактно (топ-8) */}
      <Glass span={5}>
        <Eyebrow t="Стоп-лист · остатки" hint="красный = 0 в остатке" />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
          <thead><tr><th style={th}>Позиция</th><th style={{ ...th, textAlign: 'right' }}>Остаток</th><th style={{ ...th, textAlign: 'right' }}>Статус</th></tr></thead>
          <tbody>{itemsTop.map((it) => (
            <tr key={it.sku}>
              <td style={td}>{it.name}<Sub>{it.category}</Sub></td>
              <td style={{ ...tdR, color: it.status === 'stop' ? 'var(--red)' : it.status === 'low' ? 'var(--amb)' : 'var(--hi)' }}>{num(it.balance)}</td>
              <td style={td}><div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end' }}><span style={{ fontSize: 12, color: 'var(--lo)' }}>{stLabel(it.status)}</span><Orb tone={stTone(it.status)} /></div></td>
            </tr>
          ))}</tbody>
        </table>
        {items.length > itemsTop.length && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--mute)' }}>+ ещё {items.length - itemsTop.length} на контроле</div>}
      </Glass>

      {/* 4 · Списания по категориям */}
      <Glass span={5}>
        <Eyebrow t="Списания по категориям" hint="% от закупа · красный — сигнал" />
        <Chart html={hbar(woBars, '%')} />
        {worst && <div style={{ marginTop: 8 }}>
          <Insight tag="списания" title={`${worst.name} — крупнейшая утечка`} money={tenge(worst.written)} tone="neg"
            detail={`${worst.pct.toFixed(1).replace('.', ',')}% от закупа (${tenge(worst.purchases)}). Проверить сроки, порции, заготовку — цель ниже 10%.`} />
        </div>}
      </Glass>

      {/* 5 · Отмены */}
      <Glass span={6}>
        <Eyebrow t="Отмены заказов" hint={`${cn.data.total_orders} отмен · ${tenge(cn.data.total_amount)}`} />
        <Chart html={hbar(cnBars, ' ₸')} />
      </Glass>

      {/* 6 · Изъятия из кассы */}
      <Glass span={6}>
        <Eyebrow t="Изъятия из кассы" hint={`итого ${tenge(wd.data.total)}`} />
        <div style={{ marginTop: 8 }}>
          {wd.data.withdrawals.map((w, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13.5, color: 'var(--hi)' }}>{w.comment}</div>
                <Sub>{w.date}</Sub>
              </div>
              <span className="tnum" style={{ fontSize: 15, fontWeight: 650 }}>{tenge(w.sum)}</span>
            </div>
          ))}
        </div>
      </Glass>

      {/* 7 · Закуп по поставщикам (из накладных) */}
      {supRows.length > 0 && (
        <Glass span={12}>
          <Eyebrow t="Закуп по поставщикам" hint={`${invs.length} накладных · ${tenge(supTotal)}`} />
          <Chart html={hbar(supRows, ' ₸')} />
        </Glass>
      )}
    </>
  )
}
