-- Server sessions use service_role; browser/anonymous clients must not access
-- student identities, correct answers or grades directly.
alter table public.students enable row level security;
alter table public.books enable row level security;
alter table public.parts enable row level security;
alter table public.questions enable row level security;
alter table public.attempts enable row level security;
alter table public.answers enable row level security;
revoke all on public.students, public.books, public.parts, public.questions,
  public.attempts, public.answers from anon, authenticated;
grant select, insert, update, delete on public.students, public.books, public.parts,
  public.questions, public.attempts, public.answers to service_role;

create or replace function public.submit_part_atomic(
  p_student_id uuid, p_part_id uuid, p_answers jsonb
) returns boolean language plpgsql security invoker set search_path = public as $$
declare
  v_attempt_id uuid;
begin
  if p_answers is null or jsonb_typeof(p_answers) <> 'array' or jsonb_array_length(p_answers) = 0 then
    return false;
  end if;
  if exists (
    select 1 from jsonb_array_elements(p_answers) as a
    left join public.questions q on q.id = (a->>'question_id')::uuid
    where q.id is null or q.part_id <> p_part_id
  ) or (select count(distinct a->>'question_id') from jsonb_array_elements(p_answers) a)
       <> jsonb_array_length(p_answers) then
    raise exception 'Invalid or duplicate part answers';
  end if;

  insert into public.attempts(student_id, part_id)
  values(p_student_id, p_part_id)
  on conflict(student_id, part_id) do nothing returning id into v_attempt_id;
  if v_attempt_id is null then
    select id into v_attempt_id from public.attempts
    where student_id=p_student_id and part_id=p_part_id for update;
    if exists(select 1 from public.answers where answers.attempt_id=v_attempt_id) then
      return false;
    end if;
  end if;

  insert into public.answers(attempt_id, question_id, selected_option_index, text_answer, graded)
  select v_attempt_id, q.id,
    case when q.type='choice' then (a->>'selected_option_index')::integer else null end,
    case when q.type='open' then a->>'text_answer' else null end,
    q.type='choice'
  from jsonb_array_elements(p_answers) a
  join public.questions q on q.id=(a->>'question_id')::uuid and q.part_id=p_part_id;
  return true;
end;
$$;
revoke all on function public.submit_part_atomic(uuid, uuid, jsonb) from public, anon, authenticated;
grant execute on function public.submit_part_atomic(uuid, uuid, jsonb) to service_role;


-- One JSON record avoids the row caps that apply to ordinary/nested lists.
create or replace function public.get_scoring_catalog()
returns jsonb language sql stable security invoker set search_path = public as $$
  select jsonb_build_object(
    'parts', coalesce((select jsonb_agg(jsonb_build_object(
      'id', id, 'book_id', book_id, 'order', "order", 'title', title
    ) order by book_id, "order") from public.parts), '[]'::jsonb),
    'questions', coalesce((select jsonb_agg(jsonb_build_object(
      'id', id, 'part_id', part_id, 'type', type, 'points', points,
      'correct_option_index', correct_option_index
    ) order by id) from public.questions), '[]'::jsonb)
  );
$$;
revoke all on function public.get_scoring_catalog() from public, anon, authenticated;
grant execute on function public.get_scoring_catalog() to service_role;

-- Page attempts and aggregate answers before PostgREST serializes the result.
create or replace function public.get_progress_attempts(
  p_student_id uuid default null, p_from integer default 0, p_to integer default 999
) returns jsonb language sql stable security invoker set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', a.id, 'student_id', a.student_id, 'part_id', a.part_id,
    'answers', coalesce((select jsonb_agg(to_jsonb(ans) order by ans.id)
                        from public.answers ans where ans.attempt_id=a.id), '[]'::jsonb)
  ) order by a.id), '[]'::jsonb)
  from (select id, student_id, part_id from public.attempts
        where p_student_id is null or student_id=p_student_id
        order by id offset greatest(p_from,0)
        limit least(greatest(p_to-p_from+1,0),1000)) a;
$$;
revoke all on function public.get_progress_attempts(uuid, integer, integer) from public, anon, authenticated;
grant execute on function public.get_progress_attempts(uuid, integer, integer) to service_role;
