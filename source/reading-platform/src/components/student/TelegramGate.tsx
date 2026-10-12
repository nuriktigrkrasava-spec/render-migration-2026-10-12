"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "@/app/app/app.module.css";
import StudentLogin from "./StudentLogin";

type GateState = "checking" | "needs-login" | "no-telegram" | "ready";

export default function TelegramGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<GateState>("checking");
  const [initData, setInitData] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    const wa = window.Telegram?.WebApp;
    wa?.ready();
    const data = wa?.initData ?? "";

    (async () => {
      try {
        const res = await fetch("/api/student/auth", { method: "GET" });
        const json = (await res.json().catch(() => null)) as
          | { authenticated?: boolean }
          | null;
        if (cancelled) return;

        setInitData(data);
        if (json?.authenticated) {
          setState("ready");
        } else if (data) {
          setState("needs-login");
        } else {
          setState("no-telegram");
        }
      } catch {
        if (cancelled) return;
        setInitData(data);
        setState(data ? "needs-login" : "no-telegram");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "checking") {
    return (
      <div className={styles.center}>
        <div className={styles.centerInner}>
          <div className={styles.spinner} />
          <p className={styles.centerText}>Жүктелуде…</p>
        </div>
      </div>
    );
  }

  if (state === "no-telegram") {
    return (
      <div className={styles.center}>
        <div className={styles.centerInner}>
          <div className={styles.centerTitle}>Қолданбаны Telegram арқылы ашыңыз</div>
          <p className={styles.centerText}>
            Жалғастыру үшін қолданбаны телефондағы Telegram боты арқылы ашыңыз.
          </p>
        </div>
      </div>
    );
  }

  if (state === "needs-login") {
    return (
      <StudentLogin
        initData={initData}
        onAuthed={() => {
          setState("ready");
          router.refresh();
        }}
      />
    );
  }

  return <>{children}</>;
}
