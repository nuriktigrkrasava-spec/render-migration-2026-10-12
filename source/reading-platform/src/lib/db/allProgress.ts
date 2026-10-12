import { createServerSupabase } from "@/lib/supabase/client";
import { listBooks } from "@/lib/db/books";
import { listStudents } from "@/lib/db/students";
import { partFromRow, questionFromRow } from "@/lib/db/mappers";
import { computePartStatus } from "@/lib/progress/partStatus";
import { computeBookProgress, type BookProgress } from "@/lib/progress/bookProgress";
import { unlockedThroughLevel, isLevelUnlocked } from "@/lib/progress/levelUnlock";
import type { Answer, Book, Part, PartStatus, Question, Student } from "@/lib/types";
import type { AnswerRow, PartRow, QuestionRow } from "@/lib/db/rowTypes";

export interface OverviewRow {
  student: Student;
  unlockedThrough: number;
  books: { book: Book; progress: BookProgress; unlocked: boolean }[];
}

function answerFromRow(r: AnswerRow): Answer {
  return {
    questionId: r.question_id,
    selectedOptionIndex: r.selected_option_index,
    textAnswer: r.text_answer,
    graded: r.graded,
    awardedPoints: r.awarded_points,
    teacherComment: r.teacher_comment,
  };
}

function partScore(questions: Question[], answers: Answer[]): number {
  return answers.reduce((sum, a) => {
    const q = questions.find((qq) => qq.id === a.questionId);
    if (!q) return sum;
    if (q.type === "open") return sum + (a.awardedPoints ?? 0);
    return sum + (a.selectedOptionIndex === q.correctOptionIndex ? q.points : 0);
  }, 0);
}

// Обзор учителя: раньше на каждую пару «ученик × книга» шло по три запроса
// (при 21 ученике — десятки, а с полным списком книг было бы под 250).
// Здесь пять запросов на всю страницу, остальное считается в памяти:
// частей и попыток в базе десятки, объём данных ничтожный.
export async function overviewRows(): Promise<OverviewRow[]> {
  const db = createServerSupabase();
  const [students, books] = await Promise.all([listStudents(), listBooks()]);
  if (students.length === 0 || books.length === 0) {
    return students.map((student) => ({
      student, unlockedThrough: student.startLevel, books: [],
    }));
  }

  const { data: partRows, error: partErr } = await db.from("parts")
    .select("*").in("book_id", books.map((b) => b.id)).order("order");
  if (partErr) throw partErr;
  const parts = ((partRows ?? []) as PartRow[]).map(partFromRow);

  const partIds = parts.map((p) => p.id);
  const [
    { data: qRows, error: qErr },
    { data: attemptRows, error: aErr },
  ] = await Promise.all([
    db.from("questions").select("*").in("part_id", partIds),
    db.from("attempts").select("id, student_id, part_id").in("part_id", partIds),
  ]);
  if (qErr) throw qErr;
  if (aErr) throw aErr;

  const attempts = (attemptRows ?? []) as { id: string; student_id: string; part_id: string }[];
  const answersByAttempt = new Map<string, Answer[]>();
  if (attempts.length > 0) {
    const { data: ansRows, error: ansErr } = await db.from("answers")
      .select("*").in("attempt_id", attempts.map((a) => a.id));
    if (ansErr) throw ansErr;
    for (const r of (ansRows ?? []) as AnswerRow[]) {
      const arr = answersByAttempt.get(r.attempt_id);
      if (arr) arr.push(answerFromRow(r));
      else answersByAttempt.set(r.attempt_id, [answerFromRow(r)]);
    }
  }

  const questionsByPart = new Map<string, Question[]>();
  for (const r of (qRows ?? []) as QuestionRow[]) {
    const q = questionFromRow(r);
    const arr = questionsByPart.get(q.partId);
    if (arr) arr.push(q);
    else questionsByPart.set(q.partId, [q]);
  }

  const partsByBook = new Map<string, Part[]>();
  for (const p of parts) {
    const arr = partsByBook.get(p.bookId);
    if (arr) arr.push(p);
    else partsByBook.set(p.bookId, [p]);
  }

  // Ответы ученика по частям: ученик → часть → ответы.
  const byStudent = new Map<string, Map<string, Answer[]>>();
  for (const a of attempts) {
    let perPart = byStudent.get(a.student_id);
    if (!perPart) {
      perPart = new Map();
      byStudent.set(a.student_id, perPart);
    }
    perPart.set(a.part_id, answersByAttempt.get(a.id) ?? []);
  }

  const levelBooks = books.map((b) => ({
    level: b.level,
    partIds: (partsByBook.get(b.id) ?? []).map((p) => p.id),
  }));

  return students.map((student) => {
    const perPart = byStudent.get(student.id) ?? new Map<string, Answer[]>();

    const statusById = new Map<string, PartStatus>();
    for (const p of parts) {
      statusById.set(
        p.id,
        computePartStatus(questionsByPart.get(p.id) ?? [], perPart.get(p.id) ?? []),
      );
    }

    const unlockedThrough = unlockedThroughLevel(levelBooks, student.startLevel, statusById);

    const rows = books
      .map((book) => {
        const bookParts = partsByBook.get(book.id) ?? [];
        const completed: { score: number }[] = [];
        for (const p of bookParts) {
          if (statusById.get(p.id) !== "completed") continue;
          completed.push({
            score: partScore(questionsByPart.get(p.id) ?? [], perPart.get(p.id) ?? []),
          });
        }
        return {
          book,
          progress: computeBookProgress(bookParts.length, completed),
          unlocked: isLevelUnlocked(book.level, unlockedThrough),
        };
      })
      // Учителю показываем открытые книги и те, где у ученика уже есть
      // пройденные части. Иначе после сброса стартового уровня прогресс,
      // накопленный на верхних уровнях, исчез бы у него с глаз — хотя
      // в базе он цел и вернётся к ученику, когда тот дойдёт до уровня.
      .filter((r) => r.unlocked || r.progress.completedParts > 0);

    return { student, unlockedThrough, books: rows };
  });
}
