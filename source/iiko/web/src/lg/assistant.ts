export type AdviceAction = { type: 'navigate' | 'showMetric'; tab: string; hint?: string | null }
// 'good' — похвала за результат (план недели выполнен / показатель вырос),
// остальные — проблемы по убыванию тяжести
export type Advice = {
  id: string; branch: string; severity: 'good' | 'high' | 'med' | 'low'
  title: string; detail: string; money: number | null; action?: AdviceAction | null
}
export type AssistantResp = { advice: Advice[]; count: number; demo: boolean }

// белый список действий — исполняем только известные типы
export function runAction(a: AdviceAction): void {
  if (a.type === 'navigate' || a.type === 'showMetric') {
    window.dispatchEvent(new CustomEvent('lg:go', { detail: a.tab }))
    // showMetric: подсветка целевого показателя — Фаза 3 (пока просто навигация)
  }
}
