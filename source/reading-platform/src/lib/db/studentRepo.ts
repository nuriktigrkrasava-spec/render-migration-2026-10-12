import { createServerSupabase } from "@/lib/supabase/client";
import { studentFromRow, partFromRow } from "@/lib/db/mappers";
import { fetchAllRows } from "@/lib/db/paginate";
import { listQuestions } from "@/lib/db/questions";
import { listBooks } from "@/lib/db/books";
import { getStudent } from "@/lib/db/students";
import { unlockedPartIds } from "@/lib/progress/unlock";
import { unlockedThroughLevel, isLevelUnlocked } from "@/lib/progress/levelUnlock";
import {
  partDataFrom, fetchScoringQuestions, fetchStudentAttempts,
  statusFor, bookProgressFrom, type PartData,
} from "@/lib/db/studentProgress";
import type { Student, Book, Part, PartStatus, Question, Answer } from "@/lib/types";
import type { BookProgress } from "@/lib/progress/bookProgress";
import type { PartRow } from "@/lib/db/rowTypes";

export async function upsertStudentByTelegram(input: {
  telegramId: string; name: string;
}): Promise<Student> {
  const db = createServerSupabase();
  const { data, error } = await db.from("students")
    .upsert({ telegram_id: input.telegramId, name: input.name }, { onConflict: "telegram_id" })
    .select("*").single();
  if (error) throw error;
  return studentFromRow(data);
}

export interface LevelEntry {
  book: Book;
  progress: BookProgress;
  unlocked: boolean;
  pendingReview: number; // частей, ждущих проверки учителем
}

export interface Ladder {
  student: Student;
  books: Book[];
  partsByBook: Map<string, Part[]>;
  data: PartData;
  statusById: Map<string, PartStatus>;
  unlockedThrough: number;
}

// Одна загрузка всего, что нужно экранам ученика: сам ученик, книги, части,
// данные прогресса и посчитанная граница открытых уровней.
//
// Пять последовательных запросов раньше стоили около пяти секунд на страницу:
// база отвечает примерно за секунду на круг, и круги шли один за другим.
// Теперь всё уезжает одним параллельным пакетом: ни один из запросов не нужен
// другому, а ответы приходят вложенными в попытки. Один круг вместо пяти.
export async function loadLadder(studentId: string): Promise<Ladder | null> {
  const db = createServerSupabase();

  const [student, books, partRows, questionRows, attempts] = await Promise.all([
    getStudent(studentId),
    listBooks(),
    fetchAllRows<PartRow>((from, to) =>
      db.from("parts").select("*").order("book_id").order("order").range(from, to),
    ),
    fetchScoringQuestions(),
    fetchStudentAttempts(studentId),
  ]);
  if (!student) return null;

  const data = partDataFrom(questionRows, attempts);
  const parts = partRows.map(partFromRow);

  const partsByBook = new Map<string, Part[]>();
  for (const p of parts) {
    const arr = partsByBook.get(p.bookId);
    if (arr) arr.push(p);
    else partsByBook.set(p.bookId, [p]);
  }
  for (const arr of partsByBook.values()) arr.sort((a, b) => a.order - b.order);

  const statusById = new Map<string, PartStatus>();
  for (const p of parts) statusById.set(p.id, statusFor(p.id, data));

  const unlockedThrough = unlockedThroughLevel(
    books.map((b) => ({
      level: b.level,
      partIds: (partsByBook.get(b.id) ?? []).map((p) => p.id),
    })),
    student.startLevel,
    statusById,
  );

  return { student, books, partsByBook, data, statusById, unlockedThrough };
}

export function levelsFrom(l: Ladder): LevelEntry[] {
  return l.books.map((book) => {
    const parts = l.partsByBook.get(book.id) ?? [];
    return {
      book,
      progress: bookProgressFrom(parts, l.data),
      unlocked: isLevelUnlocked(book.level, l.unlockedThrough),
      pendingReview: parts.filter(
        (p) => l.statusById.get(p.id) === "pending_review",
      ).length,
    };
  });
}

export interface BookView { unlocked: boolean; parts: PartWithStatus[]; }

// Доступ к книге и статусы её частей из уже загруженной лестницы.
export function bookViewFrom(l: Ladder, bookId: string): BookView {
  const book = l.books.find((b) => b.id === bookId);
  if (!book) return { unlocked: false, parts: [] };
  return {
    unlocked: isLevelUnlocked(book.level, l.unlockedThrough),
    parts: withStatuses(l.partsByBook.get(bookId) ?? [], l.data),
  };
}

export async function bookView(
  studentId: string, bookId: string,
): Promise<BookView> {
  const l = await loadLadder(studentId);
  if (!l) return { unlocked: false, parts: [] };
  return bookViewFrom(l, bookId);
}

export interface PartWithStatus { part: Part; status: PartStatus; unlocked: boolean; }

function withStatuses(parts: Part[], data: PartData): PartWithStatus[] {
  const statusById = new Map<string, PartStatus>();
  for (const part of parts) statusById.set(part.id, statusFor(part.id, data));
  const unlocked = unlockedPartIds(parts, statusById);
  return parts.map((part) => ({
    part,
    status: statusById.get(part.id) ?? "not_started",
    unlocked: unlocked.has(part.id),
  }));
}

export interface PartDetail {
  part: Part; questions: Question[]; answers: Answer[]; status: PartStatus;
  unlocked: boolean; bookUnlocked: boolean;
}

// Экран одной части: доступ к книге, статус части и её вопросы целиком.
// Тексты и варианты грузятся только здесь и только для этой части — общая
// загрузка прогресса тянет по вопросам лишь тип и баллы.
export async function partDetail(
  l: Ladder, bookId: string, partId: string,
): Promise<PartDetail | null> {
  const book = l.books.find((b) => b.id === bookId);
  if (!book) return null;

  const all = withStatuses(l.partsByBook.get(bookId) ?? [], l.data);
  const target = all.find((p) => p.part.id === partId);
  if (!target) return null;

  return {
    part: target.part,
    questions: await listQuestions(partId),
    answers: l.data.answersByPart.get(partId) ?? [],
    status: target.status,
    unlocked: target.unlocked,
    bookUnlocked: isLevelUnlocked(book.level, l.unlockedThrough),
  };
}
