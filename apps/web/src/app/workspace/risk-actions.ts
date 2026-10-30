"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { mutateRisk } from "../../features/risk/server";

function finish(result: string) {
  revalidatePath("/workspace/risk");
  redirect(`/workspace/risk?result=${result}`);
}

export async function activateKillSwitch(form: FormData) {
  if (form.get("confirmation") !== "停止交易") finish("confirmation");
  const reason = String(form.get("reason") ?? "").trim();
  if (!reason || reason.length > 500) finish("invalid");
  finish(await mutateRisk("kill-switch/activate", { reason }));
}
export async function requestRecovery() {
  finish(await mutateRisk("kill-switch/recovery", {}));
}
export async function approveRecovery(form: FormData) {
  const id = String(form.get("request_id") ?? "");
  const reason = String(form.get("reason") ?? "").trim();
  if (!/^[a-f0-9-]{36}$/i.test(id) || !reason || reason.length > 500)
    finish("invalid");
  if (form.get("confirmation") !== "批准恢复") finish("confirmation");
  finish(await mutateRisk(`kill-switch/recovery/${id}/approve`, { reason }));
}
