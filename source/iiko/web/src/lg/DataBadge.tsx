import { useEffect, useState } from 'react'
import { onOrgChange, isDemoData } from '../lib/api'

/* Бейдж источника данных: ДЕМО (офлайн/демо-филиал) или LIVE (реальный iiko).
   Чтобы не путаться, где показуха, а где настоящие цифры. */
export default function DataBadge() {
  const [demo, setDemo] = useState(isDemoData())
  useEffect(() => onOrgChange(() => setDemo(isDemoData())), [])
  return (
    <span className={`lg-data-badge ${demo ? 'demo' : 'live'}`} title={demo ? 'Показаны демо-данные' : 'Реальные данные iiko'}>
      <span className="dot" />{demo ? 'ДЕМО-ДАННЫЕ' : 'LIVE · iiko'}
    </span>
  )
}
