import { createHmac, timingSafeEqual } from "node:crypto";

export interface TelegramUser {
  id: string;
  firstName: string;
  lastName: string;
  username: string;
}

export function validateInitData(initData: string, botToken: string): TelegramUser | null {
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;

  const pairs: string[] = [];
  for (const [key, value] of params.entries()) {
    if (key === "hash") continue;
    pairs.push(`${key}=${value}`);
  }
  pairs.sort();
  const dataCheckString = pairs.join("\n");

  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const expected = createHmac("sha256", secret).update(dataCheckString).digest("hex");

  const a = Buffer.from(hash, "hex");
  const b = Buffer.from(expected, "hex");
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;

  const userRaw = params.get("user");
  if (!userRaw) return null;
  try {
    const u = JSON.parse(userRaw);
    return {
      id: String(u.id),
      firstName: u.first_name ?? "",
      lastName: u.last_name ?? "",
      username: u.username ?? "",
    };
  } catch {
    return null;
  }
}
