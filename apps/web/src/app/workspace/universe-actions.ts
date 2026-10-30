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

export async function customUniverseAction(form: FormData) {
  const result = await createUniverseSnapshot({
    name: String(form.get("name") ?? ""),
    instrument_ids: String(form.get("instrument_ids") ?? "")
      .split(/[\s,，;；]+/)
      .filter(Boolean),
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
