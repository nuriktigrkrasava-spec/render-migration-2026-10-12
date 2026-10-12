export interface CompletedPartScore {
  score: number;
}

export interface BookProgress {
  percent: number;
  averageScore: number;
  completedParts: number;
}

export function computeBookProgress(
  totalParts: number,
  completed: CompletedPartScore[],
): BookProgress {
  const completedParts = completed.length;
  const percent =
    totalParts === 0 ? 0 : Math.floor((completedParts / totalParts) * 100);
  const averageScore =
    completedParts === 0
      ? 0
      : completed.reduce((sum, c) => sum + c.score, 0) / completedParts;
  return { percent, averageScore, completedParts };
}
