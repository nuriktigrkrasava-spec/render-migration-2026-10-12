# Этап 3: Mini App ученика (Telegram) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Telegram Mini App для ученика: вход через Telegram (по подписи initData), список книг с прогрессом, чтение части → прохождение теста → отправка, мгновенная проверка вопросов с вариантами, последовательная разблокировка частей, экран прогресса.

**Architecture:** Маршруты ученика под `/app`. Telegram передаёт подписанный `initData`; серверная чистая функция `validateInitData(initData, botToken)` проверяет HMAC-подпись и возвращает данные пользователя (покрыта тестами). Клиентский «гейт» при загрузке Mini App читает `window.Telegram.WebApp.initData`, отправляет его в route handler `/api/student/auth`, который валидирует, создаёт/обновляет ученика и ставит подписанную cookie `student_session = <studentId>`. Серверные компоненты под `/app` читают studentId из cookie и отдают данные через репозитории этапов 1–2 и переиспользуют логику `src/lib/progress`. Отправка ответов — server action, создаёт attempt + answers.

**Tech Stack:** Next.js 16 (App Router, TS), Supabase JS, Telegram WebApp JS (`https://telegram.org/js/telegram-web-app.js`), Vitest. Подпись cookie ученика — существующий `src/lib/auth/session.ts` (секрет = `TELEGRAM_BOT_TOKEN`).

**Стратегия тестов:** Без живой БД. Покрываем тестами проверку подписи Telegram initData (`validateInitData`) — это и есть критичная часть безопасности. Репозитории и submit — тонкие обёртки, проверяются на реальной базе на этапе 4.

---

## Структура файлов (этап 3)

- `src/lib/telegram/initData.ts` (+ `.test.ts`) — `validateInitData(initData, botToken)`.
- `src/lib/auth/student.ts` — установка/чтение сессии ученика (cookie), `getStudentId()`.
- `src/lib/db/studentRepo.ts` — `upsertStudentByTelegram`, `enrolledBooksWithProgress`, `partsWithStatus`, `partDetail`, `studentAnswersForPart`.
- `src/lib/db/submit.ts` — `submitPart(studentId, partId, answers)`.
- `src/app/api/student/auth/route.ts` — POST: валидирует initData, апсертит ученика, ставит cookie.
- `src/app/app/actions.ts` — server action `submitPartAction`.
- `src/app/app/layout.tsx` + `src/components/student/TelegramGate.tsx` — загрузка SDK + авторизация.
- `src/app/app/page.tsx` — список книг с прогрессом.
- `src/app/app/books/[bookId]/page.tsx` — части книги со статусами/блокировкой.
- `src/app/app/books/[bookId]/parts/[partId]/page.tsx` — чтение + тест + результат.
- `src/app/app/progress/page.tsx` — мой прогресс.
- CSS Modules рядом с компонентами; темизация по Telegram theme params.

---

### Task 1: Проверка подписи Telegram initData (TDD)

Алгоритм Telegram: из query-строки initData убрать `hash`, собрать `data_check_string` —
строки `key=value`, отсортированные по ключу и соединённые `\n`. `secret_key =
HMAC_SHA256(key="WebAppData", message=botToken)`; ожидаемый хэш = hex(`HMAC_SHA256(
key=secret_key, message=data_check_string)`). Если совпадает с `hash` — initData валиден.
Функция возвращает `{ id, firstName, lastName, username }` из JSON-поля `user`, иначе `null`.

**Files:**
- Create: `src/lib/telegram/initData.ts`
- Test: `src/lib/telegram/initData.test.ts`

- [ ] **Step 1: Падающий тест** (в тесте независимо собираем валидный initData тем же алгоритмом)
```ts
import { describe, it, expect } from "vitest";
import { createHmac } from "node:crypto";
import { validateInitData } from "@/lib/telegram/initData";

const BOT = "123456:TEST_TOKEN";

function buildInitData(params: Record<string, string>, token: string): string {
  const dataCheckString = Object.keys(params).sort()
    .map((k) => `${k}=${params[k]}`).join("\n");
  const secret = createHmac("sha256", "WebAppData").update(token).digest();
  const hash = createHmac("sha256", secret).update(dataCheckString).digest("hex");
  const usp = new URLSearchParams(params);
  usp.set("hash", hash);
  return usp.toString();
}

const user = JSON.stringify({ id: 42, first_name: "Аня", last_name: "П", username: "anya" });

describe("validateInitData", () => {
  it("accepts a correctly signed initData and returns the user", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, BOT);
    expect(validateInitData(initData, BOT)).toEqual({
      id: "42", firstName: "Аня", lastName: "П", username: "anya",
    });
  });
  it("rejects when hash is wrong", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, BOT);
    const bad = initData.replace(/hash=[a-f0-9]+/, "hash=deadbeef");
    expect(validateInitData(bad, BOT)).toBeNull();
  });
  it("rejects when signed with a different token", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, "other:TOKEN");
    expect(validateInitData(initData, BOT)).toBeNull();
  });
  it("rejects when a field was tampered after signing", () => {
    const initData = buildInitData({ auth_date: "1700000000", user }, BOT);
    const tampered = initData.replace("auth_date=1700000000", "auth_date=1700000001");
    expect(validateInitData(tampered, BOT)).toBeNull();
  });
  it("returns null on missing hash", () => {
    expect(validateInitData("user=" + encodeURIComponent(user), BOT)).toBeNull();
  });
});
```

- [ ] **Step 2: Запустить — упадёт** (`npx vitest run src/lib/telegram/initData.test.ts`)

- [ ] **Step 3: Реализация**
```ts
import { createHmac, timingSafeEqual } from "node:crypto";

export interface TelegramUser {
  id: string;
  firstName: string;
  lastName: string;
  username: string;
}

export function validateInitData(initData: string, botToken: string): TelegramUser | null {
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;

  const pairs: string[] = [];
  for (const [key, value] of params.entries()) {
    if (key === "hash") continue;
    pairs.push(`${key}=${value}`);
  }
  pairs.sort();
  const dataCheckString = pairs.join("\n");

  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const expected = createHmac("sha256", secret).update(dataCheckString).digest("hex");

  const a = Buffer.from(hash, "hex");
  const b = Buffer.from(expected, "hex");
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;

  const userRaw = params.get("user");
  if (!userRaw) return null;
  try {
    const u = JSON.parse(userRaw);
    return {
      id: String(u.id),
      firstName: u.first_name ?? "",
      lastName: u.last_name ?? "",
      username: u.username ?? "",
    };
  } catch {
    return null;
  }
}
```
- [ ] **Step 4: Запустить — пройдёт (5 тестов)**
- [ ] **Step 5: Commit** — `feat: validate Telegram initData signature`

---

### Task 2: Сессия ученика + апсерт + route handler авторизации

**Files:**
- Create: `src/lib/auth/student.ts`
- Create: `src/lib/db/studentRepo.ts` (часть 1 — `upsertStudentByTelegram`)
- Create: `src/app/api/student/auth/route.ts`

- [ ] **Step 1: `src/lib/auth/student.ts`**
```ts
import { cookies } from "next/headers";
import { signSession, verifySession } from "@/lib/auth/session";

const COOKIE = "student_session";

function secret(): string {
  const s = process.env.TELEGRAM_BOT_TOKEN;
  if (!s) throw new Error("TELEGRAM_BOT_TOKEN is not set");
  return s;
}

export async function startStudentSession(studentId: string): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, signSession(studentId, secret()), {
    httpOnly: true, sameSite: "lax", secure: true, path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
}

export async function getStudentId(): Promise<string | null> {
  const store = await cookies();
  const token = store.get(COOKIE)?.value;
  if (!token) return null;
  return verifySession(token, secret());
}
```

- [ ] **Step 2: `upsertStudentByTelegram` в `src/lib/db/studentRepo.ts`**
```ts
import { createServerSupabase } from "@/lib/supabase/client";
import { studentFromRow } from "@/lib/db/mappers";
import type { Student } from "@/lib/types";

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
```

- [ ] **Step 3: Route handler `src/app/api/student/auth/route.ts`**
```ts
import { NextResponse, type NextRequest } from "next/server";
import { validateInitData } from "@/lib/telegram/initData";
import { upsertStudentByTelegram } from "@/lib/db/studentRepo";
import { startStudentSession } from "@/lib/auth/student";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const { initData } = await req.json();
  const token = process.env.TELEGRAM_BOT_TOKEN ?? "";
  const user = typeof initData === "string" ? validateInitData(initData, token) : null;
  if (!user) return NextResponse.json({ ok: false }, { status: 401 });

  const name = [user.firstName, user.lastName].filter(Boolean).join(" ")
    || user.username || `id${user.id}`;
  const student = await upsertStudentByTelegram({ telegramId: user.id, name });
  await startStudentSession(student.id);
  return NextResponse.json({ ok: true });
}
```
- [ ] **Step 4: Проверка типов** — `npx tsc --noEmit`.
- [ ] **Step 5: Commit** — `feat: student session auth via Telegram initData`

---

### Task 3: Чтение данных ученика

**Files:**
- Modify: `src/lib/db/studentRepo.ts` (добавить функции чтения)

Добавить в `src/lib/db/studentRepo.ts`:
```ts
import { listParts } from "@/lib/db/parts";
import { listQuestions } from "@/lib/db/questions";
import { computePartStatus } from "@/lib/progress/partStatus";
import { unlockedPartIds } from "@/lib/progress/unlock";
import { studentBookProgress } from "@/lib/db/overview";
import { bookFromRow, questionFromRow } from "@/lib/db/mappers";
import type { Book, Part, PartStatus, Question, Answer } from "@/lib/types";
import type { BookProgress } from "@/lib/progress/bookProgress";

export interface EnrolledBook { book: Book; progress: BookProgress; }

export async function enrolledBooksWithProgress(studentId: string): Promise<EnrolledBook[]> {
  const db = createServerSupabase();
  const { data, error } = await db.from("enrollments")
    .select("books!inner(*)").eq("student_id", studentId);
  if (error) throw error;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const books: Book[] = (data ?? []).map((r: any) => bookFromRow(r.books));
  const result: EnrolledBook[] = [];
  for (const book of books) {
    result.push({ book, progress: await studentBookProgress(studentId, book.id) });
  }
  return result;
}

export interface PartWithStatus { part: Part; status: PartStatus; unlocked: boolean; }

export async function studentAnswersForPart(
  studentId: string, partId: string,
): Promise<Answer[]> {
  const db = createServerSupabase();
  const { data: attempt } = await db.from("attempts")
    .select("id").eq("student_id", studentId).eq("part_id", partId).maybeSingle();
  if (!attempt) return [];
  const { data: rows } = await db.from("answers").select("*").eq("attempt_id", attempt.id);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (rows ?? []).map((r: any) => ({
    questionId: r.question_id,
    selectedOptionIndex: r.selected_option_index,
    textAnswer: r.text_answer,
    graded: r.graded,
    awardedPoints: r.awarded_points,
    teacherComment: r.teacher_comment,
  }));
}

export async function partsWithStatus(
  studentId: string, bookId: string,
): Promise<PartWithStatus[]> {
  const parts = await listParts(bookId);
  const statusById = new Map<string, PartStatus>();
  for (const part of parts) {
    const questions = await listQuestions(part.id);
    const answers = await studentAnswersForPart(studentId, part.id);
    statusById.set(part.id, computePartStatus(questions, answers));
  }
  const unlocked = unlockedPartIds(parts, statusById);
  return parts.map((part) => ({
    part,
    status: statusById.get(part.id) ?? "not_started",
    unlocked: unlocked.has(part.id),
  }));
}

export interface PartDetail {
  part: Part; questions: Question[]; answers: Answer[]; status: PartStatus; unlocked: boolean;
}

export async function partDetail(
  studentId: string, bookId: string, partId: string,
): Promise<PartDetail | null> {
  const all = await partsWithStatus(studentId, bookId);
  const target = all.find((p) => p.part.id === partId);
  if (!target) return null;
  const db = createServerSupabase();
  const { data: qrows } = await db.from("questions").select("*").eq("part_id", partId);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const questions = (qrows ?? []).map((r: any) => questionFromRow(r));
  const answers = await studentAnswersForPart(studentId, partId);
  return { part: target.part, questions, answers, status: target.status, unlocked: target.unlocked };
}
```
- [ ] **Step 1:** Добавить код выше (убедиться, что `createServerSupabase` уже импортирован в файле из Task 2 — если нет, импорт уже есть; не дублировать).
- [ ] **Step 2: Проверка типов** — `npx tsc --noEmit`.
- [ ] **Step 3: Commit** — `feat: student data reads (books, parts status, part detail)`

---

### Task 4: Отправка ответов на часть

При отправке: проверить, что часть разблокирована и ещё не пройдена (нет attempt);
создать attempt; вставить answers по каждому вопросу. Для `choice` — `selected_option_index`,
`graded=true` (автопроверка, оценка учителя не нужна). Для `open` — `text_answer`,
`graded=false`. Идемпотентность: если attempt уже есть — ничего не делать.

**Files:**
- Create: `src/lib/db/submit.ts`
- Create: `src/app/app/actions.ts`

- [ ] **Step 1: `src/lib/db/submit.ts`**
```ts
import { createServerSupabase } from "@/lib/supabase/client";
import { listQuestions } from "@/lib/db/questions";

export interface SubmittedAnswer {
  questionId: string;
  selectedOptionIndex: number | null;
  textAnswer: string | null;
}

export async function submitPart(
  studentId: string, partId: string, submitted: SubmittedAnswer[],
): Promise<void> {
  const db = createServerSupabase();

  const { data: existing } = await db.from("attempts")
    .select("id").eq("student_id", studentId).eq("part_id", partId).maybeSingle();
  if (existing) return; // уже отправлено — идемпотентно

  const questions = await listQuestions(partId);
  const typeById = new Map(questions.map((q) => [q.id, q.type]));

  const { data: attempt, error: aerr } = await db.from("attempts")
    .insert({ student_id: studentId, part_id: partId }).select("id").single();
  if (aerr) throw aerr;

  const rows = submitted.map((s) => {
    const isChoice = typeById.get(s.questionId) === "choice";
    return {
      attempt_id: attempt.id,
      question_id: s.questionId,
      selected_option_index: isChoice ? s.selectedOptionIndex : null,
      text_answer: isChoice ? null : s.textAnswer,
      graded: isChoice, // choice автоматически «проверен»
      awarded_points: null,
      teacher_comment: null,
    };
  });
  const { error: anserr } = await db.from("answers").insert(rows);
  if (anserr) throw anserr;
}
```

- [ ] **Step 2: `src/app/app/actions.ts`**
```ts
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
```
- [ ] **Step 3: Проверка типов** — `npx tsc --noEmit`.
- [ ] **Step 4: Commit** — `feat: submit part attempt and answers`

---

### Task 5: Каркас Mini App + авторизация при загрузке

Использовать навык **frontend-design**; темизацию брать из Telegram theme params
(светлая/тёмная по теме пользователя). Интерфейс мобильный, крупные кнопки.

**Files:**
- Create: `src/app/app/layout.tsx` — подключить `https://telegram.org/js/telegram-web-app.js`
  через `next/script` (strategy `beforeInteractive`); обернуть детей в `TelegramGate`.
- Create: `src/components/student/TelegramGate.tsx` — client component: при монтировании
  вызвать `window.Telegram?.WebApp?.ready()`, прочитать `initData`; если есть — `POST`
  на `/api/student/auth` с `{ initData }`; при `ok` — `router.refresh()`. Пока идёт
  авторизация — показывать «Загрузка…». Если вне Telegram (нет initData) — показать
  сообщение «Откройте приложение из Telegram».

- [ ] **Step 1:** Реализовать `TelegramGate.tsx` (состояния: loading / ready / outside-telegram).
- [ ] **Step 2:** Реализовать `layout.tsx` со скриптом Telegram и базовыми стилями/темой.
- [ ] **Step 3:** Глобальный тип `window.Telegram` — добавить `src/types/telegram.d.ts` с
  минимальным объявлением (`interface Window { Telegram?: { WebApp: { ready(): void; initData: string; colorScheme: "light"|"dark"; themeParams: Record<string,string> } } }`).
- [ ] **Step 4:** `npm run build` без ошибок.
- [ ] **Step 5: Commit** — `feat: mini app shell and Telegram auth gate`

---

### Task 6: Список книг и части книги

Серверные компоненты под `/app`. В начале каждого: `const studentId = await getStudentId();
if (!studentId) return <TelegramGate/>-совместимый плейсхолдер` — проще: если нет studentId,
показать «Загрузка…» (гейт в layout уже инициирует авторизацию и сделает refresh).
Все страницы `export const dynamic = "force-dynamic"`.

**Files:**
- Create: `src/app/app/page.tsx` — `enrolledBooksWithProgress(studentId)`; карточки книг с
  прогресс-баром (percent, «Пройдено X из Y»); ссылка на `/app/books/[bookId]`. Если книг нет —
  «Учитель ещё не открыл вам книги».
- Create: `src/app/app/books/[bookId]/page.tsx` — `partsWithStatus(studentId, bookId)`; список
  частей: завершена (галочка) / на проверке / открыта / закрыта (замок). Открытые/завершённые
  части кликабельны на `/app/books/[bookId]/parts/[partId]`; закрытые — неактивны.

- [ ] **Step 1:** Реализовать `app/page.tsx`.
- [ ] **Step 2:** Реализовать `app/books/[bookId]/page.tsx`.
- [ ] **Step 3:** `npm run build` без ошибок.
- [ ] **Step 4: Commit** — `feat: student book list and parts screens`

---

### Task 7: Часть — чтение, тест и результат

**Files:**
- Create: `src/app/app/books/[bookId]/parts/[partId]/page.tsx` (server) — `partDetail(...)`.
  Логика отображения:
  - если `!unlocked` → «Эта часть пока закрыта»;
  - если `status === "not_started"` или `"in_progress"` → показать текст для чтения и под ним
    клиентскую форму теста `<PartTest>`;
  - если `status === "pending_review"` или `"completed"` → показать текст + блок «результат»:
    по каждому вопросу — ответ ученика; для choice — верно/неверно (сравнение
    `selectedOptionIndex` с `correctOptionIndex`); для open — «на проверке» или оценка
    учителя (`awardedPoints` + `teacherComment`).
- Create: `src/components/student/PartTest.tsx` (client) — форма по вопросам: choice — радиокнопки
  вариантов; open — textarea. Кнопка «Отправить» собирает `SubmittedAnswer[]` и вызывает
  `submitPartAction({ bookId, partId, answers })`; при `ok` — `router.refresh()` (покажется
  результат). Простая клиентская валидация: на все вопросы должен быть ответ.

- [ ] **Step 1:** Реализовать `PartTest.tsx`.
- [ ] **Step 2:** Реализовать `parts/[partId]/page.tsx` (чтение + тест + результат).
- [ ] **Step 3:** `npm run build` без ошибок.
- [ ] **Step 4: Commit** — `feat: student part reading, test and result screen`

---

### Task 8: Экран «Мой прогресс»

**Files:**
- Create: `src/app/app/progress/page.tsx` — `enrolledBooksWithProgress(studentId)`; по каждой
  книге: процент, пройдено частей из всего, средний балл. Если есть части на проверке —
  показать счётчик «N на проверке» (посчитать через `partsWithStatus`, статус
  `pending_review`). Ссылки на книги.

- [ ] **Step 1:** Реализовать `progress/page.tsx`.
- [ ] **Step 2:** `npm run build` без ошибок; `npm test` всё зелёное; `npx tsc --noEmit` чисто.
- [ ] **Step 3: Commit** — `feat: student progress screen`

---

## Self-Review (выполнено при написании плана)

- **Покрытие спеца:** вход через Telegram → Task 1,2,5; список книг с прогрессом → Task 6,8;
  чтение части → Task 7; прохождение теста и отправка → Task 4,7; мгновенная проверка choice
  + открытые учителю → Task 4,7 (graded=true для choice, false для open); последовательная
  разблокировка → Task 3,6 (переиспользует `unlockedPartIds`); экран прогресса → Task 8.
- **Плейсхолдеры:** контракты функций и алгоритм проверки подписи заданы явно; UI-задачи
  описаны через конкретные источники данных, статусы и действия.
- **Согласованность:** `getStudentId`, `submitPartAction`, репозитории `studentRepo`/`submit`,
  типы `Answer`/`Question`/`Part`/`PartStatus` и логика `src/lib/progress` переиспользуются
  без дублирования; cookie ученика подписывается тем же `session.ts`, секрет — `TELEGRAM_BOT_TOKEN`.
- **Безопасность:** initData проверяется на сервере (HMAC), studentId хранится только в
  подписанной httpOnly-cookie; submit берёт studentId из cookie, а не из клиента.
- **Тесты:** критичная проверка подписи Telegram покрыта (Task 1); запросы к БД проверяются
  на этапе 4 на реальной базе.
