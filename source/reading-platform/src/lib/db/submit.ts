import { createServerSupabase } from "@/lib/supabase/client";
import { listQuestions } from "@/lib/db/questions";
import { loadLadder, bookViewFrom } from "@/lib/db/studentRepo";

export interface SubmittedAnswer {
  questionId: string;
  selectedOptionIndex: number | null;
  textAnswer: string | null;
}

export async function submitPart(studentId: string, partId: string, submitted: SubmittedAnswer[]): Promise<void> {
  const [ladder, questions] = await Promise.all([loadLadder(studentId), listQuestions(partId)]);
  if (!ladder) return;
  const part = [...ladder.partsByBook.values()].flat().find((p) => p.id === partId);
  if (!part) return;
  const view = bookViewFrom(ladder, part.bookId);
  if (!view.unlocked || !view.parts.find((p) => p.part.id === partId)?.unlocked) return;
  const validIds = new Set(questions.map((q) => q.id));
  const rows = submitted.filter((a) => validIds.has(a.questionId)).map((a) => ({
    question_id: a.questionId, selected_option_index: a.selectedOptionIndex, text_answer: a.textAnswer,
  }));
  if (rows.length === 0) return;
  if (new Set(rows.map((a) => a.question_id)).size !== rows.length) {
    throw new Error("Duplicate question answers");
  }
  // The database transaction also handles duplicate taps and repairs legacy
  // empty attempts; no orphan attempt can survive an answer-write failure.
  const { error } = await createServerSupabase().rpc("submit_part_atomic", {
    p_student_id: studentId, p_part_id: partId, p_answers: rows,
  });
  if (error) throw error;
}
