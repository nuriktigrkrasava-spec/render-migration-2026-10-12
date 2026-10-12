import { listStudents } from "@/lib/db/students";
import { listBooks } from "@/lib/db/books";
import { setStudentStartLevelAction } from "@/app/teacher/actions";
import PageHeader from "@/components/teacher/PageHeader";
import SubmitButton from "@/components/teacher/SubmitButton";
import { levelLabel } from "@/lib/options";
import styles from "./students.module.css";

export const dynamic = "force-dynamic";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default async function StudentsPage() {
  const [students, books] = await Promise.all([listStudents(), listBooks()]);

  return (
    <>
      <PageHeader
        eyebrow="Қауымдастық"
        title="Оқушылар"
        description="Оқушылар қолданбаға құпиясөзбен кіріп, өз аты-жөнін жазады. Мұнда олардың бастапқы деңгейін өзгертуге болады."
      />

      {students.length === 0 ? (
        <div className="t-empty">
          Әзірге оқушылар жоқ. Олар қолданбаға кіргеннен кейін осында көрінеді.
        </div>
      ) : (
        <div className={styles.grid}>
          {students.map((student) => {
            const booksCount = books.filter((b) => b.level <= student.startLevel).length;
            return (
              <article key={student.id} className={styles.card}>
                <div className={styles.head}>
                  <span className={styles.avatar}>{initials(student.name)}</span>
                  <div>
                    <h2 className={styles.name}>{student.name}</h2>
                    <div className={styles.tg}>
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.7"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M22 2L11 13" />
                        <path d="M22 2l-7 20-4-9-9-4 20-7z" />
                      </svg>
                      {student.telegramId}
                    </div>
                  </div>
                </div>

                <div className={styles.block}>
                  <p className={styles.blockTitle}>Бастапқы деңгей</p>
                  <p className={styles.emptyEnrolled}>
                    {levelLabel(student.startLevel)} · қолжетімді кітап: {booksCount}
                  </p>
                </div>

                <div className={styles.block}>
                  <p className={styles.blockTitle}>Деңгейді өзгерту</p>
                  <form action={setStudentStartLevelAction} className={styles.enrollForm}>
                    <input type="hidden" name="studentId" value={student.id} />
                    <select
                      name="level"
                      required
                      className="t-select"
                      defaultValue={student.startLevel}
                    >
                      {books.map((b) => (
                        <option key={b.id} value={b.level}>
                          {levelLabel(b.level)} · {b.title}
                        </option>
                      ))}
                    </select>
                    <SubmitButton
                      className="t-btn t-btn-primary t-btn-sm"
                      pendingText="…"
                    >
                      Сақтау
                    </SubmitButton>
                  </form>
                  <p className={styles.emptyEnrolled}>
                    Бастапқы деңгейден төмен деңгейлер де ашық болады.
                  </p>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}
