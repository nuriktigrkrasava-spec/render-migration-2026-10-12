export interface BookRow {
  id: string;
  title: string;
  description: string;
  cover_url: string | null;
  level: number;
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
  start_level: number;
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
