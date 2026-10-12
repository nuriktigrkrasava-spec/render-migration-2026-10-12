import Link from "next/link";
import { getStudentId } from "@/lib/auth/student";
import { loadLadder, levelsFrom } from "@/lib/db/studentRepo";
import { levelLabel } from "@/lib/options";
import LoadingScreen from "@/components/student/LoadingScreen";
import { ChevronRight, LockIcon } from "@/components/student/icons";
import styles from "./app.module.css";

export const dynamic = "force-dynamic";

export default async function StudentHomePage() {
  const studentId = await getStudentId();
  if (!studentId) return <LoadingScreen />;

  const ladder = await loadLadder(studentId);
  if (!ladder) return <LoadingScreen />;

  const levels = levelsFrom(ladder);

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <span className={styles.eyebrow}>менің деңгейлерім</span>
        <h1 className={styles.title}>Оқу сауаттылығы</h1>
        <p className={styles.subtitle}>Деңгейді бітіріңіз — келесісі ашылады</p>
      </header>

      {levels.length === 0 ? (
        <div className={styles.notice}>
          <span className={styles.noticeIcon}>📚</span>
          Әзірге кітап жоқ. Кейінірек қараңыз
        </div>
      ) : (
        <div className={styles.cardList}>
          {levels.map(({ book, progress, unlocked }) => {
            const inner = (
              <>
                <div className={styles.bookTop}>
                  <div>
                    <div className={styles.levelBadge}>{levelLabel(book.level)}</div>
                    <div className={styles.bookTitle}>{book.title}</div>
                    {book.description ? (
                      <div className={styles.bookDesc}>{book.description}</div>
                    ) : null}
                  </div>
                  {unlocked ? <ChevronRight className={styles.chevron} /> : <LockIcon />}
                </div>
                {unlocked ? (
                  <div className={styles.progress}>
                    <div className={styles.progressMeta}>
                      <span>{progress.completedParts} бөлім аяқталды</span>
                      <span className={styles.progressPercent}>{progress.percent}%</span>
                    </div>
                    <div className={styles.bar}>
                      <div
                        className={styles.barFill}
                        style={{ width: `${progress.percent}%` }}
                      />
                    </div>
                  </div>
                ) : (
                  <div className={styles.lockedNote}>
                    Алдыңғы деңгейді бітіргенде ашылады
                  </div>
                )}
              </>
            );

            return unlocked ? (
              <Link key={book.id} href={`/app/books/${book.id}`} className={styles.bookCard}>
                {inner}
              </Link>
            ) : (
              <div key={book.id} className={`${styles.bookCard} ${styles.bookCardLocked}`}>
                {inner}
              </div>
            );
          })}
        </div>
      )}

      <Link href="/app/progress" className={styles.back} style={{ marginTop: 4 }}>
        Үлгерім
        <ChevronRight />
      </Link>
    </div>
  );
}
