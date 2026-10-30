import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const review = "../../.tools/verification-local/review/";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const annual = JSON.parse(
  readFileSync(review + "annual-daily-ui-results.json"),
).checks.find(
  (c) => c.name === "annual_daily_ui_worker_publication_consumer_refresh",
).id;
const factor = JSON.parse(
  readFileSync(review + "independent-collector-results.json"),
).jobs.find((j) => j.kind === "factors").id;
const empty = JSON.parse(
  readFileSync(review + "factor-empty-ui-results.json"),
).job_id;
const browser = await chromium.launch();
const checks = [];
try {
  const context = await browser.newContext();
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
  // Consent can finish with a second navigation after the first URL wait.
  // Wait for that return before opening a task URL, otherwise it can overwrite
  // the query-bearing navigation and make a valid persisted task look absent.
  await page.waitForURL((u) => u.pathname === "/workspace/data", {
    timeout: 90000,
  });
  await expect(page.locator("#independent-collector")).toBeVisible({
    timeout: 90000,
  });
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector`;
  let factorPart;
  for (const [job, label, text] of [
    [annual, "passed", "检查通过"],
    [factor, "warning", "因子单位未知"],
    [empty, "no_observations", "无观测"],
    [
      "530fc1fd-ad05-484e-bffe-9f0391716b0e",
      "legacy_unchecked",
      "未取得质量报告",
    ],
  ]) {
    await page.goto(
      `http://127.0.0.1:13000/workspace/data?collector_job=${job}`,
      { timeout: 90000 },
    );
    const run = page.locator(`[data-collector-job="${job}"]`);
    await expect(run).toBeVisible({ timeout: 90000 });
    const qualityButton = run
      .getByRole("button", { name: "查看质量报告", exact: true })
      .first();
    if (label === "passed") {
      await qualityButton.focus();
      await page.keyboard.press("Enter");
    } else await qualityButton.click();
    await expect(run).toContainText(text, { timeout: 30000 });
    const part = await run
      .locator("[data-collector-part]")
      .first()
      .getAttribute("data-collector-part");
    const response = await page.request.get(
      `${root}/jobs/${job}/parts/${part}/quality`,
    );
    assert.equal(response.status(), 200);
    const reports = await response.json();
    if (label === "legacy_unchecked") assert.deepEqual(reports, []);
    else assert.equal(reports.at(-1).status, label);
    if (label === "warning") factorPart = part;
    await page.reload({ timeout: 90000 });
    await expect(run).toBeVisible({ timeout: 90000 });
    await run
      .getByRole("button", { name: "查看质量报告", exact: true })
      .first()
      .click();
    await expect(run).toContainText(text, { timeout: 30000 });
    checks.push({ name: `quality_${label}_ui_api_refresh`, passed: true });
  }
  const mismatch = await page.request.get(
    `${root}/jobs/${annual}/parts/${factorPart}/quality`,
  );
  assert.equal(mismatch.status(), 404);
  checks.push({ name: "existing_part_cross_job_rejected", passed: true });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 950 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      (
        await new AxeBuilder({ page })
          .include("#independent-collector")
          .analyze()
      ).violations.length,
      0,
    );
    checks.push({ name: `layout_accessibility_${width}`, passed: true });
  }
  writeFileSync(
    review + "collection-quality-ui-results.json",
    JSON.stringify(
      {
        checks,
        limitation:
          "Uses real persisted runs; supplier failures and repair UI remain separate cases",
      },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ checks }));
} finally {
  await browser.close();
}
