"use server";

import { publishResearchDataset } from "../../features/data/research-server";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import {
  claimDataIssue,
  closeDataIssue,
  createTickDataTask,
  rerunDataTask,
} from "../../features/auth/server";

async function finish(
  result: string,
  view: "ticks" | "assets" = "ticks",
  asset?: "tasks" | "quality" | "research",
) {
  revalidatePath("/workspace/data");
  // Redirect to the operation's owning area, never a client-supplied return URL.
  // The old stacked page hid this distinction; tabbed navigation must retain it.
  const query = new URLSearchParams({ result, data_view: view });
  if (asset) query.set("asset_view", asset);
  redirect(`/workspace/data?${query}`);
}

export async function rerunTaskAction(form: FormData) {
  await finish(
    await rerunDataTask(String(form.get("task_id") ?? "")),
    "assets",
    "tasks",
  );
}
export async function createTickTaskAction(form: FormData) {
  const input = {
    source: String(form.get("source") ?? ""),
    authorization_id: String(form.get("authorization_id") ?? ""),
    instrument_id: String(form.get("instrument_id") ?? ""),
    start: `${String(form.get("start") ?? "")}Z`,
    end: `${String(form.get("end") ?? "")}Z`,
    mode: String(form.get("mode") ?? "check"),
    granularity: String(form.get("granularity") ?? "tick"),
  };
  const result = await createTickDataTask(input);
  if (result === "complete" || result === "partial") {
    const query = new URLSearchParams({
      result,
      download_source: input.source,
      download_granularity: input.granularity,
      download_instrument: input.instrument_id,
      download_start: input.start.slice(0, -1),
      download_end: input.end.slice(0, -1),
    });
    redirect(`/workspace/data?${query}`);
  }
  await finish(
    result,
    result === "success" ? "assets" : "ticks",
    result === "success" ? "tasks" : undefined,
  );
}
export async function claimIssueAction(form: FormData) {
  await finish(
    await claimDataIssue(String(form.get("issue_id") ?? "")),
    "assets",
    "quality",
  );
}
export async function closeIssueAction(form: FormData) {
  await finish(
    await closeDataIssue(String(form.get("issue_id") ?? "")),
    "assets",
    "quality",
  );
}

export async function publishResearchAction(form: FormData) {
  await finish(
    await publishResearchDataset(String(form.get("task_id") ?? "")),
    "assets",
    "research",
  );
}

export async function createHistoryTaskAction(form: FormData) {
  const first = String(form.get("start_date") ?? "");
  const last = String(form.get("end_date") ?? "");
  const start = Date.parse(`${first}T00:00:00+08:00`);
  const end = Date.parse(`${last}T00:00:00+08:00`) + 86400_000;
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(first) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(last) ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    new Date(start + 28800_000).toISOString().slice(0, 10) !== first ||
    new Date(end - 86400_000 + 28800_000).toISOString().slice(0, 10) !== last ||
    start >= end
  )
    return finish("invalid");
  const task = new FormData();
  task.set("source", "mairui.a-share.bar");
  task.set("authorization_id", "mairui-default");
  task.set("instrument_id", String(form.get("instrument_id") ?? ""));
  task.set("granularity", String(form.get("granularity") ?? ""));
  task.set("start", new Date(start).toISOString().slice(0, -1));
  task.set("end", new Date(end).toISOString().slice(0, -1));
  return createTickTaskAction(task);
}
