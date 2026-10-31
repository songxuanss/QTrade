import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { authConfig } from "./config";

/** One session lookup per request. Page composition fans out into many
 * independent workspace readers; without request-scoped memoization each one
 * repeats the same /auth/session round trip before its own data request. */
export const readSession = cache(async function readSession() {
  const config = authConfig();
  if (!config) return null;
  const name = config.secureCookies
    ? "__Host-qtrade-session"
    : "qtrade-session";
  const cookie = (await cookies()).get(name)?.value;
  if (!cookie || !/^[A-Za-z0-9_-]{43}$/.test(cookie)) return null;
  try {
    const response = await fetch(config.apiOrigin + "/auth/session", {
      cache: "no-store",
      redirect: "error",
      headers: { Cookie: `${name}=${cookie}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) return null;
    const body = await response.json();
    if (
      body.authenticated !== true ||
      typeof body.workspace_id !== "string" ||
      !/^[a-f0-9-]{36}$/i.test(body.workspace_id)
    )
      return null;
    return {
      config,
      name,
      cookie,
      workspaceId: body.workspace_id as string,
      csrfToken:
        typeof body.csrf_token === "string"
          ? (body.csrf_token as string)
          : null,
    };
  } catch {
    return null;
  }
});
