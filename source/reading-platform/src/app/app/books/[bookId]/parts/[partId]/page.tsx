import Link from "next/link";
import { getStudentId } from "@/lib/auth/student";
import { loadLadder, partDetail } from "@/lib/db/studentRepo";
import { optionLetter } from "@/lib/options";
import type { Answer, Question } from "@/lib/types";
import LoadingScreen from "@/components/student/LoadingScreen";
import PartTest from "@/components/student/PartTest";
import { ChevronLeft } from "@/components/student/icons";
import styles from "../../../../app.module.css";

export const dynamic = "force-dynamic";

function ChoiceResult({ q, a }: { q: Question; a: Answer | undefined }) {
  const selected = a?.selectedOptionIndex ?? null;
  const correct = q.correctOptionIndex;
  const isCorrect = selected !== null && selected === correct;
  return (
    <div className={styles.resultCard}>
      <div className={styles.qHead}>
        <span className={styles.qText}>{q.text}</span>
      </div>
      <div className={styles.answerLine}>
        <span className={styles.answerLabel}>Сіздің жауабыңыз</span>
        <span className={styles.answerValue}>
          {selected !== null
            ? `${optionLetter(selected)}. ${q.options[selected] ?? "—"}`
            : "—"}
        </span>
      </div>
      <span
        className={`${styles.verdict} ${isCorrect ? styles.verdictOk : styles.verdictNo}`}
      >
        {isCorrect ? "Дұрыс" : "Қате"}
      </span>
      {!isCorrect && correct !== null ? (
        <span className={styles.correctNote}>
          Дұрыс жауап: {optionLetter(correct)}. {q.options[correct] ?? "—"}
        </span>
      ) : null}
    </div>
  );
}

function OpenResult({ q, a }: { q: Question; a: Answer | undefined }) {
  const graded = a?.graded ?? false;
  return (
    <div className={styles.resultCard}>
      <div className={styles.qHead}>
        <span className={styles.qText}>{q.text}</span>
      </div>
      <div className={styles.answerLine}>
        <span className={styles.answerLabel}>Сіздің жауабыңыз</span>
        <span className={styles.answerValue}>{a?.textAnswer || "—"}</span>
      </div>
      {graded ? (
        <>
          <span className={styles.points}>
            {q.points} ұпайдан {a?.awardedPoints ?? 0}
          </span>
          {a?.teacherComment ? (
            <div className={styles.comment}>{a.teacherComment}</div>
          ) : null}
        </>
      ) : (
        <span className={`${styles.verdict} ${styles.verdictNo}`}>
          Мұғалім тексеруінде
        </span>
      )}
    </div>
  );
}

export default async function PartPage({
  params,
}: {
  params: Promise<{ bookId: string; partId: string }>;
}) {
  const { bookId, partId } = await params;
  const studentId = await getStudentId();
  if (!studentId) return <LoadingScreen />;

  const ladder = await loadLadder(studentId);
  if (!ladder) return <LoadingScreen />;

  const backLink = (
    <Link href={`/app/books/${bookId}`} className={styles.back}>
      <ChevronLeft />
      Кітап бөлімдеріне
    </Link>
  );

  const detail = await partDetail(ladder, bookId, partId);

  // Прямая ссылка не должна обходить лестницу уровней.
  if (detail && !detail.bookUnlocked) {
    return (
      <div className={styles.shell}>
        {backLink}
        <div className={styles.notice}>
          <span className={styles.noticeIcon}>🔒</span>
          Бұл деңгей әзірге жабық
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className={styles.shell}>
        {backLink}
        <div className={styles.notice}>Бөлім табылмады</div>
      </div>
    );
  }

  if (!detail.unlocked) {
    return (
      <div className={styles.shell}>
        {backLink}
        <div className={styles.notice}>
          <span className={styles.noticeIcon}>🔒</span>
          Бұл бөлім әзірге жабық
        </div>
      </div>
    );
  }

  const { part, questions, answers, status } = detail;
  const showTest = status === "not_started" || status === "in_progress";
  const answerByQ = new Map(answers.map((a) => [a.questionId, a]));

  return (
    <div className={styles.shell}>
      {backLink}

      <header className={styles.header}>
        <span className={styles.eyebrow}>{part.order}-бөлім</span>
        <h1 className={styles.title}>{part.title}</h1>
      </header>

      <span className={styles.sectionLabel}>Мәтін</span>
      <div className={styles.reading}>{part.readingText}</div>

      {showTest ? (
        <>
          <span className={styles.sectionLabel}>Сұрақтар</span>
          <PartTest bookId={bookId} partId={partId} questions={questions} />
        </>
      ) : (
        <>
          <span className={styles.sectionLabel}>
            {status === "pending_review" ? "Сіздің жауаптарыңыз · тексеруде" : "Нәтиже"}
          </span>
          <div className={styles.partList}>
            {questions.map((q) =>
              q.type === "choice" ? (
                <ChoiceResult key={q.id} q={q} a={answerByQ.get(q.id)} />
              ) : (
                <OpenResult key={q.id} q={q} a={answerByQ.get(q.id)} />
              ),
            )}
          </div>
        </>
      )}
    </div>
  );
}
