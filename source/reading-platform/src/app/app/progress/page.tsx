import Link from "next/link";
import { getStudentId } from "@/lib/auth/student";
import { loadLadder, levelsFrom } from "@/lib/db/studentRepo";
import LoadingScreen from "@/components/student/LoadingScreen";
import { ChevronLeft, ChevronRight } from "@/components/student/icons";
import styles from "../app.module.css";

export const dynamic = "force-dynamic";

export default async function ProgressPage() {
  const studentId = await getStudentId();
  if (!studentId) return <LoadingScreen />;

  const ladder = await loadLadder(studentId);
  if (!ladder) return <LoadingScreen />;

  const rows = levelsFrom(ladder).filter((l) => l.unlocked);

  return (
    <div className={styles.shell}>
      <Link href="/app" className={styles.back}>
        <ChevronLeft />
        Барлық деңгейлер
      </Link>

      <header className={styles.header}>
        <span className={styles.eyebrow}>Статистика</span>
        <h1 className={styles.title}>Үлгерім</h1>
        <p className={styles.subtitle}>Барлық кітаптарыңыз бойынша жетістіктер</p>
      </header>

      {rows.length === 0 ? (
        <div className={styles.notice}>
          <span className={styles.noticeIcon}>📊</span>
          Әзірге үлгерім туралы дерек жоқ
        </div>
      ) : (
        <div className={styles.cardList}>
          {rows.map(({ book, progress, pendingReview }) => (
            <Link key={book.id} href={`/app/books/${book.id}`} className={styles.bookCard}>
              <div className={styles.bookTop}>
                <div className={styles.bookTitle}>{book.title}</div>
                <ChevronRight className={styles.chevron} />
              </div>

              <div className={styles.progress}>
                <div className={styles.progressMeta}>
                  <span>{progress.completedParts} бөлім аяқталды</span>
                  <span className={styles.progressPercent}>{progress.percent}%</span>
                </div>
                <div className={styles.bar}>
                  <div className={styles.barFill} style={{ width: `${progress.percent}%` }} />
                </div>
              </div>

              <div className={styles.statRow}>
                <div className={styles.stat}>
                  <span className={styles.statValue}>
                    {progress.averageScore.toFixed(1)}
                  </span>
                  <span className={styles.statLabel}>Орташа ұпай</span>
                </div>
                <div className={styles.stat}>
                  <span className={styles.statValue}>{pendingReview}</span>
                  <span className={styles.statLabel}>Тексеруде</span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
