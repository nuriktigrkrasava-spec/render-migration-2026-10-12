# Этап 1: Фундамент и данные — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать проект Next.js, схему базы Supabase под модель данных платформы и проверенную тестами бизнес-логику (статусы прохождения, последовательная разблокировка частей, подсчёт прогресса).

**Architecture:** Монорепо-приложение на Next.js (App Router, TypeScript). Данные в Postgres (Supabase). Бизнес-логика прогресса вынесена в чистые функции в `src/lib/progress/` и покрыта unit-тестами (Vitest) — она не зависит от БД и тестируется изолированно. Схема БД описана SQL-миграциями в `supabase/migrations/`.

**Tech Stack:** Next.js 15 (App Router), TypeScript, Supabase (Postgres + supabase-js), Vitest для unit-тестов.

---

## Структура файлов (этап 1)

- `package.json`, `tsconfig.json`, `next.config.ts` — конфигурация проекта.
- `vitest.config.ts` — конфигурация тестов.
- `supabase/migrations/0001_initial_schema.sql` — таблицы и связи.
- `src/lib/types.ts` — типы домена (Student, Book, Part, Question, Attempt, Answer, статусы).
- `src/lib/progress/partStatus.ts` — вычисление статуса части по ответам.
- `src/lib/progress/partStatus.test.ts` — тесты статуса части.
- `src/lib/progress/unlock.ts` — какие части доступны ученику (последовательно).
- `src/lib/progress/unlock.test.ts` — тесты разблокировки.
- `src/lib/progress/bookProgress.ts` — процент прохождения и средний балл по книге.
- `src/lib/progress/bookProgress.test.ts` — тесты прогресса книги.
- `src/lib/supabase/client.ts` — инициализация клиента Supabase из env.

Бизнес-логика (`partStatus`, `unlock`, `bookProgress`) — чистые функции без обращений
к БД, поэтому тестируются без сети. Файлы маленькие, одна ответственность на каждый.

---

### Task 1: Инициализация проекта Next.js + TypeScript

**Files:**
- Create: `package.json`, `tsconfig.json`, `next.config.ts`, `.gitignore`

- [ ] **Step 1: Создать Next.js проект**

Run в корне репозитория:
```bash
npx create-next-app@latest . --typescript --app --no-tailwind --eslint --src-dir --import-alias "@/*" --no-turbopack --use-npm
```
Если спросит про перезапись существующих файлов (docs/) — оставить их (не перезаписывать).

- [ ] **Step 2: Проверить, что проект запускается**

Run:
```bash
npm run build
```
Expected: сборка проходит без ошибок (генерируется `.next/`).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: scaffold Next.js + TypeScript project"
```

---

### Task 2: Настроить Vitest

**Files:**
- Create: `vitest.config.ts`
- Create: `src/lib/sanity.test.ts`
- Modify: `package.json` (добавить скрипт `test`)

- [ ] **Step 1: Установить Vitest**

Run:
```bash
npm install -D vitest
```

- [ ] **Step 2: Создать конфиг**

`vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
});
```

- [ ] **Step 3: Добавить скрипт в package.json**

В разделе `"scripts"` добавить:
```json
"test": "vitest run"
```

- [ ] **Step 4: Написать sanity-тест**

`src/lib/sanity.test.ts`:
```ts
import { describe, it, expect } from "vitest";

describe("sanity", () => {
  it("runs the test framework", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5: Запустить тест — должен пройти**

Run: `npm test`
Expected: PASS, 1 тест зелёный.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: configure Vitest"
```

---

### Task 3: Типы домена

**Files:**
- Create: `src/lib/types.ts`

- [ ] **Step 1: Описать типы**

`src/lib/types.ts`:
```ts
export type QuestionType = "choice" | "open";

export type PartStatus =
  | "not_started"
  | "in_progress"
  | "pending_review"
  | "completed";

export interface Student {
  id: string;
  telegramId: string;
  name: string;
}

export interface Book {
  id: string;
  title: string;
  description: string;
  coverUrl: string | null;
}

export interface Part {
  id: string;
  bookId: string;
  order: number; // 1..N
  title: string;
  readingText: string;
}

export interface Question {
  id: string;
  partId: string;
  type: QuestionType;
  text: string;
  points: number;
  // только для type === "choice":
  options: string[];
  correctOptionIndex: number | null;
}

export interface Answer {
  questionId: string;
  // для choice: индекс выбранного варианта; для open: текст
  selectedOptionIndex: number | null;
  textAnswer: string | null;
  // проверка открытого ответа учителем:
  graded: boolean;
  awardedPoints: number | null;
  teacherComment: string | null;
}

export interface Attempt {
  id: string;
  studentId: string;
  partId: string;
  answers: Answer[];
}
```

- [ ] **Step 2: Проверить компиляцию типов**

Run: `npx tsc --noEmit`
Expected: без ошибок.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: add domain types"
```

---

### Task 4: Статус части (partStatus)

Часть **завершена**, когда на все вопросы есть ответы И все открытые вопросы
проверены учителем. Если ответов нет — `not_started`. Если есть, но не на все
вопросы — `in_progress`. Если на все ответили, но есть непроверенные открытые —
`pending_review`.

**Files:**
- Create: `src/lib/progress/partStatus.ts`
- Test: `src/lib/progress/partStatus.test.ts`

- [ ] **Step 1: Написать падающий тест**

`src/lib/progress/partStatus.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { computePartStatus } from "@/lib/progress/partStatus";
import type { Question, Answer } from "@/lib/types";

const choiceQ = (id: string): Question => ({
  id, partId: "p", type: "choice", text: "q", points: 1,
  options: ["a", "b"], correctOptionIndex: 0,
});
const openQ = (id: string): Question => ({
  id, partId: "p", type: "open", text: "q", points: 1,
  options: [], correctOptionIndex: null,
});
const choiceA = (qid: string, idx: number): Answer => ({
  questionId: qid, selectedOptionIndex: idx, textAnswer: null,
  graded: false, awardedPoints: null, teacherComment: null,
});
const openA = (qid: string, graded: boolean): Answer => ({
  questionId: qid, selectedOptionIndex: null, textAnswer: "ответ",
  graded, awardedPoints: graded ? 1 : null, teacherComment: null,
});

describe("computePartStatus", () => {
  it("not_started when no answers", () => {
    expect(computePartStatus([choiceQ("1")], [])).toBe("not_started");
  });
  it("in_progress when some questions unanswered", () => {
    const qs = [choiceQ("1"), choiceQ("2")];
    expect(computePartStatus(qs, [choiceA("1", 0)])).toBe("in_progress");
  });
  it("pending_review when all answered but open not graded", () => {
    const qs = [choiceQ("1"), openQ("2")];
    const ans = [choiceA("1", 0), openA("2", false)];
    expect(computePartStatus(qs, ans)).toBe("pending_review");
  });
  it("completed when all answered and open graded", () => {
    const qs = [choiceQ("1"), openQ("2")];
    const ans = [choiceA("1", 0), openA("2", true)];
    expect(computePartStatus(qs, ans)).toBe("completed");
  });
  it("completed when only choice questions and all answered", () => {
    const qs = [choiceQ("1")];
    expect(computePartStatus(qs, [choiceA("1", 0)])).toBe("completed");
  });
});
```

- [ ] **Step 2: Запустить тест — должен упасть**

Run: `npx vitest run src/lib/progress/partStatus.test.ts`
Expected: FAIL — `computePartStatus is not a function` / модуль не найден.

- [ ] **Step 3: Реализовать функцию**

`src/lib/progress/partStatus.ts`:
```ts
import type { Question, Answer, PartStatus } from "@/lib/types";

export function computePartStatus(
  questions: Question[],
  answers: Answer[],
): PartStatus {
  if (answers.length === 0) return "not_started";

  const answeredIds = new Set(answers.map((a) => a.questionId));
  const allAnswered = questions.every((q) => answeredIds.has(q.id));
  if (!allAnswered) return "in_progress";

  const openUngraded = questions.some((q) => {
    if (q.type !== "open") return false;
    const ans = answers.find((a) => a.questionId === q.id);
    return !ans || !ans.graded;
  });
  if (openUngraded) return "pending_review";

  return "completed";
}
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `npx vitest run src/lib/progress/partStatus.test.ts`
Expected: PASS, 5 тестов зелёные.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: compute part status from answers"
```

---

### Task 5: Последовательная разблокировка частей (unlock)

Часть доступна, если это первая часть, ИЛИ предыдущая часть завершена. Функция
получает упорядоченный список частей и map статусов, возвращает множество id
доступных частей.

**Files:**
- Create: `src/lib/progress/unlock.ts`
- Test: `src/lib/progress/unlock.test.ts`

- [ ] **Step 1: Написать падающий тест**

`src/lib/progress/unlock.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { unlockedPartIds } from "@/lib/progress/unlock";
import type { Part, PartStatus } from "@/lib/types";

const part = (id: string, order: number): Part => ({
  id, bookId: "b", order, title: `Часть ${order}`, readingText: "...",
});

describe("unlockedPartIds", () => {
  const parts = [part("a", 1), part("b", 2), part("c", 3)];

  it("unlocks only first part when nothing completed", () => {
    const status = new Map<string, PartStatus>();
    expect(unlockedPartIds(parts, status)).toEqual(new Set(["a"]));
  });
  it("unlocks next part after previous completed", () => {
    const status = new Map<string, PartStatus>([["a", "completed"]]);
    expect(unlockedPartIds(parts, status)).toEqual(new Set(["a", "b"]));
  });
  it("does not unlock past a pending_review part", () => {
    const status = new Map<string, PartStatus>([
      ["a", "completed"], ["b", "pending_review"],
    ]);
    expect(unlockedPartIds(parts, status)).toEqual(new Set(["a", "b"]));
  });
  it("unlocks all when all completed", () => {
    const status = new Map<string, PartStatus>([
      ["a", "completed"], ["b", "completed"], ["c", "completed"],
    ]);
    expect(unlockedPartIds(parts, status)).toEqual(new Set(["a", "b", "c"]));
  });
});
```

- [ ] **Step 2: Запустить тест — должен упасть**

Run: `npx vitest run src/lib/progress/unlock.test.ts`
Expected: FAIL — модуль не найден.

- [ ] **Step 3: Реализовать функцию**

`src/lib/progress/unlock.ts`:
```ts
import type { Part, PartStatus } from "@/lib/types";

export function unlockedPartIds(
  parts: Part[],
  statusById: Map<string, PartStatus>,
): Set<string> {
  const ordered = [...parts].sort((a, b) => a.order - b.order);
  const unlocked = new Set<string>();
  for (let i = 0; i < ordered.length; i++) {
    if (i === 0) {
      unlocked.add(ordered[i].id);
      continue;
    }
    const prev = ordered[i - 1];
    if (statusById.get(prev.id) === "completed") {
      unlocked.add(ordered[i].id);
    } else {
      break;
    }
  }
  return unlocked;
}
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `npx vitest run src/lib/progress/unlock.test.ts`
Expected: PASS, 4 теста зелёные.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: sequential part unlocking"
```

---

### Task 6: Прогресс по книге (bookProgress)

Прогресс = завершённые части / всего частей (целые проценты, округление вниз).
Средний балл = сумма набранных баллов по завершённым частям / число завершённых
частей (0, если завершённых нет).

**Files:**
- Create: `src/lib/progress/bookProgress.ts`
- Test: `src/lib/progress/bookProgress.test.ts`

- [ ] **Step 1: Написать падающий тест**

`src/lib/progress/bookProgress.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { computeBookProgress } from "@/lib/progress/bookProgress";

describe("computeBookProgress", () => {
  it("0% and 0 score for empty progress", () => {
    const r = computeBookProgress(30, []);
    expect(r.percent).toBe(0);
    expect(r.averageScore).toBe(0);
    expect(r.completedParts).toBe(0);
  });
  it("computes percent rounded down", () => {
    // 1 из 3 завершено -> 33%
    const r = computeBookProgress(3, [{ score: 10 }]);
    expect(r.percent).toBe(33);
    expect(r.completedParts).toBe(1);
  });
  it("computes average score over completed parts", () => {
    const r = computeBookProgress(4, [{ score: 8 }, { score: 6 }]);
    expect(r.percent).toBe(50);
    expect(r.averageScore).toBe(7);
  });
});
```

- [ ] **Step 2: Запустить тест — должен упасть**

Run: `npx vitest run src/lib/progress/bookProgress.test.ts`
Expected: FAIL — модуль не найден.

- [ ] **Step 3: Реализовать функцию**

`src/lib/progress/bookProgress.ts`:
```ts
export interface CompletedPartScore {
  score: number;
}

export interface BookProgress {
  percent: number;
  averageScore: number;
  completedParts: number;
}

export function computeBookProgress(
  totalParts: number,
  completed: CompletedPartScore[],
): BookProgress {
  const completedParts = completed.length;
  const percent =
    totalParts === 0 ? 0 : Math.floor((completedParts / totalParts) * 100);
  const averageScore =
    completedParts === 0
      ? 0
      : completed.reduce((sum, c) => sum + c.score, 0) / completedParts;
  return { percent, averageScore, completedParts };
}
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `npx vitest run src/lib/progress/bookProgress.test.ts`
Expected: PASS, 3 теста зелёные.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: compute book progress and average score"
```

---

### Task 7: SQL-схема базы (Supabase)

**Files:**
- Create: `supabase/migrations/0001_initial_schema.sql`

- [ ] **Step 1: Написать миграцию**

`supabase/migrations/0001_initial_schema.sql`:
```sql
-- Ученики (опознаются по Telegram)
create table students (
  id uuid primary key default gen_random_uuid(),
  telegram_id text unique not null,
  name text not null,
  created_at timestamptz not null default now()
);

-- Книги
create table books (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text not null default '',
  cover_url text,
  created_at timestamptz not null default now()
);

-- Части книги
create table parts (
  id uuid primary key default gen_random_uuid(),
  book_id uuid not null references books(id) on delete cascade,
  "order" int not null,
  title text not null,
  reading_text text not null default '',
  unique (book_id, "order")
);

-- Вопросы части
create table questions (
  id uuid primary key default gen_random_uuid(),
  part_id uuid not null references parts(id) on delete cascade,
  type text not null check (type in ('choice', 'open')),
  text text not null,
  points int not null default 1,
  options jsonb not null default '[]'::jsonb,
  correct_option_index int
);

-- Доступ ученика к книге
create table enrollments (
  id uuid primary key default gen_random_uuid(),
  student_id uuid not null references students(id) on delete cascade,
  book_id uuid not null references books(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (student_id, book_id)
);

-- Прохождение части учеником
create table attempts (
  id uuid primary key default gen_random_uuid(),
  student_id uuid not null references students(id) on delete cascade,
  part_id uuid not null references parts(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (student_id, part_id)
);

-- Ответы на вопросы внутри прохождения
create table answers (
  id uuid primary key default gen_random_uuid(),
  attempt_id uuid not null references attempts(id) on delete cascade,
  question_id uuid not null references questions(id) on delete cascade,
  selected_option_index int,
  text_answer text,
  graded boolean not null default false,
  awarded_points int,
  teacher_comment text,
  unique (attempt_id, question_id)
);

create index idx_parts_book on parts(book_id);
create index idx_questions_part on questions(part_id);
create index idx_attempts_student on attempts(student_id);
create index idx_answers_attempt on answers(attempt_id);
```

- [ ] **Step 2: Проверить SQL синтаксис локально (необязательно, если есть psql)**

Если установлен Postgres/psql — можно прогнать на пустой тестовой базе:
```bash
psql "$TEST_DATABASE_URL" -f supabase/migrations/0001_initial_schema.sql
```
Expected: все `CREATE TABLE` выполняются без ошибок.
Если psql нет — пропустить; миграция применится при настройке Supabase на этапе 4.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: initial database schema"
```

---

### Task 8: Клиент Supabase

**Files:**
- Create: `src/lib/supabase/client.ts`
- Create: `.env.local.example`
- Modify: `package.json` (зависимость `@supabase/supabase-js`)

- [ ] **Step 1: Установить supabase-js**

Run:
```bash
npm install @supabase/supabase-js
```

- [ ] **Step 2: Создать пример env**

`.env.local.example`:
```
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
TELEGRAM_BOT_TOKEN=your-bot-token
TEACHER_PASSWORD=choose-a-strong-password
```

- [ ] **Step 3: Создать клиент**

`src/lib/supabase/client.ts`:
```ts
import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

// Серверный клиент с сервисным ключом — используется только в серверном коде
// (route handlers, server actions). Никогда не импортировать в клиентские компоненты.
export function createServerSupabase() {
  if (!url || !serviceKey) {
    throw new Error("Supabase env vars are not set");
  }
  return createClient(url, serviceKey, {
    auth: { persistSession: false },
  });
}
```

- [ ] **Step 4: Проверить компиляцию**

Run: `npx tsc --noEmit`
Expected: без ошибок.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add Supabase server client"
```

---

### Task 9: Прогон всех тестов и сборки

- [ ] **Step 1: Все unit-тесты зелёные**

Run: `npm test`
Expected: PASS — все тесты (partStatus, unlock, bookProgress, sanity).

- [ ] **Step 2: Сборка проходит**

Run: `npm run build`
Expected: успешная сборка без ошибок типов.

- [ ] **Step 3: Финальный commit этапа**

```bash
git add -A
git commit -m "chore: phase 1 foundation complete"
```

---

## Self-Review (выполнено при написании плана)

- **Покрытие спеца:** модель данных → Task 3 + Task 7; логика «часть завершена» →
  Task 4; разблокировка по порядку → Task 5; прогресс книги и средний балл →
  Task 6; стек (Next.js/Supabase) → Task 1/8. Панель учителя, Mini App и деплой —
  отдельные этапы 2–4 (вне этого плана сознательно).
- **Плейсхолдеры:** не обнаружено — во всех шагах реальный код и команды.
- **Согласованность типов:** `computePartStatus`, `unlockedPartIds`,
  `computeBookProgress`, типы `PartStatus`/`Question`/`Answer`/`Part` используются
  одинаково между задачами и тестами.
