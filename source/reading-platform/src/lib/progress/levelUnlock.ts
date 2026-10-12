import type { PartStatus } from "@/lib/types";

// Книга в лестнице уровней: её уровень и идентификаторы частей.
export interface LevelBook {
  level: number;
  partIds: string[];
}

// До какого уровня включительно ученику открыт доступ.
//
// Стартовый уровень задаёт учитель — с него ученик и начинает, поэтому те,
// кто уже читал книгу «своего класса», ничего не теряют. Дальше лестница
// идёт вверх: пройденная целиком книга открывает следующую.
//
// Книга без частей пройденной не считается — иначе пустой уровень,
// который учитель ещё не наполнил, открывал бы всё за собой.
export function unlockedThroughLevel(
  books: LevelBook[],
  startLevel: number,
  statusById: Map<string, PartStatus>,
): number {
  const byLevel = new Map<number, LevelBook>();
  for (const b of books) byLevel.set(b.level, b);

  let through = startLevel;
  // Шагов не больше, чем книг: защита от зацикливания на кривых данных.
  for (let i = 0; i < books.length; i++) {
    const current = byLevel.get(through);
    if (!current || current.partIds.length === 0) break;
    const done = current.partIds.every(
      (id) => statusById.get(id) === "completed",
    );
    if (!done) break;
    through += 1;
  }
  return through;
}

export function isLevelUnlocked(level: number, unlockedThrough: number): boolean {
  return level <= unlockedThrough;
}
