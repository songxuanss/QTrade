"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { controlWorkspaceRuntime } from "../../features/auth/server";

export async function controlRuntime(form: FormData) {
  const runtimeId = String(form.get("runtime_id") ?? "");
  const action = String(form.get("action") ?? "");
  const confirmation = String(form.get("confirmation") ?? "").trim();
  if (
    !["pause", "resume", "stop", "cancel_orders", "close_positions"].includes(
      action,
    )
  ) {
    redirect("/workspace/trading?result=error");
  }
  const result = await controlWorkspaceRuntime(
    runtimeId,
    action as "pause" | "resume" | "stop" | "cancel_orders" | "close_positions",
    confirmation,
  );
  revalidatePath("/workspace/trading");
  redirect(`/workspace/trading?result=${result}`);
}
