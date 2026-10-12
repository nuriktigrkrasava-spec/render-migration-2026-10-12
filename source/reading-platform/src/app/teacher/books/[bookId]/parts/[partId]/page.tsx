import Link from "next/link";
import { notFound } from "next/navigation";
import { listParts } from "@/lib/db/parts";
import { listQuestions } from "@/lib/db/questions";
import { deleteQuestionAction } from "@/app/teacher/actions";
import PageHeader from "@/components/teacher/PageHeader";
import SubmitButton from "@/components/teacher/SubmitButton";
import QuestionBuilder from "@/components/teacher/QuestionBuilder";
import { optionLetter } from "@/lib/options";
import styles from "./part.module.css";

export const dynamic = "force-dynamic";

export default async function PartBuilderPage({
  params,
}: {
  params: Promise<{ bookId: string; partId: string }>;
}) {
  const { bookId, partId } = await params;
  const parts = await listParts(bookId);
  const part = parts.find((p) => p.id === partId);
  if (!part) notFound();

  const questions = await listQuestions(partId);

  return (
    <>
      <PageHeader
        eyebrow={`${part.order}-бөлім · Тест құрастырушы`}
        title={part.title}
        description="Тест жинаңыз: нұсқалы сұрақтар автоматты түрде, ашық сұрақтар «Тексеру» бөлімінде қолмен тексеріледі."
        back={
          <Link
            href={`/teacher/books/${bookId}`}
            className="t-btn t-btn-ghost t-btn-sm"
          >
            ← Кітап бөлімдеріне
          </Link>
        }
      />

      <div className={styles.layout}>
        <div className={styles.questions}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>Тест сұрақтары</span>
            <span className={styles.count}>Барлығы: {questions.length}</span>
          </div>

          {questions.length === 0 ? (
            <div className="t-empty">
              Тестте әзірге сұрақ жоқ. Оң жақтан алғашқысын қосыңыз.
            </div>
          ) : (
            questions.map((q) => (
              <article key={q.id} className={styles.q}>
                <div className={styles.qHead}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className={styles.qMeta}>
                      <span
                        className={
                          q.type === "choice"
                            ? "t-chip t-chip-clay"
                            : "t-chip t-chip-gold"
                        }
                      >
                        {q.type === "choice" ? "Нұсқалармен" : "Ашық"}
                      </span>
                      <span className="t-chip">
                        {q.points} ұпай
                      </span>
                    </div>
                    <p className={styles.qText}>{q.text}</p>
                  </div>

                  <form action={deleteQuestionAction}>
                    <input type="hidden" name="bookId" value={bookId} />
                    <input type="hidden" name="partId" value={partId} />
                    <input type="hidden" name="questionId" value={q.id} />
                    <SubmitButton
                      className="t-btn t-btn-danger t-btn-sm"
                      pendingText="Жойылуда…"
                    >
                      Жою
                    </SubmitButton>
                  </form>
                </div>

                {q.type === "choice" ? (
                  <div className={styles.options}>
                    {q.options.map((opt, i) => {
                      const correct = i === q.correctOptionIndex;
                      return (
                        <div
                          key={i}
                          className={`${styles.option} ${
                            correct ? styles.optionCorrect : ""
                          }`}
                        >
                          <span className={styles.optMark}>
                            {correct && (
                              <svg
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="3"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="M20 6L9 17l-5-5" />
                              </svg>
                            )}
                          </span>
                          <strong style={{ marginRight: 6 }}>{optionLetter(i)}.</strong>
                          {opt}
                          {correct && (
                            <span style={{ marginLeft: "auto", fontSize: 12 }}>
                              дұрыс
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className={styles.openHint}>
                    Ашық жауап — ұпайды мұғалім тексеру кезінде қояды.
                  </p>
                )}
              </article>
            ))
          )}
        </div>

        <aside className={styles.aside}>
          <QuestionBuilder bookId={bookId} partId={partId} />
        </aside>
      </div>
    </>
  );
}
