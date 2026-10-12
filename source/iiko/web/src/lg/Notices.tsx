import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

/* Уведомление о сообщении владельца.

   Раньше плашка жила внутри «Горы» — то есть появлялась, только пока открыт
   именно этот раздел. Управляющий, оставивший сайт на «Главной» или «Складе»,
   не видел сообщения вообще: экран не смонтирован — опрашивать некому. Поэтому
   центр уведомлений висит на уровне приложения, рядом с доком, и работает на
   любом разделе.

   Плашка — только управляющему: владелец сам эти сообщения и пишет. */

export type Notice = {
  id: number; text: string; created_at?: string; organization_id?: string
  is_read?: number | null; read_by?: number; branch?: string
}

/* Звук уведомления. Файл не тянем — два коротких тона на WebAudio: это пара
   килобайт кода вместо мегабайта mp3 и не зависит от сети. Браузеры не дают
   звучать до первого действия пользователя, поэтому контекст один на страницу
   и будится любым первым касанием, а выключатель живёт в localStorage. */
let audioCtx: AudioContext | null = null

export function unlockAudio() {
  try {
    const AC = window.AudioContext
      || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    if (!audioCtx) audioCtx = new AC()
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {})
  } catch { /* звук не критичен */ }
}

export function chime(): boolean {
  try {
    if (localStorage.getItem('lg:mute') === '1') return false
    unlockAudio()
    const ctx = audioCtx
    if (!ctx || ctx.state !== 'running') return false
    const now = ctx.currentTime
    ;[[880, 0], [1320, 0.13]].forEach(([freq, at]) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      // мягкая атака и затухание: щелчок на старте резал бы ухо
      gain.gain.setValueAtTime(0.0001, now + at)
      gain.gain.exponentialRampToValueAtTime(0.16, now + at + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.22)
      osc.connect(gain); gain.connect(ctx.destination)
      osc.start(now + at); osc.stop(now + at + 0.24)
    })
    // Контекст НЕ закрываем: он один на страницу и нужен следующему звонку.
    return true
  } catch { /* звук не критичен: молча пропускаем */ }
  return false
}

/* По apple-design: полупрозрачный материал с блюром (не плоская плашка),
   появление пружиной от края — оттуда же и уходит. При prefers-reduced-motion
   простое проявление без сдвига.

   ВАЖНО про центрирование: motion сам пишет в style.transform (translateY,
   scale), и статический `translateX(-50%)` он затирал — плашка вставала левым
   краем на середину экрана и на телефоне почти целиком уезжала за правый край.
   Поэтому центруем без transform: left/right 0 + auto-поля. */
export function NoticeToast({ notice, onRead }: { notice: Notice; onRead: () => void }) {
  const reduce = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const [muted, setMuted] = useState(() => {
    try { return localStorage.getItem('lg:mute') === '1' } catch { return false }
  })
  return (
    <motion.div
      role="status" aria-live="polite"
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: -18, scale: .97 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, y: -18, scale: .97 }}
      transition={{ type: 'spring', bounce: 0, duration: 0.4 }}
      style={{
        position: 'fixed', top: 18, left: 0, right: 0, marginInline: 'auto',
        zIndex: 60, width: 'min(480px, calc(100% - 32px))', boxSizing: 'border-box',
        padding: '14px 16px', borderRadius: 18,
        background: 'rgba(18,24,40,.72)', backdropFilter: 'blur(22px) saturate(180%)',
        WebkitBackdropFilter: 'blur(22px) saturate(180%)',
        border: '1px solid rgba(255,255,255,.16)',
        boxShadow: '0 22px 48px -18px rgba(6,12,28,.75)',
      }}>
      <div style={{ fontSize: 11, letterSpacing: '.1em', textTransform: 'uppercase', color: 'rgba(255,255,255,.62)', fontWeight: 700 }}>
        Сообщение владельца
      </div>
      <div style={{ marginTop: 6, fontSize: 15, lineHeight: 1.35, color: '#fff', fontWeight: 600, letterSpacing: '-.01em', overflowWrap: 'anywhere' }}>
        {notice.text}
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button onClick={onRead}
          style={{ padding: '8px 14px', borderRadius: 11, border: '1px solid rgba(255,255,255,.22)', background: 'rgba(255,255,255,.10)', color: '#fff', fontSize: 12.5, fontWeight: 650, cursor: 'pointer', fontFamily: 'inherit' }}>
          Прочитал
        </button>
        <button onClick={() => {
            const next = !muted
            try { localStorage.setItem('lg:mute', next ? '1' : '0') } catch { /* приватный режим */ }
            setMuted(next)
            if (!next) chime()          // включили — сразу слышно, что именно включили
          }}
          title="звук уведомлений"
          aria-pressed={muted}
          style={{ marginLeft: 'auto', padding: '8px 12px', borderRadius: 11, border: '1px solid rgba(255,255,255,.18)', background: 'transparent', color: 'rgba(255,255,255,.75)', fontSize: 12.5, cursor: 'pointer', fontFamily: 'inherit' }}>
          {muted ? 'звук выкл' : 'звук'}
        </button>
      </div>
    </motion.div>
  )
}

/** Самое старое непрочитанное: сервер отдаёт список от новых к старым, а
    читать сообщения логично в том порядке, в каком их писали. */
export function oldestUnread(list: Notice[]): Notice | undefined {
  return list.filter((n) => !n.is_read).slice(-1)[0]
}

/** Какие из непрочитанных ещё не звонили. Звонок вешаем на ЛЮБОЕ новое
    сообщение, а не на самое старое: то уже прозвучало при открытии страницы и
    так и оставалось первым — новое не меняло его и звука не было. */
export function freshIds(list: Notice[], chimed: number[]): number[] {
  return list.filter((n) => !n.is_read).map((n) => n.id).filter((id) => id && !chimed.includes(id))
}

const CHIMED_KEY = 'lg:chimed'
const readChimed = (): number[] => {
  try { return JSON.parse(localStorage.getItem(CHIMED_KEY) || '[]') } catch { return [] }
}

/** Центр уведомлений: висит на уровне приложения, опрашивает сервер и рисует
    плашку поверх любого раздела. Токен — из личной ссылки (?m=); без неё это
    кабинет владельца, а владельцу свои же сообщения показывать незачем. */
export default function NoticeCenter() {
  const [notices, setNotices] = useState<Notice[]>([])
  const [ok, setOk] = useState(false)          // это управляющий по живой ссылке
  const token = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search).get('m') || ''
    : ''

  useEffect(() => {
    if (!token) return
    let alive = true
    fetch(`/api/mountain/whoami/${token}`, { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && setOk(d?.role === 'manager'))
      .catch(() => {})
    return () => { alive = false }
  }, [token])

  // Короткий опрос вместо сокета: сообщений единицы в день, а держать
  // соединение на девять филиалов дороже, чем спросить раз в 15 секунд.
  // Плюс догоняем при возврате на вкладку — телефон усыпляет таймеры.
  useEffect(() => {
    if (!ok || !token) return
    const tick = () => {
      fetch(`/api/mountain/notices/${token}`, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { notices?: Notice[] } | null) => { if (d) setNotices(d.notices || []) })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 15000)
    const onVisible = () => { if (document.visibilityState === 'visible') tick() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', onVisible) }
  }, [ok, token])

  // Разбудить звук на первом же действии пользователя — любом, а не только на
  // кнопке «звук»: браузер не даёт звучать до взаимодействия со страницей.
  useEffect(() => {
    const wake = () => {
      unlockAudio()
      window.removeEventListener('pointerdown', wake)
      window.removeEventListener('keydown', wake)
    }
    window.addEventListener('pointerdown', wake, { once: true })
    window.addEventListener('keydown', wake, { once: true })
    return () => {
      window.removeEventListener('pointerdown', wake)
      window.removeEventListener('keydown', wake)
    }
  }, [])

  const fresh = freshIds(notices, readChimed())
  const freshKey = fresh.join(',')
  useEffect(() => {
    if (!freshKey) return
    if (!chime()) return          // звук ещё не разбужен — прозвенит на следующем опросе
    const ids = freshKey.split(',').map(Number)
    try {
      localStorage.setItem(CHIMED_KEY, JSON.stringify([...readChimed(), ...ids].slice(-50)))
    } catch { /* приватный режим */ }
  }, [freshKey])

  async function read(id: number) {
    setNotices((prev) => prev.map((n) => (n.id === id ? { ...n, is_read: 1 } : n)))
    await fetch(`/api/mountain/notice/${token}/read/${id}`, { method: 'POST' }).catch(() => {})
  }

  const shown = oldestUnread(notices)
  return (
    <AnimatePresence>
      {shown && <NoticeToast key={shown.id} notice={shown} onRead={() => read(shown.id)} />}
    </AnimatePresence>
  )
}
