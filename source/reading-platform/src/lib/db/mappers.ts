import type { Book, Part, Question, Student } from "@/lib/types";
import type { BookRow, PartRow, QuestionRow, StudentRow } from "@/lib/db/rowTypes";

export function bookFromRow(r: BookRow): Book {
  return { id: r.id, title: r.title, description: r.description, coverUrl: r.cover_url, level: r.level };
}
export function partFromRow(r: PartRow): Part {
  return { id: r.id, bookId: r.book_id, order: r.order, title: r.title, readingText: r.reading_text };
}
export function questionFromRow(r: QuestionRow): Question {
  return {
    id: r.id, partId: r.part_id, type: r.type, text: r.text, points: r.points,
    options: r.options, correctOptionIndex: r.correct_option_index,
  };
}
export function studentFromRow(r: StudentRow): Student {
  return { id: r.id, telegramId: r.telegram_id, name: r.name, startLevel: r.start_level };
}
