import { createServerSupabase } from "@/lib/supabase/client";
import { listQuestions } from "@/lib/db/questions";
import { bookView } from "@/lib/db/studentRepo";

export interface SubmittedAnswer {
  questionId: string;
  selectedOptionIndex: number | null;
  textAnswer: string | null;
}

export async function submitPart(
  studentId: string, partId: string, submitted: SubmittedAnswer[],
): Promise<void> {
  const db = createServerSupabase();

  // 1. Найти книгу этой части. Нет такого part — no-op.
  const { data: partRow, error: perr } = await db.from("parts")
    .select("book_id").eq("id", partId).maybeSingle();
  if (perr) throw perr;
  if (!partRow) return;
  const bookId = partRow.book_id as string;

  // 2. Уровень книги должен быть открыт ученику — по той же лестнице, что и
  // в интерфейсе: стартовый уровень плюс пройденные целиком уровни выше.
  // 3. И сама часть должна быть открыта. Обе проверки — из одной загрузки.
  const { unlocked, parts } = await bookView(studentId, bookId);
  if (!unlocked) return;
  const entry = parts.find((p) => p.part.id === partId);
  if (!entry || !entry.unlocked) return;

  // 4. Идемпотентность: уже отправлено — no-op.
  const { data: existing } = await db.from("attempts")
    .select("id").eq("student_id", studentId).eq("part_id", partId).maybeSingle();
  if (existing) return;

  const questions = await listQuestions(partId);
  const typeById = new Map(questions.map((q) => [q.id, q.type]));
  const validIds = new Set(questions.map((q) => q.id));

  const { data: attempt, error: aerr } = await db.from("attempts")
    .insert({ student_id: studentId, part_id: partId }).select("id").single();
  if (aerr) throw aerr;

  // 5. Вставлять только ответы на вопросы, принадлежащие этой part.
  const rows = submitted
    .filter((s) => validIds.has(s.questionId))
    .map((s) => {
      const isChoice = typeById.get(s.questionId) === "choice";
      return {
        attempt_id: attempt.id,
        question_id: s.questionId,
        selected_option_index: isChoice ? s.selectedOptionIndex : null,
        text_answer: isChoice ? null : s.textAnswer,
        graded: isChoice, // choice автоматически «проверен»
        awarded_points: null,
        teacher_comment: null,
      };
    });
  const { error: anserr } = await db.from("answers").insert(rows);
  if (anserr) throw anserr;
}
