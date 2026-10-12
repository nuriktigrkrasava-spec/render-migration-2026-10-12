import { createServerSupabase } from "@/lib/supabase/client";
import { partFromRow } from "@/lib/db/mappers";
import type { Part } from "@/lib/types";

export async function listParts(bookId: string): Promise<Part[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("parts").select("*")
    .eq("book_id", bookId).order("order");
  if (error) throw error;
  return (data ?? []).map(partFromRow);
}

export async function createPart(input: {
  bookId: string; order: number; title: string; readingText: string;
}): Promise<Part> {
  const db = createServerSupabase();
  const { data, error } = await db.from("parts").insert({
    book_id: input.bookId, order: input.order, title: input.title, reading_text: input.readingText,
  }).select("*").single();
  if (error) throw error;
  return partFromRow(data);
}

export async function updatePart(id: string, input: {
  title: string; readingText: string;
}): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("parts")
    .update({ title: input.title, reading_text: input.readingText }).eq("id", id);
  if (error) throw error;
}

export async function deletePart(id: string): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("parts").delete().eq("id", id);
  if (error) throw error;
}

export async function nextPartOrder(bookId: string): Promise<number> {
  const parts = await listParts(bookId);
  return parts.length === 0 ? 1 : Math.max(...parts.map((p) => p.order)) + 1;
}

// Массовое создание пустых частей: учитель сначала делает каркас книги
// на N глав, а тексты и вопросы вписывает потом.
export async function createParts(bookId: string, count: number): Promise<void> {
  const safeCount = Math.min(50, Math.max(1, Math.trunc(count)));
  const start = await nextPartOrder(bookId);
  const rows = Array.from({ length: safeCount }, (_, i) => ({
    book_id: bookId,
    order: start + i,
    title: `${start + i}-бөлім`,
    reading_text: "",
  }));
  const db = createServerSupabase();
  const { error } = await db.from("parts").insert(rows);
  if (error) throw error;
}

// (book_id, order) уникальна — обмен идёт через свободное временное
// значение -1, как и у уровней книг.
export async function swapPartOrder(idA: string, idB: string): Promise<void> {
  const db = createServerSupabase();
  const { data, error } = await db.from("parts").select("id, order").in("id", [idA, idB]);
  if (error) throw error;
  const a = (data ?? []).find((r) => r.id === idA);
  const b = (data ?? []).find((r) => r.id === idB);
  if (!a || !b) return;

  const steps = [
    db.from("parts").update({ order: -1 }).eq("id", idA),
    db.from("parts").update({ order: a.order }).eq("id", idB),
    db.from("parts").update({ order: b.order }).eq("id", idA),
  ];
  for (const step of steps) {
    const { error: stepError } = await step;
    if (stepError) throw stepError;
  }
}

export async function movePart(
  partId: string, bookId: string, direction: "up" | "down",
): Promise<void> {
  const parts = await listParts(bookId); // уже отсортированы по order
  const i = parts.findIndex((p) => p.id === partId);
  if (i === -1) return;
  const j = direction === "up" ? i - 1 : i + 1;
  if (j < 0 || j >= parts.length) return;
  await swapPartOrder(partId, parts[j].id);
}

export async function countQuestionsByPart(partIds: string[]): Promise<Map<string, number>> {
  const counts = new Map<string, number>();
  if (partIds.length === 0) return counts;
  const db = createServerSupabase();
  const { data, error } = await db.from("questions").select("part_id").in("part_id", partIds);
  if (error) throw error;
  for (const r of (data ?? []) as { part_id: string }[]) {
    counts.set(r.part_id, (counts.get(r.part_id) ?? 0) + 1);
  }
  return counts;
}
