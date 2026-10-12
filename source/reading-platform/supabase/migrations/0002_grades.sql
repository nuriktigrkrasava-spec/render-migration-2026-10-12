-- Классы (1–4): к какому классу относится книга и какой класс выбрал ученик.

-- Книга принадлежит классу. Существующие книги по умолчанию — 1 класс.
alter table books
  add column grade int not null default 1
  check (grade between 1 and 4);

-- Ученик выбирает свой класс в мини-приложении. NULL — ещё не выбрал.
alter table students
  add column grade int
  check (grade between 1 and 4);

create index idx_books_grade on books(grade);
create index idx_students_grade on students(grade);

-- Гранты на новые объекты (на случай локального окружения).
grant all on all tables in schema public to anon, authenticated, service_role;
