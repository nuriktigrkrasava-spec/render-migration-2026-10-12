"use client";

import { useEffect } from "react";

/**
 * Reads Telegram WebApp colorScheme / themeParams and maps them onto CSS
 * custom properties scoped to the student app root. Falls back to a warm
 * "light" reading palette so the app still looks intentional outside Telegram.
 */
export default function ThemeBridge() {
  useEffect(() => {
    const root = document.documentElement;

    function apply() {
      const wa = window.Telegram?.WebApp;
      const tp = wa?.themeParams ?? {};
      const scheme = wa?.colorScheme;

      root.dataset.tgScheme = scheme ?? "light";

      // Map Telegram theme params → student app variables when present.
      const map: Record<string, string> = {
        "--tg-bg": tp.bg_color ?? "",
        "--tg-secondary-bg": tp.secondary_bg_color ?? "",
        "--tg-text": tp.text_color ?? "",
        "--tg-hint": tp.hint_color ?? "",
        "--tg-link": tp.link_color ?? "",
        "--tg-button": tp.button_color ?? "",
        "--tg-button-text": tp.button_text_color ?? "",
      };
      for (const [k, v] of Object.entries(map)) {
        if (v) root.style.setProperty(k, v);
        else root.style.removeProperty(k);
      }
    }

    apply();
    window.Telegram?.WebApp?.expand?.();

    // Telegram fires themeChanged on the WebApp object; re-read defensively.
    const id = window.setInterval(apply, 1000);
    return () => window.clearInterval(id);
  }, []);

  return null;
}
