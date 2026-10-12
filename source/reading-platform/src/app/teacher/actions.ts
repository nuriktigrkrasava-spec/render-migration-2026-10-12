"use server";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { checkPassword, startSession, endSession, isAuthenticated } from "@/lib/auth/teacher";
import * as books from "@/lib/db/books";
import * as parts from "@/lib/db/parts";
import * as questions from "@/lib/db/questions";
import * as students from "@/lib/db/students";
import * as grading from "@/lib/db/grading";

async function requireAuth() {
  if (!(await isAuthenticated())) redirect("/teacher/login");
}

export async function loginAction(formData: FormData) {
  const password = String(formData.get("password") ?? "");
  if (!checkPassword(password)) return { error: "Қате құпиясөз" };
  await startSession();
  redirect("/teacher");
}

export async function logoutAction() {
  await endSession();
  redirect("/teacher/login");
}

function parseLevel(value: FormDataEntryValue | null, fallback = 1): number {
  const n = Number(value ?? fallback);
  return Number.isInteger(n) && n >= 1 ? n : fallback;
}

export async function createBookAction(formData: FormData) {
  await requireAuth();
  await books.createBook({
    title: String(formData.get("title") ?? ""),
    description: String(formData.get("description") ?? ""),
  });
  revalidatePath("/teacher/books");
  revalidatePath("/app");
}

export async function updateBookAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  await books.updateBook(bookId, {
    title: String(formData.get("title") ?? ""),
    description: String(formData.get("description") ?? ""),
  });
  revalidatePath("/teacher/books");
  revalidatePath(`/teacher/books/${bookId}`);
}

export async function setBookLevelAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  await books.setBookLevel(bookId, parseLevel(formData.get("level")));
  revalidatePath("/teacher/books");
  revalidatePath(`/teacher/books/${bookId}`);
  revalidatePath("/app");
}

export async function moveBookLevelAction(formData: FormData) {
  await requireAuth();
  const direction = formData.get("direction") === "up" ? "up" : "down";
  await books.moveBookLevel(String(formData.get("bookId")), direction);
  revalidatePath("/teacher/books");
  revalidatePath("/app");
}

export async function deleteBookAction(formData: FormData) {
  await requireAuth();
  await books.deleteBook(String(formData.get("bookId")));
  revalidatePath("/teacher/books");
}

export async function createPartAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  const order = await parts.nextPartOrder(bookId);
  await parts.createPart({
    bookId, order,
    title: String(formData.get("title") ?? ""),
    readingText: String(formData.get("readingText") ?? ""),
  });
  revalidatePath(`/teacher/books/${bookId}`);
}

export async function createPartsAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  const count = Number(formData.get("count") ?? 1);
  await parts.createParts(bookId, Number.isFinite(count) ? count : 1);
  revalidatePath(`/teacher/books/${bookId}`);
}

export async function movePartAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  const direction = formData.get("direction") === "up" ? "up" : "down";
  await parts.movePart(String(formData.get("partId")), bookId, direction);
  revalidatePath(`/teacher/books/${bookId}`);
}

export async function updatePartAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  await parts.updatePart(String(formData.get("partId")), {
    title: String(formData.get("title") ?? ""),
    readingText: String(formData.get("readingText") ?? ""),
  });
  revalidatePath(`/teacher/books/${bookId}`);
}

export async function deletePartAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  await parts.deletePart(String(formData.get("partId")));
  revalidatePath(`/teacher/books/${bookId}`);
}

export async function createQuestionAction(formData: FormData) {
  await requireAuth();
  const type = String(formData.get("type")) as "choice" | "open";
  const partId = String(formData.get("partId"));
  const bookId = String(formData.get("bookId"));

  // Варианты приходят отдельными полями "option" в порядке отображения.
  const options = type === "choice"
    ? formData.getAll("option").map((s) => String(s).trim()).filter(Boolean)
    : [];

  // Правильный — это выбранный радио-кнопкой индекс (отсчёт с 0 — внутренний,
  // учитель его не видит, а просто кликает «верный»). Подстраховка: clamp.
  let correctOptionIndex: number | null = null;
  if (type === "choice") {
    const raw = Number(formData.get("correctOptionIndex") ?? 0);
    correctOptionIndex =
      Number.isInteger(raw) && raw >= 0 && raw < options.length ? raw : 0;
  }

  // Балл = максимум за вопрос. Для открытых вопросов держим диапазон 1–5
  // (учитель при проверке ставит 0–5), для тестовых — минимум 1.
  const rawPoints = Math.round(Number(formData.get("points") ?? 1)) || 1;
  const points = type === "open"
    ? Math.min(5, Math.max(1, rawPoints))
    : Math.max(1, rawPoints);

  await questions.createQuestion({
    partId, type,
    text: String(formData.get("text") ?? ""),
    points,
    options, correctOptionIndex,
  });
  revalidatePath(`/teacher/books/${bookId}/parts/${partId}`);
}

export async function deleteQuestionAction(formData: FormData) {
  await requireAuth();
  const bookId = String(formData.get("bookId"));
  const partId = String(formData.get("partId"));
  await questions.deleteQuestion(String(formData.get("questionId")));
  revalidatePath(`/teacher/books/${bookId}/parts/${partId}`);
}

export async function setStudentStartLevelAction(formData: FormData) {
  await requireAuth();
  await students.setStudentStartLevel(
    String(formData.get("studentId")),
    parseLevel(formData.get("level")),
  );
  revalidatePath("/teacher/students");
  revalidatePath("/teacher");
}

export async function gradeAction(formData: FormData) {
  await requireAuth();
  await grading.gradeAnswer({
    answerId: String(formData.get("answerId")),
    awardedPoints: Number(formData.get("awardedPoints") ?? 0),
    comment: String(formData.get("comment") ?? ""),
  });
  revalidatePath("/teacher/grading");
}
