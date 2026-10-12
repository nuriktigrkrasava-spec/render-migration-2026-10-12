import { createServerSupabase } from "@/lib/supabase/client";
import { questionFromRow } from "@/lib/db/mappers";
import type { Question } from "@/lib/types";

export async function listQuestions(partId: string): Promise<Question[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("questions").select("*").eq("part_id", partId);
  if (error) throw error;
  return (data ?? []).map(questionFromRow);
}

export async function createQuestion(input: {
  partId: string; type: "choice" | "open"; text: string; points: number;
  options: string[]; correctOptionIndex: number | null;
}): Promise<Question> {
  const db = createServerSupabase();
  const { data, error } = await db.from("questions").insert({
    part_id: input.partId, type: input.type, text: input.text, points: input.points,
    options: input.options, correct_option_index: input.correctOptionIndex,
  }).select("*").single();
  if (error) throw error;
  return questionFromRow(data);
}

export async function deleteQuestion(id: string): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("questions").delete().eq("id", id);
  if (error) throw error;
}
