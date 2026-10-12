import type { QuestionScoring, Answer, PartStatus } from "@/lib/types";

export function computePartStatus(
  questions: QuestionScoring[],
  answers: Answer[],
): PartStatus {
  if (answers.length === 0) return "not_started";

  const answeredIds = new Set(answers.map((a) => a.questionId));
  const allAnswered = questions.every((q) => answeredIds.has(q.id));
  if (!allAnswered) return "in_progress";

  const openUngraded = questions.some((q) => {
    if (q.type !== "open") return false;
    const ans = answers.find((a) => a.questionId === q.id);
    return !ans || !ans.graded;
  });
  if (openUngraded) return "pending_review";

  return "completed";
}
