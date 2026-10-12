import { describe, it, expect } from "vitest";
import { unlockedThroughLevel, isLevelUnlocked, type LevelBook } from "@/lib/progress/levelUnlock";
import type { PartStatus } from "@/lib/types";

// Книга уровня `level` с частями p{level}-1 … p{level}-{count}
function book(level: number, count: number): LevelBook {
  return {
    level,
    partIds: Array.from({ length: count }, (_, i) => `p${level}-${i + 1}`),
  };
}

// Статусы: перечисленные части completed, остальные not_started
function statuses(books: LevelBook[], completed: string[]): Map<string, PartStatus> {
  const m = new Map<string, PartStatus>();
  for (const b of books) {
    for (const id of b.partIds) {
      m.set(id, completed.includes(id) ? "completed" : "not_started");
    }
  }
  return m;
}

describe("unlockedThroughLevel", () => {
  const all = [book(1, 2), book(2, 2), book(3, 2), book(4, 2)];

  it("открывает уровни до стартового включительно", () => {
    expect(unlockedThroughLevel(all, 3, statuses(all, []))).toBe(3);
  });

  it("открывает следующий уровень, когда стартовый пройден целиком", () => {
    const done = statuses(all, ["p3-1", "p3-2"]);
    expect(unlockedThroughLevel(all, 3, done)).toBe(4);
  });

  it("не открывает следующий, пока пройдена лишь часть книги", () => {
    const done = statuses(all, ["p3-1"]);
    expect(unlockedThroughLevel(all, 3, done)).toBe(3);
  });

  it("не считает пройденной часть на проверке у учителя", () => {
    const m = statuses(all, ["p3-1"]);
    m.set("p3-2", "pending_review");
    expect(unlockedThroughLevel(all, 3, m)).toBe(3);
  });

  it("поднимается через несколько уровней подряд", () => {
    const done = statuses(all, ["p1-1", "p1-2", "p2-1", "p2-2"]);
    expect(unlockedThroughLevel(all, 1, done)).toBe(3);
  });

  it("пустая книга не открывает следующий уровень", () => {
    const books = [book(1, 0), book(2, 2)];
    expect(unlockedThroughLevel(books, 1, statuses(books, []))).toBe(1);
  });

  it("останавливается на дыре в нумерации уровней", () => {
    const books = [book(1, 1), book(3, 1)];
    expect(unlockedThroughLevel(books, 1, statuses(books, ["p1-1"]))).toBe(2);
  });

  it("без книг возвращает стартовый уровень", () => {
    expect(unlockedThroughLevel([], 2, new Map())).toBe(2);
  });
});

describe("isLevelUnlocked", () => {
  it("уровень открыт, если не выше достигнутого", () => {
    expect(isLevelUnlocked(1, 3)).toBe(true);
    expect(isLevelUnlocked(3, 3)).toBe(true);
    expect(isLevelUnlocked(4, 3)).toBe(false);
  });
});
