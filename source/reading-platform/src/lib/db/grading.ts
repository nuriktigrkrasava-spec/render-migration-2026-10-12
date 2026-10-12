import { createServerSupabase } from "@/lib/supabase/client";

export interface PendingAnswer {
  answerId: string;
  studentName: string;
  bookTitle: string;
  partTitle: string;
  questionText: string;
  textAnswer: string | null;
  maxPoints: number;
}

export async function listPendingOpenAnswers(): Promise<PendingAnswer[]> {
  const db = createServerSupabase();
  const { data, error } = await db
    .from("answers")
    .select(`
      id, text_answer,
      questions!inner ( text, points, type ),
      attempts!inner (
        students!inner ( name ),
        parts!inner ( title, books!inner ( title ) )
      )
    `)
    .eq("graded", false)
    .eq("questions.type", "open");
  if (error) throw error;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data ?? []).map((r: any) => ({
    answerId: r.id,
    textAnswer: r.text_answer,
    questionText: r.questions.text,
    maxPoints: r.questions.points,
    studentName: r.attempts.students.name,
    partTitle: r.attempts.parts.title,
    bookTitle: r.attempts.parts.books.title,
  }));
}

export async function gradeAnswer(input: {
  answerId: string; awardedPoints: number; comment: string;
}): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("answers").update({
    graded: true, awarded_points: input.awardedPoints, teacher_comment: input.comment,
  }).eq("id", input.answerId);
  if (error) throw error;
}
