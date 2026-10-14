import { fetchAllRows } from "@/lib/db/paginate";
import type { StudentRow } from "@/lib/db/rowTypes";
import { createServerSupabase } from "@/lib/supabase/client";
import { studentFromRow } from "@/lib/db/mappers";
import type { Student } from "@/lib/types";

export async function listStudents(): Promise<Student[]> {
  const db = createServerSupabase();
  const rows = await fetchAllRows<StudentRow>((from, to) => db.from("students")
    .select("*").order("created_at").order("id").range(from, to));
  return rows.map(studentFromRow);
}

export async function getStudent(studentId: string): Promise<Student | null> {
  const db = createServerSupabase();
  const { data, error } = await db.from("students").select("*").eq("id", studentId).maybeSingle();
  if (error) throw error;
  return data ? studentFromRow(data) : null;
}

export async function setStudentStartLevel(studentId: string, level: number): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("students").update({ start_level: level }).eq("id", studentId);
  if (error) throw error;
}
