import Script from "next/script";
import TelegramGate from "@/components/student/TelegramGate";
import ThemeBridge from "@/components/student/ThemeBridge";
import styles from "./app.module.css";

export const metadata = {
  title: "Оқу",
  description: "Оқуға арналған оқу қолданбасы",
};

export default function StudentAppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className={styles.app}>
      <Script
        src="https://telegram.org/js/telegram-web-app.js"
        strategy="beforeInteractive"
      />
      <ThemeBridge />
      <TelegramGate>{children}</TelegramGate>
    </div>
  );
}
