"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { createUniverseSnapshot } from "../../features/data/universe-server";

export async function resolveUniverseAction(form: FormData) {
  const result = await createUniverseSnapshot({
    universe_id: String(form.get("universe_id") ?? ""),
  });
  revalidatePath("/workspace/data");
  redirect(
    `/workspace/data?${new URLSearchParams(
      "snapshot_id" in result
        ? { universe_snapshot: result.snapshot_id! }
        : { universe_result: result.error! },
    )}${form.get("open_download") === "true" && "snapshot_id" in result ? "#pool-download" : ""}`,
  );
}

function extractInstrumentCodes(text: string): string[] {
  const matches =
    text.match(/(?:index:)?\d{6}(?:\.(?:SH|SZ|BJ))?/gi) ??
    text.split(/[\s,，;；]+/).filter(Boolean);
  const normalized: string[] = [];
  for (const raw of matches) {
    const token = raw.trim();
    if (!token) continue;
    const upper = token.toUpperCase();
    if (upper.startsWith("INDEX:")) {
      const code = upper.slice(6);
      normalized.push(`index:${code.includes(".") ? code : `${code}.SH`}`);
      continue;
    }
    if (/^\d{6}\.(?:SH|SZ|BJ)$/.test(upper)) {
      normalized.push(upper);
      continue;
    }
    if (/^\d{6}$/.test(upper)) {
      const suffix = upper.startsWith("6")
        ? "SH"
        : upper.startsWith("4") || upper.startsWith("8") || upper.startsWith("92")
          ? "BJ"
          : "SZ";
      normalized.push(`${upper}.${suffix}`);
      continue;
    }
    normalized.push(token);
  }
  return [...new Set(normalized)];
}

export async function customUniverseAction(form: FormData) {
  const manualText = String(form.get("instrument_ids") ?? "");
  const uploaded = form.get("instrument_file");
  let fileText = "";
  if (
    uploaded &&
    typeof uploaded === "object" &&
    "text" in uploaded &&
    typeof uploaded.text === "function" &&
    "size" in uploaded &&
    Number(uploaded.size) > 0 &&
    Number(uploaded.size) <= 1_000_000
  ) {
    fileText = await uploaded.text();
  }
  const combined = extractInstrumentCodes(`${manualText}\n${fileText}`);
  const result = await createUniverseSnapshot({
    name: String(form.get("name") ?? ""),
    instrument_ids: combined,
  });
  revalidatePath("/workspace/data");
  redirect(
    `/workspace/data?${new URLSearchParams(
      "snapshot_id" in result
        ? { universe_snapshot: result.snapshot_id! }
        : { universe_result: result.error! },
    )}`,
  );
}
