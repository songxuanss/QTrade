import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
const origin = "http://127.0.0.1:13000";
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(origin + "/workspace/data", { timeout: 90000 });
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.waitForURL(
    (url) =>
      url.pathname === "/workspace/data" || url.pathname === "/auth/consent",
    { timeout: 90000 },
  );
  if (page.url().includes("/auth/consent"))
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  const session = await (
    await page.request.get("http://127.0.0.1:3122/auth/session")
  ).json();
  const root = `http://127.0.0.1:3122/api/v1/workspaces/${session.workspace_id}/data/collector`;
  const panel = page.locator("#independent-collector");
  await panel
    .getByLabel("采集数据类别", { exact: true })
    .selectOption("factors");
  const selected = panel.locator('fieldset input[type="checkbox"]:checked');
  for (const box of await selected.all()) await box.uncheck();
  const north = panel.getByRole("checkbox", { name: /north_holding_ratio/ });
  await expect(north).toBeEnabled();
  await north.check();
  await panel.getByLabel("独立采集标的", { exact: true }).fill("600519.SH");
  await panel
    .getByLabel("独立采集开始日期", { exact: true })
    .fill("2025-09-01");
  await panel
    .getByLabel("独立采集结束日期", { exact: true })
    .fill("2025-09-05");
  await panel
    .getByLabel("独立采集下载方式", { exact: true })
    .selectOption("all");
  await panel
    .getByRole("button", { name: "预览独立采集计划", exact: true })
    .click();
  await expect(panel.getByLabel("独立采集计划", { exact: true })).toBeVisible({
    timeout: 90000,
  });
  await panel
    .getByRole("button", { name: "提交独立采集任务", exact: true })
    .click();
  await expect(panel.locator("[data-collector-job]")).toBeVisible({
    timeout: 90000,
  });
  const id = await panel
    .locator("[data-collector-job]")
    .getAttribute("data-collector-job");
  let job;
  const deadline = Date.now() + 180000;
  do {
    job = await (await page.request.get(root + "/jobs/" + id)).json();
    if (job.state === "no_data" || job.state === "failed") break;
    assert(Date.now() < deadline);
    await page.waitForTimeout(1000);
  } while (true);
  assert.equal(job.state, "no_data");
  assert(job.parts.every((p) => !p.publication));
  await page.reload({ timeout: 90000 });
  await expect(page.locator(`[data-collector-job="${id}"]`)).toContainText(
    "无观测数据",
  );
  writeFileSync(
    "../../.tools/verification-local/review/factor-empty-ui-results.json",
    JSON.stringify(
      {
        origin,
        evidence:
          "live UI/Worker/north_holding_ratio empty history; no publication; refreshed no observations",
        job_id: id,
        state: job.state,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS factor UI: north selectable, live empty history, no publication, refresh",
  );
} finally {
  await browser.close();
}
