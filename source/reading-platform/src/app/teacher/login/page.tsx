"use client";

import { useActionState } from "react";
import { loginAction } from "@/app/teacher/actions";
import SubmitButton from "@/components/teacher/SubmitButton";
import styles from "./login.module.css";

type State = { error: string } | null;

async function action(_prev: State, formData: FormData): Promise<State> {
  const result = await loginAction(formData);
  return result ?? null;
}

export default function LoginPage() {
  const [state, formAction] = useActionState<State, FormData>(action, null);

  return (
    <div className={styles.screen}>
      <section className={styles.aside}>
        <div className={styles.asideTop}>
          <span className={styles.mark}>О</span>
          <span className={styles.asideBrand}>Оқу сауаттылығы</span>
        </div>
        <div className={styles.asideBody}>
          <p className={styles.asideQuote}>
            Әрбір оқылған бет — оқушының өзіне жасаған қадамы.
          </p>
        </div>
        <p className={styles.asideMeta}>Мұғалім кабинеті</p>
      </section>

      <section className={styles.panel}>
        <div className={styles.card}>
          <span className={styles.eyebrow}>Кабинетке кіру</span>
          <h1 className={styles.title}>Қайта оралуыңызбен</h1>
          <p className={styles.lead}>
            Платформамен жұмысты жалғастыру үшін мұғалім құпиясөзін енгізіңіз.
          </p>

          <form action={formAction} className={styles.form}>
            <div className={styles.field}>
              <label htmlFor="password" className="t-label">
                Құпиясөз
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                autoFocus
                required
                className="t-input"
                placeholder="••••••••"
              />
            </div>

            {state?.error && (
              <p className={styles.error} role="alert">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                {state.error}
              </p>
            )}

            <SubmitButton
              className={`t-btn t-btn-primary ${styles.submit}`}
              pendingText="Тексерілуде…"
            >
              Кіру
            </SubmitButton>
          </form>
        </div>
      </section>
    </div>
  );
}
