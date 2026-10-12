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
