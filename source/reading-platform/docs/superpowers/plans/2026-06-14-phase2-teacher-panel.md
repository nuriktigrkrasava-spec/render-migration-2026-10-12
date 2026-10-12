# Этап 2: Панель учителя — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Веб-панель учителя: вход по паролю, управление книгами/частями/тестами, ученики и доступы, очередь проверки открытых ответов, обзор прогресса.

**Architecture:** Next.js App Router. Маршруты учителя под `/teacher`, защищены middleware (подписанная httpOnly-cookie, пароль из `TEACHER_PASSWORD`). Доступ к данным — слой репозиториев в `src/lib/db/` поверх серверного клиента Supabase; вся запись/чтение БД идёт через server actions (`"use server"`). Преобразование строк БД (snake_case) ↔ доменные типы (camelCase) вынесено в чистые мапперы и покрыто unit-тестами. Логика подписи cookie тоже покрыта тестами. UI — серверные компоненты + минимальные клиентские формы; визуальная часть оформляется через навык frontend-design (аккуратно, без «AI-шаблонности»).

**Tech Stack:** Next.js 16 (App Router, TS), Supabase JS, Vitest. Подпись cookie — встроенный `node:crypto` (HMAC), без сторонних библиотек.

**Стратегия тестов:** Живой БД пока нет. Тестируем то, что чисто: мапперы строк↔домена и подпись/проверку токена сессии. Репозитории — тонкие обёртки над Supabase, проверяются на реальной базе на этапе 4.

---

## Структура файлов (этап 2)

- `src/lib/auth/session.ts` (+ `.test.ts`) — подпись и проверка токена сессии учителя (HMAC).
- `src/lib/auth/teacher.ts` — проверка пароля, чтение сессии из cookie (серверные хелперы).
- `middleware.ts` — защита `/teacher/*` (кроме `/teacher/login`).
- `src/lib/db/rowTypes.ts` — типы строк БД (snake_case).
- `src/lib/db/mappers.ts` (+ `.test.ts`) — строка БД ↔ доменный тип.
- `src/lib/db/books.ts` — репозиторий книг.
- `src/lib/db/parts.ts` — репозиторий частей.
- `src/lib/db/questions.ts` — репозиторий вопросов.
- `src/lib/db/students.ts` — репозиторий учеников и доступов (enrollments).
- `src/lib/db/grading.ts` — очередь проверки и выставление оценки; обзор прогресса.
- `src/app/teacher/actions.ts` — server actions, вызывающие репозитории.
- `src/app/teacher/login/page.tsx` — страница входа.
- `src/app/teacher/layout.tsx` — общий каркас панели (навигация).
- `src/app/teacher/page.tsx` — обзор (ученики и прогресс).
- `src/app/teacher/books/page.tsx` — список книг + создание.
- `src/app/teacher/books/[bookId]/page.tsx` — части книги (создание/редактирование/удаление).
- `src/app/teacher/books/[bookId]/parts/[partId]/page.tsx` — конструктор теста части.
- `src/app/teacher/students/page.tsx` — ученики и выдача доступа.
- `src/app/teacher/grading/page.tsx` — очередь проверки открытых ответов.
- UI-компоненты форм по необходимости в `src/components/teacher/`.

---

### Task 1: Подпись токена сессии учителя (TDD)

Токен = `payload.signature`, где signature = HMAC-SHA256(payload, secret) в hex. payload —
строка (для учителя достаточно фиксированного `"teacher"` + срок, но в этом этапе достаточно
`"teacher"`). Функции: `signSession(value, secret)` и `verifySession(token, secret)` →
возвращает `value` при валидной подписи, иначе `null`. Сравнение подписи —
constant-time (`crypto.timingSafeEqual`).

**Files:**
- Create: `src/lib/auth/session.ts`
- Test: `src/lib/auth/session.test.ts`

- [ ] **Step 1: Падающий тест**
```ts
import { describe, it, expect } from "vitest";
import { signSession, verifySession } from "@/lib/auth/session";

describe("session token", () => {
  const secret = "test-secret";
  it("round-trips a signed value", () => {
    const token = signSession("teacher", secret);
    expect(verifySession(token, secret)).toBe("teacher");
  });
  it("rejects a tampered payload", () => {
    const token = signSession("teacher", secret);
    const tampered = "admin." + token.split(".")[1];
    expect(verifySession(tampered, secret)).toBeNull();
  });
  it("rejects a wrong secret", () => {
    const token = signSession("teacher", secret);
    expect(verifySession(token, "other-secret")).toBeNull();
  });
  it("rejects malformed token", () => {
    expect(verifySession("garbage", secret)).toBeNull();
  });
});
```
- [ ] **Step 2: Запустить — упадёт** (`npx vitest run src/lib/auth/session.test.ts`)
- [ ] **Step 3: Реализация**
```ts
import { createHmac, timingSafeEqual } from "node:crypto";

function sign(value: string, secret: string): string {
  return createHmac("sha256", secret).update(value).digest("hex");
}

export function signSession(value: string, secret: string): string {
  return `${value}.${sign(value, secret)}`;
}

export function verifySession(token: string, secret: string): string | null {
  const idx = token.lastIndexOf(".");
  if (idx <= 0) return null;
  const value = token.slice(0, idx);
  const sig = token.slice(idx + 1);
  const expected = sign(value, secret);
  const a = Buffer.from(sig, "hex");
  const b = Buffer.from(expected, "hex");
  if (a.length !== b.length) return null;
  return timingSafeEqual(a, b) ? value : null;
}
```
- [ ] **Step 4: Запустить — пройдёт (4 теста)**
- [ ] **Step 5: Commit** — `feat: signed teacher session token`

---

### Task 2: Серверные хелперы аутентификации + middleware

**Files:**
- Create: `src/lib/auth/teacher.ts`
- Create: `middleware.ts` (в корне проекта, рядом с package.json)

- [ ] **Step 1: Хелперы**

`src/lib/auth/teacher.ts`:
```ts
import { cookies } from "next/headers";
import { signSession, verifySession } from "@/lib/auth/session";

const COOKIE = "teacher_session";

function secret(): string {
  const s = process.env.TEACHER_PASSWORD;
  if (!s) throw new Error("TEACHER_PASSWORD is not set");
  return s;
}

export function checkPassword(input: string): boolean {
  const expected = process.env.TEACHER_PASSWORD;
  return !!expected && input === expected;
}

export async function startSession(): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, signSession("teacher", secret()), {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
}

export async function endSession(): Promise<void> {
  const store = await cookies();
  store.delete(COOKIE);
}

export async function isAuthenticated(): Promise<boolean> {
  const store = await cookies();
  const token = store.get(COOKIE)?.value;
  if (!token) return false;
  return verifySession(token, secret()) === "teacher";
}
```

- [ ] **Step 2: middleware**

`middleware.ts`:
```ts
import { NextResponse, type NextRequest } from "next/server";
import { verifySession } from "@/lib/auth/session";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname.startsWith("/teacher/login")) return NextResponse.next();

  const token = req.cookies.get("teacher_session")?.value;
  const secret = process.env.TEACHER_PASSWORD ?? "";
  if (!token || verifySession(token, secret) !== "teacher") {
    const url = req.nextUrl.clone();
    url.pathname = "/teacher/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/teacher/:path*"] };
```

- [ ] **Step 3: Проверка типов** — `npx tsc --noEmit` без ошибок.
- [ ] **Step 4: Commit** — `feat: teacher auth helpers and route guard`

---

### Task 3: Типы строк БД + мапперы (TDD)

**Files:**
- Create: `src/lib/db/rowTypes.ts`
- Create: `src/lib/db/mappers.ts`
- Test: `src/lib/db/mappers.test.ts`

- [ ] **Step 1: Типы строк**

`src/lib/db/rowTypes.ts`:
```ts
export interface BookRow {
  id: string;
  title: string;
  description: string;
  cover_url: string | null;
  created_at: string;
}
export interface PartRow {
  id: string;
  book_id: string;
  order: number;
  title: string;
  reading_text: string;
}
export interface QuestionRow {
  id: string;
  part_id: string;
  type: "choice" | "open";
  text: string;
  points: number;
  options: string[];
  correct_option_index: number | null;
}
export interface StudentRow {
  id: string;
  telegram_id: string;
  name: string;
  created_at: string;
}
export interface AnswerRow {
  id: string;
  attempt_id: string;
  question_id: string;
  selected_option_index: number | null;
  text_answer: string | null;
  graded: boolean;
  awarded_points: number | null;
  teacher_comment: string | null;
}
```

- [ ] **Step 2: Падающий тест мапперов**

`src/lib/db/mappers.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { bookFromRow, partFromRow, questionFromRow, studentFromRow } from "@/lib/db/mappers";

describe("mappers", () => {
  it("maps book row to domain", () => {
    const b = bookFromRow({
      id: "1", title: "T", description: "D", cover_url: null, created_at: "x",
    });
    expect(b).toEqual({ id: "1", title: "T", description: "D", coverUrl: null });
  });
  it("maps part row to domain", () => {
    const p = partFromRow({
      id: "1", book_id: "b", order: 2, title: "T", reading_text: "R",
    });
    expect(p).toEqual({ id: "1", bookId: "b", order: 2, title: "T", readingText: "R" });
  });
  it("maps question row to domain", () => {
    const q = questionFromRow({
      id: "1", part_id: "p", type: "choice", text: "T", points: 2,
      options: ["a", "b"], correct_option_index: 1,
    });
    expect(q).toEqual({
      id: "1", partId: "p", type: "choice", text: "T", points: 2,
      options: ["a", "b"], correctOptionIndex: 1,
    });
  });
  it("maps student row to domain", () => {
    const s = studentFromRow({
      id: "1", telegram_id: "123", name: "N", created_at: "x",
    });
    expect(s).toEqual({ id: "1", telegramId: "123", name: "N" });
  });
});
```

- [ ] **Step 3: Запустить — упадёт**
- [ ] **Step 4: Реализация мапперов**

`src/lib/db/mappers.ts`:
```ts
import type { Book, Part, Question, Student } from "@/lib/types";
import type { BookRow, PartRow, QuestionRow, StudentRow } from "@/lib/db/rowTypes";

export function bookFromRow(r: BookRow): Book {
  return { id: r.id, title: r.title, description: r.description, coverUrl: r.cover_url };
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
  return { id: r.id, telegramId: r.telegram_id, name: r.name };
}
```
- [ ] **Step 5: Запустить — пройдёт (4 теста)**
- [ ] **Step 6: Commit** — `feat: db row types and mappers`

---

### Task 4: Репозитории книг и частей

Тонкие обёртки над `createServerSupabase()`. Каждая функция возвращает доменные типы
через мапперы. Без своей бизнес-логики (она уже в `src/lib/progress`).

**Files:**
- Create: `src/lib/db/books.ts`
- Create: `src/lib/db/parts.ts`

- [ ] **Step 1: books.ts**
```ts
import { createServerSupabase } from "@/lib/supabase/client";
import { bookFromRow } from "@/lib/db/mappers";
import type { Book } from "@/lib/types";

export async function listBooks(): Promise<Book[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("books").select("*").order("created_at");
  if (error) throw error;
  return (data ?? []).map(bookFromRow);
}

export async function createBook(input: { title: string; description: string }): Promise<Book> {
  const db = createServerSupabase();
  const { data, error } = await db.from("books")
    .insert({ title: input.title, description: input.description })
    .select("*").single();
  if (error) throw error;
  return bookFromRow(data);
}

export async function updateBook(id: string, input: { title: string; description: string }): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("books").update(input).eq("id", id);
  if (error) throw error;
}

export async function deleteBook(id: string): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("books").delete().eq("id", id);
  if (error) throw error;
}
```

- [ ] **Step 2: parts.ts**
```ts
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
```
- [ ] **Step 3: Проверка типов** — `npx tsc --noEmit`.
- [ ] **Step 4: Commit** — `feat: books and parts repositories`

---

### Task 5: Репозитории вопросов, учеников/доступов, проверки

**Files:**
- Create: `src/lib/db/questions.ts`
- Create: `src/lib/db/students.ts`
- Create: `src/lib/db/grading.ts`

- [ ] **Step 1: questions.ts**
```ts
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
```

- [ ] **Step 2: students.ts**
```ts
import { createServerSupabase } from "@/lib/supabase/client";
import { studentFromRow } from "@/lib/db/mappers";
import type { Student } from "@/lib/types";

export async function listStudents(): Promise<Student[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("students").select("*").order("created_at");
  if (error) throw error;
  return (data ?? []).map(studentFromRow);
}

export async function listEnrolledBookIds(studentId: string): Promise<string[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("enrollments")
    .select("book_id").eq("student_id", studentId);
  if (error) throw error;
  return (data ?? []).map((r) => r.book_id as string);
}

export async function enroll(studentId: string, bookId: string): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("enrollments")
    .upsert({ student_id: studentId, book_id: bookId }, { onConflict: "student_id,book_id" });
  if (error) throw error;
}

export async function unenroll(studentId: string, bookId: string): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("enrollments").delete()
    .eq("student_id", studentId).eq("book_id", bookId);
  if (error) throw error;
}
```

- [ ] **Step 3: grading.ts** — открытые ответы, ждущие проверки, и выставление оценки.
```ts
import { createServerSupabase } from "@/lib/supabase/client";

export interface PendingAnswer {
  answerId: string;
  studentName: string;
  bookTitle: string;
  partTitle: string;
  questionText: string;
  textAnswer: string | null;
  maxPoints: number;
}

export async function listPendingOpenAnswers(): Promise<PendingAnswer[]> {
  const db = createServerSupabase();
  const { data, error } = await db
    .from("answers")
    .select(`
      id, text_answer,
      questions!inner ( text, points, type ),
      attempts!inner (
        students!inner ( name ),
        parts!inner ( title, books!inner ( title ) )
      )
    `)
    .eq("graded", false)
    .eq("questions.type", "open");
  if (error) throw error;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data ?? []).map((r: any) => ({
    answerId: r.id,
    textAnswer: r.text_answer,
    questionText: r.questions.text,
    maxPoints: r.questions.points,
    studentName: r.attempts.students.name,
    partTitle: r.attempts.parts.title,
    bookTitle: r.attempts.parts.books.title,
  }));
}

export async function gradeAnswer(input: {
  answerId: string; awardedPoints: number; comment: string;
}): Promise<void> {
  const db = createServerSupabase();
  const { error } = await db.from("answers").update({
    graded: true, awarded_points: input.awardedPoints, teacher_comment: input.comment,
  }).eq("id", input.answerId);
  if (error) throw error;
}
```
- [ ] **Step 4: Проверка типов** — `npx tsc --noEmit`.
- [ ] **Step 5: Commit** — `feat: questions, students, grading repositories`

---

### Task 6: Server actions учителя

**Files:**
- Create: `src/app/teacher/actions.ts`

- [ ] **Step 1: Реализация** — каждая функция с `"use server"`, проверяет `isAuthenticated()`
  (кроме `login`), вызывает репозиторий, делает `revalidatePath` где нужно.
```ts
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
  if (!checkPassword(password)) return { error: "Неверный пароль" };
  await startSession();
  redirect("/teacher");
}

export async function logoutAction() {
  await endSession();
  redirect("/teacher/login");
}

export async function createBookAction(formData: FormData) {
  await requireAuth();
  await books.createBook({
    title: String(formData.get("title") ?? ""),
    description: String(formData.get("description") ?? ""),
  });
  revalidatePath("/teacher/books");
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
  const optionsRaw = String(formData.get("options") ?? "");
  const options = type === "choice"
    ? optionsRaw.split("\n").map((s) => s.trim()).filter(Boolean) : [];
  const correctOptionIndex = type === "choice"
    ? Number(formData.get("correctOptionIndex") ?? 0) : null;
  await questions.createQuestion({
    partId, type,
    text: String(formData.get("text") ?? ""),
    points: Number(formData.get("points") ?? 1),
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

export async function enrollAction(formData: FormData) {
  await requireAuth();
  await students.enroll(String(formData.get("studentId")), String(formData.get("bookId")));
  revalidatePath("/teacher/students");
}

export async function unenrollAction(formData: FormData) {
  await requireAuth();
  await students.unenroll(String(formData.get("studentId")), String(formData.get("bookId")));
  revalidatePath("/teacher/students");
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
```
- [ ] **Step 2: Проверка типов** — `npx tsc --noEmit`.
- [ ] **Step 3: Commit** — `feat: teacher server actions`

---

### Task 7: Каркас панели + вход

Использовать навык **frontend-design** для аккуратного, спокойного интерфейса
(чистая типографика, нейтральная палитра, читабельные формы; без «AI-шаблонности»).

**Files:**
- Create: `src/app/teacher/layout.tsx` — боковая навигация: Обзор, Книги, Ученики, Проверка; кнопка «Выйти» (форма с `logoutAction`).
- Create: `src/app/teacher/login/page.tsx` — форма пароля (`loginAction`), показ ошибки.

- [ ] **Step 1:** Реализовать `login/page.tsx` (client component с `useActionState` для показа ошибки от `loginAction`).
- [ ] **Step 2:** Реализовать `layout.tsx` с навигацией и кнопкой выхода.
- [ ] **Step 3:** `npm run build` — собирается без ошибок.
- [ ] **Step 4: Commit** — `feat: teacher panel layout and login`

---

### Task 8: Экраны книг, частей и конструктора тестов

Серверные компоненты читают данные через репозитории; формы вызывают server actions.

**Files:**
- Create: `src/app/teacher/books/page.tsx` — список книг (`listBooks`), форма создания, удаление.
- Create: `src/app/teacher/books/[bookId]/page.tsx` — части книги (`listParts`), форма
  добавления части (заголовок + текст), редактирование/удаление, ссылка на конструктор теста.
- Create: `src/app/teacher/books/[bookId]/parts/[partId]/page.tsx` — список вопросов
  (`listQuestions`), форма добавления вопроса: выбор типа (choice/open), текст, баллы; для
  choice — варианты (по строке на вариант) и номер правильного; удаление вопроса.

- [ ] **Step 1:** Реализовать `books/page.tsx`.
- [ ] **Step 2:** Реализовать `books/[bookId]/page.tsx`.
- [ ] **Step 3:** Реализовать конструктор `parts/[partId]/page.tsx` (форма переключает
  поля вариантов в зависимости от выбранного типа — client component).
- [ ] **Step 4:** `npm run build` без ошибок.
- [ ] **Step 5: Commit** — `feat: teacher books, parts and test builder screens`

---

### Task 9: Экраны учеников, проверки и обзора

**Files:**
- Create: `src/app/teacher/students/page.tsx` — список учеников (`listStudents`); у каждого —
  какие книги выданы (`listEnrolledBookIds`), форма выдачи/снятия доступа (выбор книги).
- Create: `src/app/teacher/grading/page.tsx` — список ожидающих проверки открытых ответов
  (`listPendingOpenAnswers`); по каждому — ученик, книга/часть, вопрос, ответ; форма
  «баллы (0..max) + комментарий» → `gradeAction`.
- Create: `src/app/teacher/page.tsx` — обзор: список учеников и их прогресс. Прогресс
  считать в server component: для каждого ученика по каждой выданной книге собрать статусы
  частей (через attempts/answers) и применить `computeBookProgress`/`computePartStatus`
  из `src/lib/progress`. Добавить в `src/lib/db/grading.ts` или новый
  `src/lib/db/overview.ts` функцию `studentBookProgress(studentId, bookId)`.

- [ ] **Step 1:** Добавить `src/lib/db/overview.ts` с `studentBookProgress` (читает части
  книги, attempts ученика, answers; через `computePartStatus` определяет завершённые части
  и их баллы; возвращает `computeBookProgress`). Сигнатура:
```ts
import type { BookProgress } from "@/lib/progress/bookProgress";
export async function studentBookProgress(studentId: string, bookId: string): Promise<BookProgress>;
```
- [ ] **Step 2:** Реализовать `students/page.tsx`.
- [ ] **Step 3:** Реализовать `grading/page.tsx`.
- [ ] **Step 4:** Реализовать `page.tsx` (обзор) с использованием `studentBookProgress`.
- [ ] **Step 5:** `npm run build` без ошибок; `npm test` всё зелёное.
- [ ] **Step 6: Commit** — `feat: teacher students, grading and overview screens`

---

## Self-Review (выполнено при написании плана)

- **Покрытие спеца:** вход учителя → Task 1–2,7; книги/части/текст → Task 4,8;
  конструктор тестов → Task 5,8; ученики и доступы → Task 5,9; очередь проверки → Task 5,9;
  обзор прогресса → Task 9 (переиспользует логику этапа 1).
- **Плейсхолдеры:** контракты функций, тесты и схемы запросов заданы явно. UI-задачи
  (7–9) намеренно описаны как экраны с конкретными источниками данных и действиями —
  визуальная реализация через frontend-design, без выдуманных деталей.
- **Согласованность:** имена репозиториев и server actions, поля FormData и пути
  revalidatePath согласованы между задачами; доменные типы из `@/lib/types` и логика
  из `@/lib/progress` переиспользуются, не дублируются.
- **Тесты без БД:** покрыты подпись сессии (Task 1) и мапперы (Task 3); запросы к
  Supabase проверяются на реальной базе на этапе 4.
