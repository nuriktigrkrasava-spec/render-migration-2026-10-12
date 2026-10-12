import Link from "next/link";
import { listBooks } from "@/lib/db/books";
import { overviewRows } from "@/lib/db/allProgress";
import { levelLabel } from "@/lib/options";
import PageHeader from "@/components/teacher/PageHeader";
import styles from "./overview.module.css";

export const dynamic = "force-dynamic";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default async function OverviewPage() {
  const [rows, allBooks] = await Promise.all([overviewRows(), listBooks()]);
  const activeEnrollments = rows.reduce(
    (s, r) => s + r.books.filter((b) => b.unlocked).length, 0,
  );

  return (
    <>
      <PageHeader
        eyebrow="Мұғалім кабинеті"
        title="Шолу"
        description="Платформа бойынша жиынтық дерек және оқушылардың кітаптары бойынша үлгерімі."
      />

      <div className={styles.stats}>
        <div className={styles.stat}>
          <div className={styles.statValue}>{rows.length}</div>
          <div className={styles.statLabel}>Платформадағы оқушылар</div>
        </div>
        <div className={styles.stat}>
          <div className={styles.statValue}>{allBooks.length}</div>
          <div className={styles.statLabel}>Кітапханадағы кітаптар</div>
        </div>
        <div className={styles.stat}>
          <div className={styles.statValue}>{activeEnrollments}</div>
          <div className={styles.statLabel}>Оқушылар оқып жатқан кітаптар</div>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="t-empty">
          Әзірге оқушылар жоқ. Олар оқи бастаған бойда үлгерім осында
          көрінеді.
        </div>
      ) : (
        <div className={styles.students}>
          {rows.map(({ student, unlockedThrough, books }) => (
            <section key={student.id} className={styles.student}>
              <div className={styles.studentHead}>
                <span className={styles.avatar}>{initials(student.name)}</span>
                <div>
                  <div className={styles.studentName}>{student.name}</div>
                  <div className={styles.studentMeta}>
                    {`${levelLabel(unlockedThrough)} · ${books.filter((b) => b.unlocked).length} кітап қолжетімді`}
                  </div>
                </div>
              </div>

              {books.length === 0 ? (
                <p className={styles.noBooks}>Әзірге қолжетімді кітап жоқ.</p>
              ) : (
                <div className={styles.books}>
                  {books.map(({ book, progress: p, unlocked }) => (
                    <div key={book.id} className={styles.bookRow}>
                      <Link
                        href={`/teacher/books/${book.id}`}
                        className={styles.bookTitle}
                      >
                        {unlocked ? "" : "🔒 "}
                        {levelLabel(book.level)} · {book.title}
                      </Link>
                      <div className={styles.progressWrap}>
                        <div className={styles.track}>
                          <div
                            className={styles.fill}
                            style={{ width: `${p.percent}%` }}
                          />
                        </div>
                        <span className={styles.percent}>{p.percent}%</span>
                      </div>
                      <span className={styles.partsCount}>
                        {p.completedParts} бөлім аяқталды
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </>
  );
}
