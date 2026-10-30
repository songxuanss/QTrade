import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const scope = JSON.parse(
  readFileSync("../../.tools/verification-local/budget-scope.json"),
).scope_id;
const jobId = "5f8ba9f1-fdcd-564b-9876-12bfc0bf7815";
const observeRecovered = process.argv.includes("--observe-recovered");
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
  await page.goto("http://127.0.0.1:13000/workspace/data", { timeout: 90000 });
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${scope}`;
  const before = await (
    await page.request.get(`${root}/data/collector/jobs/${jobId}`)
  ).json();
  assert.equal(before.state, observeRecovered ? "completed" : "no_data");
  if (!observeRecovered)
    assert(before.parts.every((p) => p.publication === null));
  const panel = page.locator("#independent-collector");
  await panel.locator(".collector-recent summary").click();
  await panel
    .locator(".collector-recent")
    .getByRole("button", { name: /招商银行.*1d/ })
    .click();
  await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
    "data-collector-job",
    jobId,
  );
  if (!observeRecovered) {
    await expect(panel).toContainText("无观测数据");
    await panel
      .getByRole("button", { name: "重试失败或空分段", exact: true })
      .focus();
    // Enter dispatches an async server action. Wait for its response before
    // interpreting an unchanged old no_data read as a new terminal outcome.
    const accepted = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" && r.url().includes("/workspace/data"),
    );
    await page.keyboard.press("Enter");
    await (await accepted).finished();
  }
  let completed;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/data/collector/jobs/${jobId}`,
        );
        assert.equal(response.status(), 200);
        completed = await response.json();
        assert(
          !["failed", "no_data"].includes(completed.state),
          `retry terminal ${completed.state}`,
        );
        return (
          completed.state === "completed" &&
          completed.parts.every((p) => p.consumer?.state === "visible")
        );
      },
      { timeout: 180000, intervals: [1000, 2000] },
    )
    .toBe(true);
  assert.deepEqual(completed.request, before.request);
  assert.deepEqual(
    completed.parts.map((p) => p.part_id),
    before.parts.map((p) => p.part_id),
  );
  const rows = completed.parts.reduce(
    (n, p) => n + p.publication.record_count,
    0,
  );
  assert(rows > 0);
  await page.reload({ timeout: 90000 });
  await expect(panel.locator("[data-collector-job]")).toHaveAttribute(
    "data-collector-job",
    jobId,
  );
  await panel
    .getByRole("link", { name: "终端可见 · 查看图表", exact: true })
    .click();
  await expect(page.locator("#market-chart-heading")).toContainText(
    "招商银行 · 600036.SH",
  );
  await expect(page.locator(".market-chart svg")).toBeVisible();
  const publication = completed.parts[0].publication.publication_id;
  await page.reload({ timeout: 90000 });
  const chart = await page.request.get(
    `${root}/market-watch/chart?instrument_id=600036.SH&granularity=1d&days=30&dataset_id=${publication}`,
  );
  assert.equal(chart.status(), 200);
  const body = await chart.json();
  assert.equal(body.bars.length, rows);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      (await new AxeBuilder({ page }).analyze()).violations.length,
      0,
    );
    await page.screenshot({
      path: `../../.tools/verification-local/review/empty-retry-${width}.png`,
      fullPage: true,
    });
  }
  writeFileSync(
    "../../.tools/verification-local/review/empty-retry-ui.json",
    JSON.stringify(
      {
        job_id: jobId,
        observe_recovered: observeRecovered,
        scope_id: scope,
        rows,
        publication,
        checks: [
          "real empty outcome not published",
          "keyboard retry original request and parts",
          "Worker/source/nonempty publication",
          "named frozen chart, refresh, desktop/mobile axe0",
        ],
      },
      null,
      2,
    ),
  );
  console.log(
    `PASS real empty schedule recovery UI: ${rows} rows, original job/parts unchanged`,
  );
} finally {
  await browser.close();
}
