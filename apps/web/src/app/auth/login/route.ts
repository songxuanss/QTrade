import { NextRequest, NextResponse } from "next/server";
import {
  authConfig,
  beginLogin,
  sealTransaction,
  transactionCookieName,
} from "../../../features/auth/server";

export async function GET(request: NextRequest) {
  try {
    const config = authConfig();
    if (!config)
      return NextResponse.json(
        { error: "identity_unavailable" },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    const { url, transaction } = await beginLogin(
      config,
      request.nextUrl.searchParams.get("returnTo"),
    );
    const response = NextResponse.redirect(url, 303);
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    response.cookies.set(
      transactionCookieName(config),
      sealTransaction(config, transaction),
      {
        httpOnly: true,
        secure: config.secureCookies,
        sameSite: "lax",
        path: "/",
        maxAge: 300,
      },
    );
    return response;
  } catch {
    return NextResponse.json(
      { error: "identity_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
