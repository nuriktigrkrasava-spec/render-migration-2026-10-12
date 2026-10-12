"use client";

import { useState, useTransition } from "react";
import styles from "@/app/app/app.module.css";

type Props = {
  initData: string;
  onAuthed: () => void;
};

export default function StudentLogin({ initData, onAuthed }: Props) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setError(null);

    startTransition(async () => {
      try {
        const res = await fetch("/api/student/auth", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initData, password, name }),
        });
        const data = (await res.json().catch(() => null)) as
          | { ok?: boolean; error?: string }
          | null;

        if (res.ok && data?.ok) {
          onAuthed();
          return;
        }

        switch (data?.error) {
          case "password":
            setError("Құпиясөз қате");
            break;
          case "name":
            setError("Атыңызды жазыңыз");
            break;
          default:
            setError("Қате кетті. Қайталап көріңіз");
        }
      } catch {
        setError("Қате кетті. Қайталап көріңіз");
      }
    });
  }

  return (
    <div className={styles.login}>
      <form className={styles.loginCard} onSubmit={submit}>
        <div className={styles.header}>
          <span className={styles.eyebrow}>Қош келдіңіз</span>
          <h1 className={styles.title}>Оқу сауаттылығы</h1>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="login-name">
            Атың
          </label>
          <input
            id="login-name"
            className={styles.input}
            type="text"
            autoComplete="name"
            placeholder="Аты-жөніңіз"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={pending}
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="login-password">
            Құпиясөз
          </label>
          <input
            id="login-password"
            className={styles.input}
            type="password"
            autoComplete="current-password"
            placeholder="Құпиясөзді енгізіңіз"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={pending}
          />
        </div>

        {error && <p className={styles.formError}>{error}</p>}

        <button className={styles.submit} type="submit" disabled={pending}>
          {pending ? "Кіру…" : "Кіру"}
        </button>
      </form>
    </div>
  );
}
