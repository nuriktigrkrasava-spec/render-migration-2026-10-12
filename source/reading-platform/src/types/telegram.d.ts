export {};

declare global {
  interface TelegramWebApp {
    ready(): void;
    initData: string;
    colorScheme: "light" | "dark";
    themeParams: Record<string, string>;
    expand?(): void;
  }

  interface Window {
    Telegram?: {
      WebApp: TelegramWebApp;
    };
  }
}
