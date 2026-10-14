import { createServerSupabase } from "@/lib/supabase/client";
import { fetchAllRows } from "@/lib/db/paginate";
import { fetchScoringQuestions, type ScoringRow } from "@/lib/db/studentProgress";
import type { PartRow } from "@/lib/db/rowTypes";

export type PartSummaryRow = Omit<PartRow, "reading_text">;
export interface ScoringCatalog { parts: PartSummaryRow[]; questions: ScoringRow[] }

// SQL aggregates the light catalog into one JSON value: PostgREST's row limit
// cannot silently truncate an embedded relation. No reading texts are included.
export async function fetchScoringCatalog(): Promise<ScoringCatalog> {
  const db = createServerSupabase();
  const { data, error } = await db.rpc("get_scoring_catalog");
  if (!error) return data as ScoringCatalog;
  // Allows a staged rollout before migration 0004. Other errors remain errors.
  if (error.code !== "PGRST202") throw error;
  const [parts, questions] = await Promise.all([
    fetchAllRows<PartSummaryRow>((from, to) => db.from("parts")
      .select("id, book_id, order, title").order("book_id").order("order").range(from, to)),
    fetchScoringQuestions(),
  ]);
  return { parts, questions };
}
