import { StrictMode, Suspense, lazy, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './lg/lg.css'
import App from './lg/LiquidApp.tsx'
import { getStaffCode, getJoinToken, loadPref, savePref } from './lib/api'
import { AppFallback } from './lg/ui'

/* Приложений внутри одного бандла три, и решает адрес.

   `?j=…` — QR филиала из подсобки: человек один раз выбирает себя и получает
   личную ссылку.
   `?e=…` — личная ссылка СОТРУДНИКА: он видит только свои смены, оплату и
   сообщения. Кабинет ему не показываем даже пустым: там переключатель филиалов,
   выручка и склад, а его ручки на сервере (/api/me/*) org_id вообще не
   принимают. Разводим на входе, а не внутри кабинета, чтобы «сотрудник случайно
   увидел не своё» было невозможно по устройству, а не по забытому условию. */
const MyShift = lazy(() => import('./lg/MyShift.tsx'))
const Join = lazy(() => import('./lg/Join.tsx'))

/** Тема для мини-аппа смен: у кабинета её переключает шапка, а здесь шапки нет,
    поэтому просто уважаем прошлый выбор и системную настройку телефона. */
function StaffApp() {
  const [dark] = useState(() => {
    const saved = loadPref('theme')
    if (saved) return saved === 'dark'
    return typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-color-scheme: dark)').matches
  })
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    savePref('theme', dark ? 'dark' : 'light')
  }, [dark])
  // Пока человек не выбрал себя, показываем список; после выбора переключаемся
  // на его смены ЗДЕСЬ ЖЕ. Перезагрузка со сменой адреса потеряла бы подпись
  // Telegram — она приезжает в хеше и живёт только до навигации.
  const [code, setCode] = useState(() => getStaffCode())
  const join = getJoinToken()
  return (
    <Suspense fallback={<AppFallback />}>
      {join && !code ? <Join token={join} onDone={setCode} /> : <MyShift />}
    </Suspense>
  )
}

const Root = getStaffCode() || getJoinToken() ? StaffApp : App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
