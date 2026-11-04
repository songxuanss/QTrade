import { openDataView } from "./data-workbench-navigation.mjs";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { trackResearchDownload } from "./research-flow-evidence.mjs";

const origin = "http://127.0.0.1:13000";
const confirmSmall = process.argv.includes("--confirm-small");
const identity = JSON.parse(readFileSync("../../.tools/sso-local/state.json"));
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
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
  if (process.env.QTRADE_RESEARCH_SCOPE) {
    await expect(page).toHaveURL(origin + "/workspace/data", {
      timeout: 90000,
    });
    await page
      .getByLabel("切换工作区", { exact: true })
      .selectOption(process.env.QTRADE_RESEARCH_SCOPE);
    await page.getByRole("button", { name: "切换", exact: true }).click();
    await expect(page).toHaveURL(origin + "/", { timeout: 60000 });
    await page.goto(origin + "/workspace/data", { timeout: 90000 });
  }
  await openDataView(page, "多维下载", "分红、送转与配股");
  const panel = page.locator("#distribution-collection");
  await expect(panel).toBeVisible({ timeout: 90000 });
  const finishDownload = await trackResearchDownload(
    page,
    "mairui.corporate.distributions",
    31,
    process.env.QTRADE_RESEARCH_SCOPE,
  );
  await panel
    .getByLabel("公司行为来源")
    .selectOption("mairui.corporate.distributions");
  await panel.getByLabel("公司行为证券代码").fill("600519.SH");
  await panel.getByLabel("公告日开始（含）").fill("1990-01-01");
  await panel.getByLabel("公告日结束（不含）").fill("2027-01-01");
  await panel
    .getByRole("button", { name: "预览公司行为计划", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "公司行为采集计划", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(panel.locator(".research-plan-preview")).toContainText(
    "1 个标的",
  );
  await expect(
    panel.getByLabel("公司行为调用估计", { exact: true }),
  ).toContainText("0–1");
  if (confirmSmall) {
    const ack = panel.getByRole("checkbox", {
      name: "公司行为确认调用规模",
      exact: true,
    });
    const execute = panel.getByRole("button", {
      name: "执行公司行为采集",
      exact: true,
    });
    await expect(ack).not.toBeChecked();
    await expect(execute).toBeDisabled();
    await ack.focus();
    await page.keyboard.press("Space");
    await expect(execute).toBeEnabled();
  }
  await panel
    .getByRole("button", { name: "执行公司行为采集", exact: true })
    .click();
  const download = await finishDownload();
  const row = panel.locator(`[data-research-job="${download.request_id}"]`);
  for (let i = 0; i < 40; i++) {
    await panel
      .getByRole("button", { name: "刷新公司行为任务", exact: true })
      .click();
    if ((await row.textContent())?.includes("已发布")) break;
    await page.waitForTimeout(1000);
  }
  await expect(row).toContainText("已发布");
  await row
    .getByRole("button", { name: "浏览公司行为版本", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "已发布公司行为版本", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  assert.equal(
    await panel.locator("[data-research-versions] > details").count(),
    31,
  );
  await expect(panel.locator("[data-research-versions]")).toContainText(
    download.content_version,
  );
  await page.reload({ timeout: 90000 });
  await expect(row).toContainText("已发布");
  await row
    .getByRole("button", { name: "浏览公司行为版本", exact: true })
    .click();
  await expect(panel.locator("[data-research-versions] > details")).toHaveCount(
    31,
    { timeout: 30000 },
  );
  const results = [];
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    const axe = await new AxeBuilder({ page })
      .include("#distribution-collection")
      .analyze();
    assert.equal(
      axe.violations.length,
      0,
      JSON.stringify(axe.violations.map((v) => v.id)),
    );
    await panel.screenshot({
      path: `../../.tools/verification-local/review/distribution-ui-${viewport.width}.png`,
    });
    results.push({ viewport, records: 31, axeViolations: 0 });
  }
  assert.deepEqual(await finishDownload(), download);
  writeFileSync(
    `../../.tools/verification-local/review/distribution-ui-results${confirmSmall ? "-confirmed" : ""}.json`,
    JSON.stringify(
      {
        origin,
        confirmed_small: confirmSmall,
        download,
        evidence:
          "live UI/SSO/API/Worker/MaiRui publication and refreshed 31 events",
        results,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS distribution UI: live 31 events, refresh, desktop/mobile axe",
  );
} catch (error) {
  // Original browser errors may contain OAuth callback parameters.
  console.error("FAIL distribution UI:", error?.name ?? "Error");
  process.exitCode = 1;
} finally {
  await browser.close();
}
