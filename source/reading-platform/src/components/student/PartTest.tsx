"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { submitPartAction } from "@/app/app/actions";
import { optionLetter } from "@/lib/options";
import type { Question } from "@/lib/types";
import styles from "@/app/app/app.module.css";

interface Draft {
  selectedOptionIndex: number | null;
  textAnswer: string;
}

export default function PartTest({
  bookId,
  partId,
  questions,
}: {
  bookId: string;
  partId: string;
  questions: Question[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() =>
    Object.fromEntries(
      questions.map((q) => [q.id, { selectedOptionIndex: null, textAnswer: "" }]),
    ),
  );

  function setChoice(qid: string, index: number) {
    setError(null);
    setDrafts((d) => ({ ...d, [qid]: { selectedOptionIndex: index, textAnswer: "" } }));
  }

  function setText(qid: string, value: string) {
    setError(null);
    setDrafts((d) => ({ ...d, [qid]: { selectedOptionIndex: null, textAnswer: value } }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    for (const q of questions) {
      const draft = drafts[q.id];
      if (q.type === "choice" && draft.selectedOptionIndex === null) {
        setError("Жіберу үшін барлық сұраққа жауап беріңіз");
        return;
      }
      if (q.type === "open" && draft.textAnswer.trim() === "") {
        setError("Жіберу үшін барлық сұраққа жауап беріңіз");
        return;
      }
    }

    const answers = questions.map((q) => {
      const draft = drafts[q.id];
      return {
        questionId: q.id,
        selectedOptionIndex: q.type === "choice" ? draft.selectedOptionIndex : null,
        textAnswer: q.type === "open" ? draft.textAnswer.trim() : null,
      };
    });

    startTransition(async () => {
      const res = await submitPartAction({ bookId, partId, answers });
      if (res.ok) {
        router.refresh();
      } else {
        setError("Жіберу мүмкін болмады. Қайта көріңіз");
      }
    });
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      {questions.map((q, i) => {
        const draft = drafts[q.id];
        return (
          <div key={q.id} className={styles.qCard}>
            <div className={styles.qHead}>
              <span className={styles.qNum}>{i + 1}.</span>
              <span className={styles.qText}>
                {q.text}
                <span className={styles.qPoints}> · {q.points} ұпай</span>
              </span>
            </div>

            {q.type === "choice" ? (
              <div className={styles.options}>
                {q.options.map((opt, idx) => {
                  const checked = draft.selectedOptionIndex === idx;
                  return (
                    <label
                      key={idx}
                      className={`${styles.option} ${checked ? styles.optionChecked : ""}`}
                    >
                      <input
                        type="radio"
                        name={q.id}
                        checked={checked}
                        onChange={() => setChoice(q.id, idx)}
                      />
                      <span className={styles.radio}>
                        <span className={styles.radioDot} />
                      </span>
                      <span className={styles.optionLetter}>{optionLetter(idx)}</span>
                      <span className={styles.optionText}>{opt}</span>
                    </label>
                  );
                })}
              </div>
            ) : (
              <textarea
                className={styles.textarea}
                placeholder="Сіздің жауабыңыз…"
                value={draft.textAnswer}
                onChange={(e) => setText(q.id, e.target.value)}
              />
            )}
          </div>
        );
      })}

      {error ? <div className={styles.formError}>{error}</div> : null}

      <div className={styles.submitBar}>
        <button type="submit" className={styles.submit} disabled={pending}>
          {pending ? "Жіберілуде…" : "Жіберу"}
        </button>
      </div>
    </form>
  );
}
