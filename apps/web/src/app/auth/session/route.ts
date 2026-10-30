import { NextRequest, NextResponse } from "next/server";
import { authConfig } from "../../../features/auth/server";

export async function GET(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" };
  try {
    const config = authConfig();
    if (!config)
      return NextResponse.json(
        { error: "identity_unavailable" },
        { status: 503, headers },
      );
    const name = config.secureCookies
      ? "__Host-qtrade-session"
      : "qtrade-session";
    const cookie = request.cookies.get(name)?.value;
    if (!cookie || !/^[A-Za-z0-9_-]{43}$/.test(cookie))
      return NextResponse.json(
        { error: "authentication_required" },
        { status: 401, headers },
      );
    const response = await fetch(config.apiOrigin + "/auth/session", {
      cache: "no-store",
      redirect: "error",
      headers: { Cookie: `${name}=${cookie}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok)
      return NextResponse.json(
        { error: "authentication_required" },
        { status: response.status === 401 ? 401 : 503, headers },
      );
    const body = await response.json();
    if (
      body.authenticated !== true ||
      typeof body.user_id !== "string" ||
      typeof body.workspace_id !== "string" ||
      typeof body.expires_at !== "string" ||
      typeof body.csrf_token !== "string" ||
      !/^[A-Za-z0-9_-]{43}$/.test(body.csrf_token)
    )
      throw Error("Invalid session response");
    return NextResponse.json(
      {
        authenticated: true,
        user_id: body.user_id,
        workspace_id: body.workspace_id,
        expires_at: body.expires_at,
        csrf_token: body.csrf_token,
      },
      { headers },
    );
  } catch {
    return NextResponse.json(
      { error: "identity_unavailable" },
      { status: 503, headers },
    );
  }
}
