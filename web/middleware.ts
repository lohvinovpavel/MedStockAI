import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const COOKIE_NAME = "medstock_token";

// Browser sends httpOnly medstock_token; analogue/auth verify Bearer or
// cookie. Copying onto Authorization means a Next rewrite cannot drop the
// session on the way to 127.0.0.1:8002 (curl Bearer already works).
export function middleware(request: NextRequest) {
  const token = request.cookies.get(COOKIE_NAME)?.value;
  if (!token || request.headers.get("authorization")) {
    return NextResponse.next();
  }
  const headers = new Headers(request.headers);
  headers.set("authorization", `Bearer ${token}`);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: "/api/:path*",
};
