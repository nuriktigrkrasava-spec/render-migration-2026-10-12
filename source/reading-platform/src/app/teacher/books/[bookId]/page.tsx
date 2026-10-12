import Link from "next/link";
import { notFound } from "next/navigation";
import { listBooks } from "@/lib/db/books";
import { listParts, countQuestionsByPart } from "@/lib/db/parts";
import {
  createPartAction, createPartsAction, setBookLevelAction,
} from "@/app/teacher/actions";
import PageHeader from "@/components/teacher/PageHeader";
import SubmitButton from "@/components/teacher/SubmitButton";
import PartItem from "@/components/teacher/PartItem";
import { levelLabel } from "@/lib/options";
import styles from "./book.module.css";

export const dynamic = "force-dynamic";

export default async function BookPage({
  params,
}: {
  params: Promise<{ bookId: string }>;
}) {
  const { bookId } = await params;
  const [books, parts] = await Promise.all([listBooks(), listParts(bookId)]);
  const book = books.find((b) => b.id === bookId);
  if (!book) notFound();
  const questionCounts = await countQuestionsByPart(parts.map((p) => p.id));

  return (
    <>
      <PageHeader
        eyebrow={levelLabel(book.level)}
        title={book.title}
        description={book.description || undefined}
        back={
          <Link href="/teacher/books" className="t-btn t-btn-ghost t-btn-sm">
            ← Кітаптар тізіміне
          </Link>
        }
      />

      <div className={styles.layout}>
        <div className={styles.parts}>
          <div className={styles.sectionHead}>
            <span className={styles.sectionTitle}>Кітап бөлімдері</span>
            <span className={styles.count}>{parts.length} бөлім</span>
          </div>

          {parts.length === 0 ? (
            <div className="t-empty">
              Бұл кітапта әзірге бөлім жоқ. Оң жақтан алғашқысын қосыңыз.
            </div>
          ) : (
            parts.map((part, i) => (
              <PartItem
                key={part.id}
                part={part}
                questionCount={questionCounts.get(part.id) ?? 0}
                isFirst={i === 0}
                isLast={i === parts.length - 1}
              />
            ))
          )}
        </div>

        <aside className={styles.aside}>
          <div className={styles.formCard}>
            <h2 className={styles.formTitle}>Кітап деңгейі</h2>
            <p className={styles.formHint}>
              Деңгей — бұл бір кітап. Оқушы алдыңғы деңгейді бітіргенде осы
              деңгей ашылады. Деңгейді таңдаған кітап екеуі орындарын
              ауыстырады.
            </p>
            <form action={setBookLevelAction} className={styles.form}>
              <input type="hidden" name="bookId" value={book.id} />
              <div>
                <label htmlFor="level" className="t-label">
                  Деңгей
                </label>
                <select
                  id="level"
                  name="level"
                  required
                  className="t-select"
                  defaultValue={book.level}
                >
                  {books.map((b) => (
                    <option key={b.id} value={b.level}>
                      {levelLabel(b.level)}
                      {b.id === book.id ? "" : ` · ${b.title}`}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <SubmitButton
                  className="t-btn t-btn-primary t-btn-sm"
                  pendingText="Сақталуда…"
                >
                  Деңгейді сақтау
                </SubmitButton>
              </div>
            </form>
          </div>

          <div className={styles.formCard}>
            <h2 className={styles.formTitle}>Жаңа бөлім</h2>
            <p className={styles.formHint}>
              Бөлім келесі ретпен қосылады. Мәтінді кейінірек өңдеуге болады.
            </p>
            <form action={createPartAction} className={styles.form}>
              <input type="hidden" name="bookId" value={book.id} />
              <div>
                <label htmlFor="title" className="t-label">
                  Бөлім атауы
                </label>
                <input
                  id="title"
                  name="title"
                  required
                  className="t-input"
                  placeholder="1-тарау. Танысу"
                />
              </div>
              <div>
                <label htmlFor="readingText" className="t-label">
                  Оқуға арналған мәтін
                </label>
                <textarea
                  id="readingText"
                  name="readingText"
                  rows={7}
                  className="t-textarea"
                  placeholder="Оқушылар оқитын бөлім мәтінін енгізіңіз"
                />
              </div>
              <div>
                <SubmitButton
                  className="t-btn t-btn-primary"
                  pendingText="Қосылуда…"
                >
                  Бөлім қосу
                </SubmitButton>
              </div>
            </form>
          </div>

          <div className={styles.formCard}>
            <h2 className={styles.formTitle}>Бірден бірнеше бөлім</h2>
            <p className={styles.formHint}>
              Бос бөлімдер «12-бөлім» деген атпен қосылады — мәтін мен
              сұрақтарды кейін жазасыз.
            </p>
            <form action={createPartsAction} className={styles.form}>
              <input type="hidden" name="bookId" value={book.id} />
              <div>
                <label htmlFor="count" className="t-label">
                  Бөлім саны
                </label>
                <input
                  id="count"
                  name="count"
                  type="number"
                  min={1}
                  max={50}
                  defaultValue={10}
                  required
                  className="t-input"
                />
              </div>
              <div>
                <SubmitButton
                  className="t-btn t-btn-ghost"
                  pendingText="Құрылуда…"
                >
                  Бос бөлімдерді қосу
                </SubmitButton>
              </div>
            </form>
          </div>
        </aside>
      </div>
    </>
  );
}
