import assert from "node:assert/strict";
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const symbols = [
  "601288.SH",
  "600519.SH",
  "600036.SH",
  "601398.SH",
  "601939.SH",
  "601318.SH",
  "600000.SH",
  "600030.SH",
  "600900.SH",
  "601088.SH",
  "601857.SH",
  "600028.SH",
  "601012.SH",
  "600276.SH",
  "600887.SH",
  "000333.SZ",
  "000651.SZ",
  "000858.SZ",
  "002415.SZ",
  "300750.SZ",
];
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const scope = JSON.parse(
  readFileSync("../../.tools/verification-local/budget-scope.json"),
).scope_id;
const phase = process.env.QTRADE_BASELINE_PHASE ?? "baseline";
assert(["baseline", "retest", "layout"].includes(phase));
const sizes = process.env.QTRADE_BASELINE_SIZES
  ? process.env.QTRADE_BASELINE_SIZES.split(",").map(Number)
  : [1, 5, 20];
assert(sizes.length && sizes.every((size) => [1, 5, 20].includes(size)));
const file = `../../.tools/verification-local/review/collection-${phase}-ui.json`;
const report = {
  phase,
  scope_id: scope,
  source: "mairui.history.bars",
  period: "1d",
  mode: "all",
  start: "2025-01-01",
  end_inclusive: "2025-12-31",
  symbols,
  runs: [],
};
function persist() {
  writeFileSync(file, JSON.stringify(report, null, 2));
  chmodSync(file, 0o600);
}
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
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:13000/", { timeout: 60000 });
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}/data/collector`;
  for (const size of sizes) {
    await page.goto("http://127.0.0.1:13000/workspace/data", {
      timeout: 90000,
    });
    const panel = page.locator("#independent-collector");
    await panel
      .getByLabel("采集数据类别", { exact: true })
      .selectOption("bars");
    await panel
      .getByLabel("采集数据来源", { exact: true })
      .selectOption("mairui.history.bars");
    await panel.getByLabel("独立采集周期", { exact: true }).selectOption("1d");
    await panel
      .getByLabel("独立采集标的", { exact: true })
      .fill(symbols.slice(0, size).join(","));
    await panel
      .getByLabel("独立采集开始日期", { exact: true })
      .fill("2025-01-01");
    await panel
      .getByLabel("独立采集结束日期", { exact: true })
      .fill("2025-12-31");
    await panel
      .getByLabel("独立采集下载方式", { exact: true })
      .selectOption("all");
    const run = { size, started_at: new Date().toISOString() };
    report.runs.push(run);
    persist();
    const started = performance.now();
    await panel
      .getByRole("button", { name: "预览独立采集计划", exact: true })
      .click();
    await expect(panel.getByLabel("独立采集计划", { exact: true })).toBeVisible(
      { timeout: 90000 },
    );
    run.preview_ms = Math.round(performance.now() - started);
    const current = panel.locator("[data-collector-job]");
    const previous = (await current.count())
      ? await current.getAttribute("data-collector-job")
      : null;
    if (await panel.getByLabel("确认计划调用规模", { exact: true }).count())
      await panel.getByLabel("确认计划调用规模", { exact: true }).check();
    const submitted = performance.now();
    await panel
      .getByRole("button", { name: "提交独立采集任务", exact: true })
      .click();
    await expect
      .poll(
        () =>
          panel
            .locator("[data-collector-job]")
            .getAttribute("data-collector-job"),
        { timeout: 90000 },
      )
      .not.toBe(previous);
    run.job_id = await panel
      .locator("[data-collector-job]")
      .getAttribute("data-collector-job");
    assert(run.job_id);
    run.accepted_at = new Date().toISOString();
    run.acceptance_ms = Math.round(performance.now() - submitted);
    persist();
    const deadline = Date.now() + 900000;
    let job;
    while (Date.now() < deadline) {
      const response = await page.request.get(`${root}/jobs/${run.job_id}`);
      assert.equal(response.status(), 200);
      job = await response.json();
      if (!["running", "waiting", "queued"].includes(job.state)) break;
      await page.waitForTimeout(1500);
    }
    run.finished_at = new Date().toISOString();
    run.result_ms = Math.round(performance.now() - submitted);
    run.state = job.state;
    run.counts = job.counts;
    run.parts = job.parts.map((p) => ({
      part_id: p.part_id,
      instrument_id: p.instrument_id,
      state: p.state,
      error_code: p.error_code,
      records: p.publication?.record_count ?? null,
      publication_id: p.publication?.publication_id ?? null,
      consumer_at_result: p.consumer?.state ?? null,
    }));
    persist();
    assert(
      !["running", "waiting", "queued"].includes(job.state),
      "baseline did not finish within bound",
    );
    // Persist all failures/empty outcomes before the success check; the baseline
    // never swaps an unsuccessful symbol for a faster/available substitute.
    if (job.state === "completed") {
      await expect
        .poll(
          async () => {
            job = await (
              await page.request.get(`${root}/jobs/${run.job_id}`)
            ).json();
            return job.parts.every((p) => p.consumer?.state === "visible");
          },
          { timeout: 180000 },
        )
        .toBe(true);
      run.visible_ms = Math.round(performance.now() - submitted);
      run.nonempty = true;
      run.consumer_final = job.parts.map((p) => ({
        part_id: p.part_id,
        state: p.consumer?.state,
      }));
    } else run.nonempty = false;
    await page.reload({ timeout: 90000 });
    await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
      "data-collector-job",
      run.job_id,
    );
    run.refresh_verified = true;
    persist();
    console.log(`BASELINE ${size}: ${run.state}, ${run.result_ms}ms`);
  }
  report.completed_at = new Date().toISOString();
  persist();
  console.log(
    `UI observations ${sizes.join("/")} persisted; result classifications retained`,
  );
} catch (error) {
  console.error(`FAIL collection observation: ${error?.name ?? "Error"}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
