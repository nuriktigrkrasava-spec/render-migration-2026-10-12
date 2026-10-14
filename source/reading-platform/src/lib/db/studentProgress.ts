import { createServerSupabase } from "@/lib/supabase/client";
import { fetchAllRows } from "@/lib/db/paginate";
import { computePartStatus } from "@/lib/progress/partStatus";
import { computeBookProgress, type BookProgress } from "@/lib/progress/bookProgress";
import type { Answer, Part, PartStatus, QuestionScoring } from "@/lib/types";
import type { AnswerRow } from "@/lib/db/rowTypes";

// Только те колонки вопроса, что нужны для статуса и баллов. Текст и варианты
// не запрашиваются: на списке частей они не показываются, а весят больше всего
// остального вместе взятого.
const SCORING_COLUMNS = "id, part_id, type, points, correct_option_index";

export interface ScoringRow {
  id: string;
  part_id: string;
  type: "choice" | "open";
  points: number;
  correct_option_index: number | null;
}

function scoringFromRow(r: ScoringRow): QuestionScoring {
  return {
    id: r.id, partId: r.part_id, type: r.type,
    points: r.points, correctOptionIndex: r.correct_option_index,
  };
}

// Всё, что нужно для подсчёта статусов/прогресса одного ученика по набору
// частей. Раньше эти данные грузились по одной части (N+1: для книги из 30
// частей — ~90 последовательных запросов). Здесь вопросы и попытки с вложенными ответами
// читаются двумя независимыми выборками, при необходимости постранично.
export interface PartData {
  questionsByPart: Map<string, QuestionScoring[]>;
  answersByPart: Map<string, Answer[]>;
}

function answerFromRow(r: AnswerRow): Answer {
  return {
    questionId: r.question_id,
    selectedOptionIndex: r.selected_option_index,
    textAnswer: r.text_answer,
    graded: r.graded,
    awardedPoints: r.awarded_points,
    teacherComment: r.teacher_comment,
  };
}

// Попытка вместе с её ответами: PostgREST умеет отдавать связанные строки
// вложенными, и это экономит целый круг к базе — а круг здесь стоит около
// секунды, так что экономия заметна глазом.
export interface AttemptRef {
  id: string;
  part_id: string;
  answers: AnswerRow[];
}

// Вопросы всей платформы в лёгком виде. Фильтр по частям не нужен: экранам
// прогресса нужны вопросы всех книг сразу. Выборка постраничная и независимая
// от списка идентификаторов, поэтому длинный URL не растёт вместе с контентом.
export function fetchScoringQuestions(): Promise<ScoringRow[]> {
  const db = createServerSupabase();
  return fetchAllRows<ScoringRow>((from, to) =>
    db.from("questions").select(SCORING_COLUMNS).order("id").range(from, to),
  );
}

export function fetchStudentAttempts(studentId: string): Promise<AttemptRef[]> {
  const db = createServerSupabase();
  return fetchAllRows<AttemptRef>((from, to) =>
    db.rpc("get_progress_attempts", { p_student_id: studentId, p_from: from, p_to: to }),
  );
}

// Собирает PartData из уже загруженных строк — без единого запроса. Благодаря
// этому вопросы и попытки (с вложенными ответами) уезжают в общий параллельный
// пакет вместе с книгами и частями, и на страницу остаётся один круг к базе
// вместо пяти. При задержке около секунды на круг это решает всё.
export function partDataFrom(
  questionRows: ScoringRow[],
  attempts: AttemptRef[],
): PartData {
  const questionsByPart = new Map<string, QuestionScoring[]>();
  const answersByPart = new Map<string, Answer[]>();

  for (const r of questionRows) {
    const q = scoringFromRow(r);
    const arr = questionsByPart.get(q.partId);
    if (arr) arr.push(q);
    else questionsByPart.set(q.partId, [q]);
  }

  for (const a of attempts) {
    const answers = (a.answers ?? []).map(answerFromRow);
    const arr = answersByPart.get(a.part_id);
    if (arr) arr.push(...answers);
    else answersByPart.set(a.part_id, answers);
  }

  return { questionsByPart, answersByPart };
}

export function statusFor(partId: string, data: PartData): PartStatus {
  return computePartStatus(
    data.questionsByPart.get(partId) ?? [],
    data.answersByPart.get(partId) ?? [],
  );
}

// Баллы за пройденную часть: открытые — выставленные учителем,
// тестовые — points за верный вариант.
function partScore(questions: QuestionScoring[], answers: Answer[]): number {
  return answers.reduce((sum, a) => {
    const q = questions.find((qq) => qq.id === a.questionId);
    if (!q) return sum;
    if (q.type === "open") return sum + (a.awardedPoints ?? 0);
    return sum + (a.selectedOptionIndex === q.correctOptionIndex ? q.points : 0);
  }, 0);
}

export function bookProgressFrom(parts: Part[], data: PartData): BookProgress {
  const completed: { score: number }[] = [];
  for (const part of parts) {
    const questions = data.questionsByPart.get(part.id) ?? [];
    const answers = data.answersByPart.get(part.id) ?? [];
    if (computePartStatus(questions, answers) === "completed") {
      completed.push({ score: partScore(questions, answers) });
    }
  }
  return computeBookProgress(parts.length, completed);
}
