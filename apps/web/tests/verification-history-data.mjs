import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const identity = JSON.parse(
  readFileSync("../../.tools/sso-local/state.json", "utf8"),
);
const origin = "http://127.0.0.1:3120";
const directory = "../../.tools/verification-local/review";
mkdirSync(directory, { recursive: true });
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/workspace/data`);
  await page
    .getByRole("textbox", { name: "邮箱", exact: true })
    .fill("qtrade-integration@example.test");
  await page.getByLabel("密码", { exact: true }).fill(identity.password);
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await expect(page).toHaveURL(/(?:3121\/auth\/consent|3120\/workspace\/data)/);
  if (new URL(page.url()).pathname === "/auth/consent") {
    await page.getByRole("button", { name: "同意授权", exact: true }).click();
  }
  await expect(page).toHaveURL(`${origin}/workspace/data`);
  await expect(page.getByText("下载时长不限", { exact: true })).toBeVisible();

  for (const [period, expectedCount] of [
    ["1d", 5],
    ["5m", 240],
  ]) {
    await page.goto(`${origin}/workspace/data`);
    await page
      .getByLabel("历史行情股票代码", { exact: true })
      .fill("600519.SH");
    await page.getByLabel("历史行情周期", { exact: true }).selectOption(period);
    await page
      .getByLabel("历史行情开始日期", { exact: true })
      .fill("2025-09-01");
    await page
      .getByLabel("历史行情结束日期", { exact: true })
      .fill("2025-09-05");
    await page
      .getByRole("button", { name: "创建历史行情任务", exact: true })
      .click();
    await page.waitForURL(/result=(?:complete|partial|success|error)/);
    const result = new URL(page.url()).searchParams.get("result");
    if (result === "complete")
      await page
        .getByRole("button", { name: "重新下载全部", exact: true })
        .click();
    if (result === "partial")
      await page
        .getByRole("button", { name: "仅下载缺失数据", exact: true })
        .click();
    await expect(page.locator(".data-notice[role=status]")).toContainText(
      "操作已完成",
    );
    const row = page.locator("#collection .data-row").first();
    await expect(row).toContainText(`mairui.a-share.bar · ${period}`);
    const taskId = await row.getAttribute("data-task-id");
    const deadline = Date.now() + 180000;
    while (
      !(await row.locator(".data-status").innerText()).includes("已完成")
    ) {
      assert.ok(Date.now() < deadline, "History task completion timed out");
      assert.ok(
        !(await row.locator(".data-status").innerText()).includes("失败"),
        await row.innerText(),
      );
      await page.waitForTimeout(1500);
      await page.getByRole("link", { name: "刷新数据", exact: true }).click();
    }
    const dataset = page
      .locator("#research-data .research-dataset-row")
      .filter({ hasText: `${period} ·` })
      .first();
    await expect(dataset).toBeVisible();
    const datasetId = await dataset.getAttribute("data-dataset-id");
    await dataset
      .getByRole("link", { name: "查看质量报告", exact: true })
      .click();
    await expect(page.locator("#dataset-quality")).toContainText("5 / 5");
    await expect(page.locator("#dataset-quality")).toContainText("基础行情");
    await expect(
      page.locator("#dataset-quality .data-metric").first(),
    ).toContainText(String(expectedCount));
    await page.reload();
    await expect(page.locator("#dataset-quality")).toContainText("5 / 5");
    writeFileSync(
      `${directory}/history-${period}-results.json`,
      JSON.stringify({ taskId, datasetId, expectedCount, period }),
    );
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await expect(page.locator("h1")).toHaveText("数据管理");
      await expect(page.locator("#dataset-quality")).toContainText("5 / 5");
      await expect(page.locator("h1")).toBeVisible();
      assert.deepEqual(
        (await new AxeBuilder({ page }).analyze()).violations,
        [],
      );
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.screenshot({
        path: `${directory}/history-${period}-${viewport.width}.png`,
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  assert.deepEqual(errors, []);
  console.log(
    "Actual UI / 2025 daily and minute requests / MaiRui / Worker / persisted datasets / coverage report / refresh / desktop-mobile: PASS",
  );
} finally {
  await browser.close();
}
