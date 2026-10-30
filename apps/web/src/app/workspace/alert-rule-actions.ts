"use server";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { mutateAlertRule } from "../../features/alerts/rules-server";

function finish(result: string) {
  revalidatePath("/workspace/alerts");
  redirect(`/workspace/alerts?rules_result=${result}`);
}
export async function createAlertRule(form: FormData) {
  const name = String(form.get("name") ?? "").trim();
  const condition = String(form.get("condition") ?? "").trim();
  const cooldown = Number(form.get("cooldown_seconds"));
  const source = String(form.get("source"));
  const severity = String(form.get("severity"));
  if (
    !name ||
    name.length > 120 ||
    !condition ||
    condition.length > 512 ||
    !Number.isInteger(cooldown) ||
    cooldown < 0 ||
    cooldown > 86400 ||
    !["market", "risk", "data", "system"].includes(source) ||
    !["info", "warning", "critical"].includes(severity)
  )
    finish("invalid");
  finish(
    await mutateAlertRule("alert-rules", "POST", {
      name,
      condition,
      source,
      severity,
      cooldown_seconds: cooldown,
      enabled: true,
    }),
  );
}
export async function toggleAlertRule(form: FormData) {
  const id = String(form.get("rule_id") ?? "");
  if (
    !/^[a-f0-9-]{36}$/i.test(id) ||
    !["true", "false"].includes(String(form.get("enabled")))
  )
    finish("invalid");
  finish(
    await mutateAlertRule(`alert-rules/${id}/enabled`, "PATCH", {
      enabled: form.get("enabled") === "true",
    }),
  );
}
export async function evaluateAlertRules(form: FormData) {
  const source = String(form.get("source"));
  let facts: unknown;
  try {
    facts = JSON.parse(String(form.get("facts") ?? ""));
  } catch {
    finish("invalid");
  }
  if (
    !facts ||
    typeof facts !== "object" ||
    Array.isArray(facts) ||
    !["market", "risk", "data", "system"].includes(source)
  )
    finish("invalid");
  finish(
    await mutateAlertRule("alert-evaluations", "POST", {
      source,
      facts,
      context_snapshot: { origin: "manual_workspace_evaluation" },
    }),
  );
}
