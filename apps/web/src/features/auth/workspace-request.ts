import "server-only";
import { readSession } from "./session-access";

/** Keep cookies and CSRF tokens entirely within the server-side workspace seam. */
export async function workspaceRequest(
  path: string,
  method: "GET" | "POST" | "PATCH" | "PUT" = "GET",
  body?: unknown,
  query?: URLSearchParams,
  timeoutMs = 10_000,
): Promise<Response | null> {
  if (!/^[a-z][a-z0-9/-]*$/.test(path)) return null;
  const session = await readSession();
  if (!session) return null;
  const headers: Record<string, string> = {
    Cookie: `${session.name}=${session.cookie}`,
  };
  try {
    if (method !== "GET") {
      const response = await fetch(`${session.config.apiOrigin}/auth/session`, {
        headers,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) return response;
      const state = await response.json();
      if (typeof state.csrf_token !== "string") return null;
      headers["X-CSRF-Token"] = state.csrf_token;
    }
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces/${session.workspaceId}/${path}${query?.size ? `?${query}` : ""}`,
      {
        method,
        headers,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(
          Math.min(Math.max(timeoutMs, 1000), 90_000),
        ),
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      },
    );
    // A response body is single-use. Give each workspace consumer its own stream
    // so reused server-render fetch responses cannot break a later page render.
    return response.clone();
  } catch {
    return null;
  }
}
