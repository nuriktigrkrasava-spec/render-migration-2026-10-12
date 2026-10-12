import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import WS from "ws";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

// supabase-js при создании клиента сразу инициализирует Realtime-клиент,
// которому нужен глобальный WebSocket. На Node < 22 его нет, и без этого
// падал бы каждый серверный запрос к базе. Realtime мы не используем (канал
// не подписываем → сокет не открывается), но обязаны дать конструктору
// совместимый транспорт.
//
// Клиент кешируем в синглтон: конструкция клиента не бесплатна, а раньше он
// создавался заново на КАЖДЫЙ запрос (десятки раз за один рендер страницы).
// Сервисный ключ + persistSession:false → состояния на пользователя нет,
// поэтому общий экземпляр безопасен.
let cached: SupabaseClient | null = null;

export function createServerSupabase(): SupabaseClient {
  if (cached) return cached;
  if (!url || !serviceKey) {
    throw new Error("Supabase env vars are not set");
  }
  cached = createClient(url, serviceKey, {
    auth: { persistSession: false },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    realtime: { transport: WS as any },
  });
  return cached;
}
