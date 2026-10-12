import { cookies } from "next/headers";
import { signSession, verifySession } from "@/lib/auth/session";

const COOKIE = "teacher_session";

function secret(): string {
  const s = process.env.TEACHER_PASSWORD;
  if (!s) throw new Error("TEACHER_PASSWORD is not set");
  return s;
}

export function checkPassword(input: string): boolean {
  const expected = process.env.TEACHER_PASSWORD;
  return !!expected && input === expected;
}

export async function startSession(): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, signSession("teacher", secret()), {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
}

export async function endSession(): Promise<void> {
  const store = await cookies();
  store.delete(COOKIE);
}

export async function isAuthenticated(): Promise<boolean> {
  const store = await cookies();
  const token = store.get(COOKIE)?.value;
  if (!token) return false;
  return verifySession(token, secret()) === "teacher";
}
