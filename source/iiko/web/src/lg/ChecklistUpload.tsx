import { useEffect, useRef, useState } from 'react'
import { Glass } from './ui'
import { SLOTS, ZONES, type Done, type Issue, type Review,
         issueText, nearestSlot, sentIn, zoneVerdict, zoneCriteria } from './checklist'

/* Сдача чек-листа прямо с сайта: слот → зона → кадры → РАЗБОР.

   Экономия заложена в загрузку, а не в разбор: кадры сжимаются В БРАУЗЕРЕ до
   1024 px и JPEG 0.7 ещё до отправки. Это режет и трафик управляющего, и
   стоимость vision, которая считается по площади картинки.

   Кадры сначала КОПЯТСЯ, и только потом уходят. Раньше отправка начиналась на
   onChange: снял один кадр — он и улетел, второй ракурс той же зоны добавить
   было уже некуда. Теперь до четырёх кадров лежат превью, лишний можно убрать,
   отправка — отдельной кнопкой.

   Разбор модели раньше приходил в ответе и молча выбрасывался: управляющий
   отправлял кадры и не узнавал, что не так с санузлом. Теперь виден ответ по
   КАЖДОМУ критерию чек-листа, а зелёным грязь не отмечается. */

const MAX_PHOTOS = 4
const MAX_SIDE = 1024

type Criteria = Record<string, { label: string; items: string[] }>

function compress(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(file)
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('не картинка')) }
    img.onload = () => {
      URL.revokeObjectURL(url)
      const k = Math.min(1, MAX_SIDE / Math.max(img.width, img.height))
      const c = document.createElement('canvas')
      c.width = Math.round(img.width * k)
      c.height = Math.round(img.height * k)
      const ctx = c.getContext('2d')
      if (!ctx) { reject(new Error('canvas недоступен')); return }
      ctx.drawImage(img, 0, 0, c.width, c.height)
      resolve(c.toDataURL('image/jpeg', 0.7).split(',', 2)[1] || '')
    }
    img.src = url
  })
}

export default function ChecklistUpload({ token }: { token: string }) {
  const input = useRef<HTMLInputElement>(null)
  const [slot, setSlot] = useState(() => nearestSlot())
  const [zone, setZone] = useState('hall')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState<Done[]>([])
  const [reviews, setReviews] = useState<Review[]>([])
  const [criteria, setCriteria] = useState<Criteria>({})
  const [verdict, setVerdict] = useState<{ ok?: boolean; checked?: boolean; issues?: Issue[]; reason?: string } | null>(null)
  // кадры, снятые но ещё не отправленные: {сжатый base64, ссылка на превью}
  const [queue, setQueue] = useState<{ b64: string; url: string }[]>([])

  const load = () => {
    fetch(`/api/mountain/checklist/${token}`).then((r) => r.json())
      .then((d: { done?: Done[]; criteria?: Criteria; reviews?: Review[] }) => {
        setDone(d.done || []); setCriteria(d.criteria || {}); setReviews(d.reviews || [])
      }).catch(() => {})
  }
  useEffect(load, [token])

  /* Снятый кадр кладём в очередь, а не отправляем. Сжимаем сразу: если ждать
     отправки, четыре кадра ужимались бы разом и телефон заметно подвисал. */
  async function addPhotos(list: FileList | null) {
    if (!list?.length) return
    setErr(null)
    const room = MAX_PHOTOS - queue.length
    if (room <= 0) { setErr(`Больше ${MAX_PHOTOS} кадров на зону не нужно`); return }
    const files = Array.from(list).slice(0, room)
    try {
      const added = await Promise.all(files.map(async (f) => ({
        b64: await compress(f), url: URL.createObjectURL(f),
      })))
      setQueue((q) => [...q, ...added])
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      if (input.current) input.current.value = ''
    }
  }

  function dropPhoto(i: number) {
    setQueue((q) => {
      URL.revokeObjectURL(q[i]?.url || '')
      return q.filter((_, k) => k !== i)
    })
  }

  async function send() {
    if (!queue.length) return
    setErr(null); setBusy(true); setVerdict(null)
    try {
      const r = await fetch(`/api/mountain/checklist/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot, zone, photos: queue.map((q) => q.b64) }),
      })
      const d = await r.json()
      if (!r.ok || d.error) setErr(d.error || `HTTP ${r.status}`)
      else {
        setVerdict(d.review || { checked: false })
        queue.forEach((q) => URL.revokeObjectURL(q.url))
        setQueue([])
        load()
      }
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  const sent = (s: string, key: string) => sentIn(done, s, key)
  const slotCount = (s: string) => ZONES.filter(([k]) => sent(s, k)).length
  /* Зелёный = сдано И ЧИСТО. Раньше зелёным красилось «кадры присланы», и
     филиал, снявший загаженный санузел, получал ровно тот же зелёный, что и
     вымытый. Разбор нашёл грязь — цвет красный, сколько бы кадров ни прислали. */
  const verdictOf = (s: string, key: string) => zoneVerdict(reviews, s, key)
  const zoneColor = (s: string, key: string) => {
    if (!sent(s, key)) return 'var(--mute)'
    return verdictOf(s, key) === 'dirty' ? 'var(--red)' : 'var(--grn)'
  }
  // Замечания за сегодня по всем слотам — в одном месте, свежие сверху
  const problems = reviews.filter((r) => !r.ok && (r.issues || []).length)

  const chip = (on: boolean, extra?: React.CSSProperties): React.CSSProperties => ({
    padding: '7px 13px', borderRadius: 11, fontSize: 12.5, fontWeight: 650, cursor: 'pointer',
    border: '1px solid var(--hair)', fontFamily: 'inherit',
    background: on ? 'var(--glass-2)' : 'transparent',
    color: on ? 'var(--hi)' : 'var(--mute)', ...extra,
  })

  return (
    <Glass span={12}>
      <div className="eyebrow"><span>Сдать чек-лист</span>
        <span className="hint">кадры сжимаются в браузере · до {MAX_PHOTOS} на зону</span></div>

      {/* Слоты: рядом с временем — сколько зон из трёх уже сдано. Без счётчика
          приходилось открывать каждый слот, чтобы понять, где дыра. */}
      <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {SLOTS.map((s) => {
          const n = slotCount(s)
          return (
            <button key={s} onClick={() => setSlot(s)} style={chip(slot === s)}>
              {s}
              <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 700,
                             color: ZONES.some(([k]) => verdictOf(s, k) === 'dirty') ? 'var(--red)'
                                    : n === ZONES.length ? 'var(--grn)' : n ? 'var(--amb)' : 'var(--mute)' }}>
                {n}/{ZONES.length}
              </span>
            </button>
          )
        })}
      </div>

      {/* Зоны выбранного слота: галочка прямо на кнопке — видно, что уже сдано,
          не заглядывая в таблицу ниже. */}
      <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {ZONES.map(([k, label]) => {
          const d = sent(slot, k)
          const dirty = verdictOf(slot, k) === 'dirty'
          const col = zoneColor(slot, k)
          return (
            <button key={k} onClick={() => setZone(k)}
              style={chip(zone === k, d ? { borderColor: col } : undefined)}>
              {d && <span style={{ color: col, marginRight: 5, fontWeight: 800 }}>{dirty ? '✕' : '✓'}</span>}
              {label}
              {d && <span style={{ marginLeft: 5, fontSize: 11, color: dirty ? 'var(--red)' : 'var(--mute)' }}>
                {dirty ? 'есть замечания' : d.at}
              </span>}
            </button>
          )
        })}
      </div>

      {/* Что именно снимать: без этого «сдал чистоту» превращается в пять кадров потолка */}
      {/* Критерии: до разбора — «что снять», после — ответ модели по КАЖДОМУ
          пункту. Список один и тот же, поэтому видно, что именно зачли. */}
      {criteria[zone] && (
        <div style={{ marginTop: 12, padding: '11px 13px', borderRadius: 12, border: '1px solid var(--hair)', background: 'var(--glass-2)' }}>
          <div style={{ fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 700 }}>
            {criteria[zone].label} · {verdictOf(slot, zone) ? 'разбор по пунктам' : 'что снять'}
          </div>
          <div style={{ marginTop: 7, display: 'grid', gap: 5 }}>
            {zoneCriteria(reviews, slot, zone, criteria[zone].items).map((c) => (
              <div key={c.item} style={{ fontSize: 12.5, color: 'var(--hi)', display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <span style={{ flex: 'none', width: 12, fontWeight: 800,
                               color: c.ok === null ? 'var(--mute)' : c.ok ? 'var(--grn)' : 'var(--red)' }}>
                  {c.ok === null ? '•' : c.ok ? '✓' : '✕'}
                </span>
                <span>
                  {c.item}
                  {c.ok === false && c.what && (
                    <span style={{ color: 'var(--red)' }}> — {c.what}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Кнопку не должно вести: подпись меняет длину на каждый тап по зоне и
          ещё раз на «Загружаю…». Постоянная ширина + состояние своей строкой. */}
      {/* Превью снятого: пока кадры в очереди, их видно и лишний можно убрать. */}
      {queue.length > 0 && (
        <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {queue.map((q, i) => (
            <span key={q.url} style={{ position: 'relative', display: 'inline-block' }}>
              <img src={q.url} alt={`кадр ${i + 1}`}
                style={{ width: 68, height: 68, objectFit: 'cover', borderRadius: 10, border: '1px solid var(--hair)', display: 'block' }} />
              <button onClick={() => dropPhoto(i)} aria-label={`убрать кадр ${i + 1}`}
                style={{ position: 'absolute', top: -6, right: -6, width: 20, height: 20, borderRadius: '50%', border: 'none', background: 'var(--red)', color: '#fff', fontSize: 12, fontWeight: 800, lineHeight: 1, cursor: 'pointer', fontFamily: 'inherit' }}>×</button>
            </span>
          ))}
        </div>
      )}

      {/* Кнопку не должно вести: подписи меняют длину, поэтому ширина постоянная. */}
      <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={() => input.current?.click()} disabled={busy || queue.length >= MAX_PHOTOS}
          style={{ minWidth: 150, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: '11px 18px', borderRadius: 14, border: '1px solid var(--hair)', background: 'transparent', color: queue.length >= MAX_PHOTOS ? 'var(--mute)' : 'var(--hi)', fontSize: 13.5, fontWeight: 700, fontFamily: 'inherit', cursor: queue.length >= MAX_PHOTOS ? 'default' : 'pointer' }}>
          {queue.length ? `Ещё кадр · ${queue.length}/${MAX_PHOTOS}` : 'Снять кадр'}
        </button>
        <input ref={input} type="file" accept="image/*" multiple capture="environment" hidden
          onChange={(e) => addPhotos(e.target.files)} />
        <button onClick={send} disabled={busy || !queue.length}
          style={{ minWidth: 208, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: '11px 18px', borderRadius: 14, border: 'none', background: queue.length ? 'linear-gradient(135deg,var(--acc),var(--acc-2))' : 'var(--hair)', color: queue.length ? 'var(--on-acc)' : 'var(--mute)', fontSize: 13.5, fontWeight: 700, fontFamily: 'inherit', cursor: busy || !queue.length ? 'default' : 'pointer', opacity: busy ? .6 : 1 }}>
          {busy ? 'Отправляю…' : `Сдать ${slot} · ${ZONES.find(([k]) => k === zone)?.[1]}`}
        </button>
      </div>
      <div style={{ minHeight: 18, marginTop: 7, fontSize: 12.5, fontWeight: 650,
                    color: verdictOf(slot, zone) === 'dirty' ? 'var(--red)' : 'var(--grn)' }}>
        {sent(slot, zone)
          ? (verdictOf(slot, zone) === 'dirty'
              ? `сдано в ${sent(slot, zone)?.at}, но разбор нашёл замечания`
              : `уже сдано в ${sent(slot, zone)?.at}`)
          : queue.length ? `${queue.length} из ${MAX_PHOTOS} кадров готово к отправке` : ''}
      </div>

      {err && <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--red)' }}>{err}</div>}

      {/* Вердикт разбора сразу после отправки — ради него всё и снимают */}
      {verdict && (
        <div style={{ marginTop: 12, padding: '11px 13px', borderRadius: 12,
                      border: `1px solid ${verdict.checked ? (verdict.ok ? 'var(--grn)' : 'var(--red)') : 'var(--hair)'}`,
                      background: 'var(--glass-2)' }}>
          {verdict.checked ? (
            verdict.ok ? (
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--grn)' }}>Разбор: замечаний нет</div>
            ) : (
              <>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--red)' }}>Разбор нашёл, что поправить</div>
                <div style={{ marginTop: 6, display: 'grid', gap: 4 }}>
                  {(verdict.issues || []).map((it, i) => (
                    <div key={i} style={{ fontSize: 12.5, color: 'var(--hi)', display: 'flex', gap: 8 }}>
                      <span style={{ color: 'var(--red)' }}>•</span>{issueText(it)}
                    </div>
                  ))}
                </div>
              </>
            )
          ) : (
            /* Разбор не гоняли — так и говорим. Молчание читалось как «всё хорошо». */
            <div style={{ fontSize: 12.5, color: 'var(--mute)' }}>
              Кадры приняты. Разбор не запускался{verdict.reason ? `: ${verdict.reason}` : ''}
            </div>
          )}
        </div>
      )}

      {/* Замечания за сегодня — что плохо по всем слотам, а не только по последнему */}
      {problems.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 700 }}>
            Замечания за сегодня
          </div>
          <div style={{ marginTop: 7, display: 'grid', gap: 5 }}>
            {problems.map((r, i) => (
              <div key={`${r.slot}${i}`} style={{ display: 'flex', gap: 9, alignItems: 'baseline', fontSize: 12.5 }}>
                <span className="tnum" style={{ color: 'var(--mute)', flex: 'none' }}>{r.slot}</span>
                <span style={{ color: 'var(--red)' }}>{(r.issues || []).map(issueText).join(' · ')}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* что сдано сегодня: слоты × зоны */}
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 16, fontSize: 12.5 }}>
        <thead><tr>
          <th style={{ textAlign: 'left', padding: '0 0 8px', fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>Слот</th>
          {ZONES.map(([, label]) => (
            <th key={label} style={{ textAlign: 'center', padding: '0 0 8px', fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600, borderBottom: '1px solid var(--hair)' }}>{label}</th>
          ))}
        </tr></thead>
        <tbody>
          {SLOTS.map((s) => (
            <tr key={s}>
              <td className="tnum" style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)', color: 'var(--hi)' }}>{s}</td>
              {ZONES.map(([k]) => {
                const d = sent(s, k)
                return (
                  <td key={k} style={{ padding: '9px 0', borderBottom: '1px solid var(--hair-2)', textAlign: 'center', color: zoneColor(s, k) }}>
                    {d ? `${verdictOf(s, k) === 'dirty' ? '✕' : '✓'} ${d.photos} · ${d.at}` : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Glass>
  )
}
