import styles from "@/app/app/app.module.css";

/** Shown by server pages when the session cookie isn't readable yet
 *  (TelegramGate will router.refresh() once auth completes). */
export default function LoadingScreen() {
  return (
    <div className={styles.center}>
      <div className={styles.centerInner}>
        <div className={styles.spinner} />
        <p className={styles.centerText}>Жүктелуде…</p>
      </div>
    </div>
  );
}
