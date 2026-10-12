import { useState } from 'react'
import { motion } from 'framer-motion'
import { Glass, Delta } from './ui'
import { useApi } from '../lib/useApi'
import { isDemoData } from '../lib/api'
import { tenge } from '../lib/format'

/* ───────────────────────────────────────────────────────────────
   Главная — «двойник ресторана».
   Референсы: Truck&Co (крупный герой + KPI-бейджи с дельтой + панель +
   карусель) и Smart Home Security (наклонный 3D-план этажа с подсветкой
   зон + пины + лента активности). Свет — главная тема.

   Данные: KPI из /api/dashboard (реальные). План зала — демо-модель до
   подключения iiko Cloud order/by_table + available_restaurant_sections
   (посадка берётся напрямую из iiko). Цвет только на деньгах/занятости.
   ─────────────────────────────────────────────────────────────── */

type St = 'occ' | 'free' | 'res' | 'clean'
type Zone = 'hall' | 'terrace'
type Table = { id: string; zone: Zone; x: number; y: number; w: number; h: number; seats: number; st: St; guests?: number; t?: string }

/* координаты и размер в % площадки; зал слева-широкий, терраса компактнее */
const TABLES: Table[] = [
  { id: '1', zone: 'hall', x: 14, y: 20, w: 13, h: 15, seats: 2, st: 'occ', guests: 2, t: '1:05' },
  { id: '2', zone: 'hall', x: 36, y: 17, w: 16, h: 15, seats: 4, st: 'occ', guests: 3, t: '0:40' },
  { id: '3', zone: 'hall', x: 60, y: 19, w: 16, h: 15, seats: 4, st: 'res', t: '19:30' },
  { id: '4', zone: 'hall', x: 14, y: 48, w: 16, h: 16, seats: 4, st: 'occ', guests: 4, t: '1:50' },
  { id: '5', zone: 'hall', x: 38, y: 46, w: 19, h: 18, seats: 6, st: 'occ', guests: 5, t: '0:25' },
  { id: '6', zone: 'hall', x: 63, y: 48, w: 12, h: 14, seats: 2, st: 'free' },
  { id: '7', zone: 'hall', x: 14, y: 76, w: 12, h: 14, seats: 2, st: 'occ', guests: 2, t: '2:10' },
  { id: '8', zone: 'hall', x: 36, y: 77, w: 16, h: 15, seats: 4, st: 'clean' },
  { id: '9', zone: 'hall', x: 60, y: 77, w: 16, h: 15, seats: 4, st: 'occ', guests: 3, t: '0:55' },
  { id: 'T1', zone: 'terrace', x: 24, y: 24, w: 18, h: 18, seats: 2, st: 'occ', guests: 2, t: '0:30' },
  { id: 'T2', zone: 'terrace', x: 60, y: 24, w: 18, h: 18, seats: 4, st: 'res', t: '20:00' },
  { id: 'T3', zone: 'terrace', x: 24, y: 58, w: 22, h: 20, seats: 6, st: 'occ', guests: 6, t: '1:15' },
  { id: 'T4', zone: 'terrace', x: 62, y: 60, w: 20, h: 18, seats: 4, st: 'free' },
]

/* мягкий мятный для занятых — как active-зона в референсе Smart Home */
const MINT = '#37c489'
const STATE: Record<St, { fill: string; edge: string; label: string }> = {
  occ: { fill: MINT, edge: MINT, label: 'Занят' },
  res: { fill: 'var(--amb)', edge: 'var(--amb)', label: 'Бронь' },
  free: { fill: 'transparent', edge: 'var(--hair)', label: 'Свободен' },
  clean: { fill: '#aab2c4', edge: '#aab2c4', label: 'Убирают' },
}

type Dash = { revenue_today: number; avg_check: number; avg_check_change: number; guests: number }
type HallTable = { id: string; section: string; number?: number; seats: number; x: number | null; y: number | null; w: number | null; h: number | null; sw?: number; sh?: number; busy: boolean; reserved?: boolean; guests: number }
type Hall = { connected: boolean; branch?: string; tables?: HallTable[]; sections?: string[]; has_reservations?: boolean; reserved?: number; channels?: { name: string; count: number }[]; guests?: number; open_orders?: number; load?: number }
type Sales = { channels: { name: string; revenue: number; orders: number }[] }

/* Каналы → 4 типа заказов, те же, что в «Плане» и в карточке «Каналы продаж».
   /api/sales уже отдаёт их сгруппированными (_classify_channel на бэкенде), но
   правило повторено здесь на случай сырых названий: демо-данные и старые
   закэшированные ответы приходят с «Wolt» и «Сайт доставка» вместо групп.
   Порядок проверок важен: сначала агрегаторы (по бренду), затем самовывоз —
   «Сайт самовывоз» это вынос, а не доставка. */
type ChTag = 'hall' | 'pickup' | 'delivery' | 'agg'
function classifyChannel(name: string): ChTag {
  const s = name.toLowerCase()
  if (/агрегатор|wolt|волт|glovo|глово|яндекс|yandex|choco|чоко/.test(s)) return 'agg'
  if (/самовывоз|вынос/.test(s)) return 'pickup'
  if (/достав/.test(s)) return 'delivery'
  return 'hall' // «Зал», «Обычный заказ» и всё, что съели на месте
}
const TYPE_META: { key: ChTag; name: string; c: string }[] = [
  { key: 'hall', name: 'Зал', c: 'var(--grn)' },
  { key: 'delivery', name: 'Доставка', c: 'var(--acc)' },
  { key: 'pickup', name: 'Самовывоз', c: 'var(--amb)' },
  { key: 'agg', name: 'Агрегаторы', c: '#7b5cff' },
]

/* один стол на наклонном 3D-полу — объём через «толщину» (box-shadow) + мягкое свечение */
function TableTop({ t }: { t: Table }) {
  const occ = t.st === 'occ'
  const top = occ
    ? 'linear-gradient(150deg, #6bdcab, #2eb87b)'
    : t.st === 'clean'
      ? 'linear-gradient(150deg, #c6ccd8, #a6aec1)'
      : t.st === 'res'
        ? 'linear-gradient(150deg, color-mix(in srgb, var(--amb) 20%, #fff), color-mix(in srgb, var(--amb) 8%, #fff))'
        : 'linear-gradient(150deg, rgba(255,255,255,.96), rgba(236,240,248,.86))'
  const side = occ ? '#1e9a63' : t.st === 'clean' ? '#8b93a7' : t.st === 'res' ? 'color-mix(in srgb, var(--amb) 40%, #d8b784)' : 'rgba(172,182,203,.75)'
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }}
      transition={{ type: 'spring', stiffness: 240, damping: 22, delay: 0.05 }}
      style={{
        position: 'absolute', left: `${t.x}%`, top: `${t.y}%`, width: `${t.w}%`, height: `${t.h}%`,
        borderRadius: t.seats <= 2 ? '50%' : 13, transformStyle: 'preserve-3d', background: top,
        border: t.st === 'res' ? '1.5px solid color-mix(in srgb, var(--amb) 55%, transparent)' : t.st === 'free' ? '1.5px solid var(--hair)' : 'none',
        boxShadow: `0 9px 0 -1px ${side}, 0 24px 30px -14px rgba(20,40,80,.42)`,
      }}
    >
      {/* блик на верхней грани — «материал» */}
      <span aria-hidden style={{ position: 'absolute', left: 0, right: 0, top: 0, height: '46%', borderRadius: 'inherit', background: 'linear-gradient(180deg, rgba(255,255,255,.35), transparent)', pointerEvents: 'none' }} />
      {occ && (
        <span aria-hidden style={{ position: 'absolute', inset: -10, borderRadius: 'inherit', background: MINT, filter: 'blur(12px)', opacity: 0.3, zIndex: -1 }} />
      )}
    </motion.div>
  )
}

/* пин над столом — контр-наклонён, чтобы смотреть в камеру */
function Pin({ t }: { t: Table }) {
  if (t.st !== 'occ' && t.st !== 'res') return null
  const c = t.st === 'occ' ? 'var(--grn)' : 'var(--amb)'
  const main = t.st === 'occ' ? `${t.guests} гост.` : 'бронь'
  return (
    <div style={{ position: 'absolute', left: `${t.x + t.w / 2}%`, top: `${t.y}%`, transform: 'translate(-50%,-100%)', transformStyle: 'preserve-3d' }}>
      <div style={{ transform: 'translateY(-30px) rotateX(-34deg)', transformOrigin: 'bottom center' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap',
          background: 'rgba(255,255,255,.96)', border: '1px solid var(--hair)', borderRadius: 10,
          padding: '5px 9px', boxShadow: '0 10px 22px -10px rgba(30,45,90,.4)',
        }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: c, boxShadow: `0 0 7px ${c}`, flex: 'none' }} />
          <span className="tnum" style={{ fontSize: 11, fontWeight: 700, color: 'var(--hi)' }}>№{t.id}</span>
          <span style={{ fontSize: 10.5, color: 'var(--mute)' }}>{main}{t.t ? ` · ${t.t}` : ''}</span>
        </div>
        <span aria-hidden style={{ display: 'block', width: 1.5, height: 24, margin: '0 auto', background: `linear-gradient(${c}, transparent)` }} />
      </div>
    </div>
  )
}

const feed = [
  { c: 'var(--grn)', k: 'Зал · стол 5', v: '+2 гостя', t: '19:24' },
  { c: '#7b5cff', k: 'Wolt · заказ №142', v: '4 300 ₸', t: '19:22' },
  { c: 'var(--amb)', k: 'Зал · стол 3', v: 'бронь 19:30', t: '19:18' },
  { c: 'var(--acc)', k: 'Доставка · №141', v: '6 150 ₸', t: '19:11' },
  { c: '#ff7a1a', k: 'Glovo · заказ №140', v: '3 480 ₸', t: '19:04' },
  { c: 'var(--grn)', k: 'Зал · стол 9', v: '+3 гостя', t: '18:57' },
]

const channels = [
  { name: 'Зал', n: 24, d: 8, c: 'var(--grn)' },
  { name: 'Доставка', n: 17, d: -4, c: 'var(--acc)' },
  { name: 'Wolt', n: 12, d: 15, c: '#7b5cff' },
  { name: 'Glovo', n: 9, d: 6, c: '#ff7a1a' },
  { name: 'Вынос', n: 5, d: -2, c: 'var(--mute)' },
]

export default function Home() {
  const dash = useApi<Dash>('dashboard', { period: 'today' })
  const hall = useApi<Hall>('hall')
  const sales = useApi<Sales>('sales', { period: 'today' })
  const [zone, setZone] = useState<Zone>('hall')
  const [section, setSection] = useState<string | null>(null)

  const H = hall.data
  const demoMode = isDemoData()
  const real = !demoMode && !!(H?.connected && H.tables && H.tables.length)
  const hasRes = real && !!H!.has_reservations
  // реальный режим, но зал ещё грузится / не подключён — показываем загрузку, НЕ демо-зал
  const hallPending = !demoMode && !real

  // Секции (залы) филиала рисуем ПО ОДНОЙ — у каждой своя схема и масштаб,
  // совмещать их в одной плоскости нельзя (столы разных залов накладывались).
  const sections = real ? (H!.sections?.length ? H!.sections : [...new Set(H!.tables!.map((t) => t.section))]) : []
  const curSection = real ? (section && sections.includes(section) ? section : sections[0]) : null
  const secTables = real ? H!.tables!.filter((t) => t.section === curSection) : []

  // единый масштаб выбранной секции (у её столов sw/sh одинаковы)
  const scaled = secTables.find((t) => t.x != null && t.y != null)
  const secW = scaled?.sw || 810, secH = scaled?.sh || 550
  // Столы делятся на два разных случая, и мешать их на одном полу нельзя:
  // с координатами — настоящая расстановка из iiko; без координат — стол заведён,
  // но на схему не поставлен. Раньше вторые сваливались в один ряд поверх плана
  // и наезжали друг на друга — отсюда ощущение, что зал «плывёт».
  const placed = secTables.filter((t) => t.x != null && t.y != null)
  const unplaced = secTables.filter((t) => t.x == null || t.y == null)
  const stOf = (t: HallTable): St => (t.busy ? 'occ' : (hasRes && t.reserved ? 'res' : 'free'))
  const realTables: Table[] = placed.map((t, i) => ({
    id: String(t.number ?? i + 1), zone: 'hall' as Zone,
    x: (t.x! / secW) * 100,
    y: (t.y! / secH) * 100,
    w: t.w ? (t.w / secW) * 100 : 9,
    h: t.h ? (t.h / secH) * 100 : 9,
    seats: t.seats || 2,
    st: stOf(t),
    guests: t.guests,
  }))
  // список «не на схеме» — отдельным аккуратным рядом под планом
  const offPlan = unplaced.map((t, i) => ({
    id: String(t.number ?? i + 1), seats: t.seats || 0, st: stOf(t), guests: t.guests,
  }))

  const shown = demoMode ? TABLES.filter((t) => t.zone === zone) : realTables
  const occ = shown.filter((t) => t.st === 'occ')
  // итоги по всей секции (схема + столы вне схемы)
  const tablesTotal = demoMode ? shown.length : secTables.length
  const occTotal = demoMode ? occ.length : secTables.filter((t) => t.busy).length
  const secGuests = occ.reduce((s, t) => s + (t.guests || 0), 0)
  const guestsNow = demoMode ? secGuests : (real ? (H!.guests || 0) : 0)
  // hall-KPI: демо → демо-числа; LIVE → реальные; загрузка → «—» (не демо!)
  const guestsV: number | null = demoMode ? secGuests : (real ? (H!.guests || 0) : null)
  const loadV: number | null = demoMode ? Math.round((occ.length / (shown.length || 1)) * 100) : (real ? (H!.load || 0) : null)
  const ordersV: number | null = demoMode ? TABLES.filter((t) => t.st === 'occ').length + 8 : (real ? (H!.open_orders || 0) : null)
  const resNow = TABLES.filter((t) => t.st === 'res').length
  const nn = (n: number | null) => (n == null ? '—' : String(n))

  const kpi: { k: string; v: string; d?: number; invert?: boolean }[] = [
    { k: 'Гости сейчас', v: nn(guestsV), d: demoMode ? 12 : undefined },
    { k: 'Открытые заказы', v: nn(ordersV), d: demoMode ? 5 : undefined },
    { k: 'Загрузка зала', v: loadV == null ? '—' : `${loadV}%`, d: demoMode ? 8 : undefined },
    // без дельты: рядом с выручкой стоял avg_check_change, который бэк отдаёт
    // захардкоженным нулём — вечное «0,0%» ни о чём не говорило
    { k: 'Выручка сегодня', v: dash.data ? tenge(dash.data.revenue_today) : '—' },
    { k: 'Оборот стола', v: demoMode ? '1 ч 18 м' : '—' },
    // «Лист ожидания» убран. «Бронь на вечер» — только если у филиала подключён модуль броней.
    ...(demoMode ? [{ k: 'Бронь на вечер', v: String(resNow) }] : hasRes ? [{ k: 'Бронь на вечер', v: String(H!.reserved ?? 0) }] : []),
  ]

  // Лента активности: демо → демо-события; LIVE → реальные занятые столы; загрузка → пусто.
  const liveFeed = demoMode ? feed
    : (real ? H!.tables!.filter((t) => t.busy).slice(0, 6).map((t) => ({
        c: 'var(--grn)', k: `${t.section} · стол ${t.number ?? ''}`.trim(), v: `${t.guests || 0} гост.`, t: '',
      })) : [])

  // Заказы по типам за день из /api/sales (live-зал давал ~0 вне часа-пик,
  // т.к. это открытые заказы, а не итоги дня). Демо — прежние демо-каналы.
  const salesCh = sales.data?.channels || []
  const grouped: Record<ChTag, { o: number; r: number }> = {
    hall: { o: 0, r: 0 }, pickup: { o: 0, r: 0 }, delivery: { o: 0, r: 0 }, agg: { o: 0, r: 0 },
  }
  for (const c of salesCh) {
    const g = grouped[classifyChannel(c.name)]
    g.o += c.orders || 0
    g.r += c.revenue || 0
  }
  const chList: { name: string; n: number; d?: number; rev?: number; c: string }[] = demoMode
    ? channels
    : TYPE_META.map((m) => ({ name: m.name, n: grouped[m.key].o, rev: grouped[m.key].r, c: m.c }))

  return (
    <>
      {/* Заказы по типам — наверху: это цифры дня, ради которых открывают экран.
          Плитки «Гости сейчас / Открытые заказы / Загрузка зала» ушли вниз —
          они живут только в час-пик, а в остальное время это ряд нулей и прочерков. */}
      <Glass span={12}>
        <div className="eyebrow"><span>Заказы по типам · сегодня</span><span className="hint">Зал · Доставка · Самовывоз · Агрегаторы</span></div>
        <div style={{ display: 'flex', gap: 12, marginTop: 14, overflowX: 'auto', paddingBottom: 4 }}>
          {chList.map((ch) => (
            <div key={ch.name} style={{
              flex: '1 0 160px', minWidth: 160, borderRadius: 16, padding: '14px 16px',
              border: '1px solid var(--hair)', background: 'var(--glass-2)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 9, height: 9, borderRadius: 3, background: ch.c, flex: 'none' }} />
                <span style={{ fontSize: 12.5, color: 'var(--lo)', fontWeight: 600 }}>{ch.name}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, marginTop: 10 }}>
                <span className="tnum" style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-.02em', color: 'var(--hi)' }}>{ch.n}</span>
                <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>заказов</span>
                {ch.d != null && <Delta v={ch.d} size={11} />}
              </div>
              {ch.rev != null && (
                <div className="tnum" style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--hi)', marginTop: 6 }}>{tenge(ch.rev)}</div>
              )}
            </div>
          ))}
        </div>
      </Glass>

      {/* ГЕРОЙ — наклонный 3D-план зала */}
      <Glass span={8} style={{ overflow: 'hidden' }}>
        <div className="eyebrow">
          <span>{real ? `${H!.branch || 'Зал'} · сейчас` : 'Зал · сейчас'} {real
            ? <span className="lg-demo-chip" style={{ color: 'var(--grn)', background: 'color-mix(in srgb,var(--grn) 14%,transparent)', borderColor: 'color-mix(in srgb,var(--grn) 40%,transparent)' }}>LIVE · iiko</span>
            : demoMode
              ? <span className="lg-demo-chip">демо · живой зал скоро</span>
              : <span className="lg-demo-chip">{hall.loading ? 'загружаю зал…' : 'зал не подключён'}</span>}</span>
          {real ? (sections.length > 1 && (
            <div className="lg-seg" role="tablist">
              {sections.map((s) => (
                <button key={s} onClick={() => setSection(s)} className={`lg-per ${curSection === s ? 'on' : ''}`}>
                  {curSection === s && <motion.span layoutId="zone" className="lg-seg-pill" transition={{ type: 'spring', stiffness: 320, damping: 30 }} />}
                  <span style={{ position: 'relative', zIndex: 1 }}>{s}</span>
                </button>
              ))}
            </div>
          )) : demoMode ? (
            <div className="lg-seg" role="tablist">
              {(['hall', 'terrace'] as Zone[]).map((z) => (
                <button key={z} onClick={() => setZone(z)} className={`lg-per ${zone === z ? 'on' : ''}`}>
                  {zone === z && <motion.span layoutId="zone" className="lg-seg-pill" transition={{ type: 'spring', stiffness: 320, damping: 30 }} />}
                  <span style={{ position: 'relative', zIndex: 1 }}>{z === 'hall' ? 'Зал' : 'Терраса'}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {hallPending ? (
          <div style={{ marginTop: 18, minHeight: 200, display: 'grid', placeItems: 'center', borderRadius: 16, background: 'var(--glass-2)', border: '1px solid var(--hair)' }}>
            <span style={{ fontSize: 13, color: 'var(--mute)' }}>{hall.loading ? 'Загружаю живой зал…' : 'Живой зал iiko не подключён для этого филиала.'}</span>
          </div>
        ) : (
          <>
            {/* Пол рисуем, только если в секции есть расставленные столы. Схемы нет —
                рисовать пустой зал нечестно: показываем столы списком-сеткой. */}
            {shown.length > 0 && (
              <div style={{ perspective: 1500, marginTop: 18 }}>
                <div style={{
                  position: 'relative', width: '100%', aspectRatio: real ? `${secW} / ${secH}` : '16 / 10',
                  transform: 'rotateX(34deg)', transformStyle: 'preserve-3d', transformOrigin: 'center 62%',
                }}>
                  {/* пол — адаптивный по теме (класс .iso-floor: свет/графит) */}
                  <div className="iso-floor" style={{ position: 'absolute', inset: 0 }} />
                  {shown.map((t) => <TableTop key={t.id} t={t} />)}
                  {shown.map((t) => <Pin key={`p-${t.id}`} t={t} />)}
                </div>
              </div>
            )}

            {real && offPlan.length > 0 && (
              <div style={{ marginTop: shown.length ? 22 : 16 }}>
                <div style={{ fontSize: 11, color: 'var(--mute)', marginBottom: 9 }}>
                  {shown.length
                    ? `Ещё ${offPlan.length} ${offPlan.length === 1 ? 'стол' : 'столов'} не расставлены на схеме зала в iiko`
                    : 'Схема зала в iiko не нарисована — столы показаны списком'}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {offPlan.map((t) => {
                    const c = t.st === 'occ' ? 'var(--grn)' : t.st === 'res' ? 'var(--amb)' : 'var(--mute)'
                    return (
                      <div key={t.id} style={{
                        display: 'flex', alignItems: 'center', gap: 7, padding: '7px 11px', borderRadius: 12,
                        border: '1px solid var(--hair)',
                        background: t.st === 'occ' ? 'color-mix(in srgb,var(--grn) 12%,transparent)' : 'var(--glass-2)',
                      }}>
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: c, flex: 'none' }} />
                        <span className="tnum" style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--hi)' }}>№{t.id}</span>
                        <span style={{ fontSize: 11, color: 'var(--mute)' }}>
                          {t.st === 'occ' ? `${t.guests || 0} гост.` : t.st === 'res' ? 'бронь' : `${t.seats || 0} мест`}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 16, fontSize: 11.5, color: 'var(--lo)' }}>
              {(real ? (['occ', ...(hasRes ? ['res'] : []), 'free'] as St[]) : (['occ', 'res', 'free', 'clean'] as St[])).map((s) => (
                <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <i style={{ width: 10, height: 10, borderRadius: '50%', background: STATE[s].fill === 'transparent' ? 'var(--glass-2)' : STATE[s].fill, border: STATE[s].fill === 'transparent' ? '1.5px solid var(--hair)' : 'none' }} />
                  {STATE[s].label}
                </span>
              ))}
              {/* считаем ВСЕ столы секции, а не только расставленные на схеме:
                  иначе у филиала без схемы выходило «0/0 занято» при семи столах */}
              <span style={{ marginLeft: 'auto', color: 'var(--mute)' }}>{occTotal}/{tablesTotal} занято · {guestsNow} гостей</span>
            </div>
          </>
        )}
      </Glass>

      {/* ЛЕНТА АКТИВНОСТИ (Smart Home) */}
      <Glass span={4}>
        <div className="eyebrow"><span>Активность</span><span className="hint">живьём</span></div>
        <div style={{ marginTop: 12 }}>
          {!demoMode && liveFeed.length === 0 && (
            <div style={{ padding: '11px 0', fontSize: 12.5, color: 'var(--mute)' }}>{hall.loading ? 'Загружаю…' : real ? 'Пока тихо — открытых столов нет.' : 'Живой зал не подключён.'}</div>
          )}
          {liveFeed.map((f, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '11px 0', borderTop: i ? '1px solid var(--hair-2)' : 'none' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: f.c, flex: 'none', boxShadow: `0 0 8px ${f.c}` }} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 12.5, color: 'var(--hi)', fontWeight: 550, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.k}</div>
                <div className="tnum" style={{ fontSize: 11.5, color: 'var(--mute)' }}>{f.v}</div>
              </div>
              <span className="tnum" style={{ fontSize: 11, color: 'var(--mute)', flex: 'none' }}>{f.t}</span>
            </div>
          ))}
        </div>
      </Glass>

      {/* Оперативные плитки зала — под основными цифрами дня */}
      {kpi.map((m) => (
        <Glass key={m.k} span={3} tap>
          <div style={{ fontSize: 10, letterSpacing: '.11em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 650 }}>{m.k}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 9 }}>
            <span className="tnum" style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-.03em', color: 'var(--hi)' }}>{m.v}</span>
            <Delta v={m.d} invert={m.invert} size={12} />
          </div>
        </Glass>
      ))}
    </>
  )
}
