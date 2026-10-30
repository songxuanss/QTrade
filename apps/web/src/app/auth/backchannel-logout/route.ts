import { NextRequest, NextResponse } from "next/server";
import { authConfig } from "../../../features/auth/server";
import { verifyLogout } from "../../../features/auth/backchannel";

export async function POST(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" };
  try {
    const config = authConfig();
    if (!config)
      return NextResponse.json(
        { error: "identity_unavailable" },
        { status: 503, headers },
      );
    const body = await request.text();
    if (body.length > 70_000) throw Error("Invalid logout request");
    const type = request.headers.get("Content-Type")?.split(";")[0].trim();
    let token: unknown;
    if (type === "application/json") {
      const payload = JSON.parse(body);
      if (!payload || Object.keys(payload).length !== 1)
        throw Error("Invalid logout request");
      token = payload.logout_token;
    } else if (type === "application/x-www-form-urlencoded") {
      const form = new URLSearchParams(body);
      if (Array.from(form).length !== 1) throw Error("Invalid logout request");
      token = form.get("logout_token");
    } else throw Error("Invalid logout request");
    if (typeof token !== "string") throw Error("Invalid logout request");
    const notice = await verifyLogout(config, token);
    const result = await fetch(
      config.apiOrigin + "/internal/auth/backchannel-logout",
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${config.bridgeSecret}`,
        },
        body: JSON.stringify(notice),
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (result.status === 204)
      return new NextResponse(null, { status: 200, headers });
    return NextResponse.json(
      { error: "logout_rejected" },
      { status: result.status === 503 ? 503 : 401, headers },
    );
  } catch {
    return NextResponse.json(
      { error: "logout_rejected" },
      { status: 401, headers },
    );
  }
}
