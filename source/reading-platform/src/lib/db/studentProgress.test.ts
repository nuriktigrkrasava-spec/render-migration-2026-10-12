import { describe, it, expect, vi } from "vitest";
vi.mock("@/lib/supabase/client", ()=>({createServerSupabase:vi.fn()}));
import { partDataFrom, statusFor, bookProgressFrom } from "./studentProgress";
describe("progress mapping", () => {
 it("keeps scores and teacher grading for mixed question types", () => {
  const questions = [{id:"choice",part_id:"p",type:"choice" as const,points:2,correct_option_index:1},
   {id:"open",part_id:"p",type:"open" as const,points:5,correct_option_index:null}];
  const answers = [{id:"a",attempt_id:"at",question_id:"choice",selected_option_index:1,text_answer:null,graded:true,awarded_points:null,teacher_comment:null},
   {id:"b",attempt_id:"at",question_id:"open",selected_option_index:null,text_answer:"answer",graded:true,awarded_points:4,teacher_comment:"ok"}];
  const data=partDataFrom(questions,[{id:"at",part_id:"p",answers}]);
  expect(statusFor("p",data)).toBe("completed");
  expect(data.answersByPart.get("p")?.[1].teacherComment).toBe("ok");
  expect(bookProgressFrom([{id:"p",bookId:"book",title:"p",order:1,readingText:""}],data).averageScore).toBe(6);
 });
 it("keeps unanswered and ungraded parts distinguishable", () => {
  const data=partDataFrom([{id:"q",part_id:"p",type:"open",points:1,correct_option_index:null}],[]);
  expect(statusFor("p",data)).not.toBe("completed");
 });
});
