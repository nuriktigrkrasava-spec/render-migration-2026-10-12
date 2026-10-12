import { NextResponse, type NextRequest } from "next/server";
import { validateInitData } from "@/lib/telegram/initData";
import { upsertStudentByTelegram } from "@/lib/db/studentRepo";
import { startStudentSession, getStudentId } from "@/lib/auth/student";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const { initData, password, name } = await req.json();

  // (1) Telegram identity — unique key.
  const token = process.env.TELEGRAM_BOT_TOKEN ?? "";
  const user = typeof initData === "string" ? validateInitData(initData, token) : null;
  if (!user) return NextResponse.json({ ok: false, error: "telegram" }, { status: 401 });

  // (2) Shared password.
  const shared = process.env.STUDENT_PASSWORD;
  if (!shared || password !== shared) {
    return NextResponse.json({ ok: false, error: "password" }, { status: 401 });
  }

  // (3) Typed name (1..80 chars).
  const trimmedName = typeof name === "string" ? name.trim() : "";
  if (trimmedName.length < 1 || trimmedName.length > 80) {
    return NextResponse.json({ ok: false, error: "name" }, { status: 400 });
  }

  // (4) Upsert by Telegram id, using the typed name, and open a session.
  const student = await upsertStudentByTelegram({ telegramId: user.id, name: trimmedName });
  await startStudentSession(student.id);
  return NextResponse.json({ ok: true });
}

export async function GET() {
  return NextResponse.json({ authenticated: (await getStudentId()) !== null });
}
