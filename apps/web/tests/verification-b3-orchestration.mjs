/** B3 multi-dimension orchestration acceptance on the real deployed stack:
 * dimension cards, loop preview, sequential submit, real Worker completion and
 * the v2 preferences round-trip. */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect as playwrightExpect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const expect = playwrightExpect.configure({ timeout: 30000 });
const root = "../../.tools/";
const admin = JSON.parse(readFileSync(root + "sso-local/state.json"));
const scope = JSON.parse(
  readFileSync(root + "verification-local/budget-scope.json"),
).scope_id;
const origin = process.env.QTRADE_WEB_ORIGIN ?? "http://127.0.0.1:3120";
const api = "http://127.0.0.1:3122/api/v1/workspaces/" + scope;
const review = root + "verification-local/review/";
const report = { origin, checks: [], jobs: [], passed: false };
const save = () =>
  writeFileSync(
    review + "b3-orchestration.json",
    JSON.stringify(report, null, 2),
  );
save();
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(30000);
let phase = "login";
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

async function downloadView() {
  await page.goto(origin + "/workspace/data?data_view=download", {
    timeout: 90000,
  });
  await expect(
    page.locator(
      '[data-workbench-tabs="data_view"] [data-workbench-view="download"]',
    ),
  ).toBeVisible();
}

try {
  await page.goto(origin + "/workspace/data?data_view=download", {
    timeout: 90000,
  });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(admin.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (u) => ["/workspace/data", "/auth/consent"].includes(u.pathname),
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  await page.getByLabel("切换工作区", { exact: true }).selectOption(scope);
  await page.getByRole("button", { name: "切换", exact: true }).click();
  await page.waitForURL((u) => u.pathname === "/", { timeout: 60000 });

  phase = "compose cards";
  await downloadView();
  const panel = page.locator("#independent-collector");
  const instruments = panel.getByLabel("独立采集标的", { exact: true });
  const start = panel.getByLabel("独立采集开始日期", { exact: true });
  const end = panel.getByLabel("独立采集结束日期", { exact: true });
  await instruments.fill("601288.SH");
  await start.fill("2025-12-01");
  await end.fill("2025-12-03");
  await end.blur();
  await panel
    .getByLabel("独立采集下载方式", { exact: true })
    .selectOption("all");
  await page.getByRole("button", { name: "加入编排", exact: true }).click();
  await instruments.fill("600519.SH");
  await page.getByRole("button", { name: "加入编排", exact: true }).click();
  const cards = page.locator("[data-orchestration-card]");
  await expect(cards).toHaveCount(2);
  expect(
    await cards.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-card-state")),
    ),
  ).toEqual(["draft", "draft"]);
  await page.screenshot({
    path: review + "b3-orchestration-cards.png",
    fullPage: true,
  });

  phase = "loop preview";
  await page.locator('[data-orchestration-action="preview"]').click();
  await expect
    .poll(
      () =>
        cards.evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute("data-card-state")),
        ),
      { timeout: 120000, intervals: [500, 1000] },
    )
    .toEqual(["previewed", "previewed"]);
  report.checks.push({ loop_preview: true });

  phase = "sequential submit";
  const confirm = page.locator('[data-orchestration-action="confirm"]');
  if (await confirm.count()) await confirm.check();
  await page.locator('[data-orchestration-action="submit"]').click();
  await expect
    .poll(
      () =>
        cards.evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute("data-card-state")),
        ),
      { timeout: 180000, intervals: [500, 1000] },
    )
    .toEqual(["submitted", "submitted"]);
  const jobIds = await cards.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-card-job")),
  );
  assert(jobIds.every(Boolean), "every card must expose its job id");
  report.checks.push({ sequential_submit: true });
  expect(
    await page
      .locator('[data-workbench-tabs="data_view"] [data-workbench-view="jobs"]')
      .isVisible(),
  ).toBe(true);

  phase = "worker completion";
  for (const jobId of jobIds) {
    let last;
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `${api}/data/collector/jobs/${jobId}`,
          );
          if (!response.ok()) return `http_${response.status()}`;
          last = await response.json();
          return last.state;
        },
        { timeout: 240000, intervals: [2000, 3000] },
      )
      .toMatch(/completed/);
    const records = last.parts.reduce(
      (sum, part) => sum + (part.publication?.record_count ?? 0),
      0,
    );
    assert(records > 0, `job ${jobId} published no records`);
    const instruments = [
      ...new Set(last.parts.map((part) => part.instrument_id)),
    ];
    report.jobs.push({
      job_id: jobId,
      state: last.state,
      parts: last.parts.length,
      records,
      instruments,
    });
  }
  report.checks.push({ worker_completed: true });

  phase = "v2 preferences round-trip";
  // Stay in the same document: the cards only exist in client state until the
  // draft is saved, so switch views with the tab strip instead of reloading.
  await page.getByRole("tab", { name: "多维下载", exact: true }).click();
  await expect(
    page.locator(
      '[data-workbench-tabs="data_view"] [data-workbench-view="download"]',
    ),
  ).toBeVisible();
  await panel.getByRole("button", { name: "保存选择", exact: true }).click();
  await expect(
    panel.getByText("已为当前用户和工作空间保存选择", { exact: false }),
  ).toBeVisible({ timeout: 60000 });
  const stored = await page.request.get(`${api}/data/collector/preferences`);
  assert.equal(stored.status(), 200);
  const draft = await stored.json();
  assert.equal(draft.schema_version, 2);
  assert.equal(draft.dimensions.length, 2);
  assert.deepEqual(
    draft.dimensions.map((item) => item.instruments),
    ["601288.SH", "600519.SH"],
  );
  await page.reload({ timeout: 90000 });
  await expect(page.locator("[data-orchestration-card]")).toHaveCount(2);
  expect(
    await page
      .locator("[data-orchestration-card]")
      .evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-card-state")),
      ),
  ).toEqual(["draft", "draft"]);
  report.checks.push({ preferences_v2_round_trip: true });

  phase = "desktop axe";
  const desktopAxe = await new AxeBuilder({ page })
    .include('[data-workbench-tabs="data_view"]')
    .analyze();
  assert.equal(
    desktopAxe.violations.length,
    0,
    JSON.stringify(desktopAxe.violations.map((v) => v.id)),
  );
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "horizontal overflow at 1440",
  );
  report.checks.push({ desktop_axe: 0 });

  phase = "mobile orchestration";
  await page.setViewportSize({ width: 390, height: 844 });
  await downloadView();
  await expect(page.locator("[data-orchestration-card]")).toHaveCount(2);
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "horizontal overflow at 390",
  );
  const mobileAxe = await new AxeBuilder({ page })
    .include("[data-orchestration]")
    .analyze();
  assert.equal(
    mobileAxe.violations.length,
    0,
    JSON.stringify(mobileAxe.violations.map((v) => v.id)),
  );
  await page.screenshot({
    path: review + "b3-orchestration-mobile.png",
    fullPage: true,
  });
  report.checks.push({ mobile_no_overflow_and_axe: true });

  assert.equal(errors.length, 0, errors.join("\n"));
  report.passed = true;
  save();
  console.log(
    JSON.stringify({ passed: true, checks: report.checks, jobs: report.jobs }),
  );
} catch (error) {
  save();
  console.error(
    JSON.stringify({
      passed: false,
      phase,
      failure: `${error.name}: ${String(error.message).split("\n")[0].slice(0, 240)}`,
      page_errors: errors.slice(0, 3),
      url: page.url(),
    }),
  );
  await page
    .screenshot({
      path: review + "b3-orchestration-diagnostic.png",
      fullPage: true,
    })
    .catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
