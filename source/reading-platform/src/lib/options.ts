// Буквенная маркировка вариантов ответа: 0 → A, 1 → B, 2 → C, 3 → D …
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function optionLetter(index: number): string {
  if (index < 0) return "?";
  if (index < LETTERS.length) return LETTERS[index];
  return String(index + 1); // запасной вариант, если букв не хватит
}

// Подпись уровня в интерфейсе. Уровень — это книга, поэтому рядом с этой
// подписью всегда показывается название книги. Фиксированного списка уровней
// нет: он строится из списка книг.
export function levelLabel(level: number): string {
  return `${level}-деңгей`;
}
