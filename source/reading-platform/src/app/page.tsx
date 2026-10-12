import Link from "next/link";
import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.screen}>
      <section className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.mark}>О</span>
          <span className={styles.brandName}>Оқу сауаттылығы</span>
        </div>

        <span className={styles.eyebrow}>Оқу сауаттылығы</span>
        <h1 className={styles.title}>Оқу сауаттылығы</h1>
        <p className={styles.lead}>
          Мағыналы оқуға арналған оқу ортасы: мұғалім сабақ жүргізіп,
          жұмыстарды тексереді, оқушылар оқып, тапсырмаларға жауап береді.
        </p>

        <Link href="/teacher" className={`t-btn t-btn-primary ${styles.cta}`}>
          Мұғалім кірісі
        </Link>

        <p className={styles.note}>
          Оқушылар платформаны Telegram-бот арқылы ашады.
        </p>
      </section>
    </main>
  );
}
