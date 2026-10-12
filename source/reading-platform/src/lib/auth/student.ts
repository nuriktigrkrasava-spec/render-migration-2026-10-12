import { cookies } from "next/headers";
import { signSession, verifySession } from "@/lib/auth/session";

const COOKIE = "student_session";

function secret(): string {
  const s = process.env.TELEGRAM_BOT_TOKEN;
  if (!s) throw new Error("TELEGRAM_BOT_TOKEN is not set");
  return s;
}

export async function startStudentSession(studentId: string): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, signSession(studentId, secret()), {
    httpOnly: true, sameSite: "lax", secure: true, path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
}

export async function getStudentId(): Promise<string | null> {
  const store = await cookies();
  const token = store.get(COOKIE)?.value;
  if (!token) return null;
  return verifySession(token, secret());
}
