import { NextResponse, type NextRequest } from "next/server";
import { verifySessionEdge } from "@/lib/auth/edgeSession";

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname.startsWith("/teacher/login")) return NextResponse.next();

  const token = req.cookies.get("teacher_session")?.value;
  const secret = process.env.TEACHER_PASSWORD ?? "";
  if (!token || (await verifySessionEdge(token, secret)) !== "teacher") {
    const url = req.nextUrl.clone();
    url.pathname = "/teacher/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/teacher/:path*"] };
