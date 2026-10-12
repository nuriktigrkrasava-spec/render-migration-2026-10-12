-- Уровни вместо классов. Один уровень — одна книга.
--
-- Миграция ДОБАВЛЯЮЩАЯ: старые колонки grade остаются на месте, чтобы
-- работающая версия сайта не сломалась между применением SQL и выкатом кода.
-- Их удаляет отдельная миграция 0004, которую запускают позже.

-- Книга получает уровень, равный её прежнему классу.
alter table books add column if not exists level int;
update books set level = grade where level is null;
alter table books alter column level set not null;
alter table books add constraint books_level_key unique (level);

-- Стартовый уровень ученика: с него он начинает лестницу.
-- Все начинают с первого уровня — прежний класс не переносится (решение
-- владельца платформы). Пройденные части при этом никуда не деваются:
-- дойдя до своего уровня заново, ученик увидит их уже отмеченными.
-- Учитель может поднять стартовый уровень любому на странице «Оқушылар».
alter table students add column if not exists start_level int not null default 1;

-- Таблица доступов осталась от первой версии и никогда не использовалась.
drop table if exists enrollments;

create index if not exists idx_books_level on books(level);

grant all on all tables in schema public to anon, authenticated, service_role;
