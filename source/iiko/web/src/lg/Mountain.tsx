import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { Glass } from './ui'
import SalmonAct from './SalmonAct'
import ChecklistUpload from './ChecklistUpload'
import ChecklistBoard from './ChecklistBoard'
import Journals from './Journals'
import { tenge } from '../lib/format'
import type { Notice } from './Notices'

/* «Гора» — задачи филиалам, баллы и чек-листы из чата.

   Доступ по личной ссылке: токен берём из ?m= в адресе. Если его нет — это
   кабинет владельца, спрашиваем свою ссылку у /api/mountain/links.
   Владелец видит все филиалы и ставит задачи (ИИ предлагает — владелец
   подтверждает). Управляющий видит только свой филиал и может задачу закрыть.

   Баллы: задача быстро 10 / средне 25 / долго 50; чек-лист +1 за сданный слот,
   −1 за пропущенный. Гора — 40 ступеней по 25 баллов: три задачи не поднимают
   на вершину, а штрафы тянут вниз. */

type Task = {
  id: number; organization_id: string; title: string; detail?: string
  money?: number; effort?: string; points?: number; status?: string
}
type Stage = { name: string; from: number; next: string | null; to_next: number; index: number; count: number }
type Level = { step: number; steps: number; height_pct: number; to_next: number; summit?: number; stage?: Stage }
type Row = {
  organization_id: string; name: string
  tasks_open: Task[]; tasks_done: number; task_points: number
  checklist: { done: number; missed: number; points: number }
  points: number; level: Level; score?: number | null; score_note?: string | null
  criteria?: { key: string; label: string; value: number | null; weight: number; counted?: boolean }[]
}
type RankRow = {
  organization_id: string; name: string; points: number; level: Level
  place: number; tasks_done: number; score?: number | null
}
type Board = {
  role?: 'owner' | 'manager'; month?: string; error?: string; ranking?: RankRow[]
  scale?: { tasks: Record<string, number>; step_points: number; steps: number }
  checklist_from?: string   // пусто — чек-листы ещё не в зачёте
  branches?: Row[]; score_pending?: boolean
  demo?: boolean            // выдуманная сеть для показа клиенту
}
type Note = { branch: string; note: string }

// «Все филиалы» в выпадающем списке — не филиал, а команда развернуть задачу на всю сеть
const ALL = '__all__'

const EFFORT_COLOR: Record<string, string> = { 'быстро': 'var(--grn)', 'средне': 'var(--amb)', 'долго': 'var(--red)' }

function Eyebrow({ t, hint }: { t: string; hint?: string }) {
  return <div className="eyebrow"><span>{t}</span>{hint && <span className="hint">{hint}</span>}</div>
}

/* Гора: картинка со склоном + ВЕРТИКАЛЬНЫЙ прогресс у каждого филиала.
   Подъём — движение вверх, поэтому и полоска стоит вертикально: горизонталь
   читалась как рейтинг, а не как восхождение. Рядом с филиалом — его оценка
   0–100 (формула временная, меняем отдельно). */

const PIN_COLORS = ['#5b7cfa', '#37c489', '#e9b64a', '#e0619a', '#57c2d6', '#a97bf0', '#f0834a', '#6ad19b', '#8892b0']

const shortName = (name: string) => name.replace(/^Астана,?\s*(ул\.?)?\s*/i, '').trim() || name
const monogram = (name: string) => {
  const w = shortName(name).split(/[\s.]+/).filter(Boolean)
  return (w[0]?.[0] || '?').toUpperCase() + (w[0]?.[1] || '').toLowerCase()
}
const scoreColor = (n: number) => (n >= 70 ? 'var(--grn)' : n >= 45 ? 'var(--amb)' : 'var(--red)')

/** Куда положить всплывающий разбор оценки, чтобы он поместился целиком.

    Кольцо стоит у края карточки, а критериев восемь: разбор и уезжал за
    правый край, и не влезал по высоте. Считаем от кольца, зажимаем по
    горизонтали в окно и раскрываем в ту сторону, где места больше, отдавая
    ровно столько высоты, сколько там есть.
*/
export function popoverBox(host: { getBoundingClientRect(): DOMRect } | null,
                           win = typeof window === 'undefined' ? null : window): React.CSSProperties {
  if (!host || !win) return { position: 'absolute', bottom: 'calc(100% + 8px)', left: '50%', transform: 'translateX(-50%)', minWidth: 190 }
  const r = host.getBoundingClientRect()
  const M = 8                                   // поля от краёв окна
  const w = Math.min(280, win.innerWidth - M * 2)
  const left = Math.min(Math.max(M, r.left + r.width / 2 - w / 2), win.innerWidth - w - M)
  const above = r.top - M * 2
  const below = win.innerHeight - r.bottom - M * 2
  return below >= above
    ? { position: 'fixed', left, top: r.bottom + M, width: w, maxHeight: Math.max(120, below) }
    : { position: 'fixed', left, bottom: win.innerHeight - r.top + M, width: w, maxHeight: Math.max(120, above) }
}

/* Кольцо оценки: дуга по величине балла + разбор по критериям при наведении. */
export function ScoreRing({ score, criteria, note }: {
  score: number | null
  criteria?: { label: string; value: number | null; weight: number; counted?: boolean }[]
  note?: string | null
}) {
  const [open, setOpen] = useState(false)
  /* Разбор висел внутри строки: absolute + translateX(-50%) от кольца, а кольцо
     стоит у правого края карточки — половина разбора уезжала за экран, и с
     восемью критериями он ещё и не помещался по высоте.

     Теперь это слой поверх страницы (position: fixed), привязанный к кольцу:
     • по горизонтали центрируем по кольцу и ЗАЖИМАЕМ в окно — за край не уйдёт;
     • по вертикали раскрываем в ту сторону, где места больше, и задаём
       maxHeight ровно по этому месту: не влезло — прокручивается внутри себя,
       а не обрезается;
     • держимся за bottom, а не за top, когда раскрываемся вверх: так высоту
       разбора знать заранее не нужно.
     Прокрутка страницы разбор закрывает — иначе он «отклеился» бы от кольца. */
  const [box, setBox] = useState<React.CSSProperties | null>(null)
  const hostRef = useRef<HTMLSpanElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const show = (v: boolean) => {
    if (v) setBox(popoverBox(hostRef.current))
    setOpen(v)
  }
  /* Уводя мышь с кольца на сам разбор, курсор пересекает пустоту: закрывать
     сразу — значит не дать до разбора доехать. Даём фору, её снимает вход в
     разбор. */
  const grace = useRef<number | null>(null)
  const hideSoon = () => {
    if (grace.current) clearTimeout(grace.current)
    grace.current = window.setTimeout(() => setOpen(false), 160)
  }
  const keep = () => { if (grace.current) { clearTimeout(grace.current); grace.current = null } }
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    const outside = (e: PointerEvent) => {
      const t = e.target as Node
      if (!hostRef.current?.contains(t) && !popRef.current?.contains(t)) setOpen(false)
    }
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    window.addEventListener('keydown', esc)
    document.addEventListener('pointerdown', outside, true)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('keydown', esc)
      document.removeEventListener('pointerdown', outside, true)
      keep()
    }
  }, [open])
  const size = 36, r = 14, C = 2 * Math.PI * r
  const val = score ?? 0
  const col = scoreColor(val)
  return (
    <span ref={hostRef} style={{ position: 'relative', flex: 'none', cursor: 'pointer' }}
      role="button" tabIndex={0} aria-expanded={open}
      onClick={(e) => { e.stopPropagation(); show(!open) }}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(!open) } }}
      onMouseEnter={() => { keep(); show(true) }} onMouseLeave={hideSoon}>
      <svg width={size} height={size} style={{ display: 'block' }} aria-label={`оценка ${score ?? '—'} из 100`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--hair)" strokeWidth={3.5} />
        {score != null && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={col} strokeWidth={3.5}
            strokeLinecap="round" strokeDasharray={`${C * (val / 100)} ${C}`}
            transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        )}
        <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central"
          style={{ fontSize: 11.5, fontWeight: 800, fill: score == null ? 'var(--mute)' : col }}>
          {score ?? '—'}
        </text>
      </svg>
      {open && criteria && createPortal((
        <div ref={popRef} onMouseEnter={keep} onMouseLeave={() => setOpen(false)}
          style={{
            ...box,
            zIndex: 60, padding: '10px 12px', borderRadius: 12, textAlign: 'left',
            background: 'var(--card)', border: '1px solid var(--hair)',
            boxShadow: '0 16px 34px -14px rgba(20,40,80,.5)', display: 'block',
            overflowY: 'auto', overscrollBehavior: 'contain',
            font: '400 13px/1.4 var(--font-ui, inherit)', color: 'var(--hi)',
          }}>
          <span style={{ display: 'block', fontSize: 10.5, color: 'var(--mute)', fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase' }}>
            из чего сложилась
          </span>
          {note && (
            <span style={{ display: 'block', marginTop: 5, fontSize: 11, color: 'var(--amb)', lineHeight: 1.4 }}>{note}</span>
          )}
          {criteria.map((c) => (
            <span key={c.label} style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginTop: 5, fontSize: 12 }}>
              <span style={{ flex: 1, color: c.counted ? 'var(--hi)' : 'var(--mute)' }}>
                {c.label}<span style={{ fontSize: 10, color: 'var(--mute)' }}> · вес {c.weight}</span>
              </span>
              <span className="tnum" style={{ fontWeight: 700, color: c.value == null ? 'var(--mute)' : scoreColor(c.value) }}>
                {c.value == null ? 'нет данных' : c.value}
              </span>
            </span>
          ))}
        </div>
      ), document.body)}
    </span>
  )
}

/* Тропа-серпантин в координатах 0..100. Точки идут снизу вверх зигзагом:
   подъём в гору не бывает по прямой, и по витку сразу видно, далеко ли до
   следующего лагеря. */
const TRAIL_POINTS: [number, number][] = [
  [24, 94], [40, 86], [26, 78], [44, 70], [30, 62],
  [48, 54], [36, 46], [54, 38], [44, 30], [58, 22], [50, 12],
]

function trailPoint(frac: number): { x: number; y: number } {
  const f = Math.min(1, Math.max(0, frac)) * (TRAIL_POINTS.length - 1)
  const i = Math.min(TRAIL_POINTS.length - 2, Math.floor(f))
  const t = f - i
  const [x1, y1] = TRAIL_POINTS[i]
  const [x2, y2] = TRAIL_POINTS[i + 1]
  return { x: x1 + (x2 - x1) * t, y: y1 + (y2 - y1) * t }
}

function Peak({ rows, mine }: { rows: Row[]; mine?: string }) {
  const [hover, setHover] = useState<string | null>(null)
  const steps = rows[0]?.level.steps ?? 40
  const reduce = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  return (
    <div className="lg-peak" style={{ display: 'grid', gap: 14, alignItems: 'start' }}>
      {/* склон: пины ползут вверх по мере роста баллов */}
      <div style={{ position: 'relative', borderRadius: 16, overflow: 'hidden', background: 'var(--glass-2)' }}>
        <img src="/mountain.jpg" alt="" style={{ width: '100%', height: 'auto', display: 'block' }} />
        <div aria-hidden style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(10,16,30,.45), rgba(10,16,30,0) 45%)' }} />
        {/* Тропу не рисуем — пины и так идут по ней; пунктир поверх фотографии
            только мусорил кадр. Порядок подъёма задаёт trailPoint(). */}
        {rows.map((r, i) => {
          // Позиция — точка НА ТРОПЕ по доле пройденных баллов до вершины.
          const summit = r.level.summit || 400
          const frac = Math.min(1, Math.max(0, r.points) / summit)
          const pos = trailPoint(frac)
          const on = r.organization_id === mine || hover === r.organization_id
          const col = PIN_COLORS[i % PIN_COLORS.length]
          const leader = i === 0 && r.points > 0
          return (
            <motion.div key={r.organization_id}
              onMouseEnter={() => setHover(r.organization_id)} onMouseLeave={() => setHover(null)}
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
              animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.4, delay: i * 0.04 }}
              title={`${r.name}: ${r.points} баллов`}
              style={{
                position: 'absolute', left: `${pos.x}%`, top: `${pos.y}%`,
                transform: `translate(-50%, -50%) translateX(${(i % 3 - 1) * 7}px)`,
                width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center',
                fontSize: 9, fontWeight: 700, color: '#fff', zIndex: on ? 3 : 1,
                background: `linear-gradient(160deg, ${col}, ${col}cc)`,
                border: leader ? '2px solid var(--amb)' : on ? '2px solid #fff' : '1.5px solid rgba(255,255,255,.55)',
                boxShadow: leader ? '0 0 0 4px rgba(233,182,74,.28), 0 3px 10px -3px rgba(0,0,0,.55)'
                                  : '0 3px 10px -3px rgba(0,0,0,.55)',
              }}>
              {monogram(r.name)}
              {leader && (
                <span aria-hidden style={{ position: 'absolute', top: -12, left: '50%', transform: 'translateX(-50%)', fontSize: 12 }}>★</span>
              )}
            </motion.div>
          )
        })}
      </div>

      {/* строки: место, филиал, оценка, ВЕРТИКАЛЬНАЯ полоска подъёма, баллы */}
      <div style={{ display: 'grid', gap: 1 }}>
        {rows.map((r, i) => {
          const col = PIN_COLORS[i % PIN_COLORS.length]
          const on = r.organization_id === mine || hover === r.organization_id
          const frac = Math.min(1, r.level.step / steps)
          return (
            <div key={r.organization_id}
              onMouseEnter={() => setHover(r.organization_id)} onMouseLeave={() => setHover(null)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '6px 9px', borderRadius: 10,
                background: on ? 'var(--glass-2)' : 'transparent', transition: 'background .16s',
              }}>
              {/* место: первый выделен, остальные спокойно — иначе таблица пестрит */}
              <span className="tnum" style={{
                width: 20, fontSize: i === 0 ? 13 : 11, textAlign: 'center', fontWeight: 800,
                color: i === 0 ? 'var(--amb)' : 'var(--mute)',
              }}>{i === 0 ? '★' : i + 1}</span>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: col, flex: 'none' }} />
              <span style={{ flex: 1, fontSize: 12.5, color: 'var(--hi)', fontWeight: on ? 700 : 500 }}>
                {shortName(r.name)}
                {/* отрыв: сколько до того, кто выше — это и есть азарт */}
                {i > 0 && rows[i - 1].points > r.points && (
                  <span style={{ fontSize: 10.5, color: 'var(--mute)', marginLeft: 7 }}>
                    ↑ {rows[i - 1].points - r.points} до {shortName(rows[i - 1].name)}
                  </span>
                )}
                {i === 0 && rows.length > 1 && rows[0].points > rows[1].points && (
                  <span style={{ fontSize: 10.5, color: 'var(--grn)', marginLeft: 7, fontWeight: 700 }}>
                    +{rows[0].points - rows[1].points} отрыв
                  </span>
                )}
              </span>
              {/* Оценка филиала — кольцо, заполненное на свою долю: цифра в
                  рамке не показывает «сколько это из ста», а кольцо показывает.
                  По наведению — из чего сложилась. Задачи в неё не входят. */}
              <ScoreRing score={r.score ?? null} criteria={r.criteria} note={r.score_note} />
              {/* вертикальная полоска подъёма */}
              <span aria-hidden title={`ступень ${r.level.step} из ${steps}`}
                style={{ width: 8, height: 26, borderRadius: 4, background: 'var(--hair)', overflow: 'hidden', display: 'flex', alignItems: 'flex-end', flex: 'none' }}>
                <span style={{ display: 'block', width: '100%', height: `${frac * 100}%`, background: col, borderRadius: 4 }} />
              </span>
              {/* Минус показываем ЯВНО. Ступень у всех отстающих нулевая, и филиал
                  со штрафом −28 выглядел ровно как тот, кто просто ещё не начал:
                  «0» и «0». Красная цифра со знаком отличает провал от старта. */}
              <span className="tnum" title={r.points < 0 ? 'штрафы за пропуски перевесили — филиал ниже нуля' : undefined}
                style={{ width: 66, textAlign: 'right', fontSize: 12.5, fontWeight: 700,
                         color: r.points < 0 ? 'var(--red)' : 'var(--hi)' }}>
                {r.points}
                <span style={{ color: 'var(--mute)', fontWeight: 500, fontSize: 10.5 }}> балл.</span>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* Массивный прогресс филиала. По apple-design:
   • иерархия строится весом и размером, а не цветными плашками: крупное число
     баллов слева, ступень справа — глаз ловит их первым;
   • полоса толстая и с внутренним рельефом (светлая кромка сверху, тень внутрь) —
     читается как материал, а не как залитый прямоугольник;
   • заполнение приезжает пружиной без отскока: экран не жестовый, подпрыгивание
     было бы шумом. При prefers-reduced-motion полоса просто появляется;
   • подписи внизу — чипы: закрытые задачи, сданные чек-листы и штраф отдельно,
     чтобы «+3» и «−5» не слипались в одну строку. */
function Progress({ row }: { row: Row }) {
  const reduce = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const step = row.level.step, steps = row.level.steps || 40
  const frac = Math.min(1, Math.max(0, step / steps))
  const col = step >= steps * 0.66 ? 'var(--grn)' : step >= steps * 0.33 ? 'var(--acc)' : 'var(--amb)'

  const chip = (text: string, tone?: string) => (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 9px', borderRadius: 9,
      border: '1px solid var(--hair)', background: 'var(--glass-2)',
      fontSize: 11, fontWeight: 650, color: tone || 'var(--mute)',
    }}>{text}</span>
  )

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--hi)', letterSpacing: '-.01em' }}>
          {row.name}
        </div>
        <div className="tnum" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 26, fontWeight: 780, letterSpacing: '-.03em', lineHeight: 1,
                         color: row.points < 0 ? 'var(--red)' : 'var(--hi)' }}>
            {row.points}
          </span>
          <span style={{ fontSize: 11.5, color: 'var(--mute)', fontWeight: 600 }}>баллов</span>
        </div>
      </div>

      {/* сама полоса */}
      <div style={{ marginTop: 10, position: 'relative', height: 14, borderRadius: 999,
                    background: 'var(--hair)', overflow: 'hidden',
                    boxShadow: 'inset 0 1px 2px rgba(20,40,80,.18)' }}>
        <motion.div
          initial={reduce ? { opacity: 0 } : { width: 0 }}
          animate={reduce ? { opacity: 1, width: `${frac * 100}%` } : { width: `${frac * 100}%` }}
          transition={{ type: 'spring', bounce: 0, duration: 0.7 }}
          style={{
            height: '100%', borderRadius: 999,
            background: `linear-gradient(90deg, ${col}, ${col})`,
            boxShadow: `0 0 0 1px rgba(255,255,255,.25) inset, 0 2px 8px -2px ${col}`,
          }} />
        {/* отметки каждые 25% пути — чтобы длина читалась как лестница */}
        {[0.25, 0.5, 0.75].map((m) => (
          <span key={m} aria-hidden style={{
            position: 'absolute', left: `${m * 100}%`, top: 0, bottom: 0, width: 1,
            background: 'rgba(255,255,255,.35)',
          }} />
        ))}
      </div>

      <div style={{ marginTop: 8, display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--hi)' }}>
          Ступень {step}<span style={{ color: 'var(--mute)', fontWeight: 500 }}> из {steps}</span>
        </span>
        {row.points < 0
          /* Ниже нулевой ступени гора не опускает — иначе подъём превратился бы
             в яму. Но молчать об этом нельзя: пишем, сколько до нуля. */
          ? <span style={{ fontSize: 11.5, color: 'var(--red)', fontWeight: 650 }}>
              ниже нуля на {Math.abs(row.points)} — сначала выйти в ноль
            </span>
          : row.level.to_next
            ? <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>до следующей {row.level.to_next}</span>
            : <span style={{ fontSize: 11.5, color: 'var(--grn)', fontWeight: 650 }}>вершина</span>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {chip(`закрыто ${row.tasks_done}`, row.tasks_done ? 'var(--grn)' : undefined)}
          {chip(`чек-листы ${row.checklist.done}`)}
          {!!row.checklist.missed && chip(`пропуски ${Math.abs(row.checklist.missed)}`, 'var(--red)')}
        </span>
      </div>
    </div>
  )
}

export default function Mountain() {
  const [token, setToken] = useState<string | null>(null)
  const [board, setBoard] = useState<Board | null>(null)
  const [notes, setNotes] = useState<Note[]>([])
  const [digestHint, setDigestHint] = useState('')
  // форма ручной задачи: владелец пишет сам, ИИ в неё не лезет
  const [tOrg, setTOrg] = useState('')
  const [tTitle, setTTitle] = useState('')
  const [tDetail, setTDetail] = useState('')
  const [tEffort, setTEffort] = useState('средне')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)      // фатальная: экран не собрать
  const [formErr, setFormErr] = useState('')               // подсказка в форме, экран живёт
  const [notices, setNotices] = useState<Notice[]>([])
  const [msg, setMsg] = useState('')


  /* Токен из ссылки, иначе — кабинет владельца.

     Откат «ссылка филиала не открылась → открываем кабинет владельца» убран.
     Он стоил дорого: любая осечка — и управляющий получал доску ВЛАДЕЛЬЦА, с
     формой постановки задач, рассылкой всем и прогрессом всех филиалов, а сдача
     чек-листа исчезала, она рисуется только не-владельцу. Ссылка филиала не
     должна превращаться в чужой кабинет ни при каких обстоятельствах: не
     открылась — так и говорим. */
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get('m')

    if (!q) {
      fetch('/api/mountain/links', { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: { owner?: { link?: string } }) => {
          const t = (d.owner?.link || '').split('/m/')[1] || null
          if (t) setToken(t); else setErr('Открой сайт по своей личной ссылке.')
        })
        .catch((e) => setErr(String(e.message || e)))
      return
    }

    fetch(`/api/mountain/data/${q}`)
      .then((r) => (r.ok ? q : Promise.reject(new Error('stale'))))
      .then(setToken)
      .catch(() => setErr('Ссылка недействительна — попроси владельца прислать новую.'))
  }, [])

  const load = useCallback((t: string) => {
    fetch(`/api/mountain/data/${t}`).then((r) => r.json())
      .then((d: Board) => {
        if (d.error) { setErr(d.error); return }
        setBoard(d)
        // Показатели для оценки собираются в фоне (десятки секунд на девять
        // филиалов). Доску отдали сразу, а число в кольце дотягиваем одним
        // повторным запросом — без него оценка молчала бы до перезахода.
        if (d.score_pending) setTimeout(() => load(t), 25000)
      })
      .catch((e) => setErr(String(e.message || e)))
    fetch(`/api/mountain/notices/${t}`).then((r) => r.json())
      .then((d: { notices?: Notice[] }) => setNotices(d.notices || [])).catch(() => {})
  }, [])

  /* Дайджест теперь СПРАШИВАЮТ, а не ждут.

     Раньше «Обновить» держал запрос до конца сбора — полторы-две минуты, и
     владелец сидел над кнопкой «Считаю…», не понимая, жив ли запрос. Сервер
     отвечает сразу с pending, а мы перезапрашиваем, пока не приедет результат.
     Опрос ограничен по числу попыток: молчание не должно превращаться в
     бесконечный цикл запросов. */
  const digestTimer = useRef<number | null>(null)
  const loadDigest = useCallback((t: string, refresh = false, tries = 0) => {
    if (digestTimer.current) { clearTimeout(digestTimer.current); digestTimer.current = null }
    setBusy(refresh || tries > 0)
    fetch(`/api/mountain/digest/${t}${refresh ? '?refresh=1' : ''}`)
      .then((r) => r.json())
      .then((d: { notes?: Note[]; hint?: string; detail?: string; pending?: boolean }) => {
        if (d.detail) { setDigestHint(d.detail); setNotes([]); setBusy(false); return }
        if (d.pending) {
          setDigestHint(d.hint || 'Считаю…')
          if (tries < 12) {          // ~12 x 15 c = три минуты, дальше не мучаем сервер
            digestTimer.current = window.setTimeout(() => loadDigest(t, false, tries + 1), 15000)
          } else {
            setBusy(false)
            setDigestHint('Считается дольше обычного — загляни сюда через пару минут.')
          }
          return
        }
        setNotes(d.notes || [])
        setDigestHint(d.hint || (d.notes?.length ? '' : 'Наблюдений пока нет'))
        setBusy(false)
      })
      .catch((e) => {
        setDigestHint(`Не удалось посчитать: ${String((e as Error).message || e)}`)
        setBusy(false)
      })
  }, [])
  // таймер опроса не должен пережить экран
  useEffect(() => () => { if (digestTimer.current) clearTimeout(digestTimer.current) }, [])

  useEffect(() => { if (token) load(token) }, [token, load])
  // «Что изменилось» — блок владельца, и на сервере он owner-only. Управляющему
  // этот запрос отвечал 403 и просто сорил ошибкой в консоли: ждём роль.
  useEffect(() => {
    if (token && board?.role === 'owner') loadDigest(token)
  }, [token, board?.role, loadDigest])


  async function addTask() {
    if (!token || !tOrg || !tTitle.trim()) { setFormErr('Выбери филиал и напиши задачу'); return }
    setBusy(true); setFormErr('')
    try {
      /* «Все филиалы» — одна задача на каждый, одним запросом: сервер принимает
         список и сам сверяет каждый филиал с правами владельца. Раньше
         одинаковую задачу приходилось заводить девять раз руками. */
      const targets = tOrg === ALL ? rows.map((r) => r.organization_id) : [tOrg]
      const r = await fetch(`/api/mountain/assign/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: targets.map((org) => ({
          organization_id: org, title: tTitle.trim(),
          detail: tDetail.trim(), effort: tEffort })) }),
      })
      const d = await r.json()
      if (!r.ok || d.error) { setFormErr(d.error || `HTTP ${r.status}`); return }
      // created=0 сервер отдаёт с кодом 200: задача не создана, но текст бы стёрся
      if (!d.created) { setFormErr('Задача не создана — проверь филиал'); return }
      setTTitle(''); setTDetail(''); load(token)
    } catch (e) { setFormErr(String((e as Error).message || e)) } finally { setBusy(false) }
  }

  async function sendNotice() {
    const text = msg.trim()
    if (!token || !text) return
    setBusy(true)
    try {
      const r = await fetch(`/api/mountain/notice/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      const d = await r.json()
      if (!r.ok || d.error) { setErr(d.error || `HTTP ${r.status}`); return }
      setMsg(''); load(token)
    } finally { setBusy(false) }
  }

  async function close(id: number) {
    if (!token) return
    await fetch(`/api/mountain/close/${token}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: id }),
    }).catch(() => {})
    load(token)
  }

  if (err) return <Glass span={12}><div style={{ padding: 12, color: 'var(--red)', fontSize: 13.5 }}>{err}</div></Glass>
  if (!board) return <Glass span={12}><div style={{ padding: 12, color: 'var(--mute)', fontSize: 13.5 }}>Собираю доску…</div></Glass>

  const rows = board.branches ?? []
  const owner = board.role === 'owner'
  const mine = !owner ? rows[0]?.organization_id : undefined
  // Гора — общая: управляющий видит всю сеть и своё место в ней.
  const peakRows: Row[] = (board.ranking || []).map((r) => ({
    ...(rows.find((x) => x.organization_id === r.organization_id) || {
      organization_id: r.organization_id, name: r.name,
      tasks_open: [], tasks_done: r.tasks_done, task_points: 0,
      checklist: { done: 0, missed: 0, points: 0 },
    } as unknown as Row),
    points: r.points, level: r.level,
    // Оценка приходит и в рейтинге: управляющему нужны баллы ЧУЖИХ филиалов,
    // чтобы сравнить себя, а разбор по критериям — нет.
    score: r.score ?? rows.find((x) => x.organization_id === r.organization_id)?.score ?? null,
  }))
  const totalOpen = rows.reduce((a, r) => a + r.tasks_open.length, 0)


  return (
    <>
      {/* Гора */}
      <Glass span={12}>
        <Eyebrow t="Гора · уровень филиалов"
          hint={`${board.scale?.steps ?? 40} ступеней по ${board.scale?.step_points ?? 25} баллов · месяц ${board.month ?? ''}`} />
        <Peak rows={peakRows.length ? peakRows : rows} mine={mine} />
        <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--mute)', textAlign: 'center' }}>
          Задача: быстро 10 · средне 25 · долго 50.{' '}
          {/* Пока старт не задан, чек-листы не дают ни баллов, ни штрафов.
              Молчать об этом нельзя: нули на горе читались бы как «все провалили». */}
          {board.checklist_from
            ? <>Чек-лист: сдан слот +1, пропуск −1 · с {board.checklist_from}</>
            : <span style={{ color: 'var(--amb)' }}>Чек-листы пока не в зачёте — идёт настройка</span>}
        </div>
      </Glass>

      {/* Консоль рассылки — только у владельца. Управляющему список не нужен:
          сообщение приходит всплывающей плашкой, а второй раз читать нечего. */}
      {owner && !board.demo && (
        <Glass span={12}>
          <Eyebrow t="Сообщение управляющим" hint="придёт уведомлением на сайте, во все филиалы" />
          <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={2}
              placeholder="Например: до пятницы сдать инвентаризацию по рыбе"
              style={{ flex: 1, minWidth: 260, boxSizing: 'border-box', padding: '10px 12px', fontSize: 13.5, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 12, resize: 'vertical' }} />
            <button onClick={sendNotice} disabled={busy || !msg.trim()}
              style={{ padding: '11px 18px', borderRadius: 12, border: 'none', background: msg.trim() ? 'linear-gradient(135deg,var(--acc),var(--acc-2))' : 'var(--hair)', color: msg.trim() ? 'var(--on-acc)' : 'var(--mute)', fontSize: 13, fontWeight: 700, cursor: msg.trim() ? 'pointer' : 'default' }}>
              Отправить всем
            </button>
          </div>
          {notices.length > 0 && (
            <div style={{ marginTop: 12, display: 'grid', gap: 6 }}>
              {notices.slice(0, 5).map((n) => (
                <div key={n.id} style={{ display: 'flex', gap: 12, alignItems: 'baseline', fontSize: 12.5, paddingBottom: 6, borderBottom: '1px solid var(--hair-2)' }}>
                  <span style={{ flex: 1, color: 'var(--hi)' }}>{n.text}</span>
                  <span style={{ color: 'var(--mute)', fontSize: 11 }}>
                    {(n.created_at || '').replace('T', ' ').slice(5, 16)} · прочитали {n.read_by ?? 0}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Glass>
      )}

      {/* Акты и разборы — сразу под горой, на тех же ссылках */}
      {token && <SalmonAct token={token} />}

      {/* Задачи */}
      <Glass span={12}>
        <Eyebrow t={owner ? 'Задачи филиалам' : 'Мои задачи'}
          hint={owner ? `${totalOpen} открытых · ставит только владелец` : `${totalOpen} открытых`} />
        {owner && (
          <>
            {/* Что изменилось: наблюдение от ИИ, а не готовые задачи. Считается
                из кэша — владелец не должен ждать полторы минуты сбора. */}
            <div style={{ marginTop: 12, padding: '12px 14px', borderRadius: 13, border: '1px solid var(--hair)', background: 'var(--glass-2)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11.5, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 700 }}>
                  Что изменилось
                </span>
                <button onClick={() => token && loadDigest(token, true)} disabled={busy}
                  style={{ marginLeft: 'auto', padding: '6px 12px', borderRadius: 10, border: '1px solid var(--hair)', background: 'transparent', color: 'var(--hi)', fontSize: 11.5, fontWeight: 650, cursor: busy ? 'default' : 'pointer' }}>
                  {busy ? 'Считаю…' : 'Обновить'}
                </button>
              </div>
              {digestHint && <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>{digestHint}</div>}
              <div style={{ marginTop: 10, display: 'grid', gap: 8,
                            gridTemplateColumns: 'repeat(auto-fill,minmax(250px,1fr))' }}>
                {notes.map((n) => {
                  // Стрелку и цвет берём из самого текста: «выросла/упала на N%».
                  const m = n.note.match(/(вырос\w*|упал\w*|снизил\w*|\+|−|-)\s*(?:на\s*)?(\d+[.,]?\d*)\s*%/i)
                  const up = m ? /вырос|\+/i.test(m[1]) : null
                  const pct = m ? m[2] : null
                  const tone = up === null ? 'var(--mute)' : up ? 'var(--grn)' : 'var(--red)'
                  return (
                    <div key={n.branch} style={{
                      padding: '11px 13px', borderRadius: 13, border: '1px solid var(--hair)',
                      background: 'var(--card)', display: 'flex', gap: 11, alignItems: 'flex-start',
                    }}>
                      {pct && (
                        <span style={{
                          flex: 'none', minWidth: 46, textAlign: 'center', padding: '5px 6px',
                          borderRadius: 10, background: 'var(--glass-2)', color: tone,
                          fontWeight: 800, fontSize: 12.5, fontVariantNumeric: 'tabular-nums',
                        }}>{up ? '↑' : '↓'} {pct}%</span>
                      )}
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--hi)' }}>
                          {shortName(n.branch)}
                        </span>
                        <span style={{ display: 'block', marginTop: 3, fontSize: 12.5, color: 'var(--mute)', lineHeight: 1.4 }}>
                          {n.note}
                        </span>
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Задачу владелец пишет сам */}
            <div style={{ marginTop: 12, display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))' }}>
              <select value={tOrg} onChange={(e) => setTOrg(e.target.value)}
                style={{ padding: '9px 11px', fontSize: 13, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 11 }}>
                <option value="">Филиал</option>
                {rows.length > 1 && <option value={ALL}>Все филиалы ({rows.length})</option>}
                {rows.map((r) => <option key={r.organization_id} value={r.organization_id}>{shortName(r.name)}</option>)}
              </select>
              <input value={tTitle} onChange={(e) => setTTitle(e.target.value)} placeholder="Задача"
                style={{ padding: '9px 11px', fontSize: 13, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 11 }} />
              <input value={tDetail} onChange={(e) => setTDetail(e.target.value)} placeholder="Первый шаг (необязательно)"
                style={{ padding: '9px 11px', fontSize: 13, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 11 }} />
              <select value={tEffort} onChange={(e) => setTEffort(e.target.value)}
                style={{ padding: '9px 11px', fontSize: 13, fontFamily: 'inherit', background: 'var(--glass-2)', color: 'var(--hi)', border: '1px solid var(--hair)', borderRadius: 11 }}>
                <option value="быстро">быстро · 10 баллов</option>
                <option value="средне">средне · 25 баллов</option>
                <option value="долго">долго · 50 баллов</option>
              </select>
              <button onClick={addTask} disabled={busy}
                style={{ padding: '10px 16px', borderRadius: 12, border: 'none', background: 'linear-gradient(135deg,var(--acc),var(--acc-2))', color: 'var(--on-acc)', fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>
                {tOrg === ALL ? `Поставить всем (${rows.length})` : 'Поставить задачу'}
              </button>
            </div>
            {formErr && (
              <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--red)' }}>{formErr}</div>
            )}
            <div style={{ display: 'none' }}>
            </div>
          </>
        )}

        <div style={{ marginTop: 14, display: 'grid', gap: 10 }}>
          {rows.map((r) => (
            <div key={r.organization_id} style={{ border: '1px solid var(--hair)', borderRadius: 14, padding: '12px 14px' }}>
              <Progress row={r} />
              {r.tasks_open.map((t) => (
                <div key={t.id} style={{ marginTop: 9, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13, color: 'var(--hi)' }}>{t.title}</div>
                    {t.detail && <div style={{ fontSize: 11.5, color: 'var(--mute)' }}>{t.detail}</div>}
                    <div style={{ fontSize: 11, marginTop: 2 }}>
                      <span style={{ color: EFFORT_COLOR[t.effort || 'средне'] }}>{t.effort}</span>
                      <span style={{ color: 'var(--mute)' }}> · {t.points} баллов{t.money ? ` · ${tenge(t.money)}` : ''}</span>
                    </div>
                  </div>
                  <button onClick={() => close(t.id)}
                    style={{ padding: '7px 13px', borderRadius: 10, border: '1px solid var(--hair)', background: 'transparent', color: 'var(--hi)', fontSize: 12, fontWeight: 650, cursor: 'pointer' }}>
                    Закрыть
                  </button>
                </div>
              ))}
              {!r.tasks_open.length && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--mute)' }}>Открытых задач нет.</div>}
            </div>
          ))}
        </div>
      </Glass>

      {/* Сдачу чек-листа показываем только управляющему: владелец их не сдаёт,
          ему нужна картина «кто сдал, кто нет» — она ниже. */}
      {token && !owner && <ChecklistUpload token={token} />}
      {token && owner && <ChecklistBoard token={token} branches={rows.map((r) => r.name)} />}

      {/* Бумажные журналы — здесь же: их заполняет тот же человек и в тот же
          заход, что и чек-лист. Отдельная вкладка ради трёх галочек не нужна. */}
      {token && <Journals token={token} />}

    </>
  )
}
