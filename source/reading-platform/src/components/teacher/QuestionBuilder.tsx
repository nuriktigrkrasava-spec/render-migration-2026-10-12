"use client";

import { useState } from "react";
import { createQuestionAction } from "@/app/teacher/actions";
import SubmitButton from "@/components/teacher/SubmitButton";
import { optionLetter } from "@/lib/options";
import styles from "./QuestionBuilder.module.css";

const MAX_OPTIONS = 8;
const MIN_OPTIONS = 2;

export default function QuestionBuilder({
  bookId,
  partId,
}: {
  bookId: string;
  partId: string;
}) {
  const [type, setType] = useState<"choice" | "open">("choice");
  const [options, setOptions] = useState<string[]>(["", ""]);
  const [correctIndex, setCorrectIndex] = useState(0);

  function setOption(i: number, value: string) {
    setOptions((opts) => opts.map((o, idx) => (idx === i ? value : o)));
  }

  function addOption() {
    setOptions((opts) => (opts.length >= MAX_OPTIONS ? opts : [...opts, ""]));
  }

  function removeOption(i: number) {
    setOptions((opts) => {
      if (opts.length <= MIN_OPTIONS) return opts;
      return opts.filter((_, idx) => idx !== i);
    });
    setCorrectIndex((c) => {
      if (i === c) return 0; // удалили правильный — сбрасываем на первый
      if (i < c) return c - 1; // сдвиг индексов выше удалённого
      return c;
    });
  }

  return (
    <div className={styles.card}>
      <h2 className={styles.title}>Жаңа сұрақ</h2>
      <p className={styles.hint}>
        Сұрақ түрін таңдап, өрістерді толтырыңыз. Сұрақ осы бөлімнің тестіне
        қосылады.
      </p>

      <form action={createQuestionAction} className={styles.form}>
        <input type="hidden" name="bookId" value={bookId} />
        <input type="hidden" name="partId" value={partId} />

        <div>
          <span className="t-label">Сұрақ түрі</span>
          <div className={styles.typeToggle}>
            <label
              className={`${styles.typeOption} ${
                type === "choice" ? styles.typeOptionActive : ""
              }`}
            >
              <input
                type="radio"
                name="type"
                value="choice"
                checked={type === "choice"}
                onChange={() => setType("choice")}
              />
              <span className={styles.typeName}>Нұсқалармен</span>
              <span className={styles.typeDesc}>Бір дұрыс жауап</span>
            </label>
            <label
              className={`${styles.typeOption} ${
                type === "open" ? styles.typeOptionActive : ""
              }`}
            >
              <input
                type="radio"
                name="type"
                value="open"
                checked={type === "open"}
                onChange={() => setType("open")}
              />
              <span className={styles.typeName}>Ашық</span>
              <span className={styles.typeDesc}>Жауапты мұғалім тексереді</span>
            </label>
          </div>
        </div>

        <div>
          <label htmlFor="qtext" className="t-label">
            Сұрақ мәтіні
          </label>
          <textarea
            id="qtext"
            name="text"
            required
            rows={3}
            className="t-textarea"
            placeholder="Сұрақты тұжырымдаңыз"
          />
        </div>

        {type === "choice" && (
          <div className={styles.optionsBlock}>
            <span className="t-label">Жауап нұсқалары</span>
            <p className={styles.optionsHint}>
              Нұсқаларды жазып, дұрысының «дұрыс» дөңгелегін басыңыз.
            </p>

            <div className={styles.optionList}>
              {options.map((opt, i) => {
                const isCorrect = correctIndex === i;
                return (
                  <div
                    key={i}
                    className={`${styles.optionRow} ${
                      isCorrect ? styles.optionRowCorrect : ""
                    }`}
                  >
                    <span className={styles.optionBadge}>{optionLetter(i)}</span>
                    <input
                      type="text"
                      name="option"
                      required
                      className={styles.optionInput}
                      placeholder={`${optionLetter(i)} нұсқасы`}
                      value={opt}
                      onChange={(e) => setOption(i, e.target.value)}
                    />
                    <label
                      className={`${styles.correctToggle} ${
                        isCorrect ? styles.correctToggleOn : ""
                      }`}
                      title="Дұрыс деп белгілеу"
                    >
                      <input
                        type="radio"
                        name="correctOptionIndex"
                        value={i}
                        checked={isCorrect}
                        onChange={() => setCorrectIndex(i)}
                      />
                      <span className={styles.correctDot} />
                      дұрыс
                    </label>
                    {options.length > MIN_OPTIONS && (
                      <button
                        type="button"
                        className={styles.removeOption}
                        onClick={() => removeOption(i)}
                        aria-label="Нұсқаны жою"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            {options.length < MAX_OPTIONS && (
              <button
                type="button"
                className={styles.addOption}
                onClick={addOption}
              >
                + Нұсқа қосу
              </button>
            )}
          </div>
        )}

        <div>
          <label htmlFor="points" className="t-label">
            {type === "open" ? "Ең жоғары ұпай" : "Сұрақ ұпайы"}
          </label>
          <input
            key={type}
            id="points"
            name="points"
            type="number"
            min={1}
            max={type === "open" ? 5 : undefined}
            defaultValue={type === "open" ? 5 : 1}
            required
            className="t-input"
          />
          {type === "open" && (
            <p className={styles.hint}>
              Тексергенде жауапты 0–5 ұпай аралығында бағалайсыз.
            </p>
          )}
        </div>

        <div>
          <SubmitButton className="t-btn t-btn-primary" pendingText="Қосылуда…">
            Сұрақ қосу
          </SubmitButton>
        </div>
      </form>
    </div>
  );
}
