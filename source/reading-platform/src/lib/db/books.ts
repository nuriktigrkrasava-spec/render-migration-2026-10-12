import { createServerSupabase } from "@/lib/supabase/client";
import { bookFromRow } from "@/lib/db/mappers";
import type { Book } from "@/lib/types";

export async function listBooks(): Promise<Book[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("books").select("*").order("level");
  if (error) throw error;
  return (data ?? []).map(bookFromRow);
}

export async function listBooksForLevel(level: number): Promise<Book[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("books")
    .select("*").eq("level", level).order("created_at");
  if (error) throw error;
  return (data ?? []).map(bookFromRow);
}

export async function createBook(input: { title: string; description: string }): Promise<Book> {
  const db = createServerSupabase();
  const level = await nextBookLevel();
  const { data, error } = await db.from("books")
    .insert({ title: input.title, description: input.description, level })
    .select("*").single();
  if (error) throw error;
  return bookFromRow(data);
}

export async function updateBook(id: string, input: { title: string; description: string }): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("books").update(input).eq("id", id);
  if (error) throw error;
}

export async function nextBookLevel(): Promise<number> {
  const books = await listBooks();
  return books.length === 0 ? 1 : Math.max(...books.map((b) => b.level)) + 1;
}

// books.level уникален, поэтому обмен двух значений в лоб нарушил бы
// ограничение на промежуточном шаге. Меняем через свободное временное
// значение -1: уровни всегда положительные, столкнуться не с чем.
export async function swapBookLevels(idA: string, idB: string): Promise<void> {
  const db = createServerSupabase();
  const { data, error } = await db.from("books").select("id, level").in("id", [idA, idB]);
  if (error) throw error;
  const a = (data ?? []).find((r) => r.id === idA);
  const b = (data ?? []).find((r) => r.id === idB);
  if (!a || !b) return;

  const steps = [
    db.from("books").update({ level: -1 }).eq("id", idA),
    db.from("books").update({ level: a.level }).eq("id", idB),
    db.from("books").update({ level: b.level }).eq("id", idA),
  ];
  for (const step of steps) {
    const { error: stepError } = await step;
    if (stepError) throw stepError;
  }
}

// Назначение уровня книге. Если уровень занят — книги меняются местами:
// учитель не должен разбираться с ошибкой «уровень занят».
export async function setBookLevel(bookId: string, level: number): Promise<void> {
  const books = await listBooks();
  const target = books.find((b) => b.id === bookId);
  if (!target || target.level === level) return;

  const occupant = books.find((b) => b.level === level);
  if (occupant) {
    await swapBookLevels(bookId, occupant.id);
    return;
  }
  const db = createServerSupabase();
  const { error } = await db.from("books").update({ level }).eq("id", bookId);
  if (error) throw error;
}

export async function moveBookLevel(bookId: string, direction: "up" | "down"): Promise<void> {
  const books = await listBooks(); // уже отсортированы по level
  const i = books.findIndex((b) => b.id === bookId);
  if (i === -1) return;
  const j = direction === "up" ? i - 1 : i + 1;
  if (j < 0 || j >= books.length) return;
  await swapBookLevels(bookId, books[j].id);
}

export async function deleteBook(id: string): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("books").delete().eq("id", id);
  if (error) throw error;
}
