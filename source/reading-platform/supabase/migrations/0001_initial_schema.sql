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

-- Доступ ролей Supabase к таблицам (чтобы серверный ключ мог читать/писать).
-- На облачном Supabase это обычно настроено по умолчанию, но явные гранты
-- гарантируют работу на любом окружении (включая локальный запуск).
grant usage on schema public to anon, authenticated, service_role;
grant all on all tables in schema public to anon, authenticated, service_role;
grant all on all sequences in schema public to anon, authenticated, service_role;
