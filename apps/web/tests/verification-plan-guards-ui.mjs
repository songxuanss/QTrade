import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Large range is previewed, never submitted with acknowledgement: validate the
// guard without launching hundreds of vendor calls or consuming user budgets.
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const origin = "http://127.0.0.1:13000";
const api = "http://127.0.0.1:3122";
const browser = await chromium.launch();
const checks = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  await page.goto(`${origin}/workspace/data`, { timeout: 90000 });
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
  await expect(page).toHaveURL(`${origin}/workspace/data`, { timeout: 90000 });
  const session = await (await page.request.get(`${api}/auth/session`)).json();
  const root = `${api}/api/v1/workspaces/${session.workspace_id}/data/collector`;
  const headers = { "X-CSRF-Token": session.csrf_token };
  const panel = page.locator("#independent-collector");
  await expect(panel).toContainText("远程服务已连接", { timeout: 90000 });
  const before = await (await page.request.get(`${root}/jobs`)).json();
  await panel.getByLabel("采集数据类别", { exact: true }).selectOption("bars");
  await panel
    .getByLabel("采集数据来源", { exact: true })
    .selectOption("mairui.history.bars");
  await panel.getByLabel("独立采集周期", { exact: true }).selectOption("5m");
  await panel.getByLabel("独立采集标的", { exact: true }).fill("601288.SH");
  await panel
    .getByLabel("独立采集开始日期", { exact: true })
    .fill("2023-01-01");
  await panel
    .getByLabel("独立采集结束日期", { exact: true })
    .fill("2025-12-31");
  await panel
    .getByLabel("独立采集下载方式", { exact: true })
    .selectOption("all");
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(panel.getByLabel("采集调用估计", { exact: true })).toContainText(
    "不是供应商报价",
    { timeout: 90000 },
  );
  const ack = panel.getByRole("checkbox", {
    name: "确认计划调用规模",
    exact: true,
  });
  const submit = panel.getByRole("button", {
    name: "提交独立采集任务",
    exact: true,
  });
  await expect(ack).not.toBeChecked();
  await expect(submit).toBeDisabled();
  await ack.focus();
  await page.keyboard.press("Space");
  await expect(ack).toBeChecked();
  await expect(submit).toBeEnabled();
  checks.push(
    "large preview requires explicit keyboard acknowledgement; no download submitted",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(ack).toBeVisible();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  const axe = await new AxeBuilder({ page })
    .include("#independent-collector")
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  assert.deepEqual(
    axe.violations.map((v) => v.id),
    [],
  );
  await page.screenshot({
    path: "../../.tools/verification-local/review/plan-guards-mobile.png",
    fullPage: true,
  });
  checks.push(
    "mobile confirmation and estimate: no overflow; axe zero violations",
  );
  await panel
    .getByLabel("独立采集结束日期", { exact: true })
    .fill("2025-12-30");
  await expect(ack).toHaveCount(0);
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(ack).not.toBeChecked({ timeout: 90000 });
  await expect(submit).toBeDisabled();
  checks.push(
    "changing frozen inputs invalidates preview and resets acknowledgement",
  );
  const draft = {
    source: "mairui.history.bars",
    dataset_kind: "bars",
    instrument_ids: ["601288.SH"],
    granularity: "5m",
    start: "2022-12-31T16:00:00Z",
    end: "2025-12-30T16:00:00Z",
    mode: "all",
  };
  const response = await page.request.post(`${root}/preview`, {
    headers,
    data: draft,
  });
  assert.equal(response.status(), 200);
  const preview = await response.json();
  assert.equal(preview.confirmation_required, true);
  assert(preview.missing_parts >= preview.plan_guard.confirm_new_parts);
  const rejected = await page.request.post(`${root}/jobs`, {
    headers,
    data: {
      request: preview.request,
      coverage_version: preview.coverage_version,
    },
  });
  assert.equal(rejected.status(), 400);
  for (const confirmed of [1, "true"]) {
    const invalid = await page.request.post(`${root}/jobs`, {
      headers,
      data: {
        request: preview.request,
        coverage_version: preview.coverage_version,
        confirmed,
      },
    });
    assert.equal(invalid.status(), 422);
  }
  assert.equal(
    (
      await page.request.get(`${root}/jobs/${preview.request.request_id}`)
    ).status(),
    404,
  );
  assert.deepEqual(
    await (await page.request.get(`${root}/jobs`)).json(),
    before,
  );
  checks.push(
    "deployed API rejects missing/string/numeric acknowledgement; previews create no jobs",
  );
  await page.reload({ timeout: 90000 });
  await expect(page.locator("#independent-collector")).toContainText(
    "远程服务已连接",
    { timeout: 90000 },
  );
  checks.push("workbench accessible after refresh");
  writeFileSync(
    "../../.tools/verification-local/review/plan-guards-ui-results.json",
    JSON.stringify(
      {
        origin,
        checks,
        integration:
          "deployed UI/API/real persistence; large plan preview only; no supplier download readiness claim",
      },
      null,
      2,
    ),
  );
  console.log(`PASS ${checks.length} deployed plan guard checks`);
} finally {
  await browser.close();
}
