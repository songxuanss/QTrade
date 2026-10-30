import { NextRequest, NextResponse } from "next/server";
import { authConfig } from "../../../features/auth/server";

export async function POST(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" };
  try {
    const config = authConfig();
    if (!config)
      return NextResponse.json(
        { error: "identity_unavailable" },
        { status: 503, headers },
      );
    if (request.headers.get("Origin") !== config.publicOrigin)
      return NextResponse.json(
        { error: "logout_denied" },
        { status: 403, headers },
      );
    const name = config.secureCookies
      ? "__Host-qtrade-session"
      : "qtrade-session";
    const cookie = request.cookies.get(name)?.value;
    const csrf = request.headers.get("X-CSRF-Token");
    if (
      !cookie ||
      !/^[A-Za-z0-9_-]{43}$/.test(cookie) ||
      !csrf ||
      !/^[A-Za-z0-9_-]{43}$/.test(csrf)
    )
      return NextResponse.json(
        { error: "logout_denied" },
        { status: 403, headers },
      );
    const result = await fetch(config.apiOrigin + "/auth/logout", {
      method: "POST",
      cache: "no-store",
      redirect: "error",
      headers: { Cookie: `${name}=${cookie}`, "X-CSRF-Token": csrf },
      signal: AbortSignal.timeout(10_000),
    });
    if (result.status !== 204)
      return NextResponse.json(
        { error: "logout_denied" },
        { status: result.status === 403 ? 403 : 503, headers },
      );
    const response = new NextResponse(null, { status: 204, headers });
    response.cookies.set(name, "", {
      httpOnly: true,
      secure: config.secureCookies,
      sameSite: "lax",
      path: "/",
      maxAge: 0,
    });
    return response;
  } catch {
    return NextResponse.json(
      { error: "identity_unavailable" },
      { status: 503, headers },
    );
  }
}
