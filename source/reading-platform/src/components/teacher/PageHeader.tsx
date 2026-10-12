import styles from "./PageHeader.module.css";

export default function PageHeader({
  eyebrow,
  title,
  description,
  action,
  back,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
  back?: React.ReactNode;
}) {
  return (
    <header className={styles.header}>
      {back && <div className={styles.back}>{back}</div>}
      <div className={styles.row}>
        <div className={styles.titleBlock}>
          {eyebrow && <span className="t-eyebrow">{eyebrow}</span>}
          <h1 className={styles.title}>{title}</h1>
          {description && <p className={styles.description}>{description}</p>}
        </div>
        {action && <div className={styles.action}>{action}</div>}
      </div>
    </header>
  );
}
