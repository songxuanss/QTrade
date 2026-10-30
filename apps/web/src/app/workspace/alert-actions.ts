"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { handleAlert } from "../../features/auth/server";

async function complete(
  form: FormData,
  action: "claim" | "acknowledge" | "silence" | "close" | "note",
) {
  const eventId = String(form.get("event_id") ?? "");
  const note = String(form.get("note") ?? "").trim();
  const silence = Number(form.get("silence_minutes") ?? 0);
  const result = await handleAlert(eventId, action, {
    ...(note ? { note } : {}),
    ...(action === "silence" ? { silence_minutes: silence } : {}),
  });
  revalidatePath("/workspace/alerts");
  redirect(
    `/workspace/alerts?event_id=${encodeURIComponent(eventId)}&result=${result}`,
  );
}

export async function claimAlert(form: FormData) {
  await complete(form, "claim");
}
export async function acknowledgeAlert(form: FormData) {
  await complete(form, "acknowledge");
}
export async function silenceAlert(form: FormData) {
  await complete(form, "silence");
}
export async function closeAlert(form: FormData) {
  await complete(form, "close");
}
export async function noteAlert(form: FormData) {
  await complete(form, "note");
}
