// PostgREST отдаёт не больше max_rows строк за запрос (у Supabase по умолчанию
// 1000). Вопросов в базе уже под тысячу, поэтому выборки, которые растут вместе
// с контентом, идут страницами — иначе однажды они молча обрежутся и прогресс
// начнёт считаться неверно.
// PAGE must be <= PostgREST max_rows; supabase/config.toml sets max_rows=1000.
const PAGE = 1000;

export async function fetchAllRows<T>(
  makeQuery: (from: number, to: number) => PromiseLike<{ data: T[] | null; error: unknown }>,
): Promise<T[]> {
  const out: T[] = [];
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await makeQuery(from, from + PAGE - 1);
    if (error) throw error;
    const rows = data ?? [];
    out.push(...rows);
    if (rows.length < PAGE) return out;
  }
}
