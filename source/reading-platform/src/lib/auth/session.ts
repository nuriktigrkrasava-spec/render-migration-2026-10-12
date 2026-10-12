import { createHmac, timingSafeEqual } from "node:crypto";

function sign(value: string, secret: string): string {
  return createHmac("sha256", secret).update(value).digest("hex");
}

export function signSession(value: string, secret: string): string {
  return `${value}.${sign(value, secret)}`;
}

export function verifySession(token: string, secret: string): string | null {
  const idx = token.lastIndexOf(".");
  if (idx <= 0) return null;
  const value = token.slice(0, idx);
  const sig = token.slice(idx + 1);
  const expected = sign(value, secret);
  const a = Buffer.from(sig, "hex");
  const b = Buffer.from(expected, "hex");
  if (a.length !== b.length) return null;
  return timingSafeEqual(a, b) ? value : null;
}
