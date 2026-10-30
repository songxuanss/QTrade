import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const phase = process.env.QTRADE_BASELINE_PHASE ?? "baseline";
assert(["baseline", "retest", "layout"].includes(phase));
const baseline = JSON.parse(
  readFileSync(
    `../../.tools/verification-local/review/collection-${phase}-ui.json`,
  ),
);
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => u.pathname === "/workspace/data" || u.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/workspace/data", {
    timeout: 90000,
  });
  await page
    .getByLabel("切换工作区", { exact: true })
    .selectOption(baseline.scope_id);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/", { timeout: 60000 });
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${baseline.scope_id}`;
  for (const run of baseline.runs) {
    const response = await page.request.get(
      `${root}/data/collector/jobs/${run.job_id}`,
    );
    assert.equal(response.status(), 200);
    const job = await response.json();
    assert.equal(job.state, "completed");
    assert.equal(job.parts.length, run.size);
    assert(
      job.parts.every(
        (p) =>
          p.consumer?.state === "visible" && p.publication.record_count === 243,
      ),
    );
    const chosen = job.parts.find((p) => p.instrument_id === "601288.SH");
    assert(chosen);
    const dataset = chosen.publication.publication_id;
    await page.goto(
      `http://127.0.0.1:13000/workspace/market?instrument=601288.SH&granularity=1d&dataset_id=${dataset}`,
      { timeout: 90000 },
    );
    await expect(page.locator("#market-chart-heading")).toContainText(
      "农业银行 · 601288.SH",
    );
    await expect(page.locator(".market-chart svg")).toBeVisible();
    await page.reload({ timeout: 90000 });
    assert.equal(new URL(page.url()).searchParams.get("dataset_id"), dataset);
    const chart = await page.request.get(
      `${root}/market-watch/chart?instrument_id=601288.SH&granularity=1d&days=30&dataset_id=${dataset}`,
    );
    assert.equal(chart.status(), 200);
    const bars = (await chart.json()).bars;
    assert(bars.length > 0 && bars.length <= 243);
    assert(bars.every((b) => b.dataset_id === dataset));
    const source = [];
    let offset = 0;
    while (true) {
      const published = await page.request.get(
        `${root}/data/collector/publications/${dataset}/records?offset=${offset}`,
      );
      assert.equal(published.status(), 200);
      const pageData = await published.json();
      source.push(...pageData.records);
      if (pageData.next_offset === null) break;
      offset = pageData.next_offset;
    }
    assert.equal(source.length, 243);
    assert(
      bars.every((b) =>
        source.some((record) => record.timestamp === b.timestamp),
      ),
    );
  }
  console.log(
    `PASS ${phase} persisted UI: ${baseline.runs.map((r) => r.size).join("/")} immutable results, exact representative charts and refresh`,
  );
} catch (error) {
  console.error(`FAIL persisted chart check: ${error?.name ?? "Error"}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
