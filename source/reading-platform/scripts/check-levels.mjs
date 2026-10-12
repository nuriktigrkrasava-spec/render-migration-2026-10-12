// Проверяет, что миграция 0003 применена к боевой базе и данные перенесены
// один в один. Запуск: node --env-file=.env.local scripts/check-levels.mjs
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) {
  console.error("Нет NEXT_PUBLIC_SUPABASE_URL или SUPABASE_SERVICE_ROLE_KEY");
  process.exit(1);
}

async function get(path) {
  const res = await fetch(`${url}/rest/v1/${path}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(JSON.stringify(body));
  return body;
}

const books = await get("books?select=id,title,level&order=level");
const students = await get("students?select=id,name,start_level");

const levels = books.map((b) => b.level);
if (levels.some((l) => typeof l !== "number")) {
  throw new Error("У книги нет уровня — миграция 0003 не применена");
}
if (new Set(levels).size !== levels.length) {
  throw new Error("Уровни книг повторяются");
}
if (students.some((s) => typeof s.start_level !== "number")) {
  throw new Error("У ученика нет start_level — миграция 0003 не применена");
}

for (const b of books) console.log(`уровень ${b.level}: ${b.title}`);
console.log(`учеников: ${students.length}, все со стартовым уровнем — ок`);
