import { NextRequest, NextResponse } from "next/server";
import {
  authConfig,
  completeLogin,
  openTransaction,
  transactionCookieName,
} from "../../../features/auth/server";
import { safeReturnTo } from "../../../features/auth/config";

export async function GET(request: NextRequest) {
  let config;
  try {
    config = authConfig();
  } catch {
    return NextResponse.json(
      { error: "identity_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!config)
    return NextResponse.json(
      { error: "identity_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  let response: NextResponse | undefined;
  let sessionCookie: string | undefined;
  let stage = "transaction";
  try {
    const transaction = openTransaction(
      config,
      request.cookies.get(transactionCookieName(config))?.value,
    );
    stage = "identity_exchange";
    const payload = await completeLogin(config, request.nextUrl, transaction);
    stage = "session_initialization";
    const created = await fetch(config.apiOrigin + "/internal/auth/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.bridgeSecret}`,
      },
      body: JSON.stringify(payload),
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    if (created.status !== 204)
      throw new Error("Session initialization failed");
    stage = "session_cookie_validation";
    const cookies = created.headers.getSetCookie();
    const name = config.secureCookies
      ? "__Host-qtrade-session"
      : "qtrade-session";
    if (
      cookies.length !== 1 ||
      !cookies[0].startsWith(name + "=") ||
      !cookies[0].includes("HttpOnly") ||
      (config.secureCookies && !cookies[0].includes("Secure"))
    )
      throw new Error("Invalid session response");
    response = NextResponse.redirect(
      new URL(safeReturnTo(transaction.returnTo), config.publicOrigin),
      303,
    );
    sessionCookie = cookies[0];
  } catch {
    // Fixed stage names only: callback URLs, tokens and exception text are private.
    console.error(JSON.stringify({ event: "auth.callback_failed", stage }));
    try {
      const recorded = await fetch(
        config.apiOrigin + "/internal/auth/login-failed",
        {
          method: "POST",
          headers: { Authorization: `Bearer ${config.bridgeSecret}` },
          cache: "no-store",
          redirect: "error",
          signal: AbortSignal.timeout(10_000),
        },
      );
      if (recorded.status !== 204) throw new Error("Audit unavailable");
    } catch {
      response = NextResponse.json(
        { error: "identity_unavailable" },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
    response ??= NextResponse.redirect(
      new URL("/auth/error", config.publicOrigin),
      303,
    );
  }
  response.cookies.set(transactionCookieName(config), "", {
    httpOnly: true,
    secure: config.secureCookies,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  if (sessionCookie) response.headers.append("Set-Cookie", sessionCookie);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
