import { createServerSupabase } from "@/lib/supabase/client";
import { listBooks } from "@/lib/db/books";
import { listStudents } from "@/lib/db/students";
import { partFromRow } from "@/lib/db/mappers";
import { computePartStatus } from "@/lib/progress/partStatus";
import { computeBookProgress, type BookProgress } from "@/lib/progress/bookProgress";
import { unlockedThroughLevel, isLevelUnlocked } from "@/lib/progress/levelUnlock";
import type { Answer, Book, Part, PartStatus, QuestionScoring, Student } from "@/lib/types";
import type { AnswerRow } from "@/lib/db/rowTypes";

import { fetchScoringCatalog } from "@/lib/db/scoringCatalog";
import { fetchAllRows } from "@/lib/db/paginate";
import { partDataFrom } from "@/lib/db/studentProgress";

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

function partScore(questions: QuestionScoring[], answers: Answer[]): number {
  return answers.reduce((sum, a) => {
    const q = questions.find((qq) => qq.id === a.questionId);
    if (!q) return sum;
    if (q.type === "open") return sum + (a.awardedPoints ?? 0);
    return sum + (a.selectedOptionIndex === q.correctOptionIndex ? q.points : 0);
  }, 0);
}

// Все растущие выборки обходятся по страницам, ответы приходят с попытками.
export async function overviewRows(): Promise<OverviewRow[]> {
  const db = createServerSupabase();
  const [students, books] = await Promise.all([listStudents(), listBooks()]);
  if (students.length === 0 || books.length === 0) {
    return students.map((student) => ({
      student, unlockedThrough: student.startLevel, books: [],
    }));
  }

  const [catalog, attempts] = await Promise.all([
    fetchScoringCatalog(),
    fetchAllRows<{ id: string; student_id: string; part_id: string; answers: AnswerRow[] }>(
      (from, to) => db.rpc("get_progress_attempts", { p_from: from, p_to: to })),
  ]);
  const { parts: partRows, questions: questionRows } = catalog;
  const parts = partRows.map((row) => partFromRow({ ...row, reading_text: "" }));
  const questionsByPart = partDataFrom(questionRows, []).questionsByPart;
  const answersByAttempt = new Map(attempts.map((a) => [
    a.id, (a.answers ?? []).map(answerFromRow),
  ]));

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
