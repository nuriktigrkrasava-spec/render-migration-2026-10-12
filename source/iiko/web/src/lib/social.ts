import { useState, useEffect, useCallback } from 'react'
import { getOrg } from './api'

/* Подключение Instagram Business через Meta OAuth — общий хук для
   карточки в «Соцсети» и блока онбординга. Всё self-serve, по текущему org. */
export type IgData = {
  connected: boolean
  configured?: boolean
  stale?: boolean
  username?: string
  followers?: number
  media_count?: number
  delta_30d?: number | null
}

export function useInstagram() {
  const [data, setData] = useState<IgData | null>(null)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    fetch('/api/social/instagram/' + getOrg(), { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => j && setData(j))
      .catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (!e?.data || e.data.source !== 'ig-oauth') return
      if (e.data.ok) { setMsg(''); load() } else setMsg('Подключение отменено.')
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [load])

  const connect = useCallback(async () => {
    setMsg('')
    try {
      const r = await fetch('/api/social/instagram/connect/' + getOrg(), { headers: { Accept: 'application/json' } })
      const j = await r.json()
      if (!j.configured) { setMsg(j.reason || 'Подключение Instagram скоро появится.'); return }
      window.open(j.url, 'ig-oauth', 'width=600,height=760')
    } catch { setMsg('Не удалось начать подключение.') }
  }, [])

  return { data, msg, connect }
}
