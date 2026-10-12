import { useEffect, useState } from 'react'
import { apiGet, getOrg, onOrgChange, getPeriod, onPeriodChange } from './api'

export function useApi<T>(path: string, params: Record<string, string> = {}) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [org, setOrgState] = useState(getOrg())
  const [period, setPeriodState] = useState(getPeriod())

  // перезапрос при смене филиала и глобального периода
  useEffect(() => onOrgChange(() => setOrgState(getOrg())), [])
  useEffect(() => onPeriodChange(() => setPeriodState(getPeriod())), [])

  // period не передан вызовом → следуем глобальному фильтру; передан явно (напр. 'today') → фиксирован
  const followsGlobal = params.period === undefined
  const effParams = followsGlobal ? { ...params, period } : params
  const key = org + '|' + path + JSON.stringify(effParams)

  useEffect(() => {
    if (!org) return // филиал ещё не выбран — ждём, не показываем демо (loading остаётся true)
    const request = new AbortController()
    let alive = true
    setLoading(true)
    setError(null)
    apiGet<T>(path, effParams, request.signal)
      .then((d) => alive && setData(d))
      .catch((e) => alive && e?.name !== 'AbortError' && setError(String(e.message || e)))
      .finally(() => alive && setLoading(false))
    return () => { alive = false; request.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return { data, error, loading }
}

/** Текущий филиал с подпиской на переключатель.

    getOrg() сам по себе — снимок на момент рендера: экран, который читает его
    напрямую и ходит в fetch мимо useApi, после смены филиала показывал данные
    прошлого (а «План» ещё и сохранял правки не в тот филиал). Экранам вне
    useApi нужен именно этот хук. */
export function useOrg(): string {
  const [org, setOrgState] = useState(getOrg())
  useEffect(() => onOrgChange(() => setOrgState(getOrg())), [])
  return org
}
