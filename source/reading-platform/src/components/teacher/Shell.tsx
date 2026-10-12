"use client";

import { usePathname } from "next/navigation";
import Sidebar from "@/components/teacher/Sidebar";
import styles from "@/app/teacher/layout.module.css";

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const standalone = pathname === "/teacher/login";

  if (standalone) {
    return <>{children}</>;
  }

  return (
    <div className={styles.shell}>
      <Sidebar />
      <div className={styles.main}>
        <div className={styles.content}>{children}</div>
      </div>
    </div>
  );
}
