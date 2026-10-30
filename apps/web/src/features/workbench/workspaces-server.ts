import "server-only";
import { readSession } from "../auth/session-access";
import { ROLE_NAMES } from "../auth/public";

export type WorkspaceChoice = {
  workspace_id: string;
  name: string;
  role: keyof typeof ROLE_NAMES;
};

export async function accessibleWorkspaces() {
  const session = await readSession();
  const empty = { workspaces: [] as WorkspaceChoice[], selectedId: "" };
  if (!session) return empty;
  try {
    const response = await fetch(
      `${session.config.apiOrigin}/api/v1/workspaces`,
      {
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
        headers: { Cookie: `${session.name}=${session.cookie}` },
      },
    );
    if (!response.ok) return empty;
    const data = await response.json();
    if (
      !Array.isArray(data) ||
      data.some(
        (item) =>
          !/^[a-f0-9-]{36}$/i.test(item.workspace_id) ||
          typeof item.name !== "string" ||
          !Object.hasOwn(ROLE_NAMES, item.role),
      )
    )
      return empty;
    return {
      workspaces: data as WorkspaceChoice[],
      selectedId: session.workspaceId,
    };
  } catch {
    return empty;
  }
}
