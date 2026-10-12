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
