import assert from "node:assert/strict";
import { expect } from "@playwright/test";

// Observe the identity created by this UI action. A previously published row
// must never satisfy a fresh download test merely because it stays visible.
export async function trackResearchDownload(
  page,
  source,
  expectedRecords,
  selectedScope,
) {
  const api = "http://127.0.0.1:3122";
  const sessionResponse = await page.request.get(`${api}/auth/session`);
  assert.equal(sessionResponse.status(), 200);
  const session = await sessionResponse.json();
  // Browser navigation can select a workspace independently from the session
  // returned by a direct API request. Observe the exact scope exercised by UI.
  const scope = selectedScope ?? session.workspace_id;
  const root = `${api}/api/v1/workspaces/${scope}/data/collector/research`;
  async function jobs() {
    const response = await page.request.get(`${root}/jobs`);
    assert.equal(response.status(), 200);
    return response.json();
  }
  const before = new Set((await jobs()).map((job) => job.request_id));
  return async () => {
    let created;
    await expect
      .poll(
        async () => {
          const fresh = (await jobs()).filter(
            (job) =>
              job.request.source === source && !before.has(job.request_id),
          );
          assert(
            fresh.length <= 1,
            "ambiguous new research run: concurrent same-source submission",
          );
          created = fresh[0];
          return created?.state;
        },
        { timeout: 90000, intervals: [500, 1000] },
      )
      .toBe("completed");
    assert(created.publication_id);
    const response = await page.request.get(
      `${root}/publications/${created.publication_id}`,
    );
    assert.equal(response.status(), 200);
    const manifest = await response.json();
    assert.equal(manifest.artifact.request_id, created.request_id);
    assert.equal(manifest.scope_id, scope);
    assert.equal(manifest.artifact.record_count, expectedRecords);
    assert(["passed", "warning"].includes(manifest.quality.status));
    assert(manifest.artifact.raw_batches.length > 0);
    assert(
      manifest.artifact.raw_batches.every(
        (batch) => batch.raw_record_count > 0,
      ),
    );
    return {
      scope_id: scope,
      request_id: created.request_id,
      publication_id: created.publication_id,
      content_version: manifest.content_version,
      records_sha256: manifest.artifact.records_sha256,
      records: manifest.artifact.record_count,
      raw_batches: manifest.artifact.raw_batches.length,
      quality: manifest.quality.status,
    };
  };
}
