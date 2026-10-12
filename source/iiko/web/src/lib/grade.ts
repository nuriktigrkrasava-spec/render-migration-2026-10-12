/** Балл 0–100 → буквенная оценка. Используется на экранах «Оценка». */
export function gradeOf(score: number): string {
  const s = Math.max(0, Math.min(100, score))
  if (s >= 90) return 'A'
  if (s >= 85) return 'B+'
  if (s >= 75) return 'B'
  if (s >= 70) return 'C+'
  if (s >= 60) return 'C'
  if (s >= 50) return 'D'
  return 'E'
}
