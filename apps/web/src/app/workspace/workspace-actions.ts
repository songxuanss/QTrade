"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { readSession } from "../../features/auth/session-access";

export async function selectWorkspace(form: FormData) {
  const id = String(form.get("workspace_id") ?? "");
  const session = await readSession();
  if (!session?.csrfToken || !/^[a-f0-9-]{36}$/i.test(id))
    redirect("/workspace/settings?result=workspace_denied");
  let success = false;
  try {
    const response = await fetch(
      `${session.config.apiOrigin}/auth/session/workspace`,
      {
        method: "POST",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
        headers: {
          Cookie: `${session.name}=${session.cookie}`,
          "X-CSRF-Token": session.csrfToken,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ workspace_id: id }),
      },
    );
    success = response.ok;
  } catch {
    success = false;
  }
  if (!success) redirect("/workspace/settings?result=workspace_denied");
  revalidatePath("/", "layout");
  redirect("/");
}
