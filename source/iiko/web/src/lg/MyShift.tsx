import { useCallback, useEffect, useState } from 'react'
import { Glass, tg } from './ui'

/* Мини-апп смен — то, что видит РЯДОВОЙ СОТРУДНИК по личной ссылке (?e=…).

   Это отдельное приложение внутри того же бандла, а не раздел кабинета: у
   сотрудника нет доступа ни к выручке, ни к складу, ни к чужим зарплатам, и
   на сервере его ручки (/api/me/*) вообще не принимают org_id — филиал берётся
   из кода ссылки. Поэтому здесь нет ни переключателя филиалов, ни навигации.

   Геопозиция спрашивается перед открытием смены, но НЕ блокирует его: GPS в
   помещении врёт на сотни метров, и повар, застрявший на экране «вы не на
   месте», пойдёт звонить управляющему вместо того, чтобы готовить. Промах
   уезжает на сервер пометкой, её видит управляющий. */

type Me = { name: string; employee_id: string; branch: string; geo_required: boolean }
type Day = { date: string; start: string; end: string; pay: number | null; opened: string; geo_ok: number | null }
type Week = { from: string; to: string; days: Day[]; pay_total: number; today: string }
type Notice = { id: number; text: string; created_at: string; personal: boolean; is_read: boolean }
type Payout = { day: string; amount: number; comment: string }
type Pay = { month: string; earned: number; paid: number; balance: number;
             shifts: { date: string; start: string; end: string; pay: number }[];
             payouts: Payout[] }

const DOW = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const dowOf = (iso: string) => DOW[(new Date(iso + 'T00:00:00').getDay() + 6) % 7]
const dayNum = (iso: string) => Number(iso.slice(8, 10))

/** Координаты с телефона. Отказ, выключенный GPS и таймаут дают один результат —
    null: сервер разберёт это как «проверить нечем» и смену всё равно откроет. */
function askPosition(): Promise<{ lat: number; lon: number } | null> {
  return new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) return resolve(null)
    let done = false
    const finish = (v: { lat: number; lon: number } | null) => { if (!done) { done = true; resolve(v) } }
    // Свой таймаут поверх штатного: на части устройств колбэк не приходит вовсе,
    // и кнопка «Открыть смену» висела бы в «Определяю…» до перезагрузки.
    setTimeout(() => finish(null), 12000)
    navigator.geolocation.getCurrentPosition(
      (p) => finish({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => finish(null),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    )
  })
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)
  return r.json() as Promise<T>
}

function Card({ children, pad = 16 }: { children: React.ReactNode; pad?: number }) {
  return <Glass span={12}><div style={{ padding: pad }}>{children}</div></Glass>
}

/* ── Кнопка открытия смены ── */
function OpenShift({ today, onOpened }: { today: Day | undefined; onOpened: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const open = async () => {
    setBusy(true); setErr('')
    try {
      const pos = await askPosition()
      const r = await fetch('/api/me/open-shift', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pos ?? {}),
      })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)
      onOpened()
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  if (today?.opened) {
    const off = today.geo_ok === 0
    return (
      <Card>
        <div style={{ fontSize: 13, color: 'var(--mute)' }}>Смена открыта</div>
        <div className="tnum" style={{ fontSize: 40, fontWeight: 680, letterSpacing: '-.03em', marginTop: 4, color: 'var(--grn)' }}>
          {today.opened}
        </div>
        {today.start && (
          <div style={{ fontSize: 12.5, color: 'var(--mute)', marginTop: 6 }}>по графику с {today.start}</div>
        )}
        {off && (
          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--amb)' }}>
            Отмечено вне заведения — управляющий это увидит.
          </div>
        )}
      </Card>
    )
  }

  return (
    <Card>
      <div style={{ fontSize: 13, color: 'var(--mute)' }}>
        {today?.start ? `Сегодня по графику с ${today.start}` : 'На сегодня смены в графике нет'}
      </div>
      <button
        onClick={open} disabled={busy}
        style={{
          width: '100%', marginTop: 14, padding: '18px 16px', borderRadius: 16,
          border: '1px solid var(--grn)', background: 'color-mix(in srgb, var(--grn) 14%, transparent)',
          color: 'var(--grn)', fontSize: 17, fontWeight: 660, cursor: busy ? 'default' : 'pointer',
          opacity: busy ? 0.6 : 1,
        }}
      >
        {busy ? 'Определяю, где вы…' : 'Открыть смену'}
      </button>
      <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--mute)' }}>
        Приложение спросит геопозицию — она подтверждает, что вы на месте.
      </div>
      {err && <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--red)' }}>{err}</div>}
    </Card>
  )
}

/* ── Неделя с оплатой ── */
function MyWeek({ week }: { week: Week }) {
  const cell: React.CSSProperties = { padding: '11px 0', borderTop: '1px solid var(--hair-2)', fontSize: 13 }
  return (
    <Card>
      <div className="eyebrow" style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>Мои смены</span>
        <span className="hint">{week.from.slice(5)} – {week.to.slice(5)}</span>
      </div>
      <div style={{ marginTop: 8 }}>
        {week.days.map((d) => {
          const isToday = d.date === week.today
          const has = Boolean(d.start)
          return (
            <div key={d.date} style={{ ...cell, display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ width: 52, color: isToday ? 'var(--acc)' : 'var(--mute)', fontWeight: isToday ? 700 : 500 }}>
                {dowOf(d.date)} {dayNum(d.date)}
              </span>
              <span className="tnum" style={{ flex: 1, color: has ? 'var(--hi)' : 'var(--mute)' }}>
                {has ? `${d.start}–${d.end || '…'}` : 'выходной'}
              </span>
              {d.opened && (
                <span className="tnum" style={{ fontSize: 11.5, color: d.geo_ok === 0 ? 'var(--amb)' : 'var(--grn)' }}>
                  пришёл {d.opened}
                </span>
              )}
              <span className="tnum" style={{ minWidth: 76, textAlign: 'right', color: d.pay == null ? 'var(--mute)' : 'var(--hi)', fontWeight: 600 }}>
                {d.pay == null ? '—' : tg(d.pay)}
              </span>
            </div>
          )
        })}
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--hair)', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: 13, color: 'var(--mute)' }}>За неделю</span>
        <span className="tnum" style={{ fontSize: 22, fontWeight: 680, color: 'var(--hi)' }}>
          {week.pay_total ? tg(week.pay_total) : '—'}
        </span>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--mute)' }}>
        Считаются только смены, где оплата уже проставлена управляющим.
      </div>
    </Card>
  )
}

/* ── Зарплата: начислено, выдано, остаток ──
   Одна цифра «за неделю» на вопрос «сколько мне должны» не отвечает: человек
   держит в голове, что часть уже получил авансом. Поэтому три числа и история
   выдач — чтобы спор с управляющим решался экраном, а не памятью. */
function Wallet({ pay }: { pay: Pay }) {
  const owed = pay.balance > 0
  return (
    <Card>
      <div className="eyebrow">
        <span>Зарплата</span>
        <span className="hint">{pay.month}</span>
      </div>

      <div style={{ marginTop: 10, fontSize: 13, color: 'var(--mute)' }}>
        {owed ? 'К выплате' : pay.balance < 0 ? 'Выдано авансом' : 'Всё выплачено'}
      </div>
      <div className="tnum" style={{
        fontSize: 38, fontWeight: 680, letterSpacing: '-.03em', marginTop: 2,
        color: owed ? 'var(--hi)' : 'var(--grn)',
      }}>
        {tg(Math.abs(pay.balance))}
      </div>

      <div style={{ display: 'flex', gap: 18, marginTop: 14, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--mute)' }}>Начислено</div>
          <div className="tnum" style={{ fontSize: 17, fontWeight: 620, color: 'var(--hi)' }}>{tg(pay.earned)}</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--mute)' }}>Выдано</div>
          <div className="tnum" style={{ fontSize: 17, fontWeight: 620, color: 'var(--grn)' }}>{tg(pay.paid)}</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--mute)' }}>Смен с оплатой</div>
          <div className="tnum" style={{ fontSize: 17, fontWeight: 620, color: 'var(--hi)' }}>{pay.shifts.length}</div>
        </div>
      </div>

      {pay.payouts.length > 0 && (
        <>
          <div style={{ marginTop: 16, fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--mute)', fontWeight: 600 }}>
            История выплат
          </div>
          {pay.payouts.map((p, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '10px 0', borderTop: '1px solid var(--hair-2)' }}>
              <span style={{ width: 52, fontSize: 12, color: 'var(--mute)' }}>{p.day.slice(8)}.{p.day.slice(5, 7)}</span>
              <span style={{ flex: 1, fontSize: 12.5, color: 'var(--lo)' }}>{p.comment || 'выплата'}</span>
              <span className="tnum" style={{ fontSize: 13.5, fontWeight: 620, color: 'var(--grn)' }}>{tg(p.amount)}</span>
            </div>
          ))}
        </>
      )}

      {!pay.earned && !pay.paid && (
        <div style={{ marginTop: 12, fontSize: 12, color: 'var(--mute)' }}>
          За этот месяц пока ничего не начислено — управляющий проставляет оплату на смене.
        </div>
      )}
    </Card>
  )
}

/* ── Сообщения ── */
function Messages({ notices, onRead }: { notices: Notice[]; onRead: (id: number) => void }) {
  if (!notices.length) return null
  return (
    <Card>
      <div className="eyebrow"><span>Сообщения</span></div>
      <div style={{ marginTop: 8, display: 'grid', gap: 10 }}>
        {notices.map((n) => (
          <div key={n.id} style={{
            padding: 12, borderRadius: 13, border: '1px solid var(--hair)',
            background: n.is_read ? 'transparent' : 'var(--glass-2)',
          }}>
            {n.personal && (
              <div style={{ fontSize: 10, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--acc)', fontWeight: 700, marginBottom: 6 }}>
                лично вам
              </div>
            )}
            <div style={{ fontSize: 14, color: 'var(--hi)', whiteSpace: 'pre-wrap' }}>{n.text}</div>
            <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 11, color: 'var(--mute)' }}>{(n.created_at || '').slice(5, 16).replace('T', ' ')}</span>
              {n.is_read
                ? <span style={{ fontSize: 11.5, color: 'var(--mute)' }}>прочитано</span>
                : <button onClick={() => onRead(n.id)} style={{
                    border: '1px solid var(--grn)', color: 'var(--grn)', background: 'transparent',
                    borderRadius: 9, padding: '6px 12px', fontSize: 12.5, cursor: 'pointer',
                  }}>Прочитал</button>}
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function MyShift() {
  const [me, setMe] = useState<Me | null>(null)
  const [week, setWeek] = useState<Week | null>(null)
  const [notices, setNotices] = useState<Notice[]>([])
  const [pay, setPay] = useState<Pay | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [m, w, n, p] = await Promise.all([
        getJSON<Me>('/api/me'),
        getJSON<Week>('/api/me/week'),
        getJSON<{ notices: Notice[] }>('/api/me/notices'),
        getJSON<Pay>('/api/me/pay'),
      ])
      setMe(m); setWeek(w); setNotices(n.notices); setPay(p)
      setError('')
    } catch (e) {
      setError(String((e as Error).message || e))
    }
  }, [])

  useEffect(() => { load() }, [load])

  const read = async (id: number) => {
    setNotices((xs) => xs.map((n) => (n.id === id ? { ...n, is_read: true } : n)))
    await fetch(`/api/me/notices/${id}/read`, { method: 'POST' }).catch(() => { /* поправится при обновлении */ })
  }

  if (error) {
    return (
      <div style={{ padding: 20, maxWidth: 560, margin: '0 auto' }}>
        <Card><div style={{ fontSize: 14, color: 'var(--red)' }}>{error}</div>
          <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--mute)' }}>
            Если ссылка перестала работать — попросите управляющего выдать новую.
          </div>
        </Card>
      </div>
    )
  }
  if (!me || !week) {
    return <div style={{ padding: 20, color: 'var(--mute)', fontSize: 14 }}>Загружаю…</div>
  }

  const today = week.days.find((d) => d.date === week.today)
  const unread = notices.some((n) => !n.is_read)
  return (
    <div style={{ padding: '18px 14px 40px', maxWidth: 560, margin: '0 auto', display: 'grid', gap: 14 }}>
      <div style={{ padding: '0 4px' }}>
        <div style={{ fontSize: 22, fontWeight: 680, letterSpacing: '-.02em', color: 'var(--hi)' }}>{me.name || 'Смена'}</div>
        <div style={{ fontSize: 12.5, color: 'var(--mute)', marginTop: 2 }}>{me.branch}</div>
      </div>
      <OpenShift today={today} onOpened={load} />
      {/* Непрочитанное поднимаем над графиком: сообщение управляющего обычно
          про сегодня, а график — справочник, к нему возвращаются. */}
      {unread ? <Messages notices={notices} onRead={read} /> : null}
      {pay && <Wallet pay={pay} />}
      <MyWeek week={week} />
      {unread ? null : <Messages notices={notices} onRead={read} />}
    </div>
  )
}
