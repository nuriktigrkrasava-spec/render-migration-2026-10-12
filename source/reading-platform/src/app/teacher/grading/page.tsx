import { listPendingOpenAnswers } from "@/lib/db/grading";
import { gradeAction } from "@/app/teacher/actions";
import PageHeader from "@/components/teacher/PageHeader";
import SubmitButton from "@/components/teacher/SubmitButton";
import styles from "./grading.module.css";

export const dynamic = "force-dynamic";

export default async function GradingPage() {
  const pending = await listPendingOpenAnswers();

  return (
    <>
      <PageHeader
        eyebrow="Тексеру кезегі"
        title="Тексеру"
        description="Бағалауыңызды күтіп тұрған оқушылардың ашық жауаптары. Ұпай қойып, қаласаңыз пікір қалдырыңыз."
        action={
          pending.length > 0 ? (
            <span className="t-chip t-chip-clay">
              {pending.length} тексеруде
            </span>
          ) : undefined
        }
      />

      {pending.length === 0 ? (
        <div className="t-empty">Тексеруге жауап жоқ</div>
      ) : (
        <div className={styles.list}>
          {pending.map((item) => (
            <article key={item.answerId} className={styles.card}>
              <div className={styles.crumbs}>
                <span className={styles.student}>{item.studentName}</span>
                <span className={styles.sep}>·</span>
                <span>{item.bookTitle}</span>
                <span className={styles.sep}>·</span>
                <span>{item.partTitle}</span>
              </div>

              <div className={styles.body}>
                <div>
                  <p className={styles.question}>Сұрақ</p>
                  <p className={styles.qText}>{item.questionText}</p>

                  <p className={styles.answerLabel}>Оқушының жауабы</p>
                  {item.textAnswer ? (
                    <p className={styles.answer}>{item.textAnswer}</p>
                  ) : (
                    <p className={`${styles.answer} ${styles.answerEmpty}`}>
                      Оқушы мәтін қалдырмады.
                    </p>
                  )}
                </div>

                <form action={gradeAction} className={styles.gradeForm}>
                  <input type="hidden" name="answerId" value={item.answerId} />
                  <div>
                    <label
                      className="t-label"
                      htmlFor={`points-${item.answerId}`}
                    >
                      Ұпай
                    </label>
                    <input
                      id={`points-${item.answerId}`}
                      name="awardedPoints"
                      type="number"
                      min={0}
                      max={item.maxPoints}
                      defaultValue={0}
                      required
                      className="t-input"
                    />
                    <p className={styles.maxHint}>
                      Ең көбі: {item.maxPoints}
                    </p>
                  </div>
                  <div>
                    <label
                      className="t-label"
                      htmlFor={`comment-${item.answerId}`}
                    >
                      Пікір
                    </label>
                    <textarea
                      id={`comment-${item.answerId}`}
                      name="comment"
                      rows={3}
                      className="t-textarea"
                      placeholder="Міндетті емес — бағаға түсініктеме"
                    />
                  </div>
                  <SubmitButton
                    className="t-btn t-btn-primary"
                    pendingText="Сақталуда…"
                  >
                    Жауапты бағалау
                  </SubmitButton>
                </form>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
