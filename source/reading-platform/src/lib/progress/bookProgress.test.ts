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
