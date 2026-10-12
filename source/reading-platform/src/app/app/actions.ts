"use server";
import { revalidatePath } from "next/cache";
import { getStudentId } from "@/lib/auth/student";
import { submitPart, type SubmittedAnswer } from "@/lib/db/submit";

export async function submitPartAction(input: {
  bookId: string; partId: string; answers: SubmittedAnswer[];
}): Promise<{ ok: boolean }> {
  const studentId = await getStudentId();
  if (!studentId) return { ok: false };
  await submitPart(studentId, input.partId, input.answers);
  revalidatePath(`/app/books/${input.bookId}`);
  revalidatePath(`/app/books/${input.bookId}/parts/${input.partId}`);
  return { ok: true };
}
