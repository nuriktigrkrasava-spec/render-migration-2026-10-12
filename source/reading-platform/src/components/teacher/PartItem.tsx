"use client";

import { useState } from "react";
import Link from "next/link";
import type { Part } from "@/lib/types";
import {
  updatePartAction, deletePartAction, movePartAction,
} from "@/app/teacher/actions";
import SubmitButton from "@/components/teacher/SubmitButton";
import styles from "@/app/teacher/books/[bookId]/book.module.css";

export default function PartItem({
  part, questionCount, isFirst, isLast,
}: {
  part: Part; questionCount: number; isFirst: boolean; isLast: boolean;
}) {
  // Книга может быть на 30+ частей, поэтому строка свёрнута по умолчанию:
  // видно номер, название и число вопросов, остальное — по клику.
  const [open, setOpen] = useState(false);

  return (
    <article className={styles.part}>
      <div className={styles.row}>
        <span className={styles.order}>{part.order}</span>

        <button
          type="button"
          className={styles.rowMain}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className={styles.partTitle}>{part.title}</span>
          <span className={styles.qCount}>{questionCount} сұрақ</span>
        </button>

        <div className={styles.rowTools}>
          <form action={movePartAction} className={styles.moveForm}>
            <input type="hidden" name="bookId" value={part.bookId} />
            <input type="hidden" name="partId" value={part.id} />
            <input type="hidden" name="direction" value="up" />
            <SubmitButton
              className="t-btn t-btn-ghost t-btn-sm"
              pendingText="…"
              disabled={isFirst}
            >
              ↑
            </SubmitButton>
          </form>
          <form action={movePartAction} className={styles.moveForm}>
            <input type="hidden" name="bookId" value={part.bookId} />
            <input type="hidden" name="partId" value={part.id} />
            <input type="hidden" name="direction" value="down" />
            <SubmitButton
              className="t-btn t-btn-ghost t-btn-sm"
              pendingText="…"
              disabled={isLast}
            >
              ↓
            </SubmitButton>
          </form>
          <Link
            href={`/teacher/books/${part.bookId}/parts/${part.id}`}
            className="t-btn t-btn-ghost t-btn-sm"
          >
            Тест
          </Link>
        </div>
      </div>

      {open && (
        <div className={styles.details}>
          <form action={updatePartAction} className={styles.editForm}>
            <input type="hidden" name="bookId" value={part.bookId} />
            <input type="hidden" name="partId" value={part.id} />
            <div>
              <label className="t-label" htmlFor={`title-${part.id}`}>
                Бөлім атауы
              </label>
              <input
                id={`title-${part.id}`}
                name="title"
                defaultValue={part.title}
                required
                className="t-input"
              />
            </div>
            <div>
              <label className="t-label" htmlFor={`text-${part.id}`}>
                Оқуға арналған мәтін
              </label>
              <textarea
                id={`text-${part.id}`}
                name="readingText"
                defaultValue={part.readingText}
                rows={8}
                className="t-textarea"
              />
            </div>
            <div className={styles.editActions}>
              <SubmitButton
                className="t-btn t-btn-primary t-btn-sm"
                pendingText="Сақталуда…"
              >
                Сақтау
              </SubmitButton>
            </div>
          </form>

          <form action={deletePartAction} className={styles.deleteForm}>
            <input type="hidden" name="bookId" value={part.bookId} />
            <input type="hidden" name="partId" value={part.id} />
            <SubmitButton
              className="t-btn t-btn-danger t-btn-sm"
              pendingText="Жойылуда…"
            >
              Бөлімді жою
            </SubmitButton>
          </form>
        </div>
      )}
    </article>
  );
}
